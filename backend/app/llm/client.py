"""OpenAI 兼容 LLM 客户端（多供应商 + 双 API 方言）。

对外暴露：
- chat(...)              普通文本生成
- chat_with_reasoning(...) 生成并返回 (回答, 思考)
- stream_completion(...) 流式生成，逐块产出 (kind, text)
- chat_structured(...)   结构化输出（意图分类、槽位抽取）
- get_client(...)        AsyncOpenAI 实例

**方言兼容（社区版核心诉求）**
各家"OpenAI 兼容"实际分两类端点，互不通用：
- `chat_completions` → `POST {base}/chat/completions`
- `responses`        → `POST {base}/responses`
供应商声明 `api="auto"`（默认）时：先按 Chat Completions 发；若上游明确表示
**端点不存在**（404/405 或 400 且文案指明 unknown request url / no route），
则改发 Responses 并**把结论按供应商缓存**（同一次进程内不再重复试探）。
`responses` 与 `chat_completions` 的响应结构、usage 字段名都不同，由
`_map_*` / `_usage_tuple` 统一抹平为内部格式。

**参数降级阶梯**
不同网关对新参数的支持度参差（推理模型常拒 `temperature`，部分网关不认
`response_format` 或 `enable_thinking`）。任何一次请求失败后，若能从错误中
定位到"某个可选参数不被支持"，就**丢掉该参数重试**，而不是直接把失败抛给用户。
最多 `_MAX_ATTEMPTS` 次，避免死循环。

**统一约定**：未配置或最终失败时抛 `LLMUnavailable`，由编排层捕获降级为友好提示
（保证 /api/chat 不 500）。诊断文案**不含上游原文**，避免 Key/内网地址外泄。
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.config import get_settings
from app.llm.providers import ApiDialect, Provider, resolve_provider

_log = logging.getLogger("railfan.llm")

# ---- 系统角色提示（统一常量，避免各函数逐字重复）----
_SYSTEM_ASSISTANT = "你是 RailFanAI，一位专业、严谨并乐于助人的中国铁路车迷助手。"
_SYSTEM_STRUCTURED = "你是 RailFanAI 的结构化解析器，只输出满足要求的 JSON。"

# 参数降级阶梯：出现其中任意一个即停止重试（防止无限丢参数）
_MAX_ATTEMPTS = 5


# ---- 运行级计费累计（ContextVar 隔离并发请求）----
def _new_metrics_dict() -> dict[str, int | float]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 0.0}


# 默认值**必须是 None**：ContextVar 的 default 在导入时只求值一次，
# 若直接放一个可变 dict，所有上下文会共享同一个对象 —— set() 出去的副本仍指向它，
# 于是在编排层忘记 reset（或直接调用客户端）时，计费会在请求之间串味。
# 惰性创建可保证"每个上下文有自己的 dict"。
_RUN_METRICS: contextvars.ContextVar[dict[str, int | float] | None] = contextvars.ContextVar(
    "llm_run_metrics", default=None
)


def _metrics_ref() -> dict[str, int | float]:
    """取当前上下文的计费 dict，没有就建一个（见上方注释）。"""
    m = _RUN_METRICS.get()
    if m is None:
        m = _new_metrics_dict()
        _RUN_METRICS.set(m)
    return m

# ---- 请求级供应商选择（由 API 层按请求设置，未设置时用配置的默认供应商）----
_ACTIVE_PROVIDER: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "llm_active_provider", default=None
)

# ---- 方言探测结果缓存：cache_key -> "chat_completions" | "responses" ----
_DIALECT_CACHE: dict[str, str] = {}


def reset_run_metrics() -> None:
    _RUN_METRICS.set(_new_metrics_dict())


def get_run_metrics() -> dict[str, int | float]:
    return dict(_metrics_ref())


def set_active_provider(spec: dict[str, Any] | None) -> None:
    """设置本次请求生效的供应商（BYOK）。

    spec 支持的键：provider(id) / model / api_key / base_url / api。
    传 None 或空 dict 表示"用配置里的默认供应商"。
    """
    _ACTIVE_PROVIDER.set(dict(spec) if spec else None)


def current_provider() -> Provider:
    """解析当前生效的供应商（请求级覆盖 > LLM_PROVIDER > 默认）。"""
    spec = _ACTIVE_PROVIDER.get() or {}
    overrides = {
        k: spec.get(k)
        for k in ("api_key", "base_url", "model", "api", "structured_model")
        if spec.get(k)
    }
    try:
        return resolve_provider(spec.get("provider"), overrides)
    except ValueError as e:
        raise LLMUnavailable(str(e)) from e


def provider_label() -> str:
    """当前供应商的可读标识（进日志/流程日志，**不含 Key**）。"""
    try:
        p = current_provider()
    except LLMUnavailable:
        return "-"
    return f"{p.label}({p.id}) · {p.api}"


class LLMUnavailable(RuntimeError):
    """LLM 未配置或调用失败。"""


# ---------------------------------------------------------------- 诊断
def _status_of(e: Exception) -> int | None:
    status = getattr(e, "status_code", None) or getattr(e, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _raw_message(e: Exception) -> str:
    """取上游错误原文（**仅供内部分类使用，绝不外泄/写日志到用户可见处**）。"""
    parts = []
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            parts.append(str(err.get("message") or ""))
            parts.append(str(err.get("code") or ""))
            parts.append(str(err.get("type") or ""))
        elif err:
            parts.append(str(err))
        parts.append(str(body.get("message") or ""))
    elif body:
        parts.append(str(body))
    parts.append(str(e))
    return " ".join(p for p in parts if p).lower()


# 可选参数 → 上游可能用来拒绝它的关键词（参数降级阶梯据此决定丢谁）
_DROPPABLE_PARAMS: dict[str, tuple[str, ...]] = {
    "temperature": ("temperature",),
    "response_format": ("response_format", "response format", "json_object", "json mode"),
    "enable_thinking": ("enable_thinking", "thinking"),
    "max_tokens": ("max_tokens", "max output", "max_output_tokens", "max tokens"),
    "stream_options": ("stream_options", "include_usage"),
}

# 上游"这个端点不存在"的常见表述（必须与"模型/参数"类错误区分开，见下）
_ENDPOINT_HINTS = (
    "unknown request url", "unknown url", "no route", "unsupported endpoint",
    "endpoint not found", "invalid url", "404 page", "cannot post", "method not allowed",
)
# 出现这些词说明问题在模型名而不是端点 —— 此时绝不可触发方言切换
_MODEL_HINTS = ("model",)
_ROUTE_HINTS = ("url", "route", "endpoint", "path")


def _is_endpoint_missing(e: Exception) -> bool:
    """上游是否表示"该端点不存在"（用于 auto 方言切换）。

    必须把"模型名不存在"排除在外：网关对未知模型常回 404 + `model not found`，
    若据此切换方言，就会多发一次注定失败的请求，并把"模型名写错了"这个
    真实原因盖成"方言不对"，让用户完全找不着北。
    """
    status = _status_of(e)
    if status not in (400, 404, 405):
        return False
    text = _raw_message(e)

    # 提到模型、又没提任何"路由/端点"字样 → 是模型名问题，不是端点问题
    if any(m in text for m in _MODEL_HINTS) and not any(r in text for r in _ROUTE_HINTS):
        return False

    if status == 405:
        return True
    if status == 404 and not text.strip():
        return True          # 空 body 的 404 基本就是路径不对
    return any(h in text for h in _ENDPOINT_HINTS)


def _unsupported_param(e: Exception) -> str | None:
    """从错误中定位"哪个可选参数不被支持"；定位不到返回 None。

    只认 400/422（参数类错误）。要求同时出现**参数名**与否定语义词，
    避免把"温度超范围"这类可修正错误误判成"不支持该参数"而白丢参数。
    """
    status = _status_of(e)
    if status not in (400, 422):
        return None
    text = _raw_message(e)
    negative = ("unsupported", "not support", "unknown", "unrecognized",
                "invalid", "unexpected", "extra fields", "not allowed", "no such")
    if not any(n in text for n in negative):
        return None
    for param, keys in _DROPPABLE_PARAMS.items():
        if any(k in text for k in keys):
            return param
    return None


def _err_diagnostic(e: Exception, *, provider: Provider | None = None, dialect: str = "") -> str:
    """把 OpenAI 相关异常转成**可操作且不含上游原文**的中文诊断。

    安全约束：该文案会被编排层放进 `answer` 与 SSE `error` 事件直达前端，
    而上游异常原文可能带 Key 片段、组织 ID、内网地址或请求头。
    因此这里只保留"状态码级别"的提示，完整原文只写服务端日志。
    """
    _log.warning("LLM 调用失败（详情仅记日志）: %s: %s", type(e).__name__, e)
    status = _status_of(e)
    where = ""
    if provider is not None:
        where = f"供应商「{provider.label}」({provider.id}) 地址 {provider.base_url}"
        if dialect:
            where += f" 方言 {dialect}"
        where += "；"

    if status == 401:
        return f"LLM 鉴权失败(HTTP 401)：{where}API Key 无效或被拒绝 —— 请核对该供应商平台的 Key（详细原因见服务端日志）。"
    if status == 403:
        return f"LLM 拒绝访问(HTTP 403)：{where}Key 无权限或触发风控/地域限制（详细原因见服务端日志）。"
    if status in (400, 404, 405):
        return (
            f"LLM 请求被拒绝(HTTP {status})：{where}常见原因 —— 模型名不正确、"
            "base_url 少写或多写了 /v1、或该供应商不支持所选 API 方言"
            "（可用 API_DIALECT/供应商 api 字段显式指定，详细原因见服务端日志）。"
        )
    if status == 429:
        return f"LLM 限流或额度不足(HTTP 429)：{where}请稍后重试或更换供应商（详细原因见服务端日志）。"
    if status and status >= 500:
        return f"LLM 上游异常(HTTP {status})：{where}供应商侧故障，可稍后重试（详细原因见服务端日志）。"
    if status:
        return f"LLM 返回 HTTP {status}（{where}详细原因见服务端日志）。"
    return f"LLM 调用失败（{where}网络/连接或非 HTTP 错误，详细原因见服务端日志）。"


# ---------------------------------------------------------------- 客户端
def get_client(provider: Provider | None = None) -> AsyncOpenAI:
    """构造 AsyncOpenAI 实例（按供应商配置）。

    注意 `timeout`：推理模型首 token 可能很慢，默认 60s 见 LLM_TIMEOUT_S。
    """
    settings = get_settings()
    p = provider or current_provider()
    # 请求体带来的 base_url 是用户可控的出站目标 → 复用与 /api/providers/test 同一套 SSRF 守卫。
    # 放在这里而非各路由，是为了让**所有**出站 LLM 请求都必然经过它（含 /api/chat 那条路径）。
    try:
        from app.llm.providers import guard_request_base_url

        guard_request_base_url(p, settings)
    except ValueError as e:
        raise LLMUnavailable(str(e)) from e

    if not p.ready:
        if p.needs_key and not p.api_key:
            raise LLMUnavailable(
                f"供应商「{p.label}」未配置 API Key：请在设置页填写，或改用 LLM_API_KEY / "
                "LLM_PROVIDERS 配置（OpenAI 兼容接口）。"
            )
        raise LLMUnavailable(
            f"供应商「{p.label}」未指定模型名：请填写 model（如 deepseek-chat / glm-4-plus / qwen-plus）。"
        )
    kwargs: dict[str, Any] = {
        "api_key": p.sdk_api_key,
        "base_url": p.base_url,
        "timeout": settings.llm_timeout_s,
    }
    if p.extra_headers:
        kwargs["default_headers"] = p.extra_headers
    return AsyncOpenAI(**kwargs)


# ---------------------------------------------------------------- 消息与响应
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


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """拆出 system 与其余消息。

    Responses API 的 `input` **不接受 system 角色**（系统提示走顶层 `instructions`），
    直接把 messages 原样塞进去会被 400 拒绝。
    """
    system = None
    rest: list[dict] = []
    for m in messages:
        if m.get("role") == "system" and system is None:
            system = m.get("content") or None
        else:
            rest.append(m)
    return system, rest


def _usage_tuple(usage, dialect: str) -> tuple[int, int, int]:
    """把两种方言的 usage 统一成 (prompt, completion, total)。

    Chat Completions: prompt_tokens / completion_tokens / total_tokens
    Responses:        input_tokens  / output_tokens     / total_tokens
    """
    if usage is None:
        return (0, 0, 0)
    if dialect == "responses":
        p = getattr(usage, "input_tokens", None)
        c = getattr(usage, "output_tokens", None)
    else:
        p = getattr(usage, "prompt_tokens", None)
        c = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    p, c = int(p or 0), int(c or 0)
    total = int(total or 0) or (p + c)
    return (p, c, total)


def _record_usage(resp, latency_ms: float, dialect: str) -> None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    p, c, t = _usage_tuple(usage, dialect)
    if not (p or c or t):
        return
    r = _metrics_ref()
    r["prompt_tokens"] += p
    r["completion_tokens"] += c
    r["total_tokens"] += t
    r["latency_ms"] += latency_ms


def _reasoning_of_response(resp) -> str:
    """从 Responses API 的 Response 里抽推理摘要（结构比 chat 深一层）。"""
    chunks: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "reasoning":
            continue
        for s in getattr(item, "summary", None) or []:
            text = getattr(s, "text", None)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _text_of_response(resp) -> str:
    """取 Responses 的正文；SDK 无 output_text 属性时手工聚合。"""
    text = getattr(resp, "output_text", None)
    if text:
        return text
    chunks: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "output_text" and getattr(c, "text", None):
                chunks.append(c.text)
    return "".join(chunks)


# ---------------------------------------------------------------- 请求参数
def _kwargs_for(
    dialect: str,
    provider: Provider,
    *,
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
    no_think: bool,
    dropped: set[str],
) -> dict[str, Any]:
    """按方言组装请求参数；`dropped` 里的键不出现（参数降级阶梯的结果）。"""
    extra_body = dict(provider.extra_body)
    if no_think and "enable_thinking" not in dropped:
        extra_body["enable_thinking"] = False
    if "enable_thinking" in dropped:
        extra_body.pop("enable_thinking", None)
    if "extra_body" in dropped:
        extra_body = {}

    kw: dict[str, Any] = {"model": model}
    if dialect == "chat_completions":
        kw["messages"] = messages
        if temperature is not None and "temperature" not in dropped:
            kw["temperature"] = temperature
        if max_tokens and "max_tokens" not in dropped:
            kw["max_tokens"] = max_tokens
        if json_mode and "response_format" not in dropped:
            kw["response_format"] = {"type": "json_object"}
    else:
        system, rest = _split_system(messages)
        if system:
            kw["instructions"] = system
        kw["input"] = rest
        if temperature is not None and "temperature" not in dropped:
            kw["temperature"] = temperature
        if max_tokens and "max_tokens" not in dropped:
            kw["max_output_tokens"] = max_tokens
        if json_mode and "response_format" not in dropped:
            kw["text"] = {"format": {"type": "json_object"}}
    if extra_body:
        kw["extra_body"] = extra_body
    if provider.extra_headers:
        kw["extra_headers"] = provider.extra_headers
    return kw


def _initial_dialect(provider: Provider) -> str:
    if provider.api != "auto":
        return provider.api
    return _DIALECT_CACHE.get(provider.cache_key(), "chat_completions")


def _alternate(dialect: str) -> str:
    return "responses" if dialect == "chat_completions" else "chat_completions"


class _Ladder:
    """参数降级阶梯的状态机（非流式与流式共用同一套判定）。"""

    def __init__(self, provider: Provider):
        self.provider = provider
        self.dialect = _initial_dialect(provider)
        self.dropped: set[str] = set()
        self.attempts = 0

    @property
    def exhausted(self) -> bool:
        return self.attempts >= _MAX_ATTEMPTS

    def advance(self, e: Exception) -> str | None:
        """根据失败原因决定下一步：返回新方言 / 空串表示"丢参数后重试" / None 表示放弃。"""
        if self.exhausted:
            return None
        # 1) auto 模式下的方言切换（只切一次，且只在端点确实不存在时）
        if (
            self.provider.api == "auto"
            and "dialect" not in self.dropped
            and _is_endpoint_missing(e)
        ):
            self.dropped.add("dialect")
            self.dialect = _alternate(self.dialect)
            _DIALECT_CACHE[self.provider.cache_key()] = self.dialect
            _log.info(
                "供应商 %s 的 chat_completions 端点不存在，改用 %s 方言并缓存",
                self.provider.id, self.dialect,
            )
            return ""
        # 2) 可选参数不被支持 → 丢掉它重试
        param = _unsupported_param(e)
        if param and param not in self.dropped:
            self.dropped.add(param)
            _log.info("供应商 %s 不支持参数 %s，已丢弃后重试", self.provider.id, param)
            return ""
        return None


async def _create_once(client: AsyncOpenAI, ladder: _Ladder, kw: dict[str, Any]):
    """发一次非流式请求。"""
    if ladder.dialect == "chat_completions":
        return await client.chat.completions.create(**kw)
    return await client.responses.create(**kw)


async def _create_stream(client: AsyncOpenAI, ladder: _Ladder, kw: dict[str, Any]):
    """开一次流式请求（返回上游流对象）。"""
    if ladder.dialect == "chat_completions":
        return await client.chat.completions.create(stream=True, **kw)
    return await client.responses.create(stream=True, **kw)


def _map_chat_chunk(chunk) -> list[tuple[str, str]]:
    """Chat Completions 的一个 chunk → [(kind, text)]。"""
    out: list[tuple[str, str]] = []
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return out
    delta = choices[0].delta
    rt = getattr(delta, "reasoning_content", None)
    if rt:
        out.append(("think", rt))
    ct = getattr(delta, "content", None)
    if ct:
        out.append(("text", ct))
    return out


def _map_responses_event(ev) -> tuple[list[tuple[str, str]], str | None]:
    """Responses 的一个流事件 → ([(kind, text)], 新方言提示或 None)。

    事件类型（实测 openai SDK 3.x）：
      response.output_text.delta               → 正文增量（.delta）
      response.reasoning_summary_text.delta    → 推理摘要增量（.delta）
      response.reasoning_text.delta            → 部分实现用这个名
      response.completed                       → 收尾（usage 在 .response.usage）
      response.failed / response.error         → 失败
    """
    etype = getattr(ev, "type", "") or ""
    delta = getattr(ev, "delta", None)
    if etype == "response.output_text.delta" and delta:
        return [("text", delta)], None
    if etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta") and delta:
        return [("think", delta)], None
    if etype in ("response.failed", "response.error"):
        err = getattr(ev, "error", None) or getattr(getattr(ev, "response", None), "error", None)
        msg = getattr(err, "message", None) or str(err or "上游返回失败事件")
        raise LLMUnavailable(f"LLM 上游返回失败事件：{msg[:200]}")
    return [], None


def _stream_usage(chunk, dialect: str):
    """从流式 chunk/事件上取 usage（两种方言位置不同）。"""
    if dialect == "responses":
        if (getattr(chunk, "type", "") or "") == "response.completed":
            return getattr(getattr(chunk, "response", None), "usage", None)
        return None
    return getattr(chunk, "usage", None)


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


# ---------------------------------------------------------------- 对外 API
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

    think 取自模型的推理字段（reasoning_content/reasoning 或 Responses 的推理摘要），
    在 Mock 模式下返回占位思考。若模型不返回推理字段，则 thinking 为空串。
    history 为多轮上下文（不含本次 prompt）。
    """
    settings = get_settings()
    if settings.llm_mock:
        from app.llm import _mock

        return (await _mock.mock_chat(prompt, history=history), "(Mock 思考：基于关键词/正则的确定性分析过程。)")

    provider = current_provider()
    client = get_client(provider)
    messages = build_messages(prompt, system, history)
    ladder = _Ladder(provider)

    while True:
        ladder.attempts += 1
        kw = _kwargs_for(
            ladder.dialect, provider, messages=messages, model=model or provider.model,
            temperature=temperature, max_tokens=max_tokens, json_mode=False,
            no_think=False, dropped=ladder.dropped,
        )
        _t0 = time.perf_counter()
        try:
            resp = await _create_once(client, ladder, kw)
        except Exception as e:  # noqa: BLE001 —— 向上抛统一异常以便编排层降级
            decision = ladder.advance(e)
            if decision is not None:
                continue
            raise LLMUnavailable(_err_diagnostic(e, provider=provider, dialect=ladder.dialect)) from e

        _record_usage(resp, (time.perf_counter() - _t0) * 1000.0, ladder.dialect)
        if ladder.dialect == "chat_completions":
            msg = resp.choices[0].message
            return (msg.content or "", _reasoning_of(msg))
        return (_text_of_response(resp), _reasoning_of_response(resp))


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

    provider = current_provider()
    client = get_client(provider)
    messages = build_messages(prompt, system, history)
    ladder = _Ladder(provider)

    # 流式请求的失败常在**首个事件**才暴露（SDK 先返回流对象，连接错误延后抛出），
    # 因此打开流之后要先"预取一个事件"才能判断方言/参数是否需要降级。
    stream = None
    first = None
    while True:
        ladder.attempts += 1
        kw = _kwargs_for(
            ladder.dialect, provider, messages=messages, model=model or provider.model,
            temperature=temperature, max_tokens=max_tokens, json_mode=False,
            no_think=False, dropped=ladder.dropped,
        )
        t0 = time.perf_counter()
        try:
            stream = await _create_stream(client, ladder, kw)
            it = stream.__aiter__()
            try:
                first = await it.__anext__()
            except StopAsyncIteration:
                first = None
            break
        except Exception as e:  # noqa: BLE001
            if stream is not None:
                await _aclose_stream(stream)
                stream = None
            decision = ladder.advance(e)
            if decision is not None:
                continue
            raise LLMUnavailable(_err_diagnostic(e, provider=provider, dialect=ladder.dialect)) from e

    # 流式 usage 是"到当前为止的累计值"：逐 chunk 直接累加会重复膨胀，
    # 因此按增量计入（当前累计 - 上次累计），延迟只计一次。
    prev = {"prompt": 0, "completion": 0, "total": 0}
    _latency_recorded = False
    metrics = _metrics_ref()  # 当前上下文的计费 dict（惰性创建，勿用模块级共享对象）
    try:
        pending = first
        while True:
            if pending is None:
                try:
                    chunk = await it.__anext__()
                except StopAsyncIteration:
                    break
            else:
                chunk = pending
                pending = None

            usage = _stream_usage(chunk, ladder.dialect)
            if usage is not None:
                p, c, t = _usage_tuple(usage, ladder.dialect)
                if t or p or c:
                    metrics["prompt_tokens"] += max(p - prev["prompt"], 0)
                    metrics["completion_tokens"] += max(c - prev["completion"], 0)
                    metrics["total_tokens"] += max(t - prev["total"], 0)
                    prev.update(prompt=p, completion=c, total=t)
                    if not _latency_recorded:
                        metrics["latency_ms"] += (time.perf_counter() - t0) * 1000.0
                        _latency_recorded = True

            if ladder.dialect == "chat_completions":
                for item in _map_chat_chunk(chunk):
                    yield item
            else:
                for item in _map_responses_event(chunk)[0]:
                    yield item
    finally:
        # **必须**显式关闭上游流：用户中途"停止"或下游异常时，
        # 生成器被 aclose/GeneratorExit 中断，若不关闭则 httpx 连接与 socket 会一直挂着
        # （openai/httpx 客户端均无 __del__ 兜底，只能等 GC 且时机不确定）。
        if stream is not None:
            await _aclose_stream(stream)


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
    关掉后 3.3s → 1.1s）。**不被支持时自动丢弃该参数重试**（见 `_Ladder`），
    避免"优化变成故障"。

    两种方言下的结构约束方式不同：Chat Completions 用 `response_format`，
    Responses 用 `text.format`；都由 `_kwargs_for` 处理。

    用法：
        data = await chat_structured("……", schema={"$schema":"…", "type":"object", "properties":{...}})
    返回解析后的 dict（键名遵循 schema 的 properties）。
    history 为多轮上下文，用于消解指代（如"那明天呢"承接上一轮的车次）。
    """
    settings = get_settings()
    if settings.llm_mock:
        from app.llm import _mock

        return await _mock.mock_structured(prompt, schema)

    provider = current_provider()
    client = get_client(provider)
    _model = model or provider.effective_structured_model
    schema_json = json.dumps(schema, ensure_ascii=False)
    # 约定：结果必须是一个 JSON 对象，键名须取自 schema 的 properties。
    user_prompt = (
        f"{prompt}\n\n"
        "必须输出一个 JSON 对象，仅包含以下字段（若某字段用户未提及，取 null 或空串），"
        "不要输出任何额外文字或 markdown 代码块围栏：\n"
        f"{schema_json}"
    )
    messages = build_messages(user_prompt, system, history)
    ladder = _Ladder(provider)

    while True:
        ladder.attempts += 1
        kw = _kwargs_for(
            ladder.dialect, provider, messages=messages, model=_model,
            temperature=temperature, max_tokens=0, json_mode=True,
            no_think=no_think, dropped=ladder.dropped,
        )
        _t0 = time.perf_counter()
        try:
            resp = await _create_once(client, ladder, kw)
        except Exception as e:  # noqa: BLE001
            decision = ladder.advance(e)
            if decision is not None:
                continue
            raise LLMUnavailable(_err_diagnostic(e, provider=provider, dialect=ladder.dialect)) from e

        _record_usage(resp, (time.perf_counter() - _t0) * 1000.0, ladder.dialect)
        if ladder.dialect == "chat_completions":
            content = resp.choices[0].message.content or ""
        else:
            content = _text_of_response(resp)
        break

    obj = _parse_json_object(content)

    # 只保留 schema 声明过的键，避免噪声键混入
    props = set((schema.get("properties") or {}).keys())
    if props and isinstance(obj, dict):
        obj = {k: obj.get(k) for k in props}
    return obj


def _parse_json_object(content: str) -> dict:
    """把模型输出解析成 JSON 对象（容忍 markdown 围栏与前后噪声）。

    社区版要面对几十家网关，"严格只输出 JSON"并不总能被遵守：
    实测常见 ` ```json ... ``` ` 围栏、句子前后带解释。宽松解析能省掉一整轮失败重试。

    结构化层只接受 JSON 对象：数组/字符串/数字/null 都会让下游 AttributeError 逃出
    LLMUnavailable 契约（曾经导致意图/抽取不再降级、整个请求报错）。
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 退一步：截取第一个 { 到最后一个 } 之间的内容
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMUnavailable("LLM 返回非 JSON（无法解析结构化输出）")
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMUnavailable(f"LLM 返回非 JSON（无法解析结构化输出）: {e}") from e

    if not isinstance(obj, dict):
        raise LLMUnavailable(
            f"LLM 返回的 JSON 不是对象（实际为 {type(obj).__name__}），无法用于结构化解析"
        )
    return obj


