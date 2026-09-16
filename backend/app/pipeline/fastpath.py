"""确定性快路径（perf P0-1）：正则/本地索引直接产出 `意图 + 槽位`，**不打 LLM**。

为什么需要
----------
实测（`docs/EXPL.md 的性能一节`）：一次问答的"预筛选"耗时 3.8–11.6s，而且**几乎全花在两次
LLM 往返**（意图分类 + 槽位抽取）上——检索层的工具调用只占 0.02–2.1s。
其中约 56–68% 的车迷问法其实**结构清晰、可正则判定**（"G1经停哪些站""明天北京到上海
还有票吗""北京南站大屏下午有哪些高铁"），完全不必花两次模型调用去猜。

设计原则（安全第一）
--------------------
1. **只在高置信模式命中时走快路径**：必须同时（a）命中意图关键词、（b）拿到支撑该意图的
   关键槽位（车次号 / 起讫站 / 站名 / 线路名）。任何一条不满足 → 返回 None，交回 LLM。
2. **知识型/开放型问题一律不接管**（"CR400AF 为什么叫复兴号""两者的区别"）：无法用规则判断
   问题性质，交回 LLM 更安全。
3. 站名一律用**本地站点库最长匹配**（`rt.all_stations()`），不靠正则硬切
   （本项目有过 D09「明天下午」被当成站名的前科，以及"换乘车站"被切成"换乘车"的静默失败）。
4. 记录 `reason`（命中的规则），并让流水线把 `planner=deterministic|llm` 透出，
   便于统计快路径误判率、必要时一键关掉（`FASTPATH_ENABLED=false`）。

不做什么：不改任何工具行为、不改作答策略（D06 红线与分类型策略都由下游保持不变）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.od import parse_od
from app.pipeline.extract import Slots
from app.tools import _rt12306 as rt

_log = logging.getLogger("railfan.fastpath")


@dataclass
class FastPlan:
    """快路径结论。"""

    intent: str
    question_type: str
    slots: Slots
    reason: str                       # 命中的规则说明（日志/回归用）
    matched: list[str] = field(default_factory=list)


# ---- 意图关键词（每个意图都必须"关键词 + 关键槽位"双命中才接管）----

_ROUTING_RE = re.compile(r"(担当|哪组|哪个车组|哪台车|由谁跑|交路|车底)")
# `经过哪些(?:车)?站`：必须容忍"车"夹在中间 —— 漏写它的话「G1经过哪些车站」匹配不上
# （原来的字面量是 `经过哪些站`），会白白多一次 LLM 决策。
_STOPS_RE = re.compile(r"(经停|停靠|经过哪些(?:车)?站|途经|站序|历时|全程多久|要多久|几个小时)")
_SCREEN_RE = re.compile(r"(大屏|出发屏|到达屏|车站车次|检票口|正晚点|晚点)")
_TICKET_RE = re.compile(r"(余票|还有票|有票吗|有没有票|买票|抢票|票价|多少钱|一等座|二等座|卧铺|候补)")
_LINE_RE = re.compile(r"(多少公里|多少千米|几公里|里程|距离|多远|径路|走哪条|线路)")
_STATION_RE = re.compile(r"(电报码|车站编号|TMIS|接算站|营业限制|车站|站名|在哪个城市|属于哪个局)")
# 问"一条线路经过/沿线有哪些车站" —— 这是**以线路为口径**的问题，属于 rail_line，
# 不能因为句子里有"车站"二字就判成 station（station 是问**某一座车站**本身的信息）。
# 与 _STATION_RE 的区别就在于此：_STATION_RE 必须再配上具体站名才会命中（见规则 6）。
_LINE_STATIONS_RE = re.compile(
    r"(所有车站|全部车站|全线车站|沿线车站|沿途车站"
    r"|经过哪些(?:车)?站|途经哪些(?:车)?站|经过的(?:车)?站|停靠哪些(?:车)?站"
    r"|有哪些(?:车)?站|多少个(?:车)?站|多少站"
    r"|车站列表|站点列表|站名列表|站序)"
)
_PHOTO_RE = re.compile(r"(拍|摄影|机位|蹲守|取景|拍到)")
_KNOWLEDGE_HINT_RE = re.compile(
    r"(为什么|为何|区别|有什么不同|原理|历史|由来|命名|参数|功率|速度是多少|厂家|品牌|关系|介绍一下|科普|怎么样)")


def _station_in_text(text: str) -> str:
    """本地站点库最长匹配取站名（与 retrieve._station_from_text 同策略，独立实现避免循环依赖）。"""
    if not text:
        return ""
    try:
        names = [n for n in rt.all_stations().keys() if 2 <= len(n) <= 8]
    except Exception:  # noqa: BLE001
        return ""
    hits = [n for n in names if n in text]
    return max(hits, key=len) if hits else ""


def _line_in_text(text: str) -> str:
    """从原话里取规范线路名（复用 rail_line 的归一化 + 本地线路表校验）。"""
    from app.data import dict as D
    from app.tools.rail_line import normalize_line_name

    # `{2,6}?` 必须**懒惰**匹配。原来写的是贪心 `{2,6}`，于是它先吃到最长的候选：
    # 「陇海线沿线有哪些车站」里 "陇海线沿" + "线" 被拼成 "陇海线沿线"，校验不通过就
    # 整段跳过，而 finditer 从匹配结束处继续 —— 真正的 "陇海线" 再没机会被试到。
    # 结果是所有带"沿线 / 全线"的说法都取不到线路名（实测：''），
    # 连带 rail_line 的整条分支都进不去。
    # 后缀也要认「铁路」：「京沪铁路沿线有哪些车站」这种说法很常见，
    # 而 normalize_line_name 本来就能把 京沪铁路→京沪线、京沪高速铁路→京沪高速线。
    # 安全性由后面的线路表校验兜底（"中国铁路"这类会被过滤掉）。
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,6}?(?:高速)?(?:线|高铁|铁路))", text or ""):
        raw = m.group(1)
        name = normalize_line_name(raw)
        if name and (D.line_master(name) or D.search_lines(name, 1)):
            return name
    return ""


def _time_phrase(text: str) -> str:
    """取时间表述（用于 slots.time；retrieve/generate 会再规范化）。"""
    # 注意带上紧随其后的时段词（"今天下午"），否则下游只看到"今天"。
    m = re.search(
        r"(今天|今日|昨天|昨日|明天|明日|后天|大后天|今晚|今早|明早|明晚|这周末|本周[一二三四五六日天]|"
        r"下周[一二三四五六日天]|上个?月\d{1,2}[日号]|\d{1,2}月\d{1,2}[日号]|\d{4}-\d{2}-\d{2})"
        r"(早上|上午|中午|下午|傍晚|晚上|凌晨)?",
        text or "")
    if m:
        return (m.group(1) or "") + (m.group(2) or "")
    if re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})|(上午|下午|晚上|凌晨|早上)", text or ""):
        return "今天"                      # 只给时段没给日期 → 按今天
    return ""


def _train_code(text: str) -> str:
    for m in re.finditer(r"(0?[GDCTZKYSLBN]\d{1,5}[A-Z]?|\d{1,4})(?:次|列车)?", text or "", re.I):
        cand = m.group(1)
        if rt.is_train_code(cand) and cand.upper() not in ("12306",):
            return cand.upper()
    return ""


def _emu_model(text: str) -> str:
    m = re.search(r"(CR[0-9A-Za-z\-]{2,12}|CRH[0-9A-Za-z\-]{1,10})", text or "", re.I)
    return m.group(1).upper() if m else ""


async def plan(message: str, history: list[dict] | None = None) -> FastPlan | None:
    """尝试用确定性规则给出 (intent, question_type, slots)；判断不了返回 None。

    设为 async 的原因：站名要用**本地站点库**最长匹配，而站点库是包内静态资源需先加载
    （`rt.ensure_loaded()`，实测 ~0ms、不联网）。之前把加载放在调用方，一旦有调用方忘了加载，
    站名匹配就会静默失效、白白丢掉快路径命中率——所以加载放在这里最稳妥。
    """
    text = (message or "").strip()
    try:
        await rt.ensure_loaded()
    except Exception as e:  # noqa: BLE001 —— 站点库不可用时退化为"无站名信号"，不影响其它规则
        _log.warning("站点库加载失败：%s: %s", type(e).__name__, e)
    if not text or len(text) > 120:        # 过长/成分复杂的句子交回 LLM
        return None
    if _KNOWLEDGE_HINT_RE.search(text):    # 知识型/开放型：问题性质需模型判断，不接管
        return None

    # 多轮继承：本次没给车次/站名时，从最近一条用户消息里继承（指代消解）
    ctx_text = " ".join(str(m.get("content") or "") for m in (history or [])
                        if str(m.get("role")) == "user")[-200:]

    train = _train_code(text) or _train_code(ctx_text)
    od = parse_od(text) or None
    station = _station_in_text(text)
    line = _line_in_text(text)
    time_phrase = _time_phrase(text) or _time_phrase(ctx_text)
    matched: list[str] = []
    if train:
        matched.append(f"车次={train}")
    if od:
        matched.append(f"OD={od[0]}→{od[1]}")
    if station:
        matched.append(f"站名={station}")
    if line:
        matched.append(f"线路={line}")
    if time_phrase:
        matched.append(f"时间={time_phrase}")

    def slots(**kw) -> Slots:
        base = dict(location=None, target=None, time=time_phrase or None, direction=None, extra=None)
        base.update(kw)
        return Slots(**base)

    # ---- 1) 担当/交路（需要车次或车组号）----
    if _ROUTING_RE.search(text):
        if train:
            return FastPlan("emu_routing", "realtime", slots(target=train), "担当+车次", matched)
        emu_model = _emu_model(text)
        if emu_model and rt.is_emu_train_code(emu_model):
            return FastPlan("emu_routing", "realtime", slots(target=emu_model), "担当+车次", matched)
        model = _emu_model(text)
        if model:                          # 车型（如 CR400AF）→ rail.re 按车型反查
            return FastPlan("emu_routing", "realtime", slots(target=model), "担当+车型", matched)
        return None                        # 既无车次也无车型 → 交回 LLM（可能要追问）

    # ---- 2) 经停/历时（需要车次）----
    if _STOPS_RE.search(text) and train:
        return FastPlan("schedule", "realtime", slots(target=train), "经停+车次", matched)

    # ---- 3) 车站大屏 / 检票口 ----
    if _SCREEN_RE.search(text) and station:
        return FastPlan("station", "realtime", slots(location=station), "大屏+站名", matched)

    # ---- 4) 余票/票价（需要起讫站）----
    if _TICKET_RE.search(text) and od:
        return FastPlan("ticket", "realtime",
                        slots(direction=f"{od[0]}→{od[1]}"), "余票+区间", matched)

    # ---- 5) 里程/径路（区间或线路）----
    if _LINE_RE.search(text) and (od or line):
        return FastPlan("rail_line", "realtime",
                        slots(target=line or None, direction=f"{od[0]}→{od[1]}" if od else None),
                        "里程/径路+区间或线路", matched)

    # ---- 5b) 线路的沿线车站（需要线路名）----
    # 实测缺陷：'京沪线的所有车站' 被 LLM 判成 station，于是去调 station.lookup('京沪线')
    # —— 拿线路名当站名查，必然失败；用户拿到的是"本次无法给出完整列表"。
    # 同一件事换个说法（'京沪线经过哪些车站'）却又判成 rail_line。既然口径可以由
    # "线路名 + 车站清单词"完全确定，就不该交给模型猜（顺带省掉两次 LLM 往返）。
    if line and _LINE_STATIONS_RE.search(text):
        return FastPlan("rail_line", "realtime", slots(target=line), "沿线车站+线路", matched)

    # ---- 6) 车站信息（站名 + 车站类关键词）----
    if _STATION_RE.search(text) and station:
        return FastPlan("station", "realtime", slots(location=station), "车站信息+站名", matched)

    # ---- 7) 拍摄点（地点 + 拍摄词；目标车型可选）----
    if _PHOTO_RE.search(text) and (station or od):
        loc = station or (od[0] if od else "")
        return FastPlan("photo_spot", "realtime",
                        slots(location=loc, target=_emu_model(text) or None), "拍摄+地点", matched)

    return None
