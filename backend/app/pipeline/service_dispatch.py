"""Main service classification: operations and train collection are independent.

The public intent stays compatible; the internal query frame lives in Slots.raw.
Only explicit shared date/interval requests enter this deterministic dispatcher.
"""
from __future__ import annotations

import os
import re

from app.pipeline.extract import Slots
from app.pipeline.intent import Intent
from app.pipeline.service_dates import select_dates
from app.tools._rt12306 import is_train_code

MAX_TRAINS = 10
_TRAIN = re.compile(r"(?<![A-Za-z0-9])(?:0?[GDCTZKYSLBN]\d{1,5}[A-Z]?|\d{1,4})(?![A-Za-z0-9])", re.I)
_MODEL = re.compile(r"(?<![A-Za-z0-9])CRH?[0-9A-Za-z-]{2,12}(?![A-Za-z0-9])", re.I)
_PATTERNS = {
    "schedule": r"时刻表|图定时刻|经停|停靠|站序",
    "ticket": r"余票|还有票|有没有票|有票|候补",
    "fare": r"票价|票多少钱|多少钱|价格|票面价",
    "routing": r"交路|担当|车底|哪个车组|哪组",
}
_INTENTS = {"schedule": Intent.SCHEDULE, "ticket": Intent.TICKET,
            "fare": Intent.TICKET, "routing": Intent.EMU_ROUTING}


def train_codes(text: str) -> list[str]:
    # Remove date spans before considering bare-number passenger services.
    clean = select_dates(text or "").masked_text
    models = [m.span() for m in _MODEL.finditer(clean)]
    codes = []
    for match in _TRAIN.finditer(clean):
        code = match.group(0).upper()
        if not is_train_code(code) or any(a <= match.start() < b for a, b in models):
            continue
        if code.isdigit():
            before, after = clean[:match.start()], clean[match.end():]
            if (re.match(r"[年月日号点分:：.-]|时(?!刻表)|公里|千米|张|元|个|趟|组|车组|等座?|节|辆|吨|小时|分钟", after)
                    or re.search(r"\d[-.:：]$|编号\s*$", before)
                    or (len(code) < 3 and not re.match(r"次|列车", after))):
                continue
        if code not in codes:
            codes.append(code)
    return codes


def operations(text: str) -> list[str]:
    return [name for name, pattern in _PATTERNS.items() if re.search(pattern, text or "")]


def overflow(count: int) -> str:
    return f"本次包含 {count} 个不同车次，超出最多 {MAX_TRAINS} 个车次的可用范围。请减少到 {MAX_TRAINS} 个以内后重试；本次未发起数据查询。"


def _result(ops, trains, time=None, clarification=None, dates=None):
    raw = {"service_query": {"trains": trains, "operations": ops}}
    if dates:
        raw["service_query"]["dates"] = dates
    if clarification:
        raw["service_query"]["clarification"] = clarification
    intent = _INTENTS[ops[0]] if ops else Intent.SCHEDULE
    return intent, "realtime", Slots(target=trains[0] if len(trains) == 1 else None, time=time, raw=raw), "deterministic", ""


