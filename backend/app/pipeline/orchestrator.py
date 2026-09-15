"""三层流水线编排入口。

run(message) -> PipelineResult：
1. 意图分类（intent.classify）
2. 关键信息抽取（extract.fill）
3. 数据检索（retrieve.retrieve）——按需调用数据源工具
4. 回答生成（generate.generate）——并附带数据来源

LLM 未配置或调用失败时，捕获 LLMUnavailable 降级为友好提示，
保证 /api/chat 不返回 500。
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator, Any

from app.llm import client as llm_client
from app.llm.client import LLMUnavailable
from app.models import PipelineResult, SlotValue
from app.pipeline import generate, planner, prefetch as prefetch_mod, retrieve

_log = logging.getLogger("railfan.pipeline")


_PLANNER_ZH = {"deterministic": "快路径", "llm-merged": "合并调用", "llm-legacy": "两次调用"}


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 1)


def _friendly_llm_message(detail: str) -> str:
    """把 LLM 不可用统一成**可操作**的文案（块式与流式共用同一套措辞）。"""
    return (
        "抱歉，当前无法完成分析与回答：LLM 尚未配置或不可用。\n\n"
        f"详情：{detail}\n\n"
        "请在根目录 `.env` 填写 LLM_BASE_URL 与 LLM_API_KEY（OpenAI 兼容接口）后重试；"
        "无 Key 时也可设 LLM_MOCK=true 走本地确定性 mock 演示整链。"
    )


async def run_stream(message: str, history: list[dict] | None = None) -> AsyncIterator[dict]:
    """流式编排：逐事件产出 dict。

    事件类型：
      - {"type":"stage","stage":"intent/extract/retrieve","msg":...,"ms":...}  阶段进度
      - {"type":"think","delta":...}  思考内容的增量文本
      - {"type":"answer","delta":...} 回答的增量文本
      - {"type":"done","intent":...,"slots":...,"sources":...,"thinking":...,"usage":...,"latency_ms":...} 结束
      - {"type":"error","message":...} 异常降级

    history 为多轮上下文（不含本次 message），用于意图/抽取/生成三层。
    客户端中断（用户点“停止”）时，生成器被关闭，LLM 流会随之释放。
    """
    llm_client.reset_run_metrics()
    t_all = time.perf_counter()
    gathered_thinking: list[str] = []
    gathered_answer: list[str] = []
    sources: list[str] = []
    intent_str: str = ""
    slots_pairs: list = []
    logs: list[str] = []

    # 外层兜底：捕获所有非 LLM 异常以 emit error 事件，避免 SSE 直接断流
    try:
        # 1+2 决策（快路径 / 合并调用 / 传统两次调用，见 pipeline/planner.py）
        t0 = time.perf_counter()
        # ⚠️ 局部变量**不能**叫 planner：那会遮蔽上面 import 的 planner 模块
        # （RHS 的 planner.decide 会去找局部名 → UnboundLocalError）
        intent_, question_type, slots, planner_used = await planner.decide(message, history=history)
        # 决策走了 LLM（慢）才值得投机预取；快路径 1–16ms，没有可藏的时间
        pf = None
        if planner_used != "deterministic":
            pf = prefetch_mod.start(message)
        intent_str = f"{intent_.value}（{intent_.label_zh}）"
        slots_pairs = [(k, v) for k, v in slots.non_empty().items()]
        planner_label = _PLANNER_ZH.get(planner_used, planner_used)
        logs.append(f"[决策] {planner_label}：{intent_str} · 问题性质={question_type} "
                    f"· 槽位={list(slots_pairs)} · {_ms(t0)}ms")
        yield {"type": "stage", "stage": "intent",
               "msg": f"{intent_str} · {question_type}（{planner_label}）", "ms": _ms(t0) + 0.0}

        # 3 数据检索
        t0 = time.perf_counter()
        try:
            retrieval = await retrieve.retrieve(
                intent_.value, slots, question_type=question_type, message=message, prefetch=pf
            )
        finally:
            if pf is not None:
                await pf.cancel()
        sources = retrieval.get("sources") or []
        trace_str = str(retrieval.get("tool_trace") or "(无)")
        logs.append(f"[数据检索] 工具：{trace_str} · {_ms(t0)}ms")
        yield {"type": "stage", "stage": "retrieve", "msg": trace_str, "ms": _ms(t0)}

        # 4 回答生成（流式）
        t0 = time.perf_counter()
        prompt = generate.build_prompt(
            message, slots, retrieval, history=history, question_type=question_type
        )
        generation_failed = False
        failure_message = ""
        try:
            # 历史已在 prompt 的 [对话历史] 区块中；不再重复传入 messages（省钱且语义不变）
            async for kind, text in llm_client.stream_completion(prompt):
                if kind == "think":
                    gathered_thinking.append(text)
                    yield {"type": "think", "delta": text}
                else:
                    gathered_answer.append(text)
                    yield {"type": "answer", "delta": text}
        except LLMUnavailable as e:
            generation_failed = True
            failure_message = _friendly_llm_message(str(e))
            # 与块式接口保持同一套降级语义：给出可操作的指引，而不是裸报错
            yield {"type": "error", "message": failure_message}
            logs.append(f"[回答生成] 失败：{e}")
        else:
            logs.append(f"[回答生成] 流式输出完成 · {_ms(t0)}ms")

        metrics = llm_client.get_run_metrics()
        latency_ms = _ms(t_all)
        logs.append(f"[用量] 总 token：{metrics['total_tokens']}（输入 {metrics['prompt_tokens']} / 输出 {metrics['completion_tokens']}） · LLM 耗时 {metrics['latency_ms']}ms")
        logs.append(f"[整体] 流水线耗时 {latency_ms}ms")

        # done 事件字段与块式 PipelineResult 对齐（含 tool_trace），
        # 并如实标注本次是否真的产出了回答（answer_done），避免"空答案 + done=true"误导前端。
        yield {
            "type": "done",
            "intent": intent_str or "general",
            "question_type": question_type,
            "slots": [{"name": k, "value": v} for k, v in slots_pairs],
            "sources": sources,
            "tool_trace": (retrieval.get("tool_trace") or []),
            "thinking": "".join(gathered_thinking),
            "answer_done": not generation_failed,
            "planner": planner_used,
            "error": failure_message or None,
            "usage": {
                "total_tokens": metrics["total_tokens"],
                "prompt_tokens": metrics["prompt_tokens"],
                "completion_tokens": metrics["completion_tokens"],
            },
            "latency_ms": latency_ms,
            "process_logs": logs,
        }
    except LLMUnavailable as e:
        # 前置阶段（意图/抽取/检索前）就不可用：同样给可操作指引 + 一个 done 收尾，
        # 保证前端不会停在"半截状态"（此前只发 error，前端只能等到连接结束）
        yield {"type": "error", "message": _friendly_llm_message(str(e))}
        yield {
            "type": "done",
            "intent": "general（LLM 未可用）",
            "question_type": "realtime",
            "slots": [],
            "sources": [],
            "tool_trace": [],
            "thinking": "",
            "answer_done": False,
            "planner": "llm-unavailable",
            "error": _friendly_llm_message(str(e)),
            "usage": {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
            "latency_ms": _ms(t_all),
            "process_logs": logs,
        }
    except Exception as e:  # noqa: BLE001
        # 对外只给可读文案与异常类型；完整堆栈只进日志（避免把上游/内部细节漏给前端）
        _log.exception("流式编排出现未预期异常")
        msg = f"服务内部异常（{type(e).__name__}），请稍后重试；详细原因见服务端日志。"
        yield {"type": "error", "message": msg}
        yield {
            "type": "done",
            "intent": "general（异常）",
            "question_type": "realtime",
            "slots": [],
            "sources": [],
            "tool_trace": [],
            "thinking": "",
            "answer_done": False,
            "planner": "error",
            "error": msg,
            "usage": {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
            "latency_ms": _ms(t_all),
            "process_logs": logs,
        }


async def run(message: str, history: list[dict] | None = None) -> PipelineResult:
    llm_client.reset_run_metrics()
    logs: list[str] = []
    t_all = time.perf_counter()
    planner_used: str | None = None      # 决策来源；降级路径也要如实带出
    try:
        # 1+2 决策（快路径 / 合并调用 / 两次调用）
        t0 = time.perf_counter()
        intent_, question_type, slots, planner_used = await planner.decide(message, history=history)
        logs.append(
            f"[决策] {_PLANNER_ZH.get(planner_used, planner_used)}："
            f"{intent_.value}（{intent_.label_zh}）"
            f" · 问题性质={question_type} · 槽位={list(slots.non_empty())} · {_ms(t0)}ms"
        )

        # 3 数据检索（Agent/工具循环）；决策走 LLM 时先投机预取，把这段时间用起来
        pf = prefetch_mod.start(message) if planner_used != "deterministic" else None
        t0 = time.perf_counter()
        try:
            retrieval = await retrieve.retrieve(
                intent_.value, slots, question_type=question_type, message=message, prefetch=pf
            )
        finally:
            if pf is not None:
                await pf.cancel()
        logs.append(f"[数据检索] 工具：{retrieval.get('tool_trace') or '(无)'} · {_ms(t0)}ms")

        # 4 回答生成
        t0 = time.perf_counter()
        answer, sources, thinking = await generate.generate(
            message, slots, retrieval, history=history, question_type=question_type
        )
        logs.append(f"[回答生成] 完成 · {_ms(t0)}ms")

        metrics = llm_client.get_run_metrics()
        logs.append(f"[用量] 总计 token：{metrics['total_tokens']}（输入 {metrics['prompt_tokens']} / 输出 {metrics['completion_tokens']}） · LLM 耗时 {metrics['latency_ms']}ms")
        logs.append(f"[整体] 流水线耗时 {_ms(t_all)}ms")

        return PipelineResult(
            intent=f"{intent_.value}（{intent_.label_zh}）",
            question_type=question_type,
            slots=[SlotValue(name=k, value=v) for k, v in slots.non_empty().items()],
            answer=answer,
            thinking=thinking,
            sources=sources,
            tool_trace=retrieval.get("tool_trace", []) or [],
            process_logs=logs,
            planner=planner_used,
            usage={
                "total_tokens": metrics["total_tokens"],
                "prompt_tokens": metrics["prompt_tokens"],
                "completion_tokens": metrics["completion_tokens"],
            },
            latency_ms=_ms(t_all),
        )
    except LLMUnavailable as e:
        return PipelineResult(
            intent="general（LLM 未可用）",
            planner=planner_used or "llm-unavailable",
            process_logs=logs,
            slots=[SlotValue(name="raw_input", value=message)],
            # 与流式路径共用同一套可操作文案（此前两条路径措辞不同）
            answer=_friendly_llm_message(str(e)),
            sources=[],
            tool_trace=[],
        )
    except Exception as e:  # noqa: BLE001 —— 兜底，避免 500
        _log.exception("块式编排出现未预期异常")
        return PipelineResult(
            intent="general（异常）",
            planner=planner_used or "error",
            process_logs=logs,
            slots=[SlotValue(name="raw_input", value=message)],
            answer=(
                "处理时出现内部异常，请稍后重试。\n"
                f"（异常类型：{type(e).__name__}；详细原因见服务端日志。）"
            ),
            sources=[],
            tool_trace=[],
        )
