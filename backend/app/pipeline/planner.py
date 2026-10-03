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
from contextvars import ContextVar
from typing import Callable

from app.config import get_settings
from app.llm.client import LLMOutputInvalid, LLMUnavailable, chat_structured
from app.pipeline import extract, fastpath, intent
from app.pipeline.extract import Slots
from app.pipeline.intent import Intent
from app.pipeline.schemas import COMBINED_JSON_SCHEMA, INTENT_JSON_SCHEMA, SLOTS_JSON_SCHEMA
from app.tools import _rt12306 as rt

_log = logging.getLogger("railfan.planner")

_VALID_INTENTS = {i.value for i in Intent}
_VALID_Q_TYPES = {"realtime", "knowledge", "mixed"}

# Internal request hook, deliberately outside decide's public signature so existing
# callers and test substitutes retain their compatibility.
on_llm_decision: ContextVar[Callable[[], None] | None] = ContextVar(
    "planner_on_llm_decision", default=None
)


# 本地小模型的输出模板（`LLM_STRUCTURED_COMPACT_PROMPT=true` 时用它替代 schema 原文）。
#
# 为什么要另写一份，而不是继续把 JSON Schema 拼进提示词：
#   实测（Qwen3.5-2B / Q4_K_M，本机）把带中文描述的 schema 原文甩给 2B 模型，
#   **19 条语料里合并调用全军覆没** —— 模型把 schema 骨架当成答案模板照抄回来：
#       {"type": "object", "properties": {"intent": "查询余票", "location": {}, ...}}
#   换成"模板 + 算例"后立刻正常（含多轮省略句「那商务座呢」正确继承 target=G1）。
#   顺带把提示词从 ~1072 token 压到 ~300。
#
# **算例要覆盖"它自己想不到"的意图**：语料实测小模型的错几乎全集中在 emu_routing
# （交路/担当车组）与 station（某座车站本身）——这两类靠常识猜不出来，它会退回
# general/schedule。所以算例里专门给这两类各一个，并把最容易混的
# 「某条线路经过哪些车站」（rail_line）拿来当反例钉住。
#
# 算例不怕多：静态前缀会被 Ollama / llama-server 的前缀缓存吃掉，只在第一次请求付钱。
# **但第二个算例必须是省略句**：多轮继承是小模型最容易丢的能力，示范一次比讲一段道理管用。
_SLM_OUTPUT_TEMPLATE = (
    "\n\n只输出下面这个结构的 JSON（照抄键名，把值换成答案；没提到的填 null）：\n"
    '{"intent": "…", "question_type": "…", "location": null, "target": null,'
    ' "time": null, "direction": null, "extra": null}\n'
    "intent 只能取这 8 个之一：\n"
    "  photo_spot(拍摄点/机位) schedule(时刻/经停/历时/开行状态)\n"
    "  emu_routing(交路/担当车组/车底/哪个局/什么型号) rail_line(线路/径路/里程)\n"
    "  station(某座车站本身：大屏/检票口/电报码/在哪个城市) ticket(余票) news(资讯动态)\n"
    "  general(其他、铁路常识、闲聊)\n"
    "判断口径（**intent 表示该调哪组数据源，不是这句话提到了什么**）：\n"
    "  问题是「车型参数/技术规格（功率、最高速度、编组）/ 概念对比（有什么区别）/\n"
    "  名称由来、发展历史 / 站外交通」这类**铁路实时接口给不了、要靠常识或网络搜索**的，\n"
    "  即使提到了车次、车型或车站，intent 也取 general；question_type 取 knowledge。\n"
    "  反例：问机位（photo_spot 有网络搜索兜底）、问开通/停运（news 同理）→ 保持原 intent。\n"
    "question_type 只能取这 3 个之一：realtime(必须查数据才能答准) knowledge(铁路常识即可) mixed\n"
    "location=地点 target=车次号/车型/车组号/线路名 time=时间原话"
    " direction=区间如「北京→上海」 extra=其他要点\n"
    "示例输入：明天北京到上海还有票吗？\n"
    '示例输出：{"intent": "ticket", "question_type": "realtime", "location": null,'
    ' "target": null, "time": "明天", "direction": "北京→上海", "extra": null}\n'
    "示例输入：那商务座呢\n"
    '示例输出：{"intent": "ticket", "question_type": "realtime", "location": null,'
    ' "target": "G1", "time": null, "direction": null, "extra": "席别换成商务座"}\n'
    "示例输入：G1今天由哪组动车组担当？\n"
    '示例输出：{"intent": "emu_routing", "question_type": "realtime", "location": null,'
    ' "target": "G1", "time": "今天", "direction": null, "extra": null}\n'
    "示例输入：这车是哪个局配属的\n"
    '示例输出：{"intent": "emu_routing", "question_type": "realtime", "location": null,'
    ' "target": "G1", "time": null, "direction": null, "extra": "问配属局"}\n'
    "示例输入：上海虹桥站的检票口在哪边\n"
    '示例输出：{"intent": "station", "question_type": "realtime", "location": "上海虹桥",'
    ' "target": null, "time": null, "direction": null, "extra": null}\n'
    "示例输入：京沪线经过哪些车站？\n"
    '示例输出：{"intent": "rail_line", "question_type": "realtime", "location": null,'
    ' "target": "京沪线", "time": null, "direction": null, "extra": null}\n'
    "（注意最后这个：问的是**线路**沿线车站 → rail_line，不是 station；"
    "只有问某一座车站自身（大屏/检票口/电报码）才是 station。）\n"
    "示例输入：为什么高铁要叫复兴号？\n"
    '示例输出：{"intent": "general", "question_type": "knowledge", "location": null,'
    ' "target": "复兴号", "time": null, "direction": null, "extra": null}\n'
    "示例输入：CR400AF和CR400BF有什么区别？\n"
    '示例输出：{"intent": "general", "question_type": "knowledge", "location": null,'
    ' "target": "CR400AF", "time": null, "direction": null, "extra": null}\n'
    "（这两个是 knowledge：车型参数/命名由来**不随时间变**，靠常识就能答。"
    "反过来，只要答案取决于「今天/现在/某个具体车次或车组」→ realtime。）\n"
)


