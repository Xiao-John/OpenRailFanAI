"""确定性规则回复（mock / 模型不可用兜底）回归测试（**全部无网络、不调用模型**）。

背景：`app/llm/_mock.py` 原本只是"把检索事实原文贴出来"的演示桩，有两个真问题：
  ① 来源段把 prompt 里"以 - 开头"的行全当来源，而事实块每行也是 `- [tool] …`，
     于是输出的"数据来源"里塞满了 prompt 通用要求的原文；
  ② 槽位抽取自带一套弱正则（`\\d{2,6}` 连 "G1" 都抽不到、区间/站名完全不认），
     而 mock 模式下合并调用走的正是这条路径 → **LLM_MOCK=true 时凡交给 LLM 的问题
     都带着空槽位检索**，CI 对检索参数零鉴别力。

本套件钉住四件事：
1. **prompt 解析正确**：问题/槽位/工具/事实/来源各归各位，模板文字不得混进来源；
2. **槽位不再丢**（合并 schema 下按 properties 取交集，而不是"见 intent 就 return"）；
3. **渲染只搬运不编造**：正文逐行来自事实，知识型问题没有事实时如实说"无法作答"；
4. **降级接线正确**：有事实才降级（`degraded=True`）、没事实仍走原来的错误提示、
   可用 `LLM_FALLBACK_RENDER=false` 关掉；`answer_done` 语义不变（模型确实没答上）。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_mock_render.py
"""
from __future__ import annotations

import asyncio

from app.config import get_settings
from app.llm import _mock
from app.llm import client as llm_client
from app.llm.client import LLMUnavailable
from app.pipeline import generate, orchestrator
from app.pipeline.extract import Slots
from app.pipeline.schemas import COMBINED_JSON_SCHEMA, INTENT_JSON_SCHEMA, SLOTS_JSON_SCHEMA

# ---------------------------------------------------------------- 测试夹具
_TICKET_RETRIEVAL = {
    "sources": ["https://www.12306.cn/", "https://rail.re/"],
    "tool_trace": ["ticket.query(from=北京南,to=上海虹桥,date=2026-09-18)"],
    "note": "n",
    "data": [{
        "tool": "ticket.query",
        "text": "x",
        "sources": ["https://www.12306.cn/"],
        "note": "12306 实时数据，3 分钟内有效",
        "integrity": "命中 125 条；已展示 40 条",
        "data": {
            "from_station": "北京南", "to_station": "上海虹桥",
            "train_date": "2026-09-18", "count_all": 125, "count": 40,
            "trains": [
                {"train_no": "G1", "start_time": "09:00", "arrive_time": "13:28",
                 "seats": {"二等座": "有", "一等座": "12"}},
                {"train_no": "G3", "start_time": "09:20", "arrive_time": "13:48",
                 "seats": {"二等座": "有"}},
            ],
            "period_counts": {"09:00-12:00": 40},
            "seat_counts": {"二等座": 40},
        },
    }],
}


def _ticket_prompt(question: str = "明天北京到上海的还有票吗") -> str:
    return generate.build_prompt(
        question, Slots(direction="北京南→上海虹桥", time="明天"),
        _TICKET_RETRIEVAL, None, "ticket",
    )


# ---------------------------------------------------------------- 1) prompt 解析
def test_parse_prompt_structure():
    """问题/槽位/工具/事实/来源各归各位。"""
    v = _mock.parse_prompt(_ticket_prompt())

    assert v.question == "明天北京到上海的还有票吗", v.question
    assert v.slots == {"time": "明天", "direction": "北京南→上海虹桥"}, v.slots
    # trace 是 Python list 的 repr，而参数里含逗号 —— 必须按引号切
    assert v.tools == ["ticket.query(from=北京南,to=上海虹桥,date=2026-09-18)"], v.tools
    assert len(v.facts) == 1, v.facts
    f = v.facts[0]
    assert f.tool == "ticket.query", f.tool
    assert "北京南→上海虹桥" in f.body and "G1｜09:00-13:28" in f.body, f.body
    # 元信息行不能混进正文
    assert "数据完整性" not in f.body, f.body
    assert f.integrity == "命中 125 条；已展示 40 条", f.integrity
    assert f.sources == ("https://www.12306.cn/",), f.sources
    assert f.note == "12306 实时数据，3 分钟内有效", f.note
    print(f"[PASS] prompt 解析：槽位={v.slots} 工具={v.tools} 事实={len(v.facts)} 条")


