"""Performance correctness: reuse, isolation, cancellation, DNS and metrics (no network)."""
from __future__ import annotations

import asyncio
from collections import deque
import threading
from types import SimpleNamespace
from unittest.mock import patch

from app import metrics
from app import query_cache as cache_module
from app.query_cache import QueryCache
from app.llm import client as llm
from app.tools import registry, _http
from app.tools.base import ToolResult


async def wait_for_join(cache, key, count):
    async def check():
        while key not in cache._flights or cache._flights[key].waiters < count:
            await asyncio.sleep(0)
    await asyncio.wait_for(check(), 1)


async def test_shared_waiters_and_cancel():
    cache = QueryCache("test.shared")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    cancelled = False
    async def fetch():
        nonlocal calls, cancelled
        calls += 1
        entered.set()
        try:
            await release.wait()
            return {"rows": [1]}
        except asyncio.CancelledError:
            cancelled = True
            raise
    first = asyncio.create_task(cache.get("same", fetch, ttl=10))
    await entered.wait()
    second = asyncio.create_task(cache.get("same", fetch, ttl=10))
    await wait_for_join(cache, "same", 2)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert not cancelled and not second.done()
    release.set()
    result = await second
    result["rows"].append(2)
    assert await cache.get("same", fetch, ttl=10) == {"rows": [1]}
    assert calls == 1
    await cache.close()


async def test_last_waiter_releases_upstream():
    cache = QueryCache("test.abandoned")
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def fetch():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    task = asyncio.create_task(cache.get("one", fetch))
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert stopped.is_set() and not cache._flights
    assert not cache._values


async def test_failure_not_cached():
    cache = QueryCache("test.failure")
    entered, release = asyncio.Event(), asyncio.Event()
    async def broken():
        entered.set()
        await release.wait()
        raise RuntimeError("fixture failure")
    one = asyncio.create_task(cache.get("same", broken, ttl=10))
    await entered.wait()
    two = asyncio.create_task(cache.get("same", broken, ttl=10))
    await wait_for_join(cache, "same", 2)
    release.set()
    results = await asyncio.gather(one, two, return_exceptions=True)
    assert all(isinstance(result, RuntimeError) for result in results)
    assert not cache._values and not cache._flights
    async def recovered():
        return "recovered"
    assert await cache.get("same", recovered, ttl=10) == "recovered"
    await cache.close()


