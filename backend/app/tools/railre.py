"""rail.re（原 moerail.ml）页面抓取工具（best-effort / 已降级）。

⚠️ 现状说明（M11.1 核实）
    rail.re 主站是**前端渲染的 SPA**：服务端返回的 HTML 里几乎没有正文，
    且除 `/` 与 `/links` 外的路径（如 `/北京`）实测**恒 404**。
    因此"按车站名抓页面"这一设计用途实际不可用 ——
    真正可用的交路数据来自 **`api.rail.re`**（见 `emu_routing.py` 的 `emu.routing`）。

本工具现在的行为约定：
1. **必须给关键词**：空关键词不再抓首页（否则会把首页 JS 噪声当成"查到了内容"返回 ok=True）。
2. **无有效正文即失败**：剥离 script/style 后若没有中文正文，明确返回 ok=False，
   而不是把 SPA 外壳当结果。
3. 车次/车组/站名的正则抽取保留，仅在页面确实含正文时使用。
"""
from __future__ import annotations

import re
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

# 正文中最少需要出现的中文字符数，低于此值视为"无有效正文（SPA 外壳）"
_MIN_CJK_CHARS = 30
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class RailReTool(Tool):
    name = "railre"
    description = "rail.re 页面抓取（best-effort；交路数据请改用 emu.routing）"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        base = settings.railre_base.rstrip("/")
        keyword = (params.get("target") or params.get("station") or "").strip()

        if not keyword:
            return ToolResult(
                ok=False,
                error="缺少参数：station（车站名）或 target",
                note=(
                    "为避免把 rail.re 首页（前端渲染的 SPA 外壳）当作查询结果，"
                    "本工具要求提供关键词；若要查车次↔车组，请用 emu.routing（api.rail.re）"
                ),
            )

        # 关键词为用户可控输入 → 转义后再拼路径（防 `../` 穿越与查询注入）
        url = f"{base}/{quote(keyword, safe='')}"
        try:
            html = await get_text(url)
        except Exception as e:  # noqa: BLE001
            category, reason = classify_http_error(e)
            return ToolResult(
                ok=False,
                error=f"rail.re 抓取失败: {format_error(e)}",
                note=(
                    f"rail.re 页面不可用（{category}）：{reason}；"
                    "rail.re 主站为前端渲染站点，仅 / 与 /links 可用，"
                    "精确交路请改用 emu.routing（api.rail.re）"
                ),
            )

        body = html_to_text(html, max_len=20000)
        cjk_count = len(_CJK_RE.findall(body))
        if cjk_count < _MIN_CJK_CHARS:
            return ToolResult(
                ok=False,
                error=f"rail.re 页面无有效正文（keyword={keyword}）",
                note=(
                    "rail.re 为前端渲染站点，服务端 HTML 不含该页正文；"
                    "该工具已降级，交路/担当车组请使用 emu.routing（api.rail.re）"
                ),
            )

        # 提取车组号（如 CR400AF-2001, CRH2A-2034 等）
        emu_pattern = re.compile(
            r"(CR(H|400|300|200)[0-9A-Za-z]*[-_][0-9]{3,5}[A-Za-z]?)",
            re.IGNORECASE,
        )
        emu_matches = list({m.group(0) for m in emu_pattern.finditer(body)})

        train_matches = list({
            m.group(0).upper()
            for m in re.finditer(r"\b([GCDZKTLS]\d{1,6})\b", body)
        })

        station_matches = list({
            m.group(0)
            for m in re.finditer(r"([\u4e00-\u9fa5]{2,6}(?:站|西|南|北|东))", body)
        })

        summary_parts = []
        if emu_matches:
            summary_parts.append(f"车组号：{', '.join(sorted(emu_matches)[:10])}")
        if train_matches:
            summary_parts.append(f"车次：{', '.join(sorted(train_matches)[:15])}")

        return ToolResult(
            ok=True,
            data={
                "title": extract_title(html),
                "keyword": keyword,
                "emu_matches": sorted(emu_matches),
                "train_matches": sorted(train_matches),
                "station_matches": sorted(station_matches),
            },
            text="\n".join(summary_parts) or body[:1000],
            sources=[url],
            note="rail.re 页面 best-effort 抓取（正文有限；交路数据以 emu.routing 为准）",
        )