def test_sources_never_include_prompt_template_text():
    """来源段只能来自 `[数据来源]`，不得把 prompt 的通用要求文字当成来源。

    原实现取"所有以 - 开头的行"，而通用要求与事实块每行都以 - / 缩进开头，
    实测输出里"数据来源"混进了"每条检索事实都带有自己的来源与时效说明…"。
    """
    v = _mock.parse_prompt(_ticket_prompt())
    assert v.sources == ["https://www.12306.cn/", "https://rail.re/"], v.sources
    joined = "\n".join(v.sources)
    for banned in ("禁止", "不得", "通用要求", "检索事实", "作答策略"):
        assert banned not in joined, f"来源里混进了 prompt 模板文字：{banned!r} -> {v.sources}"
    print(f"[PASS] 来源段干净：{v.sources}")


def test_facts_survive_blank_lines():
    """事实正文含空行（抓来的网页正文）时，区块定位不能被截断。"""
    body = "- [web.fetch] 标题：京沪高铁\n\n第一段内容\n\n第二段内容\n  时效/说明：抓取于今日"
    prompt = (
        "用户原话：京沪高铁\n关键槽位 => (未抽取到关键槽位)\n工具调用：(无)\n问题性质 => realtime\n\n"
        "[检索事实]\n" + body + "\n\n[数据来源]\n- https://example.com/a\n"
    )
    v = _mock.parse_prompt(prompt)
    assert len(v.facts) == 1, v.facts
    assert "第一段内容" in v.facts[0].body and "第二段内容" in v.facts[0].body, v.facts[0].body
    assert v.facts[0].note == "抓取于今日", v.facts[0].note
    assert v.sources == ["https://example.com/a"], v.sources
    print("[PASS] 事实正文含空行仍不截断区块")


# ---------------------------------------------------------------- 2) 槽位不再丢
def test_combined_schema_returns_slots():
    """合并 schema（planner 的实际调用）必须同时产出意图与槽位。

    旧实现见 `intent` 就 return，槽位被整块丢掉 → mock 模式下带空槽位检索。
    """
    prompt = (
        "判断下面这条铁路请求的意图、问题性质，并抽取槽位。\n"
        "\n[本次用户输入]\nG1今天几点到上海虹桥？\n"
    )
    d = asyncio.run(_mock.mock_structured(prompt, COMBINED_JSON_SCHEMA))
    assert d.get("intent") == "schedule", d
    assert d.get("question_type") == "realtime", d
    assert d.get("target") == "G1", f"车次槽位丢失（'\\d{{2,6}}' 抽不到 G1）：{d}"
    assert d.get("location") == "上海虹桥", f"站名槽位丢失：{d}"
    assert d.get("time") == "今天", f"时间槽位丢失：{d}"
    assert set(d) <= set(COMBINED_JSON_SCHEMA["properties"]), d
    print(f"[PASS] 合并 schema 下槽位不再丢：{d}")


