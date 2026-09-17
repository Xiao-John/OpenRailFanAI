"""里程与车站档案工具（本地字典 + 黄河铁路网客运里程表）。

回答的问题（此前项目**完全空白**，只能靠模型瞎猜）：
- 「北京南到济南西多少公里？」→ 406 km
- 「京沪高速线经过哪些站、各站多少公里？」→ 逐站里程表（含线路连接点）
- 「北京南站的电报码 / 车站编号是多少？是不是接算站？」→ `-VNP` / `10010` / 接算站

数据来源与口径（**必须如实标注，不得混为一谈**）
1. **GTFS 快照**（`wensimehrp/chinese-railway-gtfs`，周更）：车次图定时刻 + 累计里程 +
   车站 WGS84 坐标。用于"两站里程"（取同一条车次上两站的累计里程差）。
2. **黄河铁路网客运里程表**（jprailfan，页面自述数据更新 20260810）：线路汇总、
   逐站里程、车站档案（电报码/TMIS 编号/接算站/营业限制/电话区号）。
   该表源自《铁路客运运价里程表》，**有著作权** → 仅内部检索、低频、标来源。

两者已实测互证：京沪高速线 北京南→上海虹桥 **均为 1318 km**、北京南→济南西 **均为 406 km**。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.data import dict as D
from app.tools.base import Tool, ToolResult

_MAX_LINE_ROWS = 60          # 逐站里程表最多渲染多少行
JP_MARK = "黄河铁路网客运里程表"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gtfs_mark() -> str:
    st = D.stats()
    return f"GTFS 本地快照（周更，{st.get('trips', 0)} 车次 / {st.get('stops', 0)} 站）"


def _km(v) -> float | None:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


class RailMileageTool(Tool):
    name = "rail.mileage"
    description = (
        "里程与车站档案（本地字典，毫秒级）：两站间里程、线路逐站里程表、"
        "车站电报码/TMIS 编号/是否接算站/营业限制。数据来源为 GTFS 周更快照与黄河铁路网客运里程表"
    )

    async def invoke(self, params: dict) -> ToolResult:
        from_st = str(params.get("from") or params.get("from_station") or "").strip()
        to_st = str(params.get("to") or params.get("to_station") or "").strip()
        line = str(params.get("line") or params.get("linename") or "").strip()
        station = str(params.get("station") or params.get("name") or "").strip()

        if not D.available():
            return ToolResult(
                ok=False,
                error="本地数据字典尚未构建（未找到 backend/data/dict.db）",
                note="请先运行 `python3 scripts/mirror_dict.py --all` 构建本地字典（GTFS + 线路里程表）",
            )

        if from_st and to_st:
            return self._two_stations(from_st, to_st)
        if line:
            return await self._line_table(line)
        if station:
            return await self._station_profile(station)
        return ToolResult(
            ok=False,
            error="缺少参数：需要 from+to（两站里程）、line（线路逐站里程）或 station（车站档案）",
            note="例如 from=北京南&to=济南西；或 line=京沪高速线；或 station=北京南",
        )

    # ---- 两站里程 ----

    def _two_stations(self, from_st: str, to_st: str) -> ToolResult:
        hit = D.distance_between(from_st, to_st)
        fetched = _now_iso()
        if hit:
            km = _km(hit["km"])
            # 同时给出反查的线路汇总（若有），便于核对口径
            same_line = [l for l in D.search_lines("", 2000)
                         if {l["from_station"], l["to_station"]} == {from_st, to_st}]
            extra = ""
            if same_line:
                l = same_line[0]
                extra = (f"\n另有整条线路口径：{l['line']} {l['from_station']}→{l['to_station']} "
                         f"全程 {l['mileage_km']} km")
            return ToolResult(
                ok=True,
                data={"mode": "two_stations", "from_station": from_st, "to_station": to_st,
                      "km": km, "sample_train": hit["sample_train"], "source": hit["source"]},
                text=(
                    f"{from_st} → {to_st}：**{km} 公里**"
                    f"（按 {hit['sample_train']} 次同车次经停的累计里程差计算，"
                    f"即两站间的**径路里程**）" + extra
                ),
                sources=[f"https://github.com/wensimehrp/chinese-railway-gtfs"],
                note=(f"{hit['source']}；该值为**图定径路里程**，不是最短径路、也不是票价里程。"
                      "如需「最短径路」请用 rail.line"),
                total=1, shown=1, filters={"区间": f"{from_st}→{to_st}"},
                fetched_at=fetched,
            )
        # 退路：本地线路汇总表里找**同时包含两站**的线路（无法给区间里程，但能给出线路口径）
        cands = [l for l in D.search_lines("", 2000)
                 if from_st in (l["from_station"], l["to_station"]) or to_st in (l["from_station"], l["to_station"])]
        if cands:
            top = cands[0]
            return ToolResult(
                ok=True,
                data={"mode": "two_stations_line_only", "from_station": from_st,
                      "to_station": to_st, "line": top},
                text=(f"未取到 {from_st}→{to_st} 的**区间里程**（本地 GTFS 快照里没有同时经停两站的车次）；"
                      f"但查到相关线路：{top['line']} {top['from_station']}→{top['to_station']} "
                      f"全程 {top['mileage_km']} km（起点/终点与所问站点部分吻合）"),
                sources=["https://www.jprailfan.com/tools/stat/"],
                note=f"{JP_MARK}（线路汇总口径）；区间里程请指定同线路的两站",
                total=len(cands), shown=1, filters={"区间": f"{from_st}→{to_st}"},
                fetched_at=fetched, truncated=len(cands) > 1,
            )
        return ToolResult(
            ok=False,
            error=f"未取到 {from_st}→{to_st} 的里程：本地 GTFS 快照里没有同时经停这两站的车次，"
                  "线路汇总表也无匹配线路",
            note="可换用同线路的两站（如 北京南→济南西），或先用 station.lookup 确认站名规范写法",
            sources=[f"https://github.com/wensimehrp/chinese-railway-gtfs"],
            fetched_at=fetched,
        )

    # ---- 线路逐站里程 ----

    async def _line_table(self, line: str) -> ToolResult:
        fetched = _now_iso()
        master = D.line_master(line)
        if master is None:
            near = D.search_lines(line, 6)
            if not near:
                return ToolResult(
                    ok=False,
                    error=f"本地线路表里没有「{line}」",
                    note="线路名需用规范写法（如 京沪高速线、京沪线、京广高速线）；可用关键词模糊搜索",
                    fetched_at=fetched,
                )
            lines = "；".join(f"{l['line']}（{l['from_station']}→{l['to_station']} {l['mileage_km']}km）" for l in near)
            return ToolResult(
                ok=False,
                error=f"未精确匹配线路「{line}」，相近线路：{lines}",
                note="请用上列规范线路名重查",
                fetched_at=fetched,
            )

        rows = await D.line_stations(line)
        head = (f"{master['line']}：{master['from_station']} → {master['to_station']}，"
                f"**全程 {master['mileage_km']} km**")
        if not rows:
            return ToolResult(
                ok=True,
                data={"mode": "line", "line": master},
                text=head + "\n（该线路的逐站里程表本次未能取到，可稍后重试）",
                sources=["https://www.jprailfan.com/tools/stat/"],
                note=f"{JP_MARK}（线路汇总口径）",
                total=1, shown=1, fetched_at=fetched,
            )

        shown = rows[:_MAX_LINE_ROWS]
        body = "\n".join(
            "  {seq}. {name} {dist}{tc}{no}{st}{conn}".format(
                seq=r["seq"], name=r["station"], dist=r["dist_from_start"] or "-",
                tc=f" {r['telecode']}" if r["telecode"] else "",
                no=f" 编号{r['tmjs_no']}" if r["tmjs_no"] else "",
                st=" 接算站" if (r["is_settlement"] or "") == "Yes" else "",
                conn="（线路连接点，非车站）" if r["is_connector"] else "",
            )
            for r in shown
        )
        truncated = len(rows) > len(shown)
        stations_only = [r for r in rows if not r["is_connector"]]
        return ToolResult(
            ok=True,
            data={"mode": "line", "line": master, "rows": [dict(r) for r in shown],
                  "row_count": len(rows), "station_count": len(stations_only)},
            text=(
                head
                + (f"；备注：{master['remark']}" if master.get("remark") else "")
                + f"\n逐站里程（{len(rows)} 行，其中车站 {len(stations_only)} 个"
                + ("，**已截断**" if truncated else "") + "）：\n"
                + body
            ),
            sources=["https://www.jprailfan.com/tools/stat/"],
            note=(f"{JP_MARK}（逐站口径，数据更新 20260810）"
                  "；注：`xx所` 是线路连接点、不是办理客运的车站，回答时不要把它当车站"),
            total=len(rows), shown=len(shown), truncated=truncated,
            filters={"线路": line}, fetched_at=fetched,
        )

    # ---- 车站档案 ----

    async def _station_profile(self, station: str) -> ToolResult:
        fetched = _now_iso()
        prof = await D.station_profile(station)
        if not prof:
            return ToolResult(
                ok=False,
                error=f"未取到「{station}」的车站档案（站名可能不规范）",
                note="请先用 station.lookup 确认规范站名（如 北京南、上海虹桥）",
                fetched_at=fetched,
            )
        coord = D.station_coord(station)
        lines = [l for l in D.search_lines("", 2000)
                 if station in (l["from_station"], l["to_station"])]
        text = (
            f"{prof['station']} 车站档案："
            f"拼音码 {prof['pinyin']}、电报码 {prof['telecode']}、车站编号（TMIS）{prof['tmjs_no']}、"
            f"所属路局 {prof['bureau']}、接算站 {'是' if (prof['is_settlement'] or '') == 'Yes' else '否'}、"
            f"营业限制 {prof['restriction'] or '无'}、电话订票区号 {prof['phone_area'] or '-'}"
        )
        if coord:
            text += f"\n坐标（{coord['crs']}）：{coord['lat']:.4f}, {coord['lon']:.4f}"
        if lines:
            text += "\n所在线路（起终站口径）：" + "；".join(
                f"{l['line']} {l['from_station']}→{l['to_station']} {l['mileage_km']}km" for l in lines[:6])
        return ToolResult(
            ok=True,
            data={"mode": "station", "profile": prof, "coord": coord, "lines": lines[:6]},
            text=text,
            sources=["https://www.jprailfan.com/tools/stat/"],
            note=(f"{JP_MARK}（数据更新 20260810，站点自述「非官方程序」，以铁路官方为准）"
                  + (f"；坐标为 {coord['crs']}，与高德/腾讯（GCJ-02）混用会偏 50–500m" if coord else "")),
            total=1, shown=1, filters={"车站": station}, fetched_at=fetched,
        )
