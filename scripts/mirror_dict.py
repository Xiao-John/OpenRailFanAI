#!/usr/bin/env python3
"""构建/刷新本地数据字典库（`backend/data/dict.db`，gitignored）。

为什么要本地化（2026-09-15 拍板）
--------------------------------
里程与车站档案是本项目的**能力空白**（此前只能靠模型瞎猜）；两个来源都可离线化，
且都属"字典类"数据（变更慢），**放在请求路径里联网取数既慢又对他人站点不礼貌**：

1. **GTFS**（`wensimehrp/chinese-railway-gtfs`，周更）：全路车次时刻 + 车站 WGS84 坐标
   + 每段累计里程。境内 **必须走镜像**（`github.com`/release 资产链被阻断）。
2. **jprailfan 线路汇总表**（`?key7=所有线路输出到本页`）：**一次请求**即得全部线路的
   起终站与里程（实测 345KB / 16s），避免按线路逐条抓。

用法
----
    python3 scripts/mirror_dict.py --gtfs          # 抓 GTFS（周更，已新鲜则跳过）
    python3 scripts/mirror_dict.py --lines         # 抓 jprailfan 线路汇总表
    python3 scripts/mirror_dict.py --all           # 两个都抓
    python3 scripts/mirror_dict.py --stats         # 只看本地库现状

礼遇约束（对个人站点/社区项目）：单次请求、可识别 UA、失败退避、不在请求路径里跑。
"""
from __future__ import annotations

import argparse
import io
import os
import json
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
# 与后端配置一致（DICT_DB_PATH 可覆盖；默认 backend/data/dict.db，已 gitignore）。
# 这里读环境变量而不是 import app.config：脚本要能在不装项目依赖时运行。
_db = Path(os.environ.get("DICT_DB_PATH", "data/dict.db"))
DB_PATH = _db if _db.is_absolute() else REPO / "backend" / _db

