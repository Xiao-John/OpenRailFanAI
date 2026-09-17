"""本地数据字典访问层（里程 / 车站档案 / 离线时刻）。

数据来源与构建方式见 `scripts/mirror_dict.py`；本模块只读，**不在导入时联网**。
所有"按需抓取"（jprailfan 逐站里程表、车站档案）都会：
1. 先查本地缓存 → 命中直接返回（离线、毫秒级）；
2. 未命中才联网，成功后回填本地（下次离线可用）。

为什么这样做：两个来源都是**个人/社区站点**，把它们的页面放进每次请求路径既不礼貌也慢。
本地字典把"里程/档案"这类**变更很慢的字典数据**变成零延迟查询。

坐标与口径注意：
- GTFS 坐标为 **WGS84**；高德/腾讯为 GCJ-02，混用会偏 50–500m。
- GTFS 里 `DUMMY_*` 是**线路级占位车次**（用于让无客运车次的线路也有 stops），
  凡涉及"时刻/经停"的查询一律过滤。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

_log = logging.getLogger("railfan.dict")

# backend/app/data/dict.py → parents[2] = backend/
BACKEND = Path(__file__).resolve().parents[2]
def _settings():
    from app.config import get_settings

    return get_settings()


def db_path() -> Path:
    """字典库路径（配置可覆盖 `DICT_DB_PATH`；相对路径按 backend/ 解析）。"""
    raw = str(_settings().dict_db_path or "data/dict.db")
    p = Path(raw)
    return p if p.is_absolute() else (BACKEND / p)

# jprailfan：个人站点，礼貌约束（可识别 UA + 请求间隔下限）
_UA = "RailFanAI/0.1 (self-hosted railfan assistant; +https://github.com/railfanai)"
_last_call = 0.0
_conn: sqlite3.Connection | None = None


def available() -> bool:
    return db_path().exists()


def _connect() -> sqlite3.Connection | None:
    global _conn
    if _conn is not None:
        return _conn
    path = db_path()
    if not path.exists():
        return None
    _conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    return _conn


def _read(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _connect()
    if conn is None:
        return []
    return conn.execute(sql, params).fetchall()


def _write(sql: str, params: tuple = ()) -> None:
    """按需抓取的回填（只写缓存表；用可写连接）。"""
    global _conn
    with sqlite3.connect(db_path()) as c:
        c.execute(sql, params)
        c.commit()


def reset_for_tests() -> None:
    """测试用：丢弃缓存的连接（切换 DB 文件后调用）。"""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


# ---------- GTFS：离线时刻 / 坐标 / 里程 ----------

def _norm_train(code: str | None) -> str:
    return str(code or "").strip().upper()


def trip_stops(train_code: str) -> list[dict]:
    """车次的图定站序（含到发时刻与**累计里程**）；本地快照，非实时。

    `DUMMY_*` 占位车次一律排除（它们的到发时刻是伪造的 00:00）。
    """
    code = _norm_train(train_code)
    if not code:
        return []
    rows = _read(
        "SELECT st.seq, s.name AS station, st.arr, st.dep, st.dist "
        "FROM g_trip t JOIN g_stop_time st ON st.trip_id = t.trip_id "
        "JOIN g_stop s ON s.stop_id = st.stop_id "
        "WHERE t.is_dummy = 0 AND (t.short_name = ? OR t.trip_id = ?) "
        "ORDER BY st.seq",
        (code, code),
    )
    return [{"seq": r["seq"], "station": r["station"],
             "arrive": (r["arr"] or "")[:5], "depart": (r["dep"] or "")[:5],
             "distance_km": r["dist"]} for r in rows]


def station_coord(name: str) -> dict | None:
    """车站的 WGS84 坐标（GTFS stops）。"""
    rows = _read("SELECT stop_id, name, lat, lon FROM g_stop WHERE name = ? LIMIT 1", (str(name or "").strip(),))
    if not rows:
        return None
    r = rows[0]
    return {"name": r["name"], "lat": r["lat"], "lon": r["lon"], "crs": "WGS84"}


def distance_between(from_station: str, to_station: str) -> dict | None:
    """两站里程：从**同一条车次**的累计里程求差（GTFS `shape_dist_traveled`）。

    取"同时经停这两站、且里程差最小"的车次，避免绕行交路给出虚高里程。
    """
    a = str(from_station or "").strip()
    b = str(to_station or "").strip()
    if not a or not b or a == b:
        return None
    rows = _read(
        "SELECT t.short_name AS train, MIN(t.trip_id) AS trip_id, "
        "       (sb.dist - sa.dist) AS km, sa.seq AS sa_seq, sb.seq AS sb_seq "
        "FROM g_trip t "
        "JOIN g_stop_time sa ON sa.trip_id = t.trip_id "
        "JOIN g_stop_time sb ON sb.trip_id = t.trip_id "
        "JOIN g_stop ga ON ga.stop_id = sa.stop_id "
        "JOIN g_stop gb ON gb.stop_id = sb.stop_id "
        "WHERE t.is_dummy = 0 AND ga.name = ? AND gb.name = ? "
        "  AND sa.dist IS NOT NULL AND sb.dist IS NOT NULL AND sb.seq > sa.seq "
        "GROUP BY t.short_name "
        "ORDER BY km ASC LIMIT 5",
        (a, b),
    )
    if not rows:
        return None
    best = rows[0]
    return {"from_station": a, "to_station": b, "km": round(float(best["km"]), 1),
            "sample_train": best["train"], "source": "GTFS 快照（周更）"}


# ---------- jprailfan：线路汇总 / 逐站里程 / 车站档案 ----------

_polite_lock: "asyncio.Lock | None" = None
_polite_lock_loop = None


def _polite_lock_for_loop() -> "asyncio.Lock":
    """取当前事件循环的"礼貌间隔"锁。

    锁**按事件循环惰性创建**：模块级 `asyncio.Lock()` 会把自身绑定到首次使用它的循环，
    而测试/脚本会反复 `asyncio.run()`（每次都是新循环），复用同一个锁会直接报
    "is bound to a different event loop"。
    """
    global _polite_lock, _polite_lock_loop
    loop = asyncio.get_running_loop()
    if _polite_lock is None or _polite_lock_loop is not loop:
        _polite_lock = asyncio.Lock()
        _polite_lock_loop = loop
    return _polite_lock


async def _polite_get(params: dict) -> str:
    """对 jprailfan 发一次请求，并保证两次请求**起点**间隔 ≥ `dict_site_min_interval_s`。

    ⚠️ 必须是 async，且必须持有锁：这里原来是同步 `httpx.Client(timeout=120)` + `time.sleep`，
    被 async 工具 `rail_mileage` 直接调用 → **阻塞整个事件循环最长约 120 秒**。
    期间该 worker 上所有并发请求、所有进行中的 SSE 流全部停摆，且日志里没有任何异常
    （表现是"服务莫名其妙卡死"，不是"某个请求慢"）。
    锁把"礼貌间隔"变成并发调用之间的**排队**，而不是各自 sleep 完一起打过去。
    """
    global _last_call
    interval = float(_settings().dict_site_min_interval_s or 2.0)
    async with _polite_lock_for_loop():
        wait = interval - (time.time() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.time()
        from app.tools._http import get_client

        client = await get_client()
        r = await client.get(
            "https://www.jprailfan.com/tools/stat/index.php",
            params=params,
            # 个人站点：可识别 UA + 长超时（页面大、站点慢），覆盖共享 client 的默认超时
            headers={"User-Agent": _UA},
            timeout=120.0,
        )
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


def _rows(html: str) -> list[list[str]]:
    import re

    return [c for c in (_cells(r) for r in re.findall(r"<tr.*?</tr>", html, flags=re.S)) if c]


def line_master(line: str) -> dict | None:
    """本地线路汇总表里的线路（线路名 → 起终站 + 里程 + 备注）。"""
    rows = _read("SELECT * FROM line_master WHERE line = ? LIMIT 1", (str(line or "").strip(),))
    if not rows:
        return None
    r = rows[0]
    return {"line": r["line"], "from_station": r["from_station"], "to_station": r["to_station"],
            "mileage_km": r["mileage_km"], "remark": r["remark"], "fetched_at": r["fetched_at"]}


def search_lines(keyword: str, limit: int = 8) -> list[dict]:
    rows = _read("SELECT * FROM line_master WHERE line LIKE ? ORDER BY mileage_km DESC LIMIT ?",
                 (f"%{str(keyword or '').strip()}%", int(limit)))
    return [{"line": r["line"], "from_station": r["from_station"], "to_station": r["to_station"],
             "mileage_km": r["mileage_km"]} for r in rows]


async def line_stations(line: str, *, force: bool = False) -> list[dict]:
    """线路逐站里程表（本地优先；未命中则抓一次并回填缓存）。

    含 12306 没有的字段：电报码 / TMIS 车站编号 / 是否接算站 / 营业限制，
    以及 `京津所` 这类**线路连接点**（页面注明"不是铁路车站"）。

    ⚠️ async：未命中本地缓存时会联网抓 jprailfan（最长 120 s），调用方必须 `await`，
    否则会把这个**同步阻塞**重新引回事件循环（见 `_polite_get` 的说明）。
    """
    name = str(line or "").strip()
    if not name:
        return []
    cached = _read("SELECT * FROM line_station WHERE line = ? ORDER BY seq", (name,))
    if cached and not force:
        return [dict(r) for r in cached]
    import datetime as _dt
    import re

    html = await _polite_get({"linename": name})
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    records = []
    for cells in _rows(html):
        # 逐站行：线路 | 车站 | 拼音 | 电报码 | 编号 | 路局 | 行政区 | 距起始站里程 | 相邻站里程 | 接算站 | 接算线路 | 办理限制
        if len(cells) < 8 or cells[0] != name:
            continue
        if not re.match(r"^\d+(\.\d+)?km$", cells[7] or ""):
            continue
        records.append((
            name, len(records) + 1, cells[1], cells[2], cells[3], cells[4], cells[5], cells[6],
            cells[7], cells[8] if len(cells) > 8 else "",
            cells[9] if len(cells) > 9 else "", cells[10] if len(cells) > 10 else "",
            cells[11] if len(cells) > 11 else "",
            1 if "所" in (cells[1] or "") and "站" not in (cells[1] or "") else 0, now,
        ))
    if not records:
        return []
    with sqlite3.connect(db_path()) as c:
        c.execute("DELETE FROM line_station WHERE line = ?", (name,))
        c.executemany(
            "INSERT OR REPLACE INTO line_station(line,seq,station,pinyin,telecode,tmjs_no,bureau,"
            "region,dist_from_start,dist_adjacent,is_settlement,settlement_lines,restriction,"
            "is_connector,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", records)
        c.commit()
    return [dict(r) for r in _read("SELECT * FROM line_station WHERE line = ? ORDER BY seq", (name,))]


async def station_profile(station: str, *, force: bool = False) -> dict | None:
    """车站档案（本地优先；未命中抓一次并回填）。含 12306 没有的编号/接算站/营业限制。

    ⚠️ async：未命中本地缓存时会联网抓 jprailfan，调用方必须 `await`。
    """
    name = str(station or "").strip()
    if not name:
        return None
    cached = _read("SELECT * FROM station_profile WHERE station = ? LIMIT 1", (name,))
    if cached and not force:
        return dict(cached[0])
    import datetime as _dt

    html = await _polite_get({"statinfo": name})
    rows = _rows(html)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    for cells in rows:
        # 表头：车站名称|拼音码|电报码|车站编号|所属路局|是否接算站|所属行政区|营业限制|电话订票区号
        if len(cells) >= 9 and cells[0] == name and cells[1].isalpha():
            _write(
                "INSERT OR REPLACE INTO station_profile(station,pinyin,telecode,tmjs_no,bureau,"
                "is_settlement,region,restriction,phone_area,raw_json,fetched_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (name, cells[1], cells[2], cells[3], cells[4], cells[5], cells[6], cells[7], cells[8],
                 "", now))
            return dict(_read("SELECT * FROM station_profile WHERE station = ? LIMIT 1", (name,))[0])
    return None


def stats() -> dict:
    def n(sql: str) -> int:
        rows = _read(sql)
        return int(rows[0][0]) if rows else 0

    return {
        "available": available(),
        "path": str(db_path()),
        "stops": n("SELECT COUNT(*) FROM g_stop"),
        "trips": n("SELECT COUNT(*) FROM g_trip"),
        "stop_times": n("SELECT COUNT(*) FROM g_stop_time"),
        "lines": n("SELECT COUNT(*) FROM line_master"),
        "line_stations_cached": n("SELECT COUNT(*) FROM line_station"),
        "station_profiles_cached": n("SELECT COUNT(*) FROM station_profile"),
    }
