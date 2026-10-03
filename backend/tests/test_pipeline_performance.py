"""Offline scheduling, generation budgets, visible timing and cancellation checks."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

from app import metrics
from app.llm import client
from app.llm.client import LLMUnavailable
from app.pipeline import generate, orchestrator, planner, prefetch
from app.pipeline.extract import Slots
from app.pipeline.intent import Intent
from app.tools.base import ToolResult


async def _nothing(*args, **kwargs):
    return None


def _data(structured=False, failed=False):
    return {"data": ([{"tool": "train.schedule", "text": "经停资料来自图定参考，非实际运行。",
                       "data": {"train_code": "G1", "train_date": "2026-10-01", "stops": []}}]
                     if structured and not failed else []),
            "sources": [], "tool_trace": ["train.schedule: failed" if failed else "train.schedule: ok"],
            "display_errors": ([{"tool": "train.schedule", "train_code": "G1", "date": "2026-10-01",
                                "message": "unavailable"}] if failed else [])}


async def _decide(*args, **kwargs):
    return Intent.SCHEDULE, "realtime", Slots(target="G1", time="明天"), "deterministic", ""


def test_prefetch_overlaps_decision_and_cleanup():
    async def check():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def invoke(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def merged(*args, **kwargs):
            # A deterministic dependency check: the tool must already run before
            # the planner returns, rather than merely start alongside retrieval.
            await asyncio.wait_for(started.wait(), 1)
            return {"intent": "emu_routing", "question_type": "realtime", "target": "G1", "time": "明天"}

        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(planner.rt, "ensure_loaded", _nothing), \
                patch.object(planner.fastpath, "plan_with_reason", return_value=(None, None)), \
                patch.object(planner, "chat_structured", merged), \
                patch("app.tools.registry.invoke_by_name", invoke):
            decision, pf = await orchestrator._decide_with_prefetch("G1明天几点到达？", None)
            assert decision[3] == "llm-merged" and pf is not None
            assert pf.take("emu.routing", {"train": "G1", "date": "明天"}) is not None
            assert pf.take("emu.routing", {"train": "G1", "date": "后天"}) is None
            await pf.cancel()
            assert cancelled.is_set() and all(task.done() for task in pf._tasks.values())

            started.clear()
            cancelled.clear()

            async def unavailable(*args, **kwargs):
                await asyncio.wait_for(started.wait(), 1)
                raise LLMUnavailable("offline injected failure")

            with patch.object(planner, "chat_structured", unavailable):
                try:
                    await orchestrator._decide_with_prefetch("G1明天几点到达？", None)
                except LLMUnavailable:
                    pass
                else:
                    raise AssertionError("planning error swallowed")
                assert cancelled.is_set()

            started.clear()
            cancelled.clear()

            async def blocked(*args, **kwargs):
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.Event().wait()

            with patch.object(planner, "chat_structured", blocked):
                task = asyncio.create_task(orchestrator._decide_with_prefetch("G1明天几点到达？", None))
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                assert cancelled.is_set()

        with patch.object(planner, "decide", _decide), patch.object(prefetch, "start") as start:
            decision, pf = await orchestrator._decide_with_prefetch("G1经停哪些站？", None)
            assert pf is None
            start.assert_not_called()

    asyncio.run(check())


def test_prefetch_reuses_filters_and_avoids_model_digits():
    async def invoke(*args, **kwargs):
        return ToolResult(ok=True)

    async def check():
        with patch.dict(os.environ, APP_VARIANT="main"), patch("app.tools.registry.invoke_by_name", invoke):
            await prefetch.rt.ensure_loaded()
            assert prefetch.start("CR400AF为什么叫复兴号？") is None
            assert prefetch.start("CR400AF-0207今天的车型参数") is None
            assert prefetch.start("北京到上海多少公里？") is None
            assert prefetch.start("2026-10-01是什么日子？") is None
            pf = prefetch.start("明天北京到上海晚上还有二等座票吗？")
            assert pf is not None
            params = {"from_station": "北京", "to_station": "上海", "date": "明天",
                      "after_time": "17:00", "before_time": "24:00", "seat": "second_class", "train_type": None}
            assert pf.take("ticket.query", params) is not None
            assert pf.take("ticket.query", {**params, "seat": "business_class"}) is None
            assert pf.take("ticket.query", {**params, "date": "后天"}) is None
            await pf.cancel()

    asyncio.run(check())


def test_structured_budget_prompt_and_lm_preservation():
    retrieval = _data(structured=True)
    with patch.dict(os.environ, APP_VARIANT="main"):
        client.set_active_provider(None)
        options = generate.completion_options(retrieval, "realtime")
        assert options == {"no_think": True, "max_tokens": min(generate.get_settings().llm_max_tokens, 256)}
        client.set_active_provider({"max_tokens": 900, "context_tokens": 4096})
        assert generate.completion_options(retrieval, "realtime") == {"no_think": True}
        assert generate.completion_options(retrieval, "knowledge") == {}
        assert generate.completion_options(retrieval, "mixed") == {}
        prompt = generate.build_prompt("G1明天经停哪些站？", Slots(target="G1"), retrieval,
                                       [{"role": "assistant", "content": "OLD_HISTORY"}], "realtime")
        for policy in ("历史回答不作为本次检索事实", "图定/计划", "同一次车不同车次号", "仅有站序不得补出时刻",
                       "命中 N 条/展示 M 条", "其他日期的记录不得冒充今日", "禁止由推理反推时间节点",
                       "经停资料来自图定参考", "OLD_HISTORY"):
            assert policy in prompt, policy
    with patch.dict(os.environ, APP_VARIANT="lm"):
        assert generate.completion_options(retrieval, "realtime") == {}
        legacy = generate.build_prompt("G1明天经停哪些站？", Slots(target="G1"), retrieval, question_type="realtime")
        assert generate._LONG_REQUIREMENTS in legacy
        assert len(prompt) < len(legacy)
    client.set_active_provider(None)


def test_actual_retrieval_consumes_prefetch_once():
    async def check():
        calls = []
        started = asyncio.Event()

        async def invoke(name, params):
            calls.append(name)
            if name == "emu.routing":
                started.set()
            return ToolResult(ok=True, text="查询数据为空。", data={})

        async def merged(*args, **kwargs):
            await asyncio.wait_for(started.wait(), 1)
            return {"intent": "emu_routing", "question_type": "realtime", "target": "G1", "time": "明天"}

        async def completion(*args, **kwargs):
            yield "text", "请参考查询结果。"

        before = metrics.snapshot()["caches"].get("prefetch", {}).get("used", 0)
        with patch.dict(os.environ, APP_VARIANT="main"), \
                patch.object(planner.fastpath, "plan_with_reason", return_value=(None, None)), \
                patch.object(planner, "chat_structured", merged), \
                patch("app.tools.registry.invoke_by_name", invoke), \
                patch.object(client, "stream_completion", completion):
            events = [event async for event in orchestrator.run_stream("G1明天几点到达？")]
        assert calls.count("emu.routing") == 1, calls
        assert calls.count("train.schedule") == 1, calls
        assert metrics.snapshot()["caches"]["prefetch"]["used"] == before + 1
        assert next(event for event in events if event["type"] == "done")["planner"] == "llm-merged"

    asyncio.run(check())


def test_photo_spot_and_composite_facts_keep_full_generation_budget():
    """A routing card must not truncate geographic/search advice at 256 tokens."""
    message = "吉林市XX区，要拍 CR400AF，今天下午"
    facts = [
        {"tool": "station.lookup", "text": "吉林站位置资料"},
        {"tool": "cnrail.map", "text": "铁路地图和线路方位资料"},
        {"tool": "emu.routing", "text": "CR400AF担当记录",
         "data": {"kind": "emu", "query": "CR400AF", "focus_date": "2026-09-30", "records": []}},
        {"tool": "web.search", "text": "拍摄地点候选及公开交通注意事项"},
    ]
    retrieval = {"data": facts, "sources": [],
                 "tool_trace": [entry["tool"] + ": ok" for entry in facts]}
    with patch.dict(os.environ, APP_VARIANT="main"):
        client.set_active_provider(None)
        assert not generate.is_structured_realtime(retrieval, "realtime")
        assert generate.completion_options(retrieval, "realtime") == {}
        prompt = generate.build_prompt(message, Slots(location="吉林", target="CR400AF", time="今天下午"),
                                       retrieval, question_type="realtime")
        assert generate._LONG_REQUIREMENTS in prompt
        assert generate._STRUCTURED_REQUIREMENTS not in prompt
        assert "尽量不超过100字" not in prompt
        assert all(entry["text"] in prompt for entry in facts)

        # Any additional effective tool defeats the narrow card-only premise,
        # including a failed tool whose error still requires an explanation.
        pure = _data(structured=True)
        assert generate.is_structured_realtime(pure, "realtime")
        for tool in ("station.lookup", "cnrail.map", "web.search", "ticket.query", "rail.mileage"):
            composite = {**pure, "data": [*pure["data"], {"tool": tool, "text": "有效检索事实"}]}
            assert generate.completion_options(composite, "realtime") == {}, tool
            assert generate._LONG_REQUIREMENTS in generate.build_prompt(
                message, Slots(), composite, question_type="realtime"), tool
        failed_extra = {**pure, "display_errors": [{"tool": "web.search", "message": "unavailable"}]}
        assert generate.completion_options(failed_extra, "realtime") == {}

    async def check_calls():
        options = []

        async def retrieve(*args, **kwargs):
            return retrieval

        async def completion(*args, **kwargs):
            options.append(kwargs)
            yield "text", "机位需要结合地图、公开交通资料和担当记录。"

        async def block_completion(*args, **kwargs):
            options.append(kwargs)
            return "机位需要结合地图、公开交通资料和担当记录。", ""

        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(client, "stream_completion", completion), \
                patch.object(generate, "chat_with_reasoning", block_completion):
            await orchestrator.run(message)
            _ = [event async for event in orchestrator.run_stream(message)]
        assert options == [{}, {}], options

    asyncio.run(check_calls())


def test_explicit_generation_budget_reaches_stream_and_block():
    async def check():
        options = []

        async def retrieve(*args, **kwargs):
            return _data(structured=True)

        async def completion(*args, **kwargs):
            options.append(kwargs)
            yield "text", "请查看卡片。"

        async def block_completion(*args, **kwargs):
            options.append(kwargs)
            return "请查看卡片。", ""

        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(client, "stream_completion", completion), \
                patch.object(generate, "chat_with_reasoning", block_completion):
            spec = {"max_tokens": 1000, "context_tokens": 8000}
            await orchestrator.run("query", llm=spec)
            _ = [event async for event in orchestrator.run_stream("query", llm=spec)]
        assert len(options) == 2 and all("max_tokens" not in row for row in options)
        assert all(row == {"no_think": True} for row in options)
        assert client._spec_override("max_tokens") == 1000
        assert client._spec_override("context_tokens") == 8000

    asyncio.run(check())


def test_repetition_closes_upstream_immediately():
    async def check():
        closed = []

        async def retrieve(*args, **kwargs):
            return _data()

        async def repeated(*args, **kwargs):
            try:
                for _ in range(20):
                    yield "text", "杭州、宁波、绍兴、湖州、嘉兴、桐乡、海宁、临平、台州、"
            finally:
                closed.append(True)

        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(client, "stream_completion", repeated):
            events = [event async for event in orchestrator.run_stream("query")]
        assert closed
        assert any(event["type"] == "replace" for event in events)
        done = next(event for event in events if event["type"] == "done")
        assert done["truncated"] and done["truncate_reason"] == "repetition"

    asyncio.run(check())


def test_stream_outcomes_visibility_and_close():
    async def check():
        closed = []

        async def stream(*args, **kwargs):
            try:
                yield "think", "checking"
                yield "text", "请查看结果。"
                yield "text", "查询完成。"
            finally:
                closed.append(True)

        async def failing(*args, **kwargs):
            raise LLMUnavailable("injected model failure")
            yield "text", ""

        async def collect(data, completion=stream):
            async def retrieve(*args, **kwargs):
                return data
            with patch.object(planner, "decide", _decide), \
                    patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                    patch.object(client, "stream_completion", completion):
                return [event async for event in orchestrator.run_stream("G1明天经停哪些站？")]

        before = metrics.snapshot()
        events = await collect(_data(structured=True))
        after = metrics.snapshot()
        assert closed and not any(event["type"] == "think" for event in events)
        assert after["outcomes"]["successful"] == before["outcomes"]["successful"] + 1
        assert after["stages"]["card_delivery"]["count"] > before["stages"].get("card_delivery", {}).get("count", 0)

        before = after
        events = await collect(_data(failed=True))
        after = metrics.snapshot()
        assert after["outcomes"]["partial"] == before["outcomes"]["partial"] + 1
        assert not any(event["type"] in {"think", "answer"} for event in events)

        before = after
        with patch.object(orchestrator, "_rule_fallback", return_value=""):
            events = await collect(_data(), failing)
        after = metrics.snapshot()
        assert after["requests"]["completed"] == before["requests"]["completed"] + 1
        assert after["outcomes"]["failed"] == before["outcomes"]["failed"] + 1
        assert next(event for event in events if event["type"] == "done")["answer_done"] is False

        before = after
        with patch.object(orchestrator, "_rule_fallback", return_value="规则降级说明"):
            await collect(_data(), failing)
        after = metrics.snapshot()
        assert after["outcomes"]["degraded"] == before["outcomes"]["degraded"] + 1

        async def retrieve(*args, **kwargs):
            return _data()

        closed.clear()
        before = metrics.snapshot()
        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(client, "stream_completion", stream):
            gen = orchestrator.run_stream("query")
            while (await gen.__anext__())["type"] != "answer":
                pass
            await gen.aclose()
        after = metrics.snapshot()
        assert closed, "outer stream close must immediately release upstream"
        assert after["outcomes"]["cancelled"] == before["outcomes"]["cancelled"] + 1
        assert after["stages"]["first_public_think"]["count"] > 0
        assert after["stages"]["first_public_answer"]["count"] > 0

        # Stop consumption exactly at done. This is successful completion, not
        # cancellation just because the caller does not request one more event.
        before = after
        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(client, "stream_completion", stream):
            gen = orchestrator.run_stream("query")
            while (await gen.__anext__())["type"] != "done":
                pass
            await gen.aclose()
        after = metrics.snapshot()
        assert after["outcomes"]["successful"] == before["outcomes"]["successful"] + 1
        assert after["outcomes"]["cancelled"] == before["outcomes"]["cancelled"]

    asyncio.run(check())


def test_block_outcomes_and_cancellation():
    async def check():
        async def retrieve(*args, **kwargs):
            return _data(failed=True)

        async def answer(*args, **kwargs):
            return "查询说明", [], ""

        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(generate, "generate", answer):
            before = metrics.snapshot()
            await orchestrator.run("query")
            after = metrics.snapshot()
            assert after["outcomes"]["partial"] == before["outcomes"]["partial"] + 1

        async def fail(*args, **kwargs):
            raise LLMUnavailable("injected")

        with patch.object(planner, "decide", _decide), \
                patch.object(orchestrator.retrieve, "retrieve", retrieve), \
                patch.object(generate, "generate", fail), \
                patch.object(orchestrator, "_rule_fallback", return_value="降级"):
            before = metrics.snapshot()
            result = await orchestrator.run("query")
            after = metrics.snapshot()
            assert result.degraded
            assert after["outcomes"]["degraded"] == before["outcomes"]["degraded"] + 1

        started = asyncio.Event()

        async def wait(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch.object(planner, "decide", wait):
            before = metrics.snapshot()
            task = asyncio.create_task(orchestrator.run("query"))
            await started.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            after = metrics.snapshot()
            assert after["outcomes"]["cancelled"] == before["outcomes"]["cancelled"] + 1

    asyncio.run(check())


def main():
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print("PASS", name)


if __name__ == "__main__":
    main()
