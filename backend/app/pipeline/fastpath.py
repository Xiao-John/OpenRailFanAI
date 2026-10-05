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
3. 站名一律用**本地站点库最长匹配**（`rt.station_name_set()`，缓存集合），不靠正则硬切
   （本项目有过 D09「明天下午」被当成站名的前科，以及"换乘车站"被切成"换乘车"的静默失败）。
4. 记录 `reason`（命中的规则），并让流水线把 `planner=deterministic|llm` 透出，
   便于统计快路径误判率、必要时一键关掉（`FASTPATH_ENABLED=false`）。

不做什么：不改任何工具行为、不改作答策略（D06 红线与分类型策略都由下游保持不变）。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from app.od import parse_od
from app.pipeline.extract import Slots
from app.tools import _rt12306 as rt

_log = logging.getLogger("railfan.fastpath")


# ---- 交回 LLM 的结构化原因（只移植决策树里的这一部分）----
#
# 为什么值得单独做：快路径接管时日志里有"命中了哪条规则"，而**没接管时什么都没有**
# —— 只知道"交回 LLM"，不知道是"压根没匹配到问法"还是"问法对上了但缺关键槽位"。
# 用户实测过的那些坑（"十月一日"退化成今天、"京沪线的所有车站"被判成 station）
# 若当时能看到"缺哪个槽位"，定位会快得多。
# 词表与说明沿用那份决策树包的 REASON_ZH，口径保持一致。
REASON_ZH: dict[str, str] = {
    "EMPTY": "输入为空",
    "KNOWLEDGE_OR_OPEN": "知识型/开放型问题：问题性质需模型判断，规则不猜",
    "NO_SLOT": "问法匹配到了，但缺少关键槽位（车次/站名/区间/车型/线路）",
    "TRAIN_STATION_ARRIVAL": "车次 + 具体车站的到发时刻：图定/实际到点口径需模型判断",
    "UNKNOWN_FAMILY": "没有匹配到任何已知问法",
}


@dataclass
class FastPlan:
    """快路径结论。"""

    intent: str
    question_type: str
    slots: Slots
    reason: str                       # 命中的规则说明（日志/回归用）
    matched: list[str] = field(default_factory=list)


@dataclass
class Defer:
    """**为什么**把这一句交回 LLM。

    只在快路径没接管时产生，reason 取 REASON_ZH 的键，detail 是人可读的补充说明。
    有了它，日志里"交回 LLM"才是一条可诊断的信息，而不是一句无从下手的结论。
    """

    reason: str
    detail: str = ""

    @property
    def text(self) -> str:
        """日志用的一行文本。"""
        zh = REASON_ZH.get(self.reason, self.reason)
        return f"{self.reason}（{zh}）" + (f"：{self.detail}" if self.detail else "")


# ---- 意图关键词（每个意图都必须"关键词 + 关键槽位"双命中才接管）----

_ROUTING_RE = re.compile(
    r"(担当|哪组|哪个车组|哪台车|由谁跑|跑哪(?:趟|几趟|些|个)|配属|哪个段|交路|车底)")
# `经过哪些(?:车)?站`：必须容忍"车"夹在中间 —— 漏写它的话「G1经过哪些车站」匹配不上
# （原来的字面量是 `经过哪些站`），会白白多一次 LLM 决策。
# 具体时点词（"几点到/几点开"）：与"车次+车站"同现时属红线段
_TIME_POINT_RE = re.compile(r"(几点|什么时候到|什么时候开|到点|发车时间|到达时间|始发时间)")

_STOPS_RE = re.compile(
    r"(经停|停靠|经过哪些(?:车)?站|途经|站序|历时|全程多久|要多久|几个小时"
    r"|用时|耗时|多久|坐多久"
    r"|几点|什么时候开|什么时候到|发车时间|到达时间|始发时间)")
_SCREEN_RE = re.compile(r"(大屏|出发屏|到达屏|车站车次|检票口|正晚点|晚点)")
# 口语变体要收全：实测「有票么」「还有座位吗」整类漏（只写了"有票吗"）
_TICKET_RE = re.compile(
    r"(余票|还有票|有票|有没有票|买票|抢票|票价|多少钱|一等座|二等座|卧铺|候补"
    r"|有座|座位)")
