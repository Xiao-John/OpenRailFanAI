"""未来交路边界回归：无需外网或模型。"""
from __future__ import annotations

import asyncio
from datetime import datetime as RealDatetime
from unittest.mock import patch

from app.dates import future_railway_date
from app.pipeline.extract import Slots
from app.pipeline.prefetch import start
from app.pipeline.retrieve import retrieve
from app.tools.base import ToolResult


class ChinaClock(RealDatetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 3, 0, 30, tzinfo=tz)


async def exercise():
    calls = []

    async def invoke(name, params):
        calls.append((name, params))
        return ToolResult(ok=True, data={"stub": True})

    with patch("app.dates.datetime", ChinaClock), patch("app.tools.registry.invoke_by_name", invoke):
        assert future_railway_date("明天") == "2026-10-04"
        assert future_railway_date("今天") == ""
        assert future_railway_date("昨天") == ""
        assert future_railway_date("无效日期") == ""

        for date in ("明天", "后天", "2026-10-04"):
            calls.clear()
            result = await retrieve("schedule", Slots(target="G1", time=date), message=f"G1 {date} 时刻表")
            assert any(name == "train.schedule" for name, _ in calls), calls
            assert not any(name == "emu.routing" for name, _ in calls), calls
            assert not result["display_errors"], result
            assert "无法提前确定" in result["note"], result

        for date in ("今天", "昨天", "2026-10-02"):
            calls.clear()
            await retrieve("schedule", Slots(target="G1", time=date), message=f"G1 {date} 时刻表")
            assert any(name == "emu.routing" for name, _ in calls), calls

        for target in ("G1", "CR400BF-5033"):
            calls.clear()
            result = await retrieve("emu_routing", Slots(target=target, time="明天"), message=f"明天 {target} 交路")
            assert not any(name == "emu.routing" for name, _ in calls), calls
            assert not result["display_errors"], result
            assert "无法提前确定" in result["note"], result

        calls.clear()
        result = await retrieve("general", Slots(), display_action={"kind": "emu_routing", "query": "G1", "date": "2026-10-04"})
        assert not calls, calls
        assert not result["display_errors"] and "无法提前确定" in result["note"]
        calls.clear()
        await retrieve("general", Slots(), display_action={"kind": "train_schedule_batch", "trains": ["G1", "G2"], "date": "2026-10-04"})
        assert [name for name, _ in calls] == ["train.schedule", "train.schedule"], calls

        calls.clear()
        assert start("明天 G1 用什么车") is None
        assert start("G1 时刻表", date_hint="2026-10-04") is None
        await asyncio.sleep(0)
        assert not calls, calls
        for date in ("今天", "昨天"):
            pf = start(f"{date} G1 用什么车")
            assert pf is not None
            await asyncio.sleep(0)
            await pf.cancel()
        assert len(calls) == 2 and all(name == "emu.routing" for name, _ in calls), calls


if __name__ == "__main__":
    asyncio.run(exercise())
    print("future routing guard tests passed")
