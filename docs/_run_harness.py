#!/usr/bin/env python3
"""车迷测试集执行器（只读：调用 /api/chat，记录原文证据）。

用法:
    .venv/bin/python docs/_run_harness.py A01 A02 B03 ...
    .venv/bin/python docs/_run_harness.py --file cases.json

每个 case: {"id": "A01", "message": "...", "history": [{"role":..,"content":..}]}
结果写入 docs/_results.jsonl（逐条追加/覆盖），便于生成报告。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/api/chat"
OUT = Path(__file__).resolve().parent / "_results.jsonl"


def call(message: str, history: list[dict] | None = None, timeout: int = 240) -> dict:
    body = {"message": message, "history": history or []}
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        data["_elapsed"] = round(time.time() - t0, 1)
        return data
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}", "_elapsed": round(time.time() - t0, 1)}


def record(case: dict, result: dict) -> None:
    row = {
        "id": case["id"],
        "asked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "message": case["message"],
        "history_len": len(case.get("history") or []),
        "intent": result.get("intent"),
        "question_type": result.get("question_type"),
        "slots": result.get("slots"),
        "answer": result.get("answer"),
        "sources": result.get("sources"),
        "error": result.get("error") or result.get("_error"),
        "elapsed": result.get("_elapsed"),
    }
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--file":
        cases = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    else:
        cases = []
        for a in args:
            if "=" in a:
                cid, msg = a.split("=", 1)
                cases.append({"id": cid, "message": msg})
    for case in cases:
        print(f"\n{'='*100}\n[{case['id']}] 问：{case['message']}\n", flush=True)
        res = call(case["message"], case.get("history"))
        record(case, res)
        if res.get("_error") and "answer" not in res:
            print(f"  !! 调用失败: {res['_error']}", flush=True)
            continue
        print(f"  intent={res.get('intent')} qtype={res.get('question_type')} "
              f"elapsed={res.get('_elapsed')}s", flush=True)
        print(f"  slots={json.dumps(res.get('slots'), ensure_ascii=False)}", flush=True)
        print(f"  答：{res.get('answer')}", flush=True)
        srcs = res.get("sources") or []
        print(f"  来源({len(srcs)}): {json.dumps(srcs, ensure_ascii=False)[:600]}", flush=True)


if __name__ == "__main__":
    main()
