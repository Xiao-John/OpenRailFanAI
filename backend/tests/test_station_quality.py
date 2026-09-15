"""第 3 批：站点清单质量 / 同音纠错 / 思考效率 的回归测试（**全部无网络**）。

对应你批复的六条中的第 2、4、5 条：
- **第 2 条（E08/E03）**：站点清单质量。mcp 搜索硬上限 10 条导致"查北京只给房山东/后吕村"，
  现改为本地索引排序（`city` 同城 + `num` 站序≈重要度），并声明"匹配 M 个、展示前 N 个"
- **第 4 条（E07）**：引入 pypinyin，"太安"→"泰安"（同音）直接排到候选第一位
- **第 5 条**：prompt 要求思考简短、不在思考里完整起草回答

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_station_quality.py
"""
from __future__ import annotations

import asyncio

from app.pipeline.generate import build_prompt
from app.pipeline.extract import Slots
from app.tools import _rt12306 as rt
from app.tools.station_lookup import StationLookupTool, _pinyin_of, _suggest_stations


def _lookup(name: str, **kw):
    return asyncio.run(StationLookupTool().invoke({"name": name, **kw}))


def test_major_stations_are_listed_first():
    """E08：查"北京"必须包含北京丰台（12306 站序 229），而不是只给房山东/后吕村。"""
    res = _lookup("北京")
    assert res.ok, res.error
    names = [s["name"] for s in res.data["stations"]]
    assert "北京丰台" in names, f"未含北京丰台：{names}"
    assert names[0] == "北京", names
    # 站序≈重要度：主要站应排在小站之前
    assert names.index("北京丰台") < names.index("房山东"), names
    assert res.total and res.total > res.shown, (res.total, res.shown)
    assert "展示前" in res.note and "站序" in res.note, res.note
    assert res.truncated is True, res.truncated
    print(f"[PASS] 北京清单（{res.shown}/{res.total}）：{names[:9]}…")


def test_suzhou_expectation_from_r1_e03():
    """E03：查"苏州"至少要含 苏州 / 苏州北 / 苏州园区（或声明截断）。"""
    res = _lookup("苏州")
    assert res.ok, res.error
    names = [s["name"] for s in res.data["stations"]]
    for probe in ("苏州", "苏州北", "苏州园区"):
        assert probe in names, f"未含 {probe}：{names}"
    print(f"[PASS] 苏州清单（{res.shown}/{res.total}）：{names[:6]}…")


def test_exact_and_code_lookup_unchanged():
    """精确站名与电报码查询不受改版影响。"""
    r1 = _lookup("上海虹桥")
    assert r1.ok and r1.data["stations"][0]["code"] == "AOH", r1.data
    r2 = _lookup("BJP")
    assert r2.ok and r2.data["stations"][0]["name"] == "北京", r2.data
    print("[PASS] 精确站名与电报码查询正常（AOH / BJP）")


def test_homophone_correction_with_pypinyin():
    """第 4 条：引入 pypinyin 后，"太安"→"泰安"（同音）应排候选第一位。"""
    assert _pinyin_of("太安") == _pinyin_of("泰安") == "taian", _pinyin_of("太安")
    rt_loaded = asyncio.run(rt.ensure_loaded())
    suggestions = _suggest_stations("太安站")
    assert suggestions, "未给出候选"
    assert suggestions[0][0] == "泰安", f"同音站未排第一：{suggestions[:5]}"
    print(f"[PASS] 同音纠正 -> {suggestions[:4]}")

    res = _lookup("太安站")
    assert res.ok is False, res
    assert "同音站" in (res.text or ""), res.text
    assert "泰安" in (res.text or ""), res.text
    print(f"[PASS] 工具输出标注同音站 -> {(res.text or '')[:60]}…")


def test_station_lookup_declares_matches_and_truncation():
    """完整性契约同样适用于站点查询（避免"截断冒充全集"）。"""
    res = _lookup("吉林")
    assert res.ok, res.error
    assert res.total is not None and res.shown is not None, (res.total, res.shown)
    assert res.integrity_line(), res.integrity_line()
    print(f"[PASS] 站点查询完整性 -> {res.integrity_line()[:60]}…")


def test_thinking_brevity_instruction_in_prompt():
    """第 5 条：prompt 要求思考简短、不在思考里完整起草回答。"""
    prompt = build_prompt(
        "G1 今天由哪组担当？", Slots(target="G1"),
        {"data": [], "sources": [], "tool_trace": [], "note": ""},
    )
    assert "思考要短" in prompt, "缺少思考效率指令"
    assert "不要在思考里完整起草最终回答" in prompt, prompt[:200]
    print("[PASS] prompt 含「思考要短 / 不在思考中起草回答」指令")


def main():
    test_major_stations_are_listed_first()
    test_suzhou_expectation_from_r1_e03()
    test_exact_and_code_lookup_unchanged()
    test_homophone_correction_with_pypinyin()
    test_station_lookup_declares_matches_and_truncation()
    test_thinking_brevity_instruction_in_prompt()
    print("\n站点清单质量 / 同音纠错 / 思考效率 回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
