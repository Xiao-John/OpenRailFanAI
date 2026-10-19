#!/usr/bin/env python3
"""FUCK_CR 一致性校验（仅标准库）。

用法：
    cd FUCK_CR && python3 check.py

检查：
1. JSON 可解析、schema_version 存在、字头唯一、区间格式合法
2. CSV 表头正确、行数与 JSON mapping 一致、confidence 合法
3. 所有 Markdown 的 [S#] 引用都能在 05-sources.md 找到
4. 不出现已废弃的旧目录名拼写
5. 01 正文覆盖 JSON 里的全部客运字头
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERRS: list[str] = []


def fail(msg: str) -> None:
    ERRS.append(msg)


# ---------- 1. JSON ----------

try:
    data = json.loads((ROOT / "data/train-code-2026.json").read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print(f"FATAL: JSON 无法解析: {exc}")
    sys.exit(1)

if not data.get("schema_version"):
    fail("JSON 缺少 schema_version")

RANGE_RE = re.compile(r"^[A-Z]{0,2}\d{1,5}-[A-Z]{0,2}\d{1,5}$")

for group in ("passenger", "freight", "work"):
    if group not in data:
        fail(f"JSON 缺少分组 {group}")
        continue
    for item in data[group]:
        for rng in item.get("ranges", []):
            value = rng.get("range") if isinstance(rng, dict) else rng
            if value == "5 位纯数字":
                continue
            if not isinstance(value, str) or not RANGE_RE.match(value):
                fail(f"{group} 区间格式异常: {value!r}")

prefixes = [item.get("prefix", "") for item in data.get("passenger", [])]
if len(prefixes) != len(set(prefixes)):
    fail(f"客运字头重复: {prefixes}")
if len(prefixes) != 11:
    fail(f"客运字头应为 11 个，实际 {len(prefixes)}: {prefixes}")

# ---------- 2. CSV ----------

csv_path = ROOT / "data/prefix-map.csv"
with csv_path.open(encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    rows = list(reader)
    header = reader.fieldnames or []

EXPECTED_HEADER = [
    "old_prefix", "old_read", "old_meaning", "old_speed",
    "new_prefix", "new_read", "new_meaning", "new_speed",
    "confidence", "note",
]
if header != EXPECTED_HEADER:
    fail(f"CSV 表头不符: {header}")

if len(rows) != len(data.get("mapping", [])):
    fail(f"CSV 行数 {len(rows)} != JSON mapping {len(data.get('mapping', []))}")

ALLOWED_CONF = {"A", "A-inferred", "C"}
for i, row in enumerate(rows, start=2):
    if row.get("confidence") not in ALLOWED_CONF:
        fail(f"CSV 第 {i} 行 confidence 非法: {row.get('confidence')!r}")
    if not row.get("new_prefix"):
        fail(f"CSV 第 {i} 行 new_prefix 为空")

for item in data.get("mapping", []):
    if item.get("confidence") not in ALLOWED_CONF:
        fail(f"JSON mapping confidence 非法: {item}")

# ---------- 3. [S#] 引用 ----------

sources_text = (ROOT / "05-sources.md").read_text(encoding="utf-8")
known_sources = set(re.findall(r"^\|\s*(S\d+)\s*\|", sources_text, re.M))
if not known_sources:
    fail("05-sources.md 未解析到任何 S# 登记")

md_files = sorted(ROOT.glob("*.md")) + sorted((ROOT / "data").glob("*.md"))
for md in md_files:
    text = md.read_text(encoding="utf-8")
    for ref in set(re.findall(r"\[(S\d+)\]", text)):
        if ref not in known_sources:
            fail(f"{md.name} 引用了未登记的 {ref}")
    fm = re.search(r"sources:\s*\[(.*?)\]", text, re.S)
    if fm:
        fm_refs = set(re.findall(r"S\d+", fm.group(1)))
        for ref in fm_refs:
            if ref not in known_sources:
                fail(f"{md.name} front matter 引用了未登记的 {ref}")
        prose = re.sub(r"```.*?```", "", text, flags=re.S)
        inline = set(re.findall(r"\[(S\d+)\]", prose))
        for ref in sorted(inline - fm_refs):
            fail(f"{md.name} 正文引用了 {ref}，但 front matter sources 未列")

# ---------- 4. 旧目录名 ----------

STALE = re.compile(r"FX{2}K_CR")
for md in md_files:
    if STALE.search(md.read_text(encoding="utf-8")):
        fail(f"{md.name} 仍含已废弃的旧目录名")

# ---------- 5. 01 覆盖全部客运字头 ----------

body = (ROOT / "01-code-system-2026.md").read_text(encoding="utf-8")
for prefix in prefixes:
    if f"| {prefix} |" not in body:
        fail(f"01 正文缺少客运字头 {prefix}")

# ---------- 结果 ----------

if ERRS:
    print(f"FAILED: {len(ERRS)} 项")
    for err in ERRS:
        print(f"  - {err}")
    sys.exit(1)

print(
    "OK: JSON/CSV/引用/目录名/字头覆盖 全部一致 "
    f"（客运 {len(prefixes)} 个，映射 {len(rows)} 条，来源 {len(known_sources)} 条）"
)
