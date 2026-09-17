"""共享 HTTP 抓取助手：带超时 / SSRF 防护 / 响应体上限 / 简单 HTML 文本抽取。

默认使用浏览器级请求头以避免被目标站点反爬拦截。
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

from app.config import get_settings

_log = logging.getLogger("railfan.http")

_ALLOWED_SCHEMES = ("http", "https")

# 进程内共享的抓取 client（懒建，见 `get_client`）。
# `_client_loop` 记录创建它的事件循环：连接池的锁绑定在循环上，跨循环复用会直接报错。
_client: httpx.AsyncClient | None = None
_client_loop = None


class UnsafeUrlError(ValueError):
    """URL 指向非公网目标（环回/内网/链路本地/保留地址）或 scheme 不被允许。"""


def _is_public_ip(addr: str) -> bool:
    """判断解析出的 IP 是否为公网地址（用于阻断 SSRF）。"""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])   # 去掉 IPv6 zone id
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def assert_public_url(url: str) -> None:
    """校验 URL 可安全抓取：仅 http/https，且解析出的**所有**地址都是公网地址。

    拦截 SSRF 的经典目标：127.0.0.1、10/172.16/192.168 内网、
    169.254.169.254 云元数据、file://、gopher:// 等。

    已知局限：校验与真实连接之间存在 DNS 重绑定（TOCTOU）窗口——
    对"用户直接给 URL"的抓取场景可接受；若未来接入 Agent 自动调用链，
    应改为固定解析结果再连接（transport 级 pin）。
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"不支持的 URL 协议：{parsed.scheme or '(空)'}（仅允许 http/https）")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"域名无法解析：{host}（{e}）") from e
    for info in infos:
        addr = info[4][0]
        if not _is_public_ip(addr):
            raise UnsafeUrlError(
                f"拒绝抓取非公网地址：{host} 解析为 {addr}（内网/环回/元数据地址）"
            )


# 模拟浏览器请求头，避免被 rail.re / 12306 等站点反爬
# 注意：Accept-Encoding 含 br 时需要 brotli 解压库；缺失则自动降级为 gzip/deflate，
# 否则会拿到无法解压的原始字节（曾导致 web.fetch 返回乱码）。
try:
    import brotli as _brotli  # noqa: F401

    _ENCODINGS = "gzip, deflate, br"
except ImportError:
    _ENCODINGS = "gzip, deflate"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": _ENCODINGS,
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
}


async def aclose_client() -> None:
    """关闭共享 client（FastAPI lifespan 结束时调用，避免"未关闭的 client"告警）。"""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001 —— 关闭失败不该影响进程退出
            pass
    _client = None
    _client_loop = None