def test_single_purpose_schemas_unchanged():
    """只问意图 / 只问槽位的旧模板行为保持不变（按 properties 取交集）。"""
    intent_only = asyncio.run(
        _mock.mock_structured("……\n\n[本次用户输入]\n明天北京到上海还有票吗\n", INTENT_JSON_SCHEMA)
    )
    assert set(intent_only) == {"intent", "question_type"}, intent_only
    assert intent_only["intent"] == "ticket", intent_only

    slots_only = asyncio.run(
        _mock.mock_structured("……\n\n[本次用户输入]\nG1今天几点到上海虹桥？\n", SLOTS_JSON_SCHEMA)
    )
    assert set(slots_only) == set(SLOTS_JSON_SCHEMA["properties"]), slots_only
    assert slots_only["target"] == "G1", slots_only
    print(f"[PASS] 旧模板行为不变：intent-only={intent_only} slots-only.target={slots_only['target']}")


def test_user_section_not_prompt_template():
    """判定输入必须是用户输入段落，不被 prompt 内的 few-shot 示例带偏。"""
    template = (
        "判断意图：\n\n[本次用户输入]\n你好\n\n参考示例：\n"
        '  "G1今天由哪组动车组担当？" → emu_routing\n'
        '  "北京到上海走哪条线路？"   → rail_line\n'
    )
    d = asyncio.run(_mock.mock_structured(template, INTENT_JSON_SCHEMA))
    assert d["intent"] == "general", f"被模板示例带偏：{d}"
    print(f"[PASS] 仍只按用户输入判定 -> {d}")


# ---------------------------------------------------------------- 3) 渲染只搬运不编造
def test_ticket_table_is_markdown_and_keeps_integrity():
    """余票明细的全角"｜"行转成 Markdown 表格；完整性/时效契约原样带出。"""
    out = _mock.render(_ticket_prompt(), _mock.STYLE_MOCK)
    assert "| 车次 | 发-到 | 席别 |" in out, out
    assert "| G1 | 09:00-13:28 | 二等座=有 一等座=12 |" in out, out
    assert "命中 125 条；已展示 40 条" in out, "完整性说明丢失"
    assert "12306 实时数据，3 分钟内有效" in out, "时效说明丢失"
    assert "https://www.12306.cn/" in out and "https://rail.re/" in out, out
    # 演示模式必须自报家门，不能让用户以为这是模型答的
    assert "未使用大语言模型" in out, out
    print("[PASS] 余票明细成表 + 完整性/时效/来源齐备 + 自报非模型产出")


def test_ascii_pipe_rows_are_not_tablified():
    """station.screen 用 ASCII "|" 且列数与标题不符 —— 不得误转成表格。"""
    line = "G1 北京南→上海虹桥 06:30 | 站台17A | CR400BF-S·定员576 | 上海客运段"
    out = _mock._tuplify(
        "出发屏明细（车次｜方向｜时刻｜站台｜车底·定员｜客运段）：\n" + line
    )
    assert "| ---" not in out, f"误转成表格：{out}"
    assert line in out, out
    print("[PASS] ASCII 竖线行保持纯文本（不误判为表格）")


def test_tab_tables_become_markdown():
    """经停表用制表符分隔（train_schedule）——也要成表，这是最高频的问法。"""
    raw = (
        "车次 G1（北京南→上海虹桥）经停 2 站：\n"
        "序\t站名\t到达\t发车\t停留\n"
        "01\t北京南\t----\t06:30\t----\n"
        "02\t沧州西\t07:18\t07:20\t2分钟\n"
        "（共 2 站，仅列出前 2 站）"
    )
    out = _mock._tuplify(raw)
    assert "| 序 | 站名 | 到达 | 发车 | 停留 |" in out, out
    assert "| 02 | 沧州西 | 07:18 | 07:20 | 2分钟 |" in out, out
    # 表格块之外的行必须原样保留（这里带着"仅列出前 N 站"的完整性声明）
    assert "（共 2 站，仅列出前 2 站）" in out, out
    assert "车次 G1（北京南→上海虹桥）经停 2 站：" in out, out
    # 列数不符（表头 3 列 / 数据 2 列）时不得转换，否则会拆错
    mismatch = _mock._tuplify("序\t站名\t到达\n01\t北京南")
    assert "| ---" not in mismatch, mismatch
    print("[PASS] 制表符经停表成表；列数不符时保持纯文本")


