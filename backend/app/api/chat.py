"""聊天路由。

- POST /api/chat           块式：一次返回完整 PipelineResult（供测试/兼容）。
- POST /api/chat/stream    流式(SSE)：逐事件推送——阶段进度 → think 增量 → 回答增量 → done。

多轮上下文
    请求体可带 `history`（此前已完成的消息，不含本次 message）。
    三层流水线（意图/抽取/生成）都会使用它。

暂停输出
    前端用 AbortController 中断 fetch；服务端在每个事件前检查
    `request.is_disconnected()`，一旦客户端断开立即停止（同时释放 LLM 流），
    避免继续消耗 token。前端保留已渲染的部分文本。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, PipelineResult
from app.pipeline import orchestrator

_log = logging.getLogger("railfan.api")

router = APIRouter(tags=["chat"])


def _to_history(req: ChatRequest) -> list[dict]:
    """ChatRequest.history → orchestrator 需要的 list[dict]。"""
    return [{"role": m.role, "content": m.content} for m in req.history]


def _safe_label(value: Optional[str], *, max_len: int = 32) -> str:
    """净化进入日志的用户可控字段。

    `session_id` 由前端提供，若原样写日志可注入换行伪造日志行（实测可复现）；
    这里统一剥离控制字符并限制长度。
    """
    if not value:
        return "-"
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", str(value))[:max_len]
    return cleaned or "-"


@router.post("/chat", response_model=PipelineResult)
async def chat(req: ChatRequest) -> PipelineResult:
    """块式接口：一次返回完整结果（测试/兼容用）。"""
    _log.info("chat(block) session=%s", _safe_label(req.session_id))
    return await orchestrator.run(req.message, history=_to_history(req))


def _sse_encode(event: dict) -> str:
    """把一个事件 dict 编码为一条 SSE `data:` 行（并以空行结束）。"""
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """流式接口(SSE)：阶段进度 + 思考增量 + 回答增量 + 结束计费事件。

    客户端断开（用户点“停止”或关闭页面）时立即停止生成。
    """
    _log.info("chat(stream) session=%s", _safe_label(req.session_id))
    history = _to_history(req)

    async def gen():
        agen = orchestrator.run_stream(req.message, history=history)
        completed = False
        try:
            async for event in agen:
                # 客户端已断开 → 停止生成（释放 LLM 流，不再消耗 token）
                if await request.is_disconnected():
                    _log.info("客户端断开，提前停止生成")
                    break
                yield _sse_encode(event)
                if event.get("type") in ("done", "error"):
                    completed = True
        except asyncio.CancelledError:
            # 服务端/客户端取消：安静退出，交由 aclose 释放上游资源
            _log.info("生成被取消（客户端中断）")
            raise
        except Exception as e:  # noqa: BLE001
            _log.exception("SSE 生成异常: %s", e)
            yield _sse_encode({"type": "error", "message": f"服务异常：{type(e).__name__}"})
        finally:
            # 确保上游 LLM 异步生成器被关闭（触发 httpx 流释放）。
            # 早期关闭可能让上游抛 GeneratorExit/RuntimeError，这里吞掉，
            # 避免"用户停止"时在日志里刷出无意义的堆栈。
            try:
                await agen.aclose()
            except (GeneratorExit, RuntimeError):
                pass
            except Exception:  # noqa: BLE001
                _log.debug("关闭上游生成器时出现异常", exc_info=True)
            if not completed:
                _log.debug("流式生成未正常完成（可能被用户停止）")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/sessions")
async def sessions_info() -> dict:
    """会话能力自述（前端用于探测是否支持多轮上下文）。"""
    return {
        "multi_turn": True,
        "cancellable": True,
        "max_history_turns": 6,
        # 社区版：无需登录、无账户与计费
        "auth_required": False,
        "billing_enabled": False,
    }