async def get_client() -> httpx.AsyncClient:
    """进程内**共享**的 `httpx.AsyncClient`（懒建）。

    为什么共享：此前 11 处各自 `async with httpx.AsyncClient(...)`，每次工具调用都要重付
    TCP 三次握手 + TLS 握手（境内站点约 100–300 ms）；并发下几个工具各开各的池子，
    谁也复用不了谁 —— `photo_spot` 那种 4 工具计划里，省的是"最慢那条"的墙钟时间。

    语义约定（改这里之前先读 `docs/run.md:153`）：
    - `trust_env=False`：**不继承环境/系统代理**。这既是 `assert_public_url` 的 SSRF 校验不被绕过
      的前提，也是让抓取行为可预期（macOS 开着系统代理时环回请求会被塞进代理，报莫名的 502）。
      这是全项目抓取层的既有约定，改成按调用点区分做不到（http2/代理是 client 级选项）；
    - **不设 client 默认 headers**：各调用点历史 UA / Referer 差异很大（浏览器 UA、`RailFanAI/0.3`、
      12306 专用 Referer），一律由调用方显式 `headers=` 传入，避免"共享后悄悄多出几个头"；
    - `http2=True`：原本 12306 的 kyfw 通道就是 h2，其余站点走 ALPN 协商，
      服务端不支持时 httpx 自动回落 http/1.1；
    - 超时用 client 默认值，**调用点可按需覆盖**（`client.get(..., timeout=10)`），
      这样各站点原有的 10/12/15/30/120 s 分级保持原样。
    """
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is not None and _client_loop is not loop:
        # 连接池内部锁绑定在创建它的事件循环上，**不能跨循环复用**。
        # 测试与脚本会反复 asyncio.run()（每次新循环）→ 这里直接丢弃旧 client 重建。
        # 旧循环已死，无法 await aclose()，交给 GC 回收即可。
        _client = None
    if _client is None or _client.is_closed:
        kwargs: dict = {
            "timeout": get_settings().http_timeout,
            "follow_redirects": True,
            "trust_env": False,
            "limits": httpx.Limits(
                max_connections=20, max_keepalive_connections=10, keepalive_expiry=90.0
            ),
        }
        try:
            _client = httpx.AsyncClient(http2=True, **kwargs)
        except ImportError:
            # 缺 `h2` 包时 http2=True 会直接抛 ImportError。这条路径一旦被漏掉，
            # 表现是"所有工具都连不上"——比慢一点严重得多，所以这里兜住并**留日志**：
            # 静默降级会变成"只是慢一点"，没人查得出来（Android 是 --no-deps 安装，
            # 最容易漏依赖，见 android/requirements.txt 与 tests/test_android.py）。
            _log.warning("缺少 h2 包，抓取层退回 HTTP/1.1（检查 android/requirements.txt 是否锁定 h2）")
            _client = httpx.AsyncClient(http2=False, **kwargs)
        _client_loop = loop
    return _client


async def get_text(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    max_bytes: int | None = None,
    allow_private: bool = False,
) -> str:
    """GET 一个 URL 并返回 HTML/文本（带 SSRF 校验与响应体上限）。

    - 默认仅允许公网 http/https（见 `assert_public_url`）
    - 默认最多读取 `HTTP_MAX_BYTES`（默认 2MB）字节：实测一个 172MB 页面
      曾让进程 RSS 冲到 920MB
    - 失败抛 httpx 相关异常或 `UnsafeUrlError`，由调用方降级
    """
    return (await get_text_ex(
        url, headers=headers, params=params, max_bytes=max_bytes, allow_private=allow_private
    ))[0]


async def get_text_ex(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    max_bytes: int | None = None,
    allow_private: bool = False,
) -> tuple[str, bool]:
    """同 `get_text`，但额外返回"是否因超限被截断"，供工具层如实告知用户。"""
    settings = get_settings()
    limit = int(max_bytes if max_bytes is not None else settings.http_max_bytes)
    if not allow_private:
        assert_public_url(url)

    merged = dict(BROWSER_HEADERS)
    if headers:
        merged.update(headers)
    truncated = False
    # ⚠️ 共享 client 上 trust_env=False：**本函数带 SSRF 防护，绝不能走环境/系统代理**。
    # 原因有二：
    #   1) 走代理后 `assert_public_url` 的校验形同虚设 —— 真正发起连接的是代理，
    #      它完全可以访问我们刚拦下的内网地址（防护被绕过）；
    #   2) macOS 系统代理一旦开启（含本机代理软件），环回地址请求也会被塞进代理，
    #      本地服务返回 502，表现为"莫名其妙的抓取失败"（实测踩过）。
    # 需要代理时应显式配置，而不是隐式继承环境。
    client = await get_client()
    async with client.stream(
        "GET", url, params=params, headers=merged, timeout=settings.http_timeout
    ) as resp:
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            if total + len(chunk) > limit:
                chunks.append(chunk[: max(0, limit - total)])
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        encoding = resp.encoding or "utf-8"
    if truncated:
        _log.warning("响应体超过 %d 字节上限，已截断：%s", limit, url)
    return raw.decode(encoding, errors="replace"), truncated