async def test_bounded_lru_expiry():
    cache = QueryCache("test.bounded", capacity=2)
    clock = [100.0]
    calls = []
    async def get(key):
        async def fetch():
            calls.append(key)
            return key
        return await cache.get(key, fetch, ttl=1)
    with patch.object(cache_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
        await get("a"); await get("b"); await get("a"); await get("c")
        assert set(cache._values) == {"a", "c"} and calls == ["a", "b", "c"]
        clock[0] += 2
        await get("a")
        assert calls == ["a", "b", "c", "a"] and len(cache._values) == 1
    await cache.close()


async def test_registry_dates_settings_and_mutation():
    class Fixture:
        enabled = True
        name = "ticket.query"
        def __init__(self): self.calls = 0
        async def invoke(self, params):
            self.calls += 1
            return ToolResult(True, data={"rows": []}, fetched_at="2026-09-30T12:00:00Z")
    fixture = Fixture()
    params = {"from_station": "北京", "to_station": "上海", "date": "2026-10-01"}
    await registry.close_queries()
    try:
        with patch.dict(registry._REGISTRY, {fixture.name: fixture}), patch.dict("os.environ", {"APP_VARIANT": "main"}):
            llm.set_active_provider({"api_key": "fixture-one", "model": "model-one"})
            first = await registry.invoke_by_name(fixture.name, params)
            first.data["rows"].append("mutated")
            second = await registry.invoke_by_name(fixture.name, params)
            assert not second.data["rows"] and fixture.calls == 1
            assert second.fetched_at == "2026-09-30T12:00:00Z" and "15 秒" in second.note
            await registry.invoke_by_name(fixture.name, {**params, "date": "2026-10-02"})
            llm.set_active_provider({"api_key": "fixture-two", "model": "model-one"})
            await registry.invoke_by_name(fixture.name, params)
            llm.set_active_provider({"api_key": "fixture-two", "model": "model-two"})
            await registry.invoke_by_name(fixture.name, params)
            await registry.invoke_by_name(fixture.name, {**params, "seat": "二等座"})
            assert fixture.calls == 5
    finally:
        llm.set_active_provider(None)
        await registry.close_queries()


async def test_dns_off_loop_and_guard_preserved():
    entered, release = threading.Event(), threading.Event()
    def slow_dns(*args, **kwargs):
        entered.set()
        assert release.wait(1)
        return [(None, None, None, None, ("8.8.8.8", 443))]
    with patch.object(_http.socket, "getaddrinfo", slow_dns):
        guarded = asyncio.create_task(_http.assert_public_url_async("https://fixture.example"))
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.001)
        try:
            assert entered.is_set()
            await asyncio.sleep(.01)
            assert not guarded.done(), "guard blocked event loop instead of running in worker"
        finally:
            release.set()
        await guarded
    with patch.object(_http.socket, "getaddrinfo", return_value=[(None,None,None,None,("127.0.0.1",443))]):
        try:
            await _http.assert_public_url_async("https://fixture.example")
        except _http.UnsafeUrlError:
            pass
        else:
            raise AssertionError("private DNS destination allowed")


async def test_nested_params_snapshot_before_factory():
    class Fixture:
        enabled = True
        name = "ticket.query"
        calls = 0
        async def invoke(self, params):
            self.calls += 1
            trains = list(params["action"]["trains"])
            params["action"]["trains"].append("tool mutation")
            return ToolResult(True, data={"trains": trains})
    fixture = Fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    original_get = QueryCache.get
    async def delayed_get(cache, key, factory, **kwargs):
        entered.set()
        await release.wait()
        return await original_get(cache, key, factory, **kwargs)
    params = {"date": "2026-10-01", "action": {"trains": ["G1"]}}
    await registry.close_queries()
    try:
        with patch.dict(registry._REGISTRY, {fixture.name: fixture}), \
             patch.dict("os.environ", {"APP_VARIANT": "main"}), \
             patch.object(QueryCache, "get", delayed_get):
            pending = asyncio.create_task(registry.invoke_by_name(fixture.name, params))
            await entered.wait()  # Key exists; the tool's factory has not run.
            params["action"]["trains"][0] = "G2"
            release.set()
            assert (await pending).data["trains"] == ["G1"]
            assert params["action"]["trains"] == ["G2"]
            cached = await registry.invoke_by_name(fixture.name,
                {"date": "2026-10-01", "action": {"trains": ["G1"]}})
            assert cached.data["trains"] == ["G1"] and fixture.calls == 1
    finally:
        await registry.close_queries()