# ---------------------------------------------------------------- 连通性探测
async def probe_provider(provider: Provider | None = None, *, with_models: bool = True) -> dict:
    """用一个**极小**的真实请求探测供应商是否可用（供「测试连接」按钮使用）。

    为什么不做成纯 HTTP 探活：能连通不等于能用 —— 用户真正会踩的是
    模型名错误、Key 无权、方言不对。这些都只有在真发一次请求时才会暴露。
    代价是一次 max_tokens=8 的调用（可忽略）。

    返回 dict（**绝不包含 Key 或上游原文**）：
        {ok, provider, base_url, dialect, model, latency_ms, error?, models?}
    """
    p = provider or current_provider()
    out: dict[str, Any] = {
        "ok": False,
        "provider": p.id,
        "label": p.label,
        "base_url": p.base_url,
        "api": p.api,
        "model": p.model,
        "key_required": p.needs_key,
    }
    try:
        client = get_client(p)
    except LLMUnavailable as e:
        out["error"] = str(e)
        return out

    ladder = _Ladder(p)
    messages = [{"role": "user", "content": "ping"}]
    while True:
        ladder.attempts += 1
        kw = _kwargs_for(
            ladder.dialect, p, messages=messages, model=p.model, temperature=0.0,
            max_tokens=8, json_mode=False, no_think=False, dropped=ladder.dropped,
        )
        t0 = time.perf_counter()
        try:
            await _create_once(client, ladder, kw)
        except Exception as e:  # noqa: BLE001 —— 探测失败是预期路径，转成结构化结果
            decision = ladder.advance(e)
            if decision is not None:
                continue
            out["error"] = _err_diagnostic(e, provider=p, dialect=ladder.dialect)
            out["dialect"] = ladder.dialect
            return out
        out["ok"] = True
        out["dialect"] = ladder.dialect
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        break

    if with_models:
        # 尽力而为：不少网关实现了 GET /models，能顺手帮用户挑模型名；
        # 没实现也不影响"可用"这个结论。
        try:
            resp = await client.models.list()
            ids = [getattr(m, "id", None) for m in (getattr(resp, "data", None) or [])]
            out["models"] = sorted({i for i in ids if i})[:200]
        except Exception as e:  # noqa: BLE001
            _log.debug("列举模型失败（不影响可用性结论）：%s", type(e).__name__)
    return out