def format_error(e: Exception) -> str:
    """把异常格式化为可读信息。

    部分 httpx 异常（如 ConnectTimeout）的 str() 为空串，
    直接 f"{e}" 会得到空白错误信息，故统一带上异常类型名。
    """
    msg = str(e).strip()
    if msg:
        return f"{type(e).__name__}: {msg}"
    return type(e).__name__


def classify_http_error(e: Exception) -> tuple[str, str]:
    """把抓取异常归类为 (category, 中文说明)。

    早期各工具把一切失败都写成"站点可能不可达或需要特殊网络环境"，
    掩盖了真正原因（证书过期 / 域名已下线 / 超时 / HTTP 拒绝），
    而这些 note 会经 `retrieve.py` 直达用户可见的可靠性说明 → 必须归因准确。
    """
    raw = f"{type(e).__name__}: {e}".lower()
    if "certificate" in raw or "ssl" in raw or "tls" in raw:
        return ("tls", "TLS 证书校验失败（证书可能自签/过期/域名不匹配）——通常表示站点已变更或不再对外提供 HTTPS")
    if "timeout" in raw or "timed out" in raw:
        return ("timeout", "连接或读取超时（站点无响应/被限流/需特定网络出口）")
    if "nodename" in raw or "name or service not known" in raw or "gaierror" in raw:
        return ("dns", "域名解析失败（域名可能已停用）")
    if "connect" in raw:
        return ("connect", "无法建立连接（站点可能已下线或屏蔽了当前出口）")
    status = getattr(e, "response", None)
    if status is not None:
        return ("http", f"站点返回 HTTP {getattr(status, 'status_code', '?')}")
    return ("other", f"抓取失败（{format_error(e)}）")


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)


def html_to_text(html: str, *, max_len: int = 3000) -> str:
    """极简 HTML->文本：先剥离 script/style，再去标签、合并空行、截断。

    剥离 script/style 很重要：否则 JS/CSS 会混进工具输出与模型上下文
    （既污染事实块，也是一条间接 prompt 注入面）。
    """
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = text.strip()
    if len(text) <= max_len:
        return text
    # 在 max_len 附近找合理的断点（标点/空格），避免切断中文词
    truncated = text[: max_len + 1]
    break_chars = " \n。，！？；：、.!?;:"
    for i in range(min(len(truncated) - 1, max_len), max_len // 2, -1):
        if truncated[i] in break_chars:
            return truncated[:i].rstrip() + "\u2026"
    return text[:max_len].rstrip() + "\u2026"


TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def extract_title(html: str) -> str:
    m = TITLE_RE.search(html)
    return (m.group(1).strip() if m else "") or "(无标题)"


async def post_text(
    url: str,
    data: dict,
    *,
    headers: dict | None = None,
    max_bytes: int | None = None,
    allow_private: bool = False,
    timeout: float | None = None,
) -> str:
    """POST 表单并返回文本（黄河铁路网「指定径路查询」是 POST 表单）。

    与 `get_text` 共用 SSRF 校验与响应体上限；失败抛 httpx 异常，由调用方降级。
    """
    settings = get_settings()
    limit = int(max_bytes if max_bytes is not None else settings.http_max_bytes)
    if not allow_private:
        assert_public_url(url)

    merged = dict(BROWSER_HEADERS)
    merged["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        merged.update(headers)
    # ⚠️ 共享 client 上 trust_env=False：**本函数带 SSRF 防护，绝不能走环境/系统代理**。
    # 原因见 `get_text_ex` 里的同一段说明。
    timeout_s = timeout or max(settings.http_timeout, 30.0)
    client = await get_client()
    async with client.stream(
        "POST", url, data=data, headers=merged, timeout=timeout_s
    ) as resp:
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            if total + len(chunk) > limit:
                chunks.append(chunk[: max(0, limit - total)])
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        encoding = resp.encoding or "utf-8"
    return raw.decode(encoding, errors="replace")
