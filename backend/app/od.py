"""起讫站（OD）解析工具。

用于把用户口语化的区间表述解析为 (出发站, 到达站)：
- "北京到上海" / "北京→上海" / "北京-上海" / "北京至上海"
- "从北京去上海" / "北京去上海"
- "北京 上海"（有明确分隔时）

槽位抽取常把 OD 放进 `direction`（如"北京到上海"），
因此余票/时刻路由需要能识别这种形式。
"""
from __future__ import annotations

import re

# 分隔符：到 / 至 / → / -> / - / — / ~ / 去 （"去"需前后有站名）
_OD_PATTERNS = [
    re.compile(r"^(?P<f>[\u4e00-\u9fa5A-Za-z]{2,10}?)\s*(?:到|至|→|->|—|－|-|~|～)\s*(?P<t>[\u4e00-\u9fa5A-Za-z]{2,10})$"),
    re.compile(r"^从(?P<f>[\u4e00-\u9fa5A-Za-z]{2,10}?)\s*去\s*(?P<t>[\u4e00-\u9fa5A-Za-z]{2,10})$"),
    re.compile(r"^(?P<f>[\u4e00-\u9fa5A-Za-z]{2,10}?)\s*去\s*(?P<t>[\u4e00-\u9fa5A-Za-z]{2,10})$"),
]

# 常见噪声后缀（站名后可能跟"站"）
_NOISE = ("站", "市", "高铁", "动车", "火车", "列车")

# 句末噪声：问号/句号/语气词与追问短语，会让"北京到上海走哪条线路？"整句匹配失败（R1 F01）
_TAIL_PUNCT_RE = re.compile(r"[\s？?。！!，,、；;：:~～—－\-]+$")
_TAIL_NOISE_RE = re.compile(
    r"(走哪条(?:线|线路|路)?|怎么走|怎么去|如何去?|几点(?:开|发车|到)?|多少公里|多远|"
    r"要多久|多长时间|呢|吗|吧|啊|的|)[\s？?。！!]*$"
)

# 疑问/追问词：几乎不可能出现在站名内部，**两端**都可以安全截断
_Q_WORDS = ("怎么", "如何", "走哪", "几点", "多少", "多远", "要多久", "多长时间", "请问")

# 尾部残留词：席别/车种/票务词与语气词。
# ⚠️ **只对终点使用**。这些词出现在站名内部是完全正常的 —— 实测踩过：
# 旧实现把 "都" 也当停用词用在起点上，「成都东到重庆西卧铺还有吗」的起点被截成 "成"，
# **起点被截断、终点被拉长，方向直接反了**，比"查不到"更糟。
_TAIL_JUNK = ("还有", "有票", "有座", "余票", "多少钱", "票价", "车票", "的车",
              "一等座", "二等座", "商务座", "无座", "硬座", "软座",
              "硬卧", "软卧", "动卧", "卧铺", "高铁", "动车", "普速",
              "直达", "特快", "快速", "城际", "发车", "到达",
              # 运输方式前的动词：「北京到上海坐高铁要多长时间？」剥掉句末的
              # "要多长时间"后剩 "上海坐高铁"，在"高铁"处截断会留下"坐"，
              # 终点变成 **"上海坐"** —— 一个不存在的站，查询必然落空
              # （实测：该问句今天会被接管成 rail_line 并带 direction=北京→上海坐）。
              # 站点库里没有任何站名含这三个字，可安全作为截断词。
              "坐", "乘", "搭",
              "呢", "吗", "吧", "啊", "的", "都", "有", "开")

# 句首填充语：帮我/我要/查询…（实测「帮我候补一张明天北京到广州的硬卧」→ 起点应为"北京"）
_LEAD_FILLER_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|我要|我想|我|要|候补|一张|两张|三张|查一下|查|查询|"
    r"看下|看看|给我|顺便|了解一下|从|自)[\s,，、]*")

# 时间/时段表述**不是站名**：句子里常出现"明天下午到上海的高铁"，
# 若不拦，"明天下午"会被当成出发站（实测 D09 回归）。
_TIMEISH_RE = re.compile(
    r"^(?:今天|今日|明天|明日|昨天|昨日|前天|后天|大前天|大后天|"
    r"本[周月日]|这[周月日]|下[周月日]|上[周月日]|周[一二三四五六日天]|星期[一二三四五六日天]|周末|"
    r"凌晨|早上|早晨|上午|中午|下午|晚上|晚间|夜里|夜间|傍晚|"
    r"今年|明年|去年|\d+[点时]|\d+月\d+[日号])"
)


def _is_timeish(v: str) -> bool:
    return bool(_TIMEISH_RE.match((v or "").strip()))


