#!/usr/bin/env python3
"""Measure a snapshot of the current backend without changing production files.

Uses the existing cloud configuration. Secrets stay in child-process environment;
artifacts contain timing, fixed scenario identifiers, counts and status only.
No clients, build configuration, acceptance artifacts or existing services change.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]

SCENARIOS = [
    ("routing", {"message": "G1今天由哪组动车组担当？"}),
    ("tickets", {"message": "明天北京到上海的高铁还有票吗？"}),
    ("station_screen", {"message": "北京南站大屏今天下午有哪些高铁？"}),
    ("knowledge", {"message": "CR400AF 为什么叫复兴号？"}),
    ("photo_spot", {"message": "吉林市XX区，要拍 CR400AF，今天下午"}),
]
EXTRA_SCENARIOS = [
    ("schedule", {"message": "G1明天经停哪些站？"}),
    ("batch", {"message": "查G1和G2明天的时刻表", "display_action": {
        "kind": "train_schedule_batch", "trains": ["G1", "G2"], "date": "明天"}}),
    ("fare", {"message": "明天北京南到上海虹桥G1的二等座票价多少钱？"}),
    ("mileage", {"message": "北京南到济南西多少公里？"}),
    ("multiturn", {"message": "那后天呢？", "history": [
        {"role": "user", "content": "G1明天经停哪些站？"},
        {"role": "assistant", "content": "此前查询了G1明天的经停站。"}]}),
]

# Instrumentation lives only in the temporary snapshot's launcher.
LAUNCHER = r'''
import asyncio, contextvars, json, logging, os, time
from pathlib import Path
import uvicorn
from app.pipeline import orchestrator, planner, retrieve, generate
from app.llm import client
from app.tools import registry
SPAN = contextvars.ContextVar("bench_span", default=None)
counter = 0
def rel(span): return round((time.perf_counter()-span["started"])*1000, 3)
def wrap_async(module, name, target):
    original = getattr(module, name)
    async def wrapped(*args, **kwargs):
        span=SPAN.get(); start=time.perf_counter()
        try:
            return await original(*args, **kwargs)
        finally:
            if span is not None:
                span.setdefault("timed_calls", []).append({"name":target,
                    "elapsed_ms":round((time.perf_counter()-start)*1000,3)})
    setattr(module, name, wrapped)
wrap_async(planner,"decide","decision")
wrap_async(retrieve,"retrieve","retrieve")
original_prompt=generate.build_prompt
def prompt(*args, **kwargs):
    value=original_prompt(*args, **kwargs); span=SPAN.get()
    if span is not None:
        span["generation_prompt_chars"]=len(value)
        span["generation_input_estimate_tokens"]=sum(client.estimate_tokens(m.get("content",""))
            for m in client.build_messages(value, client._SYSTEM_ASSISTANT, None))
    return value
generate.build_prompt=prompt
original_tool=registry.invoke_by_name
async def tool(name, params):
    span=SPAN.get(); start=time.perf_counter(); ok=False
    try:
        result=await original_tool(name,params); ok=bool(result.ok); return result
    finally:
        if span is not None: span.setdefault("tool_calls",[]).append({"name":name,"ok":ok,
            "elapsed_ms":round((time.perf_counter()-start)*1000,3)})
registry.invoke_by_name=tool
for name in ("_create_once","_create_stream"):
    original=getattr(client,name)
    def factory(original, name):
        async def attempt(sdk, ladder, kw):
            span=SPAN.get()
            if span is not None: span.setdefault("llm_attempts",[]).append({
                "mode":name,"dialect":ladder.dialect,"at_ms":rel(span)})
            return await original(sdk,ladder,kw)
        return attempt
    setattr(client,name,factory(original,name))
original_stream=client.stream_completion
async def stream(*args,**kwargs):
    span=SPAN.get(); start=time.perf_counter()
    if span is not None: span["generation_start_ms"]=rel(span)
    upstream=original_stream(*args,**kwargs)
    try:
        async for kind,text in upstream:
            if span is not None and text:
                key="upstream_first_"+("thinking" if kind=="think" else "text")+"_ms"
                span.setdefault(key,rel(span))
                chars="upstream_"+("thinking" if kind=="think" else "text")+"_chars"
                span[chars]=span.get(chars,0)+len(text)
            yield kind,text
    finally:
        await upstream.aclose()
        if span is not None: span["generation_elapsed_ms"]=round((time.perf_counter()-start)*1000,3)
client.stream_completion=stream
original_run=orchestrator.run_stream
async def run(*args,**kwargs):
    global counter
    counter+=1; span={"ordinal":counter,"started":time.perf_counter()}
    token=SPAN.set(span); upstream=original_run(*args,**kwargs)
    try:
        async for event in upstream: yield event
    finally:
        await upstream.aclose()
        span["server_elapsed_ms"]=rel(span); span.pop("started",None)
        with open(os.environ["BENCH_TELEMETRY"],"a",encoding="utf-8") as f:
            f.write(json.dumps(span,ensure_ascii=False)+"\n")
        SPAN.reset(token)
orchestrator.run_stream=run
from app.main import app
uvicorn.run(app,host="127.0.0.1",port=int(os.environ["BENCH_PORT"]),
            log_level="error",access_log=False)
'''


def summary(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    import math
    return {"count": len(values), "median": round(statistics.median(values), 3),
            "p95_nearest_rank": round(values[math.ceil(len(values)*.95)-1], 3),
            "min": round(values[0], 3), "max": round(values[-1], 3)}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    path.chmod(0o600)


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*.py"))}


@contextmanager
def diagnostic_log(out, secrets):
    """Protect diagnostics immediately and redact even if measurement aborts."""
    descriptor = os.open(out / "server.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as log:
            yield log
    finally:
        for path in out.iterdir():
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                for secret in secrets:
                    content = content.replace(secret, "[REDACTED]")
                path.write_text(content, encoding="utf-8")
                path.chmod(0o600)


async def measure(base_url, out, pid, rounds):
    import httpx
    samples = []
    health = []
    resources = []
    stop = asyncio.Event()
    async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=180) as http:
        for _ in range(100):
            try:
                if (await http.get("/health", timeout=1)).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.1)
        else:
            raise RuntimeError("snapshot_startup_failed")
        startup = {"health": (await http.get("/health")).json(),
                   "metrics": (await http.get("/api/metrics")).json()}
        async def monitor():
            while not stop.is_set():
                start = time.perf_counter()
                try:
                    response = await http.get("/health", timeout=2)
                    health.append({"elapsed_ms": (time.perf_counter()-start)*1000,
                                   "ok": response.status_code == 200})
                except httpx.HTTPError:
                    health.append({"elapsed_ms": (time.perf_counter()-start)*1000, "ok": False})
                proc = await asyncio.create_subprocess_exec("ps", "-o", "rss=,pcpu=", "-p", str(pid),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                output, _ = await proc.communicate()
                fields = output.decode().split()
                if len(fields) == 2:
                    resources.append({"rss_kib": int(fields[0]), "cpu_pct": float(fields[1])})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=.5)
                except asyncio.TimeoutError:
                    pass
        watcher = asyncio.create_task(monitor())
        try:
            sequence = [(name, body, i+1) for i in range(rounds) for name, body in SCENARIOS]
            sequence += [(name, body, 1) for name, body in EXTRA_SCENARIOS]
            for ordinal, (name, body, repeat) in enumerate(sequence, 1):
                started = time.perf_counter()
                record = {"ordinal": ordinal, "scenario": name, "repeat": repeat,
                          "events": {}, "stages_ms": {}, "first_answer_ms": None,
                          "first_thinking_ms": None, "first_event_ms": None,
                          "done_received": False}
                done = None
                try:
                    async with http.stream("POST", "/api/chat/stream", json={
                        **body, "session_id": "performance-bench"}) as response:
                        record["http_status"] = response.status_code
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            event = json.loads(line[6:]); kind = event.get("type", "unknown")
                            elapsed = (time.perf_counter()-started)*1000
                            record["events"][kind] = record["events"].get(kind, 0)+1
                            if record["first_event_ms"] is None: record["first_event_ms"] = elapsed
                            if kind in ("answer", "think"):
                                key = "first_answer_ms" if kind == "answer" else "first_thinking_ms"
                                if record[key] is None: record[key] = elapsed
                            if kind == "stage": record["stages_ms"][event["stage"]] = event.get("ms")
                            if kind == "done":
                                done = event
                                record["done_ms"] = elapsed
                    record["e2e_ms"] = (time.perf_counter()-started)*1000
                    record["done_received"] = done is not None
                    if done is not None:
                        for key in ("planner", "question_type", "answer_done", "degraded",
                                    "truncated", "truncate_reason", "usage", "latency_ms", "tool_trace"):
                            record[key] = done.get(key)
                        record["has_error"] = bool(done.get("error")) or bool(record["events"].get("error"))
                        record["cards"] = [{"kind": item.get("kind"), "status": item.get("status"),
                            "schema_version": item.get("schema_version"),
                            "items": [{"status": row.get("status")} for row in item.get("items", [])]}
                            for item in done.get("display_results", [])]
                except Exception as exc:
                    record["exception_type"] = type(exc).__name__
                    record["e2e_ms"] = (time.perf_counter()-started)*1000
                samples.append(record)
                save(out / "samples.json", samples)
                print(json.dumps({"ordinal": ordinal, "scenario": name, "repeat": repeat,
                    "seconds": round(record["e2e_ms"]/1000, 2), "error": record.get("has_error"),
                    "tool_trace": record.get("tool_trace"), "cards": record.get("cards")},
                    ensure_ascii=False), flush=True)
                await asyncio.sleep(1)
            save(out / "metrics.json", (await http.get("/api/metrics")).json())
        finally:
            stop.set()
            await watcher
            save(out / "local_responsiveness.json", {"health_latency_ms": summary([
                row["elapsed_ms"] for row in health]), "failed": sum(not row["ok"] for row in health),
                "samples": health, "resources": resources,
                "rss_peak_kib": max((row["rss_kib"] for row in resources), default=0)})
            save(out / "startup.json", startup)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--source-root", type=Path,
                        help="Source workspace to snapshot (defaults to this script's workspace)")
    parser.add_argument("--config-root", type=Path,
                        help="Read existing configuration here; never copy its secret files")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5:
        parser.error("--rounds must be between 1 and 5")
    source_root = (args.source_root or ROOT).resolve()
    config_root = (args.config_root or ROOT).resolve()
    os.chdir(config_root / "backend")
    sys.path.insert(0, str(config_root / "backend"))
    from app.config import get_settings
    from app.llm.providers import resolve_provider
    settings = get_settings(); provider = resolve_provider(settings=settings)
    if settings.llm_mock or not provider.ready:
        raise SystemExit("Existing configuration must be a ready, non-mock cloud provider.")
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    out = (args.out or ROOT / ".ai" / "backend-performance" / run_id).resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_before = hashes(source_root / "backend" / "app")
    manifest = {"run_id": run_id, "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "workspace": str(source_root), "config_workspace": str(config_root),
        "python": sys.version.split()[0], "machine": platform.machine(),
        "rounds": args.rounds, "provider": provider.id, "model": provider.model,
        "structured_model": provider.effective_structured_model, "base_url": provider.base_url,
        "api": provider.api, "mock": settings.llm_mock, "has_key": bool(provider.api_key),
        "settings": {key: getattr(settings, key) for key in ("llm_max_tokens", "llm_context_tokens",
            "tool_concurrency", "fastpath_enabled", "llm_generation_no_think",
            "llm_generation_compact_prompt", "fact_text_max_chars", "fact_table_max_rows", "fact_max_entries")},
        "source_sha256": source_before, "scenarios": [name for name, _ in SCENARIOS+EXTRA_SCENARIOS],
        "scenario_requests": {name: body for name, body in SCENARIOS+EXTRA_SCENARIOS},
        "instrumentation": "temporary launcher wrappers; production source copied unchanged"}
    save(out / "manifest.json", manifest)
    print("Artifacts: " + str(out), flush=True)
    with tempfile.TemporaryDirectory(prefix="openrailfan-perf-") as temp:
        snapshot = Path(temp)
        shutil.copytree(source_root / "backend" / "app", snapshot / "backend" / "app",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(source_root / "frontend", snapshot / "frontend",
                        ignore=shutil.ignore_patterns("tests", ".DS_Store"))
        shutil.copy2(source_root / "VERSION", snapshot / "VERSION")
        manifest["snapshot_sha256"] = hashes(snapshot / "backend" / "app")
        if manifest["snapshot_sha256"] != source_before:
            raise RuntimeError("source_changed_during_snapshot")
        env = dict(os.environ)
        for key, value in settings.dict().items():
            env[key.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
        from app.data.dict import db_path
        source_db = db_path()
        if source_db.exists():
            target_db = snapshot / "dict.db"
            with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as original, sqlite3.connect(target_db) as target:
                original.backup(target)
            env["DICT_DB_PATH"] = str(target_db)
            manifest["dictionary_bytes"] = target_db.stat().st_size
        else:
            env["DICT_DB_PATH"] = str(snapshot / "absent.db")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0)); port = reservation.getsockname()[1]
        env.update({"PYTHONPATH": str(snapshot / "backend"), "PYTHONDONTWRITEBYTECODE": "1",
                    "APP_VARIANT": "main", "FRONTEND_DIR": str(snapshot / "frontend"),
                    "BENCH_PORT": str(port), "BENCH_TELEMETRY": str(out / "telemetry.jsonl")})
        manifest.update(snapshot=str(snapshot), port=port)
        save(out / "manifest.json", manifest)
        launcher = snapshot / "backend" / "bench_launcher.py"
        launcher.write_text(LAUNCHER, encoding="utf-8")
        from app.llm.providers import load_providers
        secrets = [p.api_key for p in load_providers(settings).values() if p.api_key]
        with diagnostic_log(out, secrets) as log:
            process = subprocess.Popen([sys.executable, str(launcher)], cwd=snapshot / "backend",
                                       env=env, stdout=log, stderr=log)
            try:
                samples = asyncio.run(measure(f"http://127.0.0.1:{port}", out, process.pid, args.rounds))
                save(out / "summary.json", {name: {"e2e_ms": summary([
                    row["e2e_ms"] for row in samples if row["scenario"] == name]),
                    "errors": sum(bool(row.get("has_error") or row.get("exception_type")
                                       or not row.get("done_received"))
                        for row in samples if row["scenario"] == name)}
                    for name, _ in SCENARIOS+EXTRA_SCENARIOS})
            finally:
                process.terminate()
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                manifest.update(finished_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    source_unchanged=hashes(source_root / "backend" / "app") == source_before)
                save(out / "manifest.json", manifest)
        print("Completed: " + str(out), flush=True)


if __name__ == "__main__":
    main()
