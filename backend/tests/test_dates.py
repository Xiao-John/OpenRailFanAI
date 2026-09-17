"""日期归一化测试（含 M11.1 修复的回归用例）。"""
from __future__ import annotations

from datetime import date, timedelta

from app.dates import date_note, normalize_date, resolve_date

# 固定"今天"无法注入（模块直接用 date.today()），因此断言以相对天数表达，任何日期运行都成立。
TODAY = date.today()


def _rel(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def main():
    tomorrow = TODAY + timedelta(days=1)

    cases = [
        ("今天", TODAY.isoformat()),
        ("明天", tomorrow.isoformat()),
        ("后天", _rel(2)),
        # ★ 回归：最长匹配优先（修复前「大后天」被判为「后天」→ 少一天）
        ("大后天", _rel(3)),
        ("大前天", _rel(-3)),
        ("前天", _rel(-2)),
        ("2026-09-14", "2026-09-14"),
        ("2026/09/14", "2026-09-14"),
        ("20260914", "2026-09-14"),
        (None, TODAY.isoformat()),
        ("", TODAY.isoformat()),
    ]
    for raw, expected in cases:
        got = normalize_date(raw)
        assert got == expected, f"normalize_date({raw!r}) = {got!r}, 期望 {expected!r}"
        print(f"[PASS] normalize_date({raw!r}) -> {got}")

    # 中文月日：9月14日
    got = normalize_date("9月14日")
    assert got.endswith("-09-14"), got
    print(f"[PASS] normalize_date('9月14日') -> {got}")

    # ★ 回归：非法日期不得原样透传给下游（2026-13-45 / 2026-02-30）
    for bad in ("2026-13-45", "2026-02-30", "0月0日"):
        got, matched = resolve_date(bad, default_today=True)
        assert matched is False, f"{bad!r} 被误判为识别成功 -> {got}"
        assert got == TODAY.isoformat(), got
        print(f"[PASS] 非法日期 {bad!r} -> 不识别（回落今天，不原样透传）")

    # ★ 新增：星期表述
    wed = normalize_date("下周三")
    assert wed == (TODAY + timedelta(days=(7 - TODAY.weekday()) + 2)).isoformat(), wed
    print(f"[PASS] normalize_date('下周三') -> {wed}（以周一为周首）")
    for kw in ("周三", "星期三", "本周三", "这周三"):
        d = normalize_date(kw)
        assert d >= TODAY.isoformat(), f"{kw} 落到过去：{d}"
        print(f"[PASS] normalize_date({kw!r}) -> {d}（不落到过去）")

    # 无法识别时 default_today=False 返回空串
    assert normalize_date("随便说说", default_today=False) == ""
    print("[PASS] 无法识别 + default_today=False -> 空串")

    # ★ 识别状态可观测：date_note 只在"给了时间却听不懂"时出声
    assert date_note("国庆", TODAY.isoformat()) != "", "未识别表述应给出如实说明"
    assert date_note("明天", _rel(1)) == "", "识别成功时不应产生噪声说明"
    assert date_note(None, TODAY.isoformat()) == "", "用户没说时间时不应产生说明"
    print(f"[PASS] date_note 如实说明 -> {date_note('国庆', TODAY.isoformat())[:40]}…")

    # ★ 中文数字月日（用户实测：问「十月一日西安到北京的火车」，解析器只认 \d，
    #   于是整条链退化成"查今天" → 模型只能让用户再问一遍）。
    #   期望值按"已过则顺延到明年"的既有规则算，任何日期运行都成立。
    def _md(month: int, day: int) -> str:
        d = date(TODAY.year, month, day)
        if d < TODAY:
            d = date(TODAY.year + 1, month, day)
        return d.isoformat()

    for raw, month, day in [
        ("十月一日", 10, 1),
        ("十月一号", 10, 1),
        ("十二月三十一日", 12, 31),
        ("十一月十一日", 11, 11),
        ("一月一日", 1, 1),
        ("10月1日", 10, 1),          # 阿拉伯数字仍要照旧
    ]:
        got, matched = resolve_date(raw)
        assert matched is True, f"{raw!r} 应被识别，实际 matched={matched}"
        assert got == _md(month, day), f"resolve_date({raw!r}) = {got}, 期望 {_md(month, day)}"
        print(f"[PASS] 中文数字月日 {raw!r} -> {got}")

    # 相对月份里的中文数字（"下个月三号"）也要认
    got, matched = resolve_date("下个月三号")
    assert matched is True, got
    print(f"[PASS] resolve_date('下个月三号') -> {got}")

    # ★ 认不全或不合法时**宁可说不认识**，绝不猜一个日期去查
    for bad in ("二月三十日", "十三月一日", "二十三日", "十月"):
        got, matched = resolve_date(bad)
        assert matched is False, f"{bad!r} 被误判为识别成功 -> {got}"
        assert got == TODAY.isoformat(), got
        print(f"[PASS] {bad!r} -> 不识别（如实说明，不猜日期）")

    print("\n日期归一化测试全部通过 ✔")


if __name__ == "__main__":
    main()