def test_render_never_invents_facts():
    """不编造：事实正文的每一行都必须原样出现在输出里。"""
    v = _mock.parse_prompt(_ticket_prompt())
    out = _mock.render(_ticket_prompt(), _mock.STYLE_MOCK)
    for line in v.facts[0].body.splitlines():
        if line.strip() and "｜" not in line:
            assert line in out, f"事实行被改写或丢失：{line!r}"
    # 没有事实时绝不产出数字型结论
    empty = _mock.render(
        generate.build_prompt("G1几点的车", Slots(target="G1"),
                              {"data": [], "sources": [], "tool_trace": []}, None, "realtime"),
        _mock.STYLE_MOCK,
    )
    assert "没有检索到可引用的数据" in empty, empty
    print("[PASS] 只搬运不编造；无事实时如实交代")


def test_knowledge_question_without_facts_states_inability():
    """知识型问题没有事实可搬时，如实说"规则模式答不了"，不硬凑一段答案。"""
    prompt = generate.build_prompt(
        "CR400AF 的动力系统是什么", Slots(target="CR400AF"),
        {"data": [], "sources": [], "tool_trace": []}, None, "knowledge",
    )
    out = _mock.render(prompt, _mock.STYLE_MOCK)
    assert "知识型" in out and "无法给出" in out, out
    # 降级路径下这种情况返回空串：错误提示比"没有数据"更有用
    assert _mock.render_fallback(prompt) == "", _mock.render_fallback(prompt)
    print("[PASS] 知识型无事实：如实声明无法作答，降级不产出占位文字")


# ---------------------------------------------------------------- 4) 降级接线
class _StubSlots:
    target = "G1"
    location = None
    time = None
    direction = None
    extra = None

    def non_empty(self) -> dict:
        return {"target": "G1"}


class _IntentStub:
    value = "schedule"
    label_zh = "列车时刻查询"


def _events_with_retrieval(retrieval: dict) -> list[dict]:
    """生成阶段必然失败，检索返回给定结果；收集流式事件。"""
    async def _classify(message, history=None):
        return _IntentStub(), {"question_type": "realtime"}

    async def _fill(message, intent=None, history=None):
        return _StubSlots()

    async def _retrieve(intent, slots, question_type=None, message=None, prefetch=None, display_action=None):
        return retrieval

    async def _gen(*_a, **_kw):
        raise LLMUnavailable("LLM 限流或额度不足(HTTP 429)，请稍后重试。")
        yield ("text", "")  # pragma: no cover

    saved = (orchestrator.planner.intent.classify, orchestrator.planner.extract.fill,
             orchestrator.retrieve.retrieve, llm_client.stream_completion)
    orchestrator.planner.intent.classify = _classify        # type: ignore[assignment]
    orchestrator.planner.extract.fill = _fill               # type: ignore[assignment]
    orchestrator.retrieve.retrieve = _retrieve              # type: ignore[assignment]
    llm_client.stream_completion = _gen                     # type: ignore[assignment]
    try:
        async def _collect():
            return [ev async for ev in orchestrator.run_stream("G1经停哪些站？")]
        return asyncio.run(_collect())
    finally:
        (orchestrator.planner.intent.classify, orchestrator.planner.extract.fill,
         orchestrator.retrieve.retrieve, llm_client.stream_completion) = saved  # type: ignore[assignment]


