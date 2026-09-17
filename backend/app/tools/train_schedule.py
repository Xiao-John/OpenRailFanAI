"""列车时刻查询工具（实时，基于 12306）。

查询策略（逐级降级）：
1. **权威车次身份**：`search.12306.cn` 用车次号取官方 `train_no` + 起讫站（2026-09-15 增补）
2. 有 from/to（用户给定，或用上一步的权威起讫站）→ 实时查询余票/时刻
3. 仅有车次且搜索不可用 → 离线车次目录推断起止站（2022 数据，仅兜底）
4. 推断失败/不可达  → 返回离线车次归属，并明确标注非实时

**经停站**另行由 `train_no` 直查 12306 图定表（`czxx/queryByTrainNo`）：
该接口与"车次是否还在售票"无关，因此**已发车车次一样能给出完整经停**。
红线（D06）不变：**"此刻几点到/开"这类时刻问题，默认不下发任何时刻值**——
站名可以给，时刻只在用户问经停/历时站序（`include_reference`）时下发，
且必须带"图定时刻、非实际运行时刻"的显式标注。
"""
from __future__ import annotations

import logging

from app.dates import date_note, normalize_date
from app.tools import _rt12306 as rt
from app.tools._rt12306 import Realtime12306Error
from app.tools._http import format_error
from app.tools.base import Tool, ToolResult

_log = logging.getLogger("railfan.tools.schedule")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# 经停表最多渲染多少站（K507 实测 27 站、D2284 30 站；给足但不无限）
_STOPS_MAX_RENDER = 60


def _seat_summary(seats: dict) -> str:
    if not seats:
        return "（无余票信息）"
    parts = [f"{k}={v}" for k, v in seats.items() if v and v not in ("无", "--")]
    return " ".join(parts) or "（无余票信息）"


def _stop_name(s: dict) -> str:
    return str(s.get("station_name") or s.get("station") or "").strip()


def _stop_names(stops: list[dict] | None) -> list[str]:
    return [n for n in (_stop_name(s) for s in (stops or [])) if n]


def _format_stops(stops: list[dict] | None, *, with_times: bool) -> str:
    """经停表渲染：`序 站名 到达/发车 停留`。

    表头列与 12306 站点大屏口径一致（`----` 表示该站无此时刻，如始发站无到达时刻）。
    """
    if not stops:
        return ""
    lines = ["序\t站名\t到达\t发车\t停留"]
    for s in stops[:_STOPS_MAX_RENDER]:
        row = [
            str(s.get("station_no") or ""),
            _stop_name(s) or "?",
        ]
        if with_times:
            row += [
                str(s.get("arrive_time") or "----"),
                str(s.get("start_time") or "----"),
                str(s.get("stopover_time") or "----"),
            ]
        lines.append("\t".join(row))
    if len(stops) > _STOPS_MAX_RENDER:
        lines.append(f"（共 {len(stops)} 站，仅列出前 {_STOPS_MAX_RENDER} 站）")
    return "\n".join(lines)


def _format_train(t: dict, routes: list[dict] | None = None) -> str:
    text = (
        f"车次 {t.get('train_no', '')}：{t.get('from_station', '')}→{t.get('to_station', '')}，"
        f"发车 {t.get('start_time', '')}，到达 {t.get('arrive_time', '')}，"
        f"历时 {t.get('duration', '')}。余票：{_seat_summary(t.get('seats', {}))}。"
    )
    if routes:
        text += "\n经停站：" + " → ".join(
            f"{_stop_name(s)}({s.get('arrive_time', '')})" for s in routes[:_STOPS_MAX_RENDER]
        )
    return text