def test_live_loop_shutdown_isolated():
    """Two real running loops/threads share the registry, but never tasks."""
    entered = {name: threading.Event() for name in ("audit-A", "audit-B")}
    stopped = {name: threading.Event() for name in entered}
    b_joined, a_closed = threading.Event(), threading.Event()
    gates, calls, errors = {}, dict.fromkeys(entered, 0), []
    params = {"date": "2026-10-01", "from_station": "北京", "to_station": "上海"}
    class Fixture:
        enabled = True
        name = "ticket.query"
        async def invoke(self, _params):
            actor = threading.current_thread().name
            calls[actor] += 1
            entered[actor].set()
            try:
                await gates[actor].wait()
                return ToolResult(True, data={"actor": actor, "rows": [1]})
            finally:
                stopped[actor].set()
    fixture = Fixture()

    async def signal_wait(event):
        assert await asyncio.to_thread(event.wait, 3), "thread synchronization timed out"

    async def scenario(actor):
        gates[actor] = asyncio.Event()
        one = asyncio.create_task(registry.invoke_by_name(fixture.name, params))
        requests = [one]
        try:
            await signal_wait(entered[actor])
            if actor == "audit-A":
                await signal_wait(b_joined)
                await registry.close_queries()
                result = await asyncio.gather(one, return_exceptions=True)
                assert isinstance(result[0], asyncio.CancelledError)
                assert stopped[actor].is_set(), "A's upstream must be released"
                assert not stopped["audit-B"].is_set(), "A closed B's upstream"
                a_closed.set()
            else:
                two = asyncio.create_task(registry.invoke_by_name(fixture.name, params))
                requests.append(two)
                cache = registry._QUERIES[fixture.name]
                key = next(iter(cache._flights))
                await wait_for_join(cache, key, 2)
                b_joined.set()
                await signal_wait(a_closed)
                assert not stopped[actor].is_set() and not one.done() and not two.done()
                # A's lifecycle must not discard the per-tool registry cache.
                three = asyncio.create_task(registry.invoke_by_name(fixture.name, params))
                requests.append(three)
                assert registry._QUERIES[fixture.name] is cache
                await wait_for_join(cache, key, 3)
                assert calls[actor] == 1
                gates[actor].set()
                results = await asyncio.gather(*requests)
                assert all(result.data == {"actor": actor, "rows": [1]} for result in results)
                results[0].data["rows"].append(2)
                warm = await registry.invoke_by_name(fixture.name, params)
                assert warm.data == {"actor": actor, "rows": [1]} and calls[actor] == 1
        finally:
            # Reap all tasks on their own loop even if an assertion fails.
            for request in requests:
                if not request.done(): request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            await registry.close_queries()

    def worker(actor):
        try:
            asyncio.run(scenario(actor), debug=True)
        except BaseException as error:
            errors.append(error)

    with patch.dict(registry._REGISTRY, {fixture.name: fixture}), \
         patch.dict("os.environ", {"APP_VARIANT": "main"}):
        threads = [threading.Thread(target=worker, args=(actor,), name=actor) for actor in entered]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        assert all(not thread.is_alive() for thread in threads), "loop workers did not finish"
    assert not errors, [f"{type(error).__name__}: {error}" for error in errors]
    assert calls == {"audit-A": 1, "audit-B": 1}
    assert all(event.is_set() for event in stopped.values())
    print("[PASS] concurrent live loop shutdown preserves the other loop's shared query and cache")


class OfflineHTTPClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.loop = asyncio.get_running_loop()
        self.is_closed = False
        self.closes = 0

    async def get(self, _url):
        assert asyncio.get_running_loop() is self.loop
        assert not self.is_closed, "client was closed by another loop"
        return "offline response"

    async def aclose(self):
        assert asyncio.get_running_loop() is self.loop, "transport closed from another loop"
        self.closes += 1
        self.is_closed = True


