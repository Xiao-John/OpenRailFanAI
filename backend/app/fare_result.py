"""Fare display facts from tool receipts; never parse or synthesize model prose."""
from decimal import Decimal, InvalidOperation
import math
from typing import Any
from app.dates import normalize_date
from app.ticket_copy import FARE_REFERENCE, join_clause, seat_label

FARE_NOTE = FARE_REFERENCE + "到发时刻为接口区间时刻，不代表实际运行状态。"


def _raw_amount(value: Any) -> Any:
    # JSON has no non-finite numbers. Retain their spelling as diagnostic text.
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _amount(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return format(number, "f") if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _base(item: dict, data: dict) -> dict:
    context = item.get("query_context") or {}
    requested_date = context.get("date") or context.get("time")
    return {"kind": "ticket_fare", "status": "failed",
            "train_code": context.get("train") or context.get("train_code") or data.get("train_code_filter"),
            "date": (normalize_date(requested_date) if requested_date else data.get("query_date")),
            "from_station": data.get("from_station") or context.get("from_station") or context.get("from"),
            "to_station": data.get("to_station") or context.get("to_station") or context.get("to"),
            "start_time": None, "arrive_time": None, "duration": None, "prices": [],
            "fare_basis": data.get("fare_basis") or context.get("fare_basis") or ("published" if any("queryAllPublicPrice" in s for s in item.get("sources", [])) else "executed"),
            "sources": list(item.get("sources") or []), "fetched_at": item.get("fetched_at") or None,
            "error": None, "note": join_clause(item.get("note"), FARE_NOTE)}


def _project_prices(receipts: list[dict], errors: list[dict]) -> list[dict]:
    results = []
    for item in receipts:
        if item.get("tool") != "ticket.price":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        base = _base(item, data)
        rows = data.get("data")
        if data.get("query_date") and data["query_date"] != base["date"]:
            base["error"] = "票价结果查询日期与请求日期不一致"
            results.append(base)
            continue
        if not isinstance(rows, list):
            base["error"] = "票价接口未返回有效记录列表"
            results.append(base)
            continue
        if not rows:
            base["status"] = "empty"
            base["note"] = join_clause(base["note"], "未找到该查询区间的精确票价记录，不能据此判断停运或无票。")
            results.append(base)
        for row in rows:
            result = dict(base)
            if not isinstance(row, dict):
                result["error"] = "票价接口记录格式无效"
                results.append(result)
                continue
            # Row identity is authoritative. Never copy query OD onto a row.
            for field in ("train_code", "from_station", "to_station", "start_time", "arrive_time", "duration"):
                result[field] = row.get(field) or None
            prices = row.get("prices")
            result["prices"] = [{"seat": seat, "amount": _amount(value), "currency": "CNY",
                                 "raw_amount": _raw_amount(value)} for seat, value in prices.items()] if isinstance(prices, dict) else []
            if not all(result.get(k) for k in ("train_code", "date", "from_station", "to_station")):
                result["error"] = "票价记录缺少车次、查询日期或实际区间"
            elif base["train_code"] and result["train_code"] != base["train_code"]:
                result["error"] = "票价记录车次与查询车次不一致"
            elif row.get("date") and row["date"] != base["date"]:
                result["error"] = "票价记录日期与查询日期不一致"
            else:
                result["status"] = "success" if result["prices"] and all(p["amount"] is not None for p in result["prices"]) else "partial"
                if result["status"] == "partial":
                    result["note"] = join_clause(result["note"], "接口未提供完整有效的席别金额，缺失金额不是0元。")
            if result["error"]:
                # A rejected row is not a fare result for a different query.
                result = {**base, "error": result["error"]}
            results.append(result)
    for item in errors:
        if item.get("tool") == "ticket.price":
            result = _base(item, {})
            result["error"] = item.get("message") or "票价查询失败"
            results.append(result)
    return results


def _identity(item: dict) -> tuple:
    return tuple(item.get(k) for k in ("train_code", "date", "from_station", "to_station"))


def _seat_state(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "unknown"
    raw = str(value).strip()
    if raw == "候补":
        return "waitlist"
    if raw in {"无", "0"}:
        return "unavailable"
    if raw == "有" or (raw.isdigit() and int(raw) > 0):
        return "available"
    return "unknown"


def _availability(item: dict, status: str, *, row: dict | None = None, error: str | None = None) -> dict:
    row = row or {}
    seats = row.get("seats") if isinstance(row.get("seats"), dict) else {}
    values = [{"seat": seat_label(seat), "availability": _raw_amount(raw),
               "raw_value": _raw_amount(raw), "status": _seat_state(raw)} for seat, raw in seats.items()]
    if status == "success" and (not values or any(v["status"] == "unknown" for v in values)):
        status = "partial"
    return {"status": status, "seats": values, "sources": list(item.get("sources") or []),
            "fetched_at": item.get("fetched_at") or None, "error": error,
            "note": item.get("note") or None,
            "start_time": row.get("start_time") or None, "arrive_time": row.get("arrive_time") or None,
            "duration": row.get("duration") or None}


def _project_availability(receipts: list[dict], errors: list[dict]) -> list[dict]:
    results = []
    for item in receipts:
        if item.get("tool") != "ticket.query":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        base = _base(item, data)
        # train_date belongs to the query, not the train's originating calendar day.
        day = data.get("train_date") or item.get("query_date")
        if not base["date"]:
            base["date"] = day
        rows = data.get("trains")
        error = None
        if not day:
            error = "余票结果缺少查询日期"
        elif day != base["date"]:
            error = "余票结果查询日期与请求日期不一致"
        elif not isinstance(rows, list):
            error = "余票接口未返回有效记录列表"
        if error:
            results.append({**base, "availability": _availability(item, "failed", error=error)})
            continue
        if not rows:
            results.append({**base, "availability": _availability(item, "empty")})
        for row in rows:
            result = dict(base)
            error = None
            if not isinstance(row, dict):
                error = "余票接口记录格式无效"
            else:
                result.update(train_code=row.get("train_no") or row.get("train_code"),
                              from_station=row.get("from_station"), to_station=row.get("to_station"))
                if not all(_identity(result)):
                    error = "余票记录缺少车次、查询日期或实际区间"
                elif base["train_code"] and result["train_code"] != base["train_code"]:
                    error = "余票记录车次与查询车次不一致"
                elif row.get("date") and row["date"] != base["date"]:
                    error = "余票记录日期与查询日期不一致"
                elif any(base[k] and result[k] != base[k] for k in ("from_station", "to_station")):
                    error = "余票记录实际区间与查询区间不一致"
            if error:
                result = dict(base)
            result["availability"] = _availability(item, "failed" if error else "success",
                                                     row=None if error else row, error=error)
            if not error:
                for key in ("start_time", "arrive_time", "duration"):
                    result[key] = row.get(key) or None
            results.append(result)
    for item in errors:
        if item.get("tool") == "ticket.query":
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            result = _base(item, data)
            if not result["date"]:
                result["date"] = data.get("train_date") or item.get("query_date")
            status = data.get("availability_status") or item.get("availability_status") or "failed"
            if status not in ("empty", "failed"):
                status = "failed"
            error = item.get("message") or "余票查询失败"
            if data.get("train_date") and data["train_date"] != result["date"]:
                status, error = "failed", "余票结果查询日期与请求日期不一致"
            result["availability"] = _availability(item, status, error=error)
            results.append(result)
    return results


def _combined_status(fare: str, availability: str) -> str:
    if fare == availability == "success":
        return "success"
    if fare == availability == "empty":
        return "empty"
    if fare in {"success", "partial"} or availability in {"success", "partial"}:
        return "partial" if "not_requested" not in {fare, availability} else (
            availability if fare == "not_requested" else fare)
    return "empty" if "empty" in {fare, availability} and "not_requested" in {fare, availability} else "failed"


def project_fares(receipts: list[dict], errors: list[dict]) -> list[dict]:
    """Extend v1 only when an availability receipt exists; join exact identities."""
    fares = _project_prices(receipts, errors)
    tickets = _project_availability(receipts, errors)
    if not tickets:
        return fares
    results = []
    used = set()
    for fare in fares:
        result = dict(fare)
        result["fare_status"] = fare["status"]
        result["fare_error"] = fare["error"]
        key = _identity(fare)
        match = next((i for i, ticket in enumerate(tickets)
                      if i not in used and all(key) and _identity(ticket) == key), None)
        if match is not None:
            used.add(match)
            result["availability"] = tickets[match]["availability"]
            conflicts = [k for k in ("start_time", "arrive_time", "duration")
                         if fare.get(k) and result["availability"].get(k)
                         and fare[k] != result["availability"][k]]
            if conflicts:
                result["availability"]["time_discrepancy"] = conflicts
                result["availability"]["note"] = join_clause(result["availability"].get("note"),
                    "票价与余票接口区间时刻不一致，分别保留原始值。")
        else:
            result["availability"] = _availability({}, "not_requested")
        result["status"] = _combined_status(result["fare_status"], result["availability"]["status"])
        results.append(result)
    for i, ticket in enumerate(tickets):
        if i in used:
            continue
        result = dict(ticket)
        result.update(fare_status="not_requested", fare_error=None, sources=[], fetched_at=None,
                      error=None, fare_basis=None,
                      note="到发时刻为接口区间时刻，不代表实际运行状态。")
        result["status"] = _combined_status("not_requested", result["availability"]["status"])
        results.append(result)
    return results
