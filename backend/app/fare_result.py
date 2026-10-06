"""Fare display facts from tool receipts; never parse or synthesize model prose."""
from decimal import Decimal, InvalidOperation
import math
from typing import Any
from app.dates import normalize_date

FARE_NOTE = "票价不代表实时余票；到发时刻为接口区间时刻，不代表实际运行状态。"


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
            "error": None, "note": "；".join(filter(None, [item.get("note"), FARE_NOTE]))}


def project_fares(receipts: list[dict], errors: list[dict]) -> list[dict]:
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
            base["note"] += "；未找到该查询区间的精确票价记录，不能据此判断停运或无票。"
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
                    result["note"] += "；接口未提供完整有效的席别金额，缺失金额不是0元。"
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
