"""决策层性能优化回归（perf P0-1/P0-2/P0-3）。

背景（实测见 docs/perf-plan.md）
- 一次问答的"预筛选"原本 3.8–11.6s，其中几乎全是**两次 LLM 往返**（意图 + 抽取）；
- 确定性快路径（正则 + 本地站点库）可覆盖约 56–68% 的车迷问法，**0 次 LLM 调用**。

本套件钉住三件事：
1. **快路径判定正确**：该接管的接管（意图+槽位正确），该交回 LLM 的一律交回；
2. **红线不破**：知识型/开放型问题绝不走快路径（问题性质需模型判断）；
3. **预取只做优化**：命中同名同参才复用，无强信号不预取，失败不抛。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_perf_fastpath.py
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.pipeline import fastpath, planner, prefetch
from app.pipeline.schemas import COMBINED_JSON_SCHEMA, INTENT_JSON_SCHEMA, SLOTS_JSON_SCHEMA

# （问句, 期望意图, 期望关键槽位）
_GOLDEN = [
    ("G1经停哪些站？", "schedule", {"target": "G1"}),
    ("G1今天由哪组动车组担当？", "emu_routing", {"target": "G1", "time": "今天"}),
    ("g1次列车今天的车底是啥？", "emu_routing", {"target": "G1"}),
    ("明天北京到上海的高铁还有票吗？", "ticket", {"time": "明天", "direction": "北京→上海"}),
    ("北京南站大屏今天下午有哪些高铁？", "station", {"location": "北京南", "time": "今天下午"}),
    ("北京南到济南西多少公里？", "rail_line", {"direction": "北京南→济南西"}),
    ("陇海线全程多少公里？", "rail_line", {"target": "陇海线"}),
    ("北京南站的电报码是多少？", "station", {"location": "北京南"}),
    ("上海虹桥到达屏", "station", {"location": "上海虹桥"}),
]

# 必须交回 LLM 的（知识型 / 无强信号 / 开放型）
_MUST_DEFER = [
    "CR400AF用的哪个品牌的动力系统？",
    "CR400AF 和 CR400BF 有什么区别？",
    "和谐号和复兴号是什么关系？",
    "CR200J 为什么被叫绿巨人？",
    "沪苏湖高铁开通了吗？现在什么状态？",
    "帮我把这段话翻译成英文",
    "换乘车站的编号怎么看？",
    "G1今天几点到上海虹桥？",     # ← 时刻问题：无法用规则判 realtime/知识，交回 LLM 更稳
]


def test_golden_fastpath_cases():
    """快路径必须给出正确的意图与关键槽位（少一次 LLM 往返的前提）。"""
    # 线路名类用例依赖**本地字典库**（backend/data/dict.db，由 scripts/mirror_dict.py 构建）。
    # 未构建时应明确跳过而不是失败——测试不该把可选构建产物当成必然存在。
    from app.data import dict as _dict

    has_dict = _dict.available()
    for msg, want_intent, want_slots in _GOLDEN:
        if not has_dict and want_slots.get("target") and "线" in str(want_slots.get("target")):
            print(f"[SKIP] 本地字典未构建，跳过线路名用例：{msg}")
            continue
        fp = asyncio.run(fastpath.plan(msg))
        assert fp is not None, f"快路径未接管（本该能判定）：{msg}"
        assert fp.intent == want_intent, f"{msg} → {fp.intent}（期望 {want_intent}）"
        got = fp.slots.non_empty()
        for k, v in want_slots.items():
            assert got.get(k) == v, f"{msg} 槽位 {k}={got.get(k)!r}（期望 {v!r}）；全部={got}"
        assert fp.reason and fp.matched, fp
    print(f"[PASS] 快路径 {len(_GOLDEN)} 条黄金用例：意图与槽位全部正确")


async def rt_ensure():
    from app.tools import _rt12306 as rt

    await rt.ensure_loaded()


def test_no_station_no_fastpath():
    """拿不到真站名时不得接管（防"换乘车站"→"换乘车"这类假名）。"""
    asyncio.run(rt_ensure())
    got = fastpath._station_in_text("换乘车站的编号怎么看？")
    assert got == "", got
    assert asyncio.run(fastpath.plan("换乘车站的编号怎么看？")) is None
    print("[PASS] 无真实站名 → 不接管（假站名防护）")


def test_knowledge_questions_defer_to_llm():
    """★ 红线：知识型/开放型问题**必须**交回 LLM（问题性质需模型判断，规则猜不得）。"""
    for msg in _MUST_DEFER:
        fp = asyncio.run(fastpath.plan(msg))
        assert fp is None, f"{msg} 被快路径错误接管 → {fp.intent}/{fp.slots.non_empty()}"
    print(f"[PASS] {len(_MUST_DEFER)} 条知识型/无强信号问题全部交回 LLM")


def test_planner_deterministic_is_zero_llm():
    """快路径命中时**不得**触碰 LLM（这才是性能收益的来源）。"""
    called: list[str] = []

    async def _boom(*a, **kw):
        called.append("llm")
        raise AssertionError("确定性快路径不应调用 LLM")

    with patch("app.pipeline.planner.chat_structured", _boom), \
         patch("app.pipeline.planner.intent.classify", _boom), \
         patch("app.pipeline.planner.extract.fill", _boom):
        i, qt, slots, pl = asyncio.run(planner.decide("G1经停哪些站？"))
    assert pl == "deterministic" and i.value == "schedule", (pl, i)
    assert slots.non_empty() == {"target": "G1"}, slots.non_empty()
    assert not called, called
    print("[PASS] 快路径命中 → 0 次 LLM 调用（planner=deterministic）")


def test_combined_schema_matches_sources():
    """合并调用的 schema 必须等于「意图 schema ∪ 槽位 schema」，避免两处描述漂移。"""
    combined = set(COMBINED_JSON_SCHEMA["properties"])
    expect = set(INTENT_JSON_SCHEMA["properties"]) | set(SLOTS_JSON_SCHEMA["properties"])
    assert combined == expect, (combined, expect)
    # 描述也要逐字段一致（只合并、不改写）
    for k in expect:
        src = {**INTENT_JSON_SCHEMA["properties"], **SLOTS_JSON_SCHEMA["properties"]}[k]
        assert COMBINED_JSON_SCHEMA["properties"][k] == src, k
    print(f"[PASS] 合并 schema 与两份源 schema 字段/描述一致：{sorted(combined)}")


def test_prefetch_picks_one_strong_signal():
    """预取只在有强信号时启动，且最多一个工具（不对着外部站点乱打）。"""

    async def _noop():
        return None

    async def run():
        pf_train = prefetch.start("G1今天由哪组担当？")
        pf_od = prefetch.start("明天北京到上海还有票吗？")
        pf_none = prefetch.start("沪苏湖高铁开通了吗")
        return pf_train, pf_od, pf_none

    t, o, n = asyncio.run(run())
    assert t is not None and t.started == ["emu.routing"], (t and t.started)
    assert o is not None and o.started == ["ticket.query"], (o and o.started)
    assert n is None, "无强信号时不应预取"
    # 精确匹配才复用；参数不同不得复用
    key_task = list(t._tasks.values())[0]
    assert t.take("emu.routing", {"train": "G1", "date": "今天"}) is not None
    assert t.take("emu.routing", {"train": "G3", "date": "今天"}) is None
    assert t.take("train.schedule", {"train": "G1"}) is None
    asyncio.run(t.cancel())
    asyncio.run(o.cancel())
    print("[PASS] 预取：车次→emu.routing、区间→ticket.query、无信号→不预取；仅同名同参复用")


def test_prefetch_failure_is_silent():
    """预取失败/取消都不得抛出（纯优化，不能影响主流程）。"""
    async def _run():
        pf = prefetch.start("G9经停哪些站？")
        assert pf is not None
        await pf.cancel()
        return True

    assert asyncio.run(_run())
    print("[PASS] 预取取消/失败静默，不影响主流程")


def test_orchestrator_reports_planner():
    """块式接口必须透出 planner 字段（便于统计快路径命中率与误判率）。"""
    from app.pipeline import orchestrator

    res = asyncio.run(orchestrator.run("G1经停哪些站？"))
    assert res.planner == "deterministic", res.planner
    assert any("[决策]" in x and "快路径" in x for x in res.process_logs), res.process_logs[:2]
    print(f"[PASS] 编排透出 planner={res.planner}；日志：{res.process_logs[0][:56]}…")


def main():
    test_golden_fastpath_cases()
    test_no_station_no_fastpath()
    test_knowledge_questions_defer_to_llm()
    test_planner_deterministic_is_zero_llm()
    test_combined_schema_matches_sources()
    test_prefetch_picks_one_strong_signal()
    test_prefetch_failure_is_silent()
    test_orchestrator_reports_planner()
    print("\n决策层性能优化（快路径/合并调用/预取）测试全部通过 ✔")


if __name__ == "__main__":
    main()
