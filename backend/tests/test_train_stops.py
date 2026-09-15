"""车次经停站修复回归（2026-09-15，缺陷：模型看不到某车次经停哪些站）。

背景
----
用户实测「G1 经停哪些站」拿不到经停——工具只回一句"基础归属：北京南→上海"。
根因：起讫站来自 **2022 年停更的离线车次目录**（G1 被判成"上海"，实际是"上海虹桥"），
用这个 OD 查余票必然落空 → 整条链路降级；而且**降级后从不调用"按车次直查经停"的接口**。

修复
----
1. 新增权威车次身份：`search.12306.cn/search/v1/train/search?keyword=<车次>&date=YYYYMMDD`
   → 官方 `train_no` + 真实起讫站（实测 0.1s；date 必须是 YYYYMMDD，带横杠返回空）
2. 经停表改为按 `train_no` 直查 `czxx/queryByTrainNo`（12306 图定表）：
   与"该车次是否还在售票"无关，**已发车车次同样有完整经停**（实测 0.2s）
3. 红线 D06 不变：默认只给站名，不给任何时刻值；问经停/历时才附**图定**时刻表并显式标注

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_train_stops.py
"""
from __future__ import annotations

import asyncio

import app.tools.train_schedule as ts
from app.tools import _rt12306 as rt


# ---------- 1. 权威车次身份（离线目录判错时的纠正）----------

class _FakeRTWithSearch:
    """离线目录把 G1 判成「北京南→上海」（错的），search 给出正确终点「上海虹桥」。"""

    queried_od: list[tuple[str, str]] = []
    stops_calls: list[tuple[str, str]] = []

    @staticmethod
    def infer_endpoints_from_offline(code):
        return ("北京南", "上海")          # ← 2022 离线目录的错误答案

    @staticmethod
    def is_emu_train_code(code):
        return True

    @staticmethod
    async def resolve_station_code(name):
        return {"北京南": ("VNP", "北京南"), "上海": ("SHH", "上海"),
                "上海虹桥": ("AOH", "上海虹桥")}.get(name) or (None, None)

    @staticmethod
    async def resolve_train_identity(train_code, date_str):
        return {"train_no": "24000000G10L", "from_station": "北京南",
                "to_station": "上海虹桥", "from_code": "VNP", "to_code": "AOH"}

    @classmethod
    async def query_tickets(cls, from_code, to_code, date_str):
        cls.queried_od.append((from_code, to_code))
        if to_code == "AOH":              # 正确的 OD 才有 G1
            # 注意：余票接口的 train_no 字段实际是**车次号**（"G1"），与真实 MCP 行为一致；
            # 内部编号（24000000G10L）只有 search.12306.cn 才知道
            return [{"train_no": "G1", "from_station": "北京南",
                     "to_station": "上海虹桥", "start_time": "06:30",
                     "arrive_time": "11:24", "duration": "04:54", "seats": {}}]
        return []

    @classmethod
    async def query_stops_by_train_no(cls, train_no, from_code, to_code, date_str):
        cls.stops_calls.append((train_no, date_str))
        return [
            {"station_no": "01", "station_name": "北京南", "arrive_time": "----",
             "start_time": "06:30", "stopover_time": "----"},
            {"station_no": "02", "station_name": "沧州西", "arrive_time": "07:18",
             "start_time": "07:20", "stopover_time": "2分钟"},
            {"station_no": "03", "station_name": "南京南", "arrive_time": "10:13",
             "start_time": "10:15", "stopover_time": "2分钟"},
            {"station_no": "04", "station_name": "上海虹桥", "arrive_time": "11:24",
             "start_time": "11:24", "stopover_time": "----"},
        ]

    @staticmethod
    async def query_route_stations(train_no, from_code, to_code, date_str):
        return []


def test_search_identity_corrects_wrong_offline_endpoint():
    """离线目录给出错误终点时，必须用 12306 官方车次搜索纠正后再查。"""
    original = ts.rt
    _FakeRTWithSearch.queried_od = []
    _FakeRTWithSearch.stops_calls = []
    ts.rt = _FakeRTWithSearch  # type: ignore[assignment]
    try:
        res = asyncio.run(ts.TrainScheduleTool().invoke({"train": "G1", "date": "今天"}))
    finally:
        ts.rt = original  # type: ignore[assignment]

    assert res.ok, res.error
    assert ("VNP", "AOH") in _FakeRTWithSearch.queried_od, \
        f"未用权威起讫站查询（仍是离线目录的上海站）：{_FakeRTWithSearch.queried_od}"
    assert ("VNP", "SHH") not in _FakeRTWithSearch.queried_od, _FakeRTWithSearch.queried_od
    assert res.data.get("source") == "12306-realtime", res.data
    assert res.data.get("stop_count") == 4, res.data
    # 经停查询必须用**内部编号**（24000000G10L），而不是余票接口那个"车次号" G1
    assert _FakeRTWithSearch.stops_calls, "未按 train_no 直查经停"
    assert _FakeRTWithSearch.stops_calls[0][0] == "24000000G10L", _FakeRTWithSearch.stops_calls
    for name in ("沧州西", "南京南", "上海虹桥"):
        assert name in res.text, f"经停表缺少 {name}：{res.text[:300]}"
    print(f"[PASS] 权威身份纠正离线目录：OD 查询用 北京南→上海虹桥（{res.data['stop_count']} 站）")


