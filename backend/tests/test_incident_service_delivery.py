"""Incident regression: real dispatch/retrieve/delivery, fixed tool boundary only."""
import os
import unittest
from unittest.mock import patch, AsyncMock

from app.pipeline import orchestrator, service_dispatch, fastpath
from app.pipeline.extract import Slots
from app.pipeline.retrieve import retrieve, _wants_stops
from app.tools import _rt12306 as rt
from app.tools.base import ToolResult
from app.tools.ticket_price import TicketPriceTool
from app.models import ChatRequest
from app.api.chat import chat

DAY = "2026-10-04"
BATCH = "在12306上查询一下G8931.G8932这两趟车的时刻表"


def schedule(code):
    return ToolResult(ok=True, data={"train_code": code, "train_date": DAY,
        "source": "12306-timetable", "stops_with_times": True,
        "stops": [{"station": "北京南", "start_time": "10:00"}]}, sources=["https://kyfw.12306.cn"])


class IncidentDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        variant = patch.dict(os.environ, {"APP_VARIANT": "main"})
        variant.start(); self.addCleanup(variant.stop)
        await rt.ensure_loaded()

    async def execute(self, message, action=None, mode="stream", boundary=None):
        calls = []
        async def invoke(name, params):
            calls.append((name, dict(params)))
            return boundary(name, params) if boundary else schedule(params.get("train"))
        with patch("app.tools.registry.invoke_by_name", invoke), \
                patch.object(orchestrator.planner, "decide", AsyncMock(side_effect=AssertionError("must not ask decision model"))), \
                patch.object(orchestrator.llm_client, "stream_completion", side_effect=AssertionError("must not ask generation model")), \
                patch("app.pipeline.generate.chat_with_reasoning", AsyncMock(side_effect=AssertionError("must not ask generation model"))):
            if mode == "stream":
                events = [e async for e in orchestrator.run_stream(message, display_action=action)]
                self.assertFalse([e for e in events if e["type"] == "error"], events)
                done = [e for e in events if e["type"] == "done"]
                self.assertEqual(len(done), 1)
                self.assertTrue(done[0]["answer_done"])
                self.assertFalse(done[0]["degraded"])
                return done[0], calls, "".join(e.get("delta", "") for e in events if e["type"] == "answer")
            result = await orchestrator.run(message, display_action=action)
            return result.dict(), calls, result.answer

    async def test_exact_screenshot_batch_stream_and_block_match(self):
        stream, calls, answer = await self.execute(BATCH)
        self.assertEqual([p["train"] for _, p in calls], ["G8931", "G8932"])
        self.assertTrue(all(p["include_reference"] for _, p in calls))
        block, _, _ = await self.execute(BATCH, mode="block")
        self.assertEqual(stream["display_results"], block["display_results"])
        self.assertEqual(stream["display_results"][0]["status"], "success")
        self.assertIn("卡片", answer)

    async def test_action_payload_bypasses_text_and_model(self):
        action = {"kind": "train_schedule_batch", "trains": ["G2", "G1"], "date": "2026-10-05"}
        for mode in ("stream", "block"):
            _, calls, _ = await self.execute("为什么 G999 的余票", action, mode)
            self.assertEqual([p["train"] for _, p in calls], ["G2", "G1"])
            self.assertTrue(all(p["date"] == action["date"] for _, p in calls))
        text = "、".join(f"G{i}" for i in range(1, 12)) + "的票价"
        _, calls, _ = await self.execute(text, {"kind": "train_schedule_batch", "trains": ["G2"], "date": "2026-10-05"})
        self.assertEqual([p["train"] for _, p in calls], ["G2"])

    async def test_block_api_forwards_structured_action_and_request_provider(self):
        action = {"kind": "train_schedule_batch", "trains": ["G2"], "date": "2026-10-05"}
        request = ChatRequest(message="G999 的余票", display_action=action, provider="siliconflow", model="a-test-model", api_key="offline-test-key")
        with patch.object(orchestrator, "run", AsyncMock(return_value="result")) as run:
            self.assertEqual(await chat(request), "result")
        kwargs = run.await_args.kwargs
        self.assertEqual(kwargs["display_action"], action)
        self.assertEqual(kwargs["llm"]["api_key"], "offline-test-key")
        self.assertEqual(kwargs["llm"]["provider"], "siliconflow")

    async def test_partial_and_all_failures_remain_visible_and_scoped(self):
        for failed in ({"G8932"}, {"G8931", "G8932"}):
            def boundary(name, params):
                return ToolResult(ok=False, error="真实接口超时") if params["train"] in failed else schedule(params["train"])
            done, _, _ = await self.execute(BATCH, boundary=boundary)
            items = done["display_results"][0]["items"]
            self.assertEqual({i["train_code"] for i in items}, {"G8931", "G8932"})
            self.assertEqual({i["train_code"] for i in items if i["status"] == "failed"}, failed)
            self.assertTrue(all(i["date"] for i in items))
            self.assertTrue(all(i["error"] == "真实接口超时" for i in items if i["status"] == "failed"))

    async def test_static_identity_is_failure_not_empty_schedule_success(self):
        def boundary(name, params):
            return ToolResult(ok=True, data={"source": "offline-cache", "train_code": params["train"], "realtime_error": "12306 拒绝请求"})
        done, _, _ = await self.execute(BATCH, boundary=boundary)
        self.assertTrue(all(i["status"] == "failed" and "12306 拒绝请求" in i["error"] and i["date"] for i in done["display_results"][0]["items"]))
        self.assertEqual([t.split("（")[0] for t in done["tool_trace"]], ["train.schedule: failed", "train.schedule: failed"])

    async def test_ticket_missing_interval_reaches_clarification_without_model(self):
        _, calls, answer = await self.execute("查一下 2026-10-05 G1 的余票")
        self.assertEqual(calls, [])
        self.assertIn("出发站", answer); self.assertIn("到达站", answer)

    async def test_ticket_complete_interval_reaches_only_ticket_query(self):
        def boundary(name, params):
            return ToolResult(ok=True, data={"trains": [{"train_no": "G1", "seats": {"second_class": "候补"}}]})
        _, calls, answer = await self.execute("查一下 2026-10-05 G1 北京南到上海虹桥的余票", boundary=boundary)
        self.assertEqual([n for n, _ in calls], ["ticket.query"])
        self.assertEqual(calls[0][1]["train"], "G1")
        self.assertIn("二等座：候补", answer)

    async def test_ticket_entry_initializes_station_index_before_retrieval(self):
        with patch.object(rt, "ensure_loaded", AsyncMock()) as loaded, \
                patch.object(orchestrator.planner, "decide", AsyncMock(side_effect=AssertionError("must bypass"))):
            decision, pf = await orchestrator._decide_with_prefetch("G1 北京南到上海虹桥明天的余票", None)
            loaded.assert_awaited_once()
            self.assertEqual(decision[0].value, "ticket")
            self.assertIsNone(pf)

    async def test_fare_neighbor_destination_is_not_relabelled_as_requested_station(self):
        rows = [
            {"train_code": "K5201", "from_station": "北京西", "to_station": "正定", "start_time": "06:17", "arrive_time": "09:16", "duration": "02:59", "prices": {"硬座": 41.5}},
            {"train_code": "K5201", "from_station": "北京西", "to_station": "石家庄", "start_time": "06:17", "arrive_time": "09:35", "duration": "03:18", "prices": {"硬座": 43.5}},
            {"train_code": "K5202", "from_station": "北京西", "to_station": "正定", "prices": {"硬座": 99}},
        ]
        payload = {"success": True, "data": rows, "from_station": "北京西", "to_station": "正定"}
        with patch.object(rt, "query_ticket_price_validated", AsyncMock(return_value=payload)), \
                patch.object(rt, "parse_mcp_result", side_effect=lambda x: x), \
                patch.object(rt, "resolve_station_code", AsyncMock(side_effect=[("BXP", "北京西"), ("ZDP", "正定")])):
            result = await TicketPriceTool().invoke({"from_station": "北京西", "to_station": "正定", "date": "2026-10-05", "train": "K5201"})
        self.assertTrue(result.ok)
        self.assertEqual(result.data["data"], rows[:1])
        self.assertIn("北京西→正定", result.text)
        self.assertIn("41.5", result.text)
        self.assertNotIn("石家庄", result.text)
        self.assertNotIn("43.5", result.text)

    async def test_fare_screenshot_delivers_actual_tool_receipt_without_model(self):
        def boundary(name, params):
            return ToolResult(ok=True, text="K5201 北京西→正定 06:17–09:16：硬座 41.5 元；不代表当前有票。")
        _, calls, answer = await self.execute("查询10月5日 K5201北京西到正定的票价", boundary=boundary)
        self.assertEqual([n for n, _ in calls], ["ticket.price"])
        self.assertEqual(calls[0][1]["train"], "K5201")
        self.assertEqual(calls[0][1]["to_station"], "正定")
        self.assertIn("41.5", answer)

    async def test_routing_screenshot_empty_is_not_model_error_or_fabricated_record(self):
        def boundary(name, params):
            return ToolResult(ok=False, error="rail.re 未返回 CR400AF5033 的交路记录")
        done, calls, _ = await self.execute("CR400AF-5033 今天的交路", boundary=boundary)
        self.assertEqual(calls, [("emu.routing", {"emu_no": "CR400AF-5033", "date": "今天"})])
        self.assertEqual(done["display_results"][0]["kind"], "empty")
        self.assertIsNone(done["error"])

    async def test_routing_success_delivers_units_without_generation(self):
        def boundary(name, params):
            return ToolResult(ok=True, data={"kind": "emu", "query": "CR400BF5033", "focus_date": DAY,
                "records": [{"train_code": "G1", "date": DAY, "time": "10:00", "emu_no": "CR400BF5033"}]})
        done, _, _ = await self.execute("CR400BF-5033 今天的交路", boundary=boundary)
        self.assertEqual(done["display_results"][0]["records"][0]["units"][0]["emu_no"], "CR400BF5033")

    async def test_recent_action_and_natural_query_preserve_mode(self):
        def boundary(name, params):
            return ToolResult(ok=True, data={"kind": "emu", "query": "CR400BF5033", "query_mode": "recent", "focus_date": None,
                "records": [{"train_code": "G1", "date": "2026-10-01", "time": "10:00", "emu_no": "CR400BF5033"}]})
        for action in (None, {"kind": "emu_routing", "query": "CR400BF-5033", "date": None, "recent": True}):
            done, calls, _ = await self.execute("查看 CR400BF-5033 最近交路记录", action, boundary=boundary)
            self.assertTrue(calls[0][1]["recent"])
            self.assertEqual(done["display_results"][0]["records"][0]["date"], "2026-10-01")

    def test_train_numbers_do_not_consume_service_name_or_date(self):
        self.assertEqual(fastpath._train_code(BATCH), "G8931")
        self.assertEqual(fastpath._train_code("11月5日 G1234 的时刻表"), "G1234")
        self.assertEqual(fastpath._train_code("查1461次时刻表"), "1461")
        self.assertEqual(service_dispatch.train_codes("1461、1462的时刻表"), ["1461", "1462"])
        self.assertEqual(service_dispatch.train_codes("1461、1462时刻表"), ["1461", "1462"])
        for text, expected in (("G1、G2 2等座余票", ["G1", "G2"]), ("G1 的票价是41.5元吗", ["G1"]), ("G1 8节编组的交路", ["G1"]), ("G1 100元以内的票价", ["G1"])):
            self.assertEqual(service_dispatch.train_codes(text), expected)
        self.assertTrue(_wants_stops(BATCH))
        self.assertEqual(service_dispatch.decide("G1大后天的余票")[2].time, "大后天")
        self.assertEqual(service_dispatch.decide("G1 2026/10/05 的余票")[2].time, "2026/10/05")
        for text in ("那 G1 的余票呢", "这个区间还有票吗"):
            self.assertIsNone(service_dispatch.decide(text, history=[{"role": "user", "content": "北京南到上海虹桥明天G1"}]))
        with patch.dict(os.environ, {"APP_VARIANT": "lm"}):
            self.assertIsNone(service_dispatch.decide(BATCH))
            self.assertFalse(_wants_stops(BATCH))

    def test_complex_queries_are_not_silently_projected(self):
        for text in ("G1明天和G2后天的时刻表", "G1和G2为什么时刻表不同", "G1农历初五的余票", "G1今天的余票，G2的交路", "那这趟明天的余票呢", "G1 2026.10.05 的余票", "G1 2025年10月5日的余票"):
            result = service_dispatch.decide(text)
            self.assertTrue(result is None or result[2].raw.get("service_query", {}).get("clarification"), text)

    async def test_integrated_ten_train_fares_and_overflow_without_any_tool_calls(self):
        ten = "、".join(f"G{i}" for i in range(1, 11))
        def boundary(name, params):
            return ToolResult(ok=True, text=f"{params['train']} 北京南→上海虹桥：41.5元（不代表当前有票）")
        _, calls, answer = await self.execute(f"{ten} 2026-10-05 北京南到上海虹桥的票价", boundary=boundary)
        self.assertEqual(len(calls), 10)
        self.assertEqual({p['train'] for _, p in calls}, {f'G{i}' for i in range(1, 11)})
        self.assertIn("G10", answer)
        _, calls, answer = await self.execute(f"{ten}、G11 的票价")
        self.assertEqual(calls, [])
        self.assertIn("超出", answer)
        self.assertIn("10", answer)

    async def test_common_multiple_operations_are_kept_for_each_train(self):
        def boundary(name, params):
            if name == "train.schedule":
                return schedule(params['train'])
            return ToolResult(ok=True, text=f"{params['train']} 票价41.5元", data={"trains": [{"train_no": params['train'], "seats": {"second_class": "有"}}]})
        _, calls, answer = await self.execute("G1、G2 2026-10-05 北京南到上海虹桥的时刻表、余票和票价", boundary=boundary)
        self.assertEqual(len(calls), 6)
        self.assertEqual({n for n, _ in calls}, {"train.schedule", "ticket.query", "ticket.price"})
        self.assertIn("G1", answer); self.assertIn("G2", answer)

    async def test_legacy_multitrain_expansion_does_not_undo_date_defer(self):
        with patch("app.tools.registry.invoke_by_name", AsyncMock()) as tool:
            result = await retrieve("ticket", Slots(time="明天", direction="北京南→上海虹桥"), "realtime", "G1、G2农历初五的余票")
        tool.assert_not_awaited()
        self.assertIn("确认", result["direct_answer"])
        result = service_dispatch.decide("G1北京南到上海虹桥、G2济南西到南京南的票价")
        self.assertIn("多个乘车区间", result[2].raw["service_query"]["clarification"])

    async def test_ten_real_receipts_are_not_cut_as_model_repetition(self):
        def boundary(name, params):
            return ToolResult(ok=True, text="共同来源说明" * 80,
                              sources=["https://kyfw.12306.cn"], note="这是该区间的票价，不代表当前有票。", total=1, shown=1)
        trains = "、".join(f"G{i}" for i in range(1, 11))
        for mode in ("stream", "block"):
            done, calls, answer = await self.execute(f"{trains} 北京南到上海虹桥明天的票价", mode=mode, boundary=boundary)
            self.assertEqual(len(calls), 10)
            self.assertFalse(done["truncated"])
            self.assertIn("G10", answer)


if __name__ == "__main__":
    unittest.main()