_LINE_RE = re.compile(
    r"(多少公里|多少千米|几公里|里程|距离|多远|径路|走哪条|线路|有多长|多长|全长|长度)")
_STATION_RE = re.compile(
    r"(电报码|车站编号|TMIS|接算站|营业限制|车站|站名|在哪个城市|属于哪个局"
    r"|几个站台|几台|站台规模|面积|特等站|一等站|二等站|三等站|几等站)")
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
# 非铁路交通词：命中即交回 LLM（不许按火车票/车站查）
_NON_RAIL_RE = re.compile(r"(机票|飞机|航班|机场大巴|大巴|长途汽车|汽车站|客车|打车|网约车|自驾|地铁|公交)")

_KNOWLEDGE_HINT_RE = re.compile(
    r"(为什么|为何|区别|有什么不同|差别|差在哪|怎么来的|咋来的|由来|原理|历史|命名|"
    r"参数|功率|速度是多少|厂家|品牌|关系|介绍一下|科普|怎么样"
    r"|几节|多少节|编组|哪个更)")


def _station_in_text(text: str) -> str:
    """本地站点库最长匹配取站名（与 retrieve._station_from_text 同策略，独立实现避免循环依赖）。"""
    if not text:
        return ""
    names = [n for n in _station_names() if 2 <= len(n) <= 8]
    hits = [n for n in names if n in text]
    return max(hits, key=len) if hits else ""


def _line_in_text(text: str) -> str:
    """从原话里取规范线路名（复用 rail_line 的归一化 + 本地线路表校验）。"""
    from app.data import dict as D
    from app.tools.rail_line import normalize_line_name

    # 候选形态：{2,6} 个汉字 + 可选"高速" + 线/高铁/铁路。后缀「铁路」也要认
    # （「京沪铁路沿线有哪些车站」很常见，而 normalize_line_name 能把
    # 京沪铁路→京沪线、京沪高速铁路→京沪高速线）；安全性由线路表校验兜底。
    #
    # ⚠️ 匹配失败后**只能前进一个字符**，不能从匹配结束处继续。同一个根因踩过两次：
    #   ① `{2,6}` 贪心 → 「陇海线沿线有哪些车站」把 "陇海线沿"+"线" 拼成"陇海线沿线"，
    #      校验不过就整段跳过 → 已改成懒惰；
    #   ② 只改懒惰还不够 —— 「那京沪线呢」「查一下京沪线」「北京到上海走京沪线要多久？」
    #      会从**句首**起匹配出一个更长的候选（"那京沪线"、"查一下京沪线"），
    #      校验不过时 finditer 从该匹配的**末尾**继续，恰好跳过真正的"京沪线"。
    #      实测这三句全部取不到线路名（"…走京沪线要多久？"直接 NO_SLOT 交回 LLM）。
    # 所以改成手动扫描：每次失败只把起点右移一个字符，保证任何位置的候选都被试到。
    pat = re.compile(r"([\u4e00-\u9fa5]{2,6}?(?:高速)?(?:线|高铁|铁路))")
    src = text or ""
    pos = 0
    while pos < len(src):
        m = pat.search(src, pos)
        if not m:
            break
        name = normalize_line_name(m.group(1))
        if name and (D.line_master(name) or D.search_lines(name, 1)):
            return name
        pos = m.start() + 1
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
        if os.environ.get("APP_VARIANT", "main").lower() != "lm" and cand.isdigit():
            before, after = text[:m.start()], text[m.end():]
            if (before[-1:].isdigit() or after[:1].isdigit()
                    or (m.group(0) == cand and re.match(r"[月日号点分:：-]|时(?!刻表)|车组|组", after))
                    or re.search(r"\d[-:：]$", before)):
                continue
        # 不能把车型里的数字当车次：实测「CR400AF 这车型都担当哪些交路？」抠出 "400"，
        # target 变成 "400" 而不是 CR400AF。判据是**词边界**——紧挨着字母的数字属于型号。
        if m.start() > 0 and text[m.start() - 1].isascii() and text[m.start() - 1].isalpha():
            continue
        if m.end() < len(text) and text[m.end()].isascii() and text[m.end()].isalpha():
            continue
        if rt.is_train_code(cand) and cand.upper() not in ("12306",):
            return cand.upper()
    return ""


