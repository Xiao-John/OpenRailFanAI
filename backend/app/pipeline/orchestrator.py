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
import os
import re
import time
from typing import AsyncIterator, Any

from app.llm import client as llm_client
from app.llm.client import LLMUnavailable
from app.config import get_settings
from app.models import PipelineResult, SlotValue
from app.display_result import serialize_display_results
from app.pipeline import generate
from app.pipeline import repetition, planner, prefetch as prefetch_mod, retrieve

_log = logging.getLogger("railfan.pipeline")


_PLANNER_ZH = {"deterministic": "快路径", "llm-merged": "合并调用", "llm-legacy": "两次调用"}
_FARE_ONLY_RE = re.compile(r"票价|票多少钱|多少钱|票面价")
_OTHER_TICKET_RE = re.compile(r"余票|还有票|有票|候补|能买")
_KNOWLEDGE_RE = re.compile(r"为什么|原理|历史|发展|区别|科普")


def _ticket_display(intent, question_type: str, message: str) -> tuple[str, str]:
    """为明确的票价问题标出真实查询类型，避免误显示为余票/混合型。"""
    name = f"{intent.value}（{intent.label_zh}）"
    if (intent.value == "ticket" and _FARE_ONLY_RE.search(message)
            and not _OTHER_TICKET_RE.search(message)):
        name = "ticket（票价查询）"
        if not _KNOWLEDGE_RE.search(message):
            question_type = "realtime"
    return name, question_type


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 1)


async def _decide_with_prefetch(message: str, history: list[dict] | None,
                                display_action: dict | None = None):
    pf = None

    def begin() -> None:
        nonlocal pf
        if pf is None and not display_action:
            try:
                pf = prefetch_mod.start(message)
            except Exception as e:  # speculative optimization must not affect planning
                _log.debug("预取未启动：%s", type(e).__name__)

    main = os.environ.get("APP_VARIANT", "main").lower() != "lm"
    token = planner.on_llm_decision.set(begin if main else None)
    try:
        decision = await planner.decide(message, history=history)
        if not main and decision[3] != "deterministic":
            pf = prefetch_mod.start(message)
        return decision, pf
    except BaseException:
        if pf is not None:
            await pf.cancel()
        raise
    finally:
        planner.on_llm_decision.reset(token)


def _tool_failed(retrieval: dict) -> bool:
    return bool(retrieval.get("display_errors")) or any(
        re.search(r":\s*(?:failed|error)\b", str(item), re.I)
        for item in (retrieval.get("tool_trace") or [])
    )


def _friendly_llm_message(detail: str) -> str:
    """把 LLM 不可用统一成**可操作**的文案（块式与流式共用同一套措辞）。"""
    return (
        "抱歉，当前无法完成分析与回答：LLM 尚未配置或不可用。\n\n"
        f"详情：{detail}\n\n"
        "可任选一种方式配置（均为 OpenAI 兼容接口）："
        "① 在界面「设置」里选内置供应商并填写自己的 Key；"
        "② 在根目录 `.env` 填 LLM_BASE_URL 与 LLM_API_KEY（旧方式，仍兼容）；"
        "③ 用 LLM_PROVIDER 选内置供应商（如 deepseek / openai / ollama），"
        "或用 LLM_PROVIDERS / LLM_PROVIDERS_FILE 添加自定义供应商。"
        "无 Key 时也可设 LLM_MOCK=true 走本地确定性 mock 演示整链。"
    )


def _rule_fallback(prompt: str) -> str:
    """模型不可用时的确定性规则排版；无事实可搬、或该功能被关掉时返回空串。

    刻意**不抛异常**：降级路径自己再出错会把"模型不可用"变成"500 内部异常"，
    把真正的故障原因埋掉。
    """
    if not getattr(get_settings(), "llm_fallback_render", True):
        return ""
    try:
        from app.llm import _mock

        return _mock.render_fallback(prompt)
    except Exception as e:  # noqa: BLE001
        _log.warning("规则降级渲染失败（忽略，仅保留错误提示）：%s: %s", type(e).__name__, e)
        return ""