def test_live_loop_http_clients_isolated():
    """Actual concurrent loop threads; all HTTP transport work stays offline."""
    clients, errors = {}, []
    b_ready, a_closed = threading.Event(), threading.Event()
    async def scenario(actor):
        client = await _http.get_client()
        clients[actor] = client
        try:
            reused = await asyncio.gather(*(_http.get_client() for _ in range(3)))
            assert all(value is client for value in reused)
            assert await client.get("offline") == "offline response"
            if actor == "http-A":
                assert await asyncio.to_thread(b_ready.wait, 3)
                assert clients["http-B"] is not client
                await _http.aclose_client()
                assert client.is_closed and client.closes == 1
                assert not clients["http-B"].is_closed
                await _http.aclose_client()  # Idempotent for the current loop.
                assert client.closes == 1
                a_closed.set()
            else:
                b_ready.set()
                assert await asyncio.to_thread(a_closed.wait, 3)
                assert await _http.get_client() is client
                assert await client.get("offline") == "offline response"
        finally:
            await _http.aclose_client()
            assert client.is_closed and client.closes == 1

    def worker(actor):
        try:
            asyncio.run(scenario(actor), debug=True)
        except BaseException as error:
            errors.append(error)

    with patch.dict("os.environ", {"APP_VARIANT": "main"}), \
         patch.object(_http, "get_settings", lambda: SimpleNamespace(http_timeout=37.5)), \
         patch.object(_http.httpx, "AsyncClient", OfflineHTTPClient):
        threads = [threading.Thread(target=worker, args=(actor,), name=actor) for actor in ("http-A", "http-B")]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        assert all(not thread.is_alive() for thread in threads)
    assert not errors, [f"{type(error).__name__}: {error}" for error in errors]
    assert len(clients) == 2 and all(client.is_closed and client.closes == 1 for client in clients.values())
    for client in clients.values():
        options = client.options
        assert options["http2"] is True and options["timeout"] == 37.5
        assert options["follow_redirects"] is True and options["trust_env"] is False
        assert "headers" not in options and "proxy" not in options
        limits = options["limits"]
        assert (limits.max_connections, limits.max_keepalive_connections, limits.keepalive_expiry) == (20, 10, 90.0)
    print("[PASS] concurrent HTTP loops reuse their own clients and close only their own transports")


async def test_http_h2_fallback_and_lm_lifecycle():
    for variant in ("main", "lm"):
        attempts = []
        def factory(**kwargs):
            attempts.append(kwargs)
            if kwargs["http2"]:
                raise ImportError("offline missing h2 fixture")
            return OfflineHTTPClient(**kwargs)
        with patch.dict("os.environ", {"APP_VARIANT": variant}), \
             patch.object(_http.httpx, "AsyncClient", factory), \
             patch.object(_http, "get_settings", lambda: SimpleNamespace(http_timeout=19)):
            await _http.aclose_client()
            client = await _http.get_client()
            assert await _http.get_client() is client
            assert [attempt["http2"] for attempt in attempts] == [True, False]
            assert attempts[0]["trust_env"] is False and attempts[1]["trust_env"] is False
            assert attempts[0]["timeout"] == attempts[1]["timeout"] == 19
            if variant == "lm":
                assert _http._client is client and _http._client_loop is asyncio.get_running_loop()
            await _http.aclose_client()
            assert client.is_closed and client.closes == 1
            if variant == "lm":
                assert _http._client is None and _http._client_loop is None


def test_metrics_outcomes_and_percentiles():
    assert metrics._summary(deque([1, 2, 100]))["p95_ms"] == 100
    before = metrics.snapshot()["outcomes"]
    metrics.record_request("completed")
    metrics.record_request("completed", tool_failed=True)
    metrics.record_request("completed", degraded=True, model_failed=True)
    metrics.record_request("completed", model_failed=True)
    metrics.record_request("cancelled")
    after = metrics.snapshot()["outcomes"]
    assert {key: after[key]-before[key] for key in before} == {
        "successful": 1, "partial": 1, "degraded": 1, "failed": 1, "cancelled": 1}


async def main():
    for test in (test_shared_waiters_and_cancel, test_last_waiter_releases_upstream,
                 test_failure_not_cached, test_bounded_lru_expiry,
                 test_registry_dates_settings_and_mutation, test_dns_off_loop_and_guard_preserved,
                 test_nested_params_snapshot_before_factory, test_http_h2_fallback_and_lm_lifecycle):
        await test()
        print("[PASS]", test.__name__)
    test_metrics_outcomes_and_percentiles()
    print("[PASS] metrics outcomes and nearest-rank percentile")


if __name__ == "__main__":
    asyncio.run(main())
    test_live_loop_shutdown_isolated()
    test_live_loop_http_clients_isolated()
