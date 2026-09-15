"""意图分类识别层。

用 LLM 结构化输出，把用户输入归类为预定义的 Intent 枚举之一，
并将分类结果连同用户输入用于后续槽位抽取。
"""
from __future__ import annotations

from enum import Enum

from app.context import format_history
from app.llm.client import chat_structured
from app.pipeline.schemas import INTENT_JSON_SCHEMA


class Intent(str, Enum):
    PHOTO_SPOT = "photo_spot"      # 拍摄点/机位推荐（要拍CR400AF这类）
    SCHEDULE = "schedule"          # 列车时刻/运行信息查询
    EMU_ROUTING = "emu_routing"    # 动车组交路/担当车组查询
    RAIL_LINE = "rail_line"        # 线路/径路/里程查询
    STATION = "station"            # 车站/站点信息
    TICKET = "ticket"              # 余票查询
    NEWS = "news"                  # 铁路资讯
    GENERAL = "general"            # 其他/闲聊

    @property
    def label_zh(self) -> str:
        return INTENT_LABEL_ZH.get(self, self.value)


INTENT_LABEL_ZH: dict[Intent, str] = {
    Intent.PHOTO_SPOT: "拍摄点/机位推荐",
    Intent.SCHEDULE: "列车时刻查询",
    Intent.EMU_ROUTING: "车组交路查询",
    Intent.RAIL_LINE: "线路径路查询",
    Intent.STATION: "车站信息查询",
    Intent.TICKET: "余票查询",
    Intent.NEWS: "铁路资讯",
    Intent.GENERAL: "其他/闲聊",
}


class QuestionType(str, Enum):
    """问题性质——决定生成层能否使用模型自身知识。"""

    REALTIME = "realtime"     # 依赖实时/事实性数据，必须依据检索事实
    KNOWLEDGE = "knowledge"   # 知识型，允许结合模型知识（需标注来源与不确定度）
    MIXED = "mixed"           # 兼有，需分别处理


QUESTION_TYPE_LABEL_ZH: dict[QuestionType, str] = {
    QuestionType.REALTIME: "实时数据型",
    QuestionType.KNOWLEDGE: "知识型",
    QuestionType.MIXED: "混合型",
}


async def classify(message: str, history: list[dict] | None = None) -> tuple[Intent, dict]:
    """对用户输入做意图分类。

    返回 (Intent, 原始结构化 dict)。若返回的 intent 值不在枚举中，则回退为 GENERAL。
    dict 同时包含规范化的 question_type（realtime/knowledge/mixed），
    供生成层切换"能否使用模型知识"的作答策略。
    history 提供多轮上下文，用于判断"那明天呢"这类承接性输入的意图。
    """
    ctx = format_history(history)
    prompt = (
        "请判断下面这条铁路相关请求的主要意图（intent）与问题性质（question_type），"
        "并从 schema 的枚举中选择：\n"
        + (f"\n[前文对话]\n{ctx}\n" if ctx else "")
        + f"\n[本次用户输入]\n{message}\n\n"
        "注意：\n"
        "1. intent 应针对【本次用户输入】判断；前文仅用于消解指代"
        "（如“那明天呢”需承接前文的话题）。\n"
        "2. question_type 的关键区分：**答案是否必须依赖数据源检索**？\n"
        "   需要检索数据源才能答准的 → realtime，包括：\n"
        "     列车时刻/余票/今日担当车组/开行状态/车站信息/**两站间径路与里程**。\n"
        "     （注意：径路里程虽然长期稳定，但属于具体事实，模型不应凭记忆给出，\n"
        "       因此判为 realtime。）\n"
        "   答案相对稳定、靠铁路常识即可作答的 → knowledge，包括：\n"
        "     车型技术参数（动力系统/牵引功率/编组/厂商品牌）、发展历史、\n"
        "     样车或试验车编号、命名规则、型号谱系对比（如“复兴号与和谐号的区别”）。\n"
        "   两者兼有 → mixed（如今日是否开行 + 该车型动力系统）。\n\n"
        "参考示例（务必对齐）：\n"
        '  "G1今天由哪组动车组担当？"        → intent=emu_routing, question_type=realtime\n'
        '  "明天北京到上海还有票吗？"         → intent=ticket, question_type=realtime\n'
        '  "北京到上海走哪条线路？"           → intent=rail_line, question_type=realtime\n'
        '  "CR400AF用的哪个品牌的动力系统？"  → intent=general, question_type=knowledge\n'
        '  "复兴号和和谐号的区别是什么？"       → intent=general, question_type=knowledge\n'
        '  "CR400AF样车的车组号是多少？"       → intent=emu_routing, question_type=knowledge\n'
        '  "从上海怎么去迪士尼方便？要坐什么线？" → intent=general, question_type=knowledge\n'
        '  "机场到市区怎么走最方便？"           → intent=general, question_type=knowledge\n'
        "  （判别要点：问“今天/某日 具体是哪个”多为 realtime；\n"
        "    与铁路无关的城市交通/出行方式（地铁、公交、打车、怎么去某地）→ general + knowledge，"
        "不要判成 rail_line 或 ticket。）"
    )
    data = await chat_structured(prompt, schema=INTENT_JSON_SCHEMA)
    data = data if isinstance(data, dict) else {}
    raw = data.get("intent")
    try:
        intent = Intent(raw) if raw else Intent.GENERAL
    except ValueError:
        intent = Intent.GENERAL

    raw_qt = data.get("question_type")
    try:
        qtype = QuestionType(raw_qt) if raw_qt else QuestionType.REALTIME
    except ValueError:
        qtype = QuestionType.REALTIME

    return intent, {
        **data,
        "intent": intent.value,
        "question_type": qtype.value,
    }
