"""Main ticket replies from query receipts, without inventing railway facts."""
from __future__ import annotations

import os

from app.tools.base import ToolResult
from app.ticket_copy import AVAILABILITY_SNAPSHOT, seat_label


def direct_answer(retrieval: dict) -> str:
    """Internal delivery hint; never a new client field or an LM behavior change."""
    if os.environ.get("APP_VARIANT", "main").lower() == "lm":
        return ""
    value = retrieval.get("direct_answer")
    return value if isinstance(value, str) else ""


def direct_complete(retrieval: dict) -> bool:
    """An empty body can still be a completed deterministic structured delivery."""
    return os.environ.get("APP_VARIANT", "main").lower() != "lm" and (
        retrieval.get("direct_complete") is True or bool(direct_answer(retrieval)))


def missing_interval(train: str | None, date: str | None) -> str:
    subject = " ".join(filter(None, [date, train]))
    return (
        f"查询{(' ' + subject) if subject else ''}余票还缺少完整区间："
        "请补充实际乘车的出发站和到达站。\n\n"
        "余票随乘车区间变化，不能用列车全程或时刻表代替。"
        "例如：北京南到上海虹桥。"
    )


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render(params: dict, result: ToolResult) -> str:
    """Copy seat states verbatim; empty/missing values do not mean sold out."""
    scope = f"{params.get('from_station', '')}→{params.get('to_station', '')}"
    subject = " ".join(filter(None, [params.get("date"), params.get("train"), scope]))
    if not result.ok:
        parts = [f"未取得 {subject} 的余票结果：{result.error or '接口未提供结果'}"]
        if result.note:
            parts.append(result.note)
        if result.sources:
            parts.append("来源：" + "、".join(result.sources))
        return "\n\n".join(parts)

    data = result.data if isinstance(result.data, dict) else {}
    rows = data.get("trains") if isinstance(data.get("trains"), list) else []
    query_day = str(data.get("train_date") or params.get("date") or "")
    parts = [f"{query_day} {scope} 余票查询结果"]
    if rows:
        table = ["| 车次 | 席别余票 |", "| --- | --- |"]
        for row in rows:
            if not isinstance(row, dict):
                continue
            seats = row.get("seats") if isinstance(row.get("seats"), dict) else {}
            values = []
            for key, value in seats.items():
                label = seat_label(key)
                state = "未提供" if value is None or str(value).strip() in {"", "--"} else str(value)
                values.append(f"{label}：{state}")
            seat_text = "；".join(values) or "席别余票数据缺失，无法判断是否有票"
            table.append(f"| {_cell(row.get('train_no') or '未提供车次')} | {_cell(seat_text)} |")
        parts.append("\n".join(table))
    else:
        parts.append("接口未提供可展示的席别余票数据，不能据此判断有票或无票。")
    # Completeness and freshness are part of the receipt, not model prose.
    if result.total is not None:
        shown = result.shown if result.shown is not None else len(rows)
        parts.append(f"命中 {result.total} 趟，已展示 {shown} 趟。")
    if result.truncated:
        parts.append("结果已截断，以上不是全部车次；可缩小区间或筛选条件后重查。")
    if result.filters:
        parts.append("筛选条件：" + "、".join(f"{k}={v}" for k, v in result.filters.items()))
    if result.fetched_at:
        parts.append(f"采样时刻：{result.fetched_at}")
    if result.note:
        parts.append(result.note)
    if AVAILABILITY_SNAPSHOT not in (result.note or ""):
        parts.append(AVAILABILITY_SNAPSHOT)
    if result.sources:
        parts.append("来源：" + "、".join(result.sources))
    return "\n\n".join(parts)
