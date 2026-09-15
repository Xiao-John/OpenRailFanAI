"""本地数据字典（里程 / 车站档案 / 离线时刻）回归测试。

覆盖 2026-09-15 拍板的"字典本地化"：
- `scripts/mirror_dict.py` 构建的本地库（GTFS 周更快照 + 黄河铁路网线路汇总表）
- `app/data/dict.py` 访问层（含按需抓取回填缓存）
- `rail.mileage` 工具（两站里程 / 线路逐站里程 / 车站档案）
- 检索层路由：**纯里程问题不得再去调冷启动 ~19s 的慢接口**

前置：本地字典缺失时本套件给出明确跳过提示（不判失败），
构建命令：`python3 scripts/mirror_dict.py --all`

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_dict_mileage.py
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.data import dict as D
from app.pipeline import retrieve as R
from app.pipeline.extract import Slots
from app.tools.base import ToolResult
from app.tools.rail_mileage import RailMileageTool

_skips: list[str] = []


def _require_dict() -> bool:
    if not D.available():
        _skips.append("本地字典未构建（python3 scripts/mirror_dict.py --all）")
        return False
    return True


# ---------- 1. 访问层 ----------

def test_gtfs_lookup():
    if not _require_dict():
        return
    st = D.stats()
    assert st["trips"] > 1000 and st["stops"] > 1000, st

    stops = D.trip_stops("G1")
    names = [s["station"] for s in stops]
    assert len(stops) >= 5, names
    assert names[0] == "北京南" and names[-1] == "上海虹桥", names
    assert stops[-1]["distance_km"] == 1318.0, stops[-1]
    # 累计里程必须单调递增（否则"里程差"无意义）
    dists = [s["distance_km"] for s in stops]
    assert all(b >= a for a, b in zip(dists, dists[1:])), dists
    print(f"[PASS] GTFS 本地快照：G1 {len(stops)} 站，终点 {stops[-1]['distance_km']}km，累计里程单调")


def test_dummy_trips_are_filtered():
    """`DUMMY_*` 是线路级占位（到发时刻是伪造的 00:00），**绝不能**当车次返回。"""
    if not _require_dict():
        return
    rows = D._read("SELECT trip_id FROM g_trip WHERE is_dummy=1 LIMIT 1")
    if not rows:
        print("[SKIP] 本地快照里没有 DUMMY 占位车次")
        return
    dummy_id = rows[0]["trip_id"]
    assert D.trip_stops(dummy_id) == [], f"DUMMY 占位车次被当作真实车次返回：{dummy_id}"
    print(f"[PASS] DUMMY 占位车次已过滤（{dummy_id} → 空）")


def test_distance_and_coord():
    if not _require_dict():
        return
    a = D.distance_between("北京南", "济南西")
    assert a and abs(a["km"] - 406) < 1, a            # 与黄河里程表互证：406km
    b = D.distance_between("北京南", "上海虹桥")
    assert b and abs(b["km"] - 1318) < 1, b
    assert D.distance_between("北京南", "北京南") is None
    coord = D.station_coord("北京南")
    assert coord and coord["crs"] == "WGS84" and 39 < coord["lat"] < 41, coord
    print(f"[PASS] 两站里程 北京南→济南西 {a['km']}km / →上海虹桥 {b['km']}km；坐标 {coord['lat']:.4f},{coord['lon']:.4f} (WGS84)")


def test_line_master_both_gauges():
    """既有线口径与高铁口径必须都在，且**不得混用**。"""
    if not _require_dict():
        return
    high = D.line_master("京沪高速线")
    old = D.line_master("京沪线")
    assert high and abs(high["mileage_km"] - 1318) < 1, high
    assert old and abs(old["mileage_km"] - 1463) < 1, old
    print(f"[PASS] 线路汇总两种口径：京沪高速线 {high['mileage_km']}km / 京沪线（既有）{old['mileage_km']}km")


# ---------- 2. 工具 ----------

def test_tool_two_stations():
    if not _require_dict():
        return
    res = asyncio.run(RailMileageTool().invoke({"from": "北京南", "to": "济南西"}))
    assert res.ok, res.error
    assert res.data["km"] == 406.0, res.data
    assert "406" in res.text and "图定径路里程" in (res.note or ""), res.note
    assert res.total == 1 and res.shown == 1, (res.total, res.shown)
    print(f"[PASS] rail.mileage 两站：{res.text.splitlines()[0][:60]}")


def test_tool_line_table_marks_connectors():
    """线路逐站表必须标注"线路连接点不是车站"（京津所一类），否则模型会把它们当车站。"""
    if not _require_dict():
        return
    res = asyncio.run(RailMileageTool().invoke({"line": "京沪高速线"}))
    assert res.ok, res.error
    assert "1318" in res.text, res.text[:200]
    assert "线路连接点" in res.text and "京津所" in res.text, res.text[:600]
    assert "接算站" in res.text and "编号10010" in res.text, res.text[:600]
    print("[PASS] rail.mileage 线路表：逐站里程 + TMIS 编号 + 连接点标注")


def test_tool_station_profile_and_missing():
    if not _require_dict():
        return
    res = asyncio.run(RailMileageTool().invoke({"station": "北京南"}))
    assert res.ok, res.error
    for piece in ("-VNP", "10010", "接算站"):
        assert piece in res.text, res.text
    bad = asyncio.run(RailMileageTool().invoke({"station": "阿斯加德"}))
    assert not bad.ok and "未取到" in (bad.error or ""), bad.error
    noargs = asyncio.run(RailMileageTool().invoke({}))
    assert not noargs.ok and "缺少参数" in (noargs.error or ""), noargs.error
    print("[PASS] rail.mileage 车站档案 + 非法站名/缺参优雅失败")


# ---------- 3. 检索层路由（性能相关）----------

class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name: str, params: dict) -> ToolResult:
        self.calls.append((name, dict(params or {})))
        return ToolResult(ok=True, data={}, text=f"stub:{name}")

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def params_of(self, name: str) -> list[dict]:
        return [p for n, p in self.calls if n == name]


def _plan(intent: str, message: str, **slots) -> list[str]:
    rec = _Recorder()
    with patch("app.tools.registry.invoke_by_name", rec):
        asyncio.run(R.retrieve(intent, Slots(**slots), question_type="realtime", message=message))
    return rec.names()


def test_pure_mileage_question_skips_slow_tools():
    """★ 纯里程问题：只用本地字典（毫秒级），**不得**调 rail.line_stations（冷启动 ~19s）。"""
    names = _plan("rail_line", "北京南到济南西多少公里？")
    assert "rail.mileage" in names, names
    assert "rail.line_stations" not in names, f"纯里程问题仍调了慢接口：{names}"
    assert "rail.line" not in names, f"纯里程问题仍调了 rail.line：{names}"
    print(f"[PASS] 纯里程问题 -> {names}（不含慢接口）")


def test_stops_question_still_uses_line_tool():
    """问"经过哪些站"仍走指定径路（站序的权威口径），口径不同要能同时给。"""
    names = _plan("rail_line", "京沪线经过哪些站？")
    assert "rail.line_stations" in names, names
    combo = _plan("rail_line", "京沪线经过哪些站、各站多少公里？")
    assert "rail.line_stations" in combo and "rail.mileage" in combo, combo
    print(f"[PASS] 站序问题 -> {names}；站序+里程 -> {combo}")


def test_station_profile_routing_uses_authoritative_index():
    """车站档案问法：站名必须用**本地站点库最长匹配**，不得正则硬切出「换乘车」。"""
    names = _plan("station", "北京南站的车站编号是多少？")
    assert "rail.mileage" in names, names
    rec = _Recorder()
    with patch("app.tools.registry.invoke_by_name", rec):
        asyncio.run(R.retrieve("station", Slots(), question_type="realtime",
                               message="北京南站的车站编号是多少？"))
    params = rec.params_of("rail.mileage")
    assert params and params[0].get("station") == "北京南", params
    print(f"[PASS] 车站档案路由：station={params[0]['station']}")


def test_station_from_text_never_invents():
    """站名抽取的假名防护（曾是静默失败点：少一句 import → 静默退回正则）。"""
    cases = {"北京南站的车站编号是多少？": "北京南",
             "上海虹桥站是不是接算站？": "上海虹桥",
             "换乘车站的编号怎么看？": "",
             "这站的编号是多少？": "",
             "G1今天由哪组担当？": ""}
    for text, expect in cases.items():
        got = asyncio.run(R._station_from_text(text))
        assert got == expect, f"{text} → {got!r}（期望 {expect!r}）"
    print("[PASS] 站名抽取：最长匹配 + 无真实站名时返回空（不硬切）")


def main():
    test_gtfs_lookup()
    test_dummy_trips_are_filtered()
    test_distance_and_coord()
    test_line_master_both_gauges()
    test_tool_two_stations()
    test_tool_line_table_marks_connectors()
    test_tool_station_profile_and_missing()
    test_pure_mileage_question_skips_slow_tools()
    test_stops_question_still_uses_line_tool()
    test_station_profile_routing_uses_authoritative_index()
    test_station_from_text_never_invents()
    if _skips:
        print(f"\n[WARN] 有 {len(_skips)} 项因前置缺失被跳过：")
        for s in _skips:
            print(f"       · {s}")
    print("\n本地数据字典（里程/档案）测试全部通过 ✔")


if __name__ == "__main__":
    main()
