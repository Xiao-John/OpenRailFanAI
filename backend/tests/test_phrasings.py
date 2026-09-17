"""「用户说法 → 内部值」的探针语料 —— **全部无网络**。

本文件的立意（为什么单独建一份语料）
------------------------------------
有一类缺陷反复出现：**某个说法没有被任何规则认领**，于是被静默忽略、或退化成默认值。
已发生的实例：

- 「十月一日西安到北京的火车」：日期解析器只认阿拉伯数字 → 退化成"查今天" →
  模型只能让用户再问一遍（用户实测）
- 「G字头」「8点以后」「无座」「动卧」：筛选条件被静默忽略，查询照跑但根本没过滤

这类缺陷的共同点：**单元测试测不到** —— 单测只验证"我写过的规则"；
而语料关心的是"**用户会怎么说**"，所以它能把"没人认领的说法"暴露出来。
（问法 → 意图/槽位的语料在 `test_perf_fastpath.py` 的 `_GOLDEN` 里，本文件不重复。）

怎么扩
------
往下面四张表里各加一行，写清**期望值**即可。约定：

1. **每条都必须有明确期望值**。写不出期望值的说明该说法本身有歧义，
   应当先讨论口径再进表，而不是塞进来凑数。
2. **"不认识"也是正确答案**。如「二月三十日」应当**不识别**，
   而不是猜一个日期去查。所以非法/歧义样本必须进表 —— 它们钉的是"宁可说不认识"。
3. 每张表都有**条数下限**，防止有人为了让测试变绿而删用例。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_phrasings.py
"""
from __future__ import annotations

from datetime import date, timedelta

from app.dates import resolve_date
from app.pipeline.retrieve import _parse_seat, _parse_time_window, _parse_train_type

TODAY = date.today()


