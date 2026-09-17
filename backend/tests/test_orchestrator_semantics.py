"""块式/流式编排的故障语义一致性测试（**无网络**，M11.1 审计修复回归）。

对应 `审计报告（历史）` P1-5：同一故障下
- 块式 `run()` 给友好指引，流式 `run_stream()` 只给裸 error；
- 生成失败时流式还会先 `error` 再 `done(answer_done=true, answer 空)`；
- `done` 事件缺 `tool_trace`（与块式 `PipelineResult` 不对齐）。

本测试用假 LLM 制造两类故障（意图阶段失败 / 生成阶段失败），逐条断言修复后的行为：
1. `error` 事件文案必须**可操作**（含 LLM_MOCK / .env 指引），不是裸异常串；
2. `done` 事件**恰好一次**，字段与块式对齐（含 `tool_trace`）；
3. 失败时 `answer_done=False`，成功时 `answer_done=True`；
4. 失败时 `done.error` 与 `error` 事件文案一致，前端可据此渲染。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_orchestrator_semantics.py
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.llm import client as llm_client
from app.llm.client import LLMUnavailable
from app.pipeline import orchestrator


class _StubSlots:
    def __init__(self, **kw):
        self.location = kw.get("location")
        self.target = kw.get("target")
        self.time = kw.get("time")
        self.direction = kw.get("direction")
        self.extra = None

    def non_empty(self) -> dict:
        return {k: v for k, v in (("target", self.target),) if v}


def _events(message: str) -> list[dict]:
    async def _collect():
        out = []
        async for ev in orchestrator.run_stream(message):
            out.append(ev)
        return out

    return asyncio.run(_collect())


def _assert_single_done(events: list[dict]) -> dict:
    dones = [e for e in events if e.get("type") == "done"]
    assert len(dones) == 1, f"done 事件应恰好一次，实际 {len(dones)} 次：{[e['type'] for e in events]}"
    assert events[-1]["type"] == "done", f"done 必须是最后一个事件：{[e['type'] for e in events]}"
    return dones[0]


def _patch_stages_fail():
    """让意图阶段就失败（模拟 LLM 未配置/不可达）。"""
    async def _boom(*_a, **_kw):
        raise LLMUnavailable("LLM 鉴权失败(HTTP 401)：API Key 无效或被拒绝。")

    return _boom


def _patch_generation_fail(monkeypatched: dict):
    """让意图/抽取/检索正常，但生成阶段失败。"""
    async def _classify(message, history=None):
        return _IntentStub(), {"question_type": "realtime"}

    async def _fill(message, intent=None, history=None):
        return _StubSlots(target="G1")

    async def _retrieve(intent, slots, question_type=None, message=None, prefetch=None):
        return {"data": [], "sources": ["https://example.com/x"], "tool_trace": ["emu.routing: ok"], "note": "n"}

    async def _gen(*_a, **_kw):
        # 必须是 async generator（stream_completion 的调用方式是 `async for`）
        raise LLMUnavailable("LLM 限流或额度不足(HTTP 429)，请稍后重试。")
        yield ("text", "")  # pragma: no cover —— 仅为构成 async generator

    monkeypatched.update(
        intent_classify=orchestrator.planner.intent.classify,
        extract_fill=orchestrator.planner.extract.fill,
        retrieve=orchestrator.retrieve.retrieve,
    )
    orchestrator.planner.intent.classify = _classify            # type: ignore[assignment]
    orchestrator.planner.extract.fill = _fill                    # type: ignore[assignment]
    orchestrator.retrieve.retrieve = _retrieve           # type: ignore[assignment]
    llm_client.stream_completion = _gen                  # type: ignore[assignment]


def _restore(monkeypatched: dict):
    if "intent_classify" in monkeypatched:
        orchestrator.planner.intent.classify = monkeypatched["intent_classify"]      # type: ignore[assignment]
        orchestrator.planner.extract.fill = monkeypatched["extract_fill"]            # type: ignore[assignment]
        orchestrator.retrieve.retrieve = monkeypatched["retrieve"]           # type: ignore[assignment]


class _IntentStub:
    value = "emu_routing"
    label_zh = "车组交路查询"


def test_stage_failure_is_actionable_and_closed():
    """意图/抽取阶段不可用：error 可操作 + done 收尾 + answer_done=False。"""
    original = orchestrator.planner.intent.classify
    original_fill = orchestrator.planner.extract.fill
    orchestrator.planner.intent.classify = _patch_stages_fail()   # type: ignore[assignment]
    try:
        events = _events("G1今天由哪组动车组担当？")
    finally:
        orchestrator.planner.intent.classify = original           # type: ignore[assignment]
        orchestrator.planner.extract.fill = original_fill         # type: ignore[assignment]

    errors = [e for e in events if e.get("type") == "error"]
    assert errors, f"未发出 error 事件：{events}"
    msg = errors[0]["message"]
    assert "LLM_MOCK" in msg and ".env" in msg, f"错误文案不可操作：{msg}"

    done = _assert_single_done(events)
    assert done["answer_done"] is False, done
    assert done["error"] == msg, "done.error 与 error 事件文案不一致"
    assert "tool_trace" in done and isinstance(done["tool_trace"], list), done
    print(f"[PASS] 阶段失败 -> error 可操作 + 单个 done(answer_done=False)；文案：{msg.splitlines()[0]}")


def test_generation_failure_keeps_sources_and_marks_not_done():
    """生成阶段失败：仍返回已检索到的来源/工具轨迹，但 answer_done=False。"""
    patched: dict = {}
    original_stream = llm_client.stream_completion
    _patch_generation_fail(patched)
    try:
        events = _events("G1今天由哪组动车组担当？")
    finally:
        _restore(patched)
        llm_client.stream_completion = original_stream    # type: ignore[assignment]

    done = _assert_single_done(events)
    assert done["answer_done"] is False, f"生成失败却标记为完成：{done}"
    assert done["tool_trace"] == ["emu.routing: ok"], done
    assert done["sources"] == ["https://example.com/x"], done
    errors = [e for e in events if e.get("type") == "error"]
    assert errors and "429" in errors[0]["message"], errors
    print("[PASS] 生成失败 -> done(answer_done=False) 且保留 tool_trace/sources")


def test_success_marks_done_true():
    """正常路径：answer_done=True，且 done 仍带 tool_trace/sources。"""
    async def _classify(message, history=None):
        return _IntentStub(), {"question_type": "realtime"}

    async def _fill(message, intent=None, history=None):
        return _StubSlots(target="G1")

    async def _retrieve(intent, slots, question_type=None, message=None, prefetch=None):
        return {"data": [], "sources": ["https://example.com/ok"], "tool_trace": ["emu.routing: ok"], "note": "n"}

    async def _stream(*_a, **_kw) -> AsyncIterator[tuple[str, str]]:
        yield ("think", "思考中")
        yield ("text", "答案是 CR400BFA-5159。")

    original = (orchestrator.planner.intent.classify, orchestrator.planner.extract.fill,
                orchestrator.retrieve.retrieve, llm_client.stream_completion)
    orchestrator.planner.intent.classify = _classify          # type: ignore[assignment]
    orchestrator.planner.extract.fill = _fill                  # type: ignore[assignment]
    orchestrator.retrieve.retrieve = _retrieve         # type: ignore[assignment]
    llm_client.stream_completion = _stream             # type: ignore[assignment]
    try:
        events = _events("G1今天由哪组动车组担当？")
    finally:
        (orchestrator.planner.intent.classify, orchestrator.planner.extract.fill,
         orchestrator.retrieve.retrieve, llm_client.stream_completion) = original  # type: ignore[assignment]

    done = _assert_single_done(events)
    assert done["answer_done"] is True, done
    assert done["error"] is None, done
    assert done["tool_trace"] == ["emu.routing: ok"], done
    assert "".join(e.get("delta", "") for e in events if e["type"] == "answer").startswith("答案是")
    print("[PASS] 正常路径 -> answer_done=True、error=None、tool_trace 已对齐")


# ---- 决策层已收敛到 planner（perf P0）：本套件注入的是 legacy 两段式 ----
# 关掉确定性快路径 + 让"合并调用"失败，从而使 planner 走**传统两次调用**分支，
# 这样注入 intent.classify / extract.fill 仍然生效（行为与优化前一致，且不联网）。
def _force_legacy():
    import unittest.mock as _mock

    return [
        _mock.patch("app.pipeline.fastpath.plan_with_reason", new=_mock.AsyncMock(return_value=(None, None))),
        _mock.patch("app.pipeline.planner.chat_structured",
                    new=_mock.AsyncMock(side_effect=RuntimeError("merged call disabled in test"))),
    ]


def main():
    with _force_legacy()[0], _force_legacy()[1]:
        _run_all()


def _run_all():
    test_stage_failure_is_actionable_and_closed()
    test_generation_failure_keeps_sources_and_marks_not_done()
    test_success_marks_done_true()
    print("\n编排故障语义一致性测试全部通过 ✔")


if __name__ == "__main__":
    main()