def _emu_model(text: str) -> str:
    m = re.search(r"(CR[0-9A-Za-z\-]{2,12}|CRH[0-9A-Za-z\-]{1,10})", text or "", re.I)
    return m.group(1).upper() if m else ""


async def rule_slots(text: str) -> dict[str, str | None]:
    """用确定性规则抽取槽位（**供 mock / 兜底复用**，判据与快路径完全一致）。

    为什么把这条单独暴露出来：`llm/_mock.py` 原先自己写了一套弱正则
    （`[A-Z]{1,2}\\d{2,6}` 连 "G1" 都抽不到，区间与站名完全不认），而 mock 模式下
    合并调用走的正是这条抽取路径 —— 结果是 **LLM_MOCK=true 时所有交给 LLM 的问题
    都带着空槽位去检索**，CI 对工具路由零鉴别力（与模块 docstring 里记的
    "13 个工具的路由逻辑从未被执行" 是同一类病）。这里统一到一套判据上，
    避免第二份实现再次漂移。

    为什么是 async：站名匹配依赖站点库，而 `all_stations()` 在库未加载时**返回空字典
    而不是抛异常** —— 调用方一旦忘了先加载，站名会静默变成"没提到站"，比报错危险得多
    （`_station_in_text` 自己的 docstring 也记着这条）。所以这里自己保证加载；
    已加载时只是一次布尔判断，无额外开销。
    """
    t = text or ""
    try:
        await rt.ensure_loaded()
    except Exception as e:  # noqa: BLE001 —— 站点库不可用时退化为"无站名信号"
        _log.warning("站点库加载失败（槽位抽取将没有站名）：%s: %s", type(e).__name__, e)
    try:
        od = parse_od(t)
    except Exception:  # noqa: BLE001
        od = None
    return {
        "location": _station_in_text(t) or None,
        "target": _train_code(t) or _emu_model(t) or None,
        "time": _time_phrase(t) or None,
        "direction": (f"{od[0]}→{od[1]}" if od else None)
        or next((d for d in ("上行", "下行", "方向") if d in t), None),
    }


# ---------------------------------------------------------------- 多轮继承
# 语料实测（tests/corpus/intent_corpus.jsonl，24 条带 history 的用例）：
# 单轮问法接管率 74.4%，多轮只有 13.0%。漏掉的几乎全是**省略句**——
# 「这车呢 / 那明天呢 / 那上海虹桥呢 / 改成上海呢 / 刚才那趟经停哪些站」，
# 句子里没有任何可检索的字面信号，全靠上文消解。它们被交回 LLM，
# 于是每次多付一次 1.2–9.3s 的决策往返（快路径的全部意义就在省掉它）。
_CTX_TURNS = 3          # 最多回看几轮用户消息


def _history_user_turns(history: list[dict] | None) -> list[str]:
    """历史里的**用户消息**，最近的在前。

    为什么只用用户消息、不拿助手回答当槽位来源：用户上一轮亲口说的对象才是
    「这车/那趟」的所指。实测语料里 assistant 文本里的实体常常是另一回事
    （问「G1 今天由哪组动车组担当？」的答句里是 CR400AF-5054，此时用户说
    「那是哪个局的车」问的是车组而不是 G1），拿回答文本继承会张冠李戴。
    """
    out: list[str] = []
    for m in reversed(history or []):
        if str(m.get("role")) != "user":
            continue
        c = str(m.get("content") or "").strip()
        if c:
            out.append(c)
    return out


def _first_of(turns: list[str], fn) -> str:
    """按"最近优先"在历史里取第一个能抽到的值。"""
    for t in turns:
        v = fn(t)
        if v:
            return v
    return ""


def _plausible_od(od: tuple[str, str] | None) -> tuple[str, str] | None:
    """起讫站是否**两端都是真实站名**（按站点库精确匹配）。

    这道校验是**继承**路径的闸门：`parse_od` 历史上会从噪声句里抠出
    ('那趟车现在跑', '哪了') 这类垃圾，不设闸门就可能把上文的噪声当成区间拿去查，
    产出"看起来正常的错答案"。`parse_od` 自己现在也做站点库引导（见 `od._parse_od_guided`），
    两处判据一致；这里保留是因为**站点库不可用时 `parse_od` 会退回纯规则**，
    而继承是我新加的风险面，宁可不继承。
    """
    if not od:
        return None
    names = _station_names()
    if not names:                      # 站点库不可用 → 宁可不继承
        return None
    return od if (od[0] in names and od[1] in names) else None


