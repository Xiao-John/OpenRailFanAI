"""多轮上下文处理测试（app/context.py）。"""
from __future__ import annotations

from app.context import (
    MAX_CHARS_PER_MSG,
    MAX_TURNS,
    format_history,
    trim_history,
)


def test_trim_empty():
    assert trim_history(None) == []
    assert trim_history([]) == []
    print("[PASS] trim_history 空输入 -> []")


def test_trim_filters_invalid():
    raw = [
        {"role": "user", "content": "有效"},
        {"role": "system", "content": "非法角色"},
        {"role": "assistant", "content": "   "},
        {"role": "assistant", "content": "有效回答"},
        {"bogus": 1},
        None,
    ]
    got = trim_history(raw)
    assert got == [
        {"role": "user", "content": "有效"},
        {"role": "assistant", "content": "有效回答"},
    ], got
    print("[PASS] trim_history 过滤非法角色/空内容")


def test_trim_limits_turns():
    raw = [{"role": "user", "content": f"第{i}条"} for i in range(20)]
    got = trim_history(raw)
    assert len(got) <= MAX_TURNS, len(got)
    assert got[-1]["content"] == "第19条", got[-1]
    print(f"[PASS] trim_history 截断到最近 {len(got)} 条（上限 {MAX_TURNS}）")


def test_trim_truncates_long_message():
    raw = [{"role": "user", "content": "长" * 5000}]
    got = trim_history(raw)
    assert len(got) == 1
    assert len(got[0]["content"]) <= MAX_CHARS_PER_MSG + 1, len(got[0]["content"])
    assert got[0]["content"].endswith("…")
    print(f"[PASS] trim_history 单条截断到 {len(got[0]['content'])} 字")


def test_format_history():
    h = [
        {"role": "user", "content": "G1今天由哪组担当？"},
        {"role": "assistant", "content": "由 CR400BFA-5054 担当。"},
    ]
    txt = format_history(h)
    assert "用户：G1今天由哪组担当？" in txt, txt
    assert "助手：由 CR400BFA-5054 担当。" in txt, txt
    print(f"[PASS] format_history -> {txt!r}")


def test_format_history_empty():
    assert format_history(None) == ""
    assert format_history([]) == ""
    print("[PASS] format_history 空输入 -> ''")


def test_format_history_shortens_assistant():
    h = [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "答" * 500},
    ]
    txt = format_history(h)
    # 助手回复应被压缩，避免历史噪声占用 prompt
    assert len(txt) < 300, len(txt)
    print(f"[PASS] format_history 压缩助手长回复 -> {len(txt)} 字")


def main():
    test_trim_empty()
    test_trim_filters_invalid()
    test_trim_limits_turns()
    test_trim_truncates_long_message()
    test_format_history()
    test_format_history_empty()
    test_format_history_shortens_assistant()
    print("\n上下文处理测试全部通过 ✔")


if __name__ == "__main__":
    main()