async def run_stream(
    message: str,
    history: list[dict] | None = None,
    llm: dict | None = None,
    display_action: dict | None = None,
) -> AsyncIterator[dict]:
    """流式编排：逐事件产出 dict。

    事件类型：
      - {"type":"stage","stage":"intent/extract/retrieve","msg":...,"ms":...}  阶段进度
      - {"type":"think","delta":...}  思考内容的增量文本
      - {"type":"answer","delta":...} 回答的增量文本
      - {"type":"done","intent":...,"slots":...,"sources":...,"thinking":...,"usage":...,"latency_ms":...} 结束
      - {"type":"error","message":...} 异常降级

    history 为多轮上下文（不含本次 message），用于意图/抽取/生成三层。
    llm 为本请求的供应商覆盖（BYOK：provider/model/api_key/base_url/api），
    在生成器**内部**就位，保证意图/抽取/生成三层都用同一个供应商。
    客户端中断（用户点“停止”）时，生成器被关闭，LLM 流会随之释放。
    """
    # 供应商与计费都是请求级 ContextVar：必须在生成器内部设置，
    # 否则可能落在 SSE 响应任务之外，子任务读不到（表现为"选了供应商却不生效"）。
    llm_client.set_active_provider(llm)
    llm_client.reset_run_metrics()
    t_all = time.perf_counter()
    gathered_thinking: list[str] = []
    gathered_answer: list[str] = []
    sources: list[str] = []
    intent_str: str = ""
    slots_pairs: list = []
    logs: list[str] = []
    from app import metrics as perf_metrics
    request_recorded = False
    pipeline_recorded = False
    pf = None
    first_think = False
    first_answer = False
    first_upstream = set()

    # 外层兜底：捕获所有非 LLM 异常以 emit error 事件，避免 SSE 直接断流
    try:
        # 1+2 决策（快路径 / 合并调用 / 传统两次调用，见 pipeline/planner.py）
        t0 = time.perf_counter()
        # ⚠️ 局部变量**不能**叫 planner：那会遮蔽上面 import 的 planner 模块
        # （RHS 的 planner.decide 会去找局部名 → UnboundLocalError）
        decision, pf = await _decide_with_prefetch(message, history, display_action)
        intent_, question_type, slots, planner_used, defer_reason = decision
        intent_str, question_type = _ticket_display(intent_, question_type, message)
        slots_pairs = [(k, v) for k, v in slots.non_empty().items()]
        planner_label = _PLANNER_ZH.get(planner_used, planner_used)
        # 快路径没接管时，把"为什么交回 LLM"一并写进这条**用户可见**的日志：
        # 以前只有一句"交回 LLM"，看不出是没匹配到问法，还是问法对上了但缺槽位
        defer_note = f" · 快路径未接管：{defer_reason}" if defer_reason else ""
        logs.append(f"[决策] {planner_label}：{intent_str} · 问题性质={question_type} "
                    f"· 槽位={list(slots_pairs)} · {_ms(t0)}ms{defer_note}")
        perf_metrics.record_stage("decision", _ms(t0))
        yield {"type": "stage", "stage": "intent",
               "msg": f"{intent_str} · {question_type}（{planner_label}）", "ms": _ms(t0) + 0.0,
               "recognized": slots.target or ""}

        # 3 数据检索
        t0 = time.perf_counter()
        try:
            retrieval = await retrieve.retrieve(
                intent_.value, slots, question_type=question_type, message=message, prefetch=pf,
                display_action=display_action,
            )
        finally:
            if pf is not None:
                await pf.cancel()
        sources = retrieval.get("sources") or []
        trace_str = str(retrieval.get("tool_trace") or "(无)")
        structured_tool_names = {"train.schedule", "emu.routing"}
        structured_tool_errors = [
            item for item in (retrieval.get("display_errors") or [])
            if isinstance(item, dict) and item.get("tool") in structured_tool_names
        ]
        buffer_structured_answer = any(
            isinstance(item, dict) and item.get("tool") in {"train.schedule", "emu.routing"}
            for item in (retrieval.get("data") or [])
        ) or bool(structured_tool_errors)
        logs.append(f"[数据检索] 工具：{trace_str} · {_ms(t0)}ms")
        perf_metrics.record_stage("retrieve", _ms(t0))
        yield {"type": "stage", "stage": "retrieve", "msg": trace_str, "ms": _ms(t0)}

        # 4 回答生成（流式）
        t0 = time.perf_counter()
        generation_t0 = t0
        prompt = generate.build_prompt(
            message, slots, retrieval, history=history, question_type=question_type
        )
        generation_failed = False
        degraded = False          # 是否走了"确定性规则排版"降级（答案非模型产出）
        failure_message = ""
        loop_cut = False
        stream = None
        try:
            # 历史已在 prompt 的 [对话历史] 区块中；不再重复传入 messages（省钱且语义不变）
            checked = 0
            stream = llm_client.stream_completion(
                prompt, **generate.completion_options(retrieval, question_type))
            async for kind, text in stream:
                if text and kind not in first_upstream:
                    perf_metrics.record_stage("first_upstream_" + ("think" if kind == "think" else "answer"),
                                              _ms(generation_t0))
                    first_upstream.add(kind)
                if kind == "think":
                    gathered_thinking.append(text)
                    if not buffer_structured_answer:
                        if text and not first_think:
                            perf_metrics.record_stage("first_public_think", _ms(t_all))
                            first_think = True
                        yield {"type": "think", "delta": text}
                else:
                    gathered_answer.append(text)
                    if not buffer_structured_answer:
                        if text and not first_answer:
                            perf_metrics.record_stage("first_public_answer", _ms(t_all))
                            first_answer = True
                        yield {"type": "answer", "delta": text}
                    # 重复循环检测：循环一定长在尾部，所以要**边生成边查**。
                    # 早发现早停流，省下的是设备端真金白银的解码时间 ——
                    # 实测 0.8B 白吐了 1200 token（撞上限）才停，那一段全是重复。
                    # 检查本身 0.03ms，每 128 字查一次可以忽略。
                    total = sum(len(x) for x in gathered_answer)
                    if total - checked >= 128:
                        checked = total
                        if repetition.detect("".join(gathered_answer)) is not None:
                            loop_cut = True
                            break
        except LLMUnavailable as e:
            generation_failed = True
            failure_message = _friendly_llm_message(str(e))
            # 与块式接口保持同一套降级语义：给出可操作的指引，而不是裸报错
            yield {"type": "error", "message": failure_message}
            logs.append(f"[回答生成] 失败：{e}")
            # 降级第二段：模型不可用，但**检索已经完成**。快路径命中的问法本来就不需要
            # 模型决策，数据是真金白银抓到的 —— 用确定性规则把它排版出来，比只丢一句
            # 错误有用得多。没有事实可搬时不产出任何内容（错误提示仍然独立可见）。
            fb = _rule_fallback(prompt)
            if fb:
                degraded = True
                gathered_answer.append(fb)
                if not buffer_structured_answer:
                    if not first_answer:
                        perf_metrics.record_stage("first_public_answer", _ms(t_all))
                        first_answer = True
                    yield {"type": "answer", "delta": fb}
                logs.append("[回答生成] 已降级为确定性规则排版（未使用语言模型，首行已自报）")
        else:
            logs.append(f"[回答生成] 流式输出完成 · {_ms(t0)}ms")
        finally:
            # Breaking on repetition or closing the outer SSE generator must close
            # upstream now, rather than defer socket release to GC finalization.
            if stream is not None:
                await stream.aclose()
            perf_metrics.record_stage("generation", _ms(generation_t0))

        if loop_cut:
            # 已经吐出去的那段重复文本要**收回来**：前端按 delta 累加，所以下发一次
            # `replace` 让它整体替换。不这么做的话用户会看到一屏重复内容，
            # 而我们只是"停止生成"——问题照样在界面上。
            full = "".join(gathered_answer)
            cut_at = repetition.detect(full)
            kept = (full[:cut_at] if cut_at is not None else full).rstrip()
            kept += "\n\n（后续内容出现连续重复，已在此截断。）"
            gathered_answer = [kept]
            llm_client.mark_truncated()          # 复用既有的"被截断"如实标注机制
            if not buffer_structured_answer:
                yield {"type": "replace", "text": kept}
            logs.append(f"[回答生成] ⚠️ 检测到连续重复，已在第 {cut_at or 0} 字处截断并停止生成")

        if buffer_structured_answer:
            buffered = "".join(gathered_answer)
            if structured_tool_errors:
                gathered_answer = [""]
                logs.append("[回答生成] 已抑制结构化工具失败时未经核验的模型事实文本")
            elif generate.suppress_duplicate_structured_answer(buffered, retrieval):
                gathered_answer = [""]
                logs.append("[回答生成] 已抑制重复或冲突的结构化事实文本，保留结构化结果卡片")
            elif buffered and not generation_failed and not degraded:
                # 结构化查询只在完整事实校验后发布一次叙述，避免先把错误数据推到页面。
                if not first_answer:
                    perf_metrics.record_stage("first_public_answer", _ms(t_all))
                    first_answer = True
                yield {"type": "answer", "delta": buffered}
            elif buffered and degraded:
                if not first_answer:
                    perf_metrics.record_stage("first_public_answer", _ms(t_all))
                    first_answer = True
                yield {"type": "answer", "delta": buffered}

        metrics = llm_client.get_run_metrics()
        latency_ms = _ms(t_all)
        logs.append(f"[用量] 总 token：{metrics['total_tokens']}（输入 {metrics['prompt_tokens']} / 输出 {metrics['completion_tokens']}） · LLM 耗时 {metrics['latency_ms']}ms")
        logs.append(f"[整体] 流水线耗时 {latency_ms}ms")
        perf_metrics.record_stage("pipeline", latency_ms)
        pipeline_recorded = True

        # done 事件字段与块式 PipelineResult 对齐（含 tool_trace），
        # 并如实标注本次是否真的产出了回答（answer_done），避免"空答案 + done=true"误导前端。
        # 截断要**分因**：两者对用户的意义完全不同 ——
        #   length：回答没写完，调大输出上限可解决
        #   repetition：模型退化成复读，调大上限只会让它重复得更久
        # 混成一句话会把用户引向错误的操作。
        truncate_reason = None
        if llm_client.was_truncated():
            truncate_reason = "repetition" if loop_cut else "length"
            if truncate_reason == "length":
                logs.append("⚠️ 回答因输出长度上限被截断（可提高 LLM_MAX_TOKENS 后重试）")
        display_results = serialize_display_results(retrieval.get("data") or [], retrieval.get("display_errors") or [])
        if display_results:
            perf_metrics.record_stage("card_delivery", _ms(t_all))
        perf_metrics.record_request("completed", degraded=degraded,
                                    tool_failed=_tool_failed(retrieval),
                                    model_failed=generation_failed,
                                    truncated=llm_client.was_truncated())
        request_recorded = True
        yield {
            "type": "done",
            "intent": intent_str or "general",
            "question_type": question_type,
            "slots": [{"name": k, "value": v} for k, v in slots_pairs],
            "sources": sources,
            "tool_trace": (retrieval.get("tool_trace") or []),
            "thinking": "" if buffer_structured_answer else "".join(gathered_thinking),
            "answer_done": not generation_failed,
            # 非模型产出的降级回复：answer_done 仍为 False（模型确实没答上），
            # 用这个字段如实区分"有正文但是规则排版"与"根本没有正文"
            "degraded": degraded,
            "truncated": llm_client.was_truncated(),
            "truncate_reason": truncate_reason,
            "planner": planner_used,
            "error": failure_message or None,
            "usage": {
                "total_tokens": metrics["total_tokens"],
                "prompt_tokens": metrics["prompt_tokens"],
                "completion_tokens": metrics["completion_tokens"],
            },
            "latency_ms": latency_ms,
            "process_logs": logs,
            "display_results": display_results,
        }
    except LLMUnavailable as e:
        perf_metrics.record_request("failed")
        request_recorded = True
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
        perf_metrics.record_request("failed")
        request_recorded = True
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
    finally:
        try:
            if pf is not None:
                await pf.cancel()
        finally:
            if not pipeline_recorded:
                perf_metrics.record_stage("pipeline", _ms(t_all))
            if not request_recorded:
                perf_metrics.record_request("cancelled")