def _station_names() -> set[str]:
    """全部站名集合（`_rt12306.station_name_set()` 的缓存版）。"""
    try:
        return rt.station_name_set()
    except Exception:  # noqa: BLE001
        return set()


def _first_od(turns: list[str]) -> tuple[str, str] | None:
    for t in turns:
        od = _plausible_od(parse_od(t))
        if od:
            return od
    return None


def _target_of(text: str) -> str:
    """车次/车组的统一取值：**车组号优先于从它内部抠出来的"车次号"**。

    判据与 `_ROUTING_RE` 分支一致（"这个车次号是不是型号串的一部分"）。
    实测：「CR400AF-5054现在跑哪趟？」会被抠出 "5054"、「CR400AF 这车型都担当
    哪些交路？」抠出 "400" —— 都是把型号里的数字当成了车次。
    """
    emu = _emu_model(text)
    train = _train_code(text)
    if emu and (not train or train in emu):
        return emu
    return train or emu


def _station_hits(text: str) -> list[str]:
    """原话里出现的**全部**站名（丢掉被更长站名包含的短名），按长度降序。

    用途是判断"本次到底提到了几个站"。只看 `_station_in_text`（只回最长的一个）
    会出事：实测「深圳北到厦门北今天还有票吗？要高铁的」里两个站名同长，
    `max` 取到先出现的**深圳北**，于是把它当成"本次新给的终点"去替换上文区间，
    得到 **北京南→深圳北** —— 起点当成终点，方向直接错（"看起来正常的错答案"）。
    """
    names = [n for n in _station_names() if 2 <= len(n) <= 8]
    found = [n for n in names if n in (text or "")]
    kept: list[str] = []
    for n in sorted(found, key=len, reverse=True):
        if not any(n in k for k in kept):     # 「北京」被「北京南」包含 → 丢掉
            kept.append(n)
    return kept


def _merge_od(
    own_od: tuple[str, str] | None, station: str, text: str,
    inh_od: tuple[str, str] | None, hits: list[str],
) -> tuple[tuple[str, str] | None, bool]:
    """定本次区间；返回 `(区间, 是否无法确定)`。

    决策表（顺序即优先级）：
      ① 本次给了**可信**区间 → 直接用（可信 = 两端都是站点库里的真实站名，
         或者本次只提到一个站名 —— 单站句里的解析结果无从"多解"）；
      ② 本次提到**多个**站名却解析不出区间 → 说不清换的是哪一个 → 标为无法确定
         （`parse_od` 对「深圳北到厦门北今天还有票吗？要高铁的」会直接返回 None，
         对「有没有通宵的车从西安到兰州」会给出 junk 起点）；
      ③ 本次只提一个站名 + 上文有区间 → 「从X」换起点，否则换终点；
      ④ 其余 → 用上文的区间。
    """
    if own_od and (_plausible_od(own_od) or len(hits) <= 1):
        return own_od, False
    if len(hits) > 1:
        return None, True
    if station and inh_od:
        if re.search(r"从\s*" + re.escape(station), text):
            return (station, inh_od[1]), False
        return (inh_od[0], station), False
    return inh_od, False


async def _inherit_plan(turns: list[str]) -> FastPlan | None:
    """在历史用户消息里找最近一条**能用规则判定**的，取它的意图/问题性质/槽位。

    递归深度固定为 1（`history=None`）：只继承一轮，不做套娃式传递 ——
    「那明天呢 → 那后天呢」这种链式承接的口径本就该由模型判断。
    """
    for t in turns[:_CTX_TURNS]:
        fp, _ = await plan_with_reason(t, None)
        if fp is not None:
            return fp
    return None


# 这些意图下"区间"才是正确的槽位落点（与各自分支的既有约定一致：
# ticket/rail_line 用 direction，schedule/station/photo_spot 用 location）
_OD_INTENTS = frozenset({"ticket", "rail_line"})


