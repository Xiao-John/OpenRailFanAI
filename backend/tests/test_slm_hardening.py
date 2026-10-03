#!/usr/bin/env python3
"""本地小模型（SLM）加固 —— 输出不可用 ≠ 服务不可用。

背景
----
把决策层换成本地 2B 级模型（或任何便宜模型）时，最先崩的**不是判错，而是格式**：
模型回了一句话、一段解释、或者带尾随逗号的 JSON。改造前的后果很重 ——
`_parse_json_object` 抛的 `LLMUnavailable` 与"服务连不上"是同一个类型，
决策层直接 `raise`，整轮请求从"可能答得上"掉到"只能规则排版"。

本套件钉住三件事：
1. **解析宽容度**：围栏 / 前后噪声 / 尾随逗号都能吃下；`null` 这种"合法 JSON 但非对象"
   要报对错因（曾经被 None 哨兵误判成"无法解析"）。
2. **异常分层**：`LLMOutputInvalid` 是 `LLMUnavailable` 子类 —— 既有降级契约不变，
   但调用方多了一个"还能换条简单路再试"的判据。
3. **决策层行为**：合并调用输出不可解析 → 退回两次更简单的调用（小模型常常能过）；
   而真正的"服务不可用"必须照旧往上抛，不许被这层吞掉。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_slm_hardening.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import client as llm
from app.llm.client import LLMOutputInvalid, LLMUnavailable, _parse_json_object
from app.pipeline import planner
from app.pipeline.extract import Slots
from app.pipeline.intent import Intent

_failures: list[str] = []
_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(msg)


def _raises_output_invalid(text: str, why: str) -> None:
    try:
        got = _parse_json_object(text)
    except LLMOutputInvalid:
        return
    except Exception as e:  # noqa: BLE001
        check(False, f"{why}: 期望 LLMOutputInvalid，实际 {type(e).__name__}: {e}")
        return
    check(False, f"{why}: 期望报错，实际解析成 {got!r}")


# ---------------------------------------------------------------- 1) 解析宽容度
def test_parse_tolerance() -> None:
    # 各种"几乎对了"的形态都必须吃下
    cases = [
        ('{"intent": "ticket", "question_type": "realtime"}', "纯 JSON", "ticket"),
        ('```json\n{"intent": "ticket", "question_type": "realtime"}\n```', "markdown 围栏", "ticket"),
        ('```\n{"intent": "ticket", "question_type": "realtime"}\n```', "无语言标记的围栏", "ticket"),
        ('好的，结果是：\n{"intent": "ticket", "question_type": "realtime"}\n以上。', "前后带解释", "ticket"),
        ('{"intent": "ticket", "question_type": "realtime",}', "尾随逗号（对象）", "ticket"),
        ('{"intent": "ticket",\n  "question_type": "realtime",\n}', "尾随逗号 + 换行", "ticket"),
        ('{"intent": "ticket", "slots": {"target": "G1",}}', "嵌套尾随逗号", "ticket"),
    ]
    for text, why, want in cases:
        obj = _parse_json_object(text)
        check(obj.get("intent") == want, f"{why}: intent 应为 {want}，实际 {obj.get('intent')!r}")
    print(f"[PASS] 解析宽容度：围栏 / 噪声 / 尾随逗号共 {len(cases)} 种形态全部解析成功")

    # 嵌套尾随逗号要真的把逗号删干净（不能只删最外层）
    nested = _parse_json_object('{"intent": "ticket", "slots": {"target": "G1",},}')
    check(nested.get("slots") == {"target": "G1"}, nested)

    # 只保留 schema 声明过的键由 chat_structured 负责；这里只保证解析层不丢键
    check(_parse_json_object('{"a": 1, "b": null}') == {"a": 1, "b": None}, "null 值要保留为 None")


def test_non_object_json_reports_right_reason() -> None:
    """`null` / 数组 / 字符串都是"合法 JSON 但不是对象" —— 必须报这个原因。

    这里踩过一个真坑：最初用 `None` 当"没解析出来"的哨兵，于是内容正好是 `null` 时
    会被误判成"无法解析"，把调用方引向完全错误的排查方向。
    """
    for text in ("null", "[]", "[1, 2]", '"just a string"', "123", "true"):
        try:
            _parse_json_object(text)
            check(False, f"{text!r}: 非对象 JSON 应当报错")
        except LLMOutputInvalid as e:
            check("不是对象" in str(e), f"{text!r}: 错因应为「不是对象」，实际 {e}")
    print("[PASS] 合法 JSON 但非对象（null/数组/字符串/数字/布尔）→ 统一报「不是对象」")


def test_truncated_output_diagnosed() -> None:
    """截断要给一句能对症的诊断（调大 max_tokens），而不是笼统的"非 JSON"。"""
    truncated = (
        '{"intent": "schedule", "question_type": "realtime", "slots": '
        '{"target": "G1", "location": "上海虹桥", "time": "今天"'
    )
    try:
        _parse_json_object(truncated)
        check(False, "截断输出应当报错")
    except LLMOutputInvalid as e:
        check("截断" in str(e), f"截断应给出截断诊断，实际：{e}")

    # 只是没写 JSON（没有括号）时**不该**误报截断
    try:
        _parse_json_object("我不知道该怎么回答这个问题，请换一种问法试试看。")
        check(False, "纯文本应当报错")
    except LLMOutputInvalid as e:
        check("截断" not in str(e), f"无括号的纯文本不应报截断：{e}")
    print("[PASS] 截断（括号未闭合）与「压根没写 JSON」给出不同诊断，不互相误报")


def test_failure_is_still_an_llm_unavailable() -> None:
    """向下兼容的硬约束：既有 `except LLMUnavailable` 的降级路径必须原样生效。"""
    check(issubclass(LLMOutputInvalid, LLMUnavailable), "LLMOutputInvalid 必须是 LLMUnavailable 子类")
    try:
        _parse_json_object("完全不是 JSON")
    except LLMUnavailable as e:            # 故意用父类捕获
        check(isinstance(e, LLMOutputInvalid), "应能被父类 LLMUnavailable 捕获")
    else:
        check(False, "应当抛出 LLMUnavailable")
    print("[PASS] LLMOutputInvalid ⊂ LLMUnavailable：既有降级契约不变")


# ---------------------------------------------------------------- 2) 决策层行为
def test_planner_falls_back_on_invalid_output() -> None:
    """合并调用输出不可解析 → 退回两次简单调用，而不是整轮降级。"""
    calls: list[str] = []

    async def _bad_json(*a, **kw):
        calls.append("merged")
        raise LLMOutputInvalid("LLM 返回非 JSON（无法解析结构化输出）")

    async def _classify(message, history=None):
        calls.append("classify")
        return Intent.TICKET, {"question_type": "realtime"}

    async def _fill(message, intent=None, history=None):
        calls.append("fill")
        return Slots(location="北京南", target=None, time="今天", direction=None, extra=None, raw={})

    async def run():
        # 用一条快路径**必然不接管**的问法（知识型开放问题）
        with patch("app.pipeline.planner.chat_structured", _bad_json), \
             patch("app.pipeline.planner.intent.classify", _classify), \
             patch("app.pipeline.planner.extract.fill", _fill):
            return await planner.decide("为什么高铁要叫复兴号？")

    intent, q_type, slots, who, _defer = asyncio.run(run())
    check(who == "llm-legacy", f"应回退到 llm-legacy，实际 {who}")
    check(intent is Intent.TICKET, f"意图应取两次调用的结果，实际 {intent}")
    check(q_type == "realtime", q_type)
    check(slots.location == "北京南" and slots.time == "今天", slots.non_empty())
    check(calls == ["merged", "classify", "fill"], f"调用顺序应为 合并→意图→槽位，实际 {calls}")
    print("[PASS] 合并调用输出不可解析 → 退回两次简单调用（planner=llm-legacy）")


def test_planner_still_raises_on_real_unavailability() -> None:
    """真正的"服务不可用"必须照旧往上抛 —— 这层不许把硬故障吞成软失败。"""

    async def _down(*a, **kw):
        raise LLMUnavailable("LLM 鉴权失败(HTTP 401)：API Key 无效或被拒绝。")

    async def run():
        with patch("app.pipeline.planner.chat_structured", _down):
            return await planner.decide("为什么高铁要叫复兴号？")

    try:
        asyncio.run(run())
    except LLMUnavailable as e:
        check("401" in str(e), f"应原样抛出上游诊断，实际 {e}")
        print("[PASS] 真·服务不可用仍抛出 LLMUnavailable（编排层照旧降级）")
        return
    check(False, "服务不可用时不应返回结果")


def test_planner_invalid_intent_still_falls_back() -> None:
    """合并调用**解析成功但 intent 非法**（小模型最常见的"格式对、值瞎填"）也要回退。"""

    async def _bad_intent(*a, **kw):
        return {"intent": "查票", "question_type": "realtime", "target": "G1"}

    async def _classify(message, history=None):
        return Intent.SCHEDULE, {"question_type": "realtime"}

    async def _fill(message, intent=None, history=None):
        return Slots(location=None, target="G1", time=None, direction=None, extra=None, raw={})

    async def run():
        with patch("app.pipeline.planner.chat_structured", _bad_intent), \
             patch("app.pipeline.planner.intent.classify", _classify), \
             patch("app.pipeline.planner.extract.fill", _fill):
            return await planner.decide("为什么高铁要叫复兴号？")

    intent, _qt, _slots, who, _defer = asyncio.run(run())
    check(who == "llm-legacy", f"非法 intent 应回退，实际 {who}")
    check(intent is Intent.SCHEDULE, intent)
    print("[PASS] 合并调用输出的 intent 非法（小模型的「格式对、值瞎填」）→ 照旧回退")


def test_structured_schema_keys_are_filtered() -> None:
    """回归：`chat_structured` 只保留 schema 声明过的键（小模型爱多吐字段）。"""
    schema = {"type": "object", "properties": {"intent": {}, "question_type": {}}}
    captured: dict = {}

    class _Msg:
        content = '{"intent": "ticket", "question_type": "realtime", "思考过程": "……", "confidence": 0.9}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    async def _create_once(*a, **kw):
        captured.update(kw)
        return _Resp()

    async def run():
        with patch.object(llm, "_create_once", _create_once), \
             patch.object(llm, "get_client", lambda *a, **kw: object()), \
             patch.object(llm, "current_provider", lambda: _fake_provider()):
            return await llm.chat_structured("查 G1", schema)

    obj = asyncio.run(run())
    check(set(obj) == {"intent", "question_type"}, f"多余键应被丢弃，实际 {sorted(obj)}")
    print("[PASS] 结构化输出只保留 schema 声明的键（小模型多吐的字段被丢掉）")


def _fake_provider():
    from app.llm.providers import Provider

    return Provider(
        id="fake", label="假供应商", base_url="http://127.0.0.1:1/v1",
        api_key="k", model="m", api="chat_completions",
    )


# ---------------------------------------------------------------- 3) 约束解码
class _FakeStatusError(Exception):
    """模拟 openai SDK 的 APIStatusError（阶梯靠 status_code + body.error.message 判定）。"""

    def __init__(self, status: int, message: str):
        self.status_code = status
        self.body = {"error": {"message": message}}
        super().__init__(message)


class _FakeResp:
    def __init__(self, content: str):
        class _Msg:
            pass

        msg = _Msg()
        msg.content = content

        class _Choice:
            pass

        ch = _Choice()
        ch.message = msg
        self.choices = [ch]
        self.usage = None


def _run_chat_structured(schema: dict, *, json_schema_flag: bool, calls: list[dict],
                         first_fails: bool = False):
    """跑一次 chat_structured，记录每次真正发出的请求参数。"""
    from types import SimpleNamespace

    state = {"n": 0}

    async def _create_once(client, ladder, kw):
        calls.append(dict(kw))
        state["n"] += 1
        if first_fails and state["n"] == 1:
            raise _FakeStatusError(400, "response_format: json_schema is not supported by this server")
        return _FakeResp('{"intent": "ticket", "question_type": "realtime"}')

    async def run():
        with patch.object(llm, "_create_once", _create_once), \
             patch.object(llm, "get_client", lambda *a, **kw: object()), \
             patch.object(llm, "current_provider", lambda: _fake_provider()), \
             patch.object(llm, "get_settings", lambda: SimpleNamespace(
                 llm_mock=False, llm_structured_json_schema=json_schema_flag)):
            return await llm.chat_structured("查 G1", schema)

    return asyncio.run(run())


_SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"},
                                            "question_type": {"type": "string"}}}


def test_constrained_decoding_is_opt_in() -> None:
    """默认只声明 json_object；开了 LLM_STRUCTURED_JSON_SCHEMA 才把**完整 schema** 传下去。

    为什么这条重要：约束解码（语法掩码）是小模型能不能当决策器的胜负手
    （实测"好好请求"合法率 4%、重试 5 次 23%、约束解码 100%）。所以这条链路必须
    被测住 —— 一旦 schema 没真的传下去，本地小模型就悄悄退回"求它好好写 JSON"。
    """
    calls: list[dict] = []
    obj = _run_chat_structured(_SCHEMA, json_schema_flag=False, calls=calls)
    rf = calls[0].get("response_format")
    check(rf == {"type": "json_object"}, f"默认应为 json_object，实际 {rf}")
    check(obj.get("intent") == "ticket", obj)

    calls = []
    _run_chat_structured(_SCHEMA, json_schema_flag=True, calls=calls)
    rf = calls[0].get("response_format")
    check(isinstance(rf, dict) and rf.get("type") == "json_schema",
          f"开启后应为 json_schema，实际 {rf}")
    inner = (rf or {}).get("json_schema") or {}
    check(inner.get("schema") == _SCHEMA, f"完整 schema 必须原样下发，实际 {inner.get('schema')}")
    check(isinstance(inner.get("name"), str) and inner["name"], f"json_schema 需要 name，实际 {inner}")
    check("strict" not in inner,
          "不应带 strict：strict 要求 additionalProperties=false 与全字段必填，"
          "与本项目 draft-07 的 [\"string\",\"null\"] 写法不兼容")
    print("[PASS] 约束解码可开关：默认 json_object，开启后下发完整 schema（含 name、不带 strict）")


def test_json_schema_is_dropped_when_unsupported() -> None:
    """上游不支持 json_schema 时必须**丢掉 response_format 重试**，而不是整轮失败。

    这是"开了约束解码但供应商不支持"的唯一安全出口 —— 参数降级阶梯负责它，
    所以 'json_schema' 必须出现在可降级关键词里。
    """
    check(llm._unsupported_param(_FakeStatusError(
        400, "response_format: json_schema is not supported")) == "response_format",
        "json_schema 不被支持时必须能被识别为 response_format 降级")

    calls: list[dict] = []
    obj = _run_chat_structured(_SCHEMA, json_schema_flag=True, calls=calls, first_fails=True)
    check(len(calls) == 2, f"应当失败一次后重试一次，实际 {len(calls)} 次")
    check((calls[0].get("response_format") or {}).get("type") == "json_schema", calls[0])
    check("response_format" not in calls[1],
          f"重试时必须不带 response_format，实际 {calls[1].get('response_format')}")
    check(obj.get("intent") == "ticket", f"重试成功后仍要能拿到结果，实际 {obj}")
    print("[PASS] 上游不支持 json_schema → 丢弃 response_format 重试（不整轮失败）")


def test_compact_prompt_replaces_schema_dump() -> None:
    """小模型必须走「模板 + 算例」，而不是把 JSON Schema 原文拼进提示词。

    这是本地化改造里**最关键的一条**：实测把带中文描述的 schema 原文甩给 Qwen3.5-2B，
    它会**把 schema 骨架当成答案模板照抄回来**
    （`{"type":"object","properties":{"intent":"查询余票","location":{},…}}`），
    19 条语料的合并调用 19 次全废。所以这条链路必须被钉住。
    """
    from app.pipeline.planner import _merged_static_prompt

    full = _merged_static_prompt(compact=False)
    slim = _merged_static_prompt(compact=True)

    check('"intent": "…"' in slim, "精简版必须给出可照抄的输出模板")
    check("只能取这 8 个之一" in slim and "只能取这 3 个之一" in slim,
          "模板必须把 intent / question_type 的取值枚举写清楚（否则小模型会自创取值）")
    # 至少两个算例，且其中一个必须是省略句（多轮继承最容易丢）
    check(slim.count("示例输出：") >= 2, f"至少两个算例，实际 {slim.count('示例输出：')}")
    check("示例输入：那商务座呢" in slim, "必须有一个多轮省略句算例（钉住槽位继承）")
    check('"target": "G1"' in slim, "省略句算例要示范出继承来的槽位值")
    check(len(slim) > len(full), "精简版要把模板接在指令之后")
    check("判定要点" in slim, "精简版仍要保留判定要点（rail_line/station 的区分靠它）")
    print(f"[PASS] 精简提示词：{len(slim)} vs {len(full)} 字符，含 8 选 1 / 3 选 1 枚举与 2 个算例")


def test_schema_can_be_left_out_of_prompt() -> None:
    """`schema_in_prompt=False` 时提示词里不得出现 schema 原文（调用方自己说格式）。"""
    calls: list[dict] = []
    captured: dict = {}

    async def _create_once(client, ladder, kw):
        calls.append(dict(kw))
        captured["messages"] = kw.get("messages")
        return _FakeResp('{"intent": "ticket"}')

    async def run():
        from types import SimpleNamespace

        with patch.object(llm, "_create_once", _create_once), \
             patch.object(llm, "get_client", lambda *a, **kw: object()), \
             patch.object(llm, "current_provider", lambda: _fake_provider()), \
             patch.object(llm, "get_settings", lambda: SimpleNamespace(
                 llm_mock=False, llm_structured_json_schema=False)):
            return await llm.chat_structured(
                "模板提示词：{'intent': '…'}", _SCHEMA, schema_in_prompt=False)

    asyncio.run(run())
    blob = "\n".join(m.get("content", "") for m in captured["messages"])
    check('"properties"' not in blob, f"schema 原文不该出现在提示词里：{blob[:200]}")
    check("模板提示词" in blob, "调用方给的提示词要原样保留")
    # 默认路径仍然要带 schema（云端行为不变）
    calls.clear()
    _run_chat_structured(_SCHEMA, json_schema_flag=False, calls=calls)
    blob2 = "\n".join(m.get("content", "") for m in calls[0]["messages"])
    check('"properties"' in blob2, "默认路径必须照旧把 schema 拼进提示词（云端行为零变化）")
    print("[PASS] schema_in_prompt=False 时不拼 schema 原文；默认路径行为不变")


def test_no_think_field_is_per_provider() -> None:
    """关思考的字段名**按供应商**下发 —— 各家不通用，而且不认时是静默忽略。

    实测（Qwen3.5-2B on Ollama 0.33.3，同一提示词、同一 max_tokens）：
      {"enable_thinking": false}   → 被忽略，400 token 全是思考，正文为**空**
      {"think": false}             → OpenAI 兼容层同样忽略（只有原生 /api/chat 认）
      {"reasoning_effort": "none"} → 生效：44 token / 0.9s，正文就是那个 JSON
    所以 ollama 预设必须带 no_think_body，否则本地路径表现为"模型答不出话"。
    """
    from app.llm.providers import BUILTIN_PROVIDERS, provider_from_dict

    ollama = provider_from_dict("ollama", BUILTIN_PROVIDERS["ollama"], source="builtin")
    check(ollama.no_think_body == {"reasoning_effort": "none"},
          f"ollama 预设应带 reasoning_effort，实际 {ollama.no_think_body}")

    kw = llm._kwargs_for("chat_completions", ollama, messages=[{"role": "user", "content": "x"}],
                         model="m", temperature=0.0, max_tokens=0, json_mode=True,
                         no_think=True, dropped=set())
    check(kw.get("extra_body") == {"reasoning_effort": "none"},
          f"关思考时应下发 reasoning_effort，实际 {kw.get('extra_body')}")

    # 没有声明 no_think_body 的供应商仍用通用字段（云端行为不变）
    plain = provider_from_dict("plain", {"base_url": "https://api.example.com/v1", "model": "m"},
                               source="request")
    kw2 = llm._kwargs_for("chat_completions", plain, messages=[{"role": "user", "content": "x"}],
                          model="m", temperature=0.0, max_tokens=0, json_mode=True,
                          no_think=True, dropped=set())
    check(kw2.get("extra_body") == {"enable_thinking": False},
          f"普通供应商应回落到 enable_thinking，实际 {kw2.get('extra_body')}")

    # 关掉思考时不下发任何东西
    kw3 = llm._kwargs_for("chat_completions", ollama, messages=[{"role": "user", "content": "x"}],
                          model="m", temperature=0.0, max_tokens=0, json_mode=True,
                          no_think=False, dropped=set())
    check("extra_body" not in kw3, f"no_think=False 时不应带 extra_body，实际 {kw3.get('extra_body')}")

    # reasoning_effort 不被支持时也要能被阶梯丢掉（否则整轮失败）
    check(llm._unsupported_param(_FakeStatusError(
        400, "unknown field reasoning_effort")) == "enable_thinking",
        "reasoning_effort 不被支持时必须能被识别为可降级参数")
    print("[PASS] 关思考字段按供应商下发：ollama→reasoning_effort，其余→enable_thinking")


def test_no_think_defaults_to_config() -> None:
    """不传 no_think 时沿用配置 —— 兜底路径（intent/extract）也要吃到它。

    这里踩过一个真坑：只有合并调用显式传了 no_think，兜底的 intent.classify /
    extract.fill 走默认 False。云端只是白烧钱，本地小模型则是**整轮超时**
    （思考 token 吃满预算、正文为空）。
    """
    from types import SimpleNamespace

    seen: list[bool] = []

    async def _create_once(client, ladder, kw):
        seen.append("extra_body" in kw)
        return _FakeResp('{"intent": "ticket"}')

    async def run(setting: bool):
        with patch.object(llm, "_create_once", _create_once), \
             patch.object(llm, "get_client", lambda *a, **kw: object()), \
             patch.object(llm, "current_provider", lambda: _fake_provider()), \
             patch.object(llm, "get_settings", lambda: SimpleNamespace(
                 llm_mock=False, llm_structured_no_think=setting,
                 llm_structured_json_schema=False)):
            return await llm.chat_structured("x", _SCHEMA)   # 故意不传 no_think

    asyncio.run(run(True))
    check(seen[-1] is True, "配置为 true 时，不传 no_think 也应当关思考")
    asyncio.run(run(False))
    check(seen[-1] is False, "配置为 false 时不应带关思考字段")
    print("[PASS] no_think 不传时沿用 LLM_STRUCTURED_NO_THINK（兜底路径同样生效）")


def test_generation_no_think_is_configurable() -> None:
    """**生成路径**也要能关思考，且默认关（云端行为零变化）。

    实测（llama-server + Qwen3.5-0.8B、max_tokens=64、问"G1 始发站"）：
        不传关闭参数          → 正文 0 字、思考 202 字、finish_reason=length
        reasoning_effort=none → 正文 15 字、思考 0 字、finish_reason=stop   ← 唯一有效
        enable_thinking=false → 正文 0 字、思考 216 字（**被静默忽略**）
    即本地生成不关思考 = **正文一个字都没有**，整条生成链路不可用。
    而生成路径原先硬编码 `no_think=False`（早期只考虑云端），所以必须由配置接管。
    """
    from types import SimpleNamespace

    seen: list[bool] = []

    async def _create_once(client, ladder, kw):
        seen.append("extra_body" in kw)
        return _FakeResp("好的")

    async def run(setting: bool):
        with patch.object(llm, "_create_once", _create_once), \
             patch.object(llm, "get_client", lambda *a, **kw: object()), \
             patch.object(llm, "current_provider", lambda: _fake_provider()), \
             patch.object(llm, "get_settings", lambda: SimpleNamespace(
                 llm_mock=False, llm_generation_no_think=setting,
                 llm_model="m", llm_context_tokens=8192, llm_max_tokens=192,
                 llm_chars_per_token=1.5)):
            return await llm.chat_with_reasoning("x")     # 故意不传 no_think

    asyncio.run(run(True))
    check(seen[-1] is True, "LLM_GENERATION_NO_THINK=true 时生成调用应带关思考字段")
    asyncio.run(run(False))
    check(seen[-1] is False, "默认（false）时生成调用**不得**带关思考字段 —— 云端行为不能变")
    print("[PASS] 生成路径关思考可配（默认关）：LLM_GENERATION_NO_THINK")


def test_reasoning_extraction_survives_missing_model_extra() -> None:
    """`_reasoning_of` 必须在**没有思考**时也不炸。

    这是个潜伏很久的 bug：它先试 `reasoning_content`/`reasoning`，拿到就 return；
    只有两个都为空（= 这次没思考）才回落到 `message.model_extra`。
    而本仓库锁的 openai SDK 版本上 `model_extra` **不存在** —— 于是
    "关思考"这条路径 100% 抛 AttributeError。云端默认开思考，这段兜底从没被执行到，
    直到本地推理把"关思考"变成必经之路才暴露。
    """
    class _MsgNoExtra:
        content = "答案"
        reasoning_content = None
        reasoning = None

    check(llm._reasoning_of(_MsgNoExtra()) == "",
          "没有思考内容且 SDK 无 model_extra 时应返回空串，而不是抛 AttributeError")

    class _MsgWithReasoning:
        reasoning_content = "想了 3 个字"

    check(llm._reasoning_of(_MsgWithReasoning()) == "想了 3 个字",
          "有 reasoning_content 时应原样返回")

    class _MsgWithExtra:
        model_extra = {"reasoning_content": "藏在 extra 里"}

    check(llm._reasoning_of(_MsgWithExtra()) == "藏在 extra 里",
          "有 model_extra 时仍应能取到 reasoning_content")

    print("[PASS] 思考提取对缺失字段免疫（关思考路径不再抛 AttributeError）")


def test_variable_part_always_goes_last() -> None:
    """随请求变化的内容必须拼在提示词**最后** —— 本地推理吃到前缀缓存的前提。

    为什么值得单独钉一条：本地推理的延迟几乎全在 prefill，而 Ollama / llama-server 靠
    **复用上一次请求的公共前缀**（KV cache）把它省掉。用户输入一旦夹在中间，
    后面的 schema / 判定要点就全部作废、每次重算。这条断言直接量"两个不同问题的
    提示词能共用多长的前缀"。
    """
    from app.pipeline.planner import _merged_static_prompt, _merged_user_tail

    static = _merged_static_prompt(compact=True)
    # 注意：静态前缀里**可以**有「那商务座呢」—— 那是模板里的少样本算例，本来就该固定。
    # 所以这里换一句模板里没出现过的话，来验"当前输入没混进静态部分"。
    probe = "G35今天几点到上海虹桥"
    check(probe not in static, "静态前缀里不许出现本次用户输入")
    check("前文对话" not in static, "静态前缀里不许出现前文对话段")
    check("[本次用户输入]" not in static, "静态前缀里不许出现本次输入段")

    tail = _merged_user_tail("那商务座呢", [{"role": "user", "content": "G1今天还有票吗"}])
    check(tail.index("前文对话") < tail.index("本次用户输入"), "尾巴里前文在输入之前")
    check(tail.rstrip().endswith("那商务座呢"), "尾巴必须以本次用户输入收尾")

    # 真跑两个不同的问题，量公共前缀能覆盖多少
    seen: list[str] = []

    def _run_one(message: str) -> None:
        async def _create_once(client, ladder, kw):
            seen.append(kw["messages"][-1]["content"])
            return _FakeResp('{"intent": "ticket"}')

        async def run():
            from types import SimpleNamespace

            with patch.object(llm, "_create_once", _create_once), \
                 patch.object(llm, "get_client", lambda *a, **kw: object()), \
                 patch.object(llm, "current_provider", lambda: _fake_provider()), \
                 patch.object(llm, "get_settings", lambda: SimpleNamespace(
                     llm_mock=False, llm_structured_json_schema=False)):
                await llm.chat_structured(
                    _merged_static_prompt(compact=True), _SCHEMA,
                    schema_in_prompt=False, prompt_tail=_merged_user_tail(message, None))

        asyncio.run(run())

    _run_one(probe)
    _run_one("西安北站哪个站台最出片？")
    a, b = seen
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    check(common >= len(static),
          f"两个不同问题的公共前缀 {common} 字符，应覆盖整个静态前缀（{len(static)} 字符）")
    check(a.rstrip().endswith(probe) and b.rstrip().endswith("西安北站哪个站台最出片？"),
          "提示词必须以本次用户输入收尾")
    print(f"[PASS] 变量拼在最后：两个不同问题共用 {common} 字符前缀（静态部分全命中）")


def main() -> int:
    print("=" * 72)
    print("本地小模型（SLM）加固测试")
    print("=" * 72)

    test_parse_tolerance()
    test_non_object_json_reports_right_reason()
    test_truncated_output_diagnosed()
    test_failure_is_still_an_llm_unavailable()
    test_planner_falls_back_on_invalid_output()
    test_planner_still_raises_on_real_unavailability()
    test_planner_invalid_intent_still_falls_back()
    test_structured_schema_keys_are_filtered()
    test_constrained_decoding_is_opt_in()
    test_json_schema_is_dropped_when_unsupported()
    test_compact_prompt_replaces_schema_dump()
    test_schema_can_be_left_out_of_prompt()
    test_variable_part_always_goes_last()
    test_no_think_field_is_per_provider()
    test_no_think_defaults_to_config()
    test_generation_no_think_is_configurable()
    test_reasoning_extraction_survives_missing_model_extra()

    if _failures:
        print(f"\n失败 {len(_failures)} 条（共校验 {_checks} 项）：")
        for f in _failures[:30]:
            print(f"  ✗ {f}")
        return 1
    print(f"\n全部通过 ✔（{_checks} 项断言）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
