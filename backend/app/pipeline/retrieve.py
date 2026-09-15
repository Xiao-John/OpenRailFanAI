"""数据检索层（Agent 工具循环，M3.1）。

根据 intent + 槽位选择数据源工具并调用，收集 {data, sources, tool_trace}。
工具路由（按 intent）：
- photo_spot  -> station.lookup + cnrail.map + emu.routing(担当车组) + web.search 兜底
- emu_routing -> emu.routing(担当车组/交路) + train.schedule
- rail_line   -> rail.line(两站间最短径路: 线路序列+车站+里程)
- ticket      -> ticket.query(12306 实时余票) + station.lookup
- schedule    -> train.schedule(实时时刻) + emu.routing + ticket.query
- station     -> station.lookup + cnrail.map（问"大屏/检票口/到发车次"时加 station.screen）
- news/general-> web.search 兜底

注：实时能力统一走 mcp-server-12306（`ticket.query` / `train.schedule`），
`t12306.search_tickets` 仅作为配置了 T12306_BASE 反代时的备选，不再由路由主动调用。

工具调用失败或未启用不影响整体：记录 note，交由生成层如实说明。
"""
from __future__ import annotations

import asyncio
import logging
import re

from app.config import get_settings
from app.od import parse_od
from app.pipeline.extract import Slots
from app.tools import registry
from app.tools import _rt12306 as rt
from app.tools._rt12306 import extract_train_code, is_emu_train_code
from app.tools.base import ToolResult

_log = logging.getLogger("railfan.retrieve")

# 车型/车组号（CR400AF / CRH2A / CR400BFA5054 等）—— 不是站名
_EMU_LIKE_RE = re.compile(r"^(CR|CRH)[0-9A-Za-z\-]*$", re.I)

# 站名/电报码类事实词：这类问题即使被判为 knowledge，也必须走站点库精确查表
# （2026-09-14 修复 E01★/E06：'上海虹桥的电报码' 被判知识型 → 只做网页搜索 → 给不出 AOH）
_STATION_FACT_RE = re.compile(r"(电报码|电报|拼音码|站名|车站|站台|几台|几个站台)")

# 车站大屏类问法：这类问题**既不该走 ticket.query（要区间）也不该走 train.schedule（要车次）**，
# 而是"一个车站 + 当日全部到发车次"，只有 station.screen 能答。
# 分强弱两档，避免"G1 今天晚点了吗"这种**车次级**问题被误判成车站级、还附一句无用的缺参说明：
#   强（本身就表明"我要看这个大屏"）：大屏 / 出发屏 / 到达屏 / 车站车次
#   弱（只有识别出具体车站时才值得查）：检票口 / 检票 / 正晚点 / 晚点
_SCREEN_STRONG_RE = re.compile(r"(大屏|出发屏|到达屏|车站车次)")
_SCREEN_WEAK_RE = re.compile(r"(检票口|检票|正晚点|晚点)")


def _is_train_code(v: str | None) -> bool:
    """是否为车次号（兼容 "G1次列车" 这类带后缀写法）。"""
    return extract_train_code(v) is not None


def _station_like(v: str | None) -> bool:
    """粗判"像不像站名"（用于决定能否把 location/target 当作起讫站）。

    背景：早期实现直接 `od = (loc, tgt)`，实测产生
    `ticket.query(from_station=吉林站, to_station=CR400AF)`、`to_station='G1'`
    这类**无效区间查询** —— 既浪费一次外部调用，也会把"查不到"错误地写进可靠性说明。
    这里做保守判断：含车次号 / 车型号 / 长英文串 / 数字的一律不算站名。
    """
    if not v:
        return False
    s = str(v).strip()
    if not (2 <= len(s) <= 8):
        return False
    if _is_train_code(s) or _EMU_LIKE_RE.match(s):
        return False
    if re.search(r"[A-Za-z]{2,}", s):     # 长英文串（型号/代码/占位符 "XX"）
        return False
    if re.search(r"\d", s):               # 站名一般不含数字
        return False
    return True


# ---- 从用户原话解析"集合筛选条件"（2026-09-14：把筛选放到工具侧，避免截断冒充缺失）----

