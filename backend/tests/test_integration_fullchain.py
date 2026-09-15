"""整链集成测试：意图→抽取→真实检索(cnrail.map)→生成(数据注入)。\n\n固定 intent/extract 返回值，让 cnrail.map 真实取数(即时、无网络)，\nrailre 用假实现(依赖外部网络，避免测试时延/抖动)，\ngenerate.chat 用假实现捕获 prompt。\n\n目的：证明 M4b——检索层拿到的真实数据被注入到生成层 prompt。\n\n运行：cd backend && PYTHONPATH=. ./.venv/bin/python tests/test_integration_fullchain.py\n"""
from __future__ import annotations

import asyncio
import unittest.mock as mock

from app.pipeline import extract, generate, intent, orchestrator
from app.tools.railre import RailReTool


async def test_fullchain_photo_spot():
    # 固定意图 + 槽位（签名需兼容 orchestrator 传入的 history 关键字参数）
    async def _classify(message, history=None):
        return intent.Intent.PHOTO_SPOT, {"intent": "photo_spot"}

    async def _fill(message, *, intent=None, history=None):
        return extract.Slots(location="吉林市", target="CR400AF", time="今天下午")

    captured = {}

    async def _chat(prompt, **kw):
        captured["prompt"] = prompt
        return (
            "（假）已根据检索到的事实作答，并引用数据来源。",
            "（think）生成层将 cnrail 地图来源与 CR400AF 交路摘要代入推理，随后组织回答。",
        )

    # railre 依赖外部网络，改为即时假实现，保持测试确定、快速
    async def _railre_invoke(self, params):
        from app.tools.base import ToolResult

        return ToolResult(
            ok=True,
            text="（测试）CR400AF 为复兴号高速动车组，交路检索结果摘要。",
            sources=["https://rail.re/CR400AF"],
            note="test stub",
        )

    patches = [
        mock.patch.object(intent, "classify", side_effect=_classify),
        mock.patch.object(extract, "fill", side_effect=_fill),
        mock.patch.object(generate, "chat_with_reasoning", side_effect=_chat),
        mock.patch.object(RailReTool, "invoke", _railre_invoke),
    ]
    for p in patches:
        p.start()
    try:
        result = await orchestrator.run("我在吉林市XX区，要拍 CR400AF，今天下午")
    finally:
        for p in patches:
            p.stop()

    # 1) 检索真实调用了 cnrail.map 并拿到吉林地图外链
    trace = "\n".join(result.tool_trace)
    assert "cnrail.map: ok" in trace, trace
    # 2) 生成层 prompt 注入了检索事实与数据来源
    assert "[检索事实]" in captured["prompt"], captured["prompt"]
    assert "cnrail.geogv.org" in captured["prompt"], captured["prompt"]
    assert "CR400AF" in captured["prompt"], captured["prompt"]
    # 3) 结果带着来源
    assert any("cnrail.geogv.org" in s for s in result.sources), result.sources
    # 4) think 内容透传到结果
    assert result.thinking and "（think）" in result.thinking, result.thinking
    print("[PASS] 整链：意图→槽位→真实cnrail地图→生成prompt注入检索事实与来源，think 透传")


async def main():
    await test_fullchain_photo_spot()
    print("\n整链集成测试全部通过 ✔")


if __name__ == "__main__":
    asyncio.run(main())
