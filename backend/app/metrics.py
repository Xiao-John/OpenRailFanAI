"""低基数、脱敏的进程内性能指标。

只记录阶段/工具/供应商标识及耗时、状态、token，不记录用户输入、Key、URL 或原始错误。
这是单进程快照；多 worker 部署需由外部指标后端聚合。
"""
from __future__ import annotations

import threading
import math
from collections import defaultdict, deque
from statistics import median
from typing import Any

_LOCK = threading.Lock()
_WINDOW = 512
_STAGES: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=_WINDOW))
_TOOLS: dict[str, dict[str, Any]] = {}
_LLM: dict[str, dict[str, Any]] = {}
_REQUESTS = {"completed": 0, "failed": 0, "cancelled": 0}
_OUTCOMES = {"successful": 0, "partial": 0, "degraded": 0, "failed": 0, "cancelled": 0}
_CACHE: dict[str, dict[str, int]] = {}
_OPERATIONS: dict[str, dict[str, int]] = {}


def _summary(samples: deque[float]) -> dict[str, int | float]:
    values = sorted(samples)
    if not values:
        return {"count": 0, "p50_ms": 0, "p95_ms": 0}
    p95 = values[math.ceil(len(values) * 0.95) - 1]
    return {"count": len(values), "p50_ms": round(median(values), 1),
            "p95_ms": round(p95, 1)}


def record_request(status: str, *, degraded: bool = False, tool_failed: bool = False,
                   model_failed: bool = False, truncated: bool = False) -> None:
    if status not in _REQUESTS:
        return
    with _LOCK:
        _REQUESTS[status] += 1
        outcome = ("cancelled" if status == "cancelled" else
                   "failed" if status == "failed" else
                   "degraded" if degraded else "failed" if model_failed else
                   "partial" if tool_failed or truncated else "successful")
        _OUTCOMES[outcome] += 1


def record_cache(name: str, status: str) -> None:
    """Only internal fixed names/statuses; never cache keys or request values."""
    if status not in {"hit", "miss", "joined", "expired", "evicted", "abandoned",
                      "started", "used", "unused", "cancelled"}:
        return
    with _LOCK:
        row = _CACHE.setdefault(name, {})
        row[status] = row.get(status, 0) + 1


def record_operation(name: str, elapsed_ms: float, ok: bool) -> None:
    record_stage("io." + name, elapsed_ms)
    with _LOCK:
        row = _OPERATIONS.setdefault(name, {"calls": 0, "failed": 0})
        row["calls"] += 1
        row["failed"] += int(not ok)


def record_stage(name: str, elapsed_ms: float) -> None:
    with _LOCK:
        _STAGES[name].append(max(0.0, float(elapsed_ms)))


def record_tool(name: str, ok: bool, elapsed_ms: float) -> None:
    with _LOCK:
        row = _TOOLS.setdefault(name, {"calls": 0, "ok": 0, "failed": 0,
                                       "latencies_ms": deque(maxlen=_WINDOW)})
        row["calls"] += 1
        row["ok" if ok else "failed"] += 1
        row["latencies_ms"].append(max(0.0, float(elapsed_ms)))


def record_llm(provider: str, elapsed_ms: float, prompt_tokens: int = 0,
               completion_tokens: int = 0, failed: bool = False, cancelled: bool = False) -> None:
    # provider 必须是内部目录 id 或清理后的短标签；调用方不得传 URL/Key。
    key = str(provider or "unknown")[:40]
    with _LOCK:
        row = _LLM.setdefault(key, {"calls": 0, "failed": 0, "cancelled": 0, "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "latencies_ms": deque(maxlen=_WINDOW)})
        row["calls"] += 1
        row["failed"] += int(bool(failed))
        row["cancelled"] += int(bool(cancelled))
        row["prompt_tokens"] += max(0, int(prompt_tokens or 0))
        row["completion_tokens"] += max(0, int(completion_tokens or 0))
        row["latencies_ms"].append(max(0.0, float(elapsed_ms)))


def record_llm_failure(provider: str, elapsed_ms: float) -> None:
    record_llm(provider, elapsed_ms, failed=True)


def snapshot() -> dict[str, Any]:
    """返回近 512 个样本的阶段与工具延迟，以及进程启动以来的计数。"""
    with _LOCK:
        stages = {k: _summary(v) for k, v in _STAGES.items()}
        tools = {k: {"calls": v["calls"], "ok": v["ok"], "failed": v["failed"],
                     **_summary(v["latencies_ms"])} for k, v in _TOOLS.items()}
        llm = {k: {"calls": v["calls"], "failed": v["failed"], "cancelled": v["cancelled"],
                   "prompt_tokens": v["prompt_tokens"],
                   "completion_tokens": v["completion_tokens"],
                   **_summary(v["latencies_ms"])} for k, v in _LLM.items()}
        return {"scope": "process", "window": _WINDOW, "requests": dict(_REQUESTS),
                "outcomes": dict(_OUTCOMES), "caches": {k: dict(v) for k, v in _CACHE.items()},
                "operations": {k: dict(v) for k, v in _OPERATIONS.items()},
                "stages": stages, "tools": tools, "llm": llm}
