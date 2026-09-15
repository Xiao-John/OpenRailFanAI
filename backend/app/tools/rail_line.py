"""铁路线路（径路）查询工具。

数据源：黄河铁路网「中国铁路旅客径路查询」
        https://jprailfan.com/tools/dert/index.php?action=shrtroute

返回两站之间的**最短径路**，逐行给出：
    线路 | 车站全名 | 车站简称 | 车站电报码 | 里程(区间/累计)

这是 12306 与 rail.re 都不提供的数据（12306 无线路概念，rail.re 只有车次↔车组）。
典型问题："北京到上海走哪些线路""京沪线经过哪些站""A 到 B 多少公里"。

注意：该页面约 800KB（内含全国站名表），响应较慢，故使用更长的超时。
"""
from __future__ import annotations

import html as _html
import logging
import re
import time
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.tools._http import BROWSER_HEADERS, format_error, post_text
from app.tools.base import Tool, ToolResult

_log = logging.getLogger("railfan.tools")

_BASE = "https://jprailfan.com/tools/dert/index.php"

# 表头定位标记（页面内可能出现多次，见 `_candidate_tables`）
_TABLE_MARKER = "车站电报码"

# 行政区后缀：站名识别失败时尝试去掉（"北京市"→"北京"）
_ADMIN_SUFFIX_RE = re.compile(r"(市|省|自治区|特别行政区|地区|自治州|县|区)$")

# 行：<tr><td>线路</td><td>全名</td><td>简称</td><td>-电报码</td><td>区间km/总Xkm</td></tr>
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
# 里程："9km/总9km" 或 "0km"
_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*km")
_KM_TOTAL_RE = re.compile(r"总\s*(\d+(?:\.\d+)?)\s*km")


_SELECT_BLOCK_RE = re.compile(r"<select\b.*?</select>", re.I | re.S)


def _strip_selects(html: str) -> str:
    """去掉 <select>…</select>：desgroute 页把线路/车站下拉框嵌在结果表附近，
    不剥离会把 option 文本混进"线路/车站"单元格（实测）。"""
    return _SELECT_BLOCK_RE.sub(" ", html)


def _clean(cell: str) -> str:
    txt = _html.unescape(_TAG_RE.sub("", cell))
    return re.sub(r"\s+", " ", txt).strip()


def _balanced_table(html: str, start: int) -> str:
    """从 <table 起点做标签配平，返回该表完整子串（嵌套表也正确）。"""
    depth = 0
    i = start
    while i < len(html):
        nxt_open = html.find("<table", i)
        nxt_close = html.find("</table>", i)
        if nxt_close < 0:
            break
        if 0 <= nxt_open < nxt_close:
            depth += 1
            i = nxt_open + len("<table")
        else:
            depth -= 1
            i = nxt_close + len("</table>")
            if depth == 0:
                return html[start:i]
    return html[start:]


def _candidate_tables(html: str) -> list[str]:
    """返回**所有**含表头标记的表。

    早期实现只取 `车站电报码` 的**首次出现**，但实测该串在 817KB 页面里出现两次
    （站名列表表 + 径路结果表）：一旦顺序变化就会静默解析出 0 行。
    现在把所有候选都交给解析器，由"哪张表能解析出有效径路行"来决定。
    """
    out: list[str] = []
    marker = html.find(_TABLE_MARKER)
    while marker >= 0:
        start = html.rfind("<table", 0, marker)
        if start >= 0:
            table = _balanced_table(html, start)
            if table and table not in out:
                out.append(table)
        marker = html.find(_TABLE_MARKER, marker + 1)
    return out


def _extract_route_table(html: str) -> str:
    """兼容接口：返回首个候选表（真正的选择逻辑在 `parse_route_table`）。"""
    tables = _candidate_tables(html)
    return tables[0] if tables else ""


# 电报码：3 位大写字母（径路表内的有效车站）
_TELECODE_RE = re.compile(r"^[A-Z]{3}$")


def _parse_rows(table: str) -> list[dict]:
    """解析单张表为结构化行（仅接受「5 列 + 合法电报码」）。"""
    rows: list[dict] = []
    for tr in _ROW_RE.findall(table):
        cells = [_clean(c) for c in _CELL_RE.findall(tr)]
        if len(cells) != 5:
            continue
        line, station, short, telecode, mileage = cells
        telecode = telecode.lstrip("-").strip()
        if station in ("车站全名", "线路") or "里程" in mileage:
            continue
        # 过滤掉站名列表/提示文本：径路行必须有规范电报码
        if not _TELECODE_RE.match(telecode):
            continue

        km = None
        m = _KM_RE.search(mileage)
        if m:
            km = float(m.group(1))
        cum = None
        m2 = _KM_TOTAL_RE.search(mileage)
        if m2:
            cum = float(m2.group(1))       # 来自"总Xkm"：是累计里程，可信
        elif km is not None:
            cum = km                        # 起点行只有 "0km"（区间里程=累计里程）
        # 端点行允许里程缺失
        if km is None and cum is None:
            continue

        rows.append({
            "line": line,                 # 起点站该列为空
            "station": station,
            "short": short,
            "telecode": telecode,
            "km": km,
            "cum_km": cum,
            # 该行累计里程是否来自"总Xkm"（否则只是区间里程的近似）
            "cum_from_total": m2 is not None,
            "raw_mileage": mileage,
        })
    return rows


