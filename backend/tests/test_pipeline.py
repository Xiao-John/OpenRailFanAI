"""三层流水线端到端验证（使用假 LLM，不依赖真实 API Key）。

覆盖：
1. 意图分类 + 槽位抽取在假 LLM 下正确组装进 PipelineResult
2. LLM 未配置（llm_api_key 为空）时 orchestrator 友好降级，不抛异常

运行：cd backend && ./.venv/bin/python tests/test_pipeline.py
"""
from __future__ import annotations

import asyncio
import unittest.mock as mock

from app.pipeline import extract, generate, intent, orchestrator
from app.pipeline import planner as planner_mod


def _disable_fastpath():
    """关掉确定性快路径，强制走 LLM 路径。

    本套件用假 LLM 固定槽位（location="吉林市XX区"）来验证**整链**；
    快路径会给出等价但字面不同的槽位（location="吉林"），两套断言会互相打架。
    快路径本身的正确性由 `test_perf_fastpath.py` 专门覆盖。
    """
    return mock.patch("app.pipeline.fastpath.plan", new=mock.AsyncMock(return_value=None))


def _patch_llm():
    """把三层用到的 LLM 函数替换为可控假实现。"""
    patches = [
        # 决策层已合并为一次调用（perf P0-2）：假数据要打在**新的接缝** planner 上
        mock.patch.object(
            planner_mod,
            "chat_structured",
            return_value={
                "intent": "photo_spot",
                "question_type": "realtime",
                "location": "吉林市XX区",
                "target": "CR400AF",
                "time": "今天下午",
                "direction": None,
                "extra": None,
            },
        ),
        mock.patch.object(
            generate,
            "chat_with_reasoning",
            return_value=(
                "（假 LLM）已理解：你想在吉林市附近拍摄复兴号 CR400AF，时间为今天下午。",
                "（think）先在槽位锁定 目标=CR400AF、地点=吉林市XX区、时间=今天下午，再决定拍摄建议。",
            ),
        ),
    ]
    return patches


async def test_full_pipeline():
    with mock.patch("app.config.get_settings") as _noop:  # 保持默认设置即可
        pass
    patches = _patch_llm() + [_disable_fastpath()]
    for p in patches:
        p.start()
    try:
        result = await orchestrator.run("我在吉林市XX区，要拍 CR400AF，今天下午")
    finally:
        for p in patches:
            p.stop()

    assert "photo_spot" in result.intent, result.intent
    slot_map = {s.name: s.value for s in result.slots}
    assert slot_map.get("target") == "CR400AF", slot_map
    assert slot_map.get("location") == "吉林市XX区", slot_map
    assert slot_map.get("time") == "今天下午", slot_map
    assert "理解：你想在吉林市" in result.answer, result.answer
    assert result.thinking and "（think）" in result.thinking, result.thinking
    print("[PASS] intent=photo_spot, slots={目标/location/time} 正确组装进结果，think 透传")


async def test_degradation_without_llm():
    # 模拟未配置 LLM：让 classify 抛 LLMUnavailable
    async def _boom(*a, **k):
        from app.llm.client import LLMUnavailable

        raise LLMUnavailable("未配置 LLM（测试降级）")

    # 决策已合并为一次调用：让**合并调用**抛 LLMUnavailable（快路径也关掉，
    # 否则这条问句仍可能被快路径接管 → 测不到降级）
    with mock.patch.object(planner_mod, "chat_structured", side_effect=_boom), \
         mock.patch("app.pipeline.fastpath.plan", new=mock.AsyncMock(return_value=None)):
        result = await orchestrator.run("帮我查车次")

    assert "general" in result.intent, result.intent
    assert "未配置" in result.answer, result.answer
    assert "LLM".lower() in result.answer.lower() or "api_key" in result.answer.lower(), result.answer
    print("[PASS] LLM 未配置时友好降级，不抛异常")


async def test_stream_pipeline():
    """流式 SSE 路径：确认 stage/think/answer/done 事件链完整，日志与 usage 透传。"""
    patches = _patch_llm() + [_disable_fastpath()]  # intent / extract 仍用 chat_structured 假数据

    # 额外 patch 流式生成：用假的 async generator 模拟 stream_completion
    async def _mock_stream(*a, **k):
        yield ("think", "(Mock think: keyword analysis.)")
        yield ("text", "(Mock answer) Understood")
        yield ("text", ": photo spot at Jilin.")

    from app.llm import client as llm_client

    patches.append(mock.patch.object(llm_client, "stream_completion", side_effect=_mock_stream))

    for p in patches:
        p.start()
    events = []
    try:
        async for ev in orchestrator.run_stream("拍 CR400AF，今天下午"):
            events.append(ev)
    finally:
        for p in patches:
            p.stop()

    types = [e["type"] for e in events]
    # 检查必需的事件类型
    assert "stage" in types, f"缺 stage 事件: {types}"
    assert "think" in types, f"缺 think 事件: {types}"
    assert "answer" in types, f"缺 answer 事件: {types}"
    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1, f"done 事件应为 1，实际: {len(done_events)}"
    d = done_events[0]
    assert d.get("intent"), "done 缺 intent"
    assert isinstance(d.get("usage"), dict), "done 缺 usage"
    assert isinstance(d.get("process_logs"), list) and len(d["process_logs"]) >= 5, (
        f"process_logs 条目过少: {d.get('process_logs')}"
    )
    print("[PASS] SSE 流式路径：stage/think/answer/done 事件完整，日志与 usage 透传")


async def main():
    await test_full_pipeline()
    await test_degradation_without_llm()
    await test_stream_pipeline()
    print("\n全部测试通过 ✔")


if __name__ == "__main__":
    asyncio.run(main())
