#!/usr/bin/env python3
"""12306「车站大屏」接口探针 / 小屏渲染。

对应文档：docs/datasources.md

用法：
    python3 scripts/probe_station_screen.py VNP                    # 今天，只看统计
    python3 scripts/probe_station_screen.py VNP 20260915           # 指定日期
    python3 scripts/probe_station_screen.py VNP 20260915 dep 20    # 出发屏前 20 条
    python3 scripts/probe_station_screen.py VNP 20260915 arr 20    # 到达屏前 20 条
    python3 scripts/probe_station_screen.py VNP --raw | head -c 500   # 原始 JSON

站名也可以用中文（自动经 station_name.js 转电报码）：
    python3 scripts/probe_station_screen.py 北京南

仅用标准库，可在项目 venv 之外运行。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

API = "https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation"
STATION_JS = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def post_form(url: str, data: dict[str, str], timeout: int = 25) -> dict:
    """POST form-urlencoded。

    ⚠️ 必须 POST：同一 URL 用 GET 会返回 {"status": false, "errorMsg": "系统忙…(M0003)"}。
    不需要 Cookie / Referer，UA 可有可无。
    """
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_station_code(name: str) -> str:
    """中文站名 / 三字码 → 电报码（复用 12306 官方站点库）。"""
    s = name.strip()
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()
    with urllib.request.urlopen(
        urllib.request.Request(STATION_JS, headers={"User-Agent": UA}), timeout=25
    ) as resp:
        js = resp.read().decode("utf-8")
    for item in re.findall(r"@[a-z]+\|([^|]+)\|([A-Z]{3})\|", js):
        if item[0] == s:
            return item[1]
    raise SystemExit(f"未在 12306 站点库里找到站名：{name}")


def fetch_screen(station_code: str, train_date: str) -> dict:
    return post_form(API, {"train_start_date": train_date, "train_station_code": station_code})


def split_boards(rows: list[dict], code: str) -> tuple[list[dict], list[dict], list[dict]]:
    """按方向切分成 出发屏 / 到达屏 / 过路。

    判据（实测，见文档 §3）：
      本站始发   start_station_telecode == 本站  → 发车用 start_time（arrive_time 为 '----'）
      本站终到   end_station_telecode == 本站    → 到达用 arrive_time
      过路       两者都不等于本站                → 到发时刻都有
    """
    dep, arr, thru = [], [], []
    for r in rows:
        is_start = r.get("start_station_telecode") == code
        is_end = r.get("end_station_telecode") == code
        if is_start:
            dep.append(r)
        elif is_end:
            arr.append(r)
        else:
            thru.append(r)
    return dep + thru, arr + thru, thru


def fmt_time(v: str) -> str:
    return "  --  " if v in ("", "----") else v


def render(rows: list[dict], kind: str, limit: int) -> None:
    tcol = "start_time" if kind == "dep" else "arrive_time"
    print(f"{'车次':<9}{'终到站/始发站':<24}{'时刻':<8}{'站台':<14}{'车底':<16}{'客运段'}")
    print("-" * 92)
    for r in rows[:limit]:
        other = r.get("end_station_name") if kind == "dep" else r.get("start_station_name")
        plat = r.get("platform_no", "").rstrip("#") or "--"
        print(
            f"{r.get('station_train_code',''):<9}"
            f"{other or '':<24}"
            f"{fmt_time(r.get(tcol,'')):<8}"
            f"{plat:<14}"
            f"{r.get('jiaolu_train_style') or '--':<16}"
            f"{r.get('jiaolu_corporation_code') or ''}"
        )
    if len(rows) > limit:
        print(f"... 共 {len(rows)} 条，仅显示前 {limit} 条")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    station = argv[1]
    code = resolve_station_code(station)
    d = argv[2] if len(argv) > 2 and not argv[2].startswith("-") else date.today().strftime("%Y%m%d")
    kind = argv[3] if len(argv) > 3 else "stat"
    limit = int(argv[4]) if len(argv) > 4 else 20

    payload = fetch_screen(code, d)
    rows = payload.get("data") or []

    if "--raw" in argv:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not payload.get("status"):
        print(f"[FAIL] status=false  errorMsg={payload.get('errorMsg')!r}")
        print("       提示：若你把请求改成了 GET，12306 一定回 (M0003)；本接口必须 POST。")
        return 1

    print(f"车站 {station} ({code})  日期 {d}  记录 {len(rows)} 条  "
          f"快照基准 {rows[0].get('base_datetime') if rows else '—'}")
    if not rows:
        # 空数组歧义：可能是超出可查窗口，也可能是真的没有车 —— 响应里区分不了
        print("⚠️ 返回空数据。注意：超出日期窗口时 12306 同样返回 status=true + []，")
        print("   '取不到' 与 '没有车' 在本接口无法区分，不要据此对用户说'该站当日无车'。")
        return 0

    dep, arr, thru = split_boards(rows, code)
    print(f"出发屏 {len(dep)} 条（其中过路 {len(thru)}） / 到达屏 {len(arr)} 条（含同一批过路）")
    print(f"数据基准时刻 base_datetime = {rows[0].get('base_datetime')}"
          f"  （渲染于 {datetime.now():%Y-%m-%d %H:%M:%S}）\n")

    if kind == "dep":
        render(dep, "dep", limit)
    elif kind == "arr":
        render(arr, "arr", limit)
    else:
        print("首末车次样例：")
        render(dep, "dep", 3)
        print()
        render(arr, "arr", 3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
