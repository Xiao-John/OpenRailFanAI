"""决策层：把"意图分类 + 槽位抽取"合成一步（perf P0-1 / P0-2）。

三级策略（从快到慢，逐级保底）：

1. **确定性快路径**（`fastpath.plan`）：正则 + 本地站点库直接产出结果，**0 次 LLM 调用**。
   实测可覆盖约 56–68% 的车迷问法 → 预筛选从 3.8–11.6s 降到 **<0.3s**。
2. **合并调用**：判断不了时，用**一次**结构化调用同时拿 intent + question_type + slots
   （关思考：实测抽取 3.3s → 1.1s），替代原来的两次往返。
3. **传统两次调用**（`intent.classify` + `extract.fill`）：合并调用失败或输出非法时兜底，
   保证行为与优化前一致（可回退、可对照）。

对外只暴露 `decide()`，返回 `(Intent, question_type, Slots, planner)`，
其中 `planner ∈ {"deterministic", "llm-merged", "llm-legacy"}`，会随 `done` 事件透出，
用于统计快路径误判率（必要时 `FASTPATH_ENABLED=false` 一键关掉）。
"""
from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.llm.client import LLMUnavailable, chat_structured
from app.pipeline import extract, fastpath, intent
from app.pipeline.extract import Slots
from app.pipeline.intent import Intent
from app.pipeline.schemas import COMBINED_JSON_SCHEMA, INTENT_JSON_SCHEMA, SLOTS_JSON_SCHEMA
from app.tools import _rt12306 as rt

_log = logging.getLogger("railfan.planner")

_VALID_INTENTS = {i.value for i in Intent}
_VALID_Q_TYPES = {"realtime", "knowledge", "mixed"}


def _merged_prompt(message: str, history: list[dict] | None) -> str:
    """合并调用的 prompt：把两次调用里**必要的判定要点**压成一份，去掉重复的解释文字。"""
    from app.context import format_history

    ctx = format_history(history)
    return (
        "你是铁路出行助手的输入解析器。读下面这条用户输入，一次性给出：\n"
        "1) 主意图 intent；2) 问题性质 question_type；3) 关键槽位（location/target/time/direction/extra）。\n"
        + (f"\n[前文对话]\n{ctx}\n" if ctx else "")
        + f"\n[本次用户输入]\n{message}\n\n"
        "判定要点：\n"
        "- intent 针对【本次用户输入】；前文只用于消解指代（如「那明天呢」承接上一轮的车次）。\n"
        "- question_type：**答案是否必须依赖数据源检索**？必须检索才能答准的（列车时刻/余票/"
        "今日担当车组/开行状态/车站信息/两站间径路里程）→ realtime；"
        "靠铁路常识即可作答的（车型技术参数、发展历史、命名由来）→ knowledge；两者兼有 → mixed。\n"
        "- 槽位：优先用本次输入；本次省略主体（如仅说「那明天呢」）可从前文继承地点/车次/车型；"
        "继承值要填成可直接检索的完整形式；确实没有的字段填 null。\n"
        "- target 填车次号（G1）、车型（CR400AF）、车组号（CR400AF-0207）或线路名（京沪线）。\n"
        "- **rail_line 与 station 的区分（容易判错）**：问【某条线路】经过 / 沿线 / 有哪些车站 → rail_line，target 填线路名；问【某一座车站】本身（大屏 / 检票口 / 电报码 / 到发车次 / 在哪个城市）→ station。句子里出现「车站」二字**并不等于** station —— 实测反例：「京沪线的所有车站」曾被判成 station，于是拿线路名当站名去查，必然失败。\n"
        "- direction 填区间方向（如「北京→上海」），没有则 null。"
    )


def _slots_from(data: dict) -> Slots:
    def g(k: str) -> str | None:
        v = data.get(k)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return Slots(location=g("location"), target=g("target"), time=g("time"),
                 direction=g("direction"), extra=g("extra"), raw=dict(data))


async def decide(message: str, history: list[dict] | None = None) -> tuple[Intent, str, Slots, str]:
    """返回 (intent, question_type, slots, planner)。"""
    settings = get_settings()
    t0 = time.perf_counter()

    # 站点库是包内静态资源（3384 站，实测 ~0ms，不联网）；必须先加载，
    # 否则快路径里的"站名最长匹配"会静默退回空串、白白丢掉快路径命中率。
    try:
        await rt.ensure_loaded()
    except Exception as e:  # noqa: BLE001
        _log.warning("站点库加载失败（快路径站名匹配将不可用）：%s: %s", type(e).__name__, e)

    # ---- 1) 确定性快路径（0 次 LLM）----
    if getattr(settings, "fastpath_enabled", True):
        try:
            fp = await fastpath.plan(message, history)
        except Exception as e:  # noqa: BLE001 —— 快路径异常绝不能影响可用性
            _log.warning("快路径判断异常，回退 LLM：%s: %s", type(e).__name__, e)
            fp = None
        if fp is not None:
            _log.info("快路径命中：%s（%s）· %.0fms", fp.reason, ", ".join(fp.matched),
                      (time.perf_counter() - t0) * 1000)
            return Intent(fp.intent), fp.question_type, fp.slots, "deterministic"

    # ---- 2) 合并调用（1 次 LLM）----
    try:
        data = await chat_structured(
            _merged_prompt(message, history),
            schema=COMBINED_JSON_SCHEMA,
            no_think=bool(getattr(settings, "llm_structured_no_think", True)),
        )
        intent_val = str((data or {}).get("intent") or "").strip()
        q_type = str((data or {}).get("question_type") or "").strip()
        if intent_val in _VALID_INTENTS:
            if q_type not in _VALID_Q_TYPES:
                q_type = "realtime"        # 从严回退（与既有策略一致）
            return Intent(intent_val), q_type, _slots_from(data or {}), "llm-merged"
        _log.warning("合并调用输出非法 intent=%r，回退两次调用", intent_val)
    except LLMUnavailable:
        raise                          # LLM 不可用是**硬故障**，交由编排层统一降级
    except Exception as e:  # noqa: BLE001
        _log.warning("合并调用异常，回退两次调用：%s: %s", type(e).__name__, e)

    # ---- 3) 传统两次调用（兜底，行为与优化前一致）----
    intent_, detail = await intent.classify(message, history=history)
    q_type = str((detail or {}).get("question_type") or "realtime")
    slots = await extract.fill(message, intent=intent_.value, history=history)
    return intent_, q_type, slots, "llm-legacy"


# 供测试/回归对照用：把两级 schema 暴露出来（测试会断言它们与合并 schema 字段一致）
__all__ = ["decide", "INTENT_JSON_SCHEMA", "SLOTS_JSON_SCHEMA", "COMBINED_JSON_SCHEMA"]
