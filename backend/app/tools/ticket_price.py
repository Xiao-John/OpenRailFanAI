"""12306 票价查询工具。票价与实时余票是不同接口、不同事实。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import os

from app.dates import date_note, normalize_date
from app.tools import _rt12306 as rt
from app.tools._http import format_error
from app.tools.base import Tool, ToolResult
from app.ticket_copy import FARE_REFERENCE, join_clause


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
        main = os.environ.get("APP_VARIANT", "main").lower() != "lm"
        basis = params.get("fare_basis") or "executed"
        if main and basis not in {"executed", "published"}:
            return ToolResult(ok=False, error="不支持的票价口径")
        try:
            if main and basis == "published":
                from app.data.dict import db_path
                from app.data.published_fares import lookup
                await rt.ensure_loaded()
                origin = await rt.resolve_station_code(from_station)
                destination = await rt.resolve_station_code(to_station)
                if not origin or not destination:
                    return ToolResult(ok=False, error="无法确认公布票价的实际区间")
                payload = lookup(db_path(), train_code, origin[1], destination[1], date_str)
                if not payload:
                    return ToolResult(ok=False, error="本地词典暂无该车次、区间及有效期内的公布票价；未发起网络查询")
            else:
                payload = await rt.query_ticket_prices(from_station, to_station, date_str, train_code)
        except Exception as exc:  # noqa: BLE001
            if main and basis == "published":
                return ToolResult(
                    ok=False,
                    error=f"未取得公布参考票价：{format_error(exc)}",
                    note="本地词典公布票价数据源暂不可用。",
                )
            return ToolResult(
                ok=False,
                error=f"12306 票价查询失败：{format_error(exc)}",
                note="实际执行票价未取得；未使用公布票价替代。" if main and basis == "executed" else "票价接口暂不可用；票价查询不代表余票状态。",
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
                ((f"{seat} 未返回有效金额" if price is None else f"{seat} {display_price(price)} 元")
                 if main else f"{seat} {display_price(price)} 元") for seat, price in prices.items()
            ) or "接口未返回票价"
            lines.append(
                f"{row.get('train_code')} "
                + (f"{row.get('from_station')}→{row.get('to_station')} " if os.environ.get("APP_VARIANT", "main").lower() != "lm" else "")
                + (f"{row.get('start_time') or '--'}–{row.get('arrive_time') or '--'}"
                   f"（历时 {row.get('duration') or '未知'}）：{price_text}" if main else
                   f"{row.get('start_time')}–{row.get('arrive_time')}（历时 {row.get('duration', '未知')}）：{price_text}")
            )
        if not rows:
            lines.append(f"未查到{train_code + ' 次' if train_code else ''}车次票价记录。")
        lines.append(FARE_REFERENCE)
        note = date_note(raw_date, date_str)
        if main:
            lines.append("以上为实际执行票价，以购票提交时价格为准。" if basis == "executed" else "以上为词典公布参考票价，不是当前购票执行价。")
        berth_note = ""
        if main and basis == "executed" and any(any(seat in (row.get("prices") or {}) for seat in ("硬卧", "软卧", "高级软卧", "动卧")) for row in rows):
            berth_note = "卧铺为接口返回的席别价格，未细分上、中、下铺。"
            lines.append(berth_note)
        source = ("https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice" if main and basis == "executed"
                  else "https://kyfw.12306.cn/otn/leftTicketPrice/queryAllPublicPrice")
        return ToolResult(
            ok=True,
            data={**payload, "train_code_filter": train_code or None,
                  **({"query_date": date_str, "fare_basis": basis} if main else {})},
            text="\n".join(lines),
            sources=payload.get("sources", [source]) if main and basis == "published" else [source],
            note=join_clause(join_clause(note, ("12306 实际执行票价。" if basis == "executed" else "词典公布参考票价。") if main else "票价可作为购票参考。"), berth_note),
            total=len(rows), shown=len(rows), fetched_at=(payload.get("fetched_at", "") if main and basis == "published" else datetime.now(timezone.utc).isoformat() if main
                                                          else rt._now_iso() if hasattr(rt, "_now_iso") else ""),
        )