# 句首时间词（"明天北京到上海还有票吗" → 先剥掉"明天"再解析区间）
_LEAD_TIME_RE = re.compile(
    r"^(?:今天|今日|明天|明日|昨天|昨日|前天|后天|大前天|大后天|"
    r"本[周月日]|这[周月日]|下[周月日]|上[周月日]|周[一二三四五六日天]|星期[一二三四五六日天]|周末|"
    r"凌晨|早上|早晨|上午|中午|下午|晚上|晚间|夜里|夜间|傍晚|今年|明年|去年)"
    r"[\s,，、]*"
)


def _cut_at_first(v: str, words) -> str:
    """在 v 中最早出现的词处截断（只截"词前还有内容"的情况）。"""
    cut = len(v)
    for w in words:
        i = v.find(w)
        if 0 < i < cut:
            cut = i
    return v[:cut].strip() or v


def _trim_origin(v: str) -> str:
    """起点：只切疑问词，**绝不**切"都/开/有/的"这类词。

    「成都东」里就有"都" —— 把它们当停用词用在起点上会把站名截断（实测事故）。
    """
    return _cut_at_first((v or "").strip(), _Q_WORDS)


def _trim_dest(v: str) -> str:
    """终点：疑问词 + 尾部残留（席别/车种/票务/语气词）都要切。"""
    return _cut_at_first(_trim_origin(v), _TAIL_JUNK)


def _clean_station(v: str) -> str:
    v = (v or "").strip()
    # 只去掉尾部的运输方式/泛称，保留"站/市"交由站点解析降级处理
    for noise in ("高铁", "动车", "火车", "列车"):
        if v.endswith(noise) and len(v) > len(noise):
            v = v[: -len(noise)]
    return v.strip()


def parse_od(text: str | None, station_ok=None) -> tuple[str, str] | None:
    """解析区间表述为 (出发站, 到达站)；无法识别返回 None。

    `station_ok`：可选的"这是不是一个真实站名"谓词。默认自动取本地站点库，
    取到就启用**引导式解析**（见 `_parse_od_guided`）。站点库不可用时行为与
    旧实现完全一致（纯字符串规则），所以无网络的单测里跑的就是旧路径。
    """
    if station_ok is None:
        station_ok = _default_station_ok()
    legacy = _parse_od_rules(text)
    if station_ok is None:
        return legacy
    # 规则解出来的结果**两端都确实是站名**时才采信：绝大多数正常问法走这条，
    # 行为与旧实现一致；只有规则解出垃圾（或解不出）时才去做候选搜索。
    # 这么排是为了"能不动就不动"——引导式只用来补规则的漏与错。
    if legacy and station_ok(legacy[0]) and station_ok(legacy[1]):
        return legacy
    return _parse_od_guided(text, station_ok) or legacy


def _default_station_ok():
    """默认谓词：本地站点库（懒加载；不可用返回 None → 退回纯规则解析）。

    懒加载而不是模块级 import：`app/od.py` 是纯字符串工具，被大量无网络单测
    直接调用，不该在 import 期就牵出 httpx/mcp 那条链。
    """
    try:
        from app.tools._rt12306 import station_name_set

        names = station_name_set()
    except Exception:  # noqa: BLE001
        return None
    return (names.__contains__ if names else None)


def _strip_edges(s: str) -> str:
    """剥掉句首填充语/时间词与句末追问短语/标点（原来内联在 parse_od 里的四步）。"""
    s = _LEAD_FILLER_RE.sub("", s).strip()     # 句首填充语（帮我/我要/候补一张…）
    s = _LEAD_TIME_RE.sub("", s).strip()       # 句首时间词（明天/下周三…）
    s = _TAIL_NOISE_RE.sub("", s).strip()      # 句末追问短语（走哪条线路/怎么走…）
    s = _TAIL_PUNCT_RE.sub("", s).strip()      # 句末标点
    return s


def _parse_od_rules(text: str | None) -> tuple[str, str] | None:
    """原有实现：整串匹配 + 剥句末杂词（保留不动，作为默认路径与最终兜底）。"""
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None

    # 先剥离句末标点与追问短语，再匹配（否则"北京到上海走哪条线路？"整句不匹配 → 退化成网页搜索）
    prev = None
    while prev != s:
        prev = s
        s = _strip_edges(s)
    if not s:
        return None

    for pat in _OD_PATTERNS:
        m = pat.match(s)
        if m:
            f = _clean_station(_trim_origin(m.group("f")))
            t = _clean_station(_trim_dest(m.group("t")))
            # 时间/时段表述不能当站名（"明天下午到上海的高铁" → 出发站不该是"明天下午"）
            if _is_timeish(f) or _is_timeish(t):
                continue
            # 同站对（"北京到北京"）也如实返回：由工具给出"发站与到站相同"的明确结论，
            # 而不是当作"解析失败"退化成网页搜索（修复 F07）
            if f and t:
                return f, t
    return None