def _merge_inherited(
    inh: FastPlan, own_time: str, own_target: str, own_line: str,
    station: str, text: str,
) -> Slots | None:
    """把本次新增的槽位合并进继承来的槽位；**无法唯一确定时返回 None（交回模型）**。

    返回 None 的情形有两种，都属于"说了但说不清"：

    **① 时间口径冲突。** 实测「那下午还有吗」承接「G1明天上午还有二等座吗？」——
    本次只能确定"下午"，日期只能用上文的"明天"，而 `_time_phrase` 对裸时段会退回
    "今天"，直接覆盖就会把**明天**的问句答成**今天**的余票。

    **② 区间说不清。** 本次提到多个站名却没给出可信区间（见 `_merge_od` 的决策表）：
    「深圳北到厦门北今天还有票吗？要高铁的」`parse_od` 直接返回 None，
    「有没有通宵的车从西安到兰州」给出 junk 起点（'有没有通宵的车从西安'）——
    这两种情况下"拿上文的区间凑一个"会产出方向错的答案。

    这类只能交给模型：规则的职责是"不确定就别猜"。
    """
    merged = dict(inh.slots.non_empty())
    if own_time and merged.get("time") and own_time != merged["time"]:
        return None
    if own_time:
        merged["time"] = own_time
    if own_target:
        merged["target"] = own_target
    elif own_line and inh.intent in _OD_INTENTS:
        merged["target"] = own_line

    hits = _station_hits(text)

    if inh.intent in _OD_INTENTS:
        od, amb = _merge_od(parse_od(text), station, text,
                            _plausible_od(parse_od(str(merged.get("direction") or ""))),
                            hits)
        if amb:
            # 本次提到多个站名却没给出可信区间：说不清它换的是哪一个。
            # 绝不能"用继承来的区间凑一个"——那会得到 北京南→深圳北 这种方向错的答案。
            return None
        merged["direction"] = f"{od[0]}→{od[1]}" if od else None
        merged["location"] = None
    elif station:
        merged["location"] = station

    return Slots(
        location=merged.get("location"),
        target=merged.get("target"),
        time=merged.get("time"),
        direction=merged.get("direction"),
        extra=merged.get("extra"),
    )


async def plan(message: str, history: list[dict] | None = None) -> FastPlan | None:
    """兼容入口：只要结论（接管方案或 None）。要"为什么交回 LLM"时用 plan_with_reason。

    ⚠️ 测试里"关掉快路径"请 patch **plan_with_reason**（planner 的唯一入口）。
    patch 本函数不会有任何效果 —— 它只是 plan_with_reason 的薄包装。
    （改这个分层时踩过：三个测试文件原本 patch 的是 plan，入口一改就静默失效，
    表现为"stub 不生效、真的去调了模型"。）
    """
    fp, _defer = await plan_with_reason(message, history)
    return fp


