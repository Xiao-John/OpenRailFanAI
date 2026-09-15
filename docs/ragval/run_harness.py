#!/usr/bin/env python3
"""RAG-EVAL 执行 Agent：把 testset.txt 逐条打成 /api/chat 请求，落证据。

用法（仓库根目录）：
    backend/.venv/bin/python docs/ragval/run_harness.py            # 全部用例
    backend/.venv/bin/python docs/ragval/run_harness.py A01 C01    # 指定编号
    backend/.venv/bin/python docs/ragval/run_harness.py --out run/evidence_r2.jsonl

产物：docs/ragval/run/evidence.jsonl（逐条追加，含 intent/slots/answer/tool_trace/sources）
多轮用例：按 `;;` 拆轮，顺序调用并把前轮问答压进 history；轮次引用（如"先问 A03"）
自动从上文证据里取该用例的问题与回答作为历史。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTSET = Path(__file__).resolve().parent / "testset.txt"
RUNDIR = Path(__file__).resolve().parent / "run"
API = "http://127.0.0.1:8000/api/chat"

CONNECTORS = ("先问", "先说", "再问", "再说", "最后问", "最后说", "最后", "然后说", "然后",
              "接着问", "接着", "先", "再")
CASE_ID = re.compile(r"^[A-Z]\d{2,}$")


def load_cases(only: set[str] | None = None) -> list[dict]:
    cases = []
    for line in TESTSET.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 9:
            print(f"!! 跳过字段数异常的行({len(parts)}): {line[:60]}", flush=True)
            continue
        cid, cat, mode, question, expect, checks, veto, source, note = parts
        cid_clean = cid.replace("★", "").strip()
        if only and cid_clean not in only:
            continue
        cases.append({
            "id": cid_clean, "star": "★" in cid, "category": cat, "mode": mode,
            "question": question, "expect": expect, "checks": checks,
            "veto": veto, "source": source, "note": note,
        })
    return cases


def split_turns(question: str) -> list[str]:
    """把用例的"我要问的话"还原成可直接发送的车迷原话列表。"""
    if ";;" not in question:
        # 单轮里若含"下午再问「…」"这类描述，取引号内的真实问法
        quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", question)
        head = question.split(quoted[0])[0] if quoted else ""
        if quoted and re.search(r"(再问|问一下|改成|故意|写成)", head):
            return [quoted[-1].strip()]
        return [question.strip()]

    turns = []
    for raw in question.split(";;"):
        t = raw.replace("“", "").replace("”", "").replace('"', "").strip()
        changed = True
        while changed:
            changed = False
            for c in CONNECTORS:
                if t.startswith(c):
                    t = t[len(c):].strip("，,、:： ")
                    changed = True
        if t:
            turns.append(t)
    return turns


def call(message: str, history: list[dict], timeout: int = 300) -> dict:
    body = {"message": message, "history": history}
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
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


def call_with_retry(message: str, history: list[dict], retries: int = 1) -> dict:
    res = call(message, history)
    for _ in range(retries):
        if "answer" in res and res.get("answer"):
            break
        time.sleep(3)
        res = call(message, history)
    return res


def main() -> None:
    args = sys.argv[1:]
    out_path = RUNDIR / "evidence.jsonl"
    only: set[str] | None = None
    if "--out" in args:
        i = args.index("--out")
        out_path = RUNDIR / args[i + 1]
        del args[i:i + 2]
    if args:
        only = set(args)

    RUNDIR.mkdir(parents=True, exist_ok=True)
    cases = load_cases(only)
    answers: dict[str, dict] = {}          # 供多轮"先问 XX"引用
    if out_path.exists():                  # 复跑时复用已有证据做引用
        for ln in out_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(ln)
                answers[row["id"]] = row
            except Exception:  # noqa: BLE001
                pass

    print(f"用例 {len(cases)} 条 → {out_path}", flush=True)
    with out_path.open("a", encoding="utf-8") as f:
        for idx, case in enumerate(cases, 1):
            turns = split_turns(case["question"])
            history: list[dict] = []
            turn_rows = []
            print(f"\n{'='*90}\n[{idx}/{len(cases)}] {case['id']}{'★' if case['star'] else ''}"
                  f" ({case['category']}/{case['mode']}) 轮数={len(turns)}", flush=True)
            for ti, msg in enumerate(turns, 1):
                if CASE_ID.match(msg.strip()):
                    ref = answers.get(msg.strip())
                    if ref:
                        print(f"  T{ti} 引用 {msg.strip()} 的历史", flush=True)
                        history.append({"role": "user", "content": ref.get("message", "")})
                        history.append({"role": "assistant", "content": ref.get("answer", "")})
                        continue
                res = call_with_retry(msg, history)
                row = {
                    "id": case["id"], "turn": ti, "turns_total": len(turns),
                    "asked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "message": msg, "history_len": len(history),
                    "category": case["category"], "mode": case["mode"],
                    "checks": case["checks"], "veto": case["veto"],
                    "source": case["source"], "expect": case["expect"], "note": case["note"],
                    "intent": res.get("intent"), "question_type": res.get("question_type"),
                    "slots": res.get("slots"), "answer": res.get("answer"),
                    "sources": res.get("sources"), "tool_trace": res.get("tool_trace"),
                    "process_logs": res.get("process_logs"),
                    "latency_ms": res.get("latency_ms"), "elapsed": res.get("_elapsed"),
                    "error": res.get("error") or res.get("_error"),
                }
                turn_rows.append(row)
                print(f"  T{ti} 问：{msg}", flush=True)
                print(f"     intent={row['intent']} qtype={row['question_type']} "
                      f"{row['elapsed']}s err={row['error']}", flush=True)
                print(f"     答：{(row['answer'] or '')[:400]}", flush=True)
                print(f"     sources={len(row['sources'] or [])} "
                      f"tool_trace={len(row['tool_trace'] or [])}", flush=True)
                history.append({"role": "user", "content": msg})
                history.append({"role": "assistant", "content": res.get("answer") or ""})
            answers[case["id"]] = {
                "message": turns[0] if turns else case["question"],
                "answer": (turn_rows[-1].get("answer") if turn_rows else "") or "",
            }
            for row in turn_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()

    print(f"\n完成：证据写入 {out_path}", flush=True)


if __name__ == "__main__":
    main()
