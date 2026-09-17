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


def parse_od(text: str | None) -> tuple[str, str] | None:
    """解析区间表述为 (出发站, 到达站)；无法识别返回 None。"""
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None

    # 先剥离句末标点与追问短语，再匹配（否则"北京到上海走哪条线路？"整句不匹配 → 退化成网页搜索）
    prev = None
    while prev != s:
        prev = s
        s = _LEAD_FILLER_RE.sub("", s).strip()     # 句首填充语（帮我/我要/候补一张…）
        s = _LEAD_TIME_RE.sub("", s).strip()       # 句首时间词（明天/下周三…）
        s = _TAIL_NOISE_RE.sub("", s).strip()      # 句末追问短语（走哪条线路/怎么走…）
        s = _TAIL_PUNCT_RE.sub("", s).strip()      # 句末标点
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
