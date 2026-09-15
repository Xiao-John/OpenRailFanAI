"""车站大屏查询工具（12306 官方「车站车次大屏」，实时）。

回答"某车站今天有哪些车到发 / 几点发车 / 在几站台 / 由谁担当"这类问题。
数据源与探测细节见 `docs/12306-station-screen-api.md`。

与既有工具的分工：
- `ticket.query` 需要**两站区间**，回答"还有没有票"；
- `train.schedule` 需要**具体车次**，回答"这趟车几点开、经停哪"；
- `station.screen` 只需**一个车站**，回答"这个站今天都有哪些车到发"——
  这也是三者中唯一能一次给出**站台号 + 车底型号 + 担当客运段/车辆段**的接口。

设计要点（均为实测踩坑，勿随意简化）：
1. 必须 POST（GET 恒返回 M0003"系统忙"），见 `_rt12306.SCREEN_URL` 注释。
2. 空结果**不可直接当作"该站当日无车"**：超窗口/电报码非法/确实无车三者返回一致。
3. 一次返回全天 200–700 条，**必须裁剪后再下发**（本站按方向 + 时段 + 车种过滤，
   并把"命中多少/展示多少/是否截断"如实登记进 ToolResult 的数据完整性契约）。
"""
from __future__ import annotations

from app.config import get_settings
from app.dates import date_note, normalize_date
from app.tools import _rt12306 as rt
from app.tools._rt12306 import Realtime12306Error
from app.tools._http import format_error
from app.tools.base import Tool, ToolResult

# 与 ticket_query 同口径的时段划分，便于"下午/晚上"这类问法在明细被裁时仍可作答
_PERIODS = (
    ("凌晨(00-06)", 0, 360), ("上午(06-12)", 360, 720),
    ("下午(12-17)", 720, 1020), ("晚上(17-24)", 1020, 1440),
)

_DIRECTION_ALIASES = {
    "departure": "departure", "depart": "departure", "dep": "departure",
    "出发": "departure", "始发": "departure", "发车": "departure", "出发屏": "departure",
    "arrival": "arrival", "arrive": "arrival", "arr": "arrival",
    "到达": "arrival", "到站": "arrival", "终到": "arrival", "到达屏": "arrival",
}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _beijing_now():
    """北京时间当前时刻。

    12306 的时刻口径都是**北京时间**；账号/审计模块用 UTC —— 两者别混。
    这里用 UTC+8 固定偏移（中国不实行夏令时，无需时区库）。
    """
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8)))


def _to_minutes(hhmm: str | None) -> int | None:
    """'07:12' → 432（分钟）；缺失/非法返回 None。"""
    if not hhmm:
        return None
    try:
        h, mi = str(hhmm).split(":")
        return int(h) * 60 + int(mi)
    except (ValueError, AttributeError):
        return None


def _board_time(row: dict, direction: str) -> str | None:
    """该行在指定方向的**相关时刻**：出发屏看发车，到达屏看到达。"""
    return row.get("depart_time") if direction == "departure" else row.get("arrive_time")


