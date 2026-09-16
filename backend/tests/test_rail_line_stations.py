"""按线路名查站序/里程（F06 + F05）的回归测试 —— **无网络**。

逆向的接口流程（`jprailfan.com` 「指定径路查询」，见 `docs/datasources.md`）：
    step1 POST {i:"", s0:<起始站>}            → <select name=r1> 该站出发线路
    step2 POST {i:"1", s0, r1:<线路>}         → <select name=s1> **整条线站序**
    step3 POST {…, s1:<终点站>}               → 指定径路结果表（区间/累计里程）

覆盖：
- F06：按线路名反查站序
- F05：既有线（普速）口径里程（京沪线 北京→上海 = 1463km，对照最短径路 1320km）
- 路由：`rail_line` 意图在"线路名 + 问站序/里程"时走新工具 `rail.line_stations`
- 缓存：同一查询第二次不再抓页面

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_rail_line_stations.py
"""
from __future__ import annotations

import asyncio

from app.pipeline import retrieve as retrieve_mod
from app.pipeline.extract import Slots
from app.tools import rail_line as rl
from app.tools import registry as registry_mod
from app.tools.base import ToolResult

# ---------- 夹具：模拟三个步骤的响应 ----------

_STEP1 = """
<input type=hidden name=i id=i value=1>
<select name=r1 id=r1 onChange=submitForm()>
<option value=0>线路</option>
<option value=北京直通线>北京直通线(北京-北京西)</option>
<option value=京沪线>京沪线(北京-上海)</option>
<option value=京哈线>京哈线(北京-哈尔滨)</option>
</select>
<select name=s1 id=s1><option value=0>请先选择线路</option></select>
"""

_STEP2 = """
<input type=hidden name=i id=i value=1>
<select name=s1 id=s1>
<option value=0>请先选择线路</option>
<option value=北京>北京(●)</option>
<option value=天津西>天津西(●)</option>
<option value=德州>德州(●)</option>
<option value=南京>南京(●)</option>
<option value=上海>上海(●)</option>
</select>
"""

# 指定径路结果表（含"总Xkm"累计里程；注意 desgroute 页把 select 嵌在表旁，解析必须先剥离）
_STEP3 = """
<select name=r1 id=r1><option value=京沪线>京沪线(北京-上海)</option></select>
<table>
<tr><th>线路</th><th>车站全名</th><th>车站简称</th><th>车站电报码</th><th>里程</th></tr>
<tr><td></td><td>北京</td><td>京</td><td>-BJP</td><td>0km</td></tr>
<tr><td>京沪线</td><td>天津西</td><td>津</td><td>-TXP</td><td>137km/总137km</td></tr>
<tr><td>京沪线</td><td>南京</td><td>宁</td><td>-NJH</td><td>1162km/总1162km</td></tr>
<tr><td>京沪线</td><td>上海</td><td>沪</td><td>-SHH</td><td>301km/总1463km</td></tr>
</table>
"""

_calls: list[dict] = []


async def _fake_post(url: str, data: dict, **kw) -> str:
    _calls.append(dict(data))
    if not data.get("r1"):
        return _STEP1
    if not data.get("s1"):
        return _STEP2
    return _STEP3


def _with_fake_post():
    original = rl.post_text
    rl.post_text = _fake_post                      # type: ignore[assignment]
    rl._LINE_CACHE.clear()
    _calls.clear()
    return original


def _restore(original):
    rl.post_text = original                        # type: ignore[assignment]
    rl._LINE_CACHE.clear()


# ---------- F06：站序 ----------

def test_line_stations_sequence():
    original = _with_fake_post()
    try:
        res = asyncio.run(rl.RailLineStationsTool().invoke({"line": "京沪线"}))
    finally:
        _restore(original)

    assert res.ok, res.error
    stations = res.data["stations"]
    assert stations[0] == "北京" and stations[-1] == "上海", stations
    assert "天津西" in stations and "南京" in stations, stations
    # (●) 标记必须被清理
    assert all("●" not in s for s in stations), stations
    assert "共 5 站" in res.text, res.text[:120]
    assert len(_calls) == 3, f"应为 3 次请求（选站→选线→选终点），实际 {len(_calls)}"
    print(f"[PASS] F06 站序：{stations}（3 次请求）")


def test_line_mileage_is_line_scope_not_shortest_route():
    """F05：指定径路要给出线路口径的里程（本夹具 1463km，区别于最短径路 1320km）。"""
    original = _with_fake_post()
    try:
        res = asyncio.run(
            rl.RailLineStationsTool().invoke(
                {"line": "老京沪线", "from_station": "北京", "to_station": "上海"}
            )
        )
    finally:
        _restore(original)

    assert res.ok, res.error
    assert res.data["line"] == "京沪线", res.data["line"]     # "老京沪线" 归一化
    assert res.data["route"][-1]["cum_km"] == 1463.0, res.data["route"][-1]
    assert "1463" in res.text, res.text[:160]
    assert "指定径路" in res.note and "既有线" in res.note, res.note
    print(f"[PASS] F05 既有线口径里程 -> {res.text.splitlines()[0][:70]}")