def _merged_static_prompt(*, compact: bool = False) -> str:
    """合并调用的**静态前缀**：把两次调用里必要的判定要点压成一份，去掉重复的解释文字。

    **这里不许出现任何随请求变化的内容**（用户输入、前文对话都不行）——
    见 `_merged_user_tail` 的说明：本地推理里"前缀能不能被缓存"直接决定延迟。

    `compact=True` 时另附模板 + 算例代替 schema 原文 —— 本地小模型必须走这条
    （理由见 `_SLM_OUTPUT_TEMPLATE`）；云端模型继续走 schema 原文，行为完全不变。
    """
    prompt = (
        "你是铁路出行助手的输入解析器。读最后那条用户输入，一次性给出：\n"
        "1) 主意图 intent；2) 问题性质 question_type；3) 关键槽位（location/target/time/direction/extra）。\n\n"
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
    return prompt + _SLM_OUTPUT_TEMPLATE if compact else prompt


def _merged_user_tail(message: str, history: list[dict] | None) -> str:
    """本次用户输入（含前文对话）—— **必须拼在整个提示词的最后**。

    为什么单独拆出来（而不是留在 `_merged_static_prompt` 里当中间一段）：
    本地推理的延迟几乎全在 **prefill**（实测 1072 token 的提示词 19.8s、78 token 的 4.6s，
    输出都只有几十个 token），而 Ollama / llama-server 会**复用上一次请求的公共前缀**
    （KV cache，Ollama 日志里的 `created context checkpoint` 就是它）。
    用户输入夹在中间，等于把后面的 schema/模板/判定要点全部作废 —— 每次都要重算。
    挪到最后之后，可缓存前缀是"指令 + 判定要点 + （schema 或模板）"，
    每次真正要算的只剩几十个 token。

    云端模型不受影响（它不按前缀计费），但也没有理由把顺序写坏。
    """
    from app.context import format_history

    ctx = format_history(history)
    return (
        (f"\n\n[前文对话]\n{ctx}" if ctx else "")
        + f"\n\n[本次用户输入]\n{message}\n"
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


async def decide(message: str, history: list[dict] | None = None
           ) -> tuple[Intent, str, Slots, str, str]:
    """返回 (intent, question_type, slots, planner, defer_reason)。

    defer_reason 只在"快路径没接管"时有值（见 fastpath.Defer）：它说明**为什么**交回 LLM，
    是排查"这句为什么走了慢路径/判错了"的第一手信息。
    """
    settings = get_settings()
    t0 = time.perf_counter()
    defer = None

    # 站点库是包内静态资源（3384 站，实测 ~0ms，不联网）；必须先加载，
    # 否则快路径里的"站名最长匹配"会静默退回空串、白白丢掉快路径命中率。
    try:
        await rt.ensure_loaded()
    except Exception as e:  # noqa: BLE001
        _log.warning("站点库加载失败（快路径站名匹配将不可用）：%s: %s", type(e).__name__, e)

    # ---- 1) 确定性快路径（0 次 LLM）----
    if getattr(settings, "fastpath_enabled", True):
        try:
            fp, defer = await fastpath.plan_with_reason(message, history)
        except Exception as e:  # noqa: BLE001 —— 快路径异常绝不能影响可用性
            _log.warning("快路径判断异常，回退 LLM：%s: %s", type(e).__name__, e)
            fp, defer = None, None
        if fp is not None:
            _log.info("快路径命中：%s（%s）· %.0fms", fp.reason, ", ".join(fp.matched),
                      (time.perf_counter() - t0) * 1000)
            return Intent(fp.intent), fp.question_type, fp.slots, "deterministic", ""

    callback = on_llm_decision.get()
    if callback is not None:
        callback()

    # ---- 2) 合并调用（1 次 LLM）----
    # 本地小模型走"模板 + 算例"，云端走 schema 原文（见 _SLM_OUTPUT_TEMPLATE 的实测理由）；
    # 用户输入单独作为 prompt_tail 拼在最后（见 _merged_user_tail：本地推理靠前缀缓存提速）
    compact = bool(getattr(settings, "llm_structured_compact_prompt", False))
    try:
        data = await chat_structured(
            _merged_static_prompt(compact=compact),
            schema=COMBINED_JSON_SCHEMA,
            no_think=bool(getattr(settings, "llm_structured_no_think", True)),
            schema_in_prompt=not compact,
            prompt_tail=_merged_user_tail(message, history),
        )
        intent_val = str((data or {}).get("intent") or "").strip()
        q_type = str((data or {}).get("question_type") or "").strip()
        if intent_val in _VALID_INTENTS:
            if q_type not in _VALID_Q_TYPES:
                q_type = "realtime"        # 从严回退（与既有策略一致）
            return (Intent(intent_val), q_type, _slots_from(data or {}), "llm-merged",
                    defer.text if defer else "")
        _log.warning("合并调用输出非法 intent=%r，回退两次调用", intent_val)
    except LLMOutputInvalid as e:
        # 服务是通的，只是这次没给出能解析的 JSON。换成两次**更简单**的调用再试一次，
        # 比整体降级划算 —— 本地小模型最典型的失败就是这个（把 JSON 写成了解释性文字），
        # 而"只判意图"和"只抽槽位"两个单一任务它往往能过。
        _log.warning("合并调用输出不可解析，回退两次调用：%s", e)
    except LLMUnavailable:
        raise                          # LLM 不可用是**硬故障**，交由编排层统一降级
    except Exception as e:  # noqa: BLE001
        _log.warning("合并调用异常，回退两次调用：%s: %s", type(e).__name__, e)

    # ---- 3) 传统两次调用（兜底，行为与优化前一致）----
    intent_, detail = await intent.classify(message, history=history)
    q_type = str((detail or {}).get("question_type") or "realtime")
    slots = await extract.fill(message, intent=intent_.value, history=history)
    return intent_, q_type, slots, "llm-legacy", defer.text if defer else ""


# 供测试/回归对照用：把两级 schema 暴露出来（测试会断言它们与合并 schema 字段一致）
__all__ = ["decide", "INTENT_JSON_SCHEMA", "SLOTS_JSON_SCHEMA", "COMBINED_JSON_SCHEMA"]