_TIME_WINDOWS = (
    (("凌晨",), "00:00", "06:00"),
    (("早上", "早晨", "上午", "早班"), "05:00", "12:00"),
    (("中午", "午间"), "11:00", "14:00"),
    (("下午", "午后"), "12:00", "18:00"),
    (("晚上", "晚间", "夜里", "夜间", "晚班", "傍晚"), "17:00", "24:00"),
)

_SEAT_KEYWORDS = (
    (("商务座", "商务"), "business"),
    (("一等座",), "first_class"),
    (("二等座",), "second_class"),
    (("软卧",), "soft_sleeper"),
    (("硬卧",), "hard_sleeper"),
    (("硬座",), "hard_seat"),
)

_TYPE_KEYWORDS = (
    (("高铁",), "G"),
    (("动车",), "D"),
    (("城际",), "C"),
    (("普速", "绿皮", "慢车"), "K,T,Z"),
)

# 问"经停/历时/站序"时才允许下发次日参考时刻（D06 红线修复的检索侧开关）
_STOPS_QUESTION_RE = re.compile(r"(经停|停靠|经过哪些站|途经|站序|全程|历时|多长时间|要多久|几个小时)")


def _parse_time_window(*texts: str | None) -> tuple[str, str]:
    """'明天晚上' → ('17:00', '24:00')；无时段词返回 ('', '')。"""
    blob = " ".join(t for t in texts if t)
    for words, after, before in _TIME_WINDOWS:
        if any(w in blob for w in words):
            return after, before
    return "", ""


def _parse_seat(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t)
    for words, seat in _SEAT_KEYWORDS:
        if any(w in blob for w in words):
            return seat
    return ""


def _parse_train_type(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t)
    for words, ttype in _TYPE_KEYWORDS:
        if any(w in blob for w in words):
            return ttype
    return ""


def _train_code_from_message(message: str) -> str | None:
    """从原话里兜底解析车次号（含"1461"这种无"次"字的裸数字写法）。

    仅用于**槽位为空**时补位：`extract_train_code` 要求数字后接"次"，
    而用户常写"1461 都经过哪些站"（实测 D05）。
    """
    code = extract_train_code(message)
    if code:
        return code
    m = re.match(r"^\s*(\d{1,4})(?!\d)", message or "")
    if m:
        num = m.group(1)
        if not re.match(r"^(?:19|20)\d{2}$", num):     # 年份形态不算车次
            return num
    return None


# 时效敏感问题（开通/停运/调图/公告/最新进展）：需要"最新一批 + 全量一批"两份证据
_FRESHNESS_RE = re.compile(r"(开通|停运|停办|调图|公告|最新|进展|现在|目前|是否已经|通车|试运行|什么时候开)")


def _is_freshness_question(message: str | None, target: str | None) -> bool:
    return bool(_FRESHNESS_RE.search(f"{message or ''} {target or ''}"))


# ---- 线路名识别（F06/F05：按线路名查站序、指定径路里程）----
_LINE_TOKEN_RE = re.compile(r"([\u4e00-\u9fa5]{2,10}?(?:高速线|高铁|客专|铁路|通道|线))")
# 泛指词不算线路名（"走哪条线路""沿线" 等）
_LINE_STOPWORDS = {
    "线路", "哪条线", "这条线", "那条线", "本条线", "沿线", "干线", "支线", "专线",
    "单线", "双线", "复线", "哪条线路", "铁路线", "这条线路", "什么线",
}
# 问句里出现这些词，说明用户问的是"这条线本身"（站序/里程），而不是两站间怎么走
_LINE_ASK_RE = re.compile(r"(经过哪些站|经过的站|站序|沿线|经过哪里|多少个站|几站|多少公里|里程|全程|全长)")