async def run(
    message: str,
    history: list[dict] | None = None,
    llm: dict | None = None,
) -> PipelineResult:
    # 供应商与计费都是请求级 ContextVar，与 run_stream 保持一致的就位位置
    llm_client.set_active_provider(llm)
    llm_client.reset_run_metrics()
    logs: list[str] = []
    t_all = time.perf_counter()
    planner_used: str | None = None      # 决策来源；降级路径也要如实带出
    pf = None
    request_recorded = False
    pipeline_recorded = False
    from app.metrics import record_request, record_stage
    try:
        # 1+2 决策（快路径 / 合并调用 / 两次调用）
        t0 = time.perf_counter()
        decision, pf = await _decide_with_prefetch(message, history)
        intent_, question_type, slots, planner_used, defer_reason = decision
        intent_str, question_type = _ticket_display(intent_, question_type, message)
        logs.append(
            f"[决策] {_PLANNER_ZH.get(planner_used, planner_used)}："
            f"{intent_str}"
            f" · 问题性质={question_type} · 槽位={list(slots.non_empty())} · {_ms(t0)}ms"
        )
        from app.metrics import record_stage
        record_stage("decision", _ms(t0))

        # 3 数据检索（Agent/工具循环）；决策走 LLM 时先投机预取，把这段时间用起来
        t0 = time.perf_counter()
        try:
            retrieval = await retrieve.retrieve(
                intent_.value, slots, question_type=question_type, message=message, prefetch=pf
            )
        finally:
            if pf is not None:
                await pf.cancel()
        logs.append(f"[数据检索] 工具：{retrieval.get('tool_trace') or '(无)'} · {_ms(t0)}ms")
        record_stage("retrieve", _ms(t0))

        # 4 回答生成
        t0 = time.perf_counter()
        degraded = False
        generation_failed = False
        loop_cut = False
        try:
            answer, sources, thinking = await generate.generate(
                message, slots, retrieval, history=history, question_type=question_type
            )
            # 与流式路径**同一套**重复循环兜底。两处都要有：块式接口同样对外可用，
            # 只在流式里加会留下一条没被保护的路径（本仓库吃过"两处各写一份"的亏）。
            _cut = repetition.detect(answer)
            if _cut is not None:
                loop_cut = True
                answer = answer[:_cut].rstrip() + "\n\n（后续内容出现连续重复，已在此截断。）"
                llm_client.mark_truncated()
                logs.append(f"[回答生成] ⚠️ 检测到连续重复，已在第 {_cut} 字处截断")
        except LLMUnavailable as e:
            generation_failed = True
            # 与流式路径同一套降级：模型不可用但检索已完成时，用确定性规则把事实排出来。
            # 重新构一遍 prompt 只是纯字符串拼接（不发请求）—— 换取的是一份可交付的数据。
            prompt = generate.build_prompt(
                message, slots, retrieval, history=history, question_type=question_type
            )
            answer = _rule_fallback(prompt)
            if not answer:
                raise                      # 没有事实可搬 → 交由外层统一给可操作提示
            sources, thinking, degraded = retrieval.get("sources") or [], "", True
            logs.append(f"[回答生成] 模型不可用，已降级为确定性规则排版：{e}")
        finally:
            record_stage("generation", _ms(t0))
        logs.append(f"[回答生成] 完成 · {_ms(t0)}ms")

        metrics = llm_client.get_run_metrics()
        logs.append(f"[用量] 总计 token：{metrics['total_tokens']}（输入 {metrics['prompt_tokens']} / 输出 {metrics['completion_tokens']}） · LLM 耗时 {metrics['latency_ms']}ms")
        logs.append(f"[整体] 流水线耗时 {_ms(t_all)}ms")
        record_stage("pipeline", _ms(t_all))
        pipeline_recorded = True

        truncate_reason = None
        if llm_client.was_truncated():
            truncate_reason = "repetition" if loop_cut else "length"
            if truncate_reason == "length":
                logs.append("⚠️ 回答因输出长度上限被截断（可调大「最大输出 token」或 LLM_MAX_TOKENS 后重试）")
        result = PipelineResult(
            intent=intent_str,
            question_type=question_type,
            slots=[SlotValue(name=k, value=v) for k, v in slots.non_empty().items()],
            answer=answer,
            thinking=thinking,
            sources=sources,
            tool_trace=retrieval.get("tool_trace", []) or [],
            process_logs=logs,
            planner=planner_used,
            # 非模型产出的降级回复（确定性规则排版）：如实标注，避免被当成模型回答
            degraded=degraded,
            # 模型若因长度上限停止，必须如实带给前端（此前该字段被完全忽略）
            truncated=llm_client.was_truncated(),
            truncate_reason=truncate_reason,
            usage={
                "total_tokens": metrics["total_tokens"],
                "prompt_tokens": metrics["prompt_tokens"],
                "completion_tokens": metrics["completion_tokens"],
            },
            latency_ms=_ms(t_all),
            display_results=serialize_display_results(
                retrieval.get("data") or [], retrieval.get("display_errors") or []
            ),
        )
        if result.answer:
            record_stage("first_public_answer", _ms(t_all))
        if result.display_results:
            record_stage("card_delivery", _ms(t_all))
        record_request("completed", degraded=degraded,
                       tool_failed=_tool_failed(retrieval),
                       model_failed=generation_failed,
                       truncated=llm_client.was_truncated())
        request_recorded = True
        return result
    except LLMUnavailable as e:
        from app.metrics import record_request
        record_request("failed")
        request_recorded = True
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
        from app.metrics import record_request
        record_request("failed")
        request_recorded = True
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
    finally:
        try:
            if pf is not None:
                await pf.cancel()
        finally:
            if not pipeline_recorded:
                record_stage("pipeline", _ms(t_all))
            if not request_recorded:
                record_request("cancelled")
