#!/usr/bin/env python3
"""决策层模型基准：拿**真实语料**跑 planner，量出"这个模型当决策器够不够用"。

为什么需要它
------------
`tests/corpus/intent_corpus.jsonl` 是唯一的真源，但 `tests/test_corpus.py` **只跑
确定性快路径**（无网络）。于是"换模型"这件事此前没有可比较的数字：
谁也没量过"某模型在**交回 LLM 的那 79 条**上意图/槽位判对多少"。

本脚本把同一份语料喂给**真实模型**（云端或本地小模型），指标：

- **假接管**：`route=llm`（标注"必须交回模型"）的用例被快路径抢走 —— 红线，必须为 0；
  这条与模型无关（快路径不打模型），但放在同一张表里才看得见全貌。
- **意图准确率**：模型定下的 intent 与语料标注是否一致（只统计**真的由模型决定**的行）。
- **问题性质准确率**：realtime / knowledge / mixed。
- **槽位准确率**：逐字段比对语料 `expect.slots` 里声明过的键。
- **输出可用率**：结构化调用里有多少次**根本没能解析成 JSON**
  —— 小模型最先崩的就是这里，且它的后果比"判错"严重（判错还能靠规则兜，连 JSON 都不是就只能降级）。
- **延迟**：p50 / p95（串行，含网络/推理时间）。

用法（在仓库根执行）
--------------------
    # 本地 Ollama（先用 scripts/setup_local_model.sh 拉起）
    backend/.venv/bin/python scripts/bench_planner_model.py \
        --provider ollama --model minicpm5-2b:2b --out /tmp/slm.json

    # 云端基线，供对照
    backend/.venv/bin/python scripts/bench_planner_model.py \
        --provider deepseek --model deepseek-flash --out /tmp/cloud.json

    # 两份结果对比
    backend/.venv/bin/python scripts/bench_planner_model.py --compare /tmp/slm.json /tmp/cloud.json

**先跑基线再换本地模型**：没有对照的绝对分数说明不了任何事。

注意：本脚本会**真发请求**（79 条 LLM 用例 × 每条 1–2 次调用），会用掉 token/算力。
`--limit` 可先小跑几条探路。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

CORPUS = ROOT / "backend" / "tests" / "corpus" / "intent_corpus.jsonl"

# 语料里 route=llm 是"必须交回模型"；either 是"交不交都行，交了就得对"
_MODEL_ROUTES = ("llm", "either")


def load_corpus() -> list[dict]:
    return [json.loads(ln) for ln in CORPUS.read_text(encoding="utf-8").splitlines() if ln.strip()]


_STATION_KEYS: set[str] | None = None


def _station_keys() -> set[str]:
    """本地 3384 站规范名集合（键**不带**「站」字：`武昌` 而不是 `武昌站`）。

    懒加载 + 失败即空集：打分器不该因为站点库没就绪就整体崩掉。
    """
    global _STATION_KEYS
    if _STATION_KEYS is None:
        try:
            import asyncio

            from app.tools import _rt12306 as rt

            asyncio.get_event_loop()
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.tools._rt12306 import all_stations

            _STATION_KEYS = set(all_stations().keys())
        except Exception:  # noqa: BLE001
            _STATION_KEYS = set()
    return _STATION_KEYS


def _norm_slot(key: str, value):
    """槽位值归一化后再比较 —— **只归一化"同一件事的不同写法"**，不放过真错。

    修的是这一类：模型答 `武昌站` / `郑州站`，语料写 `武昌` / `郑州`，
    精确字符串比较把它记成错。实测这样冤枉了 4B 至少 6 条 ——
    而"答对了却扣分"比"答错了没扣分"更坏：它会让选型结论整体偏悲观。

    规则**故意收得很紧**：只有当"去掉末尾「站」之后确实是本地站点库里的规范名"时才归一化。
    这样 `北京南站`→`北京南` 会归一化，而编造的站名不会被顺手洗白。
    """
    if not isinstance(value, str):
        return value
    v = value.strip()
    if key in ("location", "target") and v.endswith("站"):
        stripped = v[:-1]
        if stripped and stripped in _station_keys():
            return stripped
    return v


def _slots_of(slots) -> dict:
    return {k: getattr(slots, k, None) for k in ("location", "target", "time", "direction", "extra")}


def _classify_error(e: Exception) -> str:
    """把失败归到"模型输出不可用"还是"上游/配置故障"——两者的处理方式完全不同。"""
    text = str(e)
    # client._parse_json_object 抛的是 "LLM 返回非 JSON（无法解析结构化输出）"
    if "非 JSON" in text or "不是对象" in text:
        return "invalid_output"
    return "unavailable"


async def run_one(row: dict, sem: asyncio.Semaphore) -> dict:
    """跑一条用例，返回原始结果（不在这里判分，判分统一放到 score）。"""
    from app.llm.client import LLMUnavailable
    from app.pipeline import planner

    async with sem:
        t0 = time.perf_counter()
        try:
            intent, q_type, slots, who, defer = await planner.decide(row["message"], row.get("history"))
            return {
                "id": row["id"], "ok": True, "planner": who,
                "intent": intent.value, "question_type": q_type,
                "slots": _slots_of(slots), "defer_reason": defer,
                "ms": (time.perf_counter() - t0) * 1000.0,
            }
        except LLMUnavailable as e:
            return {
                "id": row["id"], "ok": False, "planner": "llm-failed",
                "error": _classify_error(e), "error_text": str(e)[:300],
                "ms": (time.perf_counter() - t0) * 1000.0,
            }
        except Exception as e:  # noqa: BLE001 —— 基准脚本要跑完全程，单条异常不该中断
            return {
                "id": row["id"], "ok": False, "planner": "error",
                "error": "crash", "error_text": f"{type(e).__name__}: {e}"[:300],
                "ms": (time.perf_counter() - t0) * 1000.0,
            }


def score(rows: list[dict], results: dict[str, dict]) -> dict:
    """按"谁做的决定"分桶判分：快路径接管的不算模型的账，反之亦然。"""
    stat = {
        "total": len(rows), "decided_by_model": 0, "decided_by_fastpath": 0, "failed": 0,
        "intent_ok": 0, "qtype_ok": 0, "slot_ok": 0, "slot_total": 0,
        "fake_takeover": [], "wrong_intent": [], "wrong_slots": [], "failures": [],
        "by_intent": defaultdict(lambda: [0, 0]),       # intent → [对, 总（模型决定的）]
        "errors": Counter(),
    }
    for row in rows:
        r = results.get(row["id"])
        exp = row["expect"]
        if r is None:
            continue
        if not r.get("ok"):
            stat["failed"] += 1
            stat["errors"][r.get("error", "?")] += 1
            stat["failures"].append((row["id"], r.get("error", "?"), r.get("error_text", "")))
            continue

        took = r["planner"] == "deterministic"
        # 红线：标注"必须交回模型"的行被快路径接管
        if took and exp["route"] == "llm":
            stat["fake_takeover"].append((row["id"], r["intent"], row["message"]))

        if took:
            stat["decided_by_fastpath"] += 1
            continue

        stat["decided_by_model"] += 1
        stat["by_intent"][exp["intent"]][1] += 1
        if r["intent"] == exp["intent"]:
            stat["intent_ok"] += 1
            stat["by_intent"][exp["intent"]][0] += 1
        else:
            stat["wrong_intent"].append((row["id"], exp["intent"], r["intent"], row["message"]))
        if r.get("question_type") == exp.get("question_type"):
            stat["qtype_ok"] += 1
        for k, v in (exp.get("slots") or {}).items():
            stat["slot_total"] += 1
            got = (r.get("slots") or {}).get(k)
            if _norm_slot(k, got) == _norm_slot(k, v):
                stat["slot_ok"] += 1
            else:
                stat["wrong_slots"].append((row["id"], k, v, got, row["message"]))
    return stat


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100.0 * a / b:.1f}%)" if b else "n/a"


def report(stat: dict, *, title: str, meta: dict, verbose: int) -> None:
    print("=" * 74)
    print(title)
    if meta:
        for k, v in meta.items():
            print(f"  {k}: {v}")
    print("=" * 74)
    print(f"用例总数            {stat['total']}")
    print(f"  快路径接管        {stat['decided_by_fastpath']}（不消耗模型，判定与模型无关）")
    print(f"  模型决定          {stat['decided_by_model']}")
    print(f"  彻底失败          {stat['failed']}  {dict(stat['errors']) or ''}")
    print()
    print(f"意图准确率          {_pct(stat['intent_ok'], stat['decided_by_model'])}")
    print(f"问题性质准确率      {_pct(stat['qtype_ok'], stat['decided_by_model'])}")
    print(f"槽位准确率          {_pct(stat['slot_ok'], stat['slot_total'])}")
    print()
    fake = stat["fake_takeover"]
    print(f"假接管（红线，须为 0） {len(fake)}")
    for cid, got, msg in fake[:10]:
        print(f"    ! {cid} 被接管为 {got}：{msg}")
    print()
    print("分意图准确率（仅模型决定的行）：")
    for intent in sorted(stat["by_intent"]):
        ok, tot = stat["by_intent"][intent]
        print(f"    {intent:<12} {_pct(ok, tot)}")

    if verbose:
        for label, key, fmt in (
            ("判错意图", "wrong_intent", lambda t: f"期望 {t[1]:<11} 实际 {t[2]:<11} {t[3]}"),
            ("槽位不符", "wrong_slots", lambda t: f"[{t[1]}] 期望 {t[2]!r} 实际 {t[3]!r}  {t[4]}"),
            ("失败", "failures", lambda t: f"{t[1]}: {t[2][:110]}"),
        ):
            items = stat[key]
            if not items:
                continue
            print(f"\n{label}（{len(items)} 条，最多列 {verbose}）：")
            for it in items[:verbose]:
                print(f"    {it[0]}  {fmt(it)}")


def summarize_ms(results: dict[str, dict]) -> dict:
    ms = [r["ms"] for r in results.values() if r.get("ms")]
    if not ms:
        return {}
    ms.sort()
    return {
        "p50": round(statistics.median(ms), 1),
        "p95": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1),
        "max": round(ms[-1], 1),
        "sum_s": round(sum(ms) / 1000.0, 1),
    }


def dump(stat: dict, results: dict, meta: dict, out: Path) -> None:
    out.write_text(json.dumps({
        "meta": meta,
        "metrics": {
            k: stat[k] for k in
            ("total", "decided_by_model", "decided_by_fastpath", "failed",
             "intent_ok", "qtype_ok", "slot_ok", "slot_total")
        },
        "fake_takeover": stat["fake_takeover"],
        "wrong_intent": stat["wrong_intent"],
        "wrong_slots": stat["wrong_slots"],
        "failures": stat["failures"],
        "errors": dict(stat["errors"]),
        "latency_ms": summarize_ms(results),
        "by_intent": {k: list(v) for k, v in stat["by_intent"].items()},
        "results": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果已写入 {out}")


def compare(paths: list[Path]) -> int:
    """两份（或多份）结果并排对比 —— 换模型的决策依据就是这张表。"""
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    print("=" * 74)
    print("模型对比")
    print("=" * 74)
    labels = [f"{d['meta'].get('model') or d['meta'].get('provider')}" for d in loaded]
    w = max(14, *(len(x) for x in labels))
    print(f"{'指标':<20}" + "".join(f"{x:>{w}}" for x in labels))
    m = [d["metrics"] for d in loaded]
    rows_out = [
        ("模型决定的用例", "decided_by_model", False),
        ("意图准确率", "intent_ok", True),
        ("问题性质准确率", "qtype_ok", True),
        ("槽位准确率", "slot_ok", True),
        ("彻底失败", "failed", False),
        ("假接管(须0)", None, False),
    ]
    for label, key, ratio in rows_out:
        cells = []
        for d, mm in zip(loaded, m):
            if key is None:
                cells.append(str(len(d["fake_takeover"])))
            elif ratio:
                den = mm["decided_by_model"] if key != "slot_ok" else mm["slot_total"]
                cells.append(_pct(mm[key], den))
            else:
                cells.append(str(mm[key]))
        print(f"{label:<20}" + "".join(f"{c:>{w}}" for c in cells))
    print(f"{'p50 延迟(ms)':<20}" + "".join(f"{d['latency_ms'].get('p50', '-'):>{w}}" for d in loaded))
    print(f"{'总耗时(s)':<20}" + "".join(f"{d['latency_ms'].get('sum_s', '-'):>{w}}" for d in loaded))
    print(f"{'输出不可用':<20}" + "".join(f"{d['errors'].get('invalid_output', 0):>{w}}" for d in loaded))
    print(f"{'上游故障':<20}" + "".join(f"{d['errors'].get('unavailable', 0):>{w}}" for d in loaded))
    print()
    # 逐条差异：哪些用例一个模型对一个模型错
    if len(loaded) == 2:
        a, b = loaded
        exp = {r["id"]: r["expect"] for r in load_corpus()}
        only_a, only_b = [], []
        for cid, ra in a["results"].items():
            rb = b["results"].get(cid)
            if not rb or not (ra.get("ok") and rb.get("ok")):
                continue
            ea = exp.get(cid, {}).get("intent")
            if not ea:
                continue
            oka = ra.get("intent") == ea and ra["planner"] != "deterministic"
            okb = rb.get("intent") == ea and rb["planner"] != "deterministic"
            if oka and not okb:
                only_a.append((cid, ra.get("intent"), rb.get("intent")))
            elif okb and not oka:
                only_b.append((cid, rb.get("intent"), ra.get("intent")))
        print(f"只有 {labels[0]} 判对的：{len(only_a)} 条；只有 {labels[1]} 判对的：{len(only_b)} 条")
        for cid, good, bad in (only_a + only_b)[:15]:
            print(f"    {cid}  对={good} 错={bad}")
    return 0


async def amain(args) -> int:
    from app.llm import client as llm_client
    from app.tools import _rt12306 as rt

    rows = load_corpus()
    if args.routes:
        wanted = {r.strip() for r in args.routes.split(",") if r.strip()}
        rows = [r for r in rows if r["expect"]["route"] in wanted]
    if args.limit:
        # 先按意图分层抽样，避免 --limit 全落在同一个意图上
        by_intent: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_intent[r["expect"]["intent"]].append(r)
        picked: list[dict] = []
        i = 0
        while len(picked) < args.limit:
            added = False
            for k in sorted(by_intent):
                if i < len(by_intent[k]) and len(picked) < args.limit:
                    picked.append(by_intent[k][i])
                    added = True
            if not added:
                break
            i += 1
        rows = picked

    spec: dict = {}
    if args.provider:
        spec["provider"] = args.provider
    if args.model:
        spec["model"] = args.model
    if args.base_url:
        spec["base_url"] = args.base_url
    if args.api_key:
        spec["api_key"] = args.api_key
    if args.api:
        spec["api"] = args.api
    llm_client.set_active_provider(spec or None)

    p = llm_client.current_provider()
    meta = {
        "provider": p.id, "model": p.model, "base_url": p.base_url,
        "key_required": p.needs_key, "compact": bool(args.compact),
        "constrained": bool(args.constrained),
        "no_think": os.environ.get("LLM_STRUCTURED_NO_THINK", "(默认 on)"),
        "routes": args.routes, "rows": len(rows),
    }
    print(f"供应商 {p.id} · 模型 {p.model or '(未填)'} · {p.base_url}")
    if not args.skip_probe:
        probe = await llm_client.probe_provider(p)
        if not probe.get("ok"):
            print(f"连通性探测失败：{probe.get('error') or '未知原因'}")
            if not args.force:
                print("（加 --force 可跳过探测强行跑）")
                return 2
        else:
            print(f"连通性 OK · {probe.get('latency_ms')} ms · 方言 {probe.get('dialect')}")

    await rt.ensure_loaded()          # 与 planner.decide 一致的预热，避免把首次加载算进延迟
    llm_client.reset_run_metrics()

    sem = asyncio.Semaphore(max(1, args.concurrency))
    t0 = time.perf_counter()
    results = await asyncio.gather(*(run_one(r, sem) for r in rows))
    wall = time.perf_counter() - t0
    by_id = {r["id"]: r for r in results}

    stat = score(rows, by_id)
    meta["wall_s"] = round(wall, 1)
    meta["run_metrics"] = llm_client.get_run_metrics()
    meta["latency"] = summarize_ms(by_id)
    report(stat, title=f"planner 基准结果 · {p.id}/{p.model}", meta=meta, verbose=args.verbose)
    print(f"\n总墙钟 {wall:.1f}s（并发 {args.concurrency}）· 调用统计 {meta['run_metrics']}")
    if args.out:
        dump(stat, by_id, meta, Path(args.out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="拿真实语料基准化 planner 所用的模型")
    ap.add_argument("--provider", help="供应商 id（deepseek / ollama / lmstudio / 自定义…）")
    ap.add_argument("--model", help="模型名")
    ap.add_argument("--base-url", help="直接指定 OpenAI 兼容地址（临时自定义供应商）")
    ap.add_argument("--api-key", help="Key（本地服务不需要）")
    ap.add_argument("--api", help="方言：auto / chat_completions / responses")
    ap.add_argument("--routes", default="llm,either",
                    help="只跑哪些 route（默认 llm,either —— 即「模型必须负责」的那些）")
    ap.add_argument("--limit", type=int, help="只跑 N 条（按意图分层抽样，先探路用）")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="并发数（默认 1：并发会让延迟数字失真，只有赶时间才调大）")
    ap.add_argument("--think", dest="think", action="store_true", default=None,
                    help="强制开启思考（默认沿用 LLM_STRUCTURED_NO_THINK）")
    ap.add_argument("--no-think", dest="think", action="store_false",
                    help="强制关闭思考")
    ap.add_argument("--compact", action="store_true",
                    help="开启 LLM_STRUCTURED_COMPACT_PROMPT（本地小模型用「模板+算例」而非 schema 原文）")
    ap.add_argument("--constrained", action="store_true",
                    help="开启 LLM_STRUCTURED_JSON_SCHEMA（把完整 schema 下发做约束解码）")
    ap.add_argument("--skip-probe", action="store_true", help="跳过连通性探测")
    ap.add_argument("--force", action="store_true", help="探测失败也继续跑")
    ap.add_argument("--verbose", type=int, default=12, help="逐条差异最多列几条（0 = 不列）")
    ap.add_argument("--out", help="结果 JSON 落盘路径")
    ap.add_argument("--compare", nargs="+", help="对比两份（或多份）结果 JSON")
    args = ap.parse_args()

    if args.compare:
        return compare([Path(x) for x in args.compare])

    # 基准必须打真模型：把 mock 关掉，避免"测了个桩还以为是模型"
    os.environ["LLM_MOCK"] = "false"
    if args.compact:
        os.environ["LLM_STRUCTURED_COMPACT_PROMPT"] = "true"
    if args.constrained:
        os.environ["LLM_STRUCTURED_JSON_SCHEMA"] = "true"
    if args.think is not None:
        os.environ["LLM_STRUCTURED_NO_THINK"] = "true" if args.think is False else "false"
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
