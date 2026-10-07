"""Ticket request payload and public answer regressions; all data are offline fixtures."""
from __future__ import annotations

import asyncio
import os
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from app.pipeline import generate, orchestrator, prefetch
from app.pipeline.extract import Slots
from app.dates import normalize_date
from app.pipeline.intent import Intent
from app.pipeline.retrieve import retrieve
from app.tools.base import ToolResult
from app.tools.ticket_query import TicketQueryTool
from app.tools import _rt12306 as rt


DAY = "2026-10-05"
SOURCE = "https://kyfw.12306.cn/otn/leftTicket/queryI"


def fixture(seats=None, **kw):
    return ToolResult(ok=True, data={
        "from_station": "北京南", "to_station": "上海虹桥", "train_date": DAY,
        "trains": [{"train_no": "G1", "from_station": "北京南", "to_station": "上海虹桥", "seats": seats if seats is not None else {"second_class": "有", "first_class": "无"}}],
    }, sources=[SOURCE], fetched_at="2026-10-04T10:00:00Z", total=1, shown=1, **kw)


def fare_fixture(params):
    return ToolResult(ok=True, data={"query_date":normalize_date(params["date"]), "fare_basis":"executed",
        "from_station":params["from_station"], "to_station":params["to_station"],
        "data":[{"train_code":params["train"],"from_station":params["from_station"],"to_station":params["to_station"],"prices":{"二等座":"661"}}]},
        sources=["https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice"], fetched_at="2026-10-04T10:00:00Z")


class TicketDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.variant = patch.dict(os.environ, {"APP_VARIANT": "main"})
        self.variant.start()
        self.addCleanup(self.variant.stop)
        await rt.ensure_loaded()  # Bundled station dictionary, no external query.

    async def _retrieve(self, slots, message, result=None):
        calls = []
        async def invoke(name, params):
            calls.append((name, dict(params)))
            return deepcopy((result if result is not None else fixture()) if name != "ticket.price" else fare_fixture(params))
        with patch("app.tools.registry.invoke_by_name", invoke):
            out = await retrieve("ticket", slots, "realtime", message)
        return out, calls

    async def test_aggregate_note_never_doubles_sentence_punctuation(self):
        """多个工具 note 都以「。」结尾时，聚合 note 不得拼出「。；」。"""
        qnote = ("12306 实时余票（2026-10-05）；余票为查询时快照，"
                 "不能保证购票时仍然有票；请以 12306 购票页面显示为准。")
        pnote = "12306 实际执行票价。"
        seen = []

        async def invoke(name, params):
            seen.append(name)
            if name == "ticket.query":
                return fixture(note=qnote)
            if name == "ticket.price":
                result = fare_fixture(params)
                result.note = pnote
                return result
            return fixture()

        with patch("app.tools.registry.invoke_by_name", invoke):
            out = await retrieve("ticket", Slots(target="G1", time=DAY, direction="北京南→上海虹桥"),
                                 "realtime", f"{DAY} 北京南到上海虹桥 G1 的票价和余票")
        self.assertIn("ticket.query", seen)
        self.assertIn("[ticket.query]", out["note"])
        self.assertNotIn("。；", out["note"])

    async def test_original_missing_interval_is_visible_and_does_not_query(self):
        message = f"查一下 {DAY} G1 的余票"
        out, calls = await self._retrieve(Slots(target="G1", time=DAY), message)
        self.assertEqual(calls, [])
        self.assertIn("出发站", out["direct_answer"])
        self.assertIn("到达站", out["direct_answer"])
        self.assertNotIn("已用 train.schedule", out["note"])
        self.assertEqual(out["data"], [])

    async def test_placeholders_never_reach_station_or_ticket_interface(self):
        for direction in ("出发站→到达站", "[出发站]→[到达站]", "北京南→【到达站】"):
            out, calls = await self._retrieve(Slots(target="G1", time=DAY, direction=direction), f"G1 {DAY} {direction} 余票")
            self.assertEqual(calls, [], direction)
            self.assertIn("实际乘车", out["direct_answer"])

    async def test_full_interval_keeps_train_and_filters_without_schedule(self):
        out, calls = await self._retrieve(Slots(target="G1", time=DAY, direction="北京南→上海虹桥"),
                                         f"查一下 {DAY} G1 北京南到上海虹桥下午二等座的余票")
        self.assertEqual([n for n, _ in calls], ["ticket.query", "ticket.price"])
        self.assertTrue(all(p["train"] == "G1" and normalize_date(p["date"]) == DAY and p["from_station"] == "北京南" and p["to_station"] == "上海虹桥" for _, p in calls))
        p = calls[0][1]
        self.assertEqual((p["train"], p["date"], p["from_station"], p["to_station"]), ("G1", DAY, "北京南", "上海虹桥"))
        self.assertEqual((p["after_time"], p["before_time"], p["seat"]), ("12:00", "18:00", "second_class"))
        self.assertIn("二等座：有", out["direct_answer"])
        self.assertIn(SOURCE, out["direct_answer"])
        self.assertIn("|\n\n命中", out["direct_answer"])

    async def test_service_is_restored_from_current_message_if_fastpath_drops_target(self):
        _, calls = await self._retrieve(Slots(time=DAY, direction="北京南→上海虹桥"), f"{DAY} G1 北京南到上海虹桥余票")
        self.assertEqual(calls[0][1]["train"], "G1")

    async def test_plural_queries_preserve_each_requested_service(self):
        out, calls = await self._retrieve(Slots(time=DAY, direction="北京南→上海虹桥"), f"{DAY} G1 和 G2 北京南到上海虹桥余票")
        self.assertEqual([(n,p["train"]) for n,p in calls], [(n,c) for c in ["G1","G2"] for n in ["ticket.price","ticket.query"]])
        self.assertIn("G1", out["direct_answer"])
        self.assertIn("G2", out["direct_answer"])

    async def test_date_and_interval_are_request_specific(self):
        for date, direction, origin, destination in (("今天", "北京南→上海虹桥", "北京南", "上海虹桥"),
                                                     (DAY, "济南西→南京南", "济南西", "南京南")):
            _, calls = await self._retrieve(Slots(target="G1", time=date, direction=direction), f"G1 {date} {direction}余票")
            self.assertEqual((calls[0][1]["date"], calls[0][1]["from_station"], calls[0][1]["to_station"]), (date, origin, destination))

    async def test_seat_states_are_not_invented_from_missing_data(self):
        for seats, expected in (({}, "数据缺失"), ({"second_class": "--"}, "二等座：未提供"),
                                ({"second_class": "0"}, "二等座：0"), ({"second_class": "无"}, "二等座：无"),
                                ({"second_class": "候补"}, "二等座：候补")):
            out, _ = await self._retrieve(Slots(target="G1", time=DAY, direction="北京南→上海虹桥"), "G1余票", fixture(seats))
            self.assertIn(expected, out["direct_answer"])
            self.assertNotIn("停运", out["direct_answer"])
        out, _ = await self._retrieve(Slots(time=DAY, direction="北京南→上海虹桥"), "余票", fixture(note="低余量数据在两次采样间发生变化", truncated=True))
        self.assertIn("不是全部", out["direct_answer"])
        self.assertIn("两次采样间发生变化", out["direct_answer"])
        self.assertIn("2026-10-04T10:00:00Z", out["direct_answer"])

    async def test_failure_reason_is_visible_without_replacement_facts(self):
        failed = ToolResult(ok=False, error="12306 余票查询失败：网络超时", note="可稍后重试", sources=[SOURCE])
        out, _ = await self._retrieve(Slots(target="G1", time=DAY, direction="北京南→上海虹桥"), "G1余票", failed)
        self.assertIn("网络超时", out["direct_answer"])
        self.assertIn("可稍后重试", out["direct_answer"])
        self.assertEqual(out["tool_trace"], ["ticket.query: failed", "ticket.price: ok"])
        self.assertEqual(set(out["sources"]), {SOURCE, "https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice"})
        self.assertEqual([r["tool"] for r in out["data"]], ["ticket.price"])
        self.assertEqual(out["display_errors"][0]["tool"], "ticket.query")

    async def test_real_ticket_tool_filters_g1_in_g1_g2_fixture(self):
        rows = [{"train_no": code, "from_station": "北京南", "to_station": "上海虹桥",
                 "start_time": "10:00", "seats": {"second_class": "有"}} for code in ("G2", "G1")]
        async def resolve(name):
            return ("VNP", name) if name == "北京南" else ("AOH", name)
        async def invoke(name, params):
            if name == "ticket.price": return fare_fixture(params)
            self.assertEqual(name, "ticket.query")
            return await TicketQueryTool().invoke(params)
        with patch.object(rt, "resolve_station_code", resolve), patch.object(rt, "query_tickets", AsyncMock(return_value=rows)), patch("app.tools.registry.invoke_by_name", invoke):
            out = await retrieve("ticket", Slots(target="G1", time=DAY, direction="北京南→上海虹桥"), "realtime", f"G1 {DAY} 北京南到上海虹桥余票")
        self.assertEqual([t["train_no"] for t in out["data"][0]["data"]["trains"]], ["G1"])
        self.assertNotIn("G2", out["direct_answer"])

    async def test_real_tool_empty_and_filter_empty_are_not_fabricated_availability(self):
        async def resolve(name): return ("VNP", name)
        for rows, seat, expected in (([], None, "无数据"),
                                     ([{"train_no": "G1", "seats": {"second_class": "无"}, "start_time": "10:00"}], "二等座", "筛选条件")):
            async def invoke(name, params):
                return fare_fixture(params) if name == "ticket.price" else await TicketQueryTool().invoke(params)
            with patch.object(rt, "resolve_station_code", resolve), patch.object(rt, "query_tickets", AsyncMock(return_value=rows)), patch("app.tools.registry.invoke_by_name", invoke):
                out = await retrieve("ticket", Slots(target="G1", time=DAY, direction="北京南→上海虹桥", extra=seat), "realtime", "G1余票")
            self.assertIn(expected, out["direct_answer"])
            self.assertNotIn("停运", out["direct_answer"])

    async def test_main_stream_and_block_deliver_same_receipt_without_generation(self):
        for direction, result in ((None, fixture()), ("北京南→上海虹桥", fixture()),
                                  ("北京南→上海虹桥", ToolResult(ok=False, error="接口不可用"))):
            slots = Slots(target="G1", time=DAY, direction=direction)
            async def decision(*a, **kw): return ((Intent.TICKET, "realtime", slots, "llm-merged", "NO_SLOT"), None)
            calls = []
            async def invoke(name, params):
                calls.append((name, params)); return deepcopy(result if name != "ticket.price" else fare_fixture(params))
            with patch.object(orchestrator, "_decide_with_prefetch", decision), patch("app.tools.registry.invoke_by_name", invoke), patch.object(generate, "chat_with_reasoning", AsyncMock(side_effect=AssertionError("no generation"))), patch.object(orchestrator.llm_client, "stream_completion", side_effect=AssertionError("no streaming generation")):
                block = await orchestrator.run(f"查一下 {DAY} G1 的余票")
                events = [e async for e in orchestrator.run_stream(f"查一下 {DAY} G1 的余票")]
            done = [e for e in events if e["type"] == "done"]
            self.assertEqual(len(done), 1)
            self.assertEqual(events[-1]["type"], "done")
            visible = "".join(e["delta"] for e in events if e["type"] == "answer")
            self.assertEqual(visible, block.answer)
            self.assertTrue(visible)
            self.assertTrue(done[0]["answer_done"])
            self.assertFalse(done[0]["degraded"])
            self.assertEqual(done[0]["display_results"], block.display_results)
            if direction is None:
                self.assertEqual(block.display_results, [])
            else:
                self.assertEqual(len(block.display_results), 1)
                self.assertEqual(block.display_results[0]["kind"], "ticket_fare")
                self.assertEqual(block.display_results[0]["train_code"], "G1")
                self.assertEqual(block.display_results[0]["date"], DAY)
                self.assertEqual(block.display_results[0]["fare_status"], "success")
                self.assertEqual(block.display_results[0]["availability"]["status"], "success" if result.ok else "failed")
                self.assertEqual(block.display_results[0]["status"], "success" if result.ok else "partial")
                self.assertEqual(block.display_results[0]["prices"][0]["amount"], "661")
            self.assertEqual(done[0]["sources"], block.sources)
            self.assertEqual(done[0]["tool_trace"], block.tool_trace)
            self.assertFalse(any(e["type"] in {"error", "think"} for e in events))

    async def test_prefetch_does_not_lookup_routing_and_matches_final_ticket_filters(self):
        calls = []
        async def invoke(name, params): calls.append((name, params)); return fixture() if name == "ticket.query" else fare_fixture(params)
        with patch("app.tools.registry.invoke_by_name", invoke):
            self.assertIsNone(prefetch.start(f"查一下 {DAY} G1 的余票"))
            self.assertIsNone(prefetch.start(f"{DAY} G1 [出发站]到[到达站]余票"))
            message = f"{DAY} G1 北京南到上海虹桥下午二等座的余票"
            pf = prefetch.start(message)
            self.assertIsNotNone(pf)
            try:
                out = await retrieve("ticket", Slots(target="G1", time=DAY, direction="北京南→上海虹桥"), "realtime", message, prefetch=pf)
            finally:
                await pf.cancel()
            self.assertEqual([name for name,_ in calls], ["ticket.query","ticket.price"])
            self.assertEqual(sum(name == "ticket.query" for name,_ in calls), 1)
            self.assertTrue(pf.used)
            self.assertIn("二等座", out["direct_answer"])

    async def test_prefetch_ticket_task_is_closed_on_cancel(self):
        closed = asyncio.Event()
        async def wait(*a, **kw):
            try: await asyncio.Event().wait()
            finally: closed.set()
        with patch("app.tools.registry.invoke_by_name", wait):
            pf = prefetch.start("G1 今天北京南到上海虹桥余票")
            await asyncio.sleep(0)
            await pf.cancel()
            self.assertTrue(closed.is_set())

    async def test_prefetch_numeric_service_keeps_filter_and_deictic_does_not_guess(self):
        calls = []
        async def invoke(name, params): calls.append(params); return fixture()
        with patch("app.tools.registry.invoke_by_name", invoke):
            self.assertIsNone(prefetch.start("这趟北京南到上海虹桥的余票呢"))
            self.assertIsNone(prefetch.start("G1和G2北京南到上海虹桥余票"))
            pf = prefetch.start("1461北京到上海余票")
            self.assertIsNotNone(pf)
            await asyncio.sleep(0)
            await pf.cancel()
            self.assertEqual(calls[0]["train"], "1461")

    async def test_fare_receipt_and_legal_action_keep_distinct_tools(self):
        calls = []
        async def invoke(name, params): calls.append(name); return ToolResult(ok=True, data={})
        with patch("app.tools.registry.invoke_by_name", invoke):
            fare = await retrieve("ticket", Slots(target="G1", time=DAY, direction="北京南→上海虹桥"), "realtime", "G1票价多少钱")
            self.assertEqual(calls, ["ticket.price", "ticket.query"])
            self.assertIn("direct_answer", fare)
            calls.clear()
            action = await retrieve("ticket", Slots(target="G1", time=DAY), "realtime", "余票", display_action={"kind": "train_schedule_batch", "trains": ["G1"], "date": DAY})
            self.assertEqual(calls, ["train.schedule"])
            self.assertNotIn("direct_answer", action)

    async def test_lm_keeps_existing_routing_and_generation(self):
        with patch.dict(os.environ, {"APP_VARIANT": "lm"}):
            out, calls = await self._retrieve(Slots(target="G1", time=DAY), "G1余票")
            self.assertEqual(calls[0][0], "train.schedule")
            self.assertNotIn("direct_answer", out)
            with patch.object(generate, "chat_with_reasoning", AsyncMock(return_value=("原有LM回答", ""))) as gen:
                answer, _, _ = await generate.generate("余票", Slots(), {"direct_answer": "不得影响LM", "data": []})
                self.assertEqual(answer, "原有LM回答")
                gen.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
