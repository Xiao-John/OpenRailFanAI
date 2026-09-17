"""通用网页抓取兜底工具。

- `web.fetch`：抓取指定 URL，返回标题与正文摘要（真实实现，走 httpx）。
  含 **SSRF 防护**（拒绝内网/环回/元数据地址与非 http(s) 协议）与
  **响应体上限**（`HTTP_MAX_BYTES`，默认 2MB），并如实告知是否被截断。

搜索能力见 `app/tools/web_search.py`（`web.search`，Bing 中国 + 百度）。
本模块只保留抓取，避免与 `web_search.py` 出现同名 `web.search` 的两份实现。
"""
from __future__ import annotations

from app.tools._http import (
    UnsafeUrlError,
    classify_http_error,
    extract_title,
    get_text_ex,
    html_to_text,
)
from app.tools.base import Tool, ToolResult


async def fetch_page(url: str, *, max_chars: int = 3000) -> dict:
    """抓一个页面并抽取正文摘要。**不抛异常**，失败如实返回原因。

    返回 `{ok, url, title, text, truncated, error}`。

    为什么做成独立函数（而不是只留在 `WebFetchTool.invoke` 里）：
    `web.fetch` 工具与 `web.search` 命中后的"顺手读正文"（见 `web_search.py`）要的是
    **同一件事** —— SSRF 校验、响应体上限、去标签、按字数截断、如实标注截断。
    两份实现迟早漂移，而"截断没标注"恰好是本项目明确禁止的那类失真
    （把"我没读完"说成"原文就这么短"）。
    """
    try:
        html, truncated_body = await get_text_ex(url)
    except UnsafeUrlError as e:
        # 安全策略必须"拒绝并说明"，不能降级成普通网络失败
        return {"ok": False, "url": url, "title": "", "text": "",
                "truncated": False, "error": f"URL 被安全策略拒绝：{e}"}
    except Exception as e:  # noqa: BLE001 —— 调用方自行降级
        # ⚠️ 用 classify_http_error 归类，而不是把原始异常串出来：
        # httpx 的 HTTPStatusError 文本里带着整条 URL 和 MDN 链接，几百字符，
        # 进了事实块就是纯噪声（实测 403 一次能刷屏 3 行）。
        kind, zh = classify_http_error(e)
        return {"ok": False, "url": url, "title": "", "text": "",
                "truncated": False, "error": f"{kind}：{zh}"}

    text = html_to_text(html, max_len=max_chars)
    # 两种截断都要认：html_to_text 超字数时以省略号结尾；响应体也可能撞上 2MB 上限
    cut_by_chars = text.endswith("\u2026")
    return {
        "ok": True,
        "url": url,
        "title": extract_title(html),
        "text": text,
        "truncated": bool(cut_by_chars or truncated_body),
        "error": "",
    }


class WebFetchTool(Tool):
    name = "web.fetch"
    description = "抓取指定网页 URL，返回标题与正文摘要（兜底检索）"

    async def invoke(self, params: dict) -> ToolResult:
        url = (params.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, error="缺少参数 url")
        page = await fetch_page(url)
        if not page["ok"]:
            note = ("仅允许抓取公网 http/https 地址（禁止内网/环回/云元数据地址）"
                    if "安全策略" in page["error"] else "网络或目标站点拒绝")
            return ToolResult(ok=False, error=page["error"], note=note)

        return ToolResult(
            ok=True,
            data={"title": page["title"], "truncated": page["truncated"]},
            text=page["text"],
            sources=[url],
            note=("正文超过字数上限或响应体超过大小上限，已截断，摘要可能不完整"
                  if page["truncated"] else "网页正文摘要"),
        )