def parse_route_table(html: str) -> list[dict]:
    """解析径路结果表为结构化行列表（自动在多张候选表中取**最像径路的那张**）。

    每行：{line, station, short, telecode, km, cum_km, cum_from_total}
    """
    best: list[dict] = []
    for table in _candidate_tables(_strip_selects(html)):
        rows = _parse_rows(table)
        if len(rows) > len(best):
            best = rows
    return best


def _fmt_km(v: float | None) -> str:
    if v is None:
        return "?"
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _strip_admin_suffix(name: str) -> str:
    """去掉行政区后缀（"北京市"→"北京"）；无后缀时原样返回。"""
    return _ADMIN_SUFFIX_RE.sub("", name.strip()) or name.strip()



# ============ 指定径路 / 按线路名查询（desgroute，2026-09-14 逆向接入）============
# 交互流程（实测）：
#   step1 POST {i:"", s0:<起始站>, d0:"0", key1:"提交"}      → 返回 <select name=r1>：该站的出发线路
#   step2 POST {i:"1", s0:<起始站>, d0:"0", r1:<线路>}       → 返回 <select name=s1>：**整条线的站序**
#   step3 POST {…, s1:<终点站>}                              → 返回"指定径路"结果表（含区间/累计里程）
# 收益：① 按线路名反查站序（F06）；② 既有线（普速）口径里程（F05：京沪线 北京→上海 = 1463km）

_DESGROUTE_URL = _BASE + "?action=desgroute"
_OPTION_RE = re.compile(r"<option value=([^>]*)>([^<]*)", re.I)
_SELECT_RE_TMPL = r'<select name={name}[^>]*>(.*?)</select>'
_HIDDEN_RE_TMPL = r'name={name}[^>]*value=([^ >]*)'
_LINE_LABEL_RE = re.compile(r"^(?P<name>[^(（]+)[(（](?P<from>[^\-—~－]+)[\-—~－](?P<to>[^)）]+)[)）]")

# 线路 → {stations, route, ...} 的进程内缓存（desgroute 每次 3 个 800KB 页面，必须缓存）
_LINE_CACHE: dict[str, tuple[float, dict]] = {}


def _select_options(html: str, name: str) -> list[tuple[str, str]]:
    m = re.search(_SELECT_RE_TMPL.format(name=name), html, re.S | re.I)
    if not m:
        return []
    return [(v.strip(), t.strip()) for v, t in _OPTION_RE.findall(m.group(1))]


def _hidden_value(html: str, name: str) -> str:
    m = re.search(_HIDDEN_RE_TMPL.format(name=name), html)
    return m.group(1) if m else ""


def normalize_line_name(raw: str) -> str:
    """把用户口语化的线路名归一到该站的线路名（"老京沪线"→"京沪线"、"京沪高铁"→"京沪高速线"）。"""
    s = (raw or "").strip()
    s = re.sub(r"^(老|新|原|既有)", "", s)
    alias = {
        "京沪高铁": "京沪高速线", "京沪高速铁路": "京沪高速线",
        "京广高铁": "京广高速线", "京哈高铁": "京哈高速线",
        "京沪普速线": "京沪线", "京沪铁路": "京沪线",
    }
    return alias.get(s, s)


