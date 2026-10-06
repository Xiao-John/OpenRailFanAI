"""Select fare meaning from an explicit request without treating negation as consent."""
import re

_PUBLIC=r'(?:公布|基准)票价'
_EXECUTED=r'(?:实际(?:执行|浮动)?|执行|浮动|折扣|购票|售票)票价'
_NEGATIVE=r'(?:不要|不用|不需要|不查|不看|别|无需|而非|不是)\s*(?:查询|查|看|提供|返回|给出|的)?\s*'


def select(message: str) -> str | None:
    text=re.sub(_NEGATIVE+'(?:'+_PUBLIC+'|'+_EXECUTED+')','',message or '')
    public=bool(re.search(_PUBLIC,text));executed=bool(re.search(_EXECUTED,text))
    if public and executed:return None
    return 'published' if public else 'executed'

CLARIFICATION='请分别查询实际执行票价与公布参考票价，明确本次需要的票价口径。'
