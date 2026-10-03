"""生成输出的两道防线：**重复循环兜底** 与 **精简生成提示词**（无网络）。

为什么这两件事要一起测：它们治的是同一个失败。
实测（设备端 0.8B、同一条真实生成提示词）：

    长提示词（2073 字）→ 输出 1200 token（撞上限不会停）· 重复率 97% · 编造站名
    精简提示词（227 字）→ 输出   61 token · 重复率 0%

也就是说：**提示词过载是诱因，无限重复是后果**。精简提示词把概率压下去，
循环兜底负责在它真的发生时止损 —— 缺任何一半都不成立。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_output_guard.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from app.pipeline import repetition as rp

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_detects_real_degeneration():
    """真实退化样本：0.8B 把一段站名重复了 68 次。"""
    # 与设备上抓到的形态一致：编造的站名序列反复出现
    loop = "杭州、宁波、绍兴、湖州、嘉兴、桐乡、海宁、临平、台州、"
    text = "京沪线经过的 57 个站依次为：北京、天津、济南、徐州、南京、上海、" + loop * 5
    at = rp.detect(text)
    assert at is not None, "真实的重复循环没被检测到"
    assert at <= len("京沪线经过的 57 个站依次为：北京、天津、济南、徐州、南京、上海、"), (
        f"截断点 {at} 没落在循环开始处 —— 会留下残缺的重复尾巴"
    )
    out, hit = rp.cut(text)
    assert hit and out.endswith("（后文出现连续重复，已在此截断。）"), (
        "截断必须**如实告知**，不能静默丢弃（用户看到的是莫名中断的回答）"
    )
    assert "杭州" not in out, "循环内容没被真正去掉"
    print(f"[PASS] 真实退化的重复循环被抓到并截断（截断点 {at}）")


def test_does_not_flag_normal_output():
    """正常的列表 / 排比 / 表格 / 强调性重复，一律不许误伤。"""
    cases = {
        "逐条列表": "京沪线共 57 站：北京、天津、济南、徐州、南京、上海。（其余 51 站略）\n来源：jprailfan",
        "排比句": "**全程**1463 km。**历时**5 小时 28 分。**席别**二等座 553 元。\n来源：12306",
        "表格": "| 车次 | 发 | 到 |\n| G1 | 09:00 | 13:28 |\n| G3 | 10:00 | 14:28 |\n",
        "强调性短重复": "请注意，请注意，这是我强调的地方。",
        "正常长回答": ("根据检索事实，京沪高速线全长 1318 公里。该数据来自黄河铁路网客运里程表，"
                  "更新时间为 20260810。需要注意的是，`xx所` 是线路连接点，不是办理客运的车站。"
                  "以上内容均来自检索事实，未使用模型知识补充。"),
        "两条相同但不相邻": "G1 次 09:00 开。其他信息略。G1 次 09:00 开，这是重申。",
    }
    for tag, t in cases.items():
        at = rp.detect(t)
        assert at is None, f"{tag} 被误报为重复循环（截断点 {at}）"
    print(f"[PASS] {len(cases)} 种正常输出全部不误报")


def test_detect_is_cheap_enough_for_streaming():
    """流式里每 128 字查一次，单次必须是微秒级 —— 否则检测本身就成了延迟。"""
    text = "根据检索事实，京沪高速线全长 1318 公里，数据来自客运里程表。\n" * 3
    t0 = time.perf_counter()
    for _ in range(200):
        rp.detect(text)
    per_ms = (time.perf_counter() - t0) * 1000 / 200
    assert per_ms < 2.0, f"单次检测 {per_ms:.2f} ms 太慢，流式里会拖慢生成"
    print(f"[PASS] 单次检测 {per_ms:.3f} ms（流式每 128 字一次，可忽略）")


def test_compact_generation_prompt_is_shorter_and_keeps_red_lines():
    """精简生成提示词：明显更短，但**关键红线一条都不能丢**。"""
    import unittest.mock as mock

    from app.pipeline.extract import Slots
    from app.pipeline import generate

    retrieval = {
        "data": [{"tool": "rail.line_stations", "text": "京沪线全程 1463 km，共 57 站。",
                  "sources": ["https://jprailfan.com"], "note": "线路数据"}],
        "sources": ["https://jprailfan.com"], "tool_trace": ["rail.line_stations: ok"],
    }
    slots = Slots(target="京沪线")

    def prompt(compact: bool) -> str:
        cfg = type("S", (), {"llm_generation_compact_prompt": compact, "fact_max_entries": 0,
                             "fact_text_max_chars": 4000, "fact_table_max_rows": 40})()
        with mock.patch.object(generate, "get_settings", lambda: cfg):
            return generate.build_prompt("京沪线经过哪些站？", slots, retrieval,
                                         question_type="realtime")

    long_p, short_p = prompt(False), prompt(True)
    assert len(short_p) < len(long_p) * 0.5, (
        f"精简提示词没短到一半以下：{len(short_p)} vs {len(long_p)}"
    )
    # 红线：这几条防的是真实发生过的错，删任何一条都会让语料上的老问题复发
    for must in ("只依据", "不要自己推算", "倒推", "已截断", "图定"):
        assert must in short_p, f"精简版丢了关键约束「{must}」"
    # 检索事实与来源必须照旧注入，否则模型无从依据
    assert "京沪线全程 1463 km" in short_p and "https://jprailfan.com" in short_p
    print(f"[PASS] 精简生成提示词 {len(long_p)} → {len(short_p)} 字"
          f"（降 {100 * (1 - len(short_p) / len(long_p)):.0f}%），关键红线与检索事实都在")


def test_compact_generation_prompt_defaults_off():
    """默认必须是关 —— 云端行为不能因为给本地模型做的优化而改变。"""
    from app.config import Settings

    assert Settings.__fields__["llm_generation_compact_prompt"].default is False, (
        "llm_generation_compact_prompt 默认值不是 False —— 云端提示词会跟着变"
    )
    assert Settings.__fields__["llm_generation_no_think"].default is False
    print("[PASS] 两个生成侧开关默认都是关（云端行为零变化）")


def test_device_profile_sets_the_local_friendly_flags():
    """设备端档案必须把这几面都打开：关思考、精简提示词、限制注入条数。"""
    src = (REPO_ROOT / "backend" / "app" / "local_inference.py").read_text(encoding="utf-8")
    for key in ("LLM_GENERATION_NO_THINK", "LLM_GENERATION_COMPACT_PROMPT",
                "LLM_STRUCTURED_JSON_SCHEMA", "LLM_STRUCTURED_COMPACT_PROMPT",
                "FACT_MAX_ENTRIES"):
        assert key in src, f"设备端档案没设 {key}"
    print("[PASS] 设备端档案：关思考 + 精简提示词 + 注入条数上限 都已带上")


def test_zero_point_eight_b_is_withdrawn():
    """0.8B 档位必须撤下 —— 它实测会编造数据且不会自己停。"""
    from app import local_inference as li

    ids = [c["id"] for c in li.MODEL_CHOICES]
    assert not any("0.8b" in i.lower() for i in ids), f"0.8B 档位又回来了：{ids}"
    assert ids, "可下载档位不能为空"
    # 已下载过 0.8B 的人也要被告知，不能只是"列表里消失"
    warn = li._model_warning("Qwen3.5-0.8B-Q4_K_M.gguf")
    assert warn and "重复" in warn, "已下载的 0.8B 没有警告文案"
    assert li._model_warning("Qwen3.5-2B-Q4_K_M.gguf") == "", "2B 不该有警告"
    print(f"[PASS] 0.8B 已撤档（现有档位 {ids}），且对已下载者给出警告")


def test_streaming_guard_emits_replace_and_truncates():
    """**接线**测试：流式检测到重复时，必须下发 replace 收回坏内容并标注原因。

    为什么用注入而不是真跑模型：循环是否发生依赖具体数据与采样，带随机性
    （实测同一模型同一问题，换一组检索事实就不复现了）。**接线是对错问题，不该靠碰运气验**。
    """
    import asyncio
    from typing import AsyncIterator

    from app.llm import client as llm_client
    from app.pipeline import orchestrator

    loop = "杭州、宁波、绍兴、湖州、嘉兴、桐乡、海宁、临平、台州、"

    async def _stream(prompt: str, **kwargs) -> AsyncIterator[tuple[str, str]]:  # noqa: ARG001
        yield ("text", "京沪线经过的站依次为：北京、天津、济南、徐州、南京、上海、")
        for _ in range(12):                      # 退化：同一段反复吐
            yield ("text", loop)

    orig = llm_client.stream_completion
    llm_client.stream_completion = _stream          # type: ignore[assignment]
    try:
        events: list[dict] = []

        async def _run() -> None:
            async for ev in orchestrator.run_stream("京沪线经过哪些站？"):
                events.append(ev)

        asyncio.run(_run())
    finally:
        llm_client.stream_completion = orig         # type: ignore[assignment]

    kinds = [e["type"] for e in events]
    assert "replace" in kinds, f"检测到重复却没有收回坏内容（事件序列：{kinds}）"
    repl = next(e for e in events if e["type"] == "replace")
    assert "杭州" not in repl["text"], "replace 里仍有循环内容"
    assert "连续重复" in repl["text"], "截断没有如实告知用户"
    done = next(e for e in events if e["type"] == "done")
    assert done["truncated"] is True, "截断未反映到 done 事件"
    assert done["truncate_reason"] == "repetition", (
        f"截断原因标成了 {done['truncate_reason']} —— 会被前端引向『调大输出上限』这个反向操作"
    )
    print(f"[PASS] 流式接线：检测到重复 → 下发 replace 收回 → done 标 repetition（{len(repl['text'])} 字）")


def main() -> None:
    test_detects_real_degeneration()
    test_does_not_flag_normal_output()
    test_detect_is_cheap_enough_for_streaming()
    test_compact_generation_prompt_is_shorter_and_keeps_red_lines()
    test_compact_generation_prompt_defaults_off()
    test_device_profile_sets_the_local_friendly_flags()
    test_zero_point_eight_b_is_withdrawn()
    test_streaming_guard_emits_replace_and_truncates()
    print("\n输出质量防线测试全部通过 ✔")


if __name__ == "__main__":
    sys.exit(main())