# 引导式解析的分隔符：在"到/至/箭头/横杠/波浪/去"处**逐个**试切
_GUIDED_SEP_RE = re.compile(r"(?:到|至|→|->|—|－|-|~|～|去)")
_MAX_NAME_LEN = 12      # 站名长度上限（实际最长 8，留余量给"XX东"这类）


def _suffix_station(s: str, ok) -> str:
    """`s` 中最长的**后缀**且是真实站名（「有没有通宵的车从西安」→ 西安）。"""
    s = _LEAD_FILLER_RE.sub("", (s or "").strip()).strip()
    for n in range(min(len(s), _MAX_NAME_LEN), 1, -1):
        if ok(s[-n:]):
            return s[-n:]
    return ""


def _prefix_station(s: str, ok) -> str:
    """`s` 中最长的**前缀**且是真实站名（「上海虹桥的高铁全程几个小时」→ 上海虹桥）。"""
    s = (s or "").strip()
    for n in range(min(len(s), _MAX_NAME_LEN), 1, -1):
        if ok(s[:n]):
            return s[:n]
    return ""


def _side_station(seg: str, ok, *, adjacent_suffix: bool) -> str:
    """取分隔符某一侧的站名：**先试紧邻分隔符的那一端，再试另一端**。

    为什么两端都要试：杂词可能落在任一侧。
      · `有没有通宵的车从西安`（左段）→ 站名在**末尾**（后缀 西安）
      · `从北京坐到上海` 的左段 `北京坐` → 站名在**开头**（前缀 北京），后缀是"京坐"
      · `上海虹桥的高铁全程几个小时`（右段）→ 站名在**开头**（前缀 上海虹桥）
    只试一端时，上面第二例就解不出来（实测：整句回退成 ('北京坐','上海走')）。
    """
    seg = _LEAD_FILLER_RE.sub("", (seg or "").strip()).strip()
    if not seg:
        return ""
    if adjacent_suffix:
        return _suffix_station(seg, ok) or _prefix_station(seg, ok)
    return _prefix_station(seg, ok) or _suffix_station(seg, ok)


def _parse_od_guided(text: str | None, station_ok) -> tuple[str, str] | None:
    """**引导式解析**：站点库负责消歧，字符串规则只负责切候选。

    为什么需要它（实测：单轮含区间的语料 18 条里 7 条失败）——原实现是
    "整串匹配 + 只剥句末词"，目的地一旦超过 10 字上限、或句中夹了杂词，整串就不匹配；
    起点侧又刻意不切停用词（克制是对的——「成都东」里有"都"），于是杂词留在起点里。
    两类失败：
      · **解不出**：`深圳北到厦门北今天还有票吗？要高铁的` → None
                  `北京南到上海虹桥的高铁全程几个小时` → None
      · **解错**：  `有没有通宵的车从西安到兰州` → ('有没有通宵的车从西安', '兰州')
                  `从北京坐到上海走的是哪条线？` → ('北京坐', '上海走')

    做法：在每个分隔符处切成左右两段，各自**从紧邻分隔符的一端向内收缩**到
    "最长的、确实是站名的"候选（见 `_side_station`）。上面四例分别得到
    (深圳北,厦门北)、(北京南,上海虹桥)、(西安,兰州)、(北京,上海)。
    站点库在这里不是"补充校验"，而是**唯一判据** —— 字符串规则无从知道"上海坐"不是站名。

    `_is_timeish` 红线保留：时间/时段表述不算站名（"明天下午到上海的高铁"）。
    多个切分都成立时取"站名总长最大、其次分隔符最靠左"的一个（最长匹配优先，结果可复现）。
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    prev = None
    while prev != s:
        prev = s
        s = _strip_edges(s)
    if not s:
        return None

    best: tuple[tuple[int, int], tuple[str, str]] | None = None
    for m in _GUIDED_SEP_RE.finditer(s):
        f = _side_station(s[:m.start()], station_ok, adjacent_suffix=True)
        t = _side_station(s[m.end():], station_ok, adjacent_suffix=False)
        if not f or not t:
            continue
        if _is_timeish(f) or _is_timeish(t):
            continue
        score = (len(f) + len(t), -m.start())
        if best is None or score > best[0]:
            best = (score, (f, t))
    return best[1] if best else None
