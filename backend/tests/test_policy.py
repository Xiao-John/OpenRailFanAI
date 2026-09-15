"""作答策略测试：按 question_type 切换"能否使用模型知识"。

这是 M10 的核心约束——
- realtime（默认）：严格闭卷，只依据检索事实
- knowledge     ：允许结合模型知识，但必须标注来源与不确定度
- mixed         ：实时/知识分别处理

M11.1 补强（对应 审计报告（历史） P1-6）
    早期本套只断言 `answer_policy()` 返回的**字符串常量**，
    其中 `assert "模型知识" not in p or "不得" in p` 后半支恒真（realtime 策略必含"不得"），
    且**从未验证策略真的被注入 prompt** —— 若有人把 `generate.py` 里的策略注入删掉，
    本套测试依旧全绿。现在补：从 `build_prompt()` 的真实产物断言策略确实生效。
"""
from __future__ import annotations

from app.pipeline.extract import Slots
from app.pipeline.generate import answer_policy, build_prompt


def _retrieval_stub() -> dict:
    """一份最小的检索结果替身（不需要联网）。"""
    return {
        "data": [
            {
                "tool": "emu.routing",
                "data": {"count": 1},
                "text": "车次 G1：今日担当车组为 CR400BFA-5159。",
                "sources": ["https://api.rail.re/train/G1"],
                "note": "rail.re 交路数据（共 1 条记录）",
            }
        ],
        "sources": ["https://api.rail.re/train/G1"],
        "tool_trace": ["emu.routing: ok"],
        "note": "已完成工具调用",
    }


def test_realtime_is_strict():
    p = answer_policy("realtime")
    assert "只依据" in p or "只依据下方" in p, p
    assert "不得" in p, p
    # 严格策略不得出现"允许结合模型知识"这类**放宽**措辞
    # （2026-09-14：新增了唯一的窄例外"站点纠错可标注式给出同音站名"，
    #   因此这里改为断言"没有对实时数据的放宽"，而不是"完全没有'允许'二字"）
    assert "你自己的中国铁路知识" not in p, "严格策略不得包含知识型放宽条款"
    assert "不得用模型记忆补充或推测这类数据" in p, p
    print("[PASS] realtime 策略：严格闭卷（只依据检索事实）")


def test_knowledge_allows_model_knowledge():
    p = answer_policy("knowledge")
    assert "允许" in p, p
    assert "你自己的中国铁路知识" in p, p
    # 必须有标注与不确定度要求
    assert "据模型知识" in p, p
    assert "未经检索确认" in p, p
    print("[PASS] knowledge 策略：允许模型知识 + 强制标注来源/不确定度")


def test_mixed_splits():
    p = answer_policy("mixed")
    assert "分开处理" in p or "分别处理" in p, p
    assert "实时部分" in p and "知识部分" in p, p
    print("[PASS] mixed 策略：实时/知识分别处理")


def test_default_is_realtime():
    """未知/缺省必须从严，避免知识放宽被误用于实时数据。"""
    for v in (None, "", "unknown", "Knowledge", "REALTIME"):
        p = answer_policy(v)
        assert "只依据" in p or "只依据下方" in p, (v, p)
        print(f"[PASS] answer_policy({v!r}) 回退为 realtime 严格策略")


# ---------- 策略必须真的进入 prompt（真实产物断言） ----------

def test_policy_actually_injected_into_prompt():
    """从 build_prompt 的真实产物断言：策略文本、检索事实、来源都在 prompt 里。"""
    slots = Slots(target="G1", time="今天")
    retrieval = _retrieval_stub()

    prompts = {}
    for qtype in ("realtime", "knowledge", "mixed", None):
        prompts[qtype] = build_prompt("G1今天由哪组动车组担当？", slots, retrieval, question_type=qtype)

    # 1) 策略文本必须原样出现在 prompt 中（用 answer_policy 的产物比对）
    for qtype in ("realtime", "knowledge", "mixed"):
        assert answer_policy(qtype) in prompts[qtype], f"{qtype} 策略未被注入 prompt"
    assert answer_policy("realtime") in prompts[None], "缺省未回退 realtime 策略"

    # 2) 三者的 prompt 必须真的不同（否则策略切换等于没生效）
    assert prompts["realtime"] != prompts["knowledge"] != prompts["mixed"], "三种策略产物相同"
    # 严格策略只允许在"站点纠错"这一窄例外里出现"据模型知识"标注要求
    rt = prompts["realtime"]
    assert "你自己的中国铁路知识" not in rt, "严格策略误含知识型放宽条款"
    if "据模型知识" in rt:
        assert "站点纠错" in rt, "严格策略出现未经约束的'据模型知识'措辞"
    assert "据模型知识" in prompts["knowledge"], "知识型策略未包含标注要求"

    # 3) 检索事实与来源必须注入
    for qtype, p in prompts.items():
        assert "CR400BFA-5159" in p, f"{qtype}: 检索事实未注入"
        assert "https://api.rail.re/train/G1" in p, f"{qtype}: 来源未注入"
    print("[PASS] 策略/检索事实/来源确实注入 prompt（真实产物断言）")


def main():
    test_realtime_is_strict()
    test_knowledge_allows_model_knowledge()
    test_mixed_splits()
    test_default_is_realtime()
    test_policy_actually_injected_into_prompt()
    print("\n作答策略测试全部通过 ✔")


if __name__ == "__main__":
    main()
