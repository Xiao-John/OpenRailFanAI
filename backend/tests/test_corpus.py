"""A 类语料校验套件 —— 问法/说法 → 内部值的**回归**语料（**全部无网络**）。

本套件的定位（与既有测试的分工）
--------------------------------
- `test_perf_fastpath.py` 的 `_GOLDEN` / `_MUST_DEFER` 是**手写的少量**核心用例；
- `test_phrasings.py` 的四张表是**手写的**解析探针；
- **本套件**读的是 `tests/corpus/*.jsonl` —— **机器生成的规模化语料**，
  用 JSONL 做唯一真源（便于生成、diff 干净、可标注来源）。

语料怎么来的
------------
`tests/corpus/SPEC.md` 是生成契约；subagent 按契约产出 `candidates/*.jsonl`；
`scripts/validate_corpus.py` 逐条校验并打 PASS/GAP/AMBIGUOUS；
**只有 PASS 的条目被冻结**进 `intent_corpus.jsonl` / `phrasings_corpus.jsonl`。

所以本套件跑的是**已确认与代码一致的期望值**：它一旦红了，就说明
"某条原本能过的问法现在过不去了" —— 这正是回归该有的语义。

约定（沿用 `test_phrasings.py`）
1. 每条都有明确期望值；
2. **"不认识"也是正确答案**（`date_none` / `none` 钉住"宁可说不认识"）；
3. 每张表有**条数下限**，防止删用例变绿。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_corpus.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dates import resolve_date
from app.pipeline.fastpath import plan_with_reason
from app.pipeline.retrieve import _parse_seat, _parse_time_window, _parse_train_type

CORPUS = Path(__file__).resolve().parent / "corpus"
INTENT_FILE = CORPUS / "intent_corpus.jsonl"
PHRASINGS_FILE = CORPUS / "phrasings_corpus.jsonl"

TODAY = date.today()

# 条数下限：防止"删用例变绿"（语料只会增，不会减）
# 取冻结时的实际条数，留一点余量以免正常的口径微调就红
MIN_INTENT = 180
MIN_PHRASINGS = 80

_failures: list[str] = []
_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(msg)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------- 期望值求值
def eval_date(exp: dict) -> str | None:
    """expect → resolve_date 应返回的 ISO 串；None 表示"应当不识别"。"""
    k = exp.get("kind")
    if k == "date_none":
        return None
    if k == "date_offset":
        return (TODAY + timedelta(days=exp["days"])).isoformat()
    if k == "date_md":
        d = date(TODAY.year, exp["month"], exp["day"])
        if d < TODAY:                       # 已过顺延到明年（与 resolve_date 规则一致）
            d = date(TODAY.year + 1, exp["month"], exp["day"])
        return d.isoformat()
    if k == "date_iso":
        return exp["value"]
    if k == "date_next_month":
        t = TODAY.year * 12 + (TODAY.month - 1) + 1
        y, m = divmod(t, 12)
        return date(y, m + 1, exp["day"]).isoformat()
    if k == "date_weekday":
        return (TODAY + timedelta(days=(exp["weekday"] - TODAY.weekday()) % 7)).isoformat()
    raise ValueError(f"未知 kind={k}")


SEAT_ZH = {
    "一等座": "first_class", "二等座": "second_class", "商务座": "business",
    "特等座": "business", "硬卧": "hard_sleeper", "软卧": "soft_sleeper",
    "高级软卧": "advanced_soft_sleeper", "动卧": "dongwo", "卧铺": "sleeper",
    "硬座": "hard_seat", "软座": "soft_seat", "无座": "no_seat",
    "坐票": "hard_seat", "站票": "no_seat",
}
TYPE_ZH = {
    "高铁": "G", "动车": "D", "城际": "C", "直达": "Z", "特快": "T", "快速": "K",
    "普速": "K,T,Z", "普快": "K,T,Z", "绿皮车": "K,T,Z",
}


# ---------------------------------------------------------------- 语料 ① 意图
def test_intent() -> None:
    rows = load(INTENT_FILE)
    check(len(rows) >= MIN_INTENT,
          f"intent 语料条数 {len(rows)} < 下限 {MIN_INTENT}（不许删用例变绿）")

    ids = [r["id"] for r in rows]
    check(len(ids) == len(set(ids)), "intent 语料存在重复 id")

    for r in rows:
        cid, msg, exp = r["id"], r["message"], r["expect"]
        route = exp["route"]
        plan, defer = asyncio.run(plan_with_reason(msg, r.get("history")))
        took = plan is not None

        if route == "fastpath":
            check(took, f"{cid} 期望快路径接管，实际交出：{msg!r}")
            if not took:
                continue
            check(plan.intent == exp["intent"],
                  f"{cid} 意图不符：期望 {exp['intent']}，实际 {plan.intent}（{msg!r}）")
            if getattr(plan, "question_type", None):
                check(plan.question_type == exp["question_type"],
                      f"{cid} 问题性质不符：期望 {exp['question_type']}，"
                      f"实际 {plan.question_type}（{msg!r}）")
            for k, v in (exp.get("slots") or {}).items():
                got = getattr(plan.slots, k, None)
                check(got == v, f"{cid} 槽位 {k} 不符：期望 {v!r}，实际 {got!r}（{msg!r}）")

        elif route == "llm":
            check(not took,
                  f"{cid} 期望交回 LLM，实际被接管为 {plan.intent if plan else '?'}（{msg!r}）")
            want = exp.get("defer_reason")
            if want and not took:
                got = defer.reason if defer else None
                check(got == want,
                      f"{cid} 交出原因不符：期望 {want}，实际 {got}（{msg!r}）")

        else:  # either —— 软校验：接管了就必须一致
            if not took:
                continue
            check(plan.intent == exp["intent"],
                  f"{cid} either 被接管为 {plan.intent}，期望 {exp['intent']}（{msg!r}）")


# ---------------------------------------------------------------- 语料 ② 说法
def test_phrasings() -> None:
    rows = load(PHRASINGS_FILE)
    check(len(rows) >= MIN_PHRASINGS,
          f"phrasings 语料条数 {len(rows)} < 下限 {MIN_PHRASINGS}（不许删用例变绿）")

    ids = [r["id"] for r in rows]
    check(len(ids) == len(set(ids)), "phrasings 语料存在重复 id")

    by_table: dict[str, int] = {}
    for r in rows:
        by_table[r["table"]] = by_table.get(r["table"], 0) + 1

    # 每张表都要有，且都必须有"不认识"红线用例
    for t in ("time", "window", "seat", "type"):
        check(by_table.get(t, 0) > 0, f"phrasings 缺 {t} 表的用例")

    for r in rows:
        cid, raw, table, exp = r["id"], r["raw"], r["table"], r["expect"]

        if table == "time":
            want = eval_date(exp)
            got, ok = resolve_date(raw, default_today=False)
            if want is None:
                check(not ok, f"{cid} 期望不识别 {raw!r}，实际识别为 {got!r}")
            else:
                check(got == want, f"{cid} 日期不符：期望 {want}，实际 {got!r}（{raw!r}）")
            continue

        fn = {"window": _parse_time_window, "seat": _parse_seat,
              "type": _parse_train_type}[table]
        k = exp.get("kind")
        if k == "tuple":
            want = tuple(exp["value"])
        elif k == "none":
            want = ("", "") if table == "window" else ""
        elif k == "value":
            v = exp["value"]
            want = SEAT_ZH.get(v) if table == "seat" else (TYPE_ZH.get(v) if table == "type" else v)
            check(want is not None, f"{cid} 未收录的语义值 {v!r}（表 {table}）")
            if want is None:
                continue
        else:
            check(False, f"{cid} 未知 expect.kind={k}")
            continue

        got = fn(raw)
        check(got == want, f"{cid} [{table}] 不符：期望 {want!r}，实际 {got!r}（{raw!r}）")


def main() -> int:
    print("=" * 72)
    print("A 类语料回归（无网络）")
    print("=" * 72)

    test_intent()
    test_phrasings()

    if _failures:
        print(f"\n失败 {len(_failures)} 条（共校验 {_checks} 项）：")
        for f in _failures[:40]:
            print(f"  ✗ {f}")
        if len(_failures) > 40:
            print(f"  … 另有 {len(_failures) - 40} 条")
        return 1

    print(f"\n全部通过 ✔（{_checks} 项断言）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
