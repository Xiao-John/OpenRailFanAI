"""产品实测（第 1 轮，96 条车迷测试集）暴露问题的回归测试 —— **全部无网络**。

对应 `docs/test-report-2026-09-14.md` 的 5 个系统性缺陷 + 次级问题。
每条测试都对应报告中的一个"不通过/部分通过"用例编号，防止回退：

| 测试 | 对应用例 | 缺陷 |
|---|---|---|
| `test_yesterday_and_relative_dates` | A02/A06/I05b/I07b | 缺陷 1：「昨天」缺失 |
| `test_weekday_and_month_dates` | L02/L04/I05c | 星期/相对月份的边界 |
| `test_train_code_covers_conventional` | A07/D06/L04 | 缺陷 2：车次类型只认 G/D/C |
| `test_emu_routing_rejects_conventional_honestly` | A07 | 同上（工具层如实说明数据源边界） |
| `test_station_candidate_degradation` | G01★/E05 | 缺陷 4：地名降级不到位 |
| `test_station_lookup_suggests_alternatives` | E07 | 次级：错别字无纠错动作 |
| `test_retrieve_station_fact_forces_lookup` | E01★/E06 | 缺陷 3：知识型误判导致该查站的去查网页 |
| `test_retrieve_ticket_with_train_code` | C04/C09 | 席别问题走了站点查询 |
| `test_retrieve_conventional_train_routing` | A07/D06/R04 | 普速改查时刻 |
| `test_retrieve_non_code_target_is_honest` | A09 | "京沪标杆"被当车组号 |
| `test_retrieve_missing_info_notes` | C06/C08/L03/D09 | 把"没查"说成"查询失败" |
| `test_same_station_od` | F07 | 同站对退化成网页搜索 |
| `test_departed_train_reference_schedule` | D02/D03 | 缺陷 5：已发车分支丢失经停/历时 |
| `test_presale_hint` | C08/L03 | 超预售期被误述为工具故障 |
| `test_search_result_dates` | H02 | 资讯时效不可判 → 反向错误 |
| `test_news_recency_policy_in_prompt` | H02 | 同上（prompt 侧） |
| `test_urban_transit_intent_examples` | K07 | 城市交通被误判为 rail_line |

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_product_fixes.py
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from pathlib import Path

from app.dates import normalize_date, resolve_date
from app.od import parse_od
from app.pipeline import retrieve as retrieve_mod
from app.pipeline.extract import Slots
from app.tools import registry as registry_mod
from app.tools.base import ToolResult

REPO_ROOT = Path(__file__).resolve().parents[2]
TODAY = date.today()


def _rel(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


class _Recorder:
    """替换 registry.invoke_by_name，记录计划而不发任何网络请求。"""

    def __init__(self, responses: dict[str, ToolResult] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses = responses or {}
        self._original = registry_mod.invoke_by_name

    async def __call__(self, name: str, params: dict) -> ToolResult:
        self.calls.append((name, dict(params or {})))
        return self._responses.get(
            name, ToolResult(ok=True, data={"stub": True}, text=f"stub:{name}", sources=[f"s/{name}"])
        )

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def __enter__(self):
        registry_mod.invoke_by_name = self  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        registry_mod.invoke_by_name = self._original  # type: ignore[assignment]
        return False


def _retrieve(intent: str, *, qtype: str | None = None, msg: str | None = None, **slots) -> dict:
    return asyncio.run(
        retrieve_mod.retrieve(intent, Slots(**slots), question_type=qtype, message=msg)
    )


# ---------- 缺陷 1/星期/月份：日期归一化 ----------

def test_yesterday_and_relative_dates():
    """★缺陷 1：'昨天'必须解析为 -1 天（原来返回空串 → 工具回落"今天" → 答"查不到"）。"""
    assert normalize_date("昨天", default_today=False) == _rel(-1)
    assert normalize_date("昨日", default_today=False) == _rel(-1)
    assert normalize_date("昨晚", default_today=False) == _rel(-1)
    assert normalize_date("今早", default_today=False) == TODAY.isoformat()
    assert normalize_date("明晚", default_today=False) == _rel(1)
    # 相邻词不能互相污染（"大前天" 不能命中 "前天"）
    assert normalize_date("大前天", default_today=False) == _rel(-3)
    assert normalize_date("大后天", default_today=False) == _rel(3)
    print(f"[PASS] 相对日期：昨天={_rel(-1)} 昨晚={_rel(-1)} 明晚={_rel(1)} 大前天={_rel(-3)}")


def test_weekday_and_month_dates():
    """星期与相对月份：上周X 必须落在上一周（不能算成本周）。"""
    # 上周六 = 本周一往前一周的周六
    last_sat = TODAY + timedelta(days=(5 - TODAY.weekday()) - 7)
    assert normalize_date("上周六", default_today=False) == last_sat.isoformat()
    this_sat = TODAY + timedelta(days=(5 - TODAY.weekday()) % 7)
    assert normalize_date("本周六", default_today=False) == this_sat.isoformat()
    assert normalize_date("这周六", default_today=False) == this_sat.isoformat()
    nxt_wed = TODAY + timedelta(days=(7 - TODAY.weekday()) + 2)
    assert normalize_date("下周三", default_today=False) == nxt_wed.isoformat()
    # 相对月份：上个月 15 号
    y, m = TODAY.year, TODAY.month - 1
    if m == 0:
        y, m = y - 1, 12
    assert normalize_date("上个月15号", default_today=False) == f"{y:04d}-{m:02d}-15"
    print(f"[PASS] 星期/月份：上周六={last_sat} 本周六={this_sat} 下周三={nxt_wed} 上个月15号={y:04d}-{m:02d}-15")


# ---------- 缺陷 2：车次类型 ----------

def test_train_code_covers_conventional():
    """★缺陷 2：普速车次（K/T/Z/纯数字）必须被识别为车次号。"""
    from app.tools._rt12306 import extract_train_code, is_emu_train_code, is_train_code

    for code in ("K53", "T9", "Z5", "K507", "1461", "G1", "D27", "C1234"):
        assert is_train_code(code), f"{code} 应被识别为车次号"
    for emu in ("G1", "D27", "C1234"):
        assert is_emu_train_code(emu), emu
    for conv in ("K53", "T9", "Z5", "1461"):
        assert not is_emu_train_code(conv), f"{conv} 不是动车组车次"
    # 年份/编号不能被误认成车次（否则 "2026年9月14日" 会被当成车 2026）
    for bad in ("2026", "1999", "12306", "CR400AF", "京沪标杆"):
        assert not is_train_code(bad), f"{bad} 不应被识别为车次号"
    assert extract_train_code("1461次列车") == "1461"
    assert extract_train_code("K53次") == "K53"
    assert extract_train_code("2026年9月14日") is None
    print("[PASS] 车次识别：K53/T9/Z5/1461 通过；年份/编号/车型号被拒")


def test_emu_routing_rejects_conventional_honestly():
    """普速问"担当"时必须说明数据源边界，而不是"格式不正确/查询失败"。"""
    from app.tools import emu_routing

    res = asyncio.run(emu_routing.EmuRoutingTool().invoke({"train": "K53"}))
    assert res.ok is False, res
    assert "普速" in res.error, res.error
    assert "train.schedule" in (res.note or ""), res.note
    print(f"[PASS] 普速担当 -> 如实说明：{res.error}")

    # 非车次号/非车组号的目标同样要明确拒绝（不再发无效请求）
    bad = asyncio.run(emu_routing.EmuRoutingTool().invoke({"train": "京沪标杆"}))
    assert bad.ok is False and "格式不正确" in bad.error, bad
    print("[PASS] 非车次目标 -> 格式校验拒绝")


# ---------- 缺陷 4：地名降级 ----------

def test_station_candidate_degradation():
    """★缺陷 4：'吉林市船营区' 必须能逐级降到 '吉林市' / '吉林'（旗舰句依赖）。"""
    from app.tools._rt12306 import station_candidates

    cands = station_candidates("吉林市船营区")
    assert "吉林市" in cands and "吉林" in cands, cands
    assert cands[0] == "吉林市船营区", cands
    assert "重庆" in station_candidates("重庆主城区")
    assert station_candidates("上海虹桥")[0] == "上海虹桥"
    print(f"[PASS] 站名降级链：{cands}")


def test_station_lookup_suggests_alternatives():
    """错别字场景：未命中时必须给出近似候选与"核对同音字"的提示（E07）。"""
    from app.tools import station_lookup as sl

    class _Fake:
        async def __call__(self, *_a, **_kw):
            return [{"text": '{"success": false, "error": "未找到匹配的车站"}'}]

    original = sl.search_stations_validated
    sl.search_stations_validated = _Fake()          # type: ignore[assignment]
    try:
        res = asyncio.run(sl.StationLookupTool().invoke({"name": "太安站"}))
    finally:
        sl.search_stations_validated = original     # type: ignore[assignment]
    assert res.ok is False, res
    assert ("候选站" in (res.text or "")) or ("同音站" in (res.text or "")), res.text
    assert "同音" in (res.text or ""), res.text
    # 引入 pypinyin 后，同音字纠正应直接把"泰安"排到第一位
    assert "泰安" in (res.text or ""), res.text
    print(f"[PASS] 站点未命中 -> 候选与同音字提示：{(res.text or '')[:60]}…")


# ---------- 缺陷 3/席别/普速/非车次/缺参：检索路由 ----------

def test_retrieve_station_fact_forces_lookup():
    """★缺陷 3：'电报码'之类的问题即使被判知识型，也必须保留站点库精确查询。"""
    with _Recorder() as rec:
        out = _retrieve("station", qtype="knowledge", msg="上海虹桥的电报码是什么？", location="上海虹桥")
    assert "station.lookup" in rec.names(), f"站点库被丢掉了：{rec.calls}"
    assert "web.search" in rec.names(), rec.calls
    assert any("站点库" in n for n in [out["note"]]), out["note"]
    print(f"[PASS] 知识型+站点事实 -> {rec.names()}（保留站点库）")

    # 非站点事实的知识型问题仍只走搜索（不误用实时工具）
    with _Recorder() as rec2:
        _retrieve("emu_routing", qtype="knowledge", msg="CR400AF用的哪个品牌的动力系统？", target="CR400AF")
    assert rec2.names() == ["web.search"], rec2.calls
    print(f"[PASS] 一般知识型 -> {rec2.names()}")


def test_retrieve_ticket_with_train_code():
    """C04/C09：问"某车次还有商务座吗"应查 train.schedule，而不是 station.lookup('G1')。"""
    with _Recorder() as rec:
        _retrieve("ticket", target="G1", time="明天")
    assert "station.lookup" not in rec.names(), f"仍在拿车次号查站点：{rec.calls}"
    assert "train.schedule" in rec.names(), rec.calls
    print(f"[PASS] 车次席别 -> {rec.names()}")


def test_retrieve_conventional_train_routing():
    """A07/D06/L04：普速车次改查实时时刻，并说明担当数据无公开来源。"""
    with _Recorder() as rec:
        out = _retrieve("emu_routing", target="K53", time="今天")
    assert "emu.routing" not in rec.names(), f"普速仍去查交路库：{rec.calls}"
    assert "train.schedule" in rec.names(), rec.calls
    assert "普速" in out["note"], out["note"]
    print(f"[PASS] 普速交路意图 -> {rec.names()}｜{out['note'][:40]}…")


def test_retrieve_non_code_target_is_honest():
    """A09：目标不是车次号/车组号时，不要拿它去查交路，改走检索并说明。"""
    with _Recorder() as rec:
        out = _retrieve("emu_routing", target="京沪标杆", time="今天", msg="京沪标杆今天用什么车？")
    assert "emu.routing" not in rec.names(), rec.calls
    assert "web.search" in rec.names(), rec.calls
    assert "车次号/车组号" in out["note"], out["note"]
    print(f"[PASS] 非车次目标 -> {rec.names()}｜{out['note'][:44]}…")


def test_retrieve_missing_info_notes():
    """C06/C08/D09：缺参数时要说明"缺什么"，不能把"没查"说成"查询失败"。"""
    with _Recorder() as rec:
        out = _retrieve("ticket", location="北京南站", direction="从北京南出发", time="明天")
    assert "ticket.query" not in rec.names(), rec.calls
    assert "缺少完整区间" in out["note"], out["note"]
    print(f"[PASS] 缺到达站 -> {rec.names()}｜{out['note'][:40]}…")

    with _Recorder() as rec2:
        out2 = _retrieve("schedule", direction="上海方向", time="明天下午", msg="明天下午到上海的高铁都有几点的？")
    assert rec2.calls == [], f"缺出发站时不应发起查询：{rec2.calls}"
    assert "缺少出发站" in out2["note"], out2["note"]
    print(f"[PASS] 缺出发站 -> 不发起查询｜{out2['note'][:40]}…")


def test_same_station_od():
    """F07：'北京到北京' 应交给 rail.line 给出明确结论，而不是退化成网页搜索。"""
    assert parse_od("北京到北京") == ("北京", "北京")
    with _Recorder() as rec:
        _retrieve("rail_line", direction="北京到北京", location="北京")
    assert "rail.line" in rec.names(), rec.calls
    print(f"[PASS] 同站径路 -> {rec.names()}")


# ---------- 缺陷 5：已发车分支 ----------

def test_departed_train_reference_schedule():
    """★缺陷 5：已发车时应回退取次日同车次的经停/历时，而不是只回"已发车"。"""
    import app.tools.train_schedule as ts

    tomorrow = _rel(1)
    calls: list[tuple] = []

    class _FakeRT:
        @staticmethod
        async def resolve_station_code(name):
            return ("VNP", "北京南") if "北京" in name else ("AOH", "上海虹桥")

        @staticmethod
        def infer_endpoints_from_offline(code):
            return ("北京南", "上海虹桥")

        @staticmethod
        def is_emu_train_code(code):
            return True

        @staticmethod
        async def query_tickets(from_code, to_code, date_str):
            calls.append(("tickets", date_str))
            if date_str == tomorrow:
                return [{"train_no": "G1", "start_time": "06:30", "arrive_time": "11:24",
                         "duration": "04:54", "seats": {"business": "16"}}]
            return [{"train_no": "G19", "start_time": "07:00", "arrive_time": "12:00",
                     "duration": "05:00", "seats": {}}]

        @staticmethod
        async def query_route_stations(train_no, from_code, to_code, date_str):
            calls.append(("stops", date_str))
            return [{"station_name": "北京南"}, {"station_name": "济南西"}, {"station_name": "上海虹桥"}]

        @staticmethod
        async def query_stops_by_train_no(train_no, from_code, to_code, date_str):
            # 2026-09-15 起：经停表优先按权威 train_no 直查（12306 图定表）
            calls.append(("stops_direct", date_str))
            return [
                {"station_no": "01", "station_name": "北京南", "arrive_time": "----",
                 "start_time": "06:30", "stopover_time": "----"},
                {"station_no": "02", "station_name": "济南西", "arrive_time": "08:03",
                 "start_time": "08:05", "stopover_time": "2分钟"},
                {"station_no": "03", "station_name": "上海虹桥", "arrive_time": "11:24",
                 "start_time": "11:24", "stopover_time": "----"},
            ]

        @staticmethod
        async def resolve_train_identity(train_code, date_str):
            return {"train_no": train_code, "from_station": "北京南",
                    "to_station": "上海虹桥", "from_code": "VNP", "to_code": "AOH"}

    original = ts.rt
    ts.rt = _FakeRT  # type: ignore[assignment]
    try:
        # 2026-09-14 起：参考数据仅在"经停/历时"类问句下才下发（D06 红线修复），
        # 因此这里显式带 include_reference=True（模拟检索层对"经停哪些站"的判断）
        res = asyncio.run(
            ts.TrainScheduleTool().invoke({"train": "G1", "include_reference": True})
        )
        # 默认（问"今天几点到"）不得下发任何次日时刻
        res_default = asyncio.run(ts.TrainScheduleTool().invoke({"train": "G1"}))
    finally:
        ts.rt = original  # type: ignore[assignment]

    assert res.ok, res.error
    assert res.data.get("status") == "departed", res.data
    ref = res.data.get("reference") or {}
    # 2026-09-15 起：经停表直接按权威 train_no 查 12306 图定表（不再靠"次日同车次"旁路），
    # 因此日期是**用户问的那一天**，且明确标注"图定、非实际运行"。
    assert ref.get("date") == TODAY.isoformat(), f"应为用户所问日期：{ref}"
    assert ref.get("is_actual_run") is False, ref
    assert ref.get("stop_count") == 3, ref
    assert "济南西" in res.text and "11:24" in res.text, res.text
    assert "图定时刻表" in res.text and "严禁" in res.text, res.text
    assert any(c[0] == "stops_direct" for c in calls), calls

    assert res_default.data.get("reference") is None, "默认路径不应带时刻块"
    assert "11:24" not in res_default.text and "06:30" not in res_default.text, res_default.text
    print(f"[PASS] 已发车 -> 经停表由 train_no 直查（{ref['date']}，{ref['stop_count']} 站，"
          f"标注图定/非实际运行）；默认路径不泄露任何时刻")


# ---------- 次级问题：预售期 / 资讯时效 / 城市交通 ----------

def test_presale_hint():
    """C08/L03：超出预售期要说明"正常查不到"，而不是让模型说"工具故障"。"""
    from app.tools.ticket_query import _presale_hint

    far = (TODAY + timedelta(days=40)).isoformat()
    near = (TODAY + timedelta(days=3)).isoformat()
    assert "预售期" in _presale_hint(far), _presale_hint(far)
    assert _presale_hint(near) == ""
    assert _presale_hint("") == ""
    print(f"[PASS] 超预售期提示 -> {_presale_hint(far)[:44]}…")


def test_search_result_dates():
    """H02：搜索结果必须尽量带日期（时效可判），无日期要显式标注。"""
    from app.tools.web_search import _extract_date

    assert _extract_date({"title": "沪苏湖高铁正式开通运营", "snippet": "2024年12月26日"}) == "2024-12-26"
    assert _extract_date({"title": "沪苏湖高铁开通", "url": "https://x/2024-12-26/a"}) == "2024-12-26"
    # 只有月日不臆造年份
    assert _extract_date({"title": "预计年底开通 12月26日"}) == "12-26"
    assert _extract_date({"title": "沪苏湖高铁开通了吗"}) == ""
    print("[PASS] 结果日期抽取：完整日期可判、仅月日不臆造年份、无日期返回空")


def test_news_recency_policy_in_prompt():
    """H02：prompt 必须带"时效优先"规则，否则模型会取陈旧条目。"""
    from app.pipeline.generate import build_prompt

    prompt = build_prompt("沪苏湖高铁开通了吗？", Slots(), {"data": [], "sources": [], "tool_trace": [], "note": ""})
    assert "时效优先" in prompt, "缺少资讯时效规则"
    assert "日期最新" in prompt, prompt[:200]
    print("[PASS] 资讯时效规则已注入生成 prompt")


def test_urban_transit_intent_examples():
    """K07：城市交通/地铁类问题要有 few-shot 指引，避免判成 rail_line。"""
    text = (REPO_ROOT / "backend/app/pipeline/intent.py").read_text(encoding="utf-8")
    assert "迪士尼" in text and "地铁" in text, "缺少城市交通 few-shot 示例"
    assert "general + knowledge" in text, text[-400:]
    print("[PASS] 意图 prompt 已补城市交通示例与判定要点")


def main():
    test_yesterday_and_relative_dates()
    test_weekday_and_month_dates()
    test_train_code_covers_conventional()
    test_emu_routing_rejects_conventional_honestly()
    test_station_candidate_degradation()
    test_station_lookup_suggests_alternatives()
    test_retrieve_station_fact_forces_lookup()
    test_retrieve_ticket_with_train_code()
    test_retrieve_conventional_train_routing()
    test_retrieve_non_code_target_is_honest()
    test_retrieve_missing_info_notes()
    test_same_station_od()
    test_departed_train_reference_schedule()
    test_presale_hint()
    test_search_result_dates()
    test_news_recency_policy_in_prompt()
    test_urban_transit_intent_examples()
    print("\n产品实测问题修复回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