def decide(message: str, action: dict | None = None, history: list[dict] | None = None):
    if os.environ.get("APP_VARIANT", "main").lower() == "lm":
        return None
    if isinstance(action, dict):
        kind = action.get("kind")
        if kind == "train_schedule_batch":
            raw = action.get("trains")
            trains = list(dict.fromkeys(str(t).strip().upper() for t in raw if is_train_code(str(t).strip()))) if isinstance(raw, list) else []
            if len(trains) > MAX_TRAINS:
                return _result(["schedule"], trains, clarification=overflow(len(trains)))
            return Intent.SCHEDULE, "realtime", Slots(time=action.get("date")), "deterministic", ""
        if kind == "emu_routing":
            return Intent.EMU_ROUTING, "realtime", Slots(target=action.get("query"), time=action.get("date")), "deterministic", ""
        return None
    text = message or ""
    trains, ops = train_codes(text), operations(text)
    if len(trains) > MAX_TRAINS:
        return _result(ops, trains, clarification=overflow(len(trains)))
    if not ops:
        return None
    if re.search(r"为什么|原理|区别|历史(?!记录)|发展|科普", text):
        return None
    if re.search(r"这趟|那趟|这车|那些|这些|它|同上|还是|继续|这个区间", text):
        return None
    selection = select_dates(text)
    if selection.error:
        return _result(ops, trains, clarification=selection.error)
    multi_date = len(selection.days) > 1
    if (history and any(op in ops for op in ("ticket", "fare"))
            and not (multi_date and trains and re.search(r"到|至|→|->", selection.masked_text))):
        return None
    if any(op in {"ticket", "fare"} for op in ops) and len(re.findall(r"到|至|→|->", selection.masked_text)) > 1:
        return _result(ops, trains, clarification="本次包含多个乘车区间，请明确各车次对应的出发站和到达站，或按同一区间分开查询；本次未发起数据查询。")
    if multi_date and len(trains) > 1:
        train_spans = [m.span() for m in _TRAIN.finditer(selection.masked_text) if m.group().upper() in trains]
        if not (selection.spans[-1][1] <= train_spans[0][0]
                or selection.spans[0][0] >= train_spans[-1][1]):
            return _result(ops, trains, clarification="请明确各车次与日期的对应关系；共同查询可写成「G1、G2 明天、后天的时刻表」。本次未发起数据查询。")
    if multi_date and any(
            not re.fullmatch(r"(?:\s|、|,|，|和|及|与|以及|还有|到|至|[-~～])*", text[left[1]:right[0]])
            for left, right in zip(selection.spans, selection.spans[1:])):
        return _result(ops, trains, clarification="日期之间包含对象、服务或筛选条件，请明确各日期对应的查询，或将共同日期列在一起；本次未发起数据查询。")
    if multi_date and re.search(r"明早|明晚|明夜|昨早|昨晚|今早|今晨|今晚|今夜|分别|最近|历史记录", text):
        return _result(ops, trains, clarification="请明确各日期对应的时段或服务，或使用共同的完整日期列表；本次未发起数据查询。")
    time = ("、".join(selection.days) if multi_date else selection.expressions[0] if selection.expressions else None)
    recent_routing = bool(re.search(r"最近|历史记录", text) and "routing" in ops)
    remaining = selection.masked_text
    if re.search(r"农历|春节|清明|端午|中秋|以后|现在|周|星期|年|月|\d+[日号]|\d{1,4}[-/.]\d", remaining):
        if multi_date:
            return _result(ops, trains, clarification="日期列表包含尚未识别的时间表述，请使用完整公历日期；本次未发起数据查询。")
        return None
    if not time and "最近" in text and not recent_routing:
        return None
    between = (text[text.upper().find(trains[0]) + len(trains[0]):text.upper().rfind(trains[-1])]
               if len(trains) > 1 else "")
    if len(ops) > 1 and ("分别" in text or any(
            re.search(pattern, between, re.I) for pattern in _PATTERNS.values())):
        return _result(ops, trains, time, "请明确各车次需要查询的服务，或将车次列在前、共同服务列在后，例如「G1、G2 的时刻表、余票和票价」。本次未发起数据查询。")
    if trains:
        return _result(ops, trains, time, dates=selection.days if multi_date else None)
    if multi_date:
        return _result(ops, trains, clarification="多日期查询请提供车次和共同服务；本次未发起数据查询。")
    if ops == ["routing"]:
        model = _MODEL.search(text)
        if model:
            return Intent.EMU_ROUTING, "realtime", Slots(target=model.group(0).upper(), time=time), "deterministic", ""
    if len(ops) == 1 and ops[0] in {"ticket", "fare"}:
        return _INTENTS[ops[0]], "realtime", Slots(time=time), "deterministic", ""
    return None