GTFS_REPO = "wensimehrp/chinese-railway-gtfs"
GH_MIRROR = "https://ghfast.top/"                     # 实测可用；ghproxy.net/gh.llkk.cc 均 000
JPRAILFAN = "https://www.jprailfan.com/tools/stat/index.php"
# 可识别 UA：便于对方站点在日志里认出我们、必要时联系我们（而不是当成匿名爬虫）
UA = "RailFanAI/0.1 (self-hosted railfan assistant; +https://github.com/railfanai)"
HEADERS = {"User-Agent": UA}
JP_LINES_KEY = "所有线路输出到本页"                    # key7 按钮值（实测必须带完整值才导出）
JP_MIN_INTERVAL_S = 2.0                              # 对个人站点：请求间隔下限
_last_jp_call = 0.0


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- GTFS：车站（含 WGS84 坐标）
CREATE TABLE IF NOT EXISTS g_stop (
    stop_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    lat     REAL,
    lon     REAL
);
CREATE INDEX IF NOT EXISTS ix_g_stop_name ON g_stop(name);
-- GTFS：车次（DUMMY_* 为线路级占位，做时刻回答时必须过滤）
CREATE TABLE IF NOT EXISTS g_trip (
    trip_id   TEXT PRIMARY KEY,
    route_id  TEXT,
    short_name TEXT,
    is_dummy  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_g_trip_short ON g_trip(short_name);
-- GTFS：站序（含累计里程）
CREATE TABLE IF NOT EXISTS g_stop_time (
    trip_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    stop_id   TEXT NOT NULL,
    arr       TEXT,
    dep       TEXT,
    dist      REAL,
    PRIMARY KEY (trip_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_g_stop_time_stop ON g_stop_time(stop_id);
-- jprailfan：线路汇总（线路名 / 起终站 / 里程 / 备注）
CREATE TABLE IF NOT EXISTS line_master (
    line       TEXT PRIMARY KEY,
    from_station TEXT,
    to_station   TEXT,
    mileage_km   REAL,
    remark       TEXT,
    fetched_at   TEXT
);
-- jprailfan：线路逐站里程表（按需抓取后缓存）
CREATE TABLE IF NOT EXISTS line_station (
    line          TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    station       TEXT,
    pinyin        TEXT,
    telecode      TEXT,
    tmjs_no       TEXT,
    bureau        TEXT,
    region        TEXT,
    dist_from_start TEXT,
    dist_adjacent   TEXT,
    is_settlement   TEXT,
    settlement_lines TEXT,
    restriction     TEXT,
    is_connector    INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT,
    PRIMARY KEY (line, seq)
);
-- jprailfan：车站档案
CREATE TABLE IF NOT EXISTS station_profile (
    station    TEXT PRIMARY KEY,
    pinyin     TEXT,
    telecode   TEXT,
    tmjs_no    TEXT,
    bureau     TEXT,
    is_settlement TEXT,
    region     TEXT,
    restriction   TEXT,
    phone_area    TEXT,
    raw_json      TEXT,
    fetched_at    TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


# ---------- GTFS ----------

def _polite_sleep() -> None:
    global _last_jp_call
    wait = JP_MIN_INTERVAL_S - (time.time() - _last_jp_call)
    if wait > 0:
        time.sleep(wait)
    _last_jp_call = time.time()


def fetch_gtfs(force: bool = False, max_age_days: int = 5) -> bool:
    """下载最新 GTFS 快照并入库。已新鲜则跳过（周更数据，无需每次抓）。"""
    conn = connect()
    last = get_meta(conn, "gtfs_pulled_at")
    if last and not force:
        import datetime as _dt

        try:
            age = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(last)).days
        except ValueError:
            age = 999
        if age < max_age_days:
            print(f"[gtfs] 本地快照 {age} 天前抓取（< {max_age_days} 天），跳过。用 --force 强制刷新。")
            return True

    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as c:
        rel = c.get(f"https://api.github.com/repos/{GTFS_REPO}/releases/latest")
        rel.raise_for_status()
        data = rel.json()
        tag = data["tag_name"]
        asset = next((a for a in data.get("assets") or [] if a["name"].endswith(".zip")), None)
        if not asset:
            print("[gtfs] release 里没有 zip 资产，放弃")
            return False
        url = GH_MIRROR + asset["browser_download_url"]
        print(f"[gtfs] 抓取 {tag}（{asset['size']} B，经镜像）…")
        t0 = time.time()
        resp = c.get(url)
        resp.raise_for_status()
        blob = resp.content
    print(f"[gtfs] 下载完成 {len(blob)} B · {time.time()-t0:.1f}s")

    if len(blob) != asset["size"]:
        print(f"[gtfs] ⚠️ 字节数不符（期望 {asset['size']}，实得 {len(blob)}），仍继续解析")

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())

        def rows(name):
            import csv

            with z.open(name) as fh:
                yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))

        conn.execute("DELETE FROM g_stop")
        conn.executemany(
            "INSERT OR REPLACE INTO g_stop(stop_id,name,lat,lon) VALUES(?,?,?,?)",
            [(r["stop_id"], r.get("stop_name", ""),
              float(r["stop_lat"]) if r.get("stop_lat") else None,
              float(r["stop_lon"]) if r.get("stop_lon") else None) for r in rows("stops.txt")],
        )
        conn.execute("DELETE FROM g_trip")
        conn.executemany(
            "INSERT OR REPLACE INTO g_trip(trip_id,route_id,short_name,is_dummy) VALUES(?,?,?,?)",
            [(r["trip_id"], r.get("route_id", ""), r.get("trip_short_name", ""),
              1 if r["trip_id"].startswith("DUMMY") else 0) for r in rows("trips.txt")],
        )
        conn.execute("DELETE FROM g_stop_time")
        conn.executemany(
            "INSERT OR REPLACE INTO g_stop_time(trip_id,seq,stop_id,arr,dep,dist) VALUES(?,?,?,?,?,?)",
            [(r["trip_id"], int(r["stop_sequence"]), r["stop_id"], r.get("arrival_time", ""),
              r.get("departure_time", ""),
              float(r["shape_dist_traveled"]) if r.get("shape_dist_traveled") else None)
             for r in rows("stop_times.txt")],
        )
    set_meta(conn, "gtfs_pulled_at", __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat())
    set_meta(conn, "gtfs_tag", tag)
    conn.commit()
    stats(conn)
    return True


# ---------- jprailfan ----------

