"""投机预取（perf P0-3）：把"检索"藏进"LLM 决策"的耗时里。

思路
----
走到 LLM 决策时说明快路径没命中（实测约 1–2s，抖动时更长）。这段时间里，**槽位其实已经
能从正则里低成本拿到**（车次号 / 起讫站），而工具调用主要依赖槽位、不依赖意图。
于是：决策一开始就并行发起**最高置信的那一个**工具调用；等计划出来后，
若恰好命中同名同参的步骤，直接复用结果（省下 0.5–2.1s），否则丢弃并取消。

纪律（不做的事）
----------------
- **只在 LLM 决策路径上预取**（快路径 1–16ms，没有可藏的时间，预取反而多打外部接口）；
- **最多预取 1 个工具**（避免对着 12306 之类的外部站点乱打）；
- 失败/取消都不抛出（预取是纯优化，绝不能改变行为或影响可用性）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from app.od import parse_od
from app.dates import resolve_date
from app.tools import _rt12306 as rt
from app.metrics import record_cache

_log = logging.getLogger("railfan.prefetch")


def _key(name: str, params: dict) -> str:
    if os.environ.get("APP_VARIANT", "main").lower() == "lm":
        return name + "|" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
    canonical = {k: v for k, v in (params or {}).items() if v is not None}
    # Missing/relative dates are equivalent only when recognized. Keep unrecognized
    # raw dates distinct so their warnings and fallback semantics aren't lost.
    day, matched = resolve_date(canonical.get("date"))
    if matched:
        canonical["date"] = day
    return name + "|" + json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)


class Prefetch:
    """一次请求内的投机取数容器（用完即弃）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, Any] = {}
        self.started: list[str] = []
        self.used: list[str] = []
        self._closed = False

    def take(self, name: str, params: dict) -> Any | None:
        """若有同名同参的预取结果（含仍在进行的），返回 awaitable；否则 None。"""
        k = _key(name, params)
        if k in self._results:
            if k not in self.used:
                record_cache("prefetch", "used")
            self.used.append(k)
            return self._results[k]
        if k in self._tasks:
            if k not in self.used:
                record_cache("prefetch", "used")
            self.used.append(k)
            return self._tasks[k]
        return None

    async def cancel(self) -> None:
        """取消未被使用的预取（并在日志里如实记录，便于评估"开枪打空"的比例）。"""
        if self._closed:
            return
        self._closed = True
        wasted = []
        for k, task in list(self._tasks.items()):
            if k not in self.used:
                record_cache("prefetch", "unused")
            # Also cancel a used task on outer cancellation: every task belongs to
            # this request, and no orphan work may outlive its final cleanup.
            if not task.done():
                task.cancel()
                record_cache("prefetch", "cancelled")
                wasted.append(k.split("|")[0])
        if wasted:
            _log.info("预取结束，已取消未完成请求：%s", ", ".join(wasted))
        for task in list(self._tasks.values()):
            try:
                await asyncio.gather(task, return_exceptions=True)
            except Exception:  # noqa: BLE001
                pass


def start(message: str, date_hint: str | None = None) -> Prefetch | None:
    """按槽位形态挑一个最可能的工具并行预取；没有把握就返回 None（不预取）。"""
    pf = Prefetch()
    text = message or ""
    main = os.environ.get("APP_VARIANT", "main").lower() != "lm"
    if main and re.search(r"为什么|原理|区别|历史|科普|多少钱|票价", text):
        return None
    if main:
        date_str, matched = resolve_date(date_hint or text, default_today=False)
        if date_hint and not matched:
            return None
    else:
        date_str = date_hint or ""

    train = ""
    pattern = (r"(?<![A-Za-z0-9])(0?[GDC]\d{1,5}[A-Z]?)(?![A-Za-z0-9])" if main
               else r"(0?[GDCTZKYSLBN]\d{1,5}[A-Z]?|\d{1,4})(?:次|列车)?")
    for m in re.finditer(pattern, text, re.I):
        cand = m.group(1)
        if rt.is_train_code(cand) and cand.upper() != "12306":
            train = cand.upper()
            break

    if train:
        # 车次是最强信号：动车组的"担当"与"时刻/经停"两个意图都会用到它，
        # 其中 emu.routing 只查 rail.re（便宜、且最常被问）→ 预取它。
        name, params = "emu.routing", {"train": train, "date": date_str or "今天"}
    elif not main or re.search(r"余票|还有.{0,12}票|有票|候补|买票|订票", text):
        od = parse_od(text)
        if od:
            name, params = "ticket.query", {
                "from_station": od[0], "to_station": od[1], "date": date_str or "今天",
            }
            if main:
                # Match the effective retrieval plan, including optional filters.
                from app.pipeline.retrieve import _parse_time_window, _parse_seat, _parse_train_type
                after, before = _parse_time_window(text, date_hint)
                params.update(after_time=after or None, before_time=before or None,
                              seat=_parse_seat(text) or None,
                              train_type=_parse_train_type(text) or None)
        else:
            return None                    # 没有可用的强信号 → 不预取（避免乱打外部接口）
    else:
        return None

    async def _run():
        try:
            from app.tools import registry

            return await registry.invoke_by_name(name, params)
        except Exception as e:  # noqa: BLE001 —— 预取失败绝不影响主流程
            _log.debug("预取 %s 失败：%s: %s", name, type(e).__name__, e)
            return None

    try:
        pf._tasks[_key(name, params)] = asyncio.create_task(_run())
        pf.started.append(name)
        record_cache("prefetch", "started")
    except RuntimeError:
        return None                       # 无事件循环（同步调用场景）
    return pf