def test_result_is_cached():
    original = _with_fake_post()
    try:
        asyncio.run(rl.RailLineStationsTool().invoke({"line": "京沪线"}))
        n1 = len(_calls)
        res2 = asyncio.run(rl.RailLineStationsTool().invoke({"line": "京沪线"}))
        n2 = len(_calls)
    finally:
        _restore(original)
    assert n1 == 3 and n2 == 3, f"第二次应命中缓存（请求数 {n1}→{n2}）"
    assert "缓存" in res2.note, res2.note
    print("[PASS] 同一线路第二次命中缓存（不再抓 3 个页面）")


def test_unknown_line_is_honest():
    original = _with_fake_post()
    try:
        res = asyncio.run(rl.RailLineStationsTool().invoke({"line": "不存在的线"}))
    finally:
        _restore(original)
    assert res.ok is False, res
    assert "未在径路库中找到线路" in res.error, res.error
    print(f"[PASS] 未知线路 -> {res.error}")


# ---------- 路由 ----------

class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._original = registry_mod.invoke_by_name

    async def __call__(self, name: str, params: dict) -> ToolResult:
        self.calls.append((name, dict(params or {})))
        return ToolResult(ok=True, data={}, text="stub", sources=[])

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def __enter__(self):
        registry_mod.invoke_by_name = self      # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        registry_mod.invoke_by_name = self._original   # type: ignore[assignment]
        return False


def _plan(msg: str, **slots) -> list[str]:
    with _Recorder() as rec:
        asyncio.run(
            retrieve_mod.retrieve("rail_line", Slots(**slots), question_type="realtime", message=msg)
        )
    return rec.names()


def test_routing_sends_line_questions_to_new_tool():
    assert _plan("京沪线经过哪些站？", target="京沪线") == ["rail.line_stations"]
    # 2026-09-15 拍板：**纯里程**问题改走本地字典 rail.mileage（毫秒级）；
    # rail.line_stations 冷启动实测 ~19s（要抓 3 个 800KB 页面），不该被"多少公里"触发。
    assert _plan("陇海线全程多少公里？", target="陇海线") == ["rail.mileage"]
    # 站序 + 里程一起问：两个口径都要给
    assert _plan("京沪线经过哪些站、各站多少公里？", target="京沪线") == ["rail.mileage", "rail.line_stations"]
    # 两站间"走哪条线路"仍走最短径路（口径不同，不能混）
    assert _plan("北京到上海走哪条线路？") == ["rail.line", "station.lookup"]
    print("[PASS] 路由：站序→rail.line_stations；纯里程→rail.mileage（本地）；两站间→rail.line（口径不混）")


def test_line_station_phrasings_survive_the_whole_chain():
    """整链回归：线路车站类问法换种说法也不能跑偏（快路径判定 → 工具计划）。

    用户实测缺陷：'京沪线经过哪些车站' 正常回答，而 '京沪线的所有车站' 被判成
    station（车站信息查询），于是去调 station.lookup('京沪线') —— 拿**线路名**当
    **站名**查，必然失败，用户拿到的是"本次无法给出完整列表"。
    同一件事换个说法结果完全不同，说明这不是数据问题而是判定问题。

    这类问法的口径由"线路名 + 车站清单词"完全确定，不该交给模型猜。
    """
    from app.pipeline import fastpath

    for msg in ["京沪线经过哪些车站", "京沪线的所有车站",
                "京沪高铁经过哪些站", "京沪线沿线有哪些车站"]:
        fp = asyncio.run(fastpath.plan(msg))
        assert fp is not None, f"{msg} 未被快路径接管（会交给 LLM 猜，实测会判错）"
        assert fp.intent == "rail_line", f"{msg} → intent={fp.intent}（应为 rail_line）"
        names = _plan(msg, target=fp.slots.target)
        assert names == ["rail.line_stations"], f"{msg} → {names}"
    # 车站类问题不能被带偏（station.lookup 查的是**单座车站**）
    for msg in ["上海虹桥站大屏", "北京南站的电报码是多少"]:
        fp = asyncio.run(fastpath.plan(msg))
        assert fp is not None and fp.intent == "station", f"{msg} → {fp and fp.intent}"
    print("[PASS] 整链：线路车站类问法→rail_line→rail.line_stations；车站类仍走 station")


def test_line_name_detection_and_normalization():
    assert rl.normalize_line_name("老京沪线") == "京沪线"
    assert rl.normalize_line_name("京沪高铁") == "京沪高速线"
    assert rl.normalize_line_name("京沪线") == "京沪线"
    # 泛指词不应被当成线路名
    from app.pipeline.retrieve import _detect_line_name

    assert _detect_line_name("北京到上海走哪条线路？") is None, _detect_line_name("北京到上海走哪条线路？")
    assert _detect_line_name("京沪线经过哪些站") == "京沪线"
    assert _detect_line_name("走老京沪线多少公里") == "老京沪线"
    print("[PASS] 线路名识别与归一化（泛指词不误判）")


def main():
    test_line_stations_sequence()
    test_line_mileage_is_line_scope_not_shortest_route()
    test_result_is_cached()
    test_unknown_line_is_honest()
    test_routing_sends_line_questions_to_new_tool()
    test_line_station_phrasings_survive_the_whole_chain()
    test_line_name_detection_and_normalization()
    print("\n按线路名查站序/里程（F06+F05）回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
