"""R1（RAG 验证第 1 轮）修复的回归测试 —— **全部无网络**。

覆盖两批修复：
1. **红线 D06**：图定值被当成"实际到发时刻"（以及自造"交叉印证"）；2026-09-15 起经停表改由权威 train_no 直查，红线不变
   → 结构性隔离：默认不下发时刻；仅在问经停/历时/站序时下发，且独立成块 + 显式标注
2. **截断冒充缺失**（C02/C04）
   → ① 过滤在工具侧完成（时段/席别/车种）；② 数据完整性契约（total/shown/truncated/filters/fetched_at）；
     ③ 表格渲染器取代 `text[:900]` 硬截

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_r1_fixes.py
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.pipeline import retrieve as retrieve_mod
from app.pipeline.extract import Slots
from app.pipeline.generate import build_prompt
from app.tools import registry as registry_mod
from app.tools import ticket_query as tq
from app.tools import train_schedule as ts
from app.tools.base import ToolResult

TOMORROW = "2026-09-15"


# ---------- 公共替身 ----------

def _fake_trains(n: int = 53) -> list[dict]:
    """构造 53 趟车：前 40 趟上午、后 13 趟晚间（模拟真实排序：按发车时刻）。"""
    out = []
    for i in range(n):
        if i < 40:
            hh, mm = 6 + i // 8, (i % 8) * 7
            seats = {"second_class": "有", "business": "18"}
        else:
            hh, mm = 17 + (i - 40) // 6, ((i - 40) % 6) * 9
            seats = {"second_class": "有" if i % 3 else "无", "first_class": "有"}
        out.append({
            "train_no": f"G{i + 1}", "from_station": "北京南", "to_station": "上海虹桥",
            "start_time": f"{hh:02d}:{mm:02d}", "arrive_time": f"{hh + 4:02d}:{mm:02d}",
            "duration": "04:54", "seats": seats,
        })
    return out


class _FakeTicketRT:
    """今日无该车次（触发已发车分支）；次日有 G1（用于验证"参考数据"路径）。"""

    def __init__(self, trains: list[dict], tomorrow: list[dict] | None = None,
                 by_date: dict[str, list[dict]] | None = None):
        self.by_date = by_date
        self.trains = trains
        self.tomorrow = tomorrow if tomorrow is not None else [
            {"train_no": "G1", "from_station": "北京南", "to_station": "上海虹桥",
             "start_time": "06:30", "arrive_time": "11:24", "duration": "04:54", "seats": {}},
        ]
        self.calls = 0

    @staticmethod
    def infer_endpoints_from_offline(code):
        return ("北京南", "上海虹桥")

    @staticmethod
    def is_emu_train_code(code):
        return True

    @staticmethod
    async def query_route_stations(train_no, from_code, to_code, date_str):
        return [{"station_name": "北京南"}, {"station_name": "济南西"}, {"station_name": "上海虹桥"}]

    @staticmethod
    async def query_stops_by_train_no(train_no, from_code, to_code, date_str):
        """2026-09-15 起经停表由权威 train_no 直查（含时刻，仅在图定块中使用）。"""
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
        """权威车次身份替身（search.12306.cn 的作用）。"""
        return {"train_no": train_code, "from_station": "北京南", "to_station": "上海虹桥",
                "from_code": "VNP", "to_code": "AOH"}

    async def resolve_station_code(self, name):
        return ({"北京南": ("VNP", "北京南"), "上海虹桥": ("AOH", "上海虹桥"),
                 "北京": ("BJP", "北京"), "上海": ("SHH", "上海")}.get(name) or ("VNP", name))

    async def query_tickets(self, from_code, to_code, date_str):
        from datetime import date as _d

        self.calls += 1
        if self.by_date is not None:        # 指定日期映射（余票过滤用例）
            return list(self.by_date.get(date_str, []))
        if date_str != _d.today().isoformat():
            return list(self.tomorrow)      # 次日（参考路径）
        return list(self.trains)            # 今日（默认为空 → 已发车分支）


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



class _FakeHttpx:
    """把 emu_routing 的共享 client 入口换成固定响应（离线）。"""

    def __init__(self, payload_by_code: dict[str, list[dict]]):
        self._payload = payload_by_code

    async def __call__(self):
        """P0-2 起工具层调的是 `get_client()`（async），不是一个 httpx 模块属性。"""
        return _FakeClient(self._payload)


class _FakeClient:
    def __init__(self, payload_by_code: dict[str, list[dict]]):
        self._payload = payload_by_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str, **_kw):
        code = url.rstrip("/").split("/")[-1]
        payload = self._payload.get(code, [])
        return _FakeResp(payload)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


# ---------- 1. D06：次日值结构性隔离 ----------

def test_departed_does_not_leak_times_by_default():
    """默认（用户问今日时刻）**不得**下发任何时刻值（红线 D06，2026-09-15 起同样成立）。"""
    tomorrow = _expected_tomorrow()
    original = ts.rt
    ts.rt = _FakeTicketRT([])          # 今日无该车次 → 走已发车分支
    try:
        res = asyncio.run(ts.TrainScheduleTool().invoke({"train": "G1"}))
    finally:
        ts.rt = original  # type: ignore[assignment]
    assert res.ok and res.data.get("status") == "departed", res.data
    assert res.data.get("reference") is None, "默认不应带任何时刻块"
    assert res.data.get("stops_with_times") is False, res.data
    assert res.data.get("today_times_available") is False, res.data
    for leaked in ("11:24", "06:30", "08:03"):
        assert leaked not in res.text, f"文本泄露了时刻值 {leaked}：{res.text[:200]}"
    assert "不可得" in res.text, res.text[:200]
    assert "交叉印证" not in res.text, res.text
    # 站名仍应给出（用户要的就是经停站；只是不给时刻）
    assert "济南西" in res.text, res.text[:300]
    assert tomorrow  # 仅用于文档语义（次日日期不出现在文本里）
    print(f"[PASS] D06 结构性隔离：默认只给站名、不下发任何时刻 -> {res.text.splitlines()[0][:56]}…")


def _expected_tomorrow() -> str:
    from datetime import date, timedelta

    return (date.today() + timedelta(days=1)).isoformat()


def test_stops_with_times_only_when_asked_and_clearly_labeled():
    """问经停/历时 → 允许下发**图定**时刻表，但必须独立成块 + 标注"图定/非实际运行"+ 严禁当实际时刻。"""
    original = ts.rt
    ts.rt = _FakeTicketRT([])
    try:
        res = asyncio.run(
            ts.TrainScheduleTool().invoke({"train": "G1", "include_reference": True})
        )
    finally:
        ts.rt = original  # type: ignore[assignment]
    ref = res.data.get("reference") or {}
    assert ref, "问经停时应附图定时刻表"
    assert ref.get("is_actual_run") is False, ref
    assert ref.get("source", "").find("图定") >= 0, ref
    assert "【图定时刻表" in res.text, res.text[:300]
    assert "严禁" in res.text and "实际运行" in res.text, res.text[:400]
    assert "11:24" in res.text and "济南西" in res.text, res.text[:400]
    assert "图定" in (res.note or ""), res.note
    print(f"[PASS] 图定时刻表显式标注：{res.text.split('【图定时刻表')[-1][:60].strip()}…")


def test_retrieve_sets_include_reference_only_for_stops_questions():
    """检索层只在"经停/历时/站序"类问句上打开 include_reference。"""
    with _Recorder() as rec:
        retrieve_mod_retrieve("G1 今天几点到上海虹桥？", target="G1", time="今天")
    params = rec.params_of("train.schedule")
    assert params, rec.calls
    assert not params[0].get("include_reference"), f"今日时刻问题不应带参考：{params[0]}"
    print("[PASS] 「几点到」→ include_reference 关闭")

    with _Recorder() as rec2:
        retrieve_mod_retrieve("G1 都经停哪些站？", target="G1")
    params2 = rec2.params_of("train.schedule")
    assert params2 and params2[0].get("include_reference"), params2
    print("[PASS] 「经停哪些站」→ include_reference 打开")


def retrieve_mod_retrieve(message: str, **slots) -> dict:
    return asyncio.run(
        retrieve_mod.retrieve("schedule", Slots(**slots), question_type="realtime", message=message)
    )


def test_cross_date_and_cross_check_rules_in_prompt():
    """prompt 必须含跨日期规则、同车不同号规则、禁止自造印证与截断声明。

    2026-09-17 用户报障（变更）：旧规则写成「跨日期**绝对**禁止」，模型据此连
    「已发车次的经停/用时」也一并拒绝回答（"该日数据不可得"），表现成"不懂变通"。
    现改为**分两档**：① 当日运行事实（发车/到达/晚点/余票/担当）仍绝对禁止跨日期；
    ② 结构性事实（经停/站序/历时/席别/车型）属图定属性，**只要有数据就必须用**，
    并交代日期与"图定"性质。红线没有放松，只是不再一刀切。
    """
    prompt = build_prompt(
        "G1 今天几点到上海虹桥？", Slots(target="G1"),
        {"data": [], "sources": [], "tool_trace": ["train.schedule: ok"], "note": ""},
    )
    # ① 当日运行事实的禁令仍在（红线 D06 没放松）
    assert "当日运行事实" in prompt, "缺少①当日运行事实的跨日期禁令"
    assert "该日数据不可得" in prompt, "缺少拿不到当日数据时的如实说明要求"
    # ② 结构性事实允许/要求使用带标注的非今日图定数据
    assert "结构性事实" in prompt, "缺少②结构性事实这一档"
    assert "图定属性" in prompt and "哪怕" in prompt, "未要求「有数据就必须用」"
    assert "查不到" in prompt, "未禁止「因为不是今天就说查不到」"
    # ③ 同车不同号
    assert "同车不同号" in prompt, "缺少同车不同号规则"
    assert "G2365/G2368" in prompt, "同车不同号规则未给实例"
    # ④ 原有两条硬规则
    assert "禁止自造印证" in prompt, "缺少禁止自造印证规则"
    assert "截断必须声明" in prompt, "缺少截断声明规则"
    print("[PASS] prompt 含跨日期分档(①事实/②结构)/同车不同号/自造印证/截断 五条硬规则")


# ---------- 2. 截断治理 ----------

def _invoke_ticket(params: dict, trains: list[dict]):
    original = tq.rt
    date_key = str(params.get("date") or "")
    tq.rt = _FakeTicketRT(trains, by_date={date_key: trains})
    try:
        return asyncio.run(tq.TicketQueryTool().invoke(params))
    finally:
        tq.rt = original  # type: ignore[assignment]


def test_ticket_filters_do_the_set_math_in_the_tool():
    """① 过滤在工具侧：问"晚上+二等座"必须只回晚间车次（不再让模型在前 10 条里找）。"""
    trains = _fake_trains(53)
    res = _invoke_ticket(
        {"from_station": "北京南", "to_station": "上海虹桥", "date": TOMORROW,
         "after_time": "17:00", "seat": "second_class"},
        trains,
    )
    assert res.ok, res.error
    # 期望值直接从 fixture 推导，避免测试与数据构造耦合
    expected_evening = [
        t for t in trains
        if t["start_time"] >= "17:00"
        and str((t.get("seats") or {}).get("second_class") or "无") not in ("无", "", "--")
    ]
    assert res.total == len(expected_evening), f"应筛出 {len(expected_evening)} 趟，实际 {res.total}"
    assert res.filters.get("发车时刻≥") == "17:00", res.filters
    assert all(t["start_time"] >= "17:00" for t in res.data["trains"]), res.data["trains"][:3]
    assert res.data["period_counts"]["晚上(17-24)"] == len(expected_evening), res.data["period_counts"]
    assert "过滤条件" in res.text or "已应用过滤" in res.text or "筛选后" in res.text, res.text[:200]
    print(f"[PASS] 工具侧过滤 -> 命中 {res.total} 趟晚间（原 53 趟），展示 {res.shown} 趟")

    # 无过滤时：必须如实声明截断
    res2 = _invoke_ticket({"from_station": "北京南", "to_station": "上海虹桥", "date": TOMORROW}, trains)
    assert res2.total == 53 and res2.shown == 10 and res2.truncated, (res2.total, res2.shown)
    assert len(res2.data["trains"]) == 10, "下发明细不应是全集"
    integrity = res2.integrity_line()
    assert "已截断" in integrity and "命中 53 条" in integrity, integrity
    print(f"[PASS] 完整性契约：{integrity[:70]}…")

    # 过滤后为空：必须说明"数据存在但不符合条件"
    res3 = _invoke_ticket(
        {"from_station": "北京南", "to_station": "上海虹桥", "date": TOMORROW,
         "after_time": "17:00", "seat": "soft_sleeper"},
        trains,
    )
    assert res3.ok is False, res3
    assert "没有符合筛选条件" in res3.error, res3.error
    assert res3.total == 53, res3.total
    print(f"[PASS] 过滤后为空 -> {res3.error[:56]}…")


def test_retrieve_parses_time_seat_type_from_message():
    """检索层把"晚上/二等座/普速"翻译成工具参数。"""
    with _Recorder() as rec:
        retrieve_mod_retrieve("明天晚上北京南到上海虹桥二等座还有吗？", direction="北京南到上海虹桥",
                              time="明天晚上", extra="二等座")
    params = rec.params_of("ticket.query")
    assert params, rec.calls
    assert params[0]["after_time"] == "17:00", params[0]
    assert params[0]["seat"] == "second_class", params[0]
    print(f"[PASS] 时段/席别解析 -> after_time={params[0]['after_time']} seat={params[0]['seat']}")

    with _Recorder() as rec2:
        retrieve_mod_retrieve("下周三北京到上海的普速有几趟？", direction="北京到上海",
                              time="下周三", target="K/Z字头普速列车")
    p2 = rec2.params_of("ticket.query")[0]
    assert p2.get("train_type") == "K,T,Z", p2
    print(f"[PASS] 车种解析 -> train_type={p2['train_type']}")


def test_table_renderer_removes_900_char_cut():
    """③ 表格渲染器：被裁掉的中后段车次现在能进入 prompt（旧实现 900 字符硬截会丢）。"""
    trains = _fake_trains(53)
    res = _invoke_ticket({"from_station": "北京南", "to_station": "上海虹桥", "date": TOMORROW}, trains)
    fact = {
        "tool": "ticket.query", "text": res.text, "sources": ["s"], "note": res.note,
        "data": res.data, "integrity": res.integrity_line(),
        "total": res.total, "shown": res.shown, "truncated": res.truncated, "filters": res.filters,
    }
    prompt = build_prompt(
        "明天北京南到上海虹桥有多少趟？", Slots(direction="北京南到上海虹桥"),
        {"data": [fact], "sources": ["s"], "tool_trace": ["ticket.query: ok"], "note": ""},
    )
    # 第 8 趟（旧实现 900 字符内大概只能到第 7-8 行）与分布统计都应出现
    assert "G8｜" in prompt, "第 8 趟车次未进入 prompt（仍在硬截）"
    assert "发车时段分布" in prompt, "缺少时段分布统计"
    assert "数据完整性" in prompt, "缺少数据完整性行"
    assert "共 53 趟" in prompt, prompt[prompt.index("[检索事实]"):][:200]
    print("[PASS] 表格渲染器：明细+分布+完整性行均进入 prompt")


def test_failed_tool_error_is_passed_through():
    """附加（P1-4）：失败工具的 error 原文必须进入事实说明，不能只说"调用失败"。"""
    class _FailRegistry(_Recorder):
        async def __call__(self, name: str, params: dict) -> ToolResult:
            self.calls.append((name, dict(params or {})))
            return ToolResult(ok=False, error=f"{name} 明确失败原因：发站与到站相同",
                              note="工具已给出明确结论")

    with _FailRegistry() as rec:
        out = retrieve_mod_retrieve("北京到北京的径路怎么走？", direction="北京到北京", location="北京")
    assert any("发站与到站相同" in n for n in [out["note"]]), out["note"]
    print(f"[PASS] 工具错误原文已透传 -> {out['note'][:70]}…")


def test_emu_routing_labels_record_time_not_train_time():
    """D06 的第二条泄露路径：rail.re 的记录时刻必须显式标注，避免被当成列车到发时刻。"""
    from app.tools import emu_routing as er

    payload = [{"date": "2026-09-14 11:24", "emu_no": "CR400BFA5159", "train_no": "G1"}]
    with patch.object(er, "get_client", _FakeHttpx({"G1": payload})):
        res = asyncio.run(er.EmuRoutingTool().invoke({"train": "G1"}))
    assert res.ok, res.error
    assert "交路记录时刻" in res.text, res.text
    assert "非列车到发时刻" in res.text, res.text
    assert "不是列车到发时刻" in (res.note or ""), res.note
    print(f"[PASS] 交路记录时刻已标注 -> {res.text.splitlines()[1][:60]}…")


def main():
    test_departed_does_not_leak_times_by_default()
    test_stops_with_times_only_when_asked_and_clearly_labeled()
    test_retrieve_sets_include_reference_only_for_stops_questions()
    test_cross_date_and_cross_check_rules_in_prompt()
    test_ticket_filters_do_the_set_math_in_the_tool()
    test_retrieve_parses_time_seat_type_from_message()
    test_table_renderer_removes_900_char_cut()
    test_failed_tool_error_is_passed_through()
    test_emu_routing_labels_record_time_not_train_time()
    print("\nR1 修复回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