def _rel(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _md(month: int, day: int) -> str:
    """月日 → ISO。已过则顺延到明年（与 resolve_date 的既有规则一致）。"""
    d = date(TODAY.year, month, day)
    if d < TODAY:
        d = date(TODAY.year + 1, month, day)
    return d.isoformat()


def _next_month(day: int) -> str:
    total = (TODAY.year * 12 + (TODAY.month - 1)) + 1
    year, month = divmod(total, 12)
    return date(year, month + 1, day).isoformat()


def _weekday(target: int) -> str:
    """最近一次周 X（含今天）。"""
    return (TODAY + timedelta(days=(target - TODAY.weekday()) % 7)).isoformat()


# ==================================================== ① 时间说法 → YYYY-MM-DD
# 期望 None = **应当不识别**（工具层据此如实说明"没听懂，已按今天处理"）
TIME_CASES: list[tuple[str | None, str | None]] = [
    # 相对
    ("今天", TODAY.isoformat()),
    ("明天", _rel(1)),
    ("后天", _rel(2)),
    ("大后天", _rel(3)),            # ★ 最长匹配优先（曾把"大后天"判成"后天"，少一天）
    ("前天", _rel(-2)),
    ("昨天", _rel(-1)),
    ("明日", _rel(1)),
    ("明晚", _rel(1)),
    ("", TODAY.isoformat()),        # 用户没说时间 → 今天（不算识别失败）
    (None, TODAY.isoformat()),
    # 显式日期
    ("2026-09-14", "2026-09-14"),
    ("2026/09/14", "2026-09-14"),
    ("20260914", "2026-09-14"),
    # 阿拉伯数字月日
    ("10月1日", _md(10, 1)),
    ("3月15号", _md(3, 15)),
    # ★ 中文数字月日（用户实测：「十月一日西安到北京的火车」曾查成当天）
    ("十月一日", _md(10, 1)),
    ("十月一号", _md(10, 1)),
    ("十二月三十一日", _md(12, 31)),
    ("十一月十一日", _md(11, 11)),
    ("一月一日", _md(1, 1)),
    # 相对月份
    ("下个月三号", _next_month(3)),
    ("下月3日", _next_month(3)),
    # 星期
    ("周三", _weekday(2)),
    ("星期三", _weekday(2)),
    ("下周三", (TODAY + timedelta(days=(7 - TODAY.weekday()) + 2)).isoformat()),
    ("上周三", (TODAY + timedelta(days=(2 - TODAY.weekday()) - 7)).isoformat()),
    ("周末", (TODAY + timedelta(days=(5 - TODAY.weekday()) % 7)).isoformat()),
    # ★ 应当**不识别**：认不全就说不认识，绝不猜一个日期去查
    ("二月三十日", None),           # 不存在的日期
    ("十三月一日", None),           # 不存在的月份
    ("二十三日", None),             # 只有日、没有月（不是完整日期）
    ("十月", None),                 # 只有月
    ("国庆", None),                 # 节日俗称：**尚未支持**，钉住现状（见文件末尾"已知缺口"）
]

# ==================================================== ② 时段说法 → (after, before)
# ('', '') = 没有时段条件
WINDOW_CASES: list[tuple[str, tuple[str, str]]] = [
    ("凌晨", ("00:00", "06:00")),
    ("早上", ("05:00", "12:00")),
    ("上午", ("05:00", "12:00")),
    ("中午", ("11:00", "14:00")),
    ("下午", ("12:00", "18:00")),
    ("晚上", ("17:00", "24:00")),
    ("傍晚", ("17:00", "24:00")),
    # ★ 具体钟点（探针查出原先整类被静默忽略）
    ("8点以后", ("08:00", "")),
    ("10点前", ("", "10:00")),
    ("18点以后", ("18:00", "")),
    ("下午3点以后", ("15:00", "")),      # 粗时段 + 钟点同现 → 按时段修正
    ("晚上8点之后", ("20:00", "")),
    ("上午9点之前", ("", "09:00")),
    # 饭前饭后：粗表里原先也没有
    ("午饭后", ("12:00", "")),
    ("晚饭前", ("", "18:00")),
    # 没有时段条件
    ("", ("", "")),
    ("夜里", ("17:00", "24:00")),
    ("7点起", ("07:00", "")),
    ("中午12点前", ("", "12:00")),
    ("有什么车", ("", "")),
]

# ==================================================== ③ 席别说法 → 12306 座位键名
# 值必须能作为**子串**命中工具侧的键名之一：
#   business / first_class / second_class / advanced_soft_sleeper /
#   soft_sleeper / hard_sleeper / soft_seat / hard_seat / no_seat / dongwo
SEAT_CASES: list[tuple[str, str]] = [
    ("商务座", "business"),
    ("商务", "business"),
    ("一等座", "first_class"),
    ("二等座", "second_class"),
    ("软卧", "soft_sleeper"),
    ("硬卧", "hard_sleeper"),
    ("硬座", "hard_seat"),
    # ★ 泛称：不指定软硬，用 sleeper 去子串命中软卧/硬卧/动卧，正是用户的意思
    ("卧铺", "sleeper"),
    ("有卧铺的吗", "sleeper"),
    ("无座", "no_seat"),                  # ★ 探针查出原先漏了
    ("动卧", "dongwo"),                   # ★
    ("高级软卧", "advanced_soft_sleeper"),  # ★ 必须排在"软卧"前面，否则被子串抢先
    ("高包", "advanced_soft_sleeper"),     # ★
    ("软座", "soft_seat"),                # ★
    # 没提席别
    ("还有无座吗", "no_seat"),
    ("商务座有吗", "business"),
    ("要动卧", "dongwo"),
    ("", ""),
    ("有去北京的车吗", ""),
]

# ==================================================== ④ 车种说法 → 车次前缀
TYPE_CASES: list[tuple[str, str]] = [
    ("高铁", "G"),
    ("动车", "D"),
    ("城际", "C"),
    # ★ "X字头"是最常见的说法之一，原先整张表都没有
    ("G字头", "G"),
    ("D字头", "D"),
    ("Z字头", "Z"),
    ("T字头", "T"),
    ("K字头", "K"),
    ("直达", "Z"),
    ("特快", "T"),
    ("快速", "K"),
    ("普速", "K,T,Z"),
    ("绿皮", "K,T,Z"),
    # 没提车种
    ("K字头的", "K"),
    ("要坐高铁", "G"),
    ("", ""),
    ("有去上海的车吗", ""),
]

# 每张表的条数下限：防止有人为了让测试变绿而删用例（语料的价值就在"够全"）
# 下限 = 当前条数（只允许加、不允许删）。改动时请同步这一行。
MIN_CASES = {"time": 32, "window": 20, "seat": 19, "type": 17}


def _check(name: str, cases: list, run, show) -> int:
    """跑一张表，返回通过条数。`run` 把说法变成实际值，`show` 把它变成可读文本。"""
    ok = 0
    for raw, expected in cases:
        got = run(raw)
        if got != expected:
            raise AssertionError(
                f"[{name}] {raw!r} → {got!r}，期望 {expected!r}\n"
                f"  （若是新增说法：请确认口径后改期望值；"
                f"若是漏认：请在对应的关键词表/正则里补上，而不是放宽期望）")
        ok += 1
        if ok <= 3 or expected is None:
            print(f"[PASS] {name} {raw!r} → {show(got)}")
    n = len(cases)
    floor = MIN_CASES[name]
    assert n >= floor, f"[{name}] 语料只剩 {n} 条（下限 {floor}）：语料被删过？"
    print(f"[PASS] {name}：{n} 条说法全部符合期望（下限 {floor}）")
    return n


def test_time_phrasings():
    """① 时间说法 → 日期。★★ 这一张直接对应"十月一日"那个用户实测缺陷。"""
    def resolve(raw):
        """返回 None 表示"没听懂"（用户没给时间时不算没听懂）。"""
        d, matched = resolve_date(raw)
        return None if (raw and not matched) else d

    ok = 0
    for raw, expected in TIME_CASES:
        got = resolve(raw)
        assert got == expected, (
            f"[time] {raw!r} → {got!r}，期望 {expected!r}\n"
            f"  （漏认就在 dates.py 里补；非法日期必须返回 None，不许猜）")
        ok += 1
        if expected is None:
            print(f"[PASS] time {raw!r} → 不识别（如实说明，不猜日期）")
    n = len(TIME_CASES)
    assert n >= MIN_CASES["time"], f"[time] 语料只剩 {n} 条"
    print(f"[PASS] time：{n} 条说法全部符合期望（下限 {MIN_CASES['time']}）")


def test_window_phrasings():
    """② 时段说法 → (after, before)。"""
    _check("window", WINDOW_CASES, lambda s: _parse_time_window(s),
           lambda v: f"{v[0] or '-'}~{v[1] or '-'}")


def test_seat_phrasings():
    """③ 席别说法 → 12306 座位键名。"""
    _check("seat", SEAT_CASES, lambda s: _parse_seat(s), lambda v: v or "（没提）")


def test_type_phrasings():
    """④ 车种说法 → 车次前缀。"""
    _check("type", TYPE_CASES, lambda s: _parse_train_type(s), lambda v: v or "（没提）")


def main():
    test_time_phrasings()
    test_window_phrasings()
    test_seat_phrasings()
    test_type_phrasings()
    print("\n说法语料探针全部通过 ✔")
    print("已知缺口（尚未支持，故未进表）：节日俗称（国庆/春节/清明…）。")
    print("  国庆/元旦是固定日期，补一张小表即可；春节/清明/端午/中秋是农历，需农历换算。")


if __name__ == "__main__":
    main()
