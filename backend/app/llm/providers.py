"""LLM 供应商注册表：多供应商 + 双 API 方言（chat.completions / responses）。

为什么需要这一层（社区版"用户自备 Key"的直接后果）：

1. **base_url 形态各家不一致**：有的必须带 `/v1`，有的走 `/api/paas/v4`（智谱）或
   `/compatible-mode/v1`（百炼）；用户还经常把**完整 endpoint URL** 直接粘进来
   （`https://api.foo.com/v1/chat/completions`）——直接塞给 SDK 会 404。
   统一在 `normalize_base_url()` 里收敛。

2. **方言不止一种**：`/v1/chat/completions`（Chat Completions）与 `/v1/responses`
   （Responses）互不兼容，部分供应商/网关只实现其中一种。`api="auto"` 时先按
   Chat Completions 发，命中 404/405 再切 Responses，并把结论**按供应商缓存**。

3. **有的服务根本不校验 Key**（Ollama / LM Studio / vLLM）：此时要求填 Key 就把
   用户挡在门外。`key_required=False` 时用一个占位串满足 SDK 的非空校验。

供应商来源（后者覆盖前者）：
    内置目录 → `LLM_PROVIDERS_FILE` 指向的 JSON → `LLM_PROVIDERS` 环境变量 JSON
    → 由 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` 合成的 `default` 供应商

**本模块不发起任何网络请求**，只做纯数据归一化，便于离线单测。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

_log = logging.getLogger("railfan.llm.providers")

# API 方言：auto = 先试 chat_completions，404/405 再退 responses
ApiDialect = Literal["auto", "chat_completions", "responses"]
DIALECTS: tuple[str, ...] = ("auto", "chat_completions", "responses")

# SDK 要求 api_key 非空；本地推理服务不需要真 Key，用这个占位串满足校验
KEYLESS_PLACEHOLDER = "not-required"

# 本地/内网地址：默认视为"不需要 Key"（Ollama、LM Studio、公司内网网关）
_LOCAL_HOST_RE = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1|host\.docker\.internal"
    r"|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|.*\.local)$",
    re.IGNORECASE,
)

# 用户常把完整 endpoint 粘进来 → 归一化时剥掉（**长后缀先匹配**，否则会被 /completions 误剥）
_ENDPOINT_SUFFIXES = ("/chat/completions", "/responses", "/completions")


# ---------------------------------------------------------------- 内置目录
# 只放"地址与默认模型"这类公开信息，**不含任何 Key**。
# 注意：模型名会随供应商迭代漂移，这里给的是相对稳定的默认值；
# model 为空串表示"该供应商模型名需用户自填"（多为按账号/组织命名，
# 例如 SiliconFlow 的 `deepseek-ai/DeepSeek-V3`），避免写死一个错的名字把人误导。
BUILTIN_PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "官方直连；reasoning 模型请改用 deepseek-reasoner",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "官方直连（国内网络通常需要代理）",
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "",
        "note": "模型名按组织命名（如 deepseek-ai/DeepSeek-V3），需自填",
    },
    "dashscope": {
        "label": "阿里云百炼（通义千问）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "走 OpenAI 兼容模式端点",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "note": "注意地址是 /api/paas/v4，不是 /v1",
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    "openrouter": {
        "label": "OpenRouter（聚合网关）",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "note": "模型名形如 vendor/model，需自填",
    },
    "gemini": {
        "label": "Google Gemini（OpenAI 兼容层）",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "note": "v1beta/openai 兼容层；国内网络通常需要代理",
    },
    "ollama": {
        "label": "Ollama（本地）",
        "base_url": "http://localhost:11434/v1",
        "model": "",
        "key_required": False,
        "note": "本地推理，无需 Key；模型名填 ollama list 里的名字",
    },
    "lmstudio": {
        "label": "LM Studio（本地）",
        "base_url": "http://localhost:1234/v1",
        "model": "",
        "key_required": False,
        "note": "本地推理，无需 Key",
    },
    "vllm": {
        "label": "vLLM（本地/自建）",
        "base_url": "http://localhost:8000/v1",
        "model": "",
        "key_required": False,
        "note": "自建推理服务；若与本项目后端同端口，请改端口",
    },
}


# ---------------------------------------------------------------- 归一化
def clean_api_key(raw: Any) -> str:
    """清洗用户粘贴的 Key：去空白、去引号、去误带的 `Bearer ` 前缀。

    实测常见输入：`"sk-xxx"`（带引号）、`sk-xxx `（尾随空格）、
    `Bearer sk-xxx`（从 curl 示例整行拷来）。以上都会让上游返回 401，
    但对用户来说"我明明填对了"，故在此静默归一化。
    """
    if raw is None:
        return ""
    text = str(raw).strip().strip('"').strip("'").strip()
    if text[:7].lower() == "bearer ":
        text = text[7:].strip()
    return text


def normalize_base_url(raw: Any) -> str:
    """把各种写法的 base_url 归一到 SDK 可用的形态。

    处理（按顺序）：
    1. 去空白、去成对引号、去末尾斜杠；
    2. 剥掉误粘的完整 endpoint 后缀（`/chat/completions`、`/responses`、`/completions`）；
    3. **仅当**路径为空（裸域名，如 `https://api.foo.com`）时补 `/v1`。
       已有路径的一律不动 —— 否则会把智谱的 `/api/paas/v4`、
       Gemini 兼容层的 `/v1beta/openai` 改坏。

    非法（无 scheme / 无主机）时抛 ValueError，由调用方转成可读提示。
    """
    text = str(raw or "").strip().strip('"').strip("'").strip()
    if not text:
        raise ValueError("base_url 不能为空")
    text = text.rstrip("/")

    # 反复剥离，覆盖 `/v1/chat/completions` 这类叠加写法
    changed = True
    while changed:
        changed = False
        for suffix in _ENDPOINT_SUFFIXES:
            if text.lower().endswith(suffix):
                text = text[: -len(suffix)].rstrip("/")
                changed = True
                break

    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"base_url 必须是 http/https 地址：{text}")
    if not parsed.hostname:
        raise ValueError(f"base_url 缺少主机名：{text}")

    if parsed.path in ("", "/"):
        text = f"{text}/v1"
    return text


def is_local_host(url: str) -> bool:
    """判断地址是否指向本机/内网（用于推断"无需 Key"）。"""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return bool(_LOCAL_HOST_RE.match(host))


def _as_dict(value: Any) -> dict[str, Any]:
    """把 extra_headers/extra_body 这类字段从 JSON 字符串或 dict 归一为 dict。"""
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"extra 字段不是合法 JSON：{e}") from e
        if not isinstance(obj, dict):
            raise ValueError("extra 字段必须是 JSON 对象")
        return {str(k): v for k, v in obj.items()}
    raise ValueError(f"extra 字段类型不支持：{type(value).__name__}")


# ---------------------------------------------------------------- 供应商
@dataclass(frozen=True)
class Provider:
    """一个可用的 LLM 供应商配置（已归一化）。"""

    id: str
    label: str
    base_url: str
    api_key: str = ""
    api: ApiDialect = "auto"
    model: str = ""
    structured_model: str = ""
    extra_headers: dict[str, Any] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    key_required: bool | None = None      # None = 按地址自动推断
    source: str = "builtin"               # builtin | file | env | legacy | request
    note: str = ""

    @property
    def needs_key(self) -> bool:
        """是否必须有 Key：显式声明优先，否则按"是否本地地址"推断。"""
        if self.key_required is not None:
            return bool(self.key_required)
        return not is_local_host(self.base_url)

    @property
    def ready(self) -> bool:
        """该供应商当前是否可用（Key 齐 + 模型名齐）。

        与 `Settings.llm_ready` 的语义保持一致：占位 Key 不算已配置。
        """
        if self.needs_key and not self.api_key:
            return False
        return bool(self.model)

    @property
    def sdk_api_key(self) -> str:
        """交给 SDK 的 Key。无 Key 服务用占位串满足 SDK 的非空校验。"""
        return self.api_key or KEYLESS_PLACEHOLDER

    @property
    def effective_structured_model(self) -> str:
        return self.structured_model or self.model

    def cache_key(self) -> str:
        """方言探测缓存的键：同地址同供应商才算同一目标。"""
        return f"{self.id}|{self.base_url}"

    def public(self) -> dict[str, Any]:
        """给前端的视图：**绝不包含 Key 本身**，只暴露是否已配置。"""
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "api": self.api,
            "model": self.model,
            "structured_model": self.structured_model,
            "has_key": bool(self.api_key),
            "key_required": self.needs_key,
            "ready": self.ready,
            "source": self.source,
            "note": self.note,
        }


def provider_from_dict(pid: str, data: dict[str, Any], *, source: str) -> Provider:
    """从原始 dict 构造 Provider（做全部归一化与字段校验）。"""
    if not isinstance(data, dict):
        raise ValueError(f"供应商 {pid} 的配置必须是对象")

    base_url_raw = data.get("base_url") or data.get("baseUrl") or ""
    base_url = normalize_base_url(base_url_raw)

    api = str(data.get("api") or data.get("dialect") or "auto").strip().lower()
    if api not in DIALECTS:
        raise ValueError(f"供应商 {pid} 的 api 非法：{api}（可选 {'/'.join(DIALECTS)}）")

    key_required = data.get("key_required", data.get("keyRequired", None))
    if key_required is not None:
        key_required = bool(key_required)

    return Provider(
        id=pid,
        label=str(data.get("label") or pid),
        base_url=base_url,
        api_key=clean_api_key(data.get("api_key") or data.get("apiKey")),
        api=api,  # type: ignore[arg-type]
        model=str(data.get("model") or "").strip(),
        structured_model=str(data.get("structured_model") or data.get("structuredModel") or "").strip(),
        extra_headers=_as_dict(data.get("extra_headers") or data.get("extraHeaders")),
        extra_body=_as_dict(data.get("extra_body") or data.get("extraBody")),
        key_required=key_required,
        source=source,
        note=str(data.get("note") or ""),
    )


def _canonical_keys(data: dict[str, Any]) -> dict[str, Any]:
    """把 camelCase / 别名键统一成 snake_case，便于与已有条目做字段级合并。"""
    alias = {
        "baseUrl": "base_url", "apiKey": "api_key", "structuredModel": "structured_model",
        "extraHeaders": "extra_headers", "extraBody": "extra_body",
        "keyRequired": "key_required", "dialect": "api",
    }
    return {alias.get(k, k): v for k, v in data.items() if k != "id"}


def _provider_to_dict(p: Provider) -> dict[str, Any]:
    """Provider → 原始 dict（用于与覆盖项做字段级合并）。"""
    return {
        "label": p.label, "base_url": p.base_url, "api_key": p.api_key, "api": p.api,
        "model": p.model, "structured_model": p.structured_model,
        "extra_headers": p.extra_headers, "extra_body": p.extra_body,
        "key_required": p.key_required, "note": p.note,
    }


def _iter_provider_items(raw: str, *, origin: str) -> list[tuple[str, dict[str, Any]]]:
    """解析供应商 JSON，返回 [(id, 原始配置)]，**不做构造**（构造要等合并之后）。

    接受 `{id: {...}}` 对象、`[{id:..., ...}]` 数组，以及 `{"providers": ...}` 包裹写法。
    """
    text = (raw or "").strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{origin} 不是合法 JSON：{e}") from e

    if isinstance(obj, dict):
        # 兼容 {"providers": [...]} 包裹写法
        if "providers" in obj and isinstance(obj["providers"], (list, dict)):
            return _iter_provider_items(
                json.dumps(obj["providers"], ensure_ascii=False), origin=origin
            )
        return [(str(k), v) for k, v in obj.items()]

    if isinstance(obj, list):
        items: list[tuple[str, dict[str, Any]]] = []
        for i, item in enumerate(obj):
            if not isinstance(item, dict):
                raise ValueError(f"{origin} 第 {i} 项必须是对象")
            pid = str(item.get("id") or "").strip()
            if not pid:
                raise ValueError(f"{origin} 第 {i} 项缺少 id")
            items.append((pid, item))
        return items

    raise ValueError(f"{origin} 必须是 JSON 对象或数组")


def _apply_items(
    providers: dict[str, Provider],
    items: list[tuple[str, dict[str, Any]]],
    *,
    tag: str,
    origin: str,
) -> None:
    """把覆盖项合并进注册表（就地修改）。

    **字段级合并**（而非整体替换）是刻意的：用户想给内置供应商换个模型时，
    最自然的写法就是 `{"deepseek": {"model": "deepseek-reasoner"}}` —— 只写要改的字段。
    若按"新条目必须给全 base_url"处理，这种写法会直接报错，逼用户抄一遍内置地址。
    """
    for pid, data in items:
        if not isinstance(data, dict):
            raise ValueError(f"{origin} 中 {pid} 的配置必须是对象")
        overrides = _canonical_keys(data)
        if pid in providers:
            merged = _provider_to_dict(providers[pid])
            merged.update(overrides)
            providers[pid] = provider_from_dict(pid, merged, source=tag)
        else:
            if not overrides.get("base_url"):
                raise ValueError(
                    f"{origin} 中的供应商 {pid} 缺少 base_url"
                    "（只有内置 id 才能只写覆盖字段，如 {\"deepseek\": {\"model\": \"...\"}}）"
                )
            providers[pid] = provider_from_dict(pid, data, source=tag)


def _load_from_file(path: str) -> list[tuple[str, dict[str, Any]]]:
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.is_file():
        # 配了路径但文件不存在：不致命（用户可能后面才创建），但要吵一声
        _log.warning("LLM_PROVIDERS_FILE 指向的文件不存在：%s", p)
        return []
    return _iter_provider_items(
        p.read_text(encoding="utf-8"), origin=f"供应商文件 {p}"
    )


def _legacy_provider(settings) -> Provider | None:
    """由老的 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 三件套合成 `default` 供应商。

    这是**向后兼容的关键**：老用户的 .env 不改也能继续跑。
    """
    base = (getattr(settings, "llm_base_url", "") or "").strip()
    key = clean_api_key(getattr(settings, "llm_api_key", ""))
    model = (getattr(settings, "llm_model", "") or "").strip()
    if not base and not key:
        return None
    try:
        p = provider_from_dict(
            "default",
            {
                "label": "默认（.env 配置）",
                "base_url": base or "https://api.deepseek.com/v1",
                "api_key": key,
                "api": getattr(settings, "llm_api_dialect", "auto") or "auto",
                "model": model,
                "structured_model": getattr(settings, "llm_structured_model", ""),
                "extra_headers": getattr(settings, "llm_extra_headers", ""),
                "extra_body": getattr(settings, "llm_extra_body", ""),
            },
            source="legacy",
        )
    except ValueError as e:
        _log.warning("LLM_* 环境变量无法构成默认供应商：%s", e)
        return None
    return p


def load_providers(settings=None) -> dict[str, Provider]:
    """装载全部供应商（不含请求级覆盖）。后者覆盖前者，见模块 docstring。"""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    providers: dict[str, Provider] = {}
    for pid, data in BUILTIN_PROVIDERS.items():
        try:
            providers[pid] = provider_from_dict(pid, data, source="builtin")
        except ValueError as e:  # 内置目录写错属于本仓库 bug，不该静默
            _log.error("内置供应商 %s 配置非法：%s", pid, e)

    _apply_items(providers, _load_from_file(settings.llm_providers_file),
                 tag="file", origin="LLM_PROVIDERS_FILE")
    _apply_items(providers, _iter_provider_items(settings.llm_providers, origin="LLM_PROVIDERS"),
                 tag="env", origin="LLM_PROVIDERS")

    legacy = _legacy_provider(settings)
    if legacy is not None:
        providers["default"] = legacy

    # 全局兜底 Key / 模型：让"选内置供应商 + 只填一个 LLM_API_KEY"也能跑通。
    #
    # **只兜底被选中的那一家**（LLM_PROVIDER 指定的），绝不无差别塞给全部内置供应商：
    # 否则界面会把 deepseek/moonshot/gemini… 全部标成"已配 Key"，
    # 而那个 Key 只属于用户真正在用的供应商，换一家就会 401 —— 属于谎报可用性。
    fallback_key = clean_api_key(settings.llm_api_key)
    fallback_model = (settings.llm_model or "").strip()
    fallback_structured = (settings.llm_structured_model or "").strip()
    explicit = (getattr(settings, "llm_provider", "") or "").strip()
    if explicit and explicit in providers and (fallback_key or fallback_model):
        p = providers[explicit]
        updates: dict[str, Any] = {}
        if not p.api_key and fallback_key and p.needs_key:
            updates["api_key"] = fallback_key
        if not p.model and fallback_model:
            updates["model"] = fallback_model
        if not p.structured_model and fallback_structured:
            updates["structured_model"] = fallback_structured
        if updates:
            providers[explicit] = replace(p, **updates)
    return providers


def default_provider_id(settings=None) -> str:
    """当前生效的供应商 id：显式 LLM_PROVIDER 优先，否则 legacy `default`。"""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    explicit = (getattr(settings, "llm_provider", "") or "").strip()
    if explicit:
        return explicit
    return "default"


def resolve_provider(
    provider_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    *,
    settings=None,
) -> Provider:
    """解析出最终生效的 Provider。

    优先级：请求级 overrides（BYOK）> provider_id 参数 > LLM_PROVIDER > `default`。

    overrides 支持的键：api_key / base_url / model / api / structured_model。
    传了 base_url 就视为**临时自定义供应商**（id 记作 `request`，不落任何配置）。
    """
    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    providers = load_providers(settings)
    wanted = (provider_id or "").strip() or default_provider_id(settings)
    base = providers.get(wanted)

    if base is None:
        available = ", ".join(sorted(providers)) or "(无)"
        raise ValueError(f"未知供应商：{wanted}（可用：{available}）")

    ov = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    if not ov:
        return base

    data: dict[str, Any] = {
        "label": base.label,
        "base_url": ov.get("base_url") or base.base_url,
        "api_key": ov.get("api_key") or base.api_key,
        "api": ov.get("api") or base.api,
        "model": ov.get("model") or base.model,
        "structured_model": ov.get("structured_model") or base.structured_model,
        "extra_headers": base.extra_headers,
        "extra_body": base.extra_body,
        "key_required": base.key_required,
        "note": base.note,
    }
    resolved = provider_from_dict(
        "request" if ov.get("base_url") else base.id,
        data,
        source="request" if ov.get("base_url") else base.source,
    )
    if ov.get("base_url"):
        # 只给了地址的临时供应商：沿用原供应商的 label 会误导（诊断里看着像在用 .env 那家），
        # 直接标出目标主机，报错时一眼能看出请求发到了哪里。
        host = urlparse(resolved.base_url).hostname or resolved.base_url
        resolved = replace(resolved, label=f"自定义供应商（{host}）")
    return resolved


def list_public(settings=None) -> list[dict[str, Any]]:
    """全部供应商的前端视图（已脱敏），按 id 排序。"""
    providers = load_providers(settings)
    return [providers[pid].public() for pid in sorted(providers)]


def guard_request_base_url(provider: Provider, settings=None) -> None:
    """对**请求级**供应商地址做 SSRF 校验。

    为什么只查 `source == "request"`：管理员通过 .env / 配置文件写的地址是
    **可信配置**（公司内网网关、本机 Ollama 都要指向私网）；而请求体里的
    base_url 是**用户可控的出站目标**，正是 SSRF 的经典入口
    （`http://169.254.169.254/...` 取云元数据、扫内网端口）。
    `/api/providers/test` 与 `/api/chat` 都必须走这里，否则就漏了一条路径。

    非生产环境放行（本地开发常用 127.0.0.1 的 Ollama/LM Studio）；
    生产环境除非显式开 `LLM_ALLOW_PRIVATE_BASE_URL`，一律只允许公网地址。
    """
    if provider.source != "request":
        return
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if settings.llm_allow_private_base_url or not settings.is_production:
        return

    from app.tools._http import UnsafeUrlError, assert_public_url

    try:
        assert_public_url(provider.base_url)
    except UnsafeUrlError as e:
        raise ValueError(
            f"拒绝该 base_url：{e}。如确需指向内网/本机，请显式设置 LLM_ALLOW_PRIVATE_BASE_URL=true。"
        ) from e
