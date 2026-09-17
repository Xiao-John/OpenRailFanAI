"""检索计划与 mock 路由的**无网络**测试（M11.1 审计修复回归）。

覆盖两处"静默给错/测不出错"的问题：
1. `retrieve.py` 用 (location, target) 伪造起讫站 → 产生无效区间查询（audit-tools P1-3）
2. `_mock.py` 对**整段 prompt**（含 few-shot 示例）做关键词匹配
   → 任何输入都被判成 emu_routing/knowledge，导致 mock/CI 下 13 个工具的路由逻辑从未被执行
   （audit-core P1-6）

策略：不联网、不调 LLM —— 用替身记录"计划要调用哪些工具与参数"，并直接驱动 mock 判定。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_routing.py
"""
from __future__ import annotations

import asyncio

from app.pipeline import intent as intent_mod
from app.pipeline import retrieve as retrieve_mod
from app.pipeline.extract import Slots
from app.pipeline.retrieve import _station_like
from app.tools import registry as registry_mod
from app.tools.base import ToolResult


class _Recorder:
    """替换 registry.invoke_by_name，记录调用并返回成功（不发任何网络请求）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._original = registry_mod.invoke_by_name

    async def __call__(self, name: str, params: dict) -> ToolResult:
        self.calls.append((name, dict(params or {})))
        return ToolResult(ok=True, data={"stub": True}, text=f"stub:{name}", sources=[f"https://stub/{name}"])

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def params_of(self, name: str) -> list[dict]:
        return [p for n, p in self.calls if n == name]

    def __enter__(self):
        registry_mod.invoke_by_name = self  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        registry_mod.invoke_by_name = self._original  # type: ignore[assignment]
        return False


def _retrieve(intent: str, *, question_type: str | None = None, message: str | None = None, **slots_kw) -> dict:
    return asyncio.run(
        retrieve_mod.retrieve(intent, Slots(**slots_kw), question_type=question_type, message=message)
    )


# ---------- 1. 伪造起讫站的防护 ----------

def test_station_like_heuristics():
    ok = ["北京", "上海虹桥", "吉林", "广州南"]
    bad = ["CR400AF", "CR400BFA5054", "G1", "G1次列车", "D122", "吉林市XX区", "北京123", "12306", ""]
    for v in ok:
        assert _station_like(v), f"{v!r} 应为站名"
    for v in bad:
        assert not _station_like(v), f"{v!r} 不应被判为站名"
    print(f"[PASS] _station_like：{len(ok)} 个站名通过、{len(bad)} 个非站名被拒")


def test_no_fabricated_od_from_location_and_target():
    """拍摄点场景（location=吉林市XX区 + target=CR400AF）不得产生区间查询。"""
    with _Recorder() as rec:
        out = _retrieve("schedule", location="吉林市XX区", target="CR400AF", time="今天下午")
    assert "ticket.query" not in rec.names(), f"伪造了区间查询：{rec.calls}"
    assert "emu.routing" in rec.names(), rec.calls        # 车型交路查询仍应发起
    assert out["tool_trace"], out
    print(f"[PASS] 不再伪造起讫站 -> 调用 {rec.names()}")

    # 车次场景（target=G1）同样不得把 G1 当到达站
    with _Recorder() as rec2:
        _retrieve("schedule", location="北京南", target="G1")
    for params in rec2.params_of("ticket.query"):
        assert params.get("to_station") not in ("G1", "CR400AF"), params
    assert all(
        p.get("to_station") != "G1" for _, p in rec2.calls
    ), f"车次被当成到达站：{rec2.calls}"
    print(f"[PASS] 车次号不会被当作到达站 -> 调用 {rec2.names()}")


# ---------- 1b. 车站大屏（station.screen）路由 ----------

def test_station_screen_keyword_routing():
    """「大屏/出发屏/到达屏/晚点」类问法应补一次 station.screen，并带上时段与车种过滤。

    两条红线：
    ① 只在命中关键词时才调（车站类问题本就不多，不该每次都多打一次 12306）；
    ② 时段/车种必须一起下发 —— 本接口一次回全天 200–700 条，只截断前 N 条
       会让"今天下午有哪些高铁"拿到凌晨的车（截断冒充缺失）。
    """
    with _Recorder() as rec:
        _retrieve("station", question_type="realtime", message="北京南站大屏今天下午有哪些高铁？",
                  location="北京南", time="今天")
    params = rec.params_of("station.screen")
    assert params, f"命中大屏关键词却未调用 station.screen：{rec.calls}"
    p = params[0]
    assert p["station"] == "北京南", p
    assert p["date"] == "今天", p
    assert (p["after_time"], p["before_time"]) == ("12:00", "18:00"), p
    assert p["train_type"] == "G", p
    print(f"[PASS] 大屏问法 -> station.screen{dict(p)}")

    # 方向词
    with _Recorder() as rec2:
        _retrieve("station", question_type="realtime", message="上海虹桥到达屏", location="上海虹桥")
    assert rec2.params_of("station.screen")[0]["direction"] == "arrival", rec2.calls
    print("[PASS] 「到达屏」-> direction=arrival")

    # 未命中关键词 / 非车站目标：不得调用
    for intent, msg, kw in [
        ("station", "北京南站有几个站台", {"location": "北京南"}),
        ("ticket", "明天北京到上海有票吗", {"direction": "北京到上海", "time": "明天"}),
        ("emu_routing", "北京南的车组交路怎么查", {"location": "北京南", "target": "CR400AF"}),
    ]:
        with _Recorder() as rec3:
            _retrieve(intent, question_type="realtime", message=msg, **kw)
        assert "station.screen" not in rec3.names(), f"{msg!r} 不该调用大屏：{rec3.calls}"
    print("[PASS] 非大屏问法不触发 station.screen（避免无谓外呼）")

    # 知识型问题（"大屏怎么用"）：不得打实时接口
    with _Recorder() as rec4:
        _retrieve("station", question_type="knowledge", message="北京南大屏怎么用", location="北京南")
    assert "station.screen" not in rec4.names(), rec4.calls
    print("[PASS] knowledge 型不调 station.screen -> " + str(rec4.names()))


def test_explicit_od_still_works():
    """显式区间表述（direction="北京到上海"）必须照常触发区间查询。"""
    with _Recorder() as rec:
        _retrieve("ticket", direction="北京到上海", time="明天")
    params = rec.params_of("ticket.query")
    assert params, f"未发起区间查询：{rec.calls}"
    assert params[0]["from_station"] == "北京" and params[0]["to_station"] == "上海", params[0]
    assert params[0]["date"] == "明天", params[0]
    print(f"[PASS] 显式区间照常查询 -> from={params[0]['from_station']} to={params[0]['to_station']}")

    # location 本身含区间表述时也应识别
    with _Recorder() as rec2:
        _retrieve("rail_line", location="北京到上海")
    assert rec2.params_of("rail.line"), rec2.calls
    print(f"[PASS] location 内的区间表述同样识别 -> {rec2.names()}")


def test_station_intent_no_longer_calls_railre():
    """车站意图不再调用恒 404 的 railre（交路数据以 emu.routing 为准）。"""
    with _Recorder() as rec:
        _retrieve("station", location="北京")
    assert "railre" not in rec.names(), f"仍在调用 railre：{rec.calls}"
    assert "station.lookup" in rec.names(), rec.calls
    print(f"[PASS] 车站意图调用 {rec.names()}（railre 已移出计划）")


def test_question_type_routing():
    """knowledge 只走搜索；mixed 在实时计划后追加搜索。"""
    with _Recorder() as rec:
        _retrieve("emu_routing", target="G1", question_type="knowledge", message="CR400AF样车车组号")
    assert rec.names() == ["web.search"], f"knowledge 应只走搜索：{rec.calls}"
    print(f"[PASS] knowledge -> {rec.names()}")

    with _Recorder() as rec2:
        _retrieve("emu_routing", target="G1", question_type="mixed", message="今日开行 + 动力系统")
    assert rec2.names()[0] == "emu.routing" and "web.search" in rec2.names(), rec2.calls
    print(f"[PASS] mixed -> {rec2.names()}")


# ---------- 2. mock 只按用户输入判定 ----------

def test_mock_uses_user_section_not_prompt_template():
    """mock 判定必须基于用户输入，不被 prompt 里的 few-shot 示例带偏。"""
    from app.llm import _mock

    # 意图 prompt 模板：含"担当/线路/余票"等示例，且用户输入是"你好"
    template = (
        "请判断下面这条铁路相关请求的主要意图（intent）与问题性质（question_type）：\n"
        "\n[本次用户输入]\n你好\n\n"
        "参考示例（务必对齐）：\n"
        '  "G1今天由哪组动车组担当？"        → intent=emu_routing, question_type=realtime\n'
        '  "北京到上海走哪条线路？"           → intent=rail_line, question_type=realtime\n'
        '  "CR400AF用的哪个品牌的动力系统？"  → intent=general, question_type=knowledge\n'
    )
    data = asyncio.run(_mock.mock_structured(template, {"properties": {"intent": {}, "question_type": {}}}))
    assert data["intent"] == "general", f"被模板示例带偏：{data}"
    assert data["question_type"] == "realtime", f"被模板示例带偏：{data}"
    print(f"[PASS] mock 只按用户输入判定 -> {data}")

    cases = [
        ("G1今天由哪组动车组担当？", "emu_routing", "realtime"),
        ("明天北京到上海还有票吗？", "ticket", "realtime"),
        ("北京到上海走哪条线路？", "rail_line", "realtime"),
        ("CR400AF用的哪个品牌的动力系统？", "general", "knowledge"),
        ("我在吉林市XX区，要拍 CR400AF，今天下午", "photo_spot", "realtime"),
        ("你好", "general", "realtime"),
    ]
    seen = []
    for msg, want_intent, want_qtype in cases:
        prompt = f"判断意图：\n\n[本次用户输入]\n{msg}\n\n参考示例：\n  \"G1今天由哪组动车组担当？\" → emu_routing/realtime\n"
        d = asyncio.run(_mock.mock_structured(prompt, {"properties": {"intent": {}, "question_type": {}}}))
        assert d["intent"] == want_intent, f"{msg!r} -> {d['intent']}，期望 {want_intent}"
        assert d["question_type"] == want_qtype, f"{msg!r} -> {d['question_type']}，期望 {want_qtype}"
        seen.append(f"{msg}→{d['intent']}")
    assert len({s.split("→")[1] for s in seen}) >= 4, f"判定过于集中，路由无鉴别力：{seen}"
    print(f"[PASS] mock 意图判定有鉴别力 -> {seen}")


def test_mock_drives_real_intent_prompt():
    """直接驱动真实的 intent.classify（LLM_MOCK=true），验证整条 mock 链路。"""
    from app.config import get_settings

    settings = get_settings()
    old = settings.llm_mock
    settings.llm_mock = True
    try:
        got = {}
        for msg in ("G1今天由哪组动车组担当？", "北京到上海走哪条线路？", "明天北京到上海还有票吗？"):
            it, detail = asyncio.run(intent_mod.classify(msg))
            got[msg] = (it.value, detail.get("question_type"))
        assert got["G1今天由哪组动车组担当？"][0] == "emu_routing", got
        assert got["北京到上海走哪条线路？"][0] == "rail_line", got
        assert got["明天北京到上海还有票吗？"][0] == "ticket", got
        assert all(v[1] == "realtime" for v in got.values()), got
        print(f"[PASS] 真实 intent prompt + mock -> {got}")
    finally:
        settings.llm_mock = old


def test_coarse_time_window_is_disclosed_not_refined():
    """粗时段口径**刻意不拆细**，但必须把"按什么筛的 + 怎么收窄"抛回给用户。

    实测：用户说"上午从合肥南到南京南有票吗"，工具里筛的是 05:00–12:00，
    而 filters 只写"发车时刻≥05:00 / <12:00" —— 用户看不出"上午"被理解成了这么宽，
    也没人告诉他"给具体钟点就能收窄"。这是**口径问题**，不是缺陷，所以选择
    如实告知而不是去猜更细的边界。
    """
    with _Recorder():
        n1 = _retrieve("ticket", question_type="realtime",
                       message="上午从合肥南到南京南有票吗", direction="合肥南→南京南").get("note") or ""
        n2 = _retrieve("ticket", question_type="realtime",
                       message="9点以后从合肥南到南京南有票吗", direction="合肥南→南京南").get("note") or ""
        n3 = _retrieve("ticket", question_type="realtime",
                       message="明天北京到上海还有票吗", direction="北京→上海").get("note") or ""

    assert "上午" in n1 and "05:00" in n1, f"粗时段口径没抛回给用户：{n1}"
    assert "具体钟点" in n1, f"没告诉用户怎么收窄：{n1}"
    assert "宽口径" not in n2, f"用户给了具体钟点，不该再报粗时段口径：{n2}"
    assert "宽口径" not in n3, f"用户压根没提时段，不该产生口径说明：{n3}"
    print("[PASS] 粗时段不拆细，而是把「按什么筛的 + 怎么收窄」告知用户")


def main():
    test_coarse_time_window_is_disclosed_not_refined()
    test_station_like_heuristics()
    test_no_fabricated_od_from_location_and_target()
    test_station_screen_keyword_routing()
    test_explicit_od_still_works()
    test_station_intent_no_longer_calls_railre()
    test_question_type_routing()
    test_mock_uses_user_section_not_prompt_template()
    test_mock_drives_real_intent_prompt()
    print("\n检索计划与 mock 路由测试全部通过 ✔")


if __name__ == "__main__":
    main()