class RailLineStationsTool(Tool):
    """按线路名查站序与里程（黄河铁路网「指定径路查询」）。

    与 `rail.line`（两站间最短径路）互补：
    - `line` 给线路名 → 返回该线**完整站序**（F06）
    - `line` + `from_station`/`to_station` → 返回**指定径路**（可强制走既有线），含总里程（F05）
    """

    name = "rail.line_stations"
    description = "按线路名查询站序与里程（指定径路；可查既有线/普速口径里程）"

    async def _post(self, data: dict, timeout: float = 60.0) -> str:
        return await post_text(_DESGROUTE_URL, data, timeout=timeout)

    async def _resolve_line(
        self, line_name: str, entry_station: str = ""
    ) -> tuple[str, str, str, str] | None:
        """确定 (线路名, 起始站, 终点站, 线路下拉 value)。"""
        wanted = normalize_line_name(line_name)
        entries = [entry_station] if entry_station else []
        # 线路名里通常自带端点（"京沪线(北京-上海)"），据此挑一个入口站
        if not entries:
            for city in ("北京", "上海", "广州", "哈尔滨", "郑州", "西安", "成都", "昆明", "乌鲁木齐"):
                entries.append(city)
        for station in entries:
            html = await self._post({"i": "", "s0": station, "d0": "0", "key1": "提交"})
            for value, label in _select_options(html, "r1"):
                if value in ("0", ""):
                    continue
                m = _LINE_LABEL_RE.match(label)
                name = (m.group("name") if m else value).strip()
                if name == wanted or value == wanted or value == line_name:
                    frm = m.group("from").strip() if m else station
                    to = m.group("to").strip() if m else ""
                    return name, frm, to, value
        return None

    async def invoke(self, params: dict) -> ToolResult:
        line_raw = (params.get("line") or params.get("line_name") or params.get("target") or "").strip()
        if not line_raw:
            return ToolResult(ok=False, error="缺少参数：line（线路名，如 京沪线）")

        from_st = (params.get("from_station") or params.get("from") or "").strip()
        to_st = (params.get("to_station") or params.get("to") or "").strip()

        settings = get_settings()
        cache_key = f"{normalize_line_name(line_raw)}|{from_st}|{to_st}"
        cached = _LINE_CACHE.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < max(60, settings.rail_line_cache_ttl_s):
            return self._to_result(cached[1], line_raw, cached=True)

        try:
            resolved = await self._resolve_line(line_raw, entry_station=from_st)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"线路查询失败：{format_error(e)}",
                note="黄河铁路网需境内网络；页面较大（约 800KB）",
            )
        if not resolved:
            return ToolResult(
                ok=False,
                error=f"未在径路库中找到线路：{line_raw}",
                note="请给出规范线路名（如 京沪线 / 京沪高速线 / 陇海线）",
            )
        name, line_from, line_to, line_value = resolved
        start = from_st or line_from
        end = to_st or line_to

        try:
            h2 = await self._post({"i": "1", "s0": start, "d0": "0", "r1": line_value, "key1": "提交"})
            stations = [
                t.replace("(●)", "").replace("（●）", "").strip()
                for _v, t in _select_options(h2, "s1")
                if t and "请先选择" not in t
            ]
            h3 = await self._post(
                {"i": "1", "s0": start, "d0": "0", "r1": line_value, "s1": end, "key1": "提交"}
            )
            rows = parse_route_table(h3)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"指定径路查询失败：{format_error(e)}",
                note="线路存在但明细获取失败；可稍后重试",
            )

        if not rows and not stations:
            return ToolResult(
                ok=False,
                error=f"未取到 {name} 的站序或径路明细",
                note="页面结构可能已变化",
            )

        payload = {
            "line": name,
            "line_query": line_raw,
            "from_station": start,
            "to_station": end,
            "stations": stations,
            "route": rows,
        }
        _LINE_CACHE[cache_key] = (now, payload)
        return self._to_result(payload, line_raw, cached=False)

    def _to_result(self, payload: dict, line_raw: str, *, cached: bool) -> ToolResult:
        name = payload["line"]
        stations = payload.get("stations") or []
        rows = payload.get("route") or []
        total_km = rows[-1]["cum_km"] if rows else None

        parts = [f"线路 {name}：共 {len(stations)} 站" if stations else f"线路 {name}"]
        if total_km is not None:
            parts.append(f"{payload['from_station']}→{payload['to_station']} 全程 {_fmt_km(total_km)} km（客运运价里程）")
        text = "；".join(parts) + "。"
        if stations:
            text += "\n站序：" + " → ".join(stations[:60]) + ("…" if len(stations) > 60 else "")
        if rows:
            text += "\n里程明细：\n" + "\n".join(
                f"{i+1}. [{r['line'] or '起点'}] {r['station']}（{r['telecode']}）{r['raw_mileage']}"
                for i, r in enumerate(rows)
            )

        src = f"https://jprailfan.com/tools/dert/index.php?action=desgroute"
        note = (
            f"黄河铁路网「指定径路查询」（{line_raw}→{name}）；"
            "**指定径路**给出的是该线路口径（既有线/普速）的里程，与两站间最短径路（可能走高线）不同"
        )
        if not rows:
            note += "；本次未取到里程明细，仅给出站序"
        if cached:
            note += "；结果来自缓存"
        return ToolResult(
            ok=True,
            data=payload,
            text=text,
            sources=[src],
            note=note,
            total=len(stations) or None,
            shown=len(stations) or None,
        )

