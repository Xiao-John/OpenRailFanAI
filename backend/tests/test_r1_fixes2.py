"""R1 缺陷第 2 批修复的回归测试（**全部无网络**）。

| 测试 | R1 缺陷 | 说明 |
|---|---|---|
| `test_slots_empty_falls_back_to_message` | P0-3（F01/D05） | 槽位为空时从原话兜底解析区间/车次号 |
| `test_realtime_intent_never_answers_with_web_search_only` | P0-3 | 实时意图缺参时明确追问，不用网页搜索顶替 |
| `test_unknown_train_code_reaches_tool` | P0-3（K01） | G99999 这类"不存在的车次"要交给数据源判定 |
| `test_railre_404_means_not_found_not_failure` | P0-3（K01） | 404 应给出"不存在"的明确结论 |
| `test_hub_probe_recovers_missing_train` | P1-6（D04） | 离线目录未命中时用枢纽区间探测起止站 |
| `test_news_searches_twice_with_freshness` | P1-7（H01/H02） | 资讯类"搜两遍"：一遍时效过滤 |
| `test_no_time_anchor_by_inference_rule` | P1-7（H02） | prompt 禁止由推理反推时间节点 |
| `test_low_count_second_verification` | P2-8（C03） | 低余量二次校验 + 变动如实标注 |
| `test_knowledge_numeric_uncertainty_rule` | P2-9 | 知识型精确数值"宁少勿错"规则 |

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_r1_fixes2.py
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.pipeline import retrieve as retrieve_mod
from app.pipeline.extract import Slots
from app.pipeline.generate import build_prompt
from app.tools import emu_routing as er
from app.tools import registry as registry_mod
from app.tools import ticket_query as tq
from app.tools import train_schedule as ts
from app.tools.base import ToolResult


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._original = registry_mod.invoke_by_name

    async def __call__(self, name: str, params: dict) -> ToolResult:
        self.calls.append((name, dict(params or {})))
        return ToolResult(ok=True, data={}, text=f"stub:{name}", sources=[f"s/{name}"])

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def params_of(self, name: str) -> list[dict]:
        return [p for n, p in self.calls if n == name]

    def __enter__(self):
        registry_mod.invoke_by_name = self  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        registry_mod.invoke_by_name = self._original  # type: ignore[assignment]
        return False


def _retrieve(intent: str, *, msg: str | None = None, qtype: str = "realtime", **slots) -> dict:
    return asyncio.run(
        retrieve_mod.retrieve(intent, Slots(**slots), question_type=qtype, message=msg)
    )


# ---------- P0-3：路由兜底 ----------

def test_slots_empty_falls_back_to_message():
    """F01：'北京到上海走哪条线路？' 抽不出槽位时，必须从原话解析出区间并调 rail.line。"""
    with _Recorder() as rec:
        out = _retrieve("rail_line", msg="北京到上海走哪条线路？")   # slots 全空
    assert "rail.line" in rec.names(), f"rail.line 仍未调用：{rec.calls}"
    params = rec.params_of("rail.line")[0]
    assert params["from_station"] == "北京" and params["to_station"] == "上海", params
    assert "web.search" not in rec.names(), f"不应退化成网页搜索：{rec.calls}"
    assert out["tool_trace"], out
    print(f"[PASS] F01 原话兜底 -> {rec.names()}（{params['from_station']}→{params['to_station']}）")

    # D05：裸数字车次（无"次"字）
    with _Recorder() as rec2:
        _retrieve("schedule", msg="1461 都经过哪些站？几点开？")
    assert "train.schedule" in rec2.names(), f"train.schedule 仍未调用：{rec2.calls}"
    assert rec2.params_of("train.schedule")[0].get("train") == "1461", rec2.params_of("train.schedule")
    print(f"[PASS] D05 原话兜底 -> {rec2.names()}（train=1461）")


def test_realtime_intent_never_answers_with_web_search_only():
    """P0-3 规则：实时意图缺参时明确追问，不得用网页搜索顶替实时数据源。"""
    with _Recorder() as rec:
        out = _retrieve("rail_line", msg="这趟车走哪条线？")        # 无区间也无车次
    assert rec.calls == [], f"缺参时不应发起任何调用：{rec.calls}"
    assert "请用户补充区间" in out["note"] or "未识别出发站" in out["note"], out["note"]
    print(f"[PASS] 径路缺参 -> 不调用工具，明确追问：{out['note'][:44]}…")

    with _Recorder() as rec2:
        out2 = _retrieve("ticket", msg="还有票吗？")
    assert "web.search" not in rec2.names(), rec2.calls
    assert "未识别出发站与到达站" in out2["note"], out2["note"]
    print(f"[PASS] 余票缺参 -> 不退化搜索：{out2['note'][:44]}…")


def test_unknown_train_code_reaches_tool():
    """K01：G99999 这类不存在的车次要进入 emu.routing（由数据源判定），不再被本地正则挡掉。"""
    with _Recorder() as rec:
        _retrieve("emu_routing", target="G99999", time="今天", msg="今天 G99999 由哪组担当？")
    assert "emu.routing" in rec.names(), f"未调用 emu.routing：{rec.calls}"
    assert rec.params_of("emu.routing")[0].get("train") == "G99999", rec.params_of("emu.routing")
    assert "web.search" not in rec.names(), f"不应退回网页搜索：{rec.calls}"
    print(f"[PASS] K01 -> {rec.names()}（train=G99999 交给数据源判定）")


def test_railre_404_means_not_found_not_failure():
    """K01：404 必须给出"不存在"的明确结论，而不是泛泛的"查询失败"。"""
    import httpx

    class _Client:
        async def get(self, url, **_kw):
            req = httpx.Request("GET", url)
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("404 Not Found", request=req, response=resp)

    async def _fake_get_client():
        return _Client()

    # P0-2 起工具层不再各自 `httpx.AsyncClient(...)`，而是向共享入口取 client，
    # 所以假实现挂在 `get_client` 上（`httpx.HTTPStatusError` 仍是真实类，无需替换）。
    with patch.object(er, "get_client", _fake_get_client):
        res = asyncio.run(er.EmuRoutingTool().invoke({"train": "G99999"}))
    assert res.ok is False, res
    assert "不存在" in res.error and "404" in res.error, res.error
    print(f"[PASS] K01 404 -> {res.error}")


# ---------- P1-6：离线目录缺口 ----------

def test_hub_probe_recovers_missing_train():
    """D04：离线目录没有 G101 时，用枢纽区间实时探测出起止站后继续查询。"""
    calls: list[str] = []

    class _FakeRT:
        @staticmethod
        def infer_endpoints_from_offline(code):
            return None                     # 目录未命中（模拟 2022 停更数据）

        @staticmethod
        def is_emu_train_code(code):
            return True

        @staticmethod
        async def resolve_station_code(name):
            table = {"北京": ("BJP", "北京"), "上海": ("SHH", "上海"),
                     "北京南": ("VNP", "北京南"), "上海虹桥": ("AOH", "上海虹桥")}
            if name in table:
                return table[name]
            return ("VNP", name)

        @staticmethod
        async def query_tickets(from_code, to_code, date_str):
            calls.append(f"{from_code}->{to_code}")
            # 探测区间（北京→上海）与实际区间（北京南→上海虹桥）都能查到 G101
            if (from_code, to_code) in (("BJP", "SHH"), ("VNP", "AOH")):
                return [{"train_no": "G101", "from_station": "北京南", "to_station": "上海虹桥",
                         "start_time": "06:10", "arrive_time": "11:00", "duration": "04:50",
                         "seats": {"second_class": "有"}}]
            return []

        @staticmethod
        async def query_route_stations(*_a):
            return [{"station_name": "北京南"}, {"station_name": "上海虹桥"}]

    original = ts.rt
    ts.rt = _FakeRT  # type: ignore[assignment]
    try:
        res = asyncio.run(ts.TrainScheduleTool().invoke({"train": "G101", "date": "明天"}))
    finally:
        ts.rt = original  # type: ignore[assignment]

    assert res.ok, f"探测后应能查到：{res.error}"
    assert calls, "未发生任何探测调用"
    assert "实时探测" in (res.note or ""), res.note
    assert res.data.get("start_time") == "06:10", res.data
    print(f"[PASS] D04 枢纽探测 -> 探测 {len(calls)} 次后命中：{res.data.get('start_time')} 发车｜{res.note[:40]}…")


# ---------- P1-7：资讯时效 ----------

def test_news_searches_twice_with_freshness():
    """H01/H02：资讯类要"搜两遍"——一遍带时效过滤（近一月），一遍不滤。"""
    with _Recorder() as rec:
        _retrieve("news", target="沪苏湖高铁", msg="沪苏湖高铁开通了吗？现在什么状态？")
    searches = rec.params_of("web.search")
    assert len(searches) == 2, f"应为两遍搜索，实际 {len(searches)}：{searches}"
    assert searches[0].get("freshness") == "month", searches
    assert not searches[1].get("freshness"), searches
    print(f"[PASS] 资讯搜两遍 -> freshness={searches[0].get('freshness')} + 无过滤")

    # 非时效类问题仍然只搜一遍
    with _Recorder() as rec2:
        _retrieve("general", target="CR400AF", msg="CR400AF 是哪个厂生产的？")
    assert len(rec2.params_of("web.search")) == 1, rec2.calls
    print("[PASS] 一般问题仍只搜一遍")


def test_web_search_freshness_note():
    """时效过滤必须体现在 note 里（Bing 支持、百度不支持时也要说明）。"""
    from app.tools import web_search as ws

    async def _fake_get_client():
        return _FakeHttpx({                                  # type: ignore[arg-type]
            "cn.bing.com": _FakeResp(_BING_OK),
        })

    with patch.object(ws, "get_client", _fake_get_client):
        res = asyncio.run(ws.WebSearchTool2().invoke({"q": "沪苏湖高铁 开通", "freshness": "month"}))
    assert res.ok, res.error
    assert "时效过滤" in res.note, res.note
    print(f"[PASS] 时效过滤标注 -> {res.note[:60]}…")


_BING_OK = """
<li class="b_algo"><h2><a href="https://x/1">沪苏湖高铁开通运营 报道</a></h2><p>2024年12月26日 开通</p></li>
<li class="b_algo"><h2><a href="https://x/2">沪苏湖高铁 开通 最新进展</a></h2><p>开通运营</p></li>
"""


class _FakeHttpx:
    def __init__(self, mapping: dict):
        self._mapping = mapping

    def AsyncClient(self, **_kw):  # noqa: N802
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, **_kw):
        for key, resp in self._mapping.items():
            if key in url:
                return resp
        raise RuntimeError(f"未预置 URL：{url}")


class _FakeResp:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_no_time_anchor_by_inference_rule():
    """H02：prompt 必须禁止由"开通 N 周年"这类推理反推时间节点。"""
    prompt = build_prompt(
        "京沪高铁现在跑 350 了吗？", Slots(target="京沪高铁"),
        {"data": [], "sources": [], "tool_trace": ["web.search: ok"], "note": ""},
    )
    assert "禁止由推理反推时间节点" in prompt, "缺少反推禁令"
    assert "15 周年" in prompt or "开通 N 周年" in prompt, prompt[:120]
    print("[PASS] prompt 含「禁止由推理反推时间节点」规则")


# ---------- P2-8 / P2-9 ----------

def test_low_count_second_verification():
    """C03：低余量（≤3 张）必须二次采样；若两次不同，如实标注"数据在变"。"""
    calls = {"n": 0}

    class _FakeRT:
        @staticmethod
        async def resolve_station_code(name):
            return {"北京南": ("VNP", "北京南"), "上海虹桥": ("AOH", "上海虹桥")}.get(name) or ("VNP", name)

        @staticmethod
        async def query_tickets(from_code, to_code, date_str):
            calls["n"] += 1
            # 第一次采样：G4 一等座 1 张；第二次：无
            first = {"business": "18", "first_class": "1", "second_class": "有"}
            second = {"business": "18", "first_class": "无", "second_class": "有"}
            return [{"train_no": "G4", "from_station": "北京南", "to_station": "上海虹桥",
                     "start_time": "07:00", "arrive_time": "11:30", "duration": "04:30",
                     "seats": first if calls["n"] == 1 else second}]

    original = tq.rt
    tq.rt = _FakeRT  # type: ignore[assignment]
    try:
        res = asyncio.run(tq.TicketQueryTool().invoke(
            {"from_station": "北京南", "to_station": "上海虹桥", "date": "2026-09-15"}
        ))
    finally:
        tq.rt = original  # type: ignore[assignment]

    assert res.ok, res.error
    assert calls["n"] >= 2, f"低余量应触发二次采样，实际调用 {calls['n']} 次"
    assert "两次采样间发生变化" in res.note, res.note
    assert "first_class: 1→无" in res.note, res.note
    print(f"[PASS] P2-8 低余量二次校验 -> {res.note[-80:]}")

    # 无低余量时不应多查
    calls["n"] = 0

    class _FakeRT2(_FakeRT):
        @staticmethod
        async def query_tickets(from_code, to_code, date_str):
            calls["n"] += 1
            return [{"train_no": "G4", "from_station": "北京南", "to_station": "上海虹桥",
                     "start_time": "07:00", "arrive_time": "11:30", "duration": "04:30",
                     "seats": {"business": "18", "second_class": "有"}}]

    tq.rt = _FakeRT2  # type: ignore[assignment]
    try:
        asyncio.run(tq.TicketQueryTool().invoke(
            {"from_station": "北京南", "to_station": "上海虹桥", "date": "2026-09-15"}
        ))
    finally:
        tq.rt = original  # type: ignore[assignment]
    assert calls["n"] == 1, f"无低余量时不应二次采样，实际 {calls['n']} 次"
    print("[PASS] 无低余量时只查一次（不浪费外部调用）")


def test_knowledge_numeric_uncertainty_rule():
    """P2-9：知识型策略要有"精确数值宁少勿错"的约束。"""
    from app.pipeline.generate import answer_policy

    p = answer_policy("knowledge")
    assert "宁少勿错" in p, p[-200:]
    assert "不确定" in p, p[-200:]
    print("[PASS] 知识型精确数值不确定度规则已注入")


def main():
    test_slots_empty_falls_back_to_message()
    test_realtime_intent_never_answers_with_web_search_only()
    test_unknown_train_code_reaches_tool()
    test_railre_404_means_not_found_not_failure()
    test_hub_probe_recovers_missing_train()
    test_news_searches_twice_with_freshness()
    test_web_search_freshness_note()
    test_no_time_anchor_by_inference_rule()
    test_low_count_second_verification()
    test_knowledge_numeric_uncertainty_rule()
    print("\nR1 第 2 批修复回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
