#!/usr/bin/env python3
"""从**地面真值**批量生成意图语料候选（模板法，不经任何模型）。

为什么要它：现有 192 条是人/模型逐条写的，规模上不去，而基准的分辨率取决于规模 ——
113 条"模型决定"的用例上，1 条错值 0.9 个点，噪声和方法差异分不开。

**关键区别：这里的标签不是"蒸馏某个模型"，而是从数据算出来的。**
    线路名与里程   ← `dict.db: line_master`（758 条）
    车次号         ← `dict.db: g_trip.short_name`（14674 趟）
    车站与所在城市 ← 本地 3384 站站点表
所以这些行的期望值是**构造性正确**的，不继承任何模型的失败模式。
（这一点直接回答了"扩充语料是不是在蒸馏"：槽位与意图这块不是。）

★ **实测结论：这批数据 100% 被确定性快路径接管，因此它不测模型。**

    9 个模板 × 20 条 = 180/180 被 `plan_with_reason` 接管

原因是模板用的正是快路径正则覆盖的标准问法。把它口语化也救不回来 ——
实测 8 组口语改写里只有 2 组逃出快路径（快路径比预期鲁棒得多）。

所以本脚本产出的是**快路径回归集**，不是模型评测集：

| 用途 | 该用什么 |
|---|---|
| 快路径回归（覆盖 41–68% 流量，此前没有规模化回归集） | ✅ 本脚本 |
| 模型评测集（要进模型准确率分母） | ❌ 模板做不到 —— 必须口语化/歧义化/多诉求，只能由模型或人来写 |
| 模型**训练**集 | ✅ 可以用：标签由地面真值构造，且真实线路名/车次号/站名能教给它领域词表 |

换句话说：**模板能给训练集，给不了评测集。** 评测集得靠人写或靠模型生成 ——
后者就是"蒸馏"，要接受它的上限。

用法（在仓库根执行）：
    backend/.venv/bin/python scripts/gen_corpus_templates.py            # 只统计，不落盘
    backend/.venv/bin/python scripts/gen_corpus_templates.py --write    # 写入 candidates/
    backend/.venv/bin/python scripts/gen_corpus_templates.py --write --per-template 120
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

DICT_DB = ROOT / "backend" / "data" / "dict.db"
OUT_DIR = ROOT / "backend" / "tests" / "corpus" / "candidates"

# 抽多少条由 --per-template 控制；默认每模板 100 条 → 总量 ~2000
DEFAULT_PER_TEMPLATE = 100
SEED = 20260920          # 固定种子：语料要可复现，不能每次生成都不一样


def _clean_line_names() -> set[str]:
    """从 `SPEC.md §6` 读出「可用于问法的干净线名」白名单。

    **为什么从 SPEC 读而不是自己过滤**：SPEC §6 明确警告「大量线名带「段」后缀
    （如 `京哈高速线京沈段`），不要用作问法」。第一版生成器没读这条，直接抽到了
    `包神线南段` —— 契约里写着的坑照样会踩，除非让代码去读契约。
    把白名单的唯一来源留在 SPEC，两边就不会漂移。
    """
    spec = (ROOT / "backend" / "tests" / "corpus" / "SPEC.md").read_text(encoding="utf-8")
    marker = "可用于问法的干净线名"
    if marker not in spec:
        return set()
    block = spec.split(marker, 1)[1].split("```")[1]        # 第一个代码块
    return {w for w in block.split() if w.endswith("线")}


def _lines(limit: int) -> list[dict]:
    """有里程的线路（路线名 + 起讫 + 里程），里程已知 → 槽位与答案都构造性正确。

    只取 SPEC §6 白名单里的干净线名；`段` 后缀与`联络线`一律排除。
    """
    allowed = _clean_line_names()
    con = sqlite3.connect(DICT_DB)
    try:
        rows = con.execute(
            "select line, from_station, to_station, mileage_km from line_master "
            "where mileage_km is not null and mileage_km > 0 "
            "order by mileage_km desc"
        ).fetchall()
    finally:
        con.close()
    out = []
    for ln, a, b, km in rows:
        if allowed and ln not in allowed:
            continue
        if ln.endswith("段") or "联络" in ln:
            continue
        out.append({"line": ln, "from": a, "to": b, "km": float(km)})
    if not out:      # 白名单没读到就退化为纯规则过滤，别整体失败
        out = [{"line": ln, "from": a, "to": b, "km": float(km)}
               for ln, a, b, km in rows if not ln.endswith("段") and "联络" not in ln]
    return out[:limit]


def _trains(limit: int) -> list[str]:
    """车次号。`g_trip.short_name` 形如 `09/S8512`，取斜杠后的车次部分。"""
    con = sqlite3.connect(DICT_DB)
    try:
        rows = con.execute("select distinct short_name from g_trip where short_name is not null").fetchall()
    finally:
        con.close()
    out: set[str] = set()
    for (sn,) in rows:
        code = str(sn).split("/")[-1].strip().upper()
        # 只留规范的「字母+数字」车次号；G/D/C/Z/T/K 是客运车次
        if re.fullmatch(r"[GDCZTK]\d{1,4}", code):
            out.add(code)
    return sorted(out)[:limit]


def _stations(limit: int) -> list[dict]:
    """本地站点表：站名（**规范名，不带「站」**）+ 所在城市。"""
    from app.tools import _rt12306 as rt

    asyncio.run(rt.ensure_loaded())
    items = [{"station": k, "city": v.get("city") or ""} for k, v in rt.all_stations().items()]
    items = [x for x in items if x["city"]]
    return items[:limit]


# ---- 模板 ----
# 每个模板产出 (message, intent, question_type, slots)。**slot 值全部来自数据，不是编的。**
def t_line_mileage(rec: dict) -> tuple:
    return (f"{rec['line']}全长多少公里？", "rail_line", "realtime",
            {"target": rec["line"]})


def t_line_stations(rec: dict) -> tuple:
    return (f"{rec['line']}沿线经过哪些车站？", "rail_line", "realtime",
            {"target": rec["line"]})


def t_line_od(rec: dict) -> tuple:
    # **只标 direction，不标 target**：问题文本里没有线路名，指望模型"凭 OD 猜出线路"
    # 是在考它的记忆而不是抽取能力。现有语料 intent_railline-0006 就是这么标的
    # （期望 target=京沪高速线 于「从北京坐到上海走的是哪条线？」），4B 答成「京沪线」被判错 ——
    # 那不是模型错，是标注在要求一个文本里不存在、且依赖知识才可能对的值。
    # SPEC §3.3 写的是"只写关键槽位，宁缺毋滥"，这条按 SPEC 修。
    return (f"从{rec['from']}到{rec['to']}走哪条线？", "rail_line", "realtime",
            {"direction": f"{rec['from']}→{rec['to']}"})


def t_train_stops(rec: str) -> tuple:
    return (f"{rec}次列车经停哪些站？", "schedule", "realtime", {"target": rec})


def t_train_depart(rec: str) -> tuple:
    return (f"{rec}今天几点发车", "schedule", "realtime", {"target": rec, "time": "今天"})


def t_train_emu(rec: str) -> tuple:
    return (f"{rec}次是由哪组动车组担当的？", "emu_routing", "realtime", {"target": rec})


def t_station_city(rec: dict) -> tuple:
    return (f"{rec['station']}站在哪个城市？", "station", "realtime", {"location": rec["station"]})


def t_station_list(rec: dict) -> tuple:
    return (f"{rec['city']}有哪些火车站？", "station", "realtime", {"location": rec["city"]})


# 多轮：单轮的跟进问法 —— 实测这是模型最弱的一环（22 条错里 5 条），必须专门压
def t_multiturn_stops(rec: str) -> tuple:
    """多轮跟进：`rec` 传**裸车次号**，target 也必须是裸车次号（SPEC §3.3：车次大写、不带「次」）。"""
    return (f"那{rec}次呢", "schedule", "realtime", {"target": rec})


TEMPLATES = [
    ("line_mileage", t_line_mileage, "lines"),
    ("line_stations", t_line_stations, "lines"),
    ("line_od", t_line_od, "lines"),
    ("train_stops", t_train_stops, "trains"),
    ("train_depart", t_train_depart, "trains"),
    ("train_emu", t_train_emu, "trains"),
    ("station_city", t_station_city, "stations"),
    ("station_list", t_station_list, "stations"),
]

MULTITURN_TEMPLATES = [("train_stops_followup", t_multiturn_stops, "trains")]


def build(per_template: int) -> list[dict]:
    rng = random.Random(SEED)
    pool = {
        "lines": _lines(400),
        "trains": _trains(400),
        "stations": _stations(400),
    }
    rows: list[dict] = []
    for name, fn, pool_key in TEMPLATES:
        items = list(pool[pool_key])
        rng.shuffle(items)
        for rec in items[:per_template]:
            msg, intent, qtype, slots = fn(rec)
            rows.append({
                "id": f"gen_{name}-{len(rows):04d}",
                "message": msg,
                "history": None,
                "expect": {"intent": intent, "question_type": qtype, "slots": slots,
                           "route": "either", "defer_reason": None},
                "note": f"模板生成（{name}）｜标签由 dict.db 地面真值构造，非模型标注",
                "source": f"gen:{name}",
            })
    # 多轮：带上一轮上下文，考察 target 继承
    items = list(pool["trains"])
    rng.shuffle(items)
    for rec in items[:per_template]:
        msg, intent, qtype, slots = t_multiturn_stops(rec)
        rows.append({
            "id": f"gen_multiturn-{len(rows):04d}",
            "message": msg,
            "history": [
                {"role": "user", "content": f"{rec}次列车经停哪些站？"},
                {"role": "assistant", "content": "（见上一轮回答）"},
            ],
            "expect": {"intent": intent, "question_type": qtype, "slots": slots,
                       "route": "either", "defer_reason": None},
            "note": "模板生成（多轮跟进）｜上一轮问的是车次，本轮「那XX呢」应继承该 target",
            "source": "gen:multiturn",
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-template", type=int, default=DEFAULT_PER_TEMPLATE)
    ap.add_argument("--write", action="store_true", help="写入 candidates/（默认只统计）")
    args = ap.parse_args()

    rows = build(args.per_template)
    by_src = collections.Counter(r["source"] for r in rows)
    by_intent = collections.Counter(r["expect"]["intent"] for r in rows)

    print(f"生成候选 {len(rows)} 条（每模板 {args.per_template}）")
    print("\n按模板：")
    for k, v in sorted(by_src.items()):
        print(f"  {k:32s} {v}")
    print("\n按意图：")
    for k, v in sorted(by_intent.items()):
        print(f"  {k:14s} {v}")
    print("\n样例：")
    for r in rows[:3] + rows[-2:]:
        print(f"  「{r['message']}」 → {r['expect']['intent']} / {r['expect']['slots']}")

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        grouped: dict[str, list[dict]] = collections.defaultdict(list)
        for r in rows:
            grouped[r["source"].split(":")[1]].append(r)
        for name, items in grouped.items():
            p = OUT_DIR / f"gen_{name}.jsonl"
            p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n",
                         encoding="utf-8")
            print(f"已写入 {p.relative_to(ROOT)}（{len(items)} 条）")
        print("\n下一步：backend/.venv/bin/python scripts/validate_corpus.py")
    else:
        print("\n（未落盘；加 --write 写入 candidates/）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