class RailLineTool(Tool):
    name = "rail.line"
    description = "查询两站间铁路最短径路（途经线路、车站序列与里程）"

    async def _fetch(self, from_st: str, to_st: str, settings) -> str:
        url = f"{_BASE}?action=shrtroute&startstat={quote(from_st)}&endstat={quote(to_st)}"
        async with httpx.AsyncClient(
            timeout=max(settings.http_timeout, 30.0),   # 页面约 800KB
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        from_st = (params.get("from_station") or params.get("from") or "").strip()
        to_st = (params.get("to_station") or params.get("to") or "").strip()

        if not from_st or not to_st:
            return ToolResult(
                ok=False,
                error="缺少参数：from_station / to_station（发站与到站）",
                note="例如：from_station=北京, to_station=上海",
            )
        if from_st == to_st:
            return ToolResult(ok=False, error="发站与到站相同")

        # 站名规范化重试（M11.1）：该接口只认站名，"北京市/上海市" 这类
        # 带行政区后缀的写法会直接查不到 —— 先按原样查，失败再去后缀重试一次。
        attempts: list[tuple[str, str]] = [(from_st, to_st)]
        norm = (_strip_admin_suffix(from_st), _strip_admin_suffix(to_st))
        if norm != (from_st, to_st) and norm[0] and norm[1]:
            attempts.append(norm)

        rows: list[dict] = []
        used: tuple[str, str] = attempts[0]
        url = f"{_BASE}?action=shrtroute&startstat={quote(used[0])}&endstat={quote(used[1])}"
        last_error = ""
        for idx, (f, t) in enumerate(attempts):
            try:
                html = await self._fetch(f, t, settings)
            except Exception as e:  # noqa: BLE001
                last_error = format_error(e)
                continue
            parsed = parse_route_table(html)
            if parsed:
                rows = parsed
                used = (f, t)
                url = f"{_BASE}?action=shrtroute&startstat={quote(f)}&endstat={quote(t)}"
                if idx > 0:
                    _log.info("rail.line 站名规范化后命中：%s→%s", f, t)
                break

        if not rows:
            if last_error:
                return ToolResult(
                    ok=False,
                    error=f"径路查询失败：{last_error}",
                    note="黄河铁路网需境内网络；页面较大（约 800KB），已放宽超时",
                )
            return ToolResult(
                ok=False,
                error=f"未查询到 {from_st} → {to_st} 的径路",
                note=(
                    "该接口要求站点名可被径路库识别（已尝试去掉“市/省/区”等后缀）；"
                    "仍失败通常表示该站在径路库中不被收录，或页面结构已变化"
                ),
            )

        # 线路序列（去掉相邻重复与空值）
        lines: list[str] = []
        for r in rows:
            if r["line"] and (not lines or lines[-1] != r["line"]):
                lines.append(r["line"])

        last = rows[-1]
        total_km = last["cum_km"]
        # 只有末行给出"总Xkm"时，才是权威的累计里程；
        # 否则该值只是区间里程的近似（早期实现会把它当总里程直接报出）
        total_confident = bool(last.get("cum_from_total")) or len(rows) == 1

        # 人类可读摘要：线路 →(站) 线路 →(站) ...
        chain: list[str] = []
        for i, r in enumerate(rows):
            if r["line"] and (i == 0 or rows[i - 1]["line"] != r["line"]):
                chain.append(f"[{r['line']}]")
            chain.append(r["station"])

        total_text = (
            f"全程 {_fmt_km(total_km)} km"
            if total_confident
            else f"约 {_fmt_km(total_km)} km（末段未给出累计里程，数值可能有偏差）"
        )
        summary = (
            f"{rows[0]['station']} → {rows[-1]['station']}："
            f"{total_text}，途经 {len(lines)} 条线路、{len(rows)} 个车站。\n"
            "线路序列：" + " → ".join(lines) + "\n"
            "径路：" + " → ".join(chain[:60]) + ("…" if len(chain) > 60 else "")
        )

        # 逐行里程明细（供生成层引用）
        detail_lines = [
            f"{i+1}. [{r['line'] or '起点'}] {r['station']}"
            f"（{r['telecode']}）{r['raw_mileage']}"
            for i, r in enumerate(rows)
        ]

        note = "黄河铁路网「旅客径路查询」非官方数据，仅供参考；里程为客运运价里程"
        if used != (from_st, to_st):
            note += f"；站名已规范化为 {used[0]}→{used[1]}"
        if not total_confident:
            note += "；末段未给出累计里程，全程里程为近似值"

        return ToolResult(
            ok=True,
            data={
                "from_station": rows[0]["station"],
                "to_station": rows[-1]["station"],
                "query_from": from_st,
                "query_to": to_st,
                "used_from": used[0],
                "used_to": used[1],
                "total_km": total_km,
                "total_km_confident": total_confident,
                "line_count": len(lines),
                "station_count": len(rows),
                "lines": lines,
                "route": rows,
            },
            text=summary + "\n\n里程明细：\n" + "\n".join(detail_lines),
            sources=[url],
            note=note,
        )