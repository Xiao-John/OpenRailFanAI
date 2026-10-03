"""Offline RT query reuse, isolation, eviction and failed-recovery regressions."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools import _rt12306 as rt
from app import metrics


DAY = "2026-09-30"


class Response:
    status_code = 200

    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class RTPerformance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, APP_VARIANT="main")
        self.environment.start()
        self.scope = patch.object(rt, "query_scope", return_value="scope-A")
        self.scope.start()
        self.loaded = patch.object(rt, "_loaded", True)
        self.loaded.start()
        for cache in (rt._RAW_ROWS_CACHE, rt._TRAIN_ID_CACHE, rt._STOPS_CACHE,
                      rt._SCREEN_CACHE, rt._CAR_DETAIL_CACHE):
            cache.clear()

    async def asyncTearDown(self):
        await rt.close_query_flights()
        self.loaded.stop()
        self.scope.stop()
        self.environment.stop()

    async def test_bounded_eviction_retains_other_fresh_snapshots(self):
        with patch.object(rt.time, "time", return_value=1000):
            cache = {"expired": (800, [0]), "old": (950, [1]), "new": (990, [2])}
            rt._cache_put(cache, "incoming", [3], 100, 3, "rt.screen")
            self.assertEqual(set(cache), {"old", "new", "incoming"})
            rt._cache_put(cache, "next", [4], 100, 3, "rt.screen")
            self.assertEqual(set(cache), {"new", "incoming", "next"})
            self.assertEqual(rt._cache_get(cache, "new", 100, "rt.screen"), [2])
        with patch.object(rt.time, "time", return_value=1090):
            self.assertIsNone(rt._cache_get(cache, "new", 100, "rt.screen"))

    async def test_screen_flights_isolate_results_and_snapshot_date_and_scope(self):
        gate, entered = asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch(code, day):
            calls.append((code, day))
            entered.set()
            await gate.wait()
            return [{"base_datetime": "2026-09-30 09:10:00", "day": day, "data": [1]}]

        with patch.object(rt, "_fetch_station_screen", fetch):
            first = asyncio.create_task(rt.query_station_screen_rows("VNP", DAY))
            await entered.wait()
            second = asyncio.create_task(rt.query_station_screen_rows(station_code="vnp", train_date=DAY))
            await asyncio.sleep(0)
            gate.set()
            a, b = await asyncio.gather(first, second)
            self.assertEqual(len(calls), 1)
            a[0]["data"].append(2)
            self.assertEqual(b[0]["data"], [1])
            warm = await rt.query_station_screen_rows("VNP", DAY)
            self.assertEqual(warm[0]["data"], [1])
            self.assertEqual(warm[0]["base_datetime"], "2026-09-30 09:10:00")
            await rt.query_station_screen_rows("VNP", "2026-10-01")
            with patch.object(rt, "query_scope", return_value="scope-B"):
                await rt.query_station_screen_rows("VNP", DAY)
            self.assertEqual(len(calls), 3)

    async def test_stops_cache_distinguishes_endpoints(self):
        calls = []

        async def fetch(train, origin, destination, day):
            calls.append((origin, destination, day))
            return [{"station_name": origin}, {"station_name": destination}]

        with patch.object(rt, "query_route_stations", fetch):
            first = await rt.query_stops_by_train_no("24000000G1", "VNP", "AOH", DAY)
            first[0]["station_name"] = "mutated"
            second = await rt.query_stops_by_train_no("24000000G1", "VNP", "AOH", DAY)
            self.assertEqual(second[0]["station_name"], "VNP")
            await rt.query_stops_by_train_no("24000000G1", "JGK", "AOH", DAY)
            await rt.query_stops_by_train_no("24000000G1", "VNP", "AOH", "2026-10-01")
            self.assertEqual(len(calls), 3)

    async def test_raw_queries_keep_init_merge_and_cancel_only_abandoned(self):
        gate, entered, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        class Client:
            async def get(self, url, **kwargs):
                calls.append(url)
                if url == rt._LEFT_TICKET_INIT:
                    return Response()
                entered.set()
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                return Response({"status": True, "data": {"result": [
                    "secret|预订|24000000G1|G1|VNP|AOH|VNP|AOH|06:00|11:00"]}})

        async def client():
            return Client()

        with patch("app.tools._http.get_client", client):
            a = asyncio.create_task(rt.query_ticket_rows("VNP", "AOH", DAY))
            await entered.wait()
            b = asyncio.create_task(rt.query_ticket_rows("VNP", "AOH", DAY))
            await asyncio.sleep(0)
            a.cancel()
            await asyncio.gather(a, return_exceptions=True)
            self.assertFalse(cancelled.is_set())
            gate.set()
            result = await b
            self.assertEqual(result[0]["train_no"], "24000000G1")
            self.assertEqual(calls, [rt._LEFT_TICKET_INIT, rt._LEFT_TICKET_QUERY])
            result[0]["train_code"] = "bad"
            self.assertEqual((await rt.query_ticket_rows("VNP", "AOH", DAY))[0]["train_code"], "G1")

            gate.clear()
            entered.clear()
            abandoned = asyncio.create_task(rt.query_ticket_rows("VNP", "AOH", "2026-10-01"))
            await entered.wait()
            abandoned.cancel()
            await asyncio.gather(abandoned, return_exceptions=True)
            self.assertTrue(cancelled.is_set())

    async def test_raw_failure_is_not_cached_as_empty_success(self):
        calls = 0

        class Client:
            async def get(self, url, **kwargs):
                nonlocal calls
                if url == rt._LEFT_TICKET_INIT:
                    return Response()
                calls += 1
                if calls == 1:
                    return Response({"status": False, "data": {"result": []}})
                return Response({"status": True, "data": {"result": [
                    "|预订|24000000G1|G1|VNP|AOH|VNP|AOH|06:00|11:00"]}})

        async def client():
            return Client()

        before = metrics.snapshot()["operations"].get("rt.raw.query", {}).get("failed", 0)
        with patch("app.tools._http.get_client", client):
            self.assertEqual(await rt.query_ticket_rows("VNP", "AOH", DAY), [])
            self.assertTrue(await rt.query_ticket_rows("VNP", "AOH", DAY))
        self.assertEqual(calls, 2)
        self.assertEqual(metrics.snapshot()["operations"]["rt.raw.query"]["failed"], before + 1)

    async def test_tickets_share_only_inflight_and_recheck_stays_fresh(self):
        calls = 0
        gate, entered = asyncio.Event(), asyncio.Event()

        async def fetch(params):
            nonlocal calls
            calls += 1
            entered.set()
            await gate.wait()
            return [{"type": "text", "text": json.dumps({"success": True, "trains": [
                {"train_no": "G1", "seats": {"二等座": str(calls)}}]})}]

        with patch.object(rt, "query_tickets_validated", fetch):
            first = asyncio.create_task(rt.query_tickets("VNP", "AOH", DAY))
            await entered.wait()
            second = asyncio.create_task(rt.query_tickets("VNP", "AOH", DAY))
            await asyncio.sleep(0)
            gate.set()
            a, b = await asyncio.gather(first, second)
            self.assertEqual(calls, 1)
            a[0]["seats"]["二等座"] = "bad"
            self.assertEqual(b[0]["seats"]["二等座"], "1")
            rechecked = await rt.query_tickets("VNP", "AOH", DAY)
            self.assertEqual(calls, 2)
            self.assertEqual(rechecked[0]["seats"]["二等座"], "2")

    async def test_identity_filters_prefix_and_car_failure_recovers(self):
        car_calls = 0

        class Client:
            async def get(self, url, **kwargs):
                nonlocal car_calls
                if url == rt._SEARCH_URL:
                    return Response({"data": [
                        {"station_train_code": "G10", "train_no": "wrong"},
                        {"station_train_code": "G1", "train_no": "right", "from_station": "北京南", "to_station": "上海虹桥"}]})
                car_calls += 1
                if car_calls <= 2:
                    return Response({"content": {"data": {}}})
                return Response({"status": 0, "content": {"data": {"carCode": "CR400BF-A-5159"}}})

        async def client():
            return Client()

        async def no_delay(delay):
            return None

        with patch("app.tools._http.get_client", client), patch("asyncio.sleep", no_delay):
            identity = await rt.search_train_identity("G1", DAY)
            self.assertEqual(identity[0], "right")
            self.assertIsNone(await rt.get_car_detail("G1", DAY))
            self.assertEqual((await rt.get_car_detail("G1", DAY))["car_code"], "CR400BF-A-5159")
            self.assertEqual(car_calls, 3)

    async def test_lm_bypasses_new_flights_and_retains_legacy_keys(self):
        async def fetch(code, day):
            return [{"base_datetime": "original"}]
        with patch.dict(os.environ, APP_VARIANT="lm"), patch.object(rt, "_fetch_station_screen", fetch):
            await rt.query_station_screen_rows("VNP", DAY)
            self.assertIn(("VNP", DAY), rt._SCREEN_CACHE)

    async def test_mcp_observer_preserves_factory_lifecycle_and_context_isolation(self):
        from mcp_12306.services import ticket_service as ts
        clients = []
        gate, entered = asyncio.Event(), asyncio.Event()

        class Client:
            def __init__(self):
                self.closed = False
                self.cookies = {}
                clients.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def get(self, url, **kwargs):
                if url == ts.HTTP_URLS["init"]:
                    self.cookies["session"] = "cookie"
                    return Response()
                self.assert_cookie = self.cookies["session"]
                entered.set()
                await gate.wait()
                return Response()

        before = metrics.snapshot()["operations"].get("rt.tickets.init", {}).get("calls", 0)
        before_query = metrics.snapshot()["operations"].get("rt.tickets.query", {}).get("calls", 0)

        async def request():
            async with ts.create_12306_client() as client:
                await client.get(ts.HTTP_URLS["init"])
                await client.get("https://example.invalid/query")
                self.assertEqual(client.cookies, {"session": "cookie"})

        with patch.object(ts, "create_12306_client", Client):
            rt._install_mcp_observer()
            installed = ts.create_12306_client
            rt._install_mcp_observer()
            self.assertIs(ts.create_12306_client, installed)
            direct = ts.create_12306_client()
            self.assertIsInstance(direct, Client)
            tracked = asyncio.create_task(rt._operation("tickets.mcp", request()))
            await entered.wait()
            untracked = asyncio.create_task(request())
            await asyncio.sleep(0)
            gate.set()
            await asyncio.gather(tracked, untracked)
            self.assertIsInstance(ts.create_12306_client(), Client)
        self.assertTrue(clients[1].closed and clients[2].closed)
        after = metrics.snapshot()["operations"]
        self.assertEqual(after["rt.tickets.init"]["calls"], before + 1)
        self.assertEqual(after["rt.tickets.query"]["calls"], before_query + 1)


if __name__ == "__main__":
    unittest.main()