# 里程/车站档案类问法（2026-09-15 新增本地字典 rail.mileage）：
# 这类问题此前**完全空白**（只能靠模型瞎猜）。里程与车站档案都是"字典类"数据，
# 已本地化为毫秒级查询，故可放心按关键词直接路由，不必担心拖慢响应。
_MILEAGE_RE = re.compile(r"(多少公里|多少千米|几公里|多少里程|里程|距离|多远)")
# 严格版"问站序"：用于区分「纯里程」与「站序+里程」。不能复用 _LINE_ASK_RE —— 
# 后者含"多少公里/里程"，会把"北京南到济南西多少公里"误判成站序问题，
# 于是又去调慢接口（rail.line_stations 冷启动实测 ~19s）。
_STOPS_SEQ_RE = re.compile(r"(经过哪些站|经过的站|站序|沿线|经过哪里|多少个站|几站|有哪些站)")

_STATION_PROFILE_RE = re.compile(r"(车站编号|TMIS|编组数|接算站|营业限制|订票区号|电报码|编号)")


# 先剥掉动词/疑问/泛称，避免把"北京到上海走哪条线"整段当成线路名
_LINE_NOISE_RE = re.compile(
    r"(请问|帮我|查一下|一下|走|坐|乘|从|到|去|的|沿线|经过|途经|哪条|这条|那条|"
    r"多少公里|多少千米|里程|全程|全长|站序|几站|多少个站|呢|吗|？|\?)"
)


def _detect_line_name(text: str | None) -> str | None:
    """从原话/槽位里识别线路名（如 京沪线 / 老京沪线 / 京沪高速线）。

    先剥离动词与疑问词再匹配（否则"北京到上海走哪条线"会被整段吞掉），
    再过滤泛指词（线路/干线/支线…）；找不到返回 None。
    """
    cleaned = _LINE_NOISE_RE.sub(" ", text or "")
    for m in _LINE_TOKEN_RE.finditer(cleaned):
        token = m.group(1).strip()
        if token in _LINE_STOPWORDS or len(token) < 3:
            continue
        return token
    return None


def _asks_about_line(text: str | None) -> bool:
    return bool(_LINE_ASK_RE.search(text or ""))


_STATION_IN_TEXT_RE = re.compile(r"([\u4e00-\u9fa5]{2,6}?)站")
# 不能当站名的前缀（"换乘车站/始发车站/终点车站" 这类会把"换乘车"当站名）
_STATION_TEXT_STOPWORDS = ("车站", "乘车站", "发车站", "到车站", "终点站", "始发站", "这", "那", "该", "本")


async def _station_from_text(message: str | None) -> str:
    """从原话里取站名（槽位抽取失败时的兜底）。

    两级策略：
    1. **优先用本地站点库做最长匹配**（`rt.all_stations()`，3384 站，最可靠）——
       在整句里找出现的最长站名（避免把"北京南站的车"切出"北京南站的车"）；
    2. 本地库不可用时退回保守正则 + 停用词过滤。
    """
    text = message or ""
    if not text:
        return ""
    db_ok = False
    try:
        await rt.ensure_loaded()          # 加载包内站点静态表（不联网，实测 ~0ms）
        names = [n for n in rt.all_stations().keys() if 2 <= len(n) <= 8]
        db_ok = bool(names)
        hits = [n for n in names if n in text]
        if hits:
            return max(hits, key=len)          # 最长匹配：北京南 优先于 北京
    except Exception as e:  # noqa: BLE001 —— 本地站点库不可用不应影响路由
        # ⚠️ 这里**必须留痕**：曾经因为少了一句 import 导致 NameError 被静默吞掉，
        # 于是"权威站点库匹配"从未生效、悄悄退回正则（把"换乘车站"切成"换乘车"）。
        _log.warning("本地站点库匹配不可用，退回正则兜底：%s: %s", type(e).__name__, e)
    if db_ok:
        # 本地库可用却无任何站名命中 → 原话里确实没有真实站名
        # （如"换乘车站的编号怎么看"），**不得**用正则硬切出"换乘车"这种假站名
        return ""
    for m in _STATION_IN_TEXT_RE.finditer(text):
        cand = m.group(1).strip()
        if any(cand.endswith(w) or cand == w for w in _STATION_TEXT_STOPWORDS):
            continue
        if _station_like(cand):
            return cand
    return ""


