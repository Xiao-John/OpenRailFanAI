"""日期归一化工具。

把用户口语化的时间表述（今天/明天/后天…）转成 12306 需要的 YYYY-MM-DD。

关键约定（M11.1 修复）
--------------------
1. **最长匹配优先**：早期实现按 dict 插入顺序 `if kw in v` 匹配，
   导致「大后天」先命中「后天」→ 少一天（real 事故级：实时查询返回错日期的确定结论）。
2. **识别结果可观测**：`resolve_date()` 返回 `(日期, 是否识别)`；
   调用方（工具层）在"用户给了时间表述但无法识别"时必须**如实说明按今天处理**，
   不得静默把今天当成用户的意图。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# 相对日期：**按 key 长度倒序匹配**（"大后天" 必须先于 "后天"）
# 2026-09-14 修复：原表缺 "昨天"（只有"前天 -2"），导致"G1 昨天是哪组车"解析为空串
# → 工具回落"今天" → 模型如实答"查不到昨天"，而 rail.re 其实返回了 09-13 的记录。
_RELATIVE: dict[str, int] = {
    "大后天": 3,
    "大前天": -3,
    "后天": 2,
    "前天": -2,
    "明天": 1,
    "明日": 1,
    "明早": 1,
    "明晚": 1,
    "明夜": 1,
    "昨天": -1,
    "昨日": -1,
    "昨晚": -1,
    "昨早": -1,
    "今天": 0,
    "今日": 0,
    "本日": 0,
    "今早": 0,
    "今晨": 0,
    "今晚": 0,
    "今夜": 0,
}
_RELATIVE_KEYS = sorted(_RELATIVE, key=len, reverse=True)

# 显式日期：2026-09-14 / 2026/09/14 / 20260914
_ISO_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_COMPACT_RE = re.compile(r"\b(\d{4})(\d{2})(\d{2})\b")

# 日期里的数字：既认阿拉伯数字，也认中文数字。
# 为什么必须认中文：**用户实测**问「十月一日西安到北京的火车」，`_MD_RE` 只写 \d 时
# 完全匹配不上 → resolve_date 返回"未识别、按今天" → 工具查的是**当天**余票 →
# 模型看到 date_note 的提示后只能让用户再问一遍。而"十月一日"是完全正常的说法。
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})"


def _cn_num(s: str) -> int | None:
    """把日期里的数字转成整数（只覆盖 1..31 这个量级，够用且不会误判）。

    认识的写法：1 / 十一 / 二十 / 二十一 / 三十一。其余一律返回 None，
    让调用方走"未识别"分支如实说明 —— 宁可说不认识，也不要猜一个日期去查。
    """
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):                       # 十一..十九
        rest = s[1:]
        return 10 + _CN_DIGITS[rest] if len(rest) == 1 and rest in _CN_DIGITS else None
    if len(s) >= 2 and s[0] in _CN_DIGITS and s[1] == "十":   # 二十 / 二十一 / 三十一
        tens = _CN_DIGITS[s[0]] * 10
        rest = s[2:]
        if not rest:
            return tens
        return tens + _CN_DIGITS[rest] if len(rest) == 1 and rest in _CN_DIGITS else None
    if len(s) == 1 and s in _CN_DIGITS:          # 单字：一..九
        return _CN_DIGITS[s]
    return None


# 月份日期：9月14日 / 十月一日 / 10月1号
_MD_RE = re.compile(rf"({_NUM})月({_NUM})[日号]")

# 星期：上周三 / 本周三 / 这周三 / 下周三 / 周三 / 星期三 / 周末
_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEKDAY_RE = re.compile(r"(上|下|本|这)?(?:个)?(?:周|星期)([一二三四五六日天])")
_WEEKEND_RE = re.compile(r"周末")

# 相对月份：上个月15号 / 下月3日 / 本月20号
_MONTH_RE = re.compile(rf"(上|下|本|这)?(?:个)?月({_NUM})[日号]")


def _safe_date(year: int, month: int, day: int) -> date | None:
    """构造日期；非法（如 2026-02-30 / 13 月）返回 None，避免把错值透传给 12306。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve_date(value: str | None, *, default_today: bool = True) -> tuple[str, bool]:
    """解析时间表述 → (YYYY-MM-DD, 是否识别成功)。

    `default_today=True` 时无法识别返回今天；`False` 时返回空串。
    第二个返回值用于让调用方区分"用户就是要今天"与"我们没听懂，退化成今天"。
    """
    today = date.today()
    fallback = today.isoformat() if default_today else ""
    if not value:
        # 未提供时间表述：默认今天（这属于"用户没说"，不算识别失败）
        return fallback, True

    v = str(value).strip()

    m = _ISO_RE.search(v)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (d.isoformat(), True) if d else (fallback, False)

    m = _COMPACT_RE.search(v)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (d.isoformat(), True) if d else (fallback, False)

    for kw in _RELATIVE_KEYS:            # 最长匹配优先
        if kw in v:
            return (today + timedelta(days=_RELATIVE[kw])).isoformat(), True

    m = _MD_RE.search(v)
    if m:
        month, day = _cn_num(m.group(1)), _cn_num(m.group(2))
        if month is None or day is None:      # 认不全就不猜，走"未识别"如实说明
            return fallback, False
        d = _safe_date(today.year, month, day)
        if d is None:
            return fallback, False
        if d < today:                    # 已过 → 顺延到明年
            d = _safe_date(today.year + 1, month, day)
            if d is None:
                return fallback, False
        return d.isoformat(), True

    # 相对月份（"上个月15号" / "下月3日"）：月份整体位移，不参与"已过顺延"
    m = _MONTH_RE.search(v)
    if m:
        prefix = m.group(1)
        day = _cn_num(m.group(2))
        if day is None:
            return fallback, False
        offset = {"上": -1, "下": 1}.get(prefix or "", 0)
        total = (today.year * 12 + (today.month - 1)) + offset
        year, month = divmod(total, 12)
        month += 1
        d = _safe_date(year, month, day)
        return (d.isoformat(), True) if d else (fallback, False)

    if _WEEKEND_RE.search(v):
        # 最近的周六（今天若是周六/周日则取当天/次日不再顺延）
        delta = (5 - today.weekday()) % 7
        return (today + timedelta(days=delta)).isoformat(), True

    m = _WEEKDAY_RE.search(v)
    if m:
        prefix, ch = m.group(1), m.group(2)
        target = _WEEKDAY_CHARS[ch]
        if prefix == "下":
            # 下周X：以"下周一开始"为基准（周日 → 次日即下周一）
            d = today + timedelta(days=(7 - today.weekday()) + target)
        elif prefix == "上":
            # 上周X：以"本周一"为基准往前推一周
            d = today + timedelta(days=(target - today.weekday()) - 7)
        elif prefix in ("本", "这"):
            d = today + timedelta(days=target - today.weekday())
            if d < today:                     # 本周该日已过 → 顺延到下一次
                d += timedelta(days=7)
        else:
            # 裸"周三"：最近一次（含今天），已过则下周
            d = today + timedelta(days=(target - today.weekday()) % 7)
        return d.isoformat(), True

    return fallback, False


def normalize_date(value: str | None, *, default_today: bool = True) -> str:
    """把时间表述归一化为 YYYY-MM-DD（兼容旧签名，内部走 `resolve_date`）。"""
    return resolve_date(value, default_today=default_today)[0]


def date_note(value: str | None, resolved: str) -> str:
    """当用户给了时间表述却无法识别时，生成"已按今天处理"的如实说明（否则返回空串）。"""
    if not value or not str(value).strip():
        return ""
    raw = str(value).strip()
    _, matched = resolve_date(raw, default_today=False)
    if matched:
        return ""
    return f"⚠️ 未能识别时间表述「{raw}」，已按今天（{resolved}）查询；如需其他日期请改用「明天」或「2026-09-14」这类表述。"
