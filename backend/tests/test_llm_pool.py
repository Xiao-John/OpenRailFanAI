"""Main SDK lifecycle, physical retries and call outcome regressions (offline)."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import patch

import httpx as _httpx
from openai import _base_client
httpx = getattr(_base_client, "httpx2", _httpx)  # Use the installed SDK's HTTP types.
from openai import DefaultAsyncHttpxClient

from app import metrics
from app.config import Settings
from app.llm import client as llm
from app.llm import pool
from app.llm.providers import Provider


SETTINGS = Settings(_env_file=None, llm_mock=False, llm_timeout_s=2,
                    llm_model="fake-model", llm_context_tokens=32000)
PROVIDER = Provider(id="pool-test", label="Offline", api_key="test-secret-a",
                    base_url="https://provider.invalid/v1", model="fake-model", source="env")


class FakeClient:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


async def lifecycle():
    p = pool.ClientPool(capacity=1)
    first = await p.acquire("a", FakeClient)
    same = await p.acquire("a", FakeClient)
    assert first is same and first.borrowers == 2
    busy = await p.acquire("b", FakeClient)
    assert len(p.entries) == 1 and busy.retired and first.client.closed == 0
    await p.release(busy)
    assert busy.client.closed == 1 and not p.ephemeral
    await p.release(same)
    await p.release(first)
    replacement = await p.acquire("b", FakeClient)
    assert first.client.closed == 1
    await p.shutdown()
    assert replacement.client.closed == 0, "shutdown must not close active borrowers"
    await p.release(replacement)
    assert replacement.client.closed == 1
    print("[PASS] bounded LRU, active borrower protection, overflow and shutdown")


async def cancelled_construction():
    p = pool.ClientPool(1)
    client = FakeClient()
    def slow_factory():
        time.sleep(.08)
        return client
    task = asyncio.create_task(p.acquire("a", slow_factory))
    await asyncio.sleep(.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("construction cancellation was swallowed")
    assert client.closed == 1 and not p.entries
    print("[PASS] cancellation during off-loop client construction closes its result")


def completion(request, *, usage=True, text="ok"):
    payload = {"id": "offline", "object": "chat.completion", "created": 0,
               "model": "fake-model", "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": text}}]}
    if usage:
        payload["usage"] = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    return httpx.Response(200, json=payload, request=request)


def factory(handler, created):
    def create(**kwargs):
        client = DefaultAsyncHttpxClient(transport=httpx.MockTransport(handler), **kwargs)
        created.append(client)
        return client
    return create


async def reuse_and_isolation():
    created, requests = [], []
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "fake-model"}]})
        return completion(request)
    with patch.object(llm, "get_settings", lambda: SETTINGS), \
         patch.object(llm, "current_provider", lambda: PROVIDER), \
         patch.object(llm, "DefaultAsyncHttpxClient", factory(handler, created)):
        await llm.shutdown_clients()
        llm.reset_run_metrics()
        await llm.chat_with_reasoning("a")
        await llm.chat_with_reasoning("b")
        assert len(created) == 1
        assert (await llm.list_models(PROVIDER))["ok"]
        assert (await llm.probe_provider(PROVIDER))["ok"]
        assert len(created) == 1, "settings endpoints must use the same leases"
        for changed in (replace(PROVIDER, api_key="test-secret-b"),
                        replace(PROVIDER, extra_headers={"X-Account": "b"}),
                        replace(PROVIDER, timeout_s=3),
                        replace(PROVIDER, model="other"),
                        replace(PROVIDER, structured_model="parser"),
                        replace(PROVIDER, extra_body={"seed": 42}),
                        replace(PROVIDER, base_url="https://other.invalid/v1"),
                        replace(PROVIDER, api="chat_completions")):
            with patch.object(llm, "current_provider", lambda: changed):
                await llm.chat_with_reasoning("isolation")
        assert len(created) == 9, "complete provider config must partition the pool"
        llm.set_active_provider({"max_tokens": 23, "context_tokens": 12000})
        await llm.chat_with_reasoning("request override")
        assert len(created) == 10
        body = json.loads(requests[-1].content)
        assert body["max_tokens"] == 23
        llm.set_active_provider(None)
        assert requests[0].headers["authorization"] == "Bearer test-secret-a"
        assert requests[5].headers["authorization"] == "Bearer test-secret-b"
        assert not any(c.is_closed for c in created)
        await llm.shutdown_clients()
        assert all(c.is_closed for c in created)
        assert llm.get_run_metrics()["total_tokens"] > 0
        assert "test-secret" not in json.dumps(metrics.snapshot())
    print("[PASS] all settings/chat paths reuse clients; credentials, headers, URL, models and overrides isolate")


async def dns_off_loop():
    created, ticks = [], 0
    import threading
    finished = threading.Event()
    def slow_guard(*_):
        time.sleep(.10)
        finished.set()
    async def heartbeat():
        nonlocal ticks
        for _ in range(10):
            ticks += int(not finished.is_set())
            await asyncio.sleep(.01)
    with patch.object(llm, "get_settings", lambda: SETTINGS), \
         patch.object(llm, "current_provider", lambda: PROVIDER), \
         patch.object(llm, "DefaultAsyncHttpxClient", factory(completion, created)), \
         patch("app.llm.providers.guard_request_base_url", slow_guard):
        await asyncio.gather(llm.chat_with_reasoning("dns"), heartbeat())
        await llm.shutdown_clients()
    assert ticks >= 5, "heartbeats must run while DNS validation is blocked"
    print("[PASS] DNS guard and client construction leave the event loop responsive")


async def physical_retry_and_missing_usage():
    created, attempts = [], 0
    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"retry-after-ms": "1"},
                                  json={"error": {"message": "temporarily busy"}})
        return completion(request, usage=False)
    before = metrics.snapshot()
    with patch.object(llm, "get_settings", lambda: SETTINGS), \
         patch.object(llm, "current_provider", lambda: PROVIDER), \
         patch.object(llm, "DefaultAsyncHttpxClient", factory(handler, created)):
        assert await llm.chat_with_reasoning("retry") == ("ok", "")
        await llm.shutdown_clients()
    after = metrics.snapshot()
    assert attempts == 2
    assert after["operations"]["llm.http_attempt"]["calls"] - before["operations"]["llm.http_attempt"]["calls"] == 2
    assert after["operations"]["llm.http_attempt"]["failed"] - before["operations"]["llm.http_attempt"]["failed"] == 1
    assert after["llm"][PROVIDER.id]["calls"] - before["llm"][PROVIDER.id]["calls"] == 1
    assert after["llm"][PROVIDER.id]["failed"] == before["llm"][PROVIDER.id]["failed"]
    print("[PASS] physical SDK retries counted; successful calls without usage counted once")


class EventStream(httpx.AsyncByteStream):
    def __init__(self, *, fail=False, stall=False):
        self.fail, self.stall = fail, stall
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        if self.stall:
            self.waiting.set()
            await asyncio.Event().wait()
        payload = {"id": "s", "object": "chat.completion.chunk", "created": 0,
                   "model": "fake-model", "choices": [{"index": 0, "finish_reason": None,
                     "delta": {"content": "first"}}]}
        yield ("data: " + json.dumps(payload) + "\n\n").encode()
        if self.fail:
            raise httpx.ReadError("test-secret-a upstream echo must not leak")
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed = True


async def stream_outcomes():
    streams, created = [], []
    mode = "success"
    def handler(request):
        stream = EventStream(fail=mode == "failure", stall=mode == "stall")
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    before = metrics.snapshot()["llm"][PROVIDER.id]
    with patch.object(llm, "get_settings", lambda: SETTINGS), \
         patch.object(llm, "current_provider", lambda: PROVIDER), \
         patch.object(llm, "DefaultAsyncHttpxClient", factory(handler, created)):
        assert [piece async for piece in llm.stream_completion("success")] == [("text", "first")]
        mode = "failure"
        try:
            async for _ in llm.stream_completion("error"):
                pass
        except llm.LLMUnavailable as e:
            assert "test-secret" not in str(e)
        else:
            raise AssertionError("mid-stream failures need the LLMUnavailable contract")
        mode = "cancel"
        stream = llm.stream_completion("close")
        assert await stream.__anext__() == ("text", "first")
        # Real consumers can close a generator from a different task context.
        await asyncio.create_task(stream.aclose())
        mode = "stall"
        stream = llm.stream_completion("cancel-prefetch")
        task = asyncio.create_task(stream.__anext__())
        while not streams or not streams[-1].stall:
            await asyncio.sleep(.001)
        await streams[-1].waiting.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await stream.aclose()
        await llm.shutdown_clients()
    after = metrics.snapshot()["llm"][PROVIDER.id]
    assert after["calls"] - before["calls"] == 4
    assert after["failed"] - before["failed"] == 1
    assert after["cancelled"] - before["cancelled"] == 2
    assert all(s.closed for s in streams)
    assert metrics.snapshot()["stages"]["llm.first_text"]["count"] >= 3
    assert all(c.is_closed for c in created)
    print("[PASS] success/failure/cancel recorded once; upstream closes before and after first content")


async def failure_and_open_cancel():
    created, started = [], asyncio.Event()
    mode = "401"
    async def handler(request):
        if mode == "stall":
            started.set()
            await asyncio.Event().wait()
        if mode == "invalid":
            return completion(request, text="invalid JSON")
        return httpx.Response(401, json={"error": {"message": "test-secret-a must not leak"}})
    before = metrics.snapshot()["llm"][PROVIDER.id]
    with patch.object(llm, "get_settings", lambda: SETTINGS), \
         patch.object(llm, "current_provider", lambda: PROVIDER), \
         patch.object(llm, "DefaultAsyncHttpxClient", factory(handler, created)):
        try:
            await llm.chat_with_reasoning("auth")
        except llm.LLMUnavailable as e:
            assert "test-secret" not in str(e)
        else:
            raise AssertionError("401 must fail")
        assert not (await llm.list_models(PROVIDER))["ok"]
        assert not (await llm.probe_provider(PROVIDER, with_models=False))["ok"]
        mode = "invalid"
        try:
            await llm.chat_structured("invalid", {"type": "object"})
        except llm.LLMOutputInvalid:
            pass
        else:
            raise AssertionError("invalid structured output must fail")
        mode = "stall"
        stream = llm.stream_completion("opening cancelled")
        task = asyncio.create_task(stream.__anext__())
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await stream.aclose()
        await llm.shutdown_clients()
    after = metrics.snapshot()["llm"][PROVIDER.id]
    assert after["calls"] - before["calls"] == 5
    assert after["failed"] - before["failed"] == 4
    assert after["cancelled"] - before["cancelled"] == 1
    assert after["prompt_tokens"] - before["prompt_tokens"] == 3
    assert all(c.is_closed for c in created)
    print("[PASS] auth/models/probe/invalid JSON failures and cancelled opening recorded once")


async def exhausted_context_budget():
    created, requests = [], []
    limited = SETTINGS.copy(update={"llm_context_tokens": 512, "llm_max_tokens": 4096})
    messages = [{"role": "user", "content": "占满窗口" * 1000}]
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=EventStream())
        return completion(request)
    with patch.object(llm, "get_settings", lambda: limited), \
         patch.object(llm, "current_provider", lambda: PROVIDER), \
         patch.object(llm, "DefaultAsyncHttpxClient", factory(handler, created)):
        llm.set_active_provider(None)
        assert llm.effective_max_tokens(messages, requested=64) == 64
        # Helpers relying on server/request defaults must obey the same cap.
        assert llm.effective_max_tokens(messages) == 256
        with patch.object(llm, "get_settings", lambda: limited.copy(update={"llm_max_tokens": 64})):
            assert llm.effective_max_tokens(messages) == 64
        llm.set_active_provider({"max_tokens": 64, "context_tokens": 512})
        assert llm.effective_max_tokens(messages) == 64
        assert llm.effective_max_tokens(messages, requested=0) == 0
        await llm.chat_with_reasoning(messages[0]["content"], max_tokens=64)
        assert [piece async for piece in llm.stream_completion(messages[0]["content"], max_tokens=64)]
        assert len(requests) == 2 and all(body["max_tokens"] == 64 for body in requests)
        llm.set_active_provider(None)
        await llm.shutdown_clients()
    print("[PASS] exhausted context preserves explicit/request/default64 caps and zero sentinel on both generation paths")


def loop_isolation():
    ids = []
    async def sample():
        p = pool.current_pool()
        lease = await p.acquire("same", FakeClient)
        ids.append(lease.client)
        await p.release(lease)
        await pool.shutdown_clients()
    asyncio.run(sample())
    asyncio.run(sample())
    assert ids[0] is not ids[1] and all(c.closed == 1 for c in ids)
    print("[PASS] SDK clients never cross event loops")


async def main():
    await lifecycle()
    await cancelled_construction()
    await reuse_and_isolation()
    await dns_off_loop()
    await physical_retry_and_missing_usage()
    await stream_outcomes()
    await failure_and_open_cancel()
    await exhausted_context_budget()


if __name__ == "__main__":
    asyncio.run(main())
    loop_isolation()
