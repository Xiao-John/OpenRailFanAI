"""意图分类与槽位抽取用的 JSON Schema 定义。

供 pipeline/intent.py 与 pipeline/extract.py 复用，传给
llm.chat_structured 做结构化约束。
"""
from __future__ import annotations

# ---- 意图分类 schema ----
INTENT_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": (
                "用户主意图，取值从以下枚举中选择一个：\n"
                '1. "photo_spot"  —— 拍摄点/机位推荐（含“要拍/拍摄/机位/蹲点”等拍摄意图）\n'
                '2. "schedule"    —— 列车时刻/运行信息查询（含“几点的车/时刻表/历时/经停站”）\n'
                '3. "emu_routing" —— 动车组交路/担当车组查询（含“担当/车底/车组号/交路/由哪组担当/跑哪些车次”）\n'
                '4. "rail_line"   —— 铁路线路/径路/里程查询（含“走哪条线/经过哪些线路/径路/多少公里/里程/线路走向”）\n'
                '5. "station"     —— 车站/站点信息\n'
                '6. "ticket"      —— 余票查询\n'
                '7. "news"        —— 铁路资讯/动态\n'
                '8. "general"     —— 其他、闲聊、或多意图无明确主诉求'
            ),
        },
        "question_type": {
            "type": "string",
            "description": (
                "问题性质，决定回答时能否使用模型自身知识。取值：\n"
                '1. "realtime"  —— 依赖实时/事实性数据，答案会随时间或具体对象变化：\n'
                "   列车时刻、余票、今日担当车组、开行状态、实时径路里程、当前配属等。\n"
                "   此类必须依据检索事实，不得凭记忆作答。\n"
                '2. "knowledge" —— 知识型问题，答案相对稳定、不依赖实时数据：\n'
                "   车型技术参数（动力系统/牵引功率/编组）、品牌与厂商、发展历史、\n"
                "   样车或试验车编号、命名规则、技术路线演进等。\n"
                "   此类允许结合模型已有知识作答（需标注来源与不确定度）。\n"
                '3. "mixed"     —— 兼有：既有实时诉求（如今日是否开行）又有知识诉求\n'
                "   （如该车型用什么动力系统）。需分别处理。"
            ),
        },
    },
    "required": ["intent", "question_type"],
}

# ---- 槽位抽取 schema ----
SLOTS_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "location": {
            "type": ["string", "null"],
            "description": "地点，如“吉林市XX区”。可为 null。",
        },
        "target": {
            "type": ["string", "null"],
            "description": "目标对象：拍摄的车型/车次/列车，或查询目标的列车，如“CR400AF”。可为 null。",
        },
        "time": {
            "type": ["string", "null"],
            "description": "时间表述，可归一化，如“今天下午”“今天14:30”。可为 null。",
        },
        "direction": {
            "type": ["string", "null"],
            "description": "方向/区间，如“上行”“哈尔滨方向”“XX站到XX站”。可为 null。",
        },
        "extra": {
            "type": ["string", "null"],
            "description": "其余未归类但对任务重要的细节，用一句话概括；抓不到的填 null。",
        },
    },
    "required": ["location", "target", "time", "direction", "extra"],
}


# ---- 合并调用 schema（perf P0-2）：一次结构化调用同时产出意图 + 问题性质 + 槽位 ----
# 为什么能合并：意图分类与槽位抽取读的是**同一条用户输入**，之前拆成两次往返
# （实测各 1.2–9.3s）纯属浪费。这里用一份 schema 一次拿全，省掉一次 LLM 往返。
def _combined_schema() -> dict:
    """由两份既有 schema 组合而成（保持字段描述完全一致，避免两处描述漂移）。"""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "intent": INTENT_JSON_SCHEMA["properties"]["intent"],
            "question_type": INTENT_JSON_SCHEMA["properties"]["question_type"],
            **SLOTS_JSON_SCHEMA["properties"],
        },
    }


COMBINED_JSON_SCHEMA = _combined_schema()
