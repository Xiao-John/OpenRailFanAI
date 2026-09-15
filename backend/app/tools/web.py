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
    extract_title,
    format_error,
    get_text_ex,
    html_to_text,
)
from app.tools.base import Tool, ToolResult


class WebFetchTool(Tool):
    name = "web.fetch"
    description = "抓取指定网页 URL，返回标题与正文摘要（兜底检索）"

    async def invoke(self, params: dict) -> ToolResult:
        url = (params.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, error="缺少参数 url")
        try:
            html, truncated = await get_text_ex(url)
        except UnsafeUrlError as e:
            # 安全策略必须"拒绝并说明"，不能降级成普通网络失败
            return ToolResult(
                ok=False,
                error=f"URL 被安全策略拒绝：{e}",
                note="仅允许抓取公网 http/https 地址（禁止内网/环回/云元数据地址）",
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"抓取失败: {format_error(e)}", note="网络或目标站点拒绝")

        return ToolResult(
            ok=True,
            data={"title": extract_title(html), "truncated": truncated},
            text=html_to_text(html),
            sources=[url],
            note="响应体超过大小上限，内容已截断，摘要可能不完整" if truncated else "网页正文摘要",
        )
