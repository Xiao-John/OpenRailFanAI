#!/usr/bin/env python3
"""RAG-EVAL 汇总 Agent：合并两个判官得分 → 加权分 / 判定档 / 一致性 / 分类统计。

用法：
    backend/.venv/bin/python docs/ragval/aggregate.py

输入：docs/ragval/run/scores_A.jsonl、scores_B.jsonl（两判官独立评分）
      docs/ragval/testset.txt（取分类与 ★）
输出：docs/ragval/run/summary.json（汇总数据）与打印出的报告骨架
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE / "run"
W = {"cor": 0.30, "grd": 0.25, "ret": 0.20, "rel": 0.10, "hon": 0.15}
DIMS = list(W)


def load(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[row["id"]] = row
    return out


def testset_meta() -> dict[str, dict]:
    meta = {}
    for line in (HERE / "testset.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = [x.strip() for x in line.split("|")]
        if len(p) != 9:
            continue
        cid = p[0].replace("★", "")
        meta[cid] = {"category": p[1], "mode": p[2], "star": "★" in p[0],
                     "checks": p[5], "veto": p[6]}
    return meta


def score_of(r: dict) -> float:
    if r.get("veto"):
        return 0.0
    return round(100 * sum(W[d] * float(r.get(d, 0)) for d in DIMS), 1)


def verdict_of(s: float, veto: bool) -> str:
    if veto:
        return "FAIL"
    if s >= 80:
        return "PASS"
    if s >= 60:
        return "PARTIAL"
    return "FAIL"


def kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's κ（三分类）。"""
    labels = ["PASS", "PARTIAL", "FAIL"]
    n = len(pairs)
    if n == 0:
        return 0.0
    po = sum(1 for a, b in pairs if a == b) / n
    pe = sum((sum(1 for a, _ in pairs if a == l) / n) * (sum(1 for _, b in pairs if b == l) / n)
             for l in labels)
    return round((po - pe) / (1 - pe), 3) if pe != 1 else 1.0


def main() -> None:
    meta = testset_meta()
    A = load(RUN / "scores_A.jsonl")
    B = load(RUN / "scores_B.jsonl")

    ids = [c for c in meta if c in A or c in B]
    rows = []
    for cid in ids:
        a, b = A.get(cid), B.get(cid)
        base = a or b
        # 维度取两判官均值（一票否决任一判官判出即生效，但需一致才计红线）
        veto_any = bool((a or {}).get("veto")) or bool((b or {}).get("veto"))
        veto_both = bool((a or {}).get("veto")) and bool((b or {}).get("veto"))
        dims = {}
        for d in DIMS:
            vals = [float(x[d]) for x in (a, b) if x and d in x]
            dims[d] = round(sum(vals) / len(vals), 3) if vals else 0.0
        s = round(100 * sum(W[d] * dims[d] for d in DIMS), 1)
        if veto_both:
            s = 0.0
        v = verdict_of(s, veto_both)
        va = verdict_of(score_of(a), bool(a.get("veto"))) if a else None
        vb = verdict_of(score_of(b), bool(b.get("veto"))) if b else None
        rows.append({
            "id": cid, **meta.get(cid, {}), **dims, "score": s, "verdict": v,
            "veto_any": veto_any, "veto_confirmed": veto_both,
            "judgeA": va, "judgeB": vb,
            "judgeA_score": score_of(a) if a else None, "judgeB_score": score_of(b) if b else None,
            "noteA": (a or {}).get("note", ""), "noteB": (b or {}).get("note", ""),
        })

    # 裁决环节：汇总判官对分歧/边界条目的最终裁定（final_verdict 覆盖机械判定）
    adj_path = RUN / "adjudication.json"
    adj = json.loads(adj_path.read_text(encoding="utf-8"))["overrides"] if adj_path.exists() else {}
    for r in rows:
        o = adj.get(r["id"])
        if o:
            r["verdict_before_adjudication"] = r["verdict"]
            r["verdict"] = o["final_verdict"]
            r["adjudication"] = o["reason"]

    pairs = [(r["judgeA"], r["judgeB"]) for r in rows if r["judgeA"] and r["judgeB"]]
    agree = sum(1 for a, b in pairs if a == b) / len(pairs) if pairs else 0
    cats = defaultdict(lambda: {"n": 0, "PASS": 0, "PARTIAL": 0, "FAIL": 0})
    for r in rows:
        c = cats[r["category"]]
        c["n"] += 1
        c[r["verdict"]] += 1
    dim_mean = {d: round(sum(r[d] for r in rows) / len(rows), 3) for d in DIMS}

    n = len(rows)
    summary = {
        "cases": n,
        "verdict_counts": {k: sum(1 for r in rows if r["verdict"] == k) for k in ("PASS", "PARTIAL", "FAIL")},
        "pass_rate": round(sum(1 for r in rows if r["verdict"] == "PASS") / n, 3),
        "veto_confirmed": [r["id"] for r in rows if r["veto_confirmed"]],
        "veto_single_judge": [r["id"] for r in rows if r["veto_any"] and not r["veto_confirmed"]],
        "star_all_pass": all(r["verdict"] == "PASS" for r in rows if r.get("star")),
        "star_failed": [r["id"] for r in rows if r.get("star") and r["verdict"] != "PASS"],
        "dim_mean": dim_mean,
        "agreement": round(agree, 3), "kappa": kappa(pairs),
        "categories": {k: dict(v) for k, v in sorted(cats.items())},
        "rows": sorted(rows, key=lambda r: (r["score"], r["id"])),
    }
    (RUN / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"用例 {n} | 通过率 {summary['pass_rate']} | 判定 {summary['verdict_counts']}")
    print(f"判官一致性 {summary['agreement']} | κ={summary['kappa']}")
    print(f"维度均值 {dim_mean}")
    print(f"★ 全过={summary['star_all_pass']} 未过={summary['star_failed']}")
    print(f"红线(双判官确认)={summary['veto_confirmed']} 单判官={summary['veto_single_judge']}")
    print("\n分类明细：")
    for k, v in summary["categories"].items():
        print(f"  {k}: n={v['n']} PASS={v['PASS']} PARTIAL={v['PARTIAL']} FAIL={v['FAIL']}")
    print("\n得分最低 15 条：")
    for r in summary["rows"][:15]:
        print(f"  {r['id']:>5} {r['score']:>5} {r['verdict']:<7} "
              f"cor={r['cor']} grd={r['grd']} ret={r['ret']} rel={r['rel']} hon={r['hon']}")


if __name__ == "__main__":
    main()