async def plan_with_reason(
    message: str, history: list[dict] | None = None
) -> tuple[FastPlan | None, "Defer | None"]:
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
        return None, Defer("EMPTY")
    if _KNOWLEDGE_HINT_RE.search(text):    # 知识型/开放型：问题性质需模型判断，不接管
        return None, Defer("KNOWLEDGE_OR_OPEN")

    # 非铁路交通：绝不能按火车票/车站去查。
    # 实测危害：「明天北京到上海的机票多少钱？」被接管成 ticket（区间还解析得好好的）
    # —— 用户拿到的是**看起来正常的错答案**，比"查不到"更糟。
    if _NON_RAIL_RE.search(text):
        return None, Defer("OUT_OF_SCOPE", f"含非铁路交通词「{_NON_RAIL_RE.search(text).group(0)}」")

    # 多轮继承：本次没给车次/站名/区间时，从最近的用户消息里补齐（指代消解）。
    # 顺序是**本次优先、历史兜底**——「那从南京走呢」的站名必须取本次的南京，
    # 而车次取上文的 G35。
    turns = _history_user_turns(history)
    recent = turns[:_CTX_TURNS]

    own_target = _train_code(text) or _emu_model(text)
    own_station = _station_in_text(text)
    own_hits = _station_hits(text)          # 本次到底提到了几个站（判区间歧义用）
    own_time = _time_phrase(text)
    own_line = _line_in_text(text)
    own_od = parse_od(text) or None

    train = _target_of(text) or _first_of(recent, _target_of)
    # 站名槽位同样要支持多轮继承：实测「那上海南呢，经停吗」承接上文后
    # location 应为 上海南，实际是 None —— 因为 ctx_text 只喂给了车次与时间。
    station = own_station or _first_of(recent, _station_in_text)
    line = own_line or _first_of(recent, _line_in_text)
    time_phrase = own_time or _first_of(recent, _time_phrase)
    # 区间：本次给了就用本次；本次只给了一个站名 + 上文有区间 → 按「从X / X呢」
    # 判定替换的是起点还是终点（见 `_merge_od` 的决策表）。本次提到多个站名却
    # 解析不出可信区间时 `od` 为 None —— 需要区间的问法自然会走到 NO_SLOT 交回模型，
    # 不会拿着半截区间去查。
    od, _od_amb = _merge_od(own_od, own_station, text, _first_od(recent), own_hits)

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
    # 日志里要能一眼看出"哪几个槽位是承上文的"——否则误继承会被当成模型判错来查
    inherited = [f"{lb} {own_v!r}→{got!r}" for lb, own_v, got in (
        ("车次", own_target, train), ("站名", own_station, station),
        ("线路", own_line, line), ("时间", own_time, time_phrase),
    ) if got and not own_v]
    if inherited:
        matched.append("承上文：" + "、".join(inherited))

    def slots(**kw) -> Slots:
        base = dict(location=None, target=None, time=time_phrase or None, direction=None, extra=None)
        base.update(kw)
        return Slots(**base)

    # ---- 1) 担当/交路（需要车次或车组号）----
    if _ROUTING_RE.search(text):
        # 车组号（CR400AF-5054）/车型（CR400AF）优先于从它内部抠出来的"车次号"：
        # 判据是"这个车次号是不是车型串的一部分"。真的同时给了车次（"G1 由 CR400AF 担当"）
        # 时，train 不在 emu_model 里，仍然按车次走 —— 车次才是主语。
        _emu = _emu_model(text)
        if _emu and (not train or train in _emu):
            return FastPlan("emu_routing", "realtime", slots(target=_emu), "担当+车型", matched), None
        if train:
            return (FastPlan("emu_routing", "realtime", slots(target=train), "担当+车次", matched), None)
        emu_model = _emu_model(text)
        if emu_model and rt.is_emu_train_code(emu_model):
            return (FastPlan("emu_routing", "realtime", slots(target=emu_model), "担当+车次", matched), None)
        model = _emu_model(text)
        if model:                          # 车型（如 CR400AF）→ rail.re 按车型反查
            return (FastPlan("emu_routing", "realtime", slots(target=model), "担当+车型", matched), None)
        # 既无车次也无车型 → 交回 LLM（可能要追问）：问法对上了，缺的是槽位
        return None, Defer("NO_SLOT", "命中「担当/交路」问法但缺车次或车型")

    # ---- 2) 经停/历时（需要车次）----
    # 红线段：**车次 + 具体车站**的到发时刻不接管。
    # 原因是口径而非能力：12306 给的是图定时刻，用户问的可能是实际到点，
    # 这个区分必须由模型结合上下文判断（R1 D06 红线）。缺车站的"G1今天几点开"不在此列。
    if _STOPS_RE.search(text) and train and station and _TIME_POINT_RE.search(text):
        return None, Defer("TRAIN_STATION_ARRIVAL",
                           f"车次 {train} + 车站「{station}」的到发时刻")

    if _STOPS_RE.search(text) and train:
        # 带上 location：多轮里"那上海南呢，经停吗"要能继承出上海南，
        # 否则继承到的站名无处安放（实测 location 一直是 None）
        return (FastPlan("schedule", "realtime", slots(target=train, location=station),
                         "经停+车次", matched), None)

    # ---- 3) 车站大屏 / 检票口 ----
    if _SCREEN_RE.search(text) and station:
        return (FastPlan("station", "realtime", slots(location=station), "大屏+站名", matched), None)

    # ---- 4) 余票/票价（需要起讫站）----
    if _TICKET_RE.search(text) and od:
        return (FastPlan("ticket", "realtime",
                        slots(direction=f"{od[0]}→{od[1]}"), "余票+区间", matched), None)

    # ---- 5) 里程/径路（区间或线路）----
    if _LINE_RE.search(text) and (od or line):
        return (FastPlan("rail_line", "realtime",
                        slots(target=line or None, direction=f"{od[0]}→{od[1]}" if od else None),
                        "里程/径路+区间或线路", matched), None)

    # ---- 5b) 线路的沿线车站（需要线路名）----
    # 实测缺陷：'京沪线的所有车站' 被 LLM 判成 station，于是去调 station.lookup('京沪线')
    # —— 拿线路名当站名查，必然失败；用户拿到的是"本次无法给出完整列表"。
    # 同一件事换个说法（'京沪线经过哪些车站'）却又判成 rail_line。既然口径可以由
    # "线路名 + 车站清单词"完全确定，就不该交给模型猜（顺带省掉两次 LLM 往返）。
    if line and _LINE_STATIONS_RE.search(text):
        return (FastPlan("rail_line", "realtime", slots(target=line), "沿线车站+线路", matched), None)

    # ---- 6) 车站信息（站名 + 车站类关键词）----
    if _STATION_RE.search(text) and station:
        return (FastPlan("station", "realtime", slots(location=station), "车站信息+站名", matched), None)

    # ---- 7) 拍摄点（地点 + 拍摄词；目标车型可选）----
    if _PHOTO_RE.search(text) and (station or od):
        loc = station or (od[0] if od else "")
        return (FastPlan("photo_spot", "realtime",
                        slots(location=loc, target=_emu_model(text) or None), "拍摄+地点", matched), None)

    # 兜底原因：区分"完全没匹配到问法"与"问法对上了但缺关键槽位"。
    # 后者对排查最有用 —— 它直接指出该补哪一类说法或哪个槽位。
    # 注意这里**先记下来、最后才返回**：第 8 步的"多轮承接"要能救回这类问句，
    # 救不回时再按原样交出（原实现是命中即 return）。
    marked_no_slot = False
    no_slot_reason: Defer | None = None
    for label, kw, ok, slot in (
        ("大屏", _SCREEN_RE, station, "站名"),
        ("余票", _TICKET_RE, od, "区间"),
        ("里程/径路", _LINE_RE, (od or line), "区间或线路名"),
        ("沿线车站", _LINE_STATIONS_RE, line, "线路名"),
        ("车站信息", _STATION_RE, station, "站名"),
        ("拍摄点", _PHOTO_RE, (station or od), "地点"),
        ("经停", _STOPS_RE, train, "车次"),
    ):
        if kw.search(text) and not marked_no_slot:
            marked_no_slot = True
            no_slot_reason = Defer("NO_SLOT", f"命中「{label}」问法但缺{slot}")

    # ---- 8) 多轮承接：本次没匹配到任何问法，但**明确带着新的槽位**----
    # 「那明天呢 / 那上海虹桥呢 / 那京沪线呢 / 改成上海呢 / 那走京广高速线呢」这类
    # 省略句，意图只能来自上文（规则判不出，只能判"是个承接"）。出手条件故意很严：
    #   ① 本次必须自带**至少一个新槽位** —— 否则就是「这车呢 / 那趟呢」这种纯指代，
    #      指的是哪个对象、问的是什么都有歧义（语料里同一句「这车呢」承接不同上文
    #      期望的意图并不相同），这种歧义必须交回模型，不能猜；
    #   ② 上文里要有**能被规则判定**的一轮，否则没有可继承的意图；
    #   ③ 时间口径冲突时不合并（见 `_merge_inherited`）。
    # 三条都满足才接管，任一条不满足就保持原来的交出理由 —— 宁慢勿错。
    #
    # 注意"新槽位"里**不含本次解析出的 od**：`parse_od` 只在句子里找"X到Y"的形态，
    # 会把「那趟车现在跑到哪了」解析成 ('那趟车现在跑','哪了')。拿它当"这次带了新区间"
    # 的凭据，等于让噪声触发接管。真正的区间问法一定同时带站名（会命中 _station）。
    own_slots = [v for v in (own_target, own_station, own_line, own_time) if v]
    if own_slots and turns:
        inh = await _inherit_plan(turns)
        if inh is not None:
            merged = _merge_inherited(inh, own_time, own_target, own_line, own_station, text)
            if merged is not None:
                return (FastPlan(inh.intent, inh.question_type, merged,
                                 f"承接上文（{inh.reason}）+本次槽位", matched), None)

    if marked_no_slot:
        return None, no_slot_reason
    return None, Defer("UNKNOWN_FAMILY")