class TrainScheduleTool(Tool):
    name = "train.schedule"
    description = "查询列车实时时刻/余票/经停站（12306，支持仅给车次自动定位起止站）"


    async def _train_identity(self, train_code: str, date_str: str) -> dict | None:
        """取权威车次身份（train_no + 起讫站）。

        在**检索层注入的 rt 模块**上取（测试会替换 `ts.rt` 为假模块），
        因此这里用 getattr 容错：假模块未实现该函数时静默退回旧路径。
        """
        fn = getattr(rt, "resolve_train_identity", None)
        if fn is None:
            return None
        try:
            return await fn(train_code, date_str)
        except Exception:  # noqa: BLE001 —— 增强路径失败不得影响主流程
            return None

    async def _fetch_stops(
        self,
        train_no: str,
        from_code: str,
        to_code: str,
        date_str: str,
        *,
        from_name: str = "",
        to_name: str = "",
        fallback_route: bool = True,
    ) -> list[dict]:
        """取全经停表：优先按权威 train_no 直查（已发车也有数据），失败再退回 OD 旁路。

        `czxx/queryByTrainNo` 的 from/to 参数只用于定位（其站序表与区间无关），
        但 MCP 侧 schema 要求非空 → 车站码解析失败时退回站名（MCP 支持三字码/全名）。
        """
        stops: list[dict] = []
        src_a = from_code or from_name
        src_b = to_code or to_name
        if train_no:
            fn = getattr(rt, "query_stops_by_train_no", None)
            if fn is not None:
                try:
                    stops = await fn(train_no, src_a, src_b, date_str) or []
                except Exception:  # noqa: BLE001
                    stops = []
        if not stops and fallback_route:
            try:
                stops = await rt.query_route_stations(
                    train_no, src_a, src_b, date_str
                ) or []
            except Exception:  # noqa: BLE001
                stops = []
        return stops


    async def _find_same_train_code(
        self,
        train_code: str,
        ident_train_no: str,
        from_code: str,
        to_code: str,
        date_str: str,
    ) -> str:
        """同一次车的**别名车次号**（内部编号相同、车次号不同）；找不到返回 ""。

        背景（用户报障）：12306 的内部编号 `train_no` 是**车底/交路**级别的键。同一次车在
        交路不同分段、上行/下行会挂不同车次号（G2365↔G2368、D2238↔D2235、G1486↔G1487、
        D6565↔D6564、K1117↔K1116、K896↔K897、Z184↔Z181 —— 实测内部编号两两完全相同），
        而 12306 的余票列表**只列当日实际开行的那个号**。

        用户记住的是其中一个号，于是问 A 号查不到、问 B 号却查得到，实际是同一次车。
        不认别名就会把"同一次车的另一个号"误答成"该日数据不可得/车次不存在"。

        用内部编号判定，而不是靠"站名相同"或"时刻接近"猜：同一区间一天几十趟车，
        只有内部编号相同才是同一次车。
        """
        code = (train_code or "").strip().upper()
        if not ident_train_no or not code:
            return ""
        fn = getattr(rt, "query_ticket_rows", None)
        if fn is None:
            return ""
        try:
            rows = await fn(from_code, to_code, date_str) or []
        except Exception:  # noqa: BLE001 —— 增强路径失败不得影响主流程
            return ""
        for row in rows:
            if str(row.get("train_no") or "").strip() != ident_train_no:
                continue
            alias = str(row.get("train_code") or "").strip().upper()
            if alias and alias != code:
                return alias
        return ""

    async def _probe_endpoints(self, train_code: str, date_str: str) -> tuple[str, str] | None:
        """离线目录未命中时，用枢纽区间实时查询"探"出该车次的起止站。

        背景（R1 P1-6 / D04）：离线车次目录源自 2022 停更的 train_list.js，
        实测缺 G101 等车次，导致"只给车次号"这条路径直接不可用。
        这里只在**该路径**上、最多探测 `HUB_PROBE_PAIRS` 个枢纽区间（每区间一次实时查询）。
        """
        from app.config import get_settings

        pairs = [x.strip() for x in (get_settings().hub_probe_pairs or "").split(",") if x.strip()]
        for pair in pairs[:4]:
            if ":" not in pair:
                continue
            a, b = (x.strip() for x in pair.split(":", 1))
            try:
                ra = await rt.resolve_station_code(a)
                rb = await rt.resolve_station_code(b)
                if not ra or not rb:
                    continue
                trains = await rt.query_tickets(ra[0], rb[0], date_str)
            except Exception:  # noqa: BLE001 —— 探测失败不影响主流程
                continue
            for t in trains:
                if str(t.get("train_no", "")).upper() == train_code:
                    return (t.get("from_station") or a, t.get("to_station") or b)
        return None

    async def invoke(self, params: dict) -> ToolResult:
        train_code = (params.get("train") or params.get("target") or "").strip().upper()
        if not train_code:
            return ToolResult(ok=False, error="缺少参数 train（车次）")

        from_st = (params.get("from") or params.get("location") or "").strip()
        to_st = (params.get("to") or params.get("direction") or "").strip()
        raw_date = params.get("date") or params.get("time")
        date_str = normalize_date(raw_date)
        # 用户给了时间表述但无法识别 → 如实说明已按今天查询
        date_warn = date_note(raw_date, date_str)
        # 是否允许下发"次日参考时刻"（仅当用户在问经停/历时站序时；由检索层按原话判断）
        include_reference = bool(params.get("include_reference"))
        # 同车不同号（别名命中时记录）：用户问的号 vs 12306 当日实际开行的号
        same_train_from = ""
        same_train_no = ""
        # 别名存在、但同一次车当日**也已发车**（余票列表里两个号都没有）→ 仅用于说明
        departed_alias = ""

        # ---- 1) 权威车次身份（search.12306.cn）：train_no + 真实起讫站 ----
        # 这一步替代"用 2022 离线目录猜起讫站"：G1 的正确终点是【上海虹桥】，
        # 离线目录说是【上海】，旧实现据此查余票必然落空 → 整条链路降级、经停全丢。
        identity = await self._train_identity(train_code, date_str)
        ident_train_no = (identity or {}).get("train_no") or ""

        # ---- 2) 缺起止站时，优先用权威起讫站；不可用再退离线目录/枢纽探测 ----
        inferred = False
        probed = False
        if not from_st or not to_st:
            if identity and identity.get("from_station") and identity.get("to_station"):
                from_st = from_st or identity["from_station"]
                to_st = to_st or identity["to_station"]
                inferred = True
            else:
                guess = rt.infer_endpoints_from_offline(train_code)
                if guess:
                    from_st, to_st = guess
                    inferred = True
                else:
                    # 目录未命中（2022 停更数据）→ 用枢纽区间实时"探"出起止站（R1 D04）
                    probed_guess = await self._probe_endpoints(train_code, date_str)
                    if probed_guess:
                        from_st, to_st = probed_guess
                        probed = True

        # ---- 3) 问经停/历时/站序时，先按权威 train_no 直查图定表 ----
        # 这一步与余票接口**完全独立**：12306 对余票接口限流/反爬时，经停照样拿得到。
        pre_stops: list[dict] = []
        if include_reference and ident_train_no:
            pre_stops = await self._fetch_stops(
                ident_train_no, "", "", date_str,
                from_name=(identity or {}).get("from_station", ""),
                to_name=(identity or {}).get("to_station", ""),
            )

        # ---- 实时查询 ----
        if from_st and to_st:
            try:
                from_res = await rt.resolve_station_code(from_st)
                to_res = await rt.resolve_station_code(to_st)
                if not from_res or not to_res:
                    raise Realtime12306Error(
                        f"无法识别站点：{from_st if not from_res else to_st}"
                    )
                from_code, from_name = from_res
                to_code, to_name = to_res

                trains = await rt.query_tickets(from_code, to_code, date_str)
                matched = [
                    t for t in trains
                    if str(t.get("train_no", "")).upper() == train_code
                ]

                if not matched:
                    listed = [
                        f"{t.get('train_no','')} {t.get('start_time','')}-{t.get('arrive_time','')}"
                        for t in trains[:15]
                    ]
                    # ---- 同车不同号（用户报障）：先认"同一次车的另一个车次号" ----
                    # 12306 余票列表只列当日实际开行的车次号；同一次车在交路不同分段会换号，
                    # 只按用户给的字面号匹配，会把"同一次车"误报成"该日查不到"。
                    alias_code = await self._find_same_train_code(
                        train_code, ident_train_no, from_code, to_code, date_str
                    )
                    if alias_code:
                        matched = [
                            t for t in trains
                            if str(t.get("train_no", "")).upper() == alias_code
                        ]
                        if matched:
                            same_train_from = train_code
                            same_train_no = ident_train_no
                            train_code = alias_code
                        else:
                            # 别名也查不到（同一次车当日已发车）→ 记下来，让答案点明
                            # "这趟车今天挂的是另一个号"，而不是让用户以为没有这趟车
                            departed_alias = alias_code
                # 别名命中 → `matched` 非空：直接走下面的正常成功路径（余票、经停照常给），
                # 不再做"已发车 / 不属于本区间"的判定。
                if not matched:
                    # 判断该车次是否本就属于该区间（属于但未列出 → 多为当日已过发车时间）
                    # 优先用**权威身份**（search.12306.cn 的起讫站）；离线目录仅在没有它时兜底。
                    # 2026-09-15 修正：此前一律用离线目录，导致"目录里没有的车次"（如 D2）
                    # 被判成"不属于该区间"→ ok=False，把"今日已发车/今日不运行"误报成工具失败。
                    if identity and identity.get("from_station"):
                        known = (identity["from_station"], identity.get("to_station") or "")
                    else:
                        known = rt.infer_endpoints_from_offline(train_code) or ("", "")
                    same_route = (
                        known[0] in (from_name, from_st) or known[1] in (to_name, to_st)
                    )
                    if same_route:
                        # 查询本身成功：结论是"该车次今日已发车"，属有效发现而非工具失败
                        #
                        # ⚠️ 2026-09-14 事故修复（R1 红线 D06）：
                        # 早期实现无条件下发"次日同车次时刻"，且与今日事实混排，
                        # 结果模型把次日 11:24 当成"今天到达时间"作答，还自造"可交叉印证"。
                        # 现在：① **默认不下发任何时刻值**（只给站名）；② 仅在用户明确问
                        # "经停/历时/站序"时下发时刻，且独立成块 + 显式标注"图定时刻，
                        # 非实际运行时刻" + 严禁用于回答实际到发/晚点。
                        #
                        # 2026-09-15 增补：经停表改由**权威 train_no 直查** 12306 图定表
                        # （`czxx/queryByTrainNo`），不再靠"次日 OD 查询"旁路——
                        # 该接口与售票状态无关，已发车车次同样有完整经停。
                        stops = pre_stops or await self._fetch_stops(
                            ident_train_no or "", from_code, to_code, date_str,
                            from_name=from_name, to_name=to_name,
                        )
                        stop_names = _stop_names(stops)
                        ref = None
                        ref_text = ""
                        ref_note = ""
                        if stops and include_reference:
                            ref = {
                                "date": date_str,
                                "stop_count": len(stops),
                                "stops": stops,
                                "source": "12306 图定表（czxx/queryByTrainNo）",
                                "is_actual_run": False,
                            }
                            ref_text = (
                                f"\n\n===【图定时刻表（{date_str}，12306 图定表）】===\n"
                                f"经停 {len(stops)} 站：\n{_format_stops(stops, with_times=True)}\n"
                                f"⚠️ 这是 12306 的 **图定（计划）时刻表**，**不是实际运行时刻**；"
                                f"**严禁**把它当作「实际到发时间/是否晚点」回答。"
                                f"该车次在 {date_str} 的**余票不可得**（12306 不再列出已发车次）。"
                                "调图后该表可能变化。"
                            )
                            ref_note = (
                                f"；已附 **图定时刻表**（{date_str}，仅用于回答经停/站序/历时，"
                                "不得当作实际运行或晚点情况）"
                            )
                        elif stop_names:
                            # 默认路径：只给站名、**不给任何时刻值**（红线 D06）
                            ref_text = (
                                f"\n该车次经停 {len(stop_names)} 站（仅站名）："
                                + " → ".join(stop_names[:_STOPS_MAX_RENDER])
                                + "\n（默认不下发时刻值；如需「图定时刻/历时」，请明确询问经停或历时）"
                            )
                        return ToolResult(
                            ok=True,
                            data={
                                "train_code": train_code,
                                "status": "departed",
                                "train_date": date_str,
                                "today_times_available": False,
                                "from_station": from_name,
                                "to_station": to_name,
                                "train_no": ident_train_no,
                                "known_route": {"from": known[0], "to": known[1]},
                                "trains": trains[:15],
                                "count": len(trains),
                                "stops": stops,
                                "stops_with_times": bool(ref),
                                "stop_count": len(stops),
                                "reference": ref,
                                "same_train_code": departed_alias,
                                "same_train_no": ident_train_no if departed_alias else "",
                                "_hint": (
                                    "如用户问经停/历时，可再次调用并带 include_reference=true"
                                    if not include_reference else ""
                                ),
                            },
                            text=(
                                f"车次 {train_code}（{known[0] or '?'}→{known[1] or '?'}）"
                                f"在 {date_str} 的 12306 剩余车次列表中不存在，"
                                "通常表示该车次今日已发车（12306 不再列出已发车次），"
                                f"因此 **{date_str} 的发车/到达时刻与余票均不可得**（不是数据缺失，"
                                "是 12306 对已发车次不再提供）。"
                                + (
                                    f"\n⚠️ 同一次车不同车次号：{train_code} 与 {departed_alias} "
                                    f"是同一次车（12306 内部编号同为 {ident_train_no}）；"
                                    f"本日 12306 列表里挂的是 {departed_alias}，同样已过发车时间。"
                                    if departed_alias else ""
                                )
                                + (
                                    "如需该车次的经停站与历时，可明确说明后重新查询。"
                                    if not include_reference else ""
                                )
                                + ref_text
                            ),
                            sources=["https://kyfw.12306.cn/otn/leftTicket/queryI"],
                            note=(
                                f"12306 实时查询成功（{date_str}）：车次已发车，"
                                f"{date_str} 的时刻/余票不可得"
                                + (
                                    f"；同一次车不同车次号：{train_code} 与 {departed_alias} "
                                    f"内部编号同为 {ident_train_no}"
                                    if departed_alias else ""
                                )
                                + ref_note
                                + "。"
                            ),
                        )
                    # 车次不属于该区间 → 真正的未找到
                    return ToolResult(
                        ok=False,
                        data={"trains": trains[:15], "count": len(trains)},
                        error=f"未在 {from_name}→{to_name}（{date_str}）找到车次 {train_code}",
                        text=(
                            f"{from_name}→{to_name}（{date_str}）区间不含 {train_code}"
                            f"（该车次属于 {known[0] or '?'}→{known[1] or '?'}）。\n"
                            "当日车次（前 15）：\n" + "\n".join(listed)
                        ),
                        note=f"已实时查询 12306（{date_str}）",
                    )

                t = matched[0]
                # 经停站：优先用权威 train_no 直查 12306 图定表（完整、不受售票状态影响）
                # 2026-09-15 修复：旧实现只走 OD 旁路，取不到时整段经停丢失。
                # ⚠️ 注意 train_no 的两种含义：余票接口返回的 `train_no` 实际是**车次号**（"G1"），
                #    而 `czxx/queryByTrainNo` 需要**内部编号**（"24000000G10L"）。
                #    因此优先用 search.12306.cn 给的权威编号，缺它时才把车次号交给 MCP 自行转换。
                routes = pre_stops or await self._fetch_stops(
                    ident_train_no or str(t.get("train_no") or ""),
                    from_code, to_code, date_str,
                    from_name=from_name, to_name=to_name,
                )
                note = f"12306 实时数据（{date_str}）"
                if same_train_from:
                    note += (
                        f"；⚠️ 同一次车不同车次号：用户问的 {same_train_from} 与本次实际开行的 "
                        f"{train_code} 是**同一次车**（12306 内部编号同为 {same_train_no}，"
                        "同一车底/交路，车次号随运行方向或交路分段变化），"
                        f"以下按 12306 当日实际开行的 {train_code} 给出"
                    )
                if identity:
                    note += "；车次起讫站按 12306 官方车次搜索校正"
                if inferred:
                    note += f"；起止站由车次目录自动推断为 {from_name}→{to_name}"
                if probed:
                    note += (
                        f"；该车次不在离线目录中，起止站由枢纽区间实时探测得到 "
                        f"（{from_name}→{to_name}）"
                    )
                if date_warn:
                    note += f"；{date_warn}"
                stops_text = _format_stops(routes, with_times=True)
                return ToolResult(
                    ok=True,
                    data={
                        "train_code": train_code,
                        "train_no": t.get("train_no", ""),
                        "from_station": t.get("from_station", ""),
                        "to_station": t.get("to_station", ""),
                        "start_time": t.get("start_time", ""),
                        "arrive_time": t.get("arrive_time", ""),
                        "duration": t.get("duration", ""),
                        "seats": t.get("seats", {}),
                        "routes": routes,
                        "stops": routes,
                        "stop_count": len(routes),
                        "source": "12306-realtime",
                        "inferred_endpoints": inferred,
                        "train_date": date_str,
                        "same_train_from": same_train_from,
                        "same_train_no": same_train_no,
                    },
                    text=(
                        (
                            f"⚠️ 同一次车不同车次号：你问的 {same_train_from} 与 12306 当日实际"
                            f"开行的 {train_code} 是**同一次车**（内部编号同为 {same_train_no}，"
                            "车次号随运行方向/交路分段变化）。以下为实际开行号的数据：\n"
                        ) if same_train_from else ""
                    )
                    + _format_train(t, routes)
                    + (f"\n\n经停站全表（{len(routes)} 站）：\n{stops_text}" if stops_text else ""),
                    sources=["https://kyfw.12306.cn/otn/leftTicket/queryI"],
                    note=note,
                )
            except Realtime12306Error as e:
                rt_err = str(e)
            except Exception as e:  # noqa: BLE001
                rt_err = format_error(e)
        else:
            rt_err = "无法确定起止站，且离线车次目录中无此车次"

        # ---- 4) 余票接口不可用（限流/反爬/已发车）但已取到图定表 → 直接给经停 ----
        # 12306 的余票接口对高频访问会限流，而"这趟车经停哪些站"本来就不需要余票数据。
        # 若此处因限流降级成"2022 静态归属"，用户就又看不到经停了——故优先交付图定表。
        if pre_stops:
            stop_names = _stop_names(pre_stops)
            stops_text = _format_stops(pre_stops, with_times=True)
            first, last = pre_stops[0], pre_stops[-1]
            return ToolResult(
                ok=True,
                data={
                    "train_code": train_code,
                    "train_no": ident_train_no,
                    "source": "12306-timetable",
                    "schedule_type": "图定",
                    "train_date": date_str,
                    "from_station": (identity or {}).get("from_station", from_st),
                    "to_station": (identity or {}).get("to_station", to_st),
                    "start_time": first.get("start_time", ""),
                    "arrive_time": last.get("arrive_time", ""),
                    "stops": pre_stops,
                    "stop_count": len(pre_stops),
                    "stops_with_times": True,
                    "realtime_error": rt_err,
                },
                text=(
                    f"车次 {train_code}（{(identity or {}).get('from_station') or from_st}"
                    f"→{(identity or {}).get('to_station') or to_st}）"
                    f"经停 {len(pre_stops)} 站（12306 图定时刻表，{date_str}）：\n{stops_text}"
                ) + (
                    f"\n（{date_str} 的余票/实际运行信息不可得：{rt_err}）"
                    if rt_err else ""
                ),
                sources=["https://kyfw.12306.cn/otn/czxx/queryByTrainNo"],
                note=(
                    "12306 **图定时刻表**（czxx/queryByTrainNo）："
                    "可用于回答经停站/站序/图定到发；**不是**实际运行时刻，"
                    "也不能据此给出余票。"
                    + (f"（余票接口本次不可用：{rt_err}）" if rt_err else "")
                ),
            )

        # ---- 降级 A：本地 GTFS 快照（周更）----
        # 12306 限流/不可达时，仍要能回答"这趟车经停哪、图定几点到"——
        # 本地快照就能给（实测 G1 7 站、终点 1318km，与 12306 图定表一致）。
        # 红线 D06 不变：**默认只给站名**，时刻仅在 include_reference（问经停/历时）时下发，
        # 且必须标注"本地快照、图定时刻、非实际运行"。
        try:
            from app.data import dict as _dict

            if _dict.available():
                local_stops = _dict.trip_stops(train_code)
                if local_stops:
                    names = [x["station"] for x in local_stops]
                    fetched = _now_iso()
                    if include_reference:
                        table = "\n".join(
                            f"{i+1}\t{x['station']}\t{x['arrive'] or '----'}\t"
                            f"{x['depart'] or '----'}\t{x.get('distance_km') if x.get('distance_km') is not None else '----'}"
                            for i, x in enumerate(local_stops)
                        )
                        text = (
                            f"车次 {train_code} 经停 {len(local_stops)} 站"
                            f"（**本地 GTFS 快照**，非实时）：\n"
                            "序\t站名\t到达\t发车\t累计里程km\n" + table
                            + f"\n⚠️ 这是本地**周更快照**里的图定时刻，**不是实际运行时刻**；"
                            f"本次 12306 实时查询未成功（{rt_err}），因此**余票与当日实际状态不可得**。"
                        )
                    else:
                        text = (
                            f"车次 {train_code} 经停 {len(local_stops)} 站（**本地 GTFS 快照**，仅站名）："
                            + " → ".join(names)
                            + f"\n（默认不下发时刻值：本次 12306 实时查询未成功（{rt_err}）。"
                            "如需图定时刻/历时，请明确询问经停或历时）"
                        )
                    return ToolResult(
                        ok=True,
                        data={
                            "train_code": train_code, "source": "local-gtfs",
                            "schedule_type": "图定（本地快照）",
                            "train_date": date_str,
                            "stops": local_stops, "stop_count": len(local_stops),
                            "stops_with_times": bool(include_reference),
                            "realtime_error": rt_err,
                            "first_station": names[0], "last_station": names[-1],
                            "distance_km": local_stops[-1].get("distance_km"),
                        },
                        text=text,
                        sources=["https://github.com/wensimehrp/chinese-railway-gtfs"],
                        note=("本地 GTFS 快照（周更）：可回答经停站/站序/图定时刻；"
                              "**不是**实时数据，也不能给出余票"),
                        total=len(local_stops), shown=len(local_stops),
                        filters={"车次": train_code}, fetched_at=fetched,
                    )
        except Exception as e:  # noqa: BLE001 —— 本地兜底失败不影响后续降级
            _log.debug("本地 GTFS 兜底失败：%s", e)

        # ---- 降级 B：离线车次归属（明确标注非实时）----
        try:
            from app.data.train_db import TrainDB, get_train_db

            tdb: TrainDB = get_train_db()
            try:
                tdb._ensure_loaded()
            except RuntimeError:
                # **不在请求路径里下载 15MB 缓存**（M11.1 修复）：
                # 早期实现会在此 await build_train_cache()，让一次用户提问同步等一个 15MB 下载，
                # 既拖长延迟又可能在超时后留下半成品。缓存属**预置数据**，
                # 由 `scripts/upgrade_python.sh` / `scripts/setup.sh` 预热。
                return ToolResult(
                    ok=False,
                    error=f"实时查询未成功（{rt_err}），且离线车次目录未预热",
                    note=(
                        "离线车次目录（非实时兜底数据）尚未构建；"
                        "请运行 `bash scripts/upgrade_python.sh` 或 "
                        "`PYTHONPATH=. .venv/bin/python -c \"import asyncio;from app.data.train_db import build_train_cache;asyncio.run(build_train_cache())\"` 预热后再试"
                    ),
                )

            info = tdb.lookup(train_code)
            if info:
                return ToolResult(
                    ok=True,
                    data={**info, "source": "offline-cache", "realtime_error": rt_err},
                    text=(
                        f"车次 {train_code} 的基础归属：{info.get('from_station','?')} → "
                        f"{info.get('to_station','?')}（{info.get('train_type','')}类）。"
                    ),
                    sources=[
                        "https://kyfw.12306.cn/otn/resources/js/query/train_list.js"
                    ],
                    note=(
                        f"⚠️ 非实时：12306 实时查询未成功（{rt_err}）。"
                        "以上仅为 2022 年列车目录的静态归属，不含当前时刻与余票。"
                    ),
                )
            matches = tdb.search(train_code)
            if matches:
                lines = [
                    f"{m['train_code']} {m.get('from_station','')}->{m.get('to_station','')}"
                    for m in matches[:10]
                ]
                return ToolResult(
                    ok=True,
                    data={"matches": matches, "count": len(matches)},
                    text="未精确匹配，找到以下相关车次：\n" + "\n".join(lines),
                    sources=[
                        "https://kyfw.12306.cn/otn/resources/js/query/train_list.js"
                    ],
                    note=f"⚠️ 非实时（{rt_err}）；匹配 {len(matches)} 条离线记录",
                )
        except Exception as e:  # noqa: BLE001
            rt_err = f"{rt_err}；离线兜底也失败：{format_error(e)}"

        return ToolResult(
            ok=False,
            error=f"未能查询车次 {train_code}：{rt_err}",
            note="请确认网络可达 12306（需境内网络 + Python ≥ 3.10）。"
                 "另：同一次车在交路不同分段会挂**不同车次号**（如 G2365/G2368、"
                 "D2238/D2235、Z184/Z181），12306 只列当日实际开行的那个号；"
                 "若你记的是其中一个，可换另一个号再问，"
                 "或用「汉口到上海虹桥今天有哪些车」按区间找。",
        )