def _facts_only() -> dict:
    return {
        "data": [{"tool": "train.schedule", "text": "G1 经停：北京南 06:30 → 上海虹桥 11:24",
                  "data": {"train_code": "G1", "train_date": "2026-09-30", "source": "12306-timetable",
                           "stops_with_times": True, "from_station": "北京南", "to_station": "上海虹桥",
                           "stops": [{"station": "北京南", "station_no": "1", "start_time": "06:30"},
                                     {"station": "上海虹桥", "station_no": "2", "arrive_time": "11:24"}]},
                  "sources": ["https://www.12306.cn/"], "note": "图定时刻", "integrity": ""}],
        "sources": ["https://www.12306.cn/"], "tool_trace": ["train.schedule: ok"], "note": "n",
    }


def test_degraded_render_when_facts_exist():
    """普通非卡片事实在模型失败时仍可规则排版；错误同时可见。"""
    events = _events_with_retrieval({
        "data": [{"tool": "station.lookup", "text": "北京南在北京市丰台区。",
                  "sources": ["https://www.12306.cn/"], "note": "站点字典", "integrity": ""}],
        "sources": ["https://www.12306.cn/"], "tool_trace": ["station.lookup: ok"], "note": "n",
    })
    answer = "".join(e.get("delta", "") for e in events if e["type"] == "answer")
    dones = [e for e in events if e["type"] == "done"]
    assert len(dones) == 1, events
    done = dones[0]

    assert answer, "有事实却没产出降级回复"
    assert "北京南在北京市丰台区" in answer, answer
    assert "未经过模型" in answer or "未经模型" in answer, answer
    assert done["degraded"] is True, done
    # answer_done 语义不变：模型确实没答上（否则会把故障说成成功）
    assert done["answer_done"] is False, done
    assert done["error"], "错误提示必须同时可见，不得被降级回复掩盖"
    assert any(e["type"] == "error" for e in events), events
    print("[PASS] 有事实 → 降级排版(degraded=True) + error 同时可见 + answer_done=False")


def test_structured_degraded_facts_stay_in_cards():
    events = _events_with_retrieval(_facts_only())
    assert not any(event["type"] in {"answer", "think"} for event in events), events
    done = next(event for event in events if event["type"] == "done")
    assert done["degraded"] and not done["answer_done"] and done["error"], done
    assert any(event["type"] == "error" for event in events), events
    card = done["display_results"][0]
    assert card["kind"] == "train_schedule" and card["status"] == "success", card
    assert card["schema_version"] == 1 and card["time_basis"] == "reference", card
    assert [stop["station"] for stop in card["stops"]] == ["北京南", "上海虹桥"], card
    assert card["stops"][0]["start_time"] == "06:30", card
    print("[PASS] 结构化降级保持 error/卡片/参考时刻，不把事实重新流入正文")


def test_no_degraded_render_without_facts():
    """没有事实可搬 → 保持原行为（只有错误提示，不产出占位正文）。"""
    events = _events_with_retrieval(
        {"data": [], "sources": ["https://example.com/x"], "tool_trace": [], "note": "n"}
    )
    answer = "".join(e.get("delta", "") for e in events if e["type"] == "answer")
    done = [e for e in events if e["type"] == "done"][0]
    assert answer == "", f"无事实却产出了正文：{answer!r}"
    assert done["degraded"] is False, done
    assert done["answer_done"] is False, done
    print("[PASS] 无事实 → 不降级，维持原来的可操作错误提示")


def test_degraded_render_can_be_disabled():
    """LLM_FALLBACK_RENDER=false → 回到"只给错误提示"的旧行为。"""
    settings = get_settings()
    saved = settings.llm_fallback_render
    settings.llm_fallback_render = False
    try:
        events = _events_with_retrieval(_facts_only())
    finally:
        settings.llm_fallback_render = saved
    answer = "".join(e.get("delta", "") for e in events if e["type"] == "answer")
    done = [e for e in events if e["type"] == "done"][0]
    assert answer == "", f"开关关闭后仍产出降级正文：{answer!r}"
    assert done["degraded"] is False, done
    print("[PASS] LLM_FALLBACK_RENDER=false 可关闭降级")