def _split_boards(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """按方向切分 (出发屏, 到达屏)。过路车同时出现在两侧（实测口径）。"""
    dep = [r for r in rows if r.get("direction") in ("departure", "through")]
    arr = [r for r in rows if r.get("direction") in ("arrival", "through")]
    return dep, arr


# ---- 与"现在"对齐（2026-09-15）----
# 真实车站大屏是**围绕此刻**的：列表从最近的车次开始，而不是从 00:00 顺排。
# 我们此前总是从 00:00 起排，导致 15 条明细被凌晨车次占满（北京南全天 259 趟出发，
# 下午提问只会看到 00:15 那几趟）——数据没错，但没用。
def _now_minutes(date_str: str) -> int | None:
    """查询日期是"今天"时返回北京时间当前分钟数；其它日期返回 None（不对齐）。"""
    from datetime import date as _date

    try:
        target = _date.fromisoformat(str(date_str))
    except (TypeError, ValueError):
        return None
    now = _beijing_now()
    if target != now.date():
        return None
    return now.hour * 60 + now.minute


def _reorder_around_now(rows: list[dict], direction: str, now_min: int) -> list[dict]:
    """把明细重排成"大屏读法"：**未到/未发的按时间升序在前**，已过/已发的按时间降序在后。

    不丢数据：已过车次仍留在列表里（只是排在后面），总数与截断声明照旧如实登记。
    """
    upcoming: list[tuple[int, dict]] = []
    past: list[tuple[int, dict]] = []
    for r in rows:
        m = _to_minutes(_board_time(r, direction))
        if m is None:
            upcoming.append((24 * 60 + 1, r))          # 时刻未知的排最后
        elif m >= now_min:
            upcoming.append((m, r))
        else:
            past.append((m, r))
    upcoming.sort(key=lambda x: x[0])
    past.sort(key=lambda x: -x[0])
    return [r for _, r in upcoming] + [r for _, r in past]


def _parse_train_type(raw) -> tuple[str, ...]:
    return tuple(x.strip().upper() for x in str(raw or "").split(",") if x.strip())


def _apply_filters(rows: list[dict], *, direction: str, params: dict) -> tuple[list[dict], dict]:
    """时段 + 车种过滤（车次号过滤在调用侧做）。返回 (结果, 已应用条件说明)。"""
    out = rows
    filters: dict = {}

    after = _to_minutes(params.get("after_time"))
    before = _to_minutes(params.get("before_time"))
    if after is not None:
        filters["时刻≥"] = str(params.get("after_time"))
    if before is not None:
        filters["时刻<"] = str(params.get("before_time"))
    if after is not None or before is not None:
        kept = []
        for r in out:
            cur = _to_minutes(_board_time(r, direction))
            if cur is None:
                continue                      # 时刻未知的不参与时段筛选
            if after is not None and cur < after:
                continue
            if before is not None and cur >= before:
                continue
            kept.append(r)
        out = kept

    prefixes = _parse_train_type(params.get("train_type"))
    if prefixes:
        filters["车种"] = "/".join(prefixes)
        out = [r for r in out if str(r.get("train") or "").upper().startswith(prefixes)]

    out = sorted(out, key=lambda r: _to_minutes(_board_time(r, direction)) or 0)
    return out, filters


def _period_counts(rows: list[dict], direction: str) -> dict:
    """按时段分组计数：明细被裁时，模型仍能可靠回答"下午/晚上有几趟"。"""
    counts: dict = {}
    for name, lo, hi in _PERIODS:
        counts[name] = sum(
            1 for r in rows
            if (m := _to_minutes(_board_time(r, direction))) is not None and lo <= m < hi
        )
    return counts


# 交路链（`jiaolu_train`）实测可达 20–38 段（城际套跑），全量渲染会撑爆 prompt
# （单行可到 200+ 字，15 行就超过生成层的 fact_text_max_chars）。故只展示**首尾若干段**。
_ROUTE_SEG_LIMIT = 6


def _route_text(route: list[dict]) -> str:
    segs = [str(x.get("train") or "") for x in route if x.get("train")]
    if not segs:
        return ""
    if len(segs) <= _ROUTE_SEG_LIMIT:
        return "→".join(segs)
    head = "→".join(segs[: _ROUTE_SEG_LIMIT - 1])
    return f"{head}→…（共 {len(segs)} 段，只列前 {_ROUTE_SEG_LIMIT - 1} 段）"


def _line(row: dict, direction: str, station: str, *, with_route: bool,
          now_min: int | None = None) -> str:
    """一行明细：`G1 北京南→上海虹桥 06:30 [17A、17B] CR400BF-S 上海客运段`。"""
    other = row.get("to_station") if direction == "departure" else row.get("from_station")
    arrow = f"{station}→{other}" if direction == "departure" else f"{other}→{station}"
    time_ = _board_time(row, direction) or "--:--"
    plat = row.get("platform") or ""
    # 车底：主值（交路型号）+ 本车底型号（不一致时才显示）+ 定员。
    # 两者实测有约 45% 的行不一致（如 G1：交路 CR400BF-S / 本车底 CR400AF-A），
    # 都摆出来让模型如实呈现分歧，而不是静默取一个。
    stock = row.get("rolling_stock") or "车底未知"
    own = row.get("rolling_stock_own") or ""
    if own and own != row.get("rolling_stock"):
        stock = f"{stock}(本车底 {own})"
    cap = row.get("capacity")
    if cap:
        stock = f"{stock}·定员{cap}"
    parts = [
        f"{row.get('train','')} {arrow} {time_}",
        f"站台{plat}" if plat else "站台未知",
        stock,
        row.get("corporation") or "",
    ]
    if direction == "arrival" and row.get("direction") == "through" and row.get("depart_time"):
        # 过路车：到达屏上顺手给出本站发车时刻，避免模型误答"终到"。
        # ⚠️ 仅限过路车 —— 终到车的 start_time 只是 arrive_time 的镜像，不是真的发车时刻。
        parts.append(f"本站{row['depart_time']}开")
    if row.get("actual_depart") and direction == "departure":
        parts.append(f"预计{row['actual_depart']}开")
    if now_min is not None:
        # 与"现在"对齐时标明已过车次，避免模型把已发车的当成接下来要等的车
        cur = _to_minutes(_board_time(row, direction))
        if cur is not None and cur < now_min:
            parts.append("已发车" if direction == "departure" else "已到达")
    if with_route and row.get("route"):
        parts.append("交路:" + _route_text(row["route"]))
    return " | ".join(p for p in parts if p)


class StationScreenTool(Tool):
    name = "station.screen"
    description = (
        "查询某车站当日**全部到发车次**（12306 官方「车站车次大屏」）："
        "到发时刻、站台、终到站、车底型号、担当客运段/车辆段、当日套跑交路"
    )

    async def invoke(self, params: dict) -> ToolResult:
        raw_station = str(
            params.get("station") or params.get("name")
            or params.get("location") or params.get("from_station") or ""
        ).strip()
        if not raw_station:
            return ToolResult(
                ok=False,
                error="缺少参数：station（车站名或电报码）",
                note="例如：station=北京南, date=今天",
            )

        raw_date = params.get("date") or params.get("time")
        date_str = normalize_date(raw_date)
        date_warn = date_note(raw_date, date_str)

        direction = _DIRECTION_ALIASES.get(str(params.get("direction") or "").strip().lower())
        want_all = direction is None
        if want_all:
            direction = "departure"          # 统计两侧；展示顺序以出发屏为主

        try:
            station_limit = int(params.get("limit") or get_settings().station_screen_limit)
        except (TypeError, ValueError):
            station_limit = get_settings().station_screen_limit

        try:
            resolved = await rt.resolve_station_code(raw_station)
            if not resolved:
                return ToolResult(
                    ok=False,
                    error=f"无法识别车站：{raw_station}",
                    note="请给出规范站名（如 北京南）或 12306 电报码（如 VNP）",
                )
            code, station_name = resolved
            rows = await rt.query_station_screen(code, date_str)
        except Realtime12306Error as e:
            return ToolResult(
                ok=False,
                error=f"12306 车站大屏查询失败：{e}",
                note="需境内网络 + Python ≥ 3.10；可稍后重试",
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"12306 车站大屏查询异常：{format_error(e)}",
                note="需境内网络 + Python ≥ 3.10；可稍后重试",
            )

        # ---- 空结果：三义歧义，绝不表述成"该站当日无车" ----
        if not rows:
            hint = rt.screen_window_hint(date_str)
            return ToolResult(
                ok=False,
                error=(
                    f"{station_name}（{code}，{date_str}）车站大屏未返回任何到发记录。"
                    "12306 对本接口在「超出可查窗口」「电报码不存在」与「该站当日确实无车」"
                    "三种情况下都返回空数组，**无法区分**"
                ),
                note=(hint or "可确认站名/电报码是否正确，或改查临近日期"),
                sources=[rt.SCREEN_URL],
                total=0, shown=0,
                filters={"车站": station_name, "日期": date_str},
                fetched_at=_now_iso(),
            )

        dep, arr = _split_boards(rows)
        target = dep if want_all else (dep if direction == "departure" else arr)
        matched, applied = _apply_filters(target, direction=direction, params=params)

        counts_line = f"出发屏 {len(dep)} 趟；到达屏 {len(arr)} 趟"

        if not matched:
            return ToolResult(
                ok=False,
                error=(
                    f"{station_name}（{date_str}）{counts_line}，"
                    "但**没有符合筛选条件的车次**（"
                    + "、".join(f"{k}{v}" for k, v in applied.items()) + "）"
                ),
                note="数据本身存在，只是不满足本次筛选；可放宽时段/车种后重查",
                sources=[rt.SCREEN_URL],
                total=len(target), shown=0, filters=applied,
                fetched_at=_now_iso(),
            )

        # 未指定时段且查的是"今天" → 按当前时刻对齐（真实大屏就这么读）
        now_min: int | None = None
        aligned = False
        if not applied and params.get("align_now", True) is not False:
            now_min = _now_minutes(date_str)
            if now_min is not None:
                matched = _reorder_around_now(matched, direction, now_min)
                aligned = True

        shown = matched[:station_limit]
        truncated = len(matched) > len(shown)
        with_route = bool(params.get("include_route"))

        head = (
            f"{station_name}（{code}，{date_str}）车站大屏：{counts_line}"
            + (f"；按条件筛选后 {len(matched)} 趟" if applied else "")
            + f"，本次展示 {len(shown)} 趟"
            + ("（**已截断**）" if truncated else "")
        )
        periods = _period_counts(matched, direction)
        dist = "；".join(f"{k} {v} 趟" for k, v in periods.items() if v)
        board_name = "出发屏（本站发车）" if direction == "departure" else "到达屏（本站到达）"
        period_label = "发车时段分布" if direction == "departure" else "到达时段分布"

        now_hhmm = f"{now_min // 60:02d}:{now_min % 60:02d}" if now_min is not None else ""
        text = (
            head
            + (f"\n（已按**当前时刻 {now_hhmm}** 对齐：先列未{'发' if direction == 'departure' else '到'}的车次，"
               f"已{'发' if direction == 'departure' else '到'}的排在后面；"
               "如需全天清单请指定时段，如 after_time=00:00）" if aligned else "")
            + "\n"
            + f"{board_name}明细（车次｜方向｜时刻｜站台｜车底·定员｜客运段）：\n"
            + "\n".join(
                _line(r, direction, station_name, with_route=with_route,
                      now_min=now_min if aligned else None)
                for r in shown)
            + (f"\n{period_label}：{dist}" if dist else "")
        )
        if want_all:
            # 用户没指定方向时，补 3 条到达屏样例，避免"只说了出发、看起来像没有到达车"
            arr_sorted = sorted(arr, key=lambda r: _to_minutes(r.get("arrive_time")) or 0)
            text += (
                "\n到达屏样例（不完整，仅为方向示意）：\n"
                + "\n".join(_line(r, "arrival", station_name, with_route=False) for r in arr_sorted[:3])
            )

        snapshot = next((r.get("snapshot_at") for r in rows if r.get("snapshot_at")), "")
        return ToolResult(
            ok=True,
            data={
                "station": station_name,
                "station_code": code,
                "train_date": date_str,
                "direction": direction,
                "count_departure": len(dep),
                "count_arrival": len(arr),
                "count": len(matched),
                "rows": shown,               # 只下发展示过的明细，避免下游误当全集
                "rows_shown": len(shown),
                "period_counts": periods,
                "applied_filters": applied,
                "aligned_to_now": aligned,
                "now": now_hhmm or None,
            },
            text=text,
            sources=[rt.SCREEN_URL],
            note=(
                f"12306 车站大屏（{date_str}，数据快照 {snapshot or '未知'}）"
                + (f"；已按当前时刻 {now_hhmm}（北京时间）对齐列表顺序，时刻为**计划时刻**"
                   if aligned else "")
                + "；站台号与列车状态随现场变化，请以车站实时广播为准"
                + (f"；{date_warn}" if date_warn else "")
            ),
            total=len(matched), shown=len(shown), truncated=truncated,
            filters=applied, fetched_at=_now_iso(),
        )
