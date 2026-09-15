"""M11.1 审计修复的回归测试（**全部无网络**，可在 CI/断网环境运行）。

覆盖本轮修复：
1. LLM 错误文案不得回吐上游原文（Key/组织 ID 泄露面）
2. 结构化输出为"合法 JSON 但非对象"时必须走 LLMUnavailable 降级（不再 AttributeError 逃逸）
3. 流式生成被中断时必须关闭上游流（httpx 连接/socket 泄漏）
4. web.fetch 的 SSRF 防护（环回/内网/元数据/file 协议）
5. 响应体大小上限与"已截断"如实告知
6. web.search 相关性闸门 + 百度兜底（修复前百度永不触发）
7. emu.routing 车型前缀 vs 单台车组（修复前把多台合并成单一结论）
8. 日志净化（session_id 注入）

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_regressions.py
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------- 测试替身（Fake） ----------


class _FakeResponse:
    def __init__(self, text: str = "", payload=None, status: int = 200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """按 URL 子串匹配返回预置响应（用于离线驱动工具层）。"""

    def __init__(self, mapping: dict[str, _FakeResponse], **_kw):
        self._mapping = mapping

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str, **_kw):
        for key, resp in self._mapping.items():
            if key in url:
                return resp
        raise RuntimeError(f"未预置的 URL：{url}")


class _FakeHttpxModule:
    """替换模块内的 httpx，避免污染全局 httpx。"""

    def __init__(self, mapping: dict[str, _FakeResponse]):
        self._mapping = mapping

    def AsyncClient(self, **_kw):  # noqa: N802 —— 模拟 httpx.AsyncClient
        return _FakeAsyncClient(self._mapping)


class _Chunk:
    def __init__(self, content: str | None = None, reasoning: str | None = None):
        self.usage = None
        delta = type("D", (), {"content": content, "reasoning_content": reasoning})()
        self.choices = [type("C", (), {"delta": delta})()]


class _FakeStream:
    """记录是否被关闭的假上游流。"""

    def __init__(self, chunks: list[_Chunk]):
        self._chunks = chunks
        self._i = 0
        self.close_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c

    async def aclose(self):
        self.close_calls += 1


# ---------- 1. 错误文案不得回吐上游原文 ----------


def test_error_diagnostic_does_not_leak_upstream():
    from app.llm.client import _err_diagnostic

    class _Upstream(Exception):
        def __init__(self):
            super().__init__(
                "Error code: 401 - {'error': {'message': 'Invalid token sk-SECRET-abcdef',"
                " 'org': 'org-12345678'}}"
            )
            self.status_code = 401

    msg = _err_diagnostic(_Upstream())
    assert "sk-SECRET-abcdef" not in msg, f"泄露了 Key 片段：{msg}"
    assert "org-12345678" not in msg, f"泄露了组织 ID：{msg}"
    assert "401" in msg, msg
    print(f"[PASS] LLM 错误文案已脱敏 -> {msg[:40]}…")


# ---------- 2. 非对象 JSON 必须降级 ----------


def test_structured_non_object_json_raises_llm_unavailable():
    from app.llm import client as llm_client
    from app.llm.client import LLMUnavailable

    class _Msg:
        def __init__(self, content):
            self.content = content
            self.model_extra = {}

    class _Resp:
        def __init__(self, content):
            self.usage = None
            self.choices = [type("C", (), {"message": _Msg(content)})()]

    class _Completions:
        def __init__(self, content):
            self._content = content

        async def create(self, **_kw):
            return _Resp(self._content)

    class _Client:
        def __init__(self, content):
            self.chat = type("X", (), {"completions": _Completions(content)})()

    settings = llm_client.get_settings()
    old_mock = settings.llm_mock
    settings.llm_mock = False
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}}
    try:
        for payload in ("[1, 2, 3]", '"just a string"', "42", "null"):
            llm_client.get_client = lambda c=payload: _Client(c)  # type: ignore[assignment]
            try:
                asyncio.run(llm_client.chat_structured("x", schema))
            except LLMUnavailable as e:
                assert "不是对象" in str(e), e
            except AttributeError as e:  # 修复前正是这条逃逸路径
                raise AssertionError(f"非对象 JSON 逃出了 LLMUnavailable 契约：{e}") from None
            else:
                raise AssertionError(f"非对象 JSON 未被拒绝：{payload}")
        print("[PASS] 合法 JSON 但非对象（list/str/num/null）-> 统一 LLMUnavailable 降级")
    finally:
        settings.llm_mock = old_mock


# ---------- 3. 中断时关闭上游流 ----------


def test_stream_closes_upstream_on_interrupt():
    from app.llm import client as llm_client

    settings = llm_client.get_settings()
    old_mock = settings.llm_mock
    settings.llm_mock = False
    stream = _FakeStream([_Chunk(content="第一段"), _Chunk(content="第二段"), _Chunk(content="第三段")])

    class _Client:
        def __init__(self):
            self.chat = type("X", (), {"completions": self})()

        async def create(self, **_kw):
            return stream

    llm_client.get_client = lambda: _Client()  # type: ignore[assignment]
    try:
        async def _consume_then_stop():
            gen = llm_client.stream_completion("hi")
            async for _kind, _text in gen:
                break                     # 模拟用户点"停止"：chat.py 随后会 aclose()
            await gen.aclose()            # 复刻 api/chat.py 的清理路径

        asyncio.run(_consume_then_stop())
        assert stream.close_calls >= 1, "中断后上游流未被关闭（连接/资源泄漏）"
        print(f"[PASS] 中断后上游流已关闭（aclose 调用 {stream.close_calls} 次）")
    finally:
        settings.llm_mock = old_mock


# ---------- 4/5. SSRF 与响应体上限 ----------


def test_ssrf_guard_blocks_non_public_targets():
    from app.tools._http import UnsafeUrlError, assert_public_url

    blocked = [
        "http://127.0.0.1:8000/health",
        "http://localhost:8000/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",   # 云元数据
        "http://[::1]/",
        "file:///etc/passwd",
        "gopher://127.0.0.1:70/",
        "ftp://example.com/x",
    ]
    for url in blocked:
        try:
            assert_public_url(url)
        except UnsafeUrlError:
            continue
        raise AssertionError(f"未被拦截：{url}")

    # 公网 IP 字面量应放行（无需 DNS）
    assert_public_url("http://93.184.216.34/")
    print(f"[PASS] SSRF 防护拦截 {len(blocked)} 类非公网/非法协议目标，公网地址放行")


def test_response_size_cap_and_truncation_flag():
    from app.tools._http import get_text_ex

    body = "<html><body>" + ("A" * 5000) + "</body></html>"

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_a):   # 静音
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/big"
    try:
        # 默认策略：环回地址必须被拒（证明守卫在真实抓取链路上生效）
        from app.tools._http import UnsafeUrlError
        try:
            asyncio.run(get_text_ex(url))
        except UnsafeUrlError:
            print("[PASS] 真实抓取链路对环回地址同样拒绝（非仅函数级校验）")
        else:
            raise AssertionError("环回地址未被拒绝")

        # 显式放行 + 小上限 → 必须截断且如实标记
        text, truncated = asyncio.run(get_text_ex(url, allow_private=True, max_bytes=64))
        assert truncated is True, "超出上限未标记截断"
        assert len(text.encode("utf-8")) <= 64, f"未真正限制字节数：{len(text)}"
        print(f"[PASS] 响应体上限生效（截断={truncated}，读取 {len(text.encode('utf-8'))} 字节）")
    finally:
        server.shutdown()
        server.server_close()


# ---------- 6. web.search 相关性闸门 + 百度兜底 ----------


_BING_IRRELEVANT = """
<li class="b_algo"><h2><a href="https://jobs.example.com/x">BOSS直聘 招聘 前端</a></h2>
<p>互联网招聘平台</p></li>
<li class="b_algo"><h2><a href="https://paper.example.com/y">Lactobacillus 论文</a></h2>
<p>微生物学研究</p></li>
"""

_BING_RELEVANT = """
<li class="b_algo"><h2><a href="https://rail.example.com/a">CR400AF 动车组 交路 查询</a></h2>
<p>复兴号动车组交路与担当车组</p></li>
<li class="b_algo"><h2><a href="https://rail.example.com/b">CR400AF 动车组介绍</a></h2>
<p>复兴号 CR400AF 动车组</p></li>
<li class="b_algo"><h2><a href="https://x.example.com/c">无关广告页面</a></h2>
<p>广告</p></li>
"""

_BAIDU_RELEVANT = """
<h3><a href="https://baike.example.com/cr400af">CR400AF 动车组 交路 百科</a></h3>
<h3><a href="https://news.example.com/emu">复兴号 CR400AF 动车组交路 报道</a></h3>
"""


def test_web_search_relevance_gate_and_baidu_fallback():
    from app.tools import web_search

    original = web_search.httpx
    try:
        # 场景 A：Bing 有结果但全部无关 → 必须回退到百度（修复前永不回退）
        web_search.httpx = _FakeHttpxModule({  # type: ignore[assignment]
            "cn.bing.com": _FakeResponse(_BING_IRRELEVANT),
            "www.baidu.com": _FakeResponse(_BAIDU_RELEVANT),
        })
        res = asyncio.run(web_search.WebSearchTool2().invoke({"q": "CR400AF 动车组 交路"}))
        assert res.ok, res.error
        assert res.data["engine"] == "baidu", f"未回退到百度：{res.data}"
        assert res.data["relevant_count"] >= 2, res.data
        assert res.data["filtered_out"] >= 0
        print(f"[PASS] 搜索引擎相关性闸门 -> 采用 {res.data['engine']}，"
              f"相关 {res.data['relevant_count']} 条（统计={res.data['engine_stats']}）")

        # 场景 B：Bing 相关 → 直接用 Bing，并过滤无关条目
        web_search.httpx = _FakeHttpxModule({  # type: ignore[assignment]
            "cn.bing.com": _FakeResponse(_BING_RELEVANT),
        })
        res2 = asyncio.run(web_search.WebSearchTool2().invoke({"q": "CR400AF 动车组 交路"}))
        assert res2.ok and res2.data["engine"] == "bing", res2.data
        titles = [r["title"] for r in res2.data["results"]]
        assert not any("无关广告页面" in t for t in titles), f"无关结果未被过滤：{titles}"
        print(f"[PASS] 相关结果被保留、无关结果被过滤 -> {titles}")

        # 场景 C：两个引擎都无关 → 如实失败，绝不把无关内容当答案
        web_search.httpx = _FakeHttpxModule({  # type: ignore[assignment]
            "cn.bing.com": _FakeResponse(_BING_IRRELEVANT),
            "www.baidu.com": _FakeResponse(_BING_IRRELEVANT),
        })
        res3 = asyncio.run(web_search.WebSearchTool2().invoke({"q": "CR400AF 动车组 交路"}))
        assert res3.ok is False, "全无关时仍返回成功（宁缺毋滥被破坏）"
        print(f"[PASS] 全部引擎均无关 -> ok=False 且如实说明 -> {res3.error[:40]}…")
    finally:
        web_search.httpx = original  # type: ignore[assignment]


# ---------- 7. emu.routing 车型前缀 vs 单台车组 ----------


_EMU_SERIES_PAYLOAD = [
    {"date": "2026-09-14 10:00", "emu_no": "CR400AF2001", "train_no": "G2863"},
    {"date": "2026-09-14 10:05", "emu_no": "CR400AF2003", "train_no": "G2861"},
    {"date": "2026-09-14 10:10", "emu_no": "CR400AF1007", "train_no": "G384"},
]
_TRAIN_PAYLOAD = [
    {"date": "2026-09-14 10:00", "emu_no": "CR400BF-A-5054", "train_no": "G1"},
    {"date": "2026-09-14 10:00", "emu_no": "CR400BF-A-5054", "train_no": "G2"},   # 别的车次
]


def test_emu_routing_series_vs_exact_and_input_validation():
    from app.tools import emu_routing

    original = emu_routing.httpx
    try:
        # 场景 A：车型前缀匹配到多台 —— 必须如实说明"不是单台车组"
        emu_routing.httpx = _FakeHttpxModule(  # type: ignore[assignment]
            {"/emu/CR400AF": _FakeResponse(payload=_EMU_SERIES_PAYLOAD)}
        )
        res = asyncio.run(emu_routing.EmuRoutingTool().invoke({"emu_no": "CR400AF"}))
        assert res.ok, res.error
        assert res.data["match_mode"] == "series", res.data
        assert res.data["unit_count"] == 3, res.data
        # 只断言**不随当天时点漂移**的部分：车型前缀必须被识别为"多台车组"而非单台。
        # （原先断言的是分支特有字样"不是单台车组"，而 rail.re 当天有无记录会走不同分支 → 时对时错）
        assert "是车型/系列前缀" in res.text, res.text
        assert "3 台车组" in res.text, res.text
        print(f"[PASS] 车型前缀 -> series 模式，如实给出 {res.data['unit_count']} 台车组")

        # 场景 B：具体车组号（带连字符也接受）→ 只保留该车组
        emu_routing.httpx = _FakeHttpxModule(  # type: ignore[assignment]
            {"/emu/CR400AF2001": _FakeResponse(payload=_EMU_SERIES_PAYLOAD)}
        )
        res2 = asyncio.run(emu_routing.EmuRoutingTool().invoke({"emu_no": "CR400AF-2001"}))
        assert res2.ok, res2.error
        assert res2.data["match_mode"] == "exact", res2.data
        assert res2.data["count"] == 1, res2.data
        print(f"[PASS] 单台车组 -> exact 模式，记录数={res2.data['count']}")

        # 场景 C：接口顺手返回的其它车次必须被过滤掉
        emu_routing.httpx = _FakeHttpxModule(  # type: ignore[assignment]
            {"/train/G1": _FakeResponse(payload=_TRAIN_PAYLOAD)}
        )
        res3 = asyncio.run(emu_routing.EmuRoutingTool().invoke({"train": "G1"}))
        assert res3.ok, res3.error
        assert res3.data["count"] == 1, f"未过滤其它车次：{res3.data}"
        assert all(r["train_code"] == "G1" for r in res3.data["records"]), res3.data
        print("[PASS] 车次查询 -> 其它车次记录被过滤")

        # 场景 D：把车次号塞进车组槽位 → 直接拒绝（不再发出无意义请求）
        res4 = asyncio.run(emu_routing.EmuRoutingTool().invoke({"emu_no": "G1"}))
        assert res4.ok is False and "格式不正确" in res4.error, res4.error
        res5 = asyncio.run(emu_routing.EmuRoutingTool().invoke({"train": "北京南"}))
        assert res5.ok is False and "格式不正确" in res5.error, res5.error
        print("[PASS] 车次/车组串槽位 -> 输入形态校验直接拒绝")
    finally:
        emu_routing.httpx = original  # type: ignore[assignment]


# ---------- 8. 日志净化 ----------


def test_log_label_sanitized():
    from app.api.chat import _safe_label

    evil = "abc\nINFO: 伪造日志行\t\x00"
    got = _safe_label(evil)
    assert "\n" not in got and "\t" not in got and "\x00" not in got, repr(got)
    assert _safe_label(None) == "-"
    assert len(_safe_label("x" * 500)) <= 32
    print(f"[PASS] 日志字段净化 -> {got!r}")


def main():
    test_error_diagnostic_does_not_leak_upstream()
    test_structured_non_object_json_raises_llm_unavailable()
    test_stream_closes_upstream_on_interrupt()
    test_ssrf_guard_blocks_non_public_targets()
    test_response_size_cap_and_truncation_flag()
    test_web_search_relevance_gate_and_baidu_fallback()
    test_emu_routing_series_vs_exact_and_input_validation()
    test_log_label_sanitized()
    print("\nM11.1 审计修复回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
