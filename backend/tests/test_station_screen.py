"""车站大屏工具（station.screen）验证。

运行：cd backend && PYTHONPATH=. ./.venv/bin/python tests/test_station_screen.py

分两层：
- 离线层：用合成行（含始发/终到/过路三类）验证归一化、方向切分、过滤、截断契约与
  "空结果三义歧义"处理 —— 不依赖网络，任何环境都必须全绿；
- 联调层：打真实 12306 大屏接口，验证 POST 必需与字段口径；网络不可达时跳过并如实登记。
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.tools import _rt12306 as rt
from app.tools import registry
from app.tools.station_screen import StationScreenTool

_skips: list[str] = []


# ---------- 合成数据（字段名与 12306 原始响应一致）----------

def _raw_departure(code: str, train: str = "G1", start: str = "06:30",
                   platform: str = "17A、17B#") -> dict:
    """本站始发：arrive_time 为 '----'。"""
    return {
        "station_train_code": train, "train_no": "24000000G10L",
        "start_station_name": "北京南", "start_station_telecode": code,
        "end_station_name": "上海虹桥", "end_station_telecode": "AOH",
        "arrive_time": "----", "start_time": start,
        "update_arrive_time": "----", "update_start_time": start.replace(":", ""),
        "platform_no": platform, "station_no": "01",
        "train_class_name": "高速", "train_type_name": "直通",
        "jiaolu_train_style": "CR400BF-S", "jiaolu_corporation_code": "上海客运段",
        "jiaolu_dept_train": "上海机辆段", "bureau_code": "H",
        "jiaolu_train": "G1|北京南|06:30|上海虹桥|11:24#G1956/7|上海虹桥|11:44|太原南|21:26#",
        "running_time": "04小时54分", "distance": "1318", "stopover_time": "0",
        "station_train_date": "20260915", "base_datetime": "2026-09-14 21:53:39.283",
    }


def _raw_arrival(code: str, train: str = "D10", arrive: str = "09:24") -> dict:
    """本站终到：start_time 是 arrive_time 的镜像（**不是**发车时刻）。"""
    return {
        "station_train_code": train, "train_no": "5500000D1010",
        "start_station_name": "上海南", "start_station_telecode": "SNH",
        "end_station_name": "北京南", "end_station_telecode": code,
        "arrive_time": arrive, "start_time": arrive,
        "update_arrive_time": arrive.replace(":", ""), "update_start_time": "----",
        "platform_no": "", "station_no": "04",
        "train_class_name": "动车", "train_type_name": "直通",
        "jiaolu_train_style": "CR200J1", "jiaolu_corporation_code": "上海客运段",
        "jiaolu_dept_train": "", "bureau_code": "H", "jiaolu_train": "",
        "running_time": "12小时19分", "distance": "1468", "stopover_time": "0",
        "station_train_date": "20260915", "base_datetime": "2026-09-14 21:53:39.283",
    }


def _raw_through(code: str, train: str = "G1234", arrive: str = "09:00",
                 depart: str = "09:05") -> dict:
    """过路车：到达与发车时刻都真实存在。"""
    row = _raw_departure(code, train=train, start=depart)
    row.update({
        "start_station_name": "济南西", "start_station_telecode": "JGK",
        "end_station_name": "南京南", "end_station_telecode": "NKH",
        "arrive_time": arrive, "update_arrive_time": arrive.replace(":", ""),
    })
    return row


def _normalized(code: str, raws: list[dict]) -> list[dict]:
    return [rt.normalize_screen_row(r, code) for r in raws]


_REAL_RESOLVE = rt.resolve_station_code
_REAL_SCREEN = rt.query_station_screen


def _restore() -> None:
    """还原被 _install 替换掉的外部入口（否则会污染后续用例）。"""
    rt.resolve_station_code = _REAL_RESOLVE          # type: ignore[assignment]
    rt.query_station_screen = _REAL_SCREEN           # type: ignore[assignment]


def _install(monkeypatch_code: str, raws: list[dict]) -> None:
    """把工具依赖的两个外部入口替换成离线实现。"""
    async def _resolve(name: str):
        return (monkeypatch_code, "北京南")

    async def _screen(station_code: str, train_date: str):
        return _normalized(station_code, raws)

    rt.resolve_station_code = _resolve          # type: ignore[assignment]
    rt.query_station_screen = _screen           # type: ignore[assignment]


# ---------- 离线层 ----------

async def test_normalize():
    code = "VNP"
    dep = rt.normalize_screen_row(_raw_departure(code), code)
    arr = rt.normalize_screen_row(_raw_arrival(code), code)
    thru = rt.normalize_screen_row(_raw_through(code), code)

    assert dep["direction"] == "departure", dep
    assert dep["arrive_time"] is None, "始发站的 arrive_time 是 '----'，必须归一化为 None"
    assert dep["depart_time"] == "06:30", dep
    assert dep["platform"] == "17A、17B", f"站台尾部 '#' 未剥离：{dep['platform']}"
    assert dep["actual_depart"] == "06:30", "紧凑写法 0630 应转成 06:30"
    assert dep["actual_arrive"] is None, dep
    assert [s["train"] for s in dep["route"]] == ["G1", "G1956/7"], dep["route"]
    assert dep["route"][1]["to_station"] == "太原南", dep["route"]

    assert arr["direction"] == "arrival", arr
    assert arr["arrive_time"] == "09:24" and arr["depart_time"] == "09:24"
    assert arr["platform"] == "", arr

    assert thru["direction"] == "through", thru
    assert (thru["arrive_time"], thru["depart_time"]) == ("09:00", "09:05"), thru
    print("[PASS] normalize_screen_row：方向判定 / '----'→None / 站台去 '#' / 紧凑时刻 / 交路解析")


async def test_split_and_render():
    code = "VNP"
    _install(code, [_raw_departure(code), _raw_arrival(code), _raw_through(code)])
    r = await StationScreenTool().invoke({"station": "北京南", "date": "2026-09-15"})
    assert r.ok, r.error
    assert r.data["count_departure"] == 2, r.data      # 始发 + 过路
    assert r.data["count_arrival"] == 2, r.data        # 终到 + 过路
    assert "出发屏（本站发车）明细" in r.text, r.text

    # 终到车不得出现"本站…开"（start_time 是镜像，不是发车）
    arr_line = [ln for ln in r.text.splitlines() if ln.startswith("D10")][0]
    assert "本站" not in arr_line, f"终到车被误标为本站发车：{arr_line}"
    # 过路车在到达屏样例里应给出本站发车时刻
    assert "本站09:05开" in r.text, r.text
    # 站台与担当信息必须出现在明细里
    assert "站台17A、17B" in r.text and "上海客运段" in r.text, r.text
    print("[PASS] 方向切分：出发屏=始发+过路，到达屏=终到+过路；终到车不发'本站开'")


async def test_filters_and_truncation():
    code = "VNP"
    raws = [_raw_departure(code, train="G1", start="06:30"),
            _raw_departure(code, train="G25", start="17:00"),
            _raw_departure(code, train="D701", start="19:30"),
            _raw_departure(code, train="K1", start="20:00")]
    _install(code, raws)
    r = await StationScreenTool().invoke({
        "station": "北京南", "direction": "出发",
        "after_time": "17:00", "train_type": "G,D", "limit": 1,
    })
    assert r.ok, r.error
    # 时段过滤后剩 3 趟，再按车种 G/D 过滤剩 2 趟（K1 被排除）
    assert r.total == 2 and r.shown == 1 and r.truncated is True, (r.total, r.shown, r.truncated)
    assert r.filters == {"时刻≥": "17:00", "车种": "G/D"}, r.filters
    assert "G1" not in r.text, "06:30 的车被时段过滤漏掉"
    assert r.integrity_line().startswith("命中 2 条"), r.integrity_line()

    # 过滤后有零条 ≠ 该站没车：必须区分"筛选为空"与"接口为空"
    r2 = await StationScreenTool().invoke({"station": "北京南", "after_time": "23:00"})
    assert not r2.ok and "没有符合筛选条件" in r2.error, r2.error
    assert r2.total == 4, r2.total
    print("[PASS] 过滤与截断契约：total/shown/truncated/filters 如实登记，空筛选与空接口不混淆")


async def test_empty_is_ambiguous():
    code = "VNP"
    _install(code, [])
    # ① 窗口内空结果：不得断言"该站当日无车"
    r = await StationScreenTool().invoke({"station": "北京南", "date": date.today().isoformat()})
    assert not r.ok, "空结果不应报 ok=True（会被生成层当成'没有车'）"
    assert "无法区分" in r.error, r.error
    # ② 窗口外空结果：note 必须点明日期超出可查范围
    far = (date.today() + timedelta(days=109)).isoformat()
    r2 = await StationScreenTool().invoke({"station": "北京南", "date": far})
    assert not r2.ok and "超出车站大屏实测可查范围" in (r2.note or ""), r2.note
    print("[PASS] 空结果三义歧义：不谎报'该站无车'，窗口外给出具体原因")


async def test_window_hint_boundary():
    today = date.today()
    assert rt.screen_window_hint(today.isoformat()) == "", "窗口内不该有提示"
    assert rt.screen_window_hint((today + timedelta(days=rt.SCREEN_WINDOW_DAYS)).isoformat()) == ""
    hint = rt.screen_window_hint((today + timedelta(days=rt.SCREEN_WINDOW_DAYS + 1)).isoformat())
    assert "超出车站大屏实测可查范围" in hint, hint
    assert rt.screen_window_hint("今天") == "", "相对日期表述（normalize_date 可解析）不该误报"
    print("[PASS] screen_window_hint 边界（±7 天）")


async def test_missing_params():
    """纯离线：缺参必须优雅失败（不发起任何外部请求）。"""
    r = await StationScreenTool().invoke({})
    assert not r.ok and "缺少参数" in r.error, r.error
    assert "station=北京南" in (r.note or ""), r.note
    print("[PASS] 缺参优雅失败，并给出用法示例")


# ---------- 联调层（需境内网络）----------

async def test_live_get_vs_post():
    """回归：GET 恒失败、POST 成功 —— 这是本接口最大的坑，必须锁死。"""
    import httpx

    _restore()

    params = {"train_start_date": date.today().strftime("%Y%m%d"), "train_station_code": "VNP"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            get_resp = await client.get(rt.SCREEN_URL, params=params)
            post_resp = await client.post(rt.SCREEN_URL, data=params)
    except Exception as e:  # noqa: BLE001
        _skips.append(f"station.screen 联调（{type(e).__name__}）")
        print(f"[SKIP] 12306 大屏接口不可达：{type(e).__name__}")
        return

    assert post_resp.status_code == 200, post_resp.status_code
    assert post_resp.json().get("status") is True, post_resp.text[:200]
    get_payload = get_resp.json()
    assert get_payload.get("status") is False, (
        "GET 竟然成功了：12306 可能改了鉴权/方法，请更新 docs/12306-station-screen-api.md"
    )
    print(f"[PASS] 联调：POST 成功 / GET 如预期失败（{get_payload.get('errorMsg')}）")


async def test_live_screen():
    _restore()
    code, name = "VNP", "北京南"

    # 非车站名必须优雅失败并说明原因（不能把它当成站名去查大屏）
    bad = await registry.invoke_by_name("station.screen", {"station": "阿斯加德"})
    assert not bad.ok and bad.error, bad
    print(f"[PASS] 非车站名优雅失败：{bad.error[:50]}")
    try:
        rows = await rt.query_station_screen(code, date.today().isoformat())
    except rt.Realtime12306Error as e:
        _skips.append(f"station.screen 实时取数（{e}）")
        print(f"[SKIP] 实时取数失败：{e}")
        return
    if not rows:
        _skips.append("station.screen 实时取数（返回空数组，可能非运营日）")
        print("[SKIP] 实时取数为空（空结果本身无法区分原因）")
        return

    dep = [r for r in rows if r["direction"] == "departure"]
    arr = [r for r in rows if r["direction"] == "arrival"]
    assert dep and arr, (len(dep), len(arr))
    dep_with_plat = [r for r in dep if r["platform"]]
    assert dep_with_plat, "出发车次应有站台号（platform_no 非空 = 本站有发车作业）"
    assert all(r["train"] for r in rows), "车次号不应为空"
    r = await StationScreenTool().invoke({"station": name, "direction": "出发", "limit": 5})
    assert r.ok and r.shown == 5, (r.error, r.shown)
    print(f"[PASS] 联调：{name} 当日 {len(rows)} 条（出发 {len(dep)} / 到达 {len(arr)}，"
          f"有站台 {len(dep_with_plat)}）；工具输出前 5 条")


# ---------- 车底字段语义：`train_style` 的后缀是【定员】而不是【车组号】----------
# 背景：`CR400BF-BS_1346` 乍看像"型号_车组号"，实测 119/121 行的后缀与 `train_limit`
# 完全相等（1346 就是定员），只有 `CR200J_16`（定员 918）例外 —— 那是编组辆数。
# 该字段若被当成车组号展示，会与 rail.re 的车组号（如 CR400BFA-5159）混淆，
# 因此**必须拆开**，并保留本车底型号以便交叉核对。

def test_train_style_suffix_is_capacity_not_set_number():
    model, cap = rt.split_train_style("CR400BF-BS_1346", "1346")
    assert (model, cap) == ("CR400BF-BS", 1346), (model, cap)

    # 后缀与定员不一致 → 不是定员，保留原文（否则信息丢失）
    model2, cap2 = rt.split_train_style("CR200J_16", "918")
    assert model2 == "CR200J_16" and cap2 == 918, (model2, cap2)

    # 无后缀
    assert rt.split_train_style("CR400AF-S", "576") == ("CR400AF-S", 576)
    assert rt.split_train_style("", None) == ("", None)

    row = rt.normalize_screen_row({
        "station_train_code": "G3", "start_station_telecode": "VNP",
        "end_station_telecode": "SHH", "start_station_name": "北京南",
        "end_station_name": "上海", "arrive_time": "----", "start_time": "06:52",
        "train_style": "CR400BF-BS_1346", "train_limit": "1346",
        "jiaolu_train_style": "CR400BF-BS",
    }, "VNP")
    assert row["rolling_stock"] == "CR400BF-BS", row          # 型号里不含定员
    assert row["capacity"] == 1346, row                        # 定员单列
    assert "_" not in row["rolling_stock"], row                # 绝不把定员当组号传出
    print("[PASS] train_style 后缀识别为定员（CR400BF-BS_1346 → 型号+定员 1346）")


def test_rolling_stock_conflict_is_surfaced_not_hidden():
    """交路型号与本车底型号不一致时，两个值都要保留（实测约 45% 的行不一致）。"""
    row = rt.normalize_screen_row({
        "station_train_code": "G1", "start_station_telecode": "VNP",
        "end_station_telecode": "AOH", "start_station_name": "北京南",
        "end_station_name": "上海虹桥", "arrive_time": "----", "start_time": "06:30",
        "train_style": "CR400AF-A", "train_limit": "1193",
        "jiaolu_train_style": "CR400BF-S",
    }, "VNP")
    assert row["rolling_stock"] == "CR400BF-S", row
    assert row["rolling_stock_own"] == "CR400AF-A", "本车底型号被丢掉了，无法交叉核对"
    from app.tools.station_screen import _line
    line = _line(row, "departure", "北京南", with_route=False)
    assert "CR400BF-S" in line and "本车底 CR400AF-A" in line and "定员1193" in line, line
    print(f"[PASS] 车底分歧同时呈现：{line}")


def test_long_jiaolu_is_capped():
    """交路链实测可达 38 段：必须截断并声明段数，否则单行就能撑爆 prompt 预算。"""
    from app.tools.station_screen import _ROUTE_SEG_LIMIT, _route_text

    route = [{"train": f"C{i}"} for i in range(1, 39)]
    text = _route_text(route)
    assert text.count("→") <= _ROUTE_SEG_LIMIT + 1, text
    assert "共 38 段" in text and "只列前" in text, text
    assert len(text) < 120, f"截断后仍过长（{len(text)}）：{text}"

    short = _route_text([{"train": "G1"}, {"train": "G1956/7"}])
    assert short == "G1→G1956/7", short
    assert _route_text([]) == ""
    print(f"[PASS] 交路链截断：38 段 -> {text}")


# ---------- 与"现在"对齐（2026-09-15）----------
# 背景：真实车站大屏是围绕此刻的。此前我们从 00:00 顺排，北京南全天 256 趟出发，
# 下午提问只能看到凌晨车次——数据没错但没用（实测：16:46 提问，首条应是 16:47 的车）。

def test_now_alignment_reorders_without_losing_data():
    from app.tools.station_screen import _reorder_around_now

    rows = [{"train": t, "depart_time": h, "arrive_time": h, "direction": "departure"}
            for t, h in [("A", "06:00"), ("B", "16:40"), ("C", "16:47"),
                         ("D", "17:30"), ("E", "09:00")]]
    out = _reorder_around_now(rows, "departure", 16 * 60 + 46)
    order = [r["train"] for r in out]
    assert order == ["C", "D", "B", "E", "A"], order        # 未发车升序在前，已发车降序在后
    assert {r["train"] for r in out} == {"A", "B", "C", "D", "E"}, "重排不得丢数据"
    print(f"[PASS] 当前时刻对齐：16:46 时列表为 {order}（未发车在前、已发车在后，数据不丢）")


def test_now_alignment_edges():
    from app.tools.station_screen import _line, _now_minutes, _reorder_around_now

    # 时刻未知的排最后
    rows = [{"train": "X", "depart_time": None, "arrive_time": None},
            {"train": "Y", "depart_time": "23:00", "arrive_time": "23:00"}]
    assert [r["train"] for r in _reorder_around_now(rows, "departure", 600)] == ["Y", "X"]

    # 非今天 → 不对齐
    assert _now_minutes("2020-01-01") is None
    assert _now_minutes("") is None

    # 已过车次必须被标记（否则模型会把已发车当成"接下来要等的车"）
    line = _line({"train": "A", "depart_time": "06:00", "arrive_time": "06:00",
                  "direction": "departure"}, "departure", "北京南",
                 with_route=False, now_min=16 * 60 + 46)
    assert "已发车" in line, line
    line2 = _line({"train": "C", "depart_time": "16:47", "arrive_time": "16:47",
                   "direction": "departure"}, "departure", "北京南",
                  with_route=False, now_min=16 * 60 + 46)
    assert "已发车" not in line2, line2
    print("[PASS] 对齐边界：时刻未知排最后、非今天不对齐、已过车次标注「已发车」")


async def main():
    await test_normalize()
    test_train_style_suffix_is_capacity_not_set_number()
    test_rolling_stock_conflict_is_surfaced_not_hidden()
    test_long_jiaolu_is_capped()
    test_now_alignment_reorders_without_losing_data()
    test_now_alignment_edges()
    await test_split_and_render()
    await test_filters_and_truncation()
    await test_empty_is_ambiguous()
    await test_window_hint_boundary()
    await test_missing_params()
    await test_live_get_vs_post()
    await test_live_screen()
    if _skips:
        print(f"\n[WARN] 本次有 {len(_skips)} 项因外部依赖不可达被跳过：")
        for s in _skips:
            print(f"       · {s}")
        print("       （这些项验证的是实时链路，跳过不代表通过）")
    print("\n车站大屏测试全部通过 ✔")


if __name__ == "__main__":
    asyncio.run(main())
