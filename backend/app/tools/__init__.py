"""数据源工具包。

每个工具实现统一的 invoke 接口（供 Agent/检索层调用）：
    async def invoke(params: dict) -> ToolResult
并登记到 registry.py 的工具注册表。

现有：
- t12306.py  —— 12306 余票/时刻查询（参考 metromancn/Parse12306）
- railre.py  —— rail.re（原 moerail.ml）车站及动车组交路
- cnrail.py  —— cnrail.geogv.org 铁路地图外链
- web.py     —— web_search / web_fetch 兜底检索
"""
