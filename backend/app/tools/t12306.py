"""12306 余票/时刻查询工具。

官方查询接口（kyfw.12306.cn）需浏览器 Cookie / 反代，无法直连成功；
参考 metromancn/Parse12306 的请求与解析。M3 实现：
- 配置了 T12306_BASE（用户自备反代/Cookie 服务）时，真实请求 leftTicket 查询。
- 未配置时为优雅停用，返回附注并提示配置，避免 Agent 误判成功。

leftTicket 查询端点形态（各反代实现可能不同）：
    {T12306_BASE}/leftTicket/query
    ?leftTicketDTO.train_date=YYYY-MM-DD&leftTicketDTO.from_station_code=XXX
    &leftTicketDTO.to_station_code=XXX&purpose_codes=ADULT
"""
from __future__ import annotations

from datetime import date

from app.config import get_settings
from app.tools._http import format_error, get_client
from app.tools.base import Tool, ToolResult


class T12306Tool(Tool):
    name = "t12306.search_tickets"
    description = "查询 12306 列车余票与时刻（需配置 T12306_BASE 反代/Cookie）"

    @property
    def enabled(self) -> bool:
        return bool(get_settings().t12306_base)

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        base = (settings.t12306_base or "").rstrip("/")
        if not base:
            return ToolResult(
                ok=False,
                error="未配置 T12306_BASE",
                note="请配置自备的 12306 查询反代/Cookie 服务后启用；否则无法取到实时余票。",
            )

        train_date = params.get("date") or date.today().isoformat()
        q: dict = dict(
            leftTicketDTO={"train_date": train_date},
            purpose_codes="ADULT",
        )
        if params.get("from_station"):
            q["leftTicketDTO"]["from_station_code"] = params["from_station"]
        if params.get("to_station"):
            q["leftTicketDTO"]["to_station_code"] = params["to_station"]

        try:
            client = await get_client()
            resp = await client.get(
                f"{base}/leftTicket/query",
                params=q,
                headers={"User-Agent": "RailFanAI/0.3"},
                timeout=settings.http_timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"12306 查询失败: {format_error(e)}",
                note="依赖自备反代/Cookie；接口异常或未配置。",
            )
        return ToolResult(
            ok=True,
            data=payload,
            text=str(payload)[:1500],
            sources=[f"{base}/leftTicket/query"],
            note="依赖用户自备 12306 反代服务",
        )
