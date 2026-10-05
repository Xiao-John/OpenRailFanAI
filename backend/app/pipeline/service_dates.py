"""Explicit shared railway dates for Main service batches; never guess bindings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re

from app.dates import _NUM, _RELATIVE_KEYS, railway_today, resolve_date

MAX_DATES = 10
DATE_TOKEN = re.compile(
    r"(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)|(?<!\d)\d{8}(?!\d)|"
    + rf"{_NUM}月{_NUM}[日号]|"
    + "|".join(map(re.escape, _RELATIVE_KEYS))
)
_SHORT_DAY = re.compile(r"(?<![\dA-Za-z])(\d{1,2})[日号]")
_RANGE = re.compile(r"\s*(?:到|至|[-~～])\s*")
_LIST_OR_RANGE = re.compile(r"\s*(?:、|,|，|和|及|到|至|[-~～])\s*")


@dataclass
class DateSelection:
    days: list[str]
    expressions: list[str]
    spans: list[tuple[int, int]]
    masked_text: str
    error: str = ""


def select_dates(text: str) -> DateSelection:
    """Resolve lists and inclusive ranges once against the China business day.

    A short day may inherit its immediately preceding explicit month. Ranges
    are checked before expansion, so an excessive span never creates jobs.
    """
    text = text or ""
    tokens = [(m.start(), m.end(), m.group()) for m in DATE_TOKEN.finditer(text)]
    for m in _SHORT_DAY.finditer(text):
        if not any(a <= m.start() < b for a, b, _ in tokens):
            tokens.append((m.start(), m.end(), m.group()))
    tokens.sort()
    today = railway_today()
    days, expressions, spans = [], [], []
    masks = list(text)
    previous = None
    error = ""
    for start, end, expression in tokens:
        gap = text[previous[1]:start] if previous else ""
        if _SHORT_DAY.fullmatch(expression):
            if not previous or "月" not in previous[2] or not _LIST_OR_RANGE.fullmatch(gap):
                # A bare day without an explicit month remains unsupported.
                continue
            try:
                day = date(previous[3].year, previous[3].month, int(_SHORT_DAY.fullmatch(expression).group(1)))
            except ValueError:
                day = None
        else:
            value, recognized = resolve_date(expression, default_today=False, reference_date=today)
            day = date.fromisoformat(value) if recognized and value else None
        spans.append((start, end)); expressions.append(expression)
        masks[start:end] = " " * (end - start)
        if day is None:
            error = f"无法识别乘车日期「{expression}」，请提供有效日期后重试；本次未发起数据查询。"
            break
        expanded = [day.isoformat()]
        if previous and _RANGE.fullmatch(gap):
            length = (day - previous[3]).days + 1
            if length <= 0:
                error = "日期范围的结束日期早于开始日期，请核对起止日期；本次未发起数据查询。"
                break
            if length > MAX_DATES:
                error = f"日期范围包含 {length} 天，最多支持 {MAX_DATES} 个日期。请减少日期后重试；本次未发起数据查询。"
                break
            expanded = [(previous[3] + timedelta(days=i)).isoformat() for i in range(length)]
            masks[previous[1]:start] = " " * (start - previous[1])
        days = list(dict.fromkeys([*days, *expanded]))
        if len(days) > MAX_DATES:
            error = f"本次包含超过 {MAX_DATES} 个不同日期，请减少到 {MAX_DATES} 个以内后重试；本次未发起数据查询。"
            break
        # Keep the original month token when inheriting a chain such as 6日、7日。
        month_expression = previous[2] if _SHORT_DAY.fullmatch(expression) else expression
        previous = (start, end, month_expression, day)
    return DateSelection(days, expressions, spans, "".join(masks), error)