def _wants_stops(message: str | None) -> bool:
    return bool(_STOPS_QUESTION_RE.search(message or ""))


_SCREEN_DEPART_RE = re.compile(r"(出发|始发|发车|送站|开车)")
_SCREEN_ARRIVE_RE = re.compile(r"(到达|到站|接站|终到)")


def _screen_direction(message: str | None) -> str | None:
    """从原话判断大屏方向：'出发屏' → departure，'到达屏' → arrival，未指定 → None（两侧都要）。"""
    text = message or ""
    dep = bool(_SCREEN_DEPART_RE.search(text))
    arr = bool(_SCREEN_ARRIVE_RE.search(text))
    if dep and not arr:
        return "departure"
    if arr and not dep:
        return "arrival"
    return None


async def retrieve(
    intent: str,
    slots: Slots,
    question_type: str | None = None,
    message: str | None = None,
    prefetch=None,          # Prefetch | None：投机预取（perf P0-3），命中即复用
) -> dict:
    """按意图与槽位检索数据，返回 {data, sources, tool_trace, note}。

    question_type 影响检索策略：
      knowledge → 不走实时数据源（避免用错工具），改为 web 搜索取据供模型参考
      mixed     → 实时计划照常执行，额外补一次 web 搜索供知识部分使用
    """
    data: list[dict] = []
    sources: list[str] = []
    trace: list[str] = []
    notes: list[str] = []

    loc = slots.location
    tgt_raw = slots.target
    time_ = slots.time
    direction = slots.direction

    # 车次号归一化："G1次列车" -> "G1"（LLM 常带后缀，直接匹配会失败）
    train_code = extract_train_code(tgt_raw)
    tgt = train_code or tgt_raw

    # 起讫站：只认显式区间表述（"北京到上海"）；
    # location+target 仅在**两侧都像站名**时才兜底，避免伪造区间（见 _station_like）
    od = parse_od(direction) or parse_od(loc) or parse_od(tgt)
    if not od and loc and tgt and loc != tgt and _station_like(loc) and _station_like(tgt):
        od = (loc, tgt)

    # ---- 原话兜底（R1 缺陷 P0-3：F01/D05 槽位抽取为空导致该调的工具没调）----
    # 抽取层偶尔返回空槽位（实测 rail_line/schedule 各一例），而**答案就在用户原话里**。
    # 这里直接用原话补：区间表述 → od；车次号 → tgt。宁可多解析一次，也不能空手去搜网页。
    if not od and message:
        od = parse_od(message)
    if not tgt and message:
        tgt = _train_code_from_message(message)
        if tgt:
            tgt_raw = tgt

    # ---- 按意图计划调用的工具与参数 ----

    # 计划层面的说明（如"为何没发起某个查询"）：会并入最终 note，
    # 让生成层如实说明"缺少什么"，而不是把"没查"说成"查询失败"（修复 C06/C08/L03/D09）
    plan_notes: list[str] = []

    def _plan(intent: str) -> list[tuple[str, dict]]:
        if intent == "ticket":
            # 余票查询：走 12306 实时接口（ticket.query）
            plan: list[tuple[str, dict]] = []
            if _is_train_code(tgt):
                # 目标是"某车次的余票/席别"（如"G1 明天还有商务座吗"）：
                # ticket.query 需要起讫站，这里改走 train.schedule 直接取该车次席别
                # （2026-09-14 修复 C04/C09：此前后者退化成 station.lookup("G1") → 必然失败）
                plan.append(("train.schedule", {
                    "train": tgt, "date": time_ or None,
                    "include_reference": _wants_stops(message) or None,
                }))
                if od:
                    plan.append(("ticket.query", {
                        "from_station": od[0], "to_station": od[1],
                        "date": time_ or None,
                    }))
                else:
                    plan_notes.append(
                        f"查询的是车次 {tgt} 的席别：已用 train.schedule 取该车次余票；"
                        "区间余票需要出发站与到达站"
                    )
                return plan
            if od:
                after, before = _parse_time_window(message, direction, time_, slots.extra)
                plan.append(("ticket.query", {
                    "from_station": od[0], "to_station": od[1],
                    "date": time_ or None,
                    # 把"晚上/上午/二等座/普速"这类条件交给工具过滤，
                    # 而不是让模型在前 N 条里找（截断冒充缺失的根因）
                    "after_time": after or None,
                    "before_time": before or None,
                    "seat": _parse_seat(message, slots.extra) or None,
                    "train_type": _parse_train_type(message, slots.extra) or None,
                }))
                # 补充站点代码信息，便于生成层解释
                plan.append(("station.lookup", {"name": od[0]}))
            elif loc or tgt:
                plan.append(("station.lookup", {"name": loc or tgt or ""}))
                plan_notes.append(
                    "未发起余票查询：缺少完整区间（需要出发站与到达站，如「北京到上海」）；"
                    f"本次只做了站点核对（{loc or tgt}）"
                )
            else:
                # 实时意图缺参**不得**退化成通用网页搜索（会命中无关百科，实测 F01/D09）
                plan_notes.append(
                    "未发起余票查询：未识别出发站与到达站，请用户补充区间（如「北京到上海」）后重查"
                )
            return plan
        if intent == "rail_line":
            # 线路/径路/里程查询：两站间最短径路（rail.line）或按线路名的指定径路（rail.line_stations）
            plan = []
            line_name = _detect_line_name(" ".join(filter(None, [tgt, slots.extra, message])))
            # ---- 纯"里程"问题 → 只用本地字典（毫秒级），不再拖上慢接口 ----
            # 为什么单独分流：rail.line_stations（指定径路）冷启动实测 ~19s（要抓 3 个 800KB 页面），
            # 而"多少公里"这类问题本地 GTFS 快照即可回答（实测与黄河里程表互证：406km / 1318km）。
            # 只有同时问"经过哪些站/站序"时，才继续走 rail.line*（那是站序的权威口径）。
            mileage_ask = bool(_MILEAGE_RE.search(message or "") or _MILEAGE_RE.search(slots.extra or ""))
            stops_ask = bool(_STOPS_SEQ_RE.search(message or "") or _STOPS_SEQ_RE.search(slots.extra or ""))
            if mileage_ask:
                if line_name:
                    plan.append(("rail.mileage", {"line": line_name}))
                elif od:
                    plan.append(("rail.mileage", {"from": od[0], "to": od[1]}))
                    plan.append(("station.lookup", {"name": od[0]}))
                elif loc:
                    plan.append(("rail.mileage", {"station": loc}))
                if plan and not stops_ask:
                    # 纯里程问题：本地字典已够，直接返回，不去碰冷启动 ~19s 的指定径路接口
                    plan_notes.append(
                        "里程取自**本地数据字典**（GTFS 周更快照 / 黄河铁路网客运里程表），"
                        "口径是两站间的**图定径路里程**，不是最短径路、也不是票价里程"
                    )
                    return plan
                if plan:
                    plan_notes.append(
                        "里程取自**本地数据字典**（GTFS 周更快照 / 黄河铁路网客运里程表）；"
                        "站序取自黄河铁路网指定径路，两者口径可能不同"
                    )
            # 按线路名问站序/里程（F06/F05）：走"指定径路"接口，可给出既有线口径
            if line_name and (_asks_about_line(message) or _asks_about_line(slots.extra) or not od):
                plan.append(("rail.line_stations", {
                    "line": line_name,
                    "from_station": od[0] if od else None,
                    "to_station": od[1] if od else None,
                }))
                if od:
                    plan_notes.append(
                        f"按线路名「{line_name}」查的是**指定径路**（该线路口径，如既有线/普速里程），"
                        "与两站间最短径路（可能走高线）口径不同"
                    )
            elif od:
                plan.append(("rail.line", {
                    "from_station": od[0], "to_station": od[1],
                }))
                plan.append(("station.lookup", {"name": od[0]}))
            elif tgt:
                # 只给了线路名但没识别出可查线路 → 搜索兜底（明确说明能力边界）
                plan.append(("web.search", {"q": f"{tgt} 经过哪些车站 线路"}))
                plan_notes.append(
                    f"未在径路库中识别出线路「{tgt}」的站序能力，已改用检索；"
                    "如需按线路名查站序，请给出规范线路名（如 京沪线 / 陇海线）"
                )
            else:
                # 同上：径路属实时/事实型，缺区间时明确追问，而不是拿整句去搜索
                plan_notes.append(
                    "未发起径路查询：未识别出发站与到达站（径路需要两个车站），"
                    "请用户补充区间（如「北京到上海」）后重查"
                )
            return plan
        if intent == "emu_routing":
            # 车组交路/担当车组查询：核心是 emu.routing
            plan = []
            if _is_train_code(tgt):
                if is_emu_train_code(tgt):
                    plan.append(("emu.routing", {"train": tgt, "date": time_ or None}))
                    plan.append(("train.schedule", {
                        "train": tgt, "date": time_ or None,
                        "include_reference": _wants_stops(message) or None,
                    }))
                else:
                    # 普速（K/T/Z/纯数字）：rail.re 交路库只收录动车组，
                    # 改查 12306 实时时刻/经停，并如实说明担当数据无公开来源
                    # （2026-09-14 修复 A07/D06/L04）
                    plan.append(("train.schedule", {
                        "train": tgt, "date": time_ or None,
                        "include_reference": _wants_stops(message) or None,
                    }))
                    plan_notes.append(
                        f"{tgt} 为普速/非动车组车次：rail.re 交路库不收录其担当信息，"
                        "已改查该车次的实时时刻与经停；机车担当无公开数据源"
                    )
            elif tgt and _EMU_LIKE_RE.match(str(tgt).strip()):
                plan.append(("emu.routing", {"emu_no": tgt, "date": time_ or None}))
            else:
                # 目标既不是车次号也不是车组号（如"京沪标杆"）：
                # 不要再把它塞进交路查询（必然格式失败），改走检索并说明需要具体车次
                plan.append(("web.search", {"q": (message or f"{loc or ''} {tgt or ''}").strip()}))
                plan_notes.append(
                    f"“{tgt or '（未给出对象）'}”不是车次号/车组号，无法直接查担当车组；"
                    "已改用检索，请提供具体车次（如 G1）或车组号（如 CR400BFA-5159）"
                )
            return plan
        if intent == "schedule":
            plan = []
            if _is_train_code(tgt):
                # 车次明确：实时时刻 + 担当车组
                # include_reference 只在用户问"经停/历时/站序"时为真（D06 红线修复）
                plan.append(("train.schedule", {
                    "train": tgt, "date": time_ or None,
                    "include_reference": _wants_stops(message) or None,
                }))
                plan.append(("emu.routing", {"train": tgt, "date": time_ or None}))
            elif tgt:
                # 车型（如 CR400AF）：查其担当交路
                plan.append(("emu.routing", {"emu_no": tgt, "date": time_ or None}))
            if loc:
                plan.append(("station.lookup", {"name": loc}))
            # 区间时刻查询（如"北京到上海有哪些车"）→ 实时余票/车次列表
            if od:
                after, before = _parse_time_window(message, direction, time_, slots.extra)
                plan.append(("ticket.query", {
                    "from_station": od[0], "to_station": od[1],
                    "date": time_ or None,
                    "after_time": after or None,
                    "before_time": before or None,
                    "seat": _parse_seat(message, slots.extra) or None,
                    "train_type": _parse_train_type(message, slots.extra) or None,
                }))
            if not plan:
                # 既没有车次也没有完整区间（如"明天下午到上海的高铁几点"）：
                # 明确说明缺什么，不要拿整句去搜索（会命中无关百科）
                plan_notes.append(
                    "未发起查询：缺少出发站（区间时刻需要「出发站→到达站」）或具体车次；"
                    "请补充出发地后重试"
                )
            return plan
        if intent == "photo_spot":
            plan = []
            if loc or tgt:
                plan.append(("station.lookup", {"name": loc or tgt or ""}))
            plan.append(("cnrail.map", {"location": loc or tgt}))
            if tgt:
                # 车次 → 查担当车组；车型 → 查该车型交路
                plan.append(("emu.routing", {
                    "train" if _is_train_code(tgt) else "emu_no": tgt,
                    "date": time_ or None,
                }))
            plan.append(("web.search", {"q": f"{loc or ''} {tgt or ''} 铁路拍摄 机位".strip()}))
            return plan
        if intent == "station":
            plan = []
            target_station = loc or tgt or (od[0] if od else "")
            if target_station:
                plan.append(("station.lookup", {"name": target_station}))
            # 注：不再调用 railre —— rail.re 主站是前端渲染 SPA，`/{站名}` 实测恒 404，
            # 调用它只会稳定失败并往 note 里塞一条误导性说明（见 审计报告（历史） P1-3）。
            # 车站信息由 station.lookup（12306 站点库）与 cnrail.map（地图外链）覆盖。
            if target_station:
                plan.append(("cnrail.map", {"location": target_station}))
            return plan
        # news / general：用 web.search 检索
        query = f"{loc or ''} {direction or ''} {tgt or ''} 铁路".strip() or "中国铁路 最新资讯"
        if intent == "news" or _is_freshness_question(message, tgt):
            # 资讯类"搜两遍"（R1 P1-7 / H01-H02）：一遍按最新（时效过滤）、一遍不滤，
            # 两批结果都进事实块，由 prompt 的"时效优先"规则择优；带日期的那批优先采信。
            q = (message or query).strip()
            return [
                ("web.search", {"q": q, "freshness": "month", "limit": 5}),
                ("web.search", {"q": q, "limit": 5}),
            ]
        return [
            ("web.search", {"q": query}),
        ]

    plan = _plan(intent)

    # 知识型问题：实时数据源不适用（如"样车车组号"查 emu.routing 必然落空），
    # 改为 web 搜索取据，让模型在"有据可依"的前提下结合自身知识作答。
    if question_type == "knowledge":
        q = (message or f"{loc or ''} {tgt or ''} 中国铁路").strip()
        station_steps = [step for step in plan if step[0] in ("station.lookup", "cnrail.map")]
        force_station = intent == "station" or bool(_STATION_FACT_RE.search(message or ""))
        if station_steps and force_station:
            # 站名/电报码"看似常识、实为精确查表"：保留站点库，网页搜索只作补充
            # （2026-09-14 修复 E01★/E06：原实现整替换成 web.search → 给不出 AOH）
            plan = station_steps + [("web.search", {"q": q, "limit": 5})]
            plan_notes.append(
                "该问题涉及站点事实（站名/电报码/站台）：已保留站点库精确查询，网页搜索仅作补充"
            )
        else:
            plan = [("web.search", {"q": q, "limit": 5})]
    elif question_type == "mixed":
        q = (message or f"{loc or ''} {tgt or ''} 中国铁路").strip()
        plan = plan + [("web.search", {"q": q, "limit": 5})]

    # ---- 车站档案（rail.mileage）：问"编号/接算站/营业限制/电报码"时补一次本地字典查询 ----
    # 本地缓存命中即毫秒级；未命中才联网抓一次并回填（见 app/data/dict.py）。
    if question_type != "knowledge" and _STATION_PROFILE_RE.search(message or ""):
        profile_station = loc or (od[0] if od else "") or tgt or await _station_from_text(message)
        if profile_station and _station_like(profile_station) and not any(
            step[0] == "rail.mileage" for step in plan
        ):
            plan.append(("rail.mileage", {"station": profile_station}))
            plan_notes.append(
                f"该问法涉及车站档案（编号/接算站/营业限制等）：已查本地字典「{profile_station}」"
            )

    # ---- 车站大屏（station.screen）：命中"大屏/检票口/正晚点"类关键词时补一次车站级查询 ----
    # 只在**这类问法出现时**才调，避免每个车站问题都多打一次 12306（限流敏感）。
    # 知识型问题不调（"大屏怎么用"这类不该打实时接口）。
    _screen_strong = bool(_SCREEN_STRONG_RE.search(message or ""))
    if question_type != "knowledge" and (
        _screen_strong or _SCREEN_WEAK_RE.search(message or "")
    ):
        screen_station = loc or tgt or (od[0] if od else "")
        if screen_station and _station_like(screen_station):
            if not any(step[0] == "station.screen" for step in plan):
                # 时段/车种过滤必须一起下发：本接口一次返回全天 200–700 条，
                # 若只截断前 N 条，"下午有哪些高铁"会拿到凌晨的车（截断冒充缺失的老毛病）。
                after, before = _parse_time_window(message, direction, time_, slots.extra)
                ttype = _parse_train_type(message, slots.extra)
                plan = plan + [("station.screen", {
                    "station": screen_station,
                    "date": time_ or None,
                    "direction": _screen_direction(message),
                    "after_time": after or None,
                    "before_time": before or None,
                    "train_type": ttype or None,
                })]
                applied = "、".join(filter(None, [
                    f"时段{after}-{before}" if after or before else "",
                    f"车种{ttype}" if ttype else "",
                ]))
                plan_notes.append(
                    f"该问法涉及车站到发大屏：已按车站「{screen_station}」查询当日全部到发车次"
                    "（含站台、车底型号、担当客运段）"
                    + (f"，并按 {applied} 过滤" if applied else "")
                )
        elif _screen_strong:
            # 只有"大屏"这类强信号才值得提醒缺站名；"G1 今天晚点了吗"不该被塞这条噪声说明
            plan_notes.append(
                "未发起车站大屏查询：未识别出具体车站；"
                "请补充站名（如「北京南站大屏」）后重查"
            )

    # ---- 工具并发执行（M11.1 成本/延迟治理）----
    # 计划内的工具彼此**无依赖**（各自独立取数），早期串行执行导致
    # 一次 photo_spot 查询最坏要等 4 个外部站点依次超时（实测 ~48s）。
    # 现在并发执行并保持**计划顺序**输出，失败依然不影响整体。
    sem = asyncio.Semaphore(max(1, int(get_settings().tool_concurrency)))

    async def _run_one(name: str, params: dict) -> ToolResult:
        async with sem:
            try:
                # 投机预取命中（同名同参）→ 直接复用，省下一次外部往返
                hit = prefetch.take(name, params) if prefetch is not None else None
                if hit is not None:
                    result = await hit
                    if isinstance(result, ToolResult):
                        return result
                return await registry.invoke_by_name(name, params)
            except Exception as e:  # noqa: BLE001 —— 单个工具异常不得拖垮整体检索
                _log.warning("工具 %s 调用异常: %s: %s", name, type(e).__name__, e)
                return ToolResult(
                    ok=False,
                    error=f"工具执行异常（{type(e).__name__}）",
                    note="该工具执行时出现异常，已跳过",
                )

    results = await asyncio.gather(*(_run_one(n, p) for n, p in plan)) if plan else []

    for (name, _params), result in zip(plan, results):
        ok = isinstance(result, ToolResult) and result.ok
        if ok:
            data.append({
                "tool": name,
                "data": result.data,
                "text": result.text,
                "sources": list(result.sources),
                # 每条事实带自己的时效说明，避免不同工具间互相污染
                "note": result.note,
                # 数据完整性契约：命中/展示/是否截断/过滤条件/采样时刻
                "total": result.total,
                "shown": result.shown,
                "truncated": result.truncated,
                "filters": dict(result.filters or {}),
                "fetched_at": result.fetched_at,
                "integrity": result.integrity_line(),
            })
            sources.extend(result.sources)
        trace.append(f"{name}: {'ok' if ok else 'failed'}")
        if result.note:
            notes.append(f"[{name}] {result.note}")
        if not ok and result.error:
            # 失败原因必须透传：否则生成层只能说"工具调用失败"（实测 F05/C06 被误述）
            notes.append(f"[{name}] 失败原因：{result.error}")

    # 计划层面的说明（"为何没查/缺什么"）与工具说明合并，让生成层如实转述
    note = "；".join(dict.fromkeys(plan_notes + notes)) or (
        "已完成工具调用（部分数据源依赖外部服务，结果见 tool_trace）。"
    )
    return {"data": data, "sources": list(dict.fromkeys(sources)), "tool_trace": trace, "note": note}