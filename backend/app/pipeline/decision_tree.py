"""Closed, context-free technical questions; unknown forms always defer."""
import re
from app.pipeline.fastpath import FastPlan
from app.pipeline.extract import Slots

_MODEL=r'CR(?:H)?\d{1,3}[A-Z]{0,3}'
_COMPARE=re.compile(rf'(?P<a>{_MODEL})\s*(?:和|与)\s*(?P<b>{_MODEL})\s*(?:到底)?(?:有什么区别|有何区别|有哪些区别|有啥区别|的区别)',re.I)
_TECHNICAL=re.compile(rf'(?P<a>{_MODEL})\s*(?:的)?\s*(?:技术参数(?:是什么)?|参数(?:是什么)?|(?:牵引)?功率(?:是多少|多少|多大)?|最高(?:运行|运营)?速度(?:是多少|多少)?|厂家(?:是谁|是哪家)?|是哪个厂家造的|用的哪个品牌的动力系统|动力系统(?:是什么|用的哪个品牌)|为什么(?:被叫|叫)(?:复兴号|和谐号|绿巨人))',re.I)
_INTRO=re.compile(rf'介绍一下(?P<a>{_MODEL})(?:这个车型)?',re.I)
_DEFINITION=re.compile(r'(?:什么是|解释一下)(?P<a>重联|动车组|动力集中|动力分散)')

def closed_knowledge(message,history=None):
    # No inherited entity, date, operation or free-form explanation is inferred.
    if history: return None
    text=(message or '').strip().rstrip('？?。').strip()
    if not text or len(text)>80: return None
    text=re.sub(r'^请问\s*','',text)
    for name,pattern in [('model_comparison',_COMPARE),('model_technical',_TECHNICAL),('model_introduction',_INTRO),('term_definition',_DEFINITION)]:
        match=pattern.fullmatch(text)
        if match:
            subject=match['a'].upper() if name!='term_definition' else match['a']
            extra='对比型号：'+match['b'].upper() if name=='model_comparison' else None
            return FastPlan('general','knowledge',Slots(target=subject,extra=extra),
                            '闭合知识问法：'+name,[name])
    return None
