"""LLM 客户端封装包。

提供 OpenAI 兼容接口的客户端：
- chat(...)            普通对话/文本生成
- chat_structured(...) 结构化输出（意图分类、槽位抽取用）
- get_client()         llm 单例入口
"""
