#!/usr/bin/env python3
"""语料候选校验器：给候选打 PASS / GAP / AMBIGUOUS 标，并出报告。

用法（在仓库根执行）：
    backend/.venv/bin/python scripts/validate_corpus.py
    backend/.venv/bin/python scripts/validate_corpus.py --batch intent_ticket
    backend/.venv/bin/python scripts/validate_corpus.py --write   # 冻结成语料

判定口径（见 backend/tests/corpus/SPEC.md §1）
------------------------------------------------
生成侧写的是**语义正确值**，不是"现在代码会输出什么"。因此：

- **PASS**     期望值与真实代码一致 → 可冻结
- **GAP**      语义正确但代码没做到 → 这是语料的产出价值（补规则 或 记「已知缺口」）
- **AMBIGUOUS** 期望值本身写不出唯一答案 → 丢弃

分路校验（SPEC §3.4）
- `route=fastpath` 硬校验：必须被 plan_with_reason 接管，且 intent/关键槽位相符
- `route=llm`      硬校验：**必须未被接管**（defer 非空）
- `route=either`   软校验：若接管则必须一致

无网络：只调本地纯函数（plan_with_reason / resolve_date / _parse_*）。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

CORPUS = ROOT / "backend" / "tests" / "corpus"
CANDIDATES = CORPUS / "candidates"

# 非对话/不可接管批次不参与路由统计
INTENT_BATCHES = [
    "intent_schedule_routing", "intent_ticket", "intent_station",
    "intent_railline", "intent_photo_news", "intent_general_defer",
    "intent_multiturn",
]
PHRASING_BATCHES = ["phrasings_time_window", "phrasings_seat_type"]

P = G = A = 0
GAPS: list[tuple[str, str, str, str]] = []
AMBIG: list[tuple[str, str, str, str]] = []


def record(tag: str, batch: str, cid: str, msg: str, detail: str) -> None:
    global P, G, A
    if tag == "PASS":
        P += 1
    elif tag == "GAP":
        G += 1
        GAPS.append((batch, cid, msg, detail))
    else:
        A += 1
        AMBIG.append((batch, cid, "", detail))


# ---------------------------------------------------------------- 期望值求值
def eval_date(exp: dict) -> tuple[str | None, str]:
    """把 phrasings 的 expect 求值成 resolve_date 应返回的 (iso, ok)。

    返回 (期望 iso 或 None, 人类可读说明)。None 表示"应当不识别"。
    """
    today = date.today()
    k = exp.get("kind")
    if k == "date_none":
        return None, "应当不识别"
    if k == "date_offset":
        return (today + timedelta(days=exp["days"])).isoformat(), f"今天{exp['days']:+d}天"
    if k == "date_md":
        d = date(today.year, exp["month"], exp["day"])
        if d < today:
            d = date(today.year + 1, exp["month"], exp["day"])
        return d.isoformat(), f"{exp['month']}月{exp['day']}日(已过顺延)"
    if k == "date_iso":
        return exp["value"], exp["value"]
    if k == "date_next_month":
        t = today.year * 12 + (today.month - 1) + 1
        y, m = divmod(t, 12)
        return date(y, m + 1, exp["day"]).isoformat(), f"下月{exp['day']}日"
    if k == "date_weekday":
        return (today + timedelta(days=(exp["weekday"] - today.weekday()) % 7)).isoformat(), \
               f"最近周{exp['weekday'] + 1}"
    return None, f"未知 kind={k}"


# 语料写**中文语义值**（SPEC §4.2.1），解析器返回**内部枚举码**。
# 这张表把中文映射成代码的返回值；映射不到的，才说明是真缺口。
SEAT_ZH = {
    "一等座": "first_class", "二等座": "second_class", "商务座": "business",
    "特等座": "business",          # 代码把"商务/特等"合并
    "硬卧": "hard_sleeper", "软卧": "soft_sleeper",
    "高级软卧": "advanced_soft_sleeper", "动卧": "dongwo",
    "卧铺": "sleeper",             # 泛称，代码刻意不硬猜软硬
    "硬座": "hard_seat", "软座": "soft_seat", "无座": "no_seat",
    "坐票": "hard_seat",           # 口语泛称 → 代码归到硬座
    "站票": "no_seat",
}
TYPE_ZH = {
    "高铁": "G", "动车": "D", "城际": "C", "直达": "Z",
    "特快": "T", "快速": "K",
    "普速": "K,T,Z", "普快": "K,T,Z", "绿皮车": "K,T,Z",
}


def eval_parser(table: str, exp: dict):
    """window/seat/type 的期望值 → 解析器实际返回的形态。"""
    k = exp.get("kind")
    if k == "tuple":
        return tuple(exp["value"]), f"tuple{exp['value']}"
    if k == "none":
        return (("", ""), "('','')") if table == "window" else ("", "''")
    if k == "value":
        v = exp["value"]
        if table == "seat":
            mapped = SEAT_ZH.get(v)
            if mapped is None:
                return None, f"中文语义值 {v!r} 无映射（未收录的席别？）"
            return mapped, f"{v}→{mapped}"
        if table == "type":
            mapped = TYPE_ZH.get(v)
            if mapped is None:
                return None, f"中文语义值 {v!r} 无映射（未收录的车种？）"
            return mapped, f"{v}→{mapped}"
        return v, repr(v)
    return None, f"未知 kind={k}"


# ---------------------------------------------------------------- 校验主体
async def check_intent(batch: str, path: Path) -> None:
    from app.pipeline.fastpath import plan_with_reason

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        cid, msg = d["id"], d["message"]
        exp = d["expect"]
        route = exp.get("route")
        hist = d.get("history")

        try:
            plan, defer = await plan_with_reason(msg, hist)
        except Exception as e:  # 代码炸了也是 GAP（且是真缺陷）
            record("GAP", batch, cid, msg, f"plan_with_reason 抛异常: {type(e).__name__}: {e}")
            continue

        took = plan is not None

        if route == "fastpath":
            if not took:
                record("GAP", batch, cid, msg,
                       f"期望快路径接管，实际交出(defer={defer.reason if defer else '?'})")
                continue
            if plan.intent != exp["intent"]:
                record("GAP", batch, cid, msg,
                       f"意图不符：期望 {exp['intent']}，实际 {plan.intent}")
                continue
            if hasattr(plan, "question_type") and getattr(plan, "question_type", None) \
                    and plan.question_type != exp["question_type"]:
                record("GAP", batch, cid, msg,
                       f"问题性质不符：期望 {exp['question_type']}，实际 {plan.question_type}")
                continue
            bad = _slot_mismatch(plan.slots, exp.get("slots") or {})
            if bad:
                record("GAP", batch, cid, msg, "关键槽位不符：" + "; ".join(bad))
                continue
            record("PASS", batch, cid, msg, "")

        elif route == "llm":
            if took:
                record("GAP", batch, cid, msg,
                       f"期望交回 LLM，实际被快路径接管为 {plan.intent} {plan.slots}")
                continue
            want = exp.get("defer_reason")
            got = defer.reason if defer else None
            if want and got != want:
                record("GAP", batch, cid, msg, f"交出原因不符：期望 {want}，实际 {got}")
                continue
            record("PASS", batch, cid, msg, "")

        else:  # either：软校验
            if not took:
                record("PASS", batch, cid, msg, "")
                continue
            if plan.intent != exp["intent"]:
                record("AMBIGUOUS", batch, cid, msg,
                       f"either 但被接管为 {plan.intent}（期望 {exp['intent']}）")
                continue
            bad = _slot_mismatch(plan.slots, exp.get("slots") or {})
            if bad:
                record("AMBIGUOUS", batch, cid, msg, "either 被接管但槽位有出入：" + "; ".join(bad))
                continue
            record("PASS", batch, cid, msg, "")


def _slot_mismatch(actual, want: dict) -> list[str]:
    """只比较语料明确写出的关键槽位。"""
    out = []
    for k, v in want.items():
        got = getattr(actual, k, None) if not isinstance(actual, dict) else actual.get(k)
        if got != v:
            out.append(f"{k}: 期望 {v!r} 实际 {got!r}")
    return out


def check_phrasings(batch: str, path: Path) -> None:
    from app.dates import resolve_date
    from app.pipeline.retrieve import _parse_seat, _parse_time_window, _parse_train_type

    fns = {"time": resolve_date, "window": _parse_time_window,
           "seat": _parse_seat, "type": _parse_train_type}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        cid, raw, table, exp = d["id"], d["raw"], d["table"], d["expect"]
        fn = fns.get(table)
        if fn is None:
            record("AMBIGUOUS", batch, cid, raw, f"未知 table={table}")
            continue

        if table == "time":
            want, human = eval_date(exp)
            got, ok = fn(raw, default_today=False)
            if want is None:
                if ok:
                    record("GAP", batch, cid, raw, f"期望不识别，实际识别为 {got}")
                else:
                    record("PASS", batch, cid, raw, "")
            else:
                if got == want:
                    record("PASS", batch, cid, raw, "")
                else:
                    record("GAP", batch, cid, raw, f"期望 {want}({human})，实际 {got!r}")
            continue

        want, human = eval_parser(table, exp)
        if want is None:
            record("AMBIGUOUS", batch, cid, raw, human)
            continue
        try:
            got = fn(raw)
        except Exception as e:
            record("GAP", batch, cid, raw, f"{fn.__name__} 抛异常: {type(e).__name__}: {e}")
            continue
        if got == want:
            record("PASS", batch, cid, raw, "")
        else:
            record("GAP", batch, cid, raw, f"期望 {human}，实际 {got!r}")


# ---------------------------------------------------------------- 报告
def _freeze(batches: list[str]) -> int:
    """把「校验为 PASS」的候选冻结成语料（JSONL 唯一真源）。

    做法：**重跑一遍校验**，只把 PASS 的原始行原样搬过去。
    为什么不复用上一遍结果：那样要把整行带在内存里，重跑更简单也更不容易错。
    """
    kept: dict[str, list[str]] = {"intent": [], "phrasings": []}
    stats: dict[str, collections.Counter] = {}

    # 用子进程式隔离不行（要共享 PASS/GAP 计数），所以直接复用同一套函数，
    # 靠"逐条判 PASS 则收行"的方式收集。这里改用更直接的策略：重跑并捕获。
    global P, G, A

    for b in batches:
        p = CANDIDATES / f"{b}.jsonl"
        if not p.exists():
            continue
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        c = collections.Counter()
        for ln in lines:
            # 单条校验：把这一条写进临时文件复用既有逻辑太重，改为直接求值
            ok = _check_one(b, json.loads(ln))
            c["total"] += 1
            if ok == "PASS":
                c["PASS"] += 1
                bucket = "phrasings" if b.startswith("phrasings_") else "intent"
                kept[bucket].append(ln)
            else:
                c[ok] += 1
        stats[b] = c

    for bucket, out in (("intent", CORPUS / "intent_corpus.jsonl"),
                        ("phrasings", CORPUS / "phrasings_corpus.jsonl")):
        rows = kept[bucket]
        # 按 id 排序，保证 diff 稳定
        rows.sort(key=lambda r: json.loads(r)["id"])
        out.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"冻结 → {out.relative_to(ROOT)}  {len(rows)} 条")

    print("\n各批次冻结情况：")
    for b, c in stats.items():
        print(f"  {b:28s} total {c['total']:3d}  PASS {c['PASS']:3d}  "
              f"GAP {c['GAP']:3d}  AMB {c['AMBIGUOUS']:3d}")
    return 0


def _check_one(batch: str, d: dict) -> str:
    """单条校验，返回 'PASS' / 'GAP' / 'AMBIGUOUS'。供 --write 冻结用。"""
    if batch.startswith("intent_"):
        return _check_one_intent(d)
    return _check_one_phrasing(d)


def _check_one_intent(d: dict) -> str:
    from app.pipeline.fastpath import plan_with_reason

    exp = d["expect"]
    route = exp.get("route")
    try:
        plan, defer = asyncio.run(plan_with_reason(d["message"], d.get("history")))
    except Exception:
        return "GAP"
    took = plan is not None
    if route == "fastpath":
        if not took or plan.intent != exp["intent"]:
            return "GAP"
        if getattr(plan, "question_type", None) and plan.question_type != exp["question_type"]:
            return "GAP"
        return "GAP" if _slot_mismatch(plan.slots, exp.get("slots") or {}) else "PASS"
    if route == "llm":
        if took:
            return "GAP"
        want = exp.get("defer_reason")
        got = defer.reason if defer else None
        return "GAP" if (want and got != want) else "PASS"
    # either
    if not took:
        return "PASS"
    if plan.intent != exp["intent"]:
        return "AMBIGUOUS"
    return "AMBIGUOUS" if _slot_mismatch(plan.slots, exp.get("slots") or {}) else "PASS"


def _check_one_phrasing(d: dict) -> str:
    from app.dates import resolve_date
    from app.pipeline.retrieve import _parse_seat, _parse_time_window, _parse_train_type

    fns = {"time": resolve_date, "window": _parse_time_window,
           "seat": _parse_seat, "type": _parse_train_type}
    table, raw, exp = d["table"], d["raw"], d["expect"]
    fn = fns.get(table)
    if fn is None:
        return "AMBIGUOUS"
    if table == "time":
        want, _ = eval_date(exp)
        try:
            got, ok = fn(raw, default_today=False)
        except Exception:
            return "GAP"
        if want is None:
            return "PASS" if not ok else "GAP"
        return "PASS" if got == want else "GAP"
    want, _ = eval_parser(table, exp)
    if want is None:
        return "AMBIGUOUS"
    try:
        got = fn(raw)
    except Exception:
        return "GAP"
    return "PASS" if got == want else "GAP"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="只校验某个批次")
    ap.add_argument("--write", action="store_true", help="把 PASS 的条目冻结进语料")
    ap.add_argument("--detail", type=int, default=12, help="每个批次打印多少条 GAP 明细")
    args = ap.parse_args()

    batches = INTENT_BATCHES + PHRASING_BATCHES
    if args.batch:
        batches = [b for b in batches if b == args.batch]
        if not batches:
            print(f"未知批次：{args.batch}", file=sys.stderr)
            return 2

    if args.write:
        return _freeze(batches)

    print("=" * 78)
    print("语料候选校验（无网络；只调本地纯函数）")
    print("=" * 78)

    per_batch: dict[str, collections.Counter] = {}

    for b in batches:
        p = CANDIDATES / f"{b}.jsonl"
        if not p.exists():
            print(f"⚠ 缺文件：{p}")
            continue
        before = (P, G, A)
        if b.startswith("intent_"):
            asyncio.run(check_intent(b, p))
        else:
            check_phrasings(b, p)
        c = collections.Counter()
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c["total"] += 1
        c["PASS"] = P - before[0]
        c["GAP"] = G - before[1]
        c["AMBIGUOUS"] = A - before[2]
        per_batch[b] = c
        print(f"\n── {b}  ({c['total']} 条)")
        print(f"   PASS {c['PASS']:3d} | GAP {c['GAP']:3d} | AMBIGUOUS {c['AMBIGUOUS']:3d}")

        gaps = [g for g in GAPS if g[0] == b]
        for _, cid, msg, detail in gaps[:args.detail]:
            print(f"   GAP {cid}: {msg[:44]}")
            print(f"        └ {detail[:100]}")
        if len(gaps) > args.detail:
            print(f"   … 另有 {len(gaps) - args.detail} 条 GAP")
        for _, cid, msg, detail in [x for x in AMBIG if x[0] == b][:3]:
            print(f"   AMB {cid}: {msg[:40]} └ {detail[:80]}")

    print("\n" + "=" * 78)
    tot = P + G + A
    print(f"总计 {tot} 条：PASS {P} ({P/max(tot,1)*100:.1f}%) | "
          f"GAP {G} ({G/max(tot,1)*100:.1f}%) | AMBIGUOUS {A} ({A/max(tot,1)*100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
