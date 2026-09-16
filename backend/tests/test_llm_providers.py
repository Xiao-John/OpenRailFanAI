"""LLM 多供应商 / 双 API 方言回归测试（**无网络**，全程用假客户端）。

对应需求：提高 API Key 兼容性 —— 用户自备 Key、可自行添加供应商；
只要是 OpenAI 兼容接口，**无论 chat.completions 还是 responses 都要能用**。

覆盖：
1. base_url / Key 的各种"人肉写法"归一化（最常见的 404/401 来源）
2. 内置目录 + 自定义供应商（env JSON / 文件 / 包裹写法）+ 旧版三件套向后兼容
3. 脱敏：任何给前端的视图都不得含 Key
4. 免 Key 本地服务（Ollama/LM Studio）
5. 请求级覆盖（BYOK）优先级
6. 双方言 usage 字段映射、responses 的 system→instructions 拆分
7. 错误分类 → 自动切方言（并缓存）→ 参数降级重试
8. 诊断文案不泄露上游原文/Key
9. mock 模式不依赖任何供应商配置

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_llm_providers.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
from pathlib import Path

from app.config import Settings
from app.llm import client as llm
from app.llm import providers as pv


# ---------------------------------------------------------------- 假客户端
class FakeStatusError(Exception):
    """模拟 openai SDK 的 APIStatusError（带 status_code 与 body.error.message）。"""

    def __init__(self, status: int, message: str):
        self.status_code = status
        self.body = {"error": {"message": message}}
        super().__init__(message)


class _Usage:
    def __init__(self, prompt: int, completion: int, total: int | None = None,
                 input_tokens: int | None = None, output_tokens: int | None = None):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total if total is not None else prompt + completion
        # Responses 方言用 input/output_tokens
        self.input_tokens = input_tokens if input_tokens is not None else prompt
        self.output_tokens = output_tokens if output_tokens is not None else completion


class _Msg:
    def __init__(self, content: str, reasoning: str | None = None):
        self.content = content
        self.reasoning_content = reasoning
        self.model_extra = {}


class _Choice:
    def __init__(self, msg, finish_reason: str = "stop"):
        self.message = msg
        self.finish_reason = finish_reason


class _ChatResp:
    def __init__(self, content: str, reasoning: str | None = None, usage=None,
                 finish_reason: str = "stop"):
        self.choices = [_Choice(_Msg(content, reasoning), finish_reason)]
        self.usage = usage


class _RespResp:
    """Responses API 的非流式响应。"""

    def __init__(self, text: str, reasoning_summary: str | None = None, usage=None):
        self.output_text = text
        self.usage = usage
        out = []
        if reasoning_summary:
            out.append(type("Item", (), {"type": "reasoning", "summary": [
                type("S", (), {"text": reasoning_summary})()]})())
        out.append(type("Item", (), {"type": "message", "content": [
            type("C", (), {"type": "output_text", "text": text})()]})())
        self.output = out


class _StreamEvent:
    def __init__(self, type_: str, delta: str | None = None, response=None):
        self.type = type_
        self.delta = delta
        self.response = response


class _StreamEvent2:
    """带 finish_reason 的 chat 流式 chunk（用于验证截断识别）。"""

    def __init__(self, content: str | None = None, finish_reason: str | None = None):
        delta = type("D", (), {"content": content, "reasoning_content": None})()
        self.choices = [type("C", (), {"delta": delta, "finish_reason": finish_reason})()]
        self.usage = None


class _FakeStream:
    """异步可迭代的假流（含 aclose）。

    `fail_with` 用于模拟"端点不存在但错误在首个流事件才浮现"：
    反向代理常把 404 的 JSON/HTML 当作 SSE 流返回，SDK 先给出流对象、
    解析首个事件时才抛错 —— 这正是流式路径必须也能切方言的原因。
    """

    def __init__(self, items, fail_with: Exception | None = None):
        self._items = list(items)
        self._i = 0
        self._fail_with = fail_with
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._fail_with is not None and self._i == 0:
            raise self._fail_with
        if self._i >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._i]
        self._i += 1
        return item

    async def aclose(self):
        self.closed = True


class _FakeClient:
    """同时具备 chat.completions 与 responses 的假 AsyncOpenAI。"""

    def __init__(self, chat=None, resp=None, chat_stream=None, resp_stream=None):
        self._chat = chat
        self._resp = resp
        self._chat_stream = chat_stream
        self._resp_stream = resp_stream
        self.calls: list[tuple[str, dict]] = []
        self.chat = type("Chat", (), {"completions": self})()
        self.responses = self
        self.models = type("Models", (), {"list": self._list_models})()

    async def _list_models(self):
        return type("ML", (), {"data": [type("M", (), {"id": "m-a"})(), type("M", (), {"id": "m-b"})()]})()

    async def _maybe(self, fn, **kw):
        """兼容同步 lambda 与 async def 两种桩写法。"""
        out = fn(**kw)
        if inspect.isawaitable(out):
            out = await out
        return out

    async def create(self, **kw):
        """chat 与 responses 共用的入口：按是否带 messages 区分调用方。"""
        is_stream = kw.pop("stream", False)
        if "messages" in kw:
            self.calls.append(("chat", kw))
            if is_stream:
                if self._chat_stream is None:
                    raise FakeStatusError(404, "Unknown request URL: POST /v1/chat/completions")
                return self._chat_stream
            if self._chat is None:
                raise FakeStatusError(404, "Unknown request URL: POST /v1/chat/completions")
            return await self._maybe(self._chat, **kw)
        self.calls.append(("responses", kw))
        if is_stream:
            if self._resp_stream is None:
                raise FakeStatusError(404, "Unknown request URL: POST /v1/responses")
            return self._resp_stream
        if self._resp is None:
            raise FakeStatusError(404, "Unknown request URL: POST /v1/responses")
        return await self._maybe(self._resp, **kw)


# ---------------------------------------------------------------- 测试装置
def _settings(**over) -> Settings:
    """构造受控 Settings：**不读 .env**（否则本机真实 Key 会干扰断言）。"""
    base = dict(
        _env_file=None,
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        llm_structured_model="",
        llm_provider="",
        llm_providers="",
        llm_providers_file="",
        llm_api_dialect="auto",
        llm_extra_headers="",
        llm_extra_body="",
        llm_mock=False,
    )
    base.update(over)
    return Settings(**base)


def _use(settings: Settings):
    """把 get_settings 替换成给定配置。

    需要同时替换两处：`providers` 内部是延迟导入（打 app.config 即生效），
    而 `client` 顶层 `from app.config import get_settings` 绑定的是**导入时的**
    函数对象，必须单独打 app.llm.client.get_settings。
    """
    import app.config as cfg
    import app.llm.client as client_mod

    cfg.get_settings = lambda: settings      # type: ignore[assignment]
    client_mod.get_settings = lambda: settings  # type: ignore[assignment]


class _Ctx:
    """极简 monkeypatch：替换函数并自动还原。"""

    def __init__(self, **targets):
        self.targets = targets
        self.saved = []

    def __enter__(self):
        for path, value in self.targets.items():
            mod_path, _, attr = path.rpartition(".")
            mod = __import__(mod_path, fromlist=[attr]) if mod_path else None
            obj = mod if mod else None
            self.saved.append((obj or __import__("app.llm.client", fromlist=["x"]), attr, getattr(obj, attr)))
            setattr(obj, attr, value)
        return self

    def __exit__(self, *exc):
        for obj, attr, old in self.saved:
            setattr(obj, attr, old)
        return False


def _reset_dialect_cache():
    llm._DIALECT_CACHE.clear()


# ---------------------------------------------------------------- 1. 归一化
def test_base_url_normalization():
    cases = {
        # 裸域名 → 补 /v1（否则 SDK 会请求 /chat/completions 而 404）
        "https://api.foo.com": "https://api.foo.com/v1",
        "https://api.foo.com/": "https://api.foo.com/v1",
        "  https://api.foo.com  ": "https://api.foo.com/v1",
        # 已经是 /v1 → 保持
        "https://api.foo.com/v1": "https://api.foo.com/v1",
        "https://api.foo.com/v1/": "https://api.foo.com/v1",
        # 误粘完整 endpoint → 剥掉（用户从 curl 示例/文档里整行拷来的常见情形）
        "https://api.foo.com/v1/chat/completions": "https://api.foo.com/v1",
        "https://api.foo.com/v1/responses": "https://api.foo.com/v1",
        # 非 /v1 的自定义前缀**必须原样保留**（智谱、Gemini 兼容层）
        "https://open.bigmodel.cn/api/paas/v4": "https://open.bigmodel.cn/api/paas/v4",
        "https://generativelanguage.googleapis.com/v1beta/openai": "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/": "https://generativelanguage.googleapis.com/v1beta/openai",
        # 本地服务
        "http://localhost:11434/v1": "http://localhost:11434/v1",
    }
    for raw, want in cases.items():
        got = pv.normalize_base_url(raw)
        assert got == want, f"normalize_base_url({raw!r}) = {got!r}，期望 {want!r}"

    for bad in ("", "   ", "api.foo.com", "ftp://api.foo.com", "file:///etc/passwd"):
        try:
            pv.normalize_base_url(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法 base_url 未被拒绝：{bad!r}")
    print(f"[PASS] base_url 归一化 {len(cases)} 例；非法输入被拒")


def test_api_key_cleaning():
    cases = {
        "sk-abc": "sk-abc",
        "  sk-abc  ": "sk-abc",
        '"sk-abc"': "sk-abc",
        "'sk-abc'": "sk-abc",
        "Bearer sk-abc": "sk-abc",
        "bearer sk-abc": "sk-abc",
        "Bearer  sk-abc ": "sk-abc",
        None: "",
        "   ": "",
    }
    for raw, want in cases.items():
        got = pv.clean_api_key(raw)
        assert got == want, f"clean_api_key({raw!r}) = {got!r}，期望 {want!r}"
    print(f"[PASS] API Key 清洗 {len(cases)} 例（引号/空格/Bearer 前缀）")


# ---------------------------------------------------------------- 2. 注册表
def test_builtin_catalog():
    providers = pv.load_providers(_settings())
    assert "deepseek" in providers and "ollama" in providers
    assert providers["deepseek"].base_url == "https://api.deepseek.com/v1"
    # 内置目录必须**不含任何 Key**（这是能安全开源的前提）
    for p in providers.values():
        assert not p.api_key, f"内置供应商 {p.id} 竟然带了 Key"

    # 即便配了全局 Key，也**不得**无差别分发给全部内置供应商 ——
    # 否则界面会把每家都标成"已配 Key"，换一家就 401（谎报可用性）。
    withkey = pv.load_providers(_settings(llm_api_key="sk-global", llm_model="m-global"))
    leaked = [p.id for p in withkey.values() if p.source == "builtin" and p.api_key]
    assert not leaked, f"全局 Key 被错误分发给了内置供应商：{leaked}"
    assert withkey["default"].api_key == "sk-global", "旧版 default 仍应拿到全局 Key"
    print(f"[PASS] 内置供应商目录 {len(providers)} 个，均不含 Key；全局 Key 不外溢")


def test_custom_providers_via_env_and_file():
    obj_form = _settings(
        llm_providers=json.dumps({"mygw": {"label": "公司网关", "base_url": "https://llm.corp.com",
                                           "api_key": "sk-corp", "model": "qwen-plus"}})
    )
    p = pv.load_providers(obj_form)["mygw"]
    assert p.base_url == "https://llm.corp.com/v1" and p.api_key == "sk-corp"
    assert p.label == "公司网关" and p.source == "env"

    arr_form = _settings(
        llm_providers=json.dumps([{"id": "a", "base_url": "https://a.com/v1", "model": "m1"},
                                  {"id": "b", "base_url": "https://b.com", "model": "m2"}])
    )
    got = pv.load_providers(arr_form)
    assert got["a"].model == "m1" and got["b"].base_url == "https://b.com/v1"

    wrapped = _settings(llm_providers=json.dumps({"providers": {"c": {"base_url": "https://c.com", "model": "m"}}}))
    assert "c" in pv.load_providers(wrapped)

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "providers.json"
        f.write_text(json.dumps({"fromfile": {"base_url": "https://f.com", "model": "mf"}}), encoding="utf-8")
        from_file = pv.load_providers(_settings(llm_providers_file=str(f)))
        assert from_file["fromfile"].base_url == "https://f.com/v1"

        # 文件存在但内容非法 → 明确报错，不静默吞掉
        bad = Path(d) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        try:
            pv.load_providers(_settings(llm_providers_file=str(bad)))
        except ValueError:
            pass
        else:
            raise AssertionError("非法供应商 JSON 未被拒绝")

        # 文件不存在 → 只告警不报错（用户可能后面才创建）
        missing = pv.load_providers(_settings(llm_providers_file=str(Path(d) / "nope.json")))
        assert "deepseek" in missing
    print("[PASS] 自定义供应商：env JSON（对象/数组/包裹）+ 文件；非法内容报错、文件缺失仅告警")


def test_legacy_env_still_works():
    """旧版三件套必须零改动继续可用（向后兼容红线）。"""
    s = _settings(llm_base_url="https://old.example.com/v1", llm_api_key="sk-old",
                  llm_model="old-model", llm_structured_model="old-struct")
    p = pv.resolve_provider(settings=s)
    assert p.id == "default" and p.base_url == "https://old.example.com/v1"
    assert p.api_key == "sk-old" and p.model == "old-model"
    assert p.effective_structured_model == "old-struct"
    assert p.ready and s.llm_ready

    # 必须带 /v1 才叫就绪：占位 Key 不算已配置
    placeholder = _settings(llm_base_url="https://x.com/v1", llm_api_key="sk-xxx", llm_model="m")
    assert placeholder.llm_ready is False, "占位 Key 不应被判为已配置"
    print("[PASS] 旧版 LLM_BASE_URL/API_KEY/MODEL 三件套向后兼容；占位 Key 不算已配置")


def test_selected_builtin_uses_global_fallback_key():
    """选内置供应商 + 只填一个 LLM_API_KEY 也要能跑（降低上手门槛）。

    语义（重要，勿改成"全局模型覆盖一切"）：`LLM_API_KEY` / `LLM_MODEL` 是
    **填空**式兜底 —— 只在供应商自身没有该字段时生效。
    否则 .env 里残留的 `LLM_MODEL=deepseek-chat` 会被发到刚选的 zhipu，
    直接 400（模型不存在），用户完全无法理解为什么。
    要改内置供应商的模型，用 LLM_PROVIDERS 覆盖该条目（见下）。
    """
    s = _settings(llm_provider="deepseek", llm_api_key="sk-only", llm_model="global-override-model")
    p = pv.resolve_provider(settings=s)
    assert p.id == "deepseek"
    assert p.base_url == "https://api.deepseek.com/v1"   # 地址来自内置目录
    assert p.api_key == "sk-only"                        # Key 走全局兜底（供应商自身无 Key）
    # 模型用**内置目录里的默认值**，不被全局 LLM_MODEL 顶掉。
    # 这里从目录读取而不是硬编码模型名：官方模型名会迭代（deepseek-chat 已下线，
    # 现为 deepseek-flash），硬编码会让"数据更新"误报成"逻辑回归"。
    assert p.model == pv.BUILTIN_PROVIDERS["deepseek"]["model"], p.model
    assert p.ready and s.llm_ready

    # 想让内置供应商换模型 → 用 LLM_PROVIDERS 覆盖该条目（这是唯一的显式途径）
    s2 = _settings(
        llm_provider="deepseek", llm_api_key="sk-only", llm_model="global-override-model",
        llm_providers=json.dumps({"deepseek": {"model": "explicit-override-model"}}),
    )
    p2 = pv.resolve_provider(settings=s2)
    assert p2.model == "explicit-override-model", p2.model
    assert p2.api_key == "sk-only"
    assert p2.base_url == "https://api.deepseek.com/v1", "覆盖模型不应丢掉内置地址"
    print("[PASS] 选内置供应商 + 全局兜底 Key 可跑通；LLM_MODEL 只填空、改模型走 LLM_PROVIDERS")


def test_public_view_never_leaks_key():
    """**脱敏红线**：给前端的任何视图都不得出现 Key。"""
    secret = "sk-super-secret-value-12345"
    s = _settings(
        llm_providers=json.dumps({"mygw": {"base_url": "https://llm.corp.com", "api_key": secret, "model": "m"}}),
        llm_api_key=secret,
    )
    blob = json.dumps(pv.list_public(s), ensure_ascii=False)
    assert secret not in blob, "供应商列表泄露了 Key"
    assert json.loads(blob)[0].get("has_key") in (True, False)
    mine = [p for p in pv.list_public(s) if p["id"] == "mygw"][0]
    assert mine["has_key"] is True and "api_key" not in mine
    print("[PASS] 供应商列表脱敏：不含 Key，只暴露 has_key")


def test_keyless_local_provider():
    """Ollama / LM Studio / 内网网关不填 Key 也应可用。"""
    ollama = pv.resolve_provider("ollama", {"model": "qwen2.5:7b"}, settings=_settings())
    assert ollama.needs_key is False
    assert ollama.api_key == "" and ollama.ready
    assert ollama.sdk_api_key == pv.KEYLESS_PLACEHOLDER

    # 公网地址默认需要 Key
    pub = pv.resolve_provider("deepseek", {"api_key": "k"}, settings=_settings())
    assert pub.needs_key is True

    # 显式 key_required 覆盖自动推断
    forced = pv.load_providers(_settings(llm_providers=json.dumps(
        {"gw": {"base_url": "http://10.0.0.9:8000/v1", "model": "m", "key_required": True}})))
    assert forced["gw"].needs_key is True
    # 10.x 内网默认视为免 Key
    auto = pv.load_providers(_settings(llm_providers=json.dumps(
        {"gw2": {"base_url": "http://10.0.0.9:8000/v1", "model": "m"}})))
    assert auto["gw2"].needs_key is False
    print("[PASS] 免 Key 本地/内网供应商可用；公网默认需要 Key；key_required 可显式覆盖")


def test_request_level_override():
    """请求级覆盖（BYOK）：优先级最高，且不污染服务端配置。"""
    s = _settings(llm_provider="deepseek", llm_api_key="sk-server", llm_model="some-global-model")
    p = pv.resolve_provider("deepseek", {"api_key": "sk-user", "model": "my-model",
                                         "base_url": "https://proxy.example.com"}, settings=s)
    assert p.api_key == "sk-user" and p.model == "my-model"
    assert p.base_url == "https://proxy.example.com/v1" and p.source == "request"

    s2 = _settings(llm_provider="deepseek", llm_api_key="sk-server", llm_model="some-global-model")
    assert pv.resolve_provider(settings=s2).id == "deepseek"

    try:
        pv.resolve_provider("nope-not-exist", settings=_settings())
    except ValueError as e:
        assert "未知供应商" in str(e)
    else:
        raise AssertionError("未知供应商未报错")
    print("[PASS] 请求级覆盖优先级最高；未知供应商给出可用列表")


# ---------------------------------------------------------------- 3. 方言差异
def test_usage_mapping_both_dialects():
    chat_usage = _Usage(prompt=10, completion=5, total=15)
    assert llm._usage_tuple(chat_usage, "chat_completions") == (10, 5, 15)
    # Responses 用 input/output_tokens（字段名不同，映射错了计费就会是 0）
    resp_usage = _Usage(prompt=0, completion=0, total=20, input_tokens=12, output_tokens=8)
    assert llm._usage_tuple(resp_usage, "responses") == (12, 8, 20)
    assert llm._usage_tuple(None, "chat_completions") == (0, 0, 0)
    print("[PASS] 双方言 usage 字段映射正确（prompt/completion ↔ input/output）")


def test_split_system_for_responses():
    msgs = llm.build_messages("问题", "系统提示", [{"role": "user", "content": "上一轮"}])
    system, rest = llm._split_system(msgs)
    assert system == "系统提示"
    assert all(m["role"] != "system" for m in rest), "Responses 的 input 不接受 system 角色"
    assert [m["role"] for m in rest] == ["user", "user"]
    print("[PASS] Responses 方言：system 拆到 instructions，input 不含 system 角色")


def test_error_classification():
    missing = FakeStatusError(404, "Unknown request URL: POST /v1/responses")
    assert llm._is_endpoint_missing(missing)
    assert llm._is_endpoint_missing(FakeStatusError(405, "Method Not Allowed"))
    # 401 不是"端点不存在"，不能触发方言切换（否则会白试一遍并掩盖鉴权问题）
    assert not llm._is_endpoint_missing(FakeStatusError(401, "Invalid API key"))
    assert not llm._is_endpoint_missing(FakeStatusError(400, "model not found"))

    assert llm._unsupported_param(FakeStatusError(400, "unsupported parameter: temperature")) == "temperature"
    assert llm._unsupported_param(FakeStatusError(400, "Unknown parameter: enable_thinking")) == "enable_thinking"
    assert llm._unsupported_param(FakeStatusError(400, "response_format is not supported")) == "response_format"
    # 参数值本身非法（可修正）不该被当成"不支持该参数"而白丢参数
    assert llm._unsupported_param(FakeStatusError(400, "temperature must be <= 2")) is None
    assert llm._unsupported_param(FakeStatusError(500, "unsupported parameter: temperature")) is None
    print("[PASS] 错误分类：端点缺失 vs 参数不支持 vs 其他（401 不误判为方言问题）")


def test_diagnostic_never_leaks_upstream_text():
    """诊断文案会直达前端，**不得**带上游原文（可能含 Key/内网地址）。"""
    leaky = FakeStatusError(401, "Incorrect API key provided: sk-live-abcdef123456 for org-42 at 10.0.0.7")
    text = llm._err_diagnostic(leaky, provider=pv.resolve_provider("deepseek", {"api_key": "x"}, settings=_settings()))
    assert "sk-live-abcdef123456" not in text
    assert "org-42" not in text and "10.0.0.7" not in text
    assert "401" in text and "API Key" in text
    print("[PASS] 诊断文案不泄露上游原文（Key/组织/内网地址）")


# ---------------------------------------------------------------- 4. 自动切换
def test_auto_dialect_switch_to_responses_and_cache():
    """**核心需求**：只提供 /responses 的供应商也要能用，且结论被缓存。"""
    _reset_dialect_cache()
    s = _settings(
        llm_provider="fake",
        llm_providers=json.dumps({"fake": {"base_url": "https://only-responses.example.com/v1",
                                           "api_key": "sk-f", "model": "resp-model", "api": "auto"}}),
    )
    fake = _FakeClient(
        chat=None,  # chat.completions 一律 404 → 应自动切到 responses
        resp=lambda **kw: _RespResp("回答正文", "推理摘要", _Usage(0, 0, 30, input_tokens=20, output_tokens=10)),
    )
    _use(s)
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: fake}):
        async def once(prompt: str):
            # 计费是上下文隔离的：必须在协程**内部**读，父上下文读不到
            out = await llm.chat_with_reasoning(prompt, model="resp-model")
            return out, llm.get_run_metrics()

        (text, think), metrics = asyncio.run(once("你好"))

    assert text == "回答正文", text
    assert think == "推理摘要", think
    assert fake.calls[0][0] == "chat" and fake.calls[1][0] == "responses", fake.calls
    assert metrics["prompt_tokens"] == 20 and metrics["completion_tokens"] == 10

    provider = pv.resolve_provider("fake", settings=s)
    assert llm._DIALECT_CACHE[provider.cache_key()] == "responses", "方言探测结论未缓存"

    # 第二次调用应**直接**走 responses（不再白试 chat）
    fake.calls.clear()
    _use(s)
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: fake}):
        asyncio.run(llm.chat_with_reasoning("再来", model="resp-model"))
    assert [c[0] for c in fake.calls] == ["responses"], f"缓存未生效：{fake.calls}"
    _reset_dialect_cache()
    print("[PASS] auto 方言：chat 端点 404 → 自动改用 responses 并缓存（二次不再白试）")


def test_explicit_dialect_skips_probing():
    _reset_dialect_cache()
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m", "api": "responses"}}))
    fake = _FakeClient(resp=lambda **kw: _RespResp("ok"))
    _use(s)
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: fake}):
        asyncio.run(llm.chat_with_reasoning("hi", model="m"))
    assert [c[0] for c in fake.calls] == ["responses"], "显式声明方言时不应试探 chat"
    print("[PASS] 显式声明 api=responses 时不试探 chat.completions")


def test_parameter_downgrade_retries():
    """上游拒绝某个可选参数时应当**丢参数重试**，而不是把失败甩给用户。"""
    _reset_dialect_cache()
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m",
                  "api": "chat_completions"}}))
    seen: list[dict] = []

    async def chat(**kw):
        seen.append(kw)
        if "temperature" in kw:
            raise FakeStatusError(400, "unsupported parameter: temperature")
        return _ChatResp("降级后成功")

    _use(s)
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(chat=chat)}):
        text, _think = asyncio.run(llm.chat_with_reasoning("hi", model="m"))

    assert text == "降级后成功", text
    assert len(seen) == 2, f"应重试一次，实际 {len(seen)} 次"
    assert "temperature" in seen[0] and "temperature" not in seen[1]
    print("[PASS] 参数降级阶梯：temperature 不被支持 → 丢弃后重试成功")


def test_structured_output_both_dialects():
    """结构化输出在两种方言下都要能解析（json_mode 的落点不同）。"""
    schema = {"type": "object", "properties": {"intent": {"type": "string"}, "target": {"type": "string"}}}

    _reset_dialect_cache()
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m",
                  "api": "chat_completions"}}))
    _use(s)
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(
            chat=lambda **kw: _ChatResp('```json\n{"intent":"ticket.query","target":"G1","noise":1}\n```'))}):
        obj = asyncio.run(llm.chat_structured("查 G1", schema, no_think=True, model="m"))
    assert obj == {"intent": "ticket.query", "target": "G1"}, obj   # 围栏被容忍，噪声键被剔除

    _reset_dialect_cache()
    s2 = _settings(llm_provider="fake2", llm_providers=json.dumps(
        {"fake2": {"base_url": "https://y.example.com/v1", "api_key": "k", "model": "m2",
                   "api": "responses"}}))
    captured: list[dict] = []

    async def resp(**kw):
        captured.append(kw)
        return _RespResp('{"intent":"station.lookup","target":"北京南"}')

    _use(s2)
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(resp=resp)}):
        obj2 = asyncio.run(llm.chat_structured("北京南", schema, model="m2"))
    assert obj2 == {"intent": "station.lookup", "target": "北京南"}, obj2
    # Responses 的结构约束走 text.format，且系统提示走 instructions
    assert captured[0]["text"] == {"format": {"type": "json_object"}}
    assert "messages" not in captured[0] and "instructions" in captured[0]
    _reset_dialect_cache()
    print("[PASS] 结构化输出双方言均可用（completions 用 response_format，responses 用 text.format）")


def test_stream_responses_dialect_mapping():
    """流式：responses 的事件类型要正确映射为 think/text，并记录 usage。"""
    _reset_dialect_cache()
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m",
                  "api": "responses"}}))
    stream = _FakeStream([
        _StreamEvent("response.reasoning_summary_text.delta", "先想"),
        _StreamEvent("response.output_text.delta", "你好"),
        _StreamEvent("response.output_text.delta", "，车迷"),
        _StreamEvent("response.completed", response=type("R", (), {
            "usage": _Usage(0, 0, 30, input_tokens=18, output_tokens=12)})()),
    ])
    _use(s)

    async def collect():
        out = []
        async for kind, text in llm.stream_completion("hi", model="m"):
            out.append((kind, text))
        return out, llm.get_run_metrics()

    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(resp_stream=stream)}):
        items, metrics = asyncio.run(collect())

    assert items == [("think", "先想"), ("text", "你好"), ("text", "，车迷")], items
    assert metrics["prompt_tokens"] == 18 and metrics["completion_tokens"] == 12, metrics
    assert stream.closed, "上游流未被关闭（会导致连接泄漏）"
    _reset_dialect_cache()
    print("[PASS] responses 流式事件映射 + usage 计费 + 流关闭")


def test_stream_auto_switch_on_first_event():
    """流式请求的失败常在首个事件才暴露 —— 也要能自动切方言。"""
    _reset_dialect_cache()
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m", "api": "auto"}}))
    broken = _FakeStream([], fail_with=FakeStatusError(
        404, "Unknown request URL: POST /v1/chat/completions"))
    good = _FakeStream([
        _StreamEvent("response.output_text.delta", "没问题"),
        _StreamEvent("response.completed", response=type("R", (), {"usage": None})()),
    ])
    fake = _FakeClient(chat_stream=broken, resp_stream=good)
    _use(s)

    async def collect():
        return [it async for it in llm.stream_completion("hi", model="m")]

    with _Ctx(**{"app.llm.client.get_client": lambda p=None: fake}):
        items = asyncio.run(collect())

    assert items == [("text", "没问题")], items
    assert fake.calls[0][0] == "chat" and fake.calls[1][0] == "responses", fake.calls
    assert broken.closed, "切换方言时应关闭已废弃的上游流"
    assert llm._DIALECT_CACHE[pv.resolve_provider("fake", settings=s).cache_key()] == "responses"
    _reset_dialect_cache()
    print("[PASS] 流式首事件失败也能切方言，并关闭废弃的上游流")


def test_probe_provider_reports_dialect_and_models():
    """「测试连接」要给出：可用性、方言、延迟、可选模型，且不回显 Key。"""
    _reset_dialect_cache()
    provider = pv.resolve_provider("fake", {"api_key": "sk-secret-probe"}, settings=_settings(
        llm_providers=json.dumps({"fake": {"base_url": "https://x.example.com/v1", "model": "m"}})))
    fake = _FakeClient(chat=None, resp=lambda **kw: _RespResp("pong"))
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: fake}):
        out = asyncio.run(llm.probe_provider(provider))
    assert out["ok"] is True and out["dialect"] == "responses"
    assert out["models"] == ["m-a", "m-b"]
    assert "sk-secret-probe" not in json.dumps(out), "探测结果泄露了 Key"
    assert "latency_ms" in out

    # 失败时也要给出可读原因（同样是脱敏的）
    failing = pv.resolve_provider("f2", {"api_key": "sk-x"}, settings=_settings(
        llm_providers=json.dumps({"f2": {"base_url": "https://y.example.com/v1", "model": "m",
                                         "api": "chat_completions"}})))
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(
            chat=lambda **kw: (_ for _ in ()).throw(FakeStatusError(401, "bad key sk-leak")))}):
        bad = asyncio.run(llm.probe_provider(failing))
    assert bad["ok"] is False and "401" in bad["error"] and "sk-leak" not in bad["error"]
    _reset_dialect_cache()
    print("[PASS] 测试连接：方言/延迟/模型清单 + 失败原因可读且不泄密")


def test_mock_mode_needs_no_provider():
    """mock 模式必须**完全不依赖**供应商配置（无 Key 演示/CI 的前提）。"""
    s = _settings(llm_mock=True)
    _use(s)
    text, think = asyncio.run(llm.chat_with_reasoning("G1 今天谁担当"))
    assert isinstance(text, str) and text
    assert "Mock" in think
    obj = asyncio.run(llm.chat_structured("北京到上海", {"type": "object", "properties": {"intent": {}}}))
    assert isinstance(obj, dict)
    print("[PASS] mock 模式在无任何供应商配置时可用")


def test_unknown_provider_raises_friendly_error():
    _use(_settings())
    try:
        llm.current_provider()
    except llm.LLMUnavailable as e:
        assert "供应商" in str(e) or "unknown" in str(e).lower() or "未知" in str(e)
    else:
        raise AssertionError("未配置时 current_provider 应抛 LLMUnavailable")
    print("[PASS] 未配置/未知供应商抛 LLMUnavailable（编排层可降级，不 500）")


def test_request_base_url_ssrf_guard():
    """请求体里的 base_url 是**用户可控的出站目标** → 生产环境必须按 SSRF 拦截。

    这条曾经漏过：守卫只加在 /api/providers/test，而 /api/chat 同样能用
    base_url 驱动服务端发请求，等于留了一条没人管的 SSRF 路径。
    现在两个入口共用 providers.guard_request_base_url。
    """
    from app.llm.providers import guard_request_base_url

    # 请求级 + 生产 + 未放开内网 → 拒绝（云元数据地址是最典型的 SSRF 目标）
    prod = _settings(app_env="production", llm_allow_private_base_url=False)
    for bad in ("http://169.254.169.254/latest/meta-data/v1", "http://127.0.0.1:8000/v1",
                "http://10.1.2.3/v1", "http://192.168.1.1/v1"):
        p = pv.resolve_provider("deepseek", {"base_url": bad, "api_key": "k", "model": "m"}, settings=prod)
        assert p.source == "request"
        try:
            guard_request_base_url(p, prod)
        except ValueError as e:
            assert "拒绝" in str(e), e
        else:
            raise AssertionError(f"生产环境未拦截内网 base_url：{bad}")

    # 请求级 + 生产 + 显式放开内网 → 放行（自建内网网关的正当用法）
    opened = _settings(app_env="production", llm_allow_private_base_url=True)
    p = pv.resolve_provider("deepseek", {"base_url": "http://10.1.2.3/v1", "api_key": "k", "model": "m"},
                            settings=opened)
    guard_request_base_url(p, opened)   # 不抛异常即通过

    # 非生产 → 放行（本地 Ollama / LM Studio 常用 127.0.0.1）
    dev = _settings(app_env="dev")
    p = pv.resolve_provider("deepseek", {"base_url": "http://127.0.0.1:11434/v1", "api_key": "k", "model": "m"},
                            settings=dev)
    guard_request_base_url(p, dev)

    # **管理员配置**的地址是可信配置（公司内网网关也要能配）→ 即便指向内网也不拦
    admin = _settings(app_env="production", llm_providers=json.dumps(
        {"corp": {"base_url": "http://10.9.9.9/v1", "api_key": "sk-corp", "model": "m"}}))
    p = pv.resolve_provider("corp", settings=admin)
    assert p.source == "env"
    guard_request_base_url(p, admin)

    # 真正拦截的是"出站请求"这一步：get_client 也必须遵守同一策略
    p = pv.resolve_provider("deepseek", {"base_url": "http://169.254.169.254/v1", "api_key": "k", "model": "m"},
                            settings=prod)
    _use(prod)
    try:
        llm.get_client(p)
    except llm.LLMUnavailable as e:
        assert "拒绝" in str(e), e
    else:
        raise AssertionError("get_client 未执行 SSRF 守卫（/api/chat 路径会漏）")
    print("[PASS] SSRF 守卫：请求级 base_url 生产拦截/开发放行/管理员配置放行，且 /api/chat 同守卫")


def test_truncation_flag_follows_finish_reason():
    """`finish_reason=length` 必须被记为"截断"。

    这是"长回答写到一半断掉、原因不明"的根因：模型明确说了因长度上限停止
    （finish_reason=length），而应用此前**完全不读这个字段**。
    """
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m",
                  "api": "chat_completions"}}))
    _use(s)

    # 注意：截断标记与计费一样是 ContextVar，asyncio.run() 跑在**复制的上下文**里，
    # 因此必须在协程**内部**读（真实请求里是同一个 task，故线上行为正确）。
    async def once(finish: str):
        await llm.chat_with_reasoning("hi", model="m")
        return llm.was_truncated()

    llm.reset_run_metrics()
    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(
            chat=lambda **kw: _ChatResp("半句话", finish_reason="length"))}):
        flagged = asyncio.run(once("length"))
    assert flagged is True, "finish_reason=length 未标记为截断"

    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(
            chat=lambda **kw: _ChatResp("完整回答", finish_reason="stop"))}):
        flagged2 = asyncio.run(once("stop"))
    assert flagged2 is False, "正常结束不应标记截断"
    print("[PASS] 截断标记跟随 finish_reason（length→True / stop→False，reset 复位）")


def test_truncation_flag_in_stream():
    """流式路径同样要识别末个 chunk 的 finish_reason=length。"""
    _reset_dialect_cache()
    s = _settings(llm_provider="fake", llm_providers=json.dumps(
        {"fake": {"base_url": "https://x.example.com/v1", "api_key": "k", "model": "m",
                  "api": "chat_completions"}}))
    stream = _FakeStream([
        _StreamEvent2("第一段"),
        _StreamEvent2("第二段", finish_reason="length"),
    ])
    _use(s)
    llm.reset_run_metrics()

    async def collect():
        out = []
        async for kind, text in llm.stream_completion("hi", model="m"):
            out.append((kind, text))
        return out, llm.was_truncated()      # 同上：上下文内读

    with _Ctx(**{"app.llm.client.get_client": lambda p=None: _FakeClient(chat_stream=stream)}):
        items, flagged = asyncio.run(collect())
    assert items == [("text", "第一段"), ("text", "第二段")], items
    assert flagged is True, "流式路径未识别 finish_reason=length"
    print("[PASS] 流式路径识别 finish_reason=length")


def test_output_budget_respects_context_window():
    """输出上限要按上下文窗口收窄，且请求级覆盖优先。

    为什么重要：用户自备的模型窗口差别极大（8k/32k/128k）。若输出上限固定给，
    小窗口模型会直接被上游 400 拒绝（context_length_exceeded）。
    """
    big = [{"role": "user", "content": "字" * 1500}]        # ≈1000 token

    # 窗口远大于输入 → 用请求的上限
    _use(_settings(llm_max_tokens=4096, llm_context_tokens=32000, llm_chars_per_token=1.5))
    assert llm.effective_max_tokens([{"role": "user", "content": "短问题"}]) == 4096

    # 窗口很小 → 收窄到窗口内（窗口 - 输入 - 512 余量）
    _use(_settings(llm_max_tokens=4096, llm_context_tokens=2000, llm_chars_per_token=1.5))
    got = llm.effective_max_tokens(big)
    assert got < 4096, f"未按窗口收窄：{got}"
    # 用同一估算函数算期望，测的是"窗口 - 输入 - 余量"这个算式本身
    used = llm.estimate_tokens(big[0]["content"])
    assert got == 2000 - used - 512, f"{got} != 2000-{used}-512"

    # 请求级覆盖（用户在界面上按自己的模型设置）优先于服务端配置
    llm.set_active_provider({"max_tokens": 777, "context_tokens": 999999})
    assert llm.effective_max_tokens([{"role": "user", "content": "短问题"}]) == 777, "请求级 max_tokens 未生效"
    llm.set_active_provider(None)

    # 0 = 不做窗口约束
    _use(_settings(llm_max_tokens=1234, llm_context_tokens=0))
    assert llm.effective_max_tokens(big) == 1234
    print("[PASS] 输出预算按窗口收窄，请求级覆盖优先，0=不约束")


def main():
    test_base_url_normalization()
    test_api_key_cleaning()
    test_builtin_catalog()
    test_custom_providers_via_env_and_file()
    test_legacy_env_still_works()
    test_selected_builtin_uses_global_fallback_key()
    test_public_view_never_leaks_key()
    test_keyless_local_provider()
    test_request_level_override()
    test_usage_mapping_both_dialects()
    test_split_system_for_responses()
    test_error_classification()
    test_diagnostic_never_leaks_upstream_text()
    test_auto_dialect_switch_to_responses_and_cache()
    test_explicit_dialect_skips_probing()
    test_parameter_downgrade_retries()
    test_structured_output_both_dialects()
    test_stream_responses_dialect_mapping()
    test_stream_auto_switch_on_first_event()
    test_probe_provider_reports_dialect_and_models()
    test_mock_mode_needs_no_provider()
    test_unknown_provider_raises_friendly_error()
    test_truncation_flag_follows_finish_reason()
    test_truncation_flag_in_stream()
    test_output_budget_respects_context_window()
    test_request_base_url_ssrf_guard()
    print("\nLLM 多供应商 / 双方言回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
