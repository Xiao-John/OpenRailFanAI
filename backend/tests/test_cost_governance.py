"""成本治理与输入约束的**无网络**测试（M11.1 审计修复回归）。

对应 `docs/review/audit-core.md` / `audit-tests-docs.md` 的三项 P2：
1. 历史在生成 prompt 与 messages 中**重复注入**（实测约 1760 字符/次）→ 现只保留 prompt 区块
2. 检索层工具**串行**调用（4 工具最坏 ~48s）→ 现并发且保持计划顺序
3. 用户消息**无长度上限**（20 万字符照跑）→ 现空消息/超长消息一律 422

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_cost_governance.py
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from app.config import get_settings
from app.llm import client as llm_client
from app.models import ChatRequest
from app.pipeline import orchestrator
from app.pipeline.extract import Slots
from app.pipeline.generate import GEN_HISTORY_LIMIT, build_prompt
from app.pipeline import retrieve as retrieve_mod
from app.tools import registry as registry_mod
from app.tools.base import ToolResult

MARKER = "MARKER-HISTORY-7F3A"


# ---------- 1. 历史只注入一次 ----------

def test_history_injected_once_into_generation():
    """同一条历史不得同时出现在 prompt 区块与 messages 中。"""
    history = [
        {"role": "user", "content": f"G1今天由哪组动车组担当？{MARKER}"},
        {"role": "assistant", "content": f"今日由 CR400BFA-5159 担当。{MARKER}"},
    ]
    slots = Slots(target="G1", time="今天")
    retrieval = {"data": [], "sources": [], "tool_trace": [], "note": ""}

    prompt = build_prompt("那明天呢？", slots, retrieval, history=history)

    assert f"[对话历史]" in prompt, "生成 prompt 应含紧凑历史区块"
    assert prompt.count(MARKER) == 2, (
        f"历史区块内应各出现一次（用户+助手 = 2），实际 {prompt.count(MARKER)}"
    )
    print(f"[PASS] 历史只进 prompt 区块（标记出现 {prompt.count(MARKER)} 次，均在区块内）")

    # 生成层深度应为 GEN_HISTORY_LIMIT（而非早期与意图层共用的 4）
    many = [{"role": "user", "content": f"第{i}问"} for i in range(10)]
    p2 = build_prompt("最近的呢？", slots, retrieval, history=many)
    assert f"第{10 - GEN_HISTORY_LIMIT}问" in p2, "生成层历史深度不足"
    assert "第0问" not in p2, "生成层历史深度超出上限"
    print(f"[PASS] 生成层历史深度 = {GEN_HISTORY_LIMIT}（意图/抽取层为 4，刻意不对称）")


def test_orchestrator_does_not_pass_history_to_llm_messages():
    """编排层调用生成时不得再传 history=（否则等于二次注入）。"""
    captured: dict = {}

    async def _stream(prompt: str, **kwargs) -> AsyncIterator[tuple[str, str]]:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        yield ("text", "好的")

    async def _classify(message, history=None):
        class _I:
            value = "emu_routing"
            label_zh = "车组交路查询"

        return _I(), {"question_type": "realtime"}

    async def _fill(message, intent=None, history=None):
        return Slots(target="G1")

    async def _retrieve(intent, slots, question_type=None, message=None, prefetch=None):
        return {"data": [], "sources": [], "tool_trace": [], "note": ""}

    original = (orchestrator.planner.intent.classify, orchestrator.planner.extract.fill,
                orchestrator.retrieve.retrieve, llm_client.stream_completion)
    orchestrator.planner.intent.classify = _classify          # type: ignore[assignment]
    orchestrator.planner.extract.fill = _fill                  # type: ignore[assignment]
    orchestrator.retrieve.retrieve = _retrieve         # type: ignore[assignment]
    llm_client.stream_completion = _stream             # type: ignore[assignment]
    try:
        async def _run():
            async for _ev in orchestrator.run_stream(
                "那明天呢？", history=[{"role": "user", "content": MARKER}]
            ):
                pass

        with _force_legacy()[0], _force_legacy()[1]:
            asyncio.run(_run())
    finally:
        (orchestrator.planner.intent.classify, orchestrator.planner.extract.fill,
         orchestrator.retrieve.retrieve, llm_client.stream_completion) = original  # type: ignore[assignment]

    kwargs = captured.get("kwargs") or {}
    assert not kwargs.get("history"), f"生成调用仍传了 history：{kwargs}"
    assert captured["prompt"].count(MARKER) == 1, "历史在 prompt 中出现次数异常"
    print("[PASS] 编排层调用生成时不传 history=（历史仅在 prompt 区块中一次）")


# ---------- 2. 工具并发 ----------

class _SlowRegistry:
    """每个工具固定耗时 0.2s，用于区分串行与并发。"""

    DELAY = 0.2

    def __init__(self) -> None:
        self.started: list[float] = []
        self._original = registry_mod.invoke_by_name

    async def __call__(self, name: str, params: dict) -> ToolResult:
        self.started.append(time.perf_counter())
        await asyncio.sleep(self.DELAY)
        return ToolResult(ok=True, data={"tool": name}, text=f"stub:{name}", sources=[f"s/{name}"])

    def __enter__(self):
        registry_mod.invoke_by_name = self  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        registry_mod.invoke_by_name = self._original  # type: ignore[assignment]
        return False


def test_tools_run_concurrently_and_keep_order():
    """4 个工具的耗时应接近 1 个（并发），且 data/trace 顺序与计划一致。"""
    with _SlowRegistry() as rec:
        t0 = time.perf_counter()
        out = asyncio.run(retrieve_mod.retrieve("photo_spot", Slots(location="北京", target="CR400AF")))
        elapsed = time.perf_counter() - t0

    n = len(out["tool_trace"])
    assert n >= 3, f"用例应触发多个工具，实际 {n}：{out['tool_trace']}"
    serial = n * _SlowRegistry.DELAY
    assert elapsed < serial * 0.6, (
        f"仍像串行执行：{n} 个工具耗时 {elapsed:.2f}s（串行约 {serial:.2f}s）"
    )
    # 顺序必须与计划一致（并发不得打乱输出顺序）
    assert [t.split(":")[0] for t in out["tool_trace"]] == [d["tool"] for d in out["data"]], (
        f"并发后顺序被打乱：trace={out['tool_trace']} data={[d['tool'] for d in out['data']]}"
    )
    print(f"[PASS] {n} 个工具并发执行耗时 {elapsed:.2f}s（串行约 {serial:.2f}s），顺序保持")


def test_tool_exception_does_not_break_retrieval():
    """单个工具抛异常时必须被隔离，其余工具结果照常返回。"""
    class _Boom:
        def __init__(self):
            self._original = registry_mod.invoke_by_name
            self.calls = 0

        async def __call__(self, name: str, params: dict) -> ToolResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("模拟工具内部崩溃")
            return ToolResult(ok=True, data={}, text=f"ok:{name}", sources=[])

        def __enter__(self):
            registry_mod.invoke_by_name = self  # type: ignore[assignment]
            return self

        def __exit__(self, *_exc):
            registry_mod.invoke_by_name = self._original  # type: ignore[assignment]
            return False

    with _Boom():
        out = asyncio.run(retrieve_mod.retrieve("photo_spot", Slots(location="北京", target="CR400AF")))
    assert out["tool_trace"], out
    assert "failed" in out["tool_trace"][0], f"首个工具的异常未被隔离：{out['tool_trace']}"
    assert any(t.endswith(": ok") for t in out["tool_trace"]), f"其余工具未执行：{out['tool_trace']}"
    print(f"[PASS] 工具异常被隔离 -> {out['tool_trace']}")


# ---------- 3. 输入长度约束 ----------

def test_message_validation():
    """空消息与超长消息必须被拒（否则超长输入直接放大成本与延迟）。"""
    limit = get_settings().max_message_chars
    ok = ChatRequest(message="  G1今天由哪组动车组担当？  ")
    assert ok.message == "G1今天由哪组动车组担当？", "消息应被 strip"

    for bad, why in (("", "空消息"), ("   ", "空白消息"), ("长" * (limit + 1), "超长消息")):
        try:
            ChatRequest(message=bad)
        except Exception as e:
            assert "message" in str(e), e
        else:
            raise AssertionError(f"{why}未被拒绝（limit={limit}）")
    print(f"[PASS] 空/超长消息被拒（上限 {limit} 字），正常消息通过并 strip")

    # HTTP 层：校验发生在任何 LLM 调用之前（无需网络）
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # 社区版无需登录：直接校验输入约束（在调用任何外部服务之前拦下）
    r = client.post("/api/chat", json={"message": "长" * (limit + 1)})
    assert r.status_code == 422, f"超长消息应返回 422，实际 {r.status_code}"
    r2 = client.post("/api/chat", json={"message": ""})
    assert r2.status_code == 422, f"空消息应返回 422，实际 {r2.status_code}"
    print("[PASS] HTTP 层：超长/空消息 -> 422（在调用任何外部服务之前被拦下）")


# ---- 决策层已收敛到 planner（perf P0）：本套件注入的是 legacy 两段式 ----
# 关掉确定性快路径 + 让"合并调用"失败，从而使 planner 走**传统两次调用**分支，
# 这样注入 intent.classify / extract.fill 仍然生效（行为与优化前一致，且不联网）。
def _force_legacy():
    import unittest.mock as _mock

    return [
        _mock.patch("app.pipeline.fastpath.plan", new=_mock.AsyncMock(return_value=None)),
        _mock.patch("app.pipeline.planner.chat_structured",
                    new=_mock.AsyncMock(side_effect=RuntimeError("merged call disabled in test"))),
    ]


def main():
    test_history_injected_once_into_generation()
    test_orchestrator_does_not_pass_history_to_llm_messages()
    test_tools_run_concurrently_and_keep_order()
    test_tool_exception_does_not_break_retrieval()
    test_message_validation()
    print("\n成本治理与输入约束测试全部通过 ✔")


if __name__ == "__main__":
    main()
