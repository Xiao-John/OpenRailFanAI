"""工具注册表。

登记所有数据源工具，供检索层/Agent 按名查找、过滤未启用的工具。
- register(tool) / get(name) / list_enabled() / invoke_by_name(name, params)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

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
from app.tools.train_schedule import TrainScheduleTool
from app.tools.web import WebFetchTool
from app.tools.web_search import WebSearchTool2

if TYPE_CHECKING:
    from app.tools.base import Tool

_REGISTRY: dict[str, "Tool"] = {}


def register(tool: "Tool") -> None:
    _REGISTRY[tool.name] = tool


def get(name: str) -> "Tool | None":
    return _REGISTRY.get(name)


def list_enabled() -> list["Tool"]:
    return [t for t in _REGISTRY.values() if t.enabled]


async def invoke_by_name(name: str, params: dict):
    tool = get(name)
    if tool is None or not tool.enabled:
        from app.tools.base import ToolResult

        return ToolResult(ok=False, error=f"工具未启用或不存在: {name}")
    return await tool.invoke(params)


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
    register(T12306Tool())
    # 额外数据源（M3.1 新增）
    register(JpRailFanTool())
    register(Freight95306Tool())
    register(KmRailTool())
    register(SytljTicketTool())


_register_all()
