"""Main snapshot wording keeps candidacy distinct from available inventory."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.display_result import serialize_display_results
from app.pipeline import generate, ticket_answer
from app.ticket_copy import (AVAILABLE_TRAIN_COUNT, AVAILABILITY_SNAPSHOT, FARE_REFERENCE,
                             SEAT_NAMES, join_clause, join_clauses, seat_label)
from app.tools import _rt12306 as rt
from app.tools.base import ToolResult
from app.tools.registry import _append_snapshot_tail
from app.tools.ticket_price import TicketPriceTool
from app.tools.ticket_query import _seat_counts

PARAMS = dict(train="G1", date="2026-10-07", from_station="北京南", to_station="上海虹桥")


class TicketCopyTests(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_count_excludes_waitlist_and_unknown(self):
        rows = [{"seats": {"second_class": v}} for v in ("有", "3", "候补", "无", "--", None, "0", "待定")]
        with patch.dict(os.environ, APP_VARIANT="main"):
            self.assertEqual(_seat_counts(rows), {"second_class": 2})
        # Keep the former LM statistics behavior outside the Main change.
        with patch.dict(os.environ, APP_VARIANT="lm"):
            self.assertEqual(_seat_counts(rows), {"second_class": 5})

    async def test_public_failure_identifies_local_dictionary(self):
        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(rt, "ensure_loaded", AsyncMock()), \
                patch.object(rt, "resolve_station_code", AsyncMock(side_effect=[("VNP", "北京南"), ("AOH", "上海虹桥")])), \
                patch("app.data.dict.db_path", return_value=":memory:"), \
                patch("app.data.published_fares.lookup", side_effect=RuntimeError("database unavailable")), \
                patch.object(rt, "query_ticket_prices", AsyncMock()) as online:
            result = await TicketPriceTool().invoke(dict(PARAMS, fare_basis="published"))
        self.assertFalse(result.ok)
        self.assertTrue(result.error.startswith("未取得公布参考票价："))
        self.assertIn("本地词典", result.note)
        self.assertNotIn("接口暂不可用", result.note)
        online.assert_not_awaited()

    async def test_reference_statement_once_per_delivery_channel(self):
        payload = dict(from_station="北京南", to_station="上海虹桥", data=[dict(
            train_code="G1", from_station="北京南", to_station="上海虹桥", prices={"二等座": "661"})])
        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(rt, "query_ticket_prices", AsyncMock(return_value=payload)):
            result = await TicketPriceTool().invoke(PARAMS)
            card = serialize_display_results([dict(tool="ticket.price", query_context=PARAMS,
                data=result.data, sources=result.sources, fetched_at=result.fetched_at, note=result.note)])[0]
        self.assertEqual(result.text.count(FARE_REFERENCE), 1)
        self.assertNotIn("可作为购票参考", result.note)
        self.assertEqual(card["note"].count(FARE_REFERENCE), 1)
        self.assertEqual(card["prices"][0]["amount"], "661")

    async def test_fare_note_never_doubles_sentence_punctuation(self):
        """票价 note 的三段拼接（日期提醒 + 口径句 + 卧铺说明）不得出现「。；」。"""
        payload = dict(from_station="北京南", to_station="上海虹桥", data=[dict(
            train_code="G1", from_station="北京南", to_station="上海虹桥", prices={"软卧": "700"})])
        params = dict(PARAMS, date="下个礼拜三")   # 无法识别 → 触发「已按今天查询」提醒（以「。」结尾）
        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(rt, "query_ticket_prices", AsyncMock(return_value=payload)):
            result = await TicketPriceTool().invoke(params)
        self.assertIn("12306 实际执行票价。", result.note)
        self.assertIn("未细分上、中、下铺", result.note)
        self.assertNotIn("。；", result.note)

    async def test_empty_fare_card_note_never_doubles_sentence_punctuation(self):
        """空结果票价卡的 note（口径句 + 「未找到…」说明）不得出现「。；」。"""
        payload = dict(from_station="北京南", to_station="上海虹桥", data=[])
        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(rt, "query_ticket_prices", AsyncMock(return_value=payload)):
            result = await TicketPriceTool().invoke(PARAMS)
            card = serialize_display_results([dict(tool="ticket.price", query_context=PARAMS,
                data=result.data, sources=result.sources, fetched_at=result.fetched_at, note=result.note)])[0]
        self.assertEqual(card["status"], "empty")
        self.assertNotIn("。；", card["note"])
        self.assertIn("不能据此判断停运或无票", card["note"])

    async def test_fare_card_note_never_doubles_sentence_punctuation(self):
        """结构化票价卡的 note 由工具 note（以「。」结尾）+ 口径说明拼成，不得出现「。；」。"""
        payload = dict(from_station="北京南", to_station="上海虹桥", data=[dict(
            train_code="G1", from_station="北京南", to_station="上海虹桥", prices={"二等座": "661"})])
        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(rt, "query_ticket_prices", AsyncMock(return_value=payload)):
            result = await TicketPriceTool().invoke(PARAMS)
            card = serialize_display_results([dict(tool="ticket.price", query_context=PARAMS,
                data=result.data, sources=result.sources, fetched_at=result.fetched_at, note=result.note)])[0]
        self.assertNotIn("。；", card["note"])
        self.assertIn("12306 实际执行票价。", card["note"])
        self.assertIn(FARE_REFERENCE, card["note"])

    def test_availability_note_segments_never_double_punctuation(self):
        """余票 note 的四段拼接（含以「。」结尾的日期提醒）不得出现「。；」。"""
        from app.tools.ticket_query import _availability_note
        with patch.dict(os.environ, APP_VARIANT="main"):
            note = _availability_note("2026-10-07", "⚠️ 未能识别时间表述「下个礼拜三」，已按今天（2026-10-07）查询。", "")
            self.assertNotIn("。；", note)
            self.assertTrue(note.endswith(AVAILABILITY_SNAPSHOT), note)
            # 低余量说明历史上自带前导「；」，拼接后不得变成「；；」
            with_check = _availability_note("2026-10-07", "", "；低余量席别已二次校验（两次采样一致，仍可能随时变化）")
            self.assertNotIn("；；", with_check)
            self.assertIn("低余量席别已二次校验", with_check)

    def test_join_clauses_dedups_and_never_doubles_punctuation(self):
        """聚合 note（旧 `"；".join(dict.fromkeys(...))`）去重不变，但不得拼出「。；」。"""
        parts = ["[ticket.query] 12306 实时余票（2026-10-07）；余票为查询时快照，…显示为准。",
                 "[ticket.price] 12306 实际执行票价。", "[ticket.price] 12306 实际执行票价。", ""]
        joined = join_clauses(parts)
        self.assertNotIn("。；", joined)
        self.assertNotIn("；；", joined)
        self.assertNotIn("；$", joined)
        self.assertEqual(joined.count("[ticket.price]"), 1)     # 去重仍生效
        self.assertIn("[ticket.query]", joined)
        self.assertEqual(join_clauses([]), "")
        self.assertEqual(join_clauses(["", None]), "")

    def test_receipt_and_fact_render_keep_snapshot_and_raw_waitlist(self):
        data = dict(train_date=PARAMS["date"], from_station="北京南", to_station="上海虹桥",
                    count_all=1, count=1, trains=[dict(train_no="G1", seats={"second_class": "候补"})],
                    seat_counts={"first_class": 1})
        with patch.dict(os.environ, APP_VARIANT="main"):
            body = ticket_answer.render(PARAMS, ToolResult(ok=True, data=data))
            fact = generate._render_fact_text(dict(tool="ticket.query", data=data), SimpleNamespace(fact_table_max_rows=10))
        self.assertIn(AVAILABILITY_SNAPSHOT, body)
        self.assertIn("二等座：候补", body)
        self.assertIn(AVAILABLE_TRAIN_COUNT, fact)
        body_with_note = ticket_answer.render(PARAMS, ToolResult(ok=True, data=data, note=AVAILABILITY_SNAPSHOT))
        self.assertEqual(body_with_note.count(AVAILABILITY_SNAPSHOT), 1)

    def test_snapshot_tail_never_doubles_sentence_punctuation(self):
        """快照复用提示追加到以「。」收尾的 note 之后，不得出现「。；」连写。"""
        note = _append_snapshot_tail(AVAILABILITY_SNAPSHOT, 15, "2026-10-06T13:31:34Z")
        self.assertIn(AVAILABILITY_SNAPSHOT, note)
        self.assertIn("查询快照最多复用 15 秒，采样时间 2026-10-06T13:31:34Z", note)
        self.assertNotIn("。；", note)
        # 原有行为不变：普通 note 仍以「；」分隔；空 note 不留下前导分号
        self.assertEqual(_append_snapshot_tail("12306 实时余票（2026-10-07）", 15, "T"),
                         "12306 实时余票（2026-10-07）；查询快照最多复用 15 秒，采样时间 T")
        self.assertEqual(_append_snapshot_tail("", 15, "T"), "查询快照最多复用 15 秒，采样时间 T")
        # 拼接函数的边界：空 tail 不留尾部分号
        self.assertEqual(join_clause("12306 实际执行票价。", ""), "12306 实际执行票价。")
        self.assertEqual(join_clause("", "尾部"), "尾部")

    def test_seat_label_is_single_source_for_user_facing_copy(self):
        """席别名称只有一处定义；未知键原样透出，不猜测也不丢弃。"""
        import app.fare_result as fare_result
        self.assertEqual(seat_label("business"), "商务座")
        self.assertEqual(seat_label("second_class"), "二等座")
        self.assertEqual(seat_label("unknown_seat"), "unknown_seat")
        # 副本已删除：两处历史映射都改用 ticket_copy 的唯一来源
        self.assertFalse(hasattr(fare_result, "_SEAT_NAMES"))
        self.assertFalse(hasattr(ticket_answer, "_SEAT_NAMES"))
        # 用户可见渲染路径确实在用这个映射
        rendered = ticket_answer.render(PARAMS, ToolResult(ok=True, data=dict(
            train_date=PARAMS["date"], from_station="北京南", to_station="上海虹桥",
            count_all=1, count=1, trains=[dict(train_no="G1", seats={"business": "有"})])))
        self.assertIn("商务座", rendered)
        self.assertNotIn("business", rendered)


if __name__ == "__main__":
    unittest.main()
