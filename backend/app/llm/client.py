"""OpenAI 兼容 LLM 客户端。

对外只暴露三个异步函数：
- chat(...)            普通文本生成（回答生成层用）
- chat_structured(...) 结构化输出（意图分类、槽位抽取层用）
- get_client()         返回 AsyncOpenAI 单例（未配置 Key 时为 None）

统一约定：未配置 Key 或调用失败时，抛出自定义的 LLMUnavailable 异常，
由编排层捕获并降级为友好提示（保证 /api/chat 不 500）。
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.config import get_settings

_log = logging.getLogger("railfan.llm")

# ---- 系统角色提示（统一常量，避免各函数逐字重复）----
_SYSTEM_ASSISTANT = "你是 RailFanAI，一位专业、严谨并乐于助人的中国铁路车迷助手。"
_SYSTEM_STRUCTURED = "你是 RailFanAI 的结构化解析器，只输出满足要求的 JSON。"


# ---- 运行级计费累计（ContextVar 隔离并发请求）----
def _new_metrics_dict() -> dict[str, int | float]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 0.0}


_RUN_METRICS: contextvars.ContextVar[dict[str, int | float]] = contextvars.ContextVar(
    "llm_run_metrics", default=_new_metrics_dict()
)


def reset_run_metrics() -> None:
    _RUN_METRICS.set(_new_metrics_dict())


def get_run_metrics() -> dict[str, int | float]:
    return dict(_RUN_METRICS.get())


def _record_usage(resp, latency_ms: float) -> None:
    u = getattr(resp, "usage", None)
    if u is None:
        return
    r = _RUN_METRICS.get()
    r["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
    r["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
    r["total_tokens"] += getattr(u, "total_tokens", 0) or 0
    r["latency_ms"] += latency_ms


class LLMUnavailable(RuntimeError):
    """LLM 未配置或调用失败。"""


def _err_diagnostic(e: Exception) -> str:
    """把 OpenAI 相关异常转成**可操作且不含上游原文**的中文诊断。

    安全约束：该文案会被编排层放进 `answer` 与 SSE `error` 事件直达前端，
    而上游异常原文可能带 Key 片段、组织 ID、内网地址或请求头。
    因此这里只保留"状态码级别"的提示，完整原文只写服务端日志。
    """
    _log.warning("LLM 调用失败（详情仅记日志）: %s: %s", type(e).__name__, e)
    status = getattr(e, "status_code", None) or getattr(e, "status", None)
    if status == 401:
        return "LLM 鉴权失败(HTTP 401)：API Key 无效或被拒绝 —— 请核对供应商平台的 Key（详细原因见服务端日志）。"
    if status in (400, 404):
        return f"LLM 请求被拒绝(HTTP {status})：多为模型名/参数不正确 —— 请核对模型标识（详细原因见服务端日志）。"
    if status == 429:
        return "LLM 限流或额度不足(HTTP 429)，请稍后重试（详细原因见服务端日志）。"
    if status:
        return f"LLM 返回 HTTP {status}（详细原因见服务端日志）。"
    return "LLM 调用失败（网络/连接或非 HTTP 错误，详细原因见服务端日志）。"


def get_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.llm_ready:
        raise LLMUnavailable(
            "未配置 LLM：请在根目录 .env 填写 LLM_BASE_URL 与 LLM_API_KEY（OpenAI 兼容接口）。"
        )
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )


def _reasoning_of(message) -> str:
    """从响应 message 中提取思考内容（兼容 DeepSeek/OpenAI 两种字段）。"""
    for attr in ("reasoning_content", "reasoning"):
        v = getattr(message, attr, None)
        if v:
            return v or ""
    extra = (message.model_extra or {}).get("reasoning_content") or (message.model_extra or {}).get("reasoning")
    return extra or ""


def build_messages(
    prompt: str,
    system: str | None = _SYSTEM_ASSISTANT,
    history: list[dict] | None = None,
) -> list[dict]:
    """组装 OpenAI 兼容的 messages 数组。

    结构：[system?] + history... + user(prompt)
    history 元素形如 {"role": "user"|"assistant", "content": "..."}，
    由调用方（编排层）负责裁剪长度，避免超出上下文窗口。
    """
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    for h in history or []:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})
    return messages


async def chat_with_reasoning(
    prompt: str,
    *,
    system: str | None = _SYSTEM_ASSISTANT,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    history: list[dict] | None = None,
) -> tuple[str, str]:
    """普通文本生成，返回 (回答, 思考内容 think)。

    think 取自模型的推理字段（reasoning_content/reasoning），在
    Mock 模式下返回占位思考。若模型不返回推理字段，则 thinking 为空串。
    history 为多轮上下文（不含本次 prompt）。
    """
    settings = get_settings()
    if settings.llm_mock:
        from app.llm import _mock

        return (await _mock.mock_chat(prompt, history=history), "(Mock 思考：基于关键词/正则的确定性分析过程。)")
    client = get_client()
    messages = build_messages(prompt, system, history)
    _t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=model or settings.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001 —— 向上抛统一异常以便编排层降级
        raise LLMUnavailable(_err_diagnostic(e)) from e
    _record_usage(resp, (time.perf_counter() - _t0) * 1000.0)
    msg = resp.choices[0].message
    return (msg.content or "", _reasoning_of(msg))


async def stream_completion(
    prompt: str,
    *,
    system: str | None = _SYSTEM_ASSISTANT,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    history: list[dict] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """流式生成，逐块产出 (kind, text)。

    kind ∈ {"think", "text"}：reasoning 模型先流 think，再流 text（回答）。
    Mock 模式下产出占位 think 与若干 text 分块，保证整链可用。
    流式调用结束后，若响应带 usage 则记入运行级计费。
    history 为多轮上下文（不含本次 prompt）。
    """
    settings = get_settings()
    if settings.llm_mock:
        from app.llm import _mock

        yield ("think", "(Mock 思考：确定性关键词/正则流程。)")
        for piece in await _mock.mock_stream_chunks(prompt, history=history):
            yield ("text", piece)
        return

    client = get_client()
    messages = build_messages(prompt, system, history)
    t0 = time.perf_counter()
    try:
        stream = await client.chat.completions.create(
            model=model or settings.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
    except Exception as e:  # noqa: BLE001
        raise LLMUnavailable(_err_diagnostic(e)) from e

    # 流式 usage 是“到当前为止的累计值”：逐 chunk 直接累加会重复膨胀，
    # 因此按增量计入（当前累计 - 上次累计），延迟只计一次。
    prev = {"prompt": 0, "completion": 0, "total": 0}
    _latency_recorded = False
    _metrics_ref = _RUN_METRICS.get()  # 取当前 async 上下文的计费 dict
    try:
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None and getattr(usage, "total_tokens", None):
                _metrics_ref["prompt_tokens"] += max(usage.prompt_tokens - prev["prompt"], 0)
                _metrics_ref["completion_tokens"] += max(usage.completion_tokens - prev["completion"], 0)
                _metrics_ref["total_tokens"] += usage.total_tokens - prev["total"]
                prev["prompt"] = usage.prompt_tokens
                prev["completion"] = usage.completion_tokens
                prev["total"] = usage.total_tokens
                if not _latency_recorded:
                    _metrics_ref["latency_ms"] += (time.perf_counter() - t0) * 1000.0
                    _latency_recorded = True
            choices = chunk.choices or []
            if not choices:
                continue
            delta = choices[0].delta
            rt = getattr(delta, "reasoning_content", None)
            if rt:
                yield ("think", rt)
            ct = getattr(delta, "content", None)
            if ct:
                yield ("text", ct)
    finally:
        # **必须**显式关闭上游流：用户中途"停止"或下游异常时，
        # 生成器被 aclose/GeneratorExit 中断，若不关闭则 httpx 连接与 socket 会一直挂着
        # （openai/httpx 客户端均无 __del__ 兜底，只能等 GC 且时机不确定）。
        await _aclose_stream(stream)


async def _aclose_stream(stream: Any) -> None:
    """尽力关闭上游流对象（兼容 aclose/close 两种形态）。"""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except (GeneratorExit, RuntimeError) as e:
            _log.debug("关闭上游流时忽略：%s", type(e).__name__)
        except Exception:  # noqa: BLE001
            _log.debug("关闭上游流失败", exc_info=True)
        return
    close = getattr(stream, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001
            _log.debug("关闭上游流失败", exc_info=True)


async def chat(
    prompt: str,
    *,
    system: str | None = _SYSTEM_ASSISTANT,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    history: list[dict] | None = None,
) -> str:
    """普通文本生成，返回回答字符串（思考内容见 chat_with_reasoning）。"""
    content, _ = await chat_with_reasoning(
        prompt, system=system, model=model, temperature=temperature,
        max_tokens=max_tokens, history=history,
    )
    return content


async def chat_structured(
    prompt: str,
    schema: dict,
    *,
    system: str | None = _SYSTEM_STRUCTURED,
    model: str | None = None,
    temperature: float = 0.0,
    history: list[dict] | None = None,
    no_think: bool = False,
) -> Any:
    """结构化输出：让模型严格按提供的 JSON Schema 输出。

    `no_think=True` 时会带上 `enable_thinking=False`（OpenAI 兼容的 `extra_body`）：
    分类/抽取这类任务里"思考 token"纯属延迟（实测槽位抽取的 reasoning tokens 达 243，
    关掉后 3.3s → 1.1s）。**失败自动退回不带该参数**，避免个别模型不认这个字段。


    用法：
        data = await chat_structured("……", schema={"$schema":"…", "type":"object", "properties":{...}})
    返回解析后的 dict（键名遵循 schema 的 properties）。
    history 为多轮上下文，用于消解指代（如"那明天呢"承接上一轮的车次）。
    """
    settings = get_settings()
    if settings.llm_mock:
        from app.llm import _mock

        return await _mock.mock_structured(prompt, schema)
    client = get_client()
    _model = model or settings.structured_model
    schema_json = json.dumps(schema, ensure_ascii=False)
    # 约定：结果必须是一个 JSON 对象，键名须取自 schema 的 properties。
    user_prompt = (
        f"{prompt}\n\n"
        "必须输出一个 JSON 对象，仅包含以下字段（若某字段用户未提及，取 null 或空串），"
        "不要输出任何额外文字或 markdown 代码块围栏：\n"
        f"{schema_json}"
    )
    messages = build_messages(user_prompt, system, history)
    _t0 = time.perf_counter()
    extra_body = {"enable_thinking": False} if no_think else None
    try:
        resp = await client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            **({"extra_body": extra_body} if extra_body else {}),
        )
    except Exception as e:  # noqa: BLE001
        if extra_body is not None:
            # 个别模型/网关不认 enable_thinking → 去掉该参数重试一次（不让优化变成故障）
            try:
                resp = await client.chat.completions.create(
                    model=_model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
            except Exception as e2:  # noqa: BLE001
                raise LLMUnavailable(_err_diagnostic(e2)) from e2
        else:
            raise LLMUnavailable(_err_diagnostic(e)) from e
    _record_usage(resp, (time.perf_counter() - _t0) * 1000.0)

    content = resp.choices[0].message.content or ""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:  # noqa: BLE001
        raise LLMUnavailable(f"LLM 返回非 JSON（无法解析结构化输出）: {e}") from e

    # 结构化层只接受 JSON 对象：数组/字符串/数字/null 都会让下游 AttributeError 逃出
    # LLMUnavailable 契约（曾经导致意图/抽取不再降级、整个请求报错）。
    if not isinstance(obj, dict):
        raise LLMUnavailable(
            f"LLM 返回的 JSON 不是对象（实际为 {type(obj).__name__}），无法用于结构化解析"
        )

    # 只保留 schema 声明过的键，避免噪声键混入
    props = set((schema.get("properties") or {}).keys())
    if props:
        obj = {k: obj.get(k) for k in props}
    return obj
