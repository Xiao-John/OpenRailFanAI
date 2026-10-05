"""Stable UI projections of normalized tool data (never model prose)."""
from __future__ import annotations

import os
from typing import Any
from app.dates import normalize_date

DISPLAY_SCHEMA_VERSION = 1


def _project_stop(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "station_no": item.get("station_no") or item.get("seq"),
        "station": item.get("station") or item.get("station_name") or item.get("name"),
        "arrive_time": item.get("arrive_time") or item.get("arrive"),
        "start_time": item.get("start_time") or item.get("depart"),
        "stopover_time": item.get("stopover_time") or item.get("stopover_min"),
    }


def _project_routing_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for index, item in enumerate(records):
        train, day, time = item.get("train_code"), item.get("date"), item.get("time")
        # Missing identity/time must not imply coupled operation.
        key = (train, day, time) if train and day and time else ("incomplete", index, None)
        record = grouped.setdefault(key, {"train_code": train, "date": day, "time": time, "units": []})
        raw = item.get("emu_no")
        label = item.get("emu_no_display") or raw
        if raw or label:
            unit = {"emu_no": raw, "emu_no_display": label}
            identity = str(raw or label).replace("-", "").replace(" ", "").upper()
            if not any(str(u.get("emu_no") or u.get("emu_no_display")).replace("-", "").replace(" ", "").upper() == identity for u in record["units"]):
                record["units"].append(unit)
    for record in grouped.values():
        record["coupled"] = len(record["units"]) > 1
    return list(grouped.values())


def _schedule(data: dict[str, Any]) -> dict[str, Any]:
    reference = data.get("reference") if isinstance(data.get("reference"), dict) else {}
    reference_stops = reference.get("stops") if isinstance(reference.get("stops"), list) else None
    source = data.get("source")
    if data.get("today_times_available") is False:
        if reference_stops is not None and reference.get("is_actual_run") is False:
            time_basis = "reference"
            stops = reference_stops
            schedule_type = reference.get("source") or "图定时刻（参考）"
        else:
            time_basis = "stations_only"
            stops = data.get("stops") or data.get("routes") or []
            schedule_type = "仅站名（当日时刻不可用）"
    elif source == "12306-realtime" and data.get("today_times_available") is not False:
        # The real-time query confirms a current train result; the stop rows
        # still come from the timetable lookup and must remain labeled as
        # scheduled reference times, never as actual running/late times.
        time_basis = "reference"
        stops = data.get("stops") or data.get("routes") or []
        schedule_type = "图定时刻"
    elif source in ("12306-timetable", "local-gtfs") or "图定" in str(data.get("schedule_type") or ""):
        time_basis = "reference" if data.get("stops_with_times") else "stations_only"
        stops = reference_stops if reference_stops is not None else (data.get("stops") or data.get("routes") or [])
        schedule_type = data.get("schedule_type") or "图定时刻（参考）"
    else:
        # Route stop rows come from the timetable lookup; absent an explicit
        # tool marker, do not present those values as today's actual times.
        time_basis = "stations_only"
        stops = data.get("stops") or data.get("routes") or []
        schedule_type = "当日查询结果"
    projected_stops = []
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        projected = _project_stop(stop)
        if time_basis != "reference":
            projected["arrive_time"] = None
            projected["start_time"] = None
        projected_stops.append(projected)
    return {
        "kind": "train_schedule",
        "status": "empty" if not projected_stops else "success",
        "train_code": data.get("train_code"),
        "date": data.get("train_date"),
        "from_station": data.get("from_station") or data.get("first_station"),
        "to_station": data.get("to_station") or data.get("last_station"),
        "start_time": data.get("start_time") if data.get("today_times_available") is not False or time_basis == "reference" else None,
        "arrive_time": data.get("arrive_time") if data.get("today_times_available") is not False or time_basis == "reference" else None,
        "duration": data.get("duration"),
        "stops": projected_stops,
        "schedule_type": schedule_type,
        "time_basis": time_basis,
        "today_times_available": data.get("today_times_available"),
        "sample_data": bool(data.get("sample_data")),
        "sources": [],
    }