def _jp_get(params: dict) -> str:
    _polite_sleep()
    with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as c:
        r = c.get(JPRAILFAN, params=params)
        r.raise_for_status()
        return r.text


def _cells(row_html: str) -> list[str]:
    import html as _html
    import re

    out = []
    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S):
        txt = _html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        out.append(re.sub(r"\s+", " ", txt))
    return out


def fetch_lines(force: bool = False, max_age_days: int = 30) -> bool:
    """抓 jprailfan「所有线路输出到本页」（key7）→ line_master。"""
    import datetime as _dt

    conn = connect()
    last = get_meta(conn, "lines_pulled_at")
    if last and not force:
        try:
            age = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(last)).days
        except ValueError:
            age = 999
        if age < max_age_days:
            print(f"[lines] 本地 {age} 天前抓取（< {max_age_days} 天），跳过。用 --force 强制刷新。")
            return True

    print("[lines] 抓取全部线路汇总（对个人站点：单次请求 + 间隔 ≥2s）…")
    html = _jp_get({"key7": JP_LINES_KEY})
    import re

    rows = re.findall(r"<tr.*?</tr>", html, flags=re.S)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    records = []
    for row in rows:
        cells = _cells(row)
        # 表头：线路名称 | 起始站 | 终到站 | 里程 | 备注
        if len(cells) < 4 or cells[0] in ("线路名称", ""):
            continue
        if not re.match(r"^\d[\d,\.]*km$", cells[3] or ""):
            continue
        mileage = float(cells[3].replace(",", "").replace("km", ""))
        records.append((cells[0], cells[1], cells[2], mileage,
                        cells[4] if len(cells) > 4 else "", now))
    if not records:
        print("[lines] ⚠️ 未解析到任何线路（页面结构可能变化）")
        return False
    conn.executemany(
        "INSERT OR REPLACE INTO line_master(line,from_station,to_station,mileage_km,remark,fetched_at)"
        " VALUES(?,?,?,?,?,?)", records)
    set_meta(conn, "lines_pulled_at", now)
    conn.commit()
    print(f"[lines] 入库 {len(records)} 条线路")
    stats(conn)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="构建本地数据字典库")
    ap.add_argument("--gtfs", action="store_true", help="抓 GTFS 快照")
    ap.add_argument("--lines", action="store_true", help="抓 jprailfan 线路汇总表")
    ap.add_argument("--all", action="store_true", help="两个都抓")
    ap.add_argument("--stats", action="store_true", help="只打印本地库现状")
    ap.add_argument("--force", action="store_true", help="忽略新鲜度检查，强制重抓")
    args = ap.parse_args()

    conn = connect()
    if args.stats or not (args.gtfs or args.lines or args.all):
        stats(conn)
        return 0
    ok = True
    if args.gtfs or args.all:
        ok = fetch_gtfs(force=args.force) and ok
    if args.lines or args.all:
        ok = fetch_lines(force=args.force) and ok
    return 0 if ok else 1


def stats(conn: sqlite3.Connection) -> None:
    def n(sql: str, *p) -> int:
        return conn.execute(sql, p).fetchone()[0]

    print(f"\n本地字典库：{DB_PATH}")
    print(f"  GTFS 快照版本 : {get_meta(conn,'gtfs_tag') or '(未抓取)'}  抓取于 {get_meta(conn,'gtfs_pulled_at') or '-'}")
    print(f"  线路汇总表    : {get_meta(conn,'lines_pulled_at') or '(未抓取)'}")
    print(f"  g_stop        : {n('SELECT COUNT(*) FROM g_stop')}")
    print(f"  g_trip        : {n('SELECT COUNT(*) FROM g_trip')}（其中 DUMMY 占位 {n('SELECT COUNT(*) FROM g_trip WHERE is_dummy=1')}）")
    print(f"  g_stop_time   : {n('SELECT COUNT(*) FROM g_stop_time')}")
    print(f"  line_master   : {n('SELECT COUNT(*) FROM line_master')}")
    print(f"  line_station  : {n('SELECT COUNT(*) FROM line_station')}（按需抓取后缓存）")
    print(f"  station_profile: {n('SELECT COUNT(*) FROM station_profile')}（按需抓取后缓存）")


if __name__ == "__main__":
    sys.exit(main())
