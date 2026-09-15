"""三层流水线包：

1. intent.py    —— 意图识别分类
2. extract.py   —— 关键信息抽取（槽位填充）
3. retrieve.py  —— 数据检索（工具调用按需取数）
4. generate.py  —— 回答生成（整合检索结果 + 数据来源）

编排入口建议在 orchestrator 或 api/chat 中串起三步，
见 docs/EXPL.md。
"""