def _routing(data: dict[str, Any]) -> dict[str, Any]:
    records = data.get("records") or []
    focus_date = data.get("focus_date")
    focus_records = [item for item in records if isinstance(item, dict) and (data.get("query_mode") == "recent" or item.get("date") == focus_date)]
    if focus_date and not focus_records:
        return {
            "kind": "empty",
            "status": "empty",
            "tool": "emu.routing",
            "query": data.get("query"),
            "query_kind": data.get("kind"),
            "date": focus_date,
            "historical_records": _project_routing_records([item for item in records if isinstance(item, dict)]),
            "source": "rail.re",
            "sources": [],
        }
    return {
        "kind": "emu_routing",
        "status": "success" if focus_records else "empty",
        "query": data.get("query"),
        "query_kind": data.get("kind"),
        "focus_date": focus_date,
        "records": _project_routing_records(focus_records),
        "source": "rail.re",
        "time_semantics": "以下为记录时间，不是列车到发时间。",
        "sample_data": bool(data.get("sample_data")),
        "sources": [],
    }


def serialize_display_results(tool_data: list[dict[str, Any]], errors: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Project the internal tool contract into versioned, UI-safe result objects."""
    # Existing clients retry a schedule batch using one date. Keep each date
    # in its own old-format card/batch, so retries cannot silently use day one.
    if (os.environ.get("APP_VARIANT", "main").lower() != "lm"
            and any("query_date" in item for item in [*tool_data, *(errors or [])])):
        groups = {}
        other_data, other_errors = [], []
        for item in tool_data:
            if item.get("tool") == "train.schedule" and isinstance(item.get("data"), dict):
                day = item.get("query_date") or item["data"].get("train_date")
                groups.setdefault(day, ([], []))[0].append(item)
            else:
                other_data.append(item)
        for item in errors or []:
            if item.get("tool") == "train.schedule":
                day = item.get("query_date") or normalize_date(item.get("date"))
                groups.setdefault(day, ([], []))[1].append(item)
            else:
                other_errors.append(item)
        if len(groups) > 1:
            results = []
            for entries, failures in groups.values():
                results.extend(serialize_display_results(entries, failures))
            results.extend(serialize_display_results(other_data, other_errors))
            return results
    schedules = []
    for item in tool_data:
        if item.get("tool") == "train.schedule" and isinstance(item.get("data"), dict):
            schedule = _schedule(item["data"])
            schedule["sources"] = item.get("sources") or []
            schedules.append(schedule)
    routings = []
    for item in tool_data:
        if item.get("tool") == "emu.routing" and isinstance(item.get("data"), dict):
            routing = _routing(item["data"])
            routing["sources"] = item.get("sources") or []
            routings.append(routing)
    results: list[dict[str, Any]] = []
    schedule_errors = [item for item in (errors or []) if item.get("tool") == "train.schedule"]
    if len(schedules) + len(schedule_errors) > 1:
        failed_schedules = [{"kind": "train_schedule", "status": "failed",
                             "train_code": item.get("train_code"), "date": normalize_date(item.get("date")),
                             "error": item.get("message", "")}
                            for item in schedule_errors]
        results.append({"kind": "train_schedule_batch", "status": "partial" if schedule_errors or any(
            item["status"] != "success" for item in schedules) else "success",
            "items": schedules + failed_schedules})
    elif schedule_errors:
        item = schedule_errors[0]
        results.append({"kind": "train_schedule", "status": "failed",
                        "train_code": item.get("train_code"),
                        "date": normalize_date(item.get("date")),
                        "error": item.get("message", "")})
    else:
        results.extend(schedules)
    for item in (errors or []):
        if item.get("tool") == "emu.routing" and any(mark in item.get("message", "") for mark in ("未返回", "没有记录")):
            results.append({"kind": "empty", "status": "empty", "tool": "emu.routing",
                            "query": item.get("query"),
                            "date": normalize_date(item.get("date")),
                            "message": item.get("message", "")})
        elif item.get("tool") != "train.schedule":
            results.append({"kind": "error", "status": "failed", "tool": item.get("tool"),
                            "message": item.get("message", "")})
    results.extend(routings)
    for result in results:
        result["schema_version"] = DISPLAY_SCHEMA_VERSION
        if result.get("kind") == "train_schedule_batch":
            for item in result.get("items", []):
                item["schema_version"] = DISPLAY_SCHEMA_VERSION
                if item.get("kind") == "train_schedule":
                    for stop in item.get("stops", []):
                        stop["schema_version"] = DISPLAY_SCHEMA_VERSION
        if result.get("kind") == "train_schedule":
            for stop in result.get("stops", []):
                stop["schema_version"] = DISPLAY_SCHEMA_VERSION
        if result.get("kind") == "emu_routing":
            for record in result.get("records", []):
                record["schema_version"] = DISPLAY_SCHEMA_VERSION
        if result.get("kind") == "empty":
            for record in result.get("historical_records", []):
                record["schema_version"] = DISPLAY_SCHEMA_VERSION
    return results
