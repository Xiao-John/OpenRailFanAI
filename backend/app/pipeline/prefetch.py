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
import re
from typing import Any

from app.od import parse_od
from app.tools import _rt12306 as rt

_log = logging.getLogger("railfan.prefetch")


def _key(name: str, params: dict) -> str:
    return name + "|" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)


class Prefetch:
    """一次请求内的投机取数容器（用完即弃）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, Any] = {}
        self.started: list[str] = []
        self.used: list[str] = []

    def take(self, name: str, params: dict) -> Any | None:
        """若有同名同参的预取结果（含仍在进行的），返回 awaitable；否则 None。"""
        k = _key(name, params)
        if k in self._results:
            self.used.append(k)
            return self._results[k]
        if k in self._tasks:
            self.used.append(k)
            return self._tasks[k]
        return None

    async def cancel(self) -> None:
        """取消未被使用的预取（并在日志里如实记录，便于评估"开枪打空"的比例）。"""
        wasted = []
        for k, task in list(self._tasks.items()):
            if k in self.used or task.done():
                continue
            task.cancel()
            wasted.append(k.split("|")[0])
        if wasted:
            _log.info("预取未被使用（已取消）：%s", ", ".join(wasted))
        for task in list(self._tasks.values()):
            try:
                await asyncio.gather(task, return_exceptions=True)
            except Exception:  # noqa: BLE001
                pass


def start(message: str, date_hint: str | None = None) -> Prefetch | None:
    """按槽位形态挑一个最可能的工具并行预取；没有把握就返回 None（不预取）。"""
    pf = Prefetch()
    text = message or ""
    date_str = date_hint or ""

    train = ""
    for m in re.finditer(r"(0?[GDCTZKYSLBN]\d{1,5}[A-Z]?|\d{1,4})(?:次|列车)?", text, re.I):
        cand = m.group(1)
        if rt.is_train_code(cand) and cand.upper() != "12306":
            train = cand.upper()
            break

    if train:
        # 车次是最强信号：动车组的"担当"与"时刻/经停"两个意图都会用到它，
        # 其中 emu.routing 只查 rail.re（便宜、且最常被问）→ 预取它。
        name, params = "emu.routing", {"train": train, "date": date_str or "今天"}
    else:
        od = parse_od(text)
        if od:
            name, params = "ticket.query", {
                "from_station": od[0], "to_station": od[1], "date": date_str or "今天",
            }
        else:
            return None                    # 没有可用的强信号 → 不预取（避免乱打外部接口）

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
    except RuntimeError:
        return None                       # 无事件循环（同步调用场景）
    return pf
