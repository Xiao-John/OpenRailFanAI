"""Offline safety and delivery regressions for bounded train-service execution."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.pipeline import service_batch
from app.tools.base import ToolResult

DAY = "2026-10-04"


def result_for(name, params):
    if name == "ticket.query":
        return ToolResult(ok=True, data={"from_station": params["from_station"], "to_station": params["to_station"],
            "train_date": params["date"], "trains": [{"train_no": params["train"], "seats": {"second_class": "候补"}}]},
            sources=["https://example.org/tickets"], total=1, shown=1)
    if name == "ticket.price":
        return ToolResult(ok=True, data={"data": []}, text=f"{params['train']} {params['from_station']}→{params['to_station']}：41.5元（不代表有票）")
    return ToolResult(ok=True, data={"train_code": params["train"], "train_date": params["date"], "stops": [{"station": "甲"}]},
                      sources=["https://example.org/rail"])


class ServiceBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.settings = patch.object(service_batch, "get_settings", return_value=SimpleNamespace(tool_concurrency=2))
        self.settings.start()
        self.addCleanup(self.settings.stop)

    async def invoke(self, name, params):
        self.calls.append((name, dict(params)))
        return result_for(name, params)

    async def execute(self, codes, ops, **kwargs):
        defaults = {"date": DAY, "od": ("北京西", "正定"), "message": "二等座下午"}
        defaults.update(kwargs)
        with patch.object(service_batch.registry, "invoke_by_name", self.invoke):
            return await service_batch.execute(codes, ops, **defaults)

    async def test_ten_distinct_allowed_duplicates_deduplicated(self):
        out = await self.execute([f"G{i}" for i in range(1, 11)] + ["g1", " G2 "], ["schedule", "schedule"])
        self.assertEqual(len(self.calls), 10)
        self.assertEqual(len(out["data"]), 10)
        self.assertTrue(all(p["date"] == DAY and p["include_reference"] is True for _, p in self.calls))

    async def test_eleven_distinct_rejects_every_operation_with_zero_calls(self):
        out = await self.execute([f"G{i}" for i in range(1, 12)], ["schedule", "ticket", "fare", "routing"])
        self.assertEqual(self.calls, [])
        self.assertEqual(out["data"], [])
        self.assertIn("最多支持同时查询 10", out["direct_answer"])
        self.assertIn("未发起查询", out["direct_answer"])

    async def test_all_services_each_train_have_isolated_complete_params(self):
        with patch.object(service_batch, "future_railway_date", return_value=""):
            out = await self.execute(["G1", "G2"], ["schedule", "ticket", "fare", "routing"], recent=True)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(len(out["data"]), 8)
        for name, params in self.calls:
            self.assertEqual(params["date"], None if name == "emu.routing" else DAY)
            self.assertIn(params["train"], {"G1", "G2"})
            if name.startswith("ticket."):
                self.assertEqual((params["from_station"], params["to_station"]), ("北京西", "正定"))
            if name == "ticket.query":
                self.assertEqual((params["after_time"], params["before_time"], params["seat"]), ("12:00", "18:00", "second_class"))
            if name == "emu.routing":
                self.assertTrue(params["recent"])
        self.assertIn("候补", out["direct_answer"])
        self.assertIn("41.5元", out["direct_answer"])

    async def test_missing_interval_skips_only_ticket_and_fare(self):
        for od in [None, ("出发站", "到达站"), ("北京西", "【到达站】")]:
            self.calls.clear()
            with patch.object(service_batch, "future_railway_date", return_value=""):
                out = await self.execute(["G1"], ["ticket", "schedule", "fare", "routing"], od=od)
            self.assertEqual([name for name, _ in self.calls], ["train.schedule", "emu.routing"])
            self.assertEqual(out["direct_answer"].count("实际乘车"), 2)
            self.assertEqual(len(out["data"]), 2)

    async def test_unsupported_routing_and_future_never_call_routing(self):
        out = await self.execute(["K5201"], ["routing", "schedule"])
        self.assertEqual([name for name, _ in self.calls], ["train.schedule"])
        self.assertIn("不支持普速", out["direct_answer"])
        self.calls.clear()
        with patch.object(service_batch, "future_railway_date", return_value="2099-10-05"):
            out = await self.execute(["G1"], ["routing", "schedule"], date="2099-10-05")
        self.assertEqual([name for name, _ in self.calls], ["train.schedule"])
        self.assertEqual(self.calls[0][1]["date"], "2099-10-05")
        self.assertIn("尚未发生", out["direct_answer"])

    async def test_failure_keeps_identity_date_source_and_other_success(self):
        async def invoke(name, params):
            self.calls.append((name, dict(params)))
            if params["train"] == "G1":
                return ToolResult(ok=False, error="真实上游网络失败", sources=["https://example.org/failure"], note="原始失败说明")
            return result_for(name, params)
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            out = await service_batch.execute(["G1", "G2"], ["schedule"], date=DAY, od=None, message="时刻表")
        self.assertEqual(len(out["data"]), 1)
        self.assertEqual(out["data"][0]["data"]["train_code"], "G2")
        self.assertEqual(out["display_errors"], [{"tool": "train.schedule", "train_code": "G1", "query": "G1", "date": DAY, "message": "真实上游网络失败"}])
        self.assertIn("https://example.org/failure", out["sources"])
        self.assertIn("真实上游网络失败", out["direct_answer"])

    async def test_offline_identity_without_stops_is_failure_not_empty_success(self):
        async def invoke(name, params):
            return ToolResult(ok=True, data={"source": "offline-cache", "realtime_error": "12306不可达"}, sources=["https://example.org/offline"])
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            out = await service_batch.execute(["G8931", "G8932"], ["schedule"], date=DAY, od=None, message="时刻表")
        self.assertEqual(out["data"], [])
        self.assertEqual([item["train_code"] for item in out["display_errors"]], ["G8931", "G8932"])
        self.assertTrue(all(item["date"] == DAY for item in out["display_errors"]))
        self.assertIn("12306不可达", out["direct_answer"])

    async def test_concurrency_is_parallel_and_bounded(self):
        active = peak = 0
        entered = asyncio.Event()
        release = asyncio.Event()
        async def invoke(name, params):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            await release.wait()
            active -= 1
            return result_for(name, params)
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            task = asyncio.create_task(service_batch.execute(["G1", "G2", "G3"], ["schedule"], date=DAY, od=None, message="时刻表"))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(peak, 2)
            release.set()
            out = await task
        self.assertEqual(len(out["data"]), 3)
        self.assertEqual(active, 0)
        self.assertEqual(peak, 2)

    async def test_cancellation_drains_running_and_queued_children(self):
        started = asyncio.Event()
        exited = []
        async def invoke(name, params):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.append(params["train"])
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            task = asyncio.create_task(service_batch.execute(["G1", "G2", "G3"], ["schedule"], date=DAY, od=None, message="时刻表"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(set(exited), {"G1", "G2"})

    async def test_tool_mutation_does_not_change_other_params_or_failed_scope(self):
        async def invoke(name, params):
            before = dict(params)
            params["train"] = "G999"
            params["date"] = "2000-01-01"
            self.calls.append((name, before))
            return ToolResult(ok=False, error="故障")
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            out = await service_batch.execute(["G1", "G2"], ["schedule"], date=DAY, od=None, message="时刻表")
        self.assertEqual([item["train_code"] for item in out["display_errors"]], ["G1", "G2"])
        self.assertTrue(all(item["date"] == DAY for item in out["display_errors"]))

    async def test_ticket_fare_failures_and_thrown_exception_remain_per_task(self):
        async def invoke(name, params):
            if name == "train.schedule":
                raise ValueError("internal unrelated exception")
            return ToolResult(ok=False, error="官方接口超时", note="原始接口说明", sources=["https://example.org/failed-ticket"])
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            out = await service_batch.execute(["G1"], ["ticket", "fare", "schedule"], date=DAY,
                                               od=("北京西", "正定"), message="查询")
        self.assertEqual(out["data"], [])
        self.assertEqual(len(out["tool_trace"]), 3)
        self.assertEqual(out["direct_answer"].count("官方接口超时"), 2)
        self.assertIn("工具执行异常（ValueError）", out["direct_answer"])
        self.assertIn("https://example.org/failed-ticket", out["sources"])
        self.assertEqual(out["display_errors"][0]["train_code"], "G1")

    async def test_ambiguous_offline_matches_never_become_unknown_success_card(self):
        async def invoke(name, params):
            return ToolResult(ok=True, data={"matches": [{"train_code": "G893"}], "count": 1}, note="非实时近似匹配")
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            out = await service_batch.execute(["G8931"], ["schedule"], date=DAY, od=None, message="时刻表")
        self.assertEqual(out["data"], [])
        self.assertEqual(out["display_errors"][0]["train_code"], "G8931")

    async def test_recent_routing_has_no_today_filter_and_preserves_each_record_date(self):
        records = [{"train_code": "G1", "date": "2026-10-02", "time": "12:10"},
                   {"train_code": "G1", "date": "2026-10-03", "time": "13:10"}]
        async def invoke(name, params):
            self.calls.append((name, dict(params)))
            return ToolResult(ok=True, data={"query": "G1", "query_mode": "recent", "records": records})
        with patch.object(service_batch.registry, "invoke_by_name", invoke), patch.object(service_batch, "future_railway_date") as future:
            out = await service_batch.execute(["G1"], ["routing"], date="2099-10-05", od=None, message="最近交路", recent=True)
        future.assert_not_called()
        self.assertIsNone(self.calls[0][1]["date"])
        self.assertTrue(self.calls[0][1]["recent"])
        self.assertEqual(out["data"][0]["data"]["records"], records)
        self.assertIn("最近记录（每条保留原始日期）", out["direct_answer"])
        self.assertNotIn("2099-10-05", out["direct_answer"])

    async def test_metrics_report_actual_offline_failure_and_success(self):
        async def invoke(name, params):
            if params["train"] == "G1":
                return ToolResult(ok=True, data={"source": "offline-cache", "realtime_error": "故障"})
            return result_for(name, params)
        with patch.object(service_batch.registry, "invoke_by_name", invoke), patch("app.metrics.record_tool") as metric:
            await service_batch.execute(["G1", "G2"], ["schedule"], date=DAY, od=None, message="时刻表")
        self.assertEqual(metric.call_count, 2)
        self.assertEqual([(c.args[0], c.args[1]) for c in metric.call_args_list], [("train.schedule", False), ("train.schedule", True)])
        self.assertTrue(all(c.args[2] >= 0 for c in metric.call_args_list))

    async def test_fare_success_keeps_integrity_source_note_and_truncation(self):
        async def invoke(name, params):
            return ToolResult(ok=True, text="G1 北京西→正定票价41.5元", total=2, shown=1, truncated=True,
                              fetched_at="2026-10-04T10:00:00Z", note="不表示有票", sources=["https://example.org/fares"])
        with patch.object(service_batch.registry, "invoke_by_name", invoke):
            out = await service_batch.execute(["G1"], ["fare"], date=DAY, od=("北京西", "正定"), message="票价")
        for expected in ["41.5元", "不表示有票", "命中 2 条", "已展示 1 条", "已截断", "2026-10-04T10:00:00Z", "https://example.org/fares"]:
            self.assertIn(expected, out["direct_answer"])


if __name__ == "__main__":
    unittest.main()
