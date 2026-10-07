"""工具注册表。

登记所有数据源工具，供检索层/Agent 按名查找、过滤未启用的工具。
- register(tool) / get(name) / list_enabled() / invoke_by_name(name, params)
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import json
import os
from copy import deepcopy

from app.query_cache import QueryCache, query_scope
from app.dates import normalize_date
from app.ticket_copy import join_clause

from app.tools.cnrail import CnRailTool
from app.tools.emu_routing import EmuRoutingTool
from app.tools.extra_sources import (
    Freight95306Tool,
    JpRailFanTool,
    KmRailTool,
    SytljTicketTool,
)
from app.tools.rail_line import RailLineStationsTool, RailLineTool
from app.tools.rail_mileage import RailMileageTool
from app.tools.railre import RailReTool
from app.tools.station_lookup import StationLookupTool
from app.tools.station_screen import StationScreenTool
from app.tools.t12306 import T12306Tool
from app.tools.ticket_query import TicketQueryTool
from app.tools.ticket_price import TicketPriceTool
from app.tools.train_schedule import TrainScheduleTool
from app.tools.web import WebFetchTool
from app.tools.web_search import WebSearchTool2

if TYPE_CHECKING:
    from app.tools.base import Tool

_REGISTRY: dict[str, "Tool"] = {}
_QUERIES: dict[str, QueryCache] = {}


def register(tool: "Tool") -> None:
    _REGISTRY[tool.name] = tool


def get(name: str) -> "Tool | None":
    return _REGISTRY.get(name)


def list_enabled() -> list["Tool"]:
    return [t for t in _REGISTRY.values() if t.enabled]


def _append_snapshot_tail(note: str, ttl: int, fetched_at: str) -> str:
    """把"快照最多复用 N 秒"接到 note 上。

    note 常以句末标点收尾（如 `AVAILABILITY_SNAPSHOT` 以「。」结尾），此时直接续写；
    否则补一个「；」。历史上无条件插「；」，于是出现「。；」连写。
    """
    return join_clause(note, f"查询快照最多复用 {ttl} 秒，采样时间 {fetched_at}")


async def invoke_by_name(name: str, params: dict):
    tool = get(name)
    if tool is None or not tool.enabled:
        from app.tools.base import ToolResult

        return ToolResult(ok=False, error=f"工具未启用或不存在: {name}")
    if os.environ.get("APP_VARIANT", "main").lower() == "lm":
        return await tool.invoke(params)
    # Snapshot before the first await: the key and eventual factory must refer
    # to the same inputs even if the caller mutates nested data meanwhile.
    params = deepcopy(params)
    # Hash the complete settings and request overrides: neither identities nor secrets
    # appear in metrics, and different dates/actions/BYOK settings cannot share results.
    key = (query_scope(), normalize_date(params.get("date") or params.get("time")),
           json.dumps(params, sort_keys=True, ensure_ascii=False, default=str))
    cache = _QUERIES.setdefault(name, QueryCache("query." + name))
    ttl = {"ticket.query": 15, "train.schedule": 30}.get(name, 0)
    async def fetch():
        result = await tool.invoke(deepcopy(params))
        if ttl and result.ok:
            from datetime import datetime, timezone
            result.fetched_at = result.fetched_at or datetime.now(timezone.utc).isoformat()
            result.note = _append_snapshot_tail(result.note, ttl, result.fetched_at)
        return result

    return await cache.get(key, fetch, ttl=ttl,
                           reusable=lambda result: bool(result.ok))


async def close_queries() -> None:
    # QueryCache.close releases only this loop. Keep the per-tool objects so
    # another live loop retains its in-flight work and bounded snapshots.
    for cache in list(_QUERIES.values()):
        await cache.close()


def _register_all() -> None:
    # 通用抓取/搜索
    register(WebFetchTool())
    register(WebSearchTool2())
    # 铁路专有数据源
    register(CnRailTool())
    register(RailReTool())
    register(RailLineTool())
    register(RailLineStationsTool())
    register(EmuRoutingTool())
    register(StationLookupTool())
    register(StationScreenTool())
    register(RailMileageTool())
    register(TrainScheduleTool())
    register(TicketQueryTool())
    register(TicketPriceTool())
    register(T12306Tool())
    # 额外数据源（M3.1 新增）
    register(JpRailFanTool())
    register(Freight95306Tool())
    register(KmRailTool())
    register(SytljTicketTool())


_register_all()
