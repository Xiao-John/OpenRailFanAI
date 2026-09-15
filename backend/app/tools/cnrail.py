"""cnrail.geogv.org —— 中国铁路地图（站名→地图外链）工具。

v1 定位：按站名/关键词生成可点击的铁路地图外链（无需实时抓取），
供“拍摄点/车站”类回答附录地图入口。结果总是可用。
"""
from __future__ import annotations

from urllib.parse import quote

from app.config import get_settings
from app.tools.base import Tool, ToolResult


class CnRailTool(Tool):
    name = "cnrail.map"
    description = "按站点名生成 cnrail.geogv.org 铁路地图外链"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        station = (params.get("station") or params.get("location") or "").strip()
        base = settings.cnrail_base.rstrip("/")
        # 站名是用户可控输入：必须转义后再拼进路径，
        # 否则 `../` 可穿越路径、`?a=b` 可注入查询参数（审计实测）。
        safe_station = quote(station, safe="")
        url = f"{base}/zh/{safe_station}" if safe_station else f"{base}/zh"
        return ToolResult(
            ok=True,
            data={"station": station or None, "map_url": url},
            text=(
                f"中国铁路地图入口：{url}"
                "（仅生成外链，不含实时数据；地图瓦片由 railmap.geogv.org 提供）"
            ),
            sources=[url],
            note="地图外链（非实时数据）",
        )