def test_user_given_od_still_uses_authoritative_train_no_for_stops():
    """用户显式给了 OD 时，仍应以权威 train_no 查经停（不依赖 OD 旁路）。"""
    original = ts.rt
    _FakeRTWithSearch.stops_calls = []
    ts.rt = _FakeRTWithSearch  # type: ignore[assignment]
    try:
        res = asyncio.run(ts.TrainScheduleTool().invoke(
            {"train": "G1", "from": "北京南", "to": "上海虹桥", "date": "今天"}
        ))
    finally:
        ts.rt = original  # type: ignore[assignment]
    assert res.ok and res.data.get("stop_count") == 4, res.data
    assert _FakeRTWithSearch.stops_calls, "未按 train_no 直查经停"
    print("[PASS] 用户给定 OD 时仍按权威 train_no 取经停")


def test_identity_failure_falls_back_to_offline_catalog():
    """search 不可用时不得崩溃：退回离线目录/枢纽探测的旧路径。"""

    class _FakeNoSearch:
        @staticmethod
        def infer_endpoints_from_offline(code):
            return ("北京", "沈阳北")

        @staticmethod
        def is_emu_train_code(code):
            return False

        @staticmethod
        async def resolve_station_code(name):
            return ("BJP", "北京") if "北京" in name else ("SBT", "沈阳北")

        @staticmethod
        async def resolve_train_identity(train_code, date_str):
            return None                       # 搜索不可用

        @staticmethod
        async def query_tickets(from_code, to_code, date_str):
            return [{"train_no": "K53", "from_station": "北京", "to_station": "沈阳北",
                     "start_time": "22:35", "arrive_time": "06:59", "duration": "08:24",
                     "seats": {}}]

        @staticmethod
        async def query_route_stations(train_no, from_code, to_code, date_str):
            return [{"station_name": "北京"}, {"station_name": "山海关"},
                    {"station_name": "沈阳北"}]

    original = ts.rt
    ts.rt = _FakeNoSearch  # type: ignore[assignment]
    try:
        res = asyncio.run(ts.TrainScheduleTool().invoke({"train": "K53", "date": "今天"}))
    finally:
        ts.rt = original  # type: ignore[assignment]
    assert res.ok, res.error
    assert res.data.get("source") == "12306-realtime", res.data
    assert "山海关" in res.text, res.text[:300]
    assert "车次目录自动推断" in (res.note or ""), res.note
    print("[PASS] search 不可用 -> 退回离线目录推断，仍有经停（OD 旁路）")


# ---------- 2. 经停表渲染与截断 ----------

def test_stops_table_render_and_cap():
    from app.tools.train_schedule import _STOPS_MAX_RENDER, _format_stops

    stops = [
        {"station_no": f"{i:02d}", "station_name": f"站{i}", "arrive_time": "10:00",
         "start_time": "10:02", "stopover_time": "2分钟"}
        for i in range(1, _STOPS_MAX_RENDER + 6)
    ]
    text = _format_stops(stops, with_times=True)
    assert text.startswith("序\t站名\t到达\t发车\t停留"), text[:80]
    assert "站1\t10:00\t10:02\t2分钟" in text, text[:200]
    assert f"共 {len(stops)} 站" in text and "仅列出前" in text, text[-120:]
    # 不给时刻时只渲染站名两列，绝不出现时间
    plain = _format_stops(stops, with_times=False)
    assert "10:00" not in plain and "站1" in plain, plain[:120]
    print(f"[PASS] 经停表渲染：{_STOPS_MAX_RENDER}+5 站触发截断声明；无时刻模式不出现时间值")


# ---------- 3. 真实 12306 联调（网络不可达时跳过）----------

def test_live_12306_identity_and_stops():
    """真实链路：search 取 train_no → queryByTrainNo 取经停（G1 应为 7 站）。"""
    import datetime as _dt

    today = _dt.date.today().isoformat()

    async def run():
        ident = await rt.resolve_train_identity("G1", today)   # 与生产同一条路径
        if not ident:
            return None
        stops = await rt.query_stops_by_train_no(
            ident["train_no"], ident["from_code"], ident["to_code"], today
        )
        return ident["train_no"], ident["from_station"], ident["to_station"], stops, ident["from_code"], ident["to_code"]

    try:
        out = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] 12306 不可达（{type(e).__name__}: {e}）——本项需要境内网络")
        return
    if not out:
        print("[SKIP] 12306 车次搜索未返回结果（网络受限或接口调整）")
        return
    train_no, from_name, to_name, stops, from_code, to_code = out
    assert from_code and to_code, f"车站码解析失败：{from_code}/{to_code}"
    assert train_no.startswith("24000000"), f"train_no 形态异常：{train_no}"
    assert from_name and to_name, (from_name, to_name)
    names = [s.get("station_name") for s in stops]
    assert len(stops) >= 5, f"经停站过少：{names}"
    for must in (from_name, to_name):
        assert must in names, f"经停表未包含 {must}：{names}"
    assert names[0] == from_name and names[-1] == to_name, names
    times = [s.get("start_time") for s in stops]
    assert any(t and t != "----" for t in times), times
    print(f"[PASS] 真实 12306：G1({train_no}) {from_name}→{to_name} 经停 {len(stops)} 站："
          f"{' → '.join(names)}")


def main():
    test_search_identity_corrects_wrong_offline_endpoint()
    test_user_given_od_still_uses_authoritative_train_no_for_stops()
    test_identity_failure_falls_back_to_offline_catalog()
    test_stops_table_render_and_cap()
    test_live_12306_identity_and_stops()
    print("\n车次经停站修复回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
