"""Bounded execution of explicit train services; no model or public protocol changes."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import time

from app.config import get_settings
from app.dates import future_railway_date, normalize_date, railway_today, resolve_date
from app.pipeline.service_dates import DATE_TOKEN, MAX_DATES
from app.pipeline import ticket_answer
from app.tools import registry
from app.tools._rt12306 import is_emu_train_code, is_train_code
from app.tools.base import ToolResult

MAX_TRAINS = 10
_TOOLS = {"schedule": "train.schedule", "ticket": "ticket.query", "fare": "ticket.price", "routing": "emu.routing"}
_LABELS = {"schedule": "时刻", "ticket": "余票", "fare": "票价", "routing": "担当/交路"}


def _response(answer: str) -> dict:
    return {"data": [], "display_errors": [], "sources": [], "tool_trace": [],
            "note": "", "direct_answer": answer}


async def execute(trains: list[str], operations: list[str], *, date, od, message: str,
                  recent: bool = False, dates: list[str] | None = None) -> dict:
    """Run independent services, preserving each requested train and date on failure.

    Admission is per distinct train, rather than per operation. A rejected batch
    never starts a partial subset. Cancellation releases all child tasks.
    """
    codes = list(dict.fromkeys(str(code).strip().upper() for code in trains if str(code).strip()))
    ops = list(dict.fromkeys(str(op).strip() for op in operations if str(op).strip()))
    if len(codes) > MAX_TRAINS:
        return _response(f"本次列出 {len(codes)} 个不同车次，最多支持同时查询 {MAX_TRAINS} 个车次；未发起查询，请减少车次后重试。")
    if not codes:
        return _response("未发起查询：请提供需要查询的车次。")
    if not ops:
        return _response("未发起查询：请指定时刻、余票、票价或担当/交路服务。")

    # Imported at execution time because retrieve imports this executor.
    from app.pipeline.retrieve import _parse_seat, _parse_time_window, _parse_train_type, _ticket_interval_ready

    if dates is None:
        days = [normalize_date(date)]
    else:
        if not isinstance(dates, list) or not dates:
            return _response("未发起查询：请提供有效的日期列表。")
        days = []
        reference = railway_today()
        for value in dates:
            if not isinstance(value, str) or not DATE_TOKEN.fullmatch(value.strip()):
                return _response("未发起查询：日期列表包含无效日期，请核对。")
            day, recognized = resolve_date(value, default_today=False, reference_date=reference)
            if not recognized or not day:
                return _response("未发起查询：日期列表包含无效日期，请核对。")
            if day not in days:
                days.append(day)
            if len(days) > MAX_DATES:
                return _response(f"未发起查询：最多支持 {MAX_DATES} 个不同日期，请减少日期后重试。")
        if recent and len(days) > 1 and "routing" in ops:
            return _response("未发起查询：最近交路与指定多日期不能同时查询，请明确查询范围。")
    interval = tuple(od) if isinstance(od, (tuple, list)) and len(od) == 2 else None
    interval_ready = _ticket_interval_ready(interval)
    after, before = _parse_time_window(message, date)
    seat, train_type = _parse_seat(message), _parse_train_type(message)
    response = _response("")
    sections: list[str | None] = []
    jobs: list[tuple[int, str, str, dict]] = []

    for code in codes:
        for day in days:
            for op in ops:
                index = len(sections)
                scope = "最近记录（每条保留原始日期）" if op == "routing" and recent else day
                heading = f"{code} · {scope} · {_LABELS.get(op, op)}"
                sections.append(None)
                if not is_train_code(code):
                    sections[index] = heading + "\n未发起查询：车次格式无效，请核对车次。"
                    continue
                if op not in _TOOLS:
                    sections[index] = heading + "\n未发起查询：不支持该服务，请选择时刻、余票、票价或担当/交路。"
                    continue
                name = _TOOLS[op]
                if op in {"ticket", "fare"} and not interval_ready:
                    sections[index] = heading + "\n未发起查询：请补充实际乘车的出发站和到达站，不能以列车始终站推测乘车区间。"
                    response["tool_trace"].append(f"{name}: skipped（{code}，缺少实际乘车区间）")
                    continue
                if op == "routing" and not is_emu_train_code(code):
                    sections[index] = heading + "\n当前交路数据源 rail.re 不支持普速/非动车组车次的交路及担当查询；未查询其他服务替代。"
                    response["tool_trace"].append(f"{name}: skipped（{code}，数据源不支持）")
                    continue
                if op == "routing" and not recent and future_railway_date(day):
                    sections[index] = heading + "\n该日期尚未发生，担当车组和交路无法提前确定；未发起交路查询，不据历史记录推断未来担当。"
                    response["tool_trace"].append(f"{name}: skipped（{code}，未来日期）")
                    continue
                params = {"train": code, "date": day}
                if op == "schedule":
                    params["include_reference"] = True
                elif op == "routing":
                    if recent:
                        params["date"] = None
                    params["recent"] = recent is True
                else:
                    params.update(from_station=interval[0], to_station=interval[1])
                    if op == "ticket":
                        params.update(after_time=after or None, before_time=before or None,
                                      seat=seat or None, train_type=train_type or None)
                jobs.append((index, op, heading, params))

    sem = asyncio.Semaphore(max(1, int(get_settings().tool_concurrency)))

    async def invoke(op: str, params: dict) -> ToolResult:
        async with sem:
            started = time.perf_counter()
            result = None
            try:
                result = await registry.invoke_by_name(_TOOLS[op], deepcopy(params))
                if not isinstance(result, ToolResult):
                    result = ToolResult(ok=False, error="工具未返回有效查询结果")
                if len(days) > 1 and result.ok and op in {"schedule", "routing"}:
                    field = "train_date" if op == "schedule" else "focus_date"
                    actual = result.data.get(field) if isinstance(result.data, dict) else None
                    if actual != params["date"]:
                        result = ToolResult(ok=False, error="返回结果的查询日期缺失或与请求日期不一致，未作为该日结果展示",
                                            sources=list(result.sources), note=result.note)
                if op == "schedule" and result.ok and isinstance(result.data, dict):
                    data = result.data
                    if (data.get("source") == "offline-cache" or "matches" in data) and not (data.get("stops") or data.get("routes")):
                        result = ToolResult(ok=False, error=(data.get("realtime_error") or result.error or "时刻查询未返回经停或时刻，仅有离线基础归属"),
                                            sources=list(result.sources), note=result.note)
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = ToolResult(ok=False, error=f"工具执行异常（{type(exc).__name__}）")
                return result
            finally:
                if isinstance(result, ToolResult):
                    from app.metrics import record_tool
                    record_tool(_TOOLS[op], result.ok, (time.perf_counter() - started) * 1000)

    tasks = [asyncio.create_task(invoke(op, params)) for _, op, _, params in jobs]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    notes = []
    for (_, op, _, params), result in zip(jobs, results):
        # Results have independent parameter objects even if a tool mutates its copy.
        name = _TOOLS[op]
        scope = "最近记录" if op == "routing" and recent else params["date"]
        response["tool_trace"].append(f"{name}: {'ok' if result.ok else 'failed'}（{params['train']}，{scope}）")
        response["sources"].extend(result.sources)
    for (index, op, heading, params), result in zip(jobs, results):
        name = _TOOLS[op]
        if result.note:
            notes.append(f"[{params['train']} {params['date']} {name}] {result.note}")
        if result.ok:
            response["data"].append({**({"query_date": params["date"]} if len(days) > 1 else {}),
                                     "tool": name, "data": deepcopy(result.data), "text": result.text,
                                     "sources": list(result.sources), "note": result.note, "total": result.total,
                                     "shown": result.shown, "truncated": result.truncated, "filters": dict(result.filters or {}),
                                     "fetched_at": result.fetched_at, "integrity": result.integrity_line()})
            if op == "ticket":
                text = ticket_answer.render(params, result)
            elif op == "fare":
                text = result.text or "票价接口未提供可展示的票价记录。"
                extra = [result.note, result.integrity_line()]
                if result.sources:
                    extra.append("来源：" + "、".join(result.sources))
                text += "\n" + "\n".join(item for item in extra if item)
            else:
                text = "查询结果已返回，请查看结果卡片。"
            sections[index] = heading + "\n" + text
        else:
            error = result.error or "工具未返回查询结果"
            sections[index] = heading + "\n查询失败：" + error
            if result.note:
                sections[index] += "\n" + result.note
            if result.sources:
                sections[index] += "\n来源：" + "、".join(result.sources)
            notes.append(f"[{params['train']} {params['date']} {name}] 失败原因：{error}")
            if op in {"schedule", "routing"}:
                response["display_errors"].append({**({"query_date": params["date"]} if len(days) > 1 else {}),
                                                   "tool": name, "train_code": params["train"],
                                                   "query": params["train"], "date": params["date"] or "", "message": error})

    response["sources"] = list(dict.fromkeys(response["sources"]))
    response["note"] = "；".join(dict.fromkeys(notes))
    response["direct_answer"] = "\n\n".join(section for section in sections if section)
    return response
