"""额外数据源工具（M3.1 新增）。

- jprailfan.com  —— 黄河铁路网（中国/日本铁路信息、客里表）
- 95306.cn       —— 营业站货运服务信息
- kmrail.cn      —— 昆铁货运（全路各站货运范围、停限装公告）
- kyfw.sytlj.com —— 沈阳铁路局余票/时刻查询

均为 best-effort 抓取，失败时优雅降级。

M11.1 修复（对应 `审计报告（历史）` P1-4 / P1-5）
------------------------------------------------------------------
1. **不再硬编码域名**：一律取 `config.py` 的 `*_base`（此前这些配置是死配置，
   域名失效时无法热修）。
2. **失败原因归因准确**：用 `classify_http_error()` 区分
   TLS 证书 / DNS / 超时 / 连接 / HTTP 状态，而不是一律说"站点可能不可达或需要特殊网络环境"。
   这些 note 会经 `retrieve.py` 拼进用户可见的可靠性说明，写错等于误导。
3. **如实标注长期可用性**：95306 / kmrail / sytlj 实测长期不可用
   （证书校验失败 / 连接超时），失败时明确提示"该源可能已下线，请勿据此判断数据缺口原因"。
"""
from __future__ import annotations

from urllib.parse import quote

from app.config import get_settings
from app.tools._http import (
    classify_http_error,
    extract_title,
    format_error,
    get_text,
    html_to_text,
)
from app.tools.base import Tool, ToolResult

# 实测长期不可用的源（失败时给出更明确提示，避免被误解为"网络受限"）
_DEGRADED_HINT = "该数据源实测长期不可用（可能已下线/更换接口），仅供参考，不代表网络受限"


def _fail(source: str, e: Exception, extra: str = "") -> ToolResult:
    """统一失败返回：错误分类 + 如实 note。"""
    category, reason = classify_http_error(e)
    note = f"{source} 不可用（{category}）：{reason}"
    if extra:
        note += f"；{extra}"
    return ToolResult(ok=False, error=f"{source} 抓取失败: {format_error(e)}", note=note)


# ---- jprailfan.com 黄河铁路网 ----

class JpRailFanTool(Tool):
    name = "jprailfan"
    description = "黄河铁路网（jprailfan.com）客里表/电报码/拼音码查询（best-effort）"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        keyword = (params.get("station") or params.get("keyword") or "").strip()
        url = settings.jprailfan_base.rstrip("/")
        if keyword:
            url += f"/?s={quote(keyword)}"
        try:
            html = await get_text(url)
        except Exception as e:  # noqa: BLE001
            return _fail("黄河铁路网", e)
        return ToolResult(
            ok=True,
            data={"title": extract_title(html), "keyword": keyword or None},
            text=html_to_text(html),
            sources=[url],
            note="黄河铁路网 best-effort 抓取（页面内容非结构化，仅作参考）",
        )


# ---- 95306.cn 营业站服务信息 ----

class Freight95306Tool(Tool):
    name = "freight.95306"
    description = "95306.cn 营业站货运服务信息查询（中国铁路总公司官方）"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        keyword = (params.get("station") or params.get("code") or params.get("tmis") or "").strip()
        url = settings.freight_95306_base.rstrip("/")
        if keyword:
            # 说明：具体查询接口未确认，此处仅按站点搜索路径 best-effort 尝试
            url += f"/search?q={quote(keyword)}"
        try:
            html = await get_text(url)
        except Exception as e:  # noqa: BLE001
            return _fail("95306.cn", e, _DEGRADED_HINT)
        return ToolResult(
            ok=True,
            data={"title": extract_title(html), "keyword": keyword or None},
            text=html_to_text(html),
            sources=[url],
            note="95306.cn best-effort 抓取（查询接口未确认，结果为页面摘要）",
        )


# ---- kmrail.cn 昆铁货运 ----

class KmRailTool(Tool):
    name = "kmrail.freight"
    description = "昆铁货运（kmrail.cn）全路货运范围/停限装公告（best-effort）"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        keyword = (params.get("station") or params.get("keyword") or "").strip()
        url = settings.kmrail_base.rstrip("/")
        if keyword:
            url += f"/?s={quote(keyword)}"
        try:
            html = await get_text(url)
        except Exception as e:  # noqa: BLE001
            return _fail("昆铁货运", e, _DEGRADED_HINT)
        return ToolResult(
            ok=True,
            data={"title": extract_title(html), "keyword": keyword or None},
            text=html_to_text(html),
            sources=[url],
            note="kmrail.cn best-effort 抓取（内容主要经微信公众号发布，网页端信息有限）",
        )


# ---- kyfw.sytlj.com 沈阳铁路局余票/时刻 ----

class SytljTicketTool(Tool):
    name = "sytlj.ticket"
    description = "沈阳铁路局余票/时刻查询（kyfw.sytlj.com，微信公众号提供）"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        keyword = (params.get("train") or params.get("station") or "").strip()
        url = settings.sytlj_base.rstrip("/")
        if keyword:
            url += f"/?q={quote(keyword)}"
        try:
            html = await get_text(url)
        except Exception as e:  # noqa: BLE001
            return _fail("沈阳铁路局余票查询", e, _DEGRADED_HINT + "；该源实际经微信公众号接口提供")
        return ToolResult(
            ok=True,
            data={"title": extract_title(html), "keyword": keyword or None},
            text=html_to_text(html),
            sources=[url],
            note="kyfw.sytlj.com best-effort 抓取（正式数据经微信公众号接口，网页端信息有限）",
        )
