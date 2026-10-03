"""12306 票价查询工具。票价与实时余票是不同接口、不同事实。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.dates import date_note, normalize_date
from app.tools import _rt12306 as rt
from app.tools._http import format_error
from app.tools.base import Tool, ToolResult


class TicketPriceTool(Tool):
    name = "ticket.price"
    description = "查询 12306 车票票价（不代表当前有票）"

    async def invoke(self, params: dict) -> ToolResult:
        from_station = str(params.get("from_station") or params.get("from") or "").strip()
        to_station = str(params.get("to_station") or params.get("to") or "").strip()
        raw_date = params.get("date") or params.get("time")
        date_str = normalize_date(raw_date)
        train_code = str(params.get("train") or params.get("train_code") or "").strip().upper()
        if not from_station or not to_station:
            return ToolResult(ok=False, error="查询票价需要出发站和到达站")
        if not date_str:
            return ToolResult(ok=False, error="无法识别乘车日期")
        try:
            payload = await rt.query_ticket_prices(from_station, to_station, date_str, train_code)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"12306 票价查询失败：{format_error(exc)}",
                note="票价接口暂不可用；票价查询不代表余票状态。",
            )

        rows = payload.get("data") or []
        lines = [f"{payload.get('from_station', from_station)}→{payload.get('to_station', to_station)}（{date_str}）票价："]
        for row in rows:
            prices = row.get("prices") or {}
            def display_price(value: object) -> str:
                try:
                    return format(Decimal(str(value)).normalize(), "f")
                except (InvalidOperation, ValueError):
                    return str(value)

            price_text = "、".join(
                f"{seat} {display_price(price)} 元" for seat, price in prices.items()
            ) or "接口未返回票价"
            lines.append(
                f"{row.get('train_code')} {row.get('start_time')}–{row.get('arrive_time')}"
                f"（历时 {row.get('duration', '未知')}）：{price_text}"
            )
        if not rows:
            lines.append(f"未查到{train_code + ' 次' if train_code else ''}车次票价记录。")
        lines.append("以上为票价信息，不表示该席别当前有票。")
        note = date_note(raw_date, date_str)
        return ToolResult(
            ok=True,
            data={**payload, "train_code_filter": train_code or None},
            text="\n".join(lines),
            sources=["https://kyfw.12306.cn/otn/leftTicketPrice/queryAllPublicPrice"],
            note=(note + "；" if note else "") + "票价来自 12306 票价接口，不代表实时余票。",
            total=len(rows), shown=len(rows), fetched_at=rt._now_iso() if hasattr(rt, "_now_iso") else "",
        )