def test_block_path_degrades_too():
    """块式 run() 与流式保持同一套降级语义。"""
    async def _retrieve(intent, slots, question_type=None, message=None, prefetch=None, display_action=None):
        return _facts_only()

    async def _boom(*_a, **_kw):
        raise LLMUnavailable("connection reset")

    saved = (orchestrator.retrieve.retrieve, generate.chat_with_reasoning)
    # 注意 patch 点：generate 是 `from ... import chat_with_reasoning` 导入的，
    # patch llm_client.chat_with_reasoning 不会生效（踩过）。
    orchestrator.retrieve.retrieve = _retrieve              # type: ignore[assignment]
    generate.chat_with_reasoning = _boom                    # type: ignore[assignment]
    try:
        r = asyncio.run(orchestrator.run("G1经停哪些站？"))
    finally:
        orchestrator.retrieve.retrieve, generate.chat_with_reasoning = saved  # type: ignore[assignment]

    assert r.degraded is True, r
    assert "北京南 06:30 → 上海虹桥 11:24" in r.answer, r.answer
    assert r.sources == ["https://www.12306.cn/"], r.sources
    assert r.tool_trace == ["train.schedule: ok"], r.tool_trace
    print("[PASS] 块式 run() 同样降级（degraded=True，来源/轨迹保留）")


def test_block_path_no_facts_still_reports_error():
    """块式无事实时仍返回可操作提示（不被降级吞掉）。"""
    async def _retrieve(intent, slots, question_type=None, message=None, prefetch=None, display_action=None):
        return {"data": [], "sources": [], "tool_trace": [], "note": "n"}

    async def _boom(*_a, **_kw):
        raise LLMUnavailable("HTTP 401")

    saved = (orchestrator.retrieve.retrieve, generate.chat_with_reasoning)
    orchestrator.retrieve.retrieve = _retrieve              # type: ignore[assignment]
    generate.chat_with_reasoning = _boom                    # type: ignore[assignment]
    try:
        r = asyncio.run(orchestrator.run("G1经停哪些站？"))
    finally:
        orchestrator.retrieve.retrieve, generate.chat_with_reasoning = saved  # type: ignore[assignment]

    assert r.degraded is False, r
    assert "无法完成分析与回答" in r.answer, r.answer
    print("[PASS] 块式无事实 → 可操作提示，degraded=False")


# ---------------------------------------------------------------- 5) 流式分块
def test_stream_chunks_reassemble_to_answer():
    """mock 流式分块拼起来必须与一次性渲染完全一致（否则流式/块式两条路会分叉）。"""
    prompt = _ticket_prompt()
    chunks = asyncio.run(_mock.mock_stream_chunks(prompt))
    joined = "".join(chunks)
    assert joined == asyncio.run(_mock.mock_chat(prompt)), "流式与块式输出不一致"
    assert len(chunks) > 3, f"分块过粗，不像流式：{len(chunks)}"
    print(f"[PASS] mock 流式分块可复原（{len(chunks)} 块）")


def main():
    test_parse_prompt_structure()
    test_sources_never_include_prompt_template_text()
    test_facts_survive_blank_lines()
    test_combined_schema_returns_slots()
    test_single_purpose_schemas_unchanged()
    test_user_section_not_prompt_template()
    test_ticket_table_is_markdown_and_keeps_integrity()
    test_ascii_pipe_rows_are_not_tablified()
    test_tab_tables_become_markdown()
    test_render_never_invents_facts()
    test_knowledge_question_without_facts_states_inability()
    test_degraded_render_when_facts_exist()
    test_structured_degraded_facts_stay_in_cards()
    test_no_degraded_render_without_facts()
    test_degraded_render_can_be_disabled()
    test_block_path_degrades_too()
    test_block_path_no_facts_still_reports_error()
    test_stream_chunks_reassemble_to_answer()
    print("\n确定性规则回复（mock/兜底）测试全部通过 ✔")


if __name__ == "__main__":
    main()
