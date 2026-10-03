"""Structured rail tool calls must not stream unchecked model prose or reasoning."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from app.pipeline import orchestrator
from app.pipeline.extract import Slots
from app.pipeline.intent import Intent


async def _events(*, failed: bool, structured: bool = True) -> list[dict]:
    async def decide(*args, **kwargs):  # noqa: ARG001
        return Intent.SCHEDULE if structured else Intent.GENERAL, "realtime", Slots(target="G1"), "deterministic", ""

    async def retrieve(*args, **kwargs):  # noqa: ARG001
        return {
            "data": [] if failed else [{"tool": "train.schedule", "data": {"train_code": "G1"}}] if structured else [],
            "display_errors": [{"tool": "train.schedule", "train_code": "G1", "message": "unavailable"}] if failed else [],
            "sources": [], "tool_trace": ["train.schedule: failed" if failed else "train.schedule: ok"],
        }

    async def stream(*args, **kwargs):  # noqa: ARG001
        yield "think", "Unverified departure at 09:00"
        yield "text", "G1 departs at 09:00 from A station"

    with patch.object(orchestrator.planner, "decide", decide), \
            patch.object(orchestrator.retrieve, "retrieve", retrieve), \
            patch.object(orchestrator.llm_client, "stream_completion", stream):
        return [event async for event in orchestrator.run_stream("G1 timetable")]


def test_structured_success_and_tool_failure_hide_model_fields():
    records = []
    for failed in (False, True):
        events = asyncio.run(_events(failed=failed))
        done = next(event for event in events if event["type"] == "done")
        assert not any(event["type"] in {"think", "answer", "replace"} for event in events)
        assert done["thinking"] == ""
        records.append({"case":"tool_failure" if failed else "tool_success",
                        "event_types":[event["type"] for event in events],
                        "answer_events":0,"thinking_length":len(done["thinking"]),
                        "unsafe_fact_text_in_events":False})
    root = Path(__file__).resolve().parents[2]
    output = root / "frontend/tests/visual/screenshots/ft1_sse_visibility.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"cases":records},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def test_unstructured_answer_and_thinking_remain_streaming():
    events = asyncio.run(_events(failed=False, structured=False))
    assert any(event["type"] == "think" for event in events)
    assert any(event["type"] == "answer" for event in events)


if __name__ == "__main__":
    test_structured_success_and_tool_failure_hide_model_fields()
    test_unstructured_answer_and_thinking_remain_streaming()
    print("structured SSE visibility tests passed")
