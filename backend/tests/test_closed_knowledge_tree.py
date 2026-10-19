import asyncio
import copy
import unittest
from unittest.mock import patch
from app.pipeline.decision_tree import closed_knowledge
from app.pipeline import planner
from app.config import get_settings

class KnowledgeTree(unittest.TestCase):
    def test_closed_forms_preserve_subjects(self):
        for text,subject in [('什么是重联？','重联'),('请问CR400AF的功率是多少？','CR400AF'),('CR200J为什么被叫绿巨人？','CR200J'),('CR400AF用的哪个品牌的动力系统？','CR400AF'),('CR400BF的牵引功率多大？','CR400BF'),('介绍一下CRH5这个车型','CRH5'),('CRH380B最高运行速度多少？','CRH380B')]:
            result=closed_knowledge(text)
            self.assertEqual((result.intent,result.question_type,result.slots.target),('general','knowledge',subject))
        result=closed_knowledge('CR400AF和CR400BF有什么区别？')
        self.assertEqual(result.slots.target,'CR400AF')
        self.assertIn('CR400BF',result.slots.extra)
    def test_unsafe_or_open_forms_defer(self):
        for text in ['G1今天几点到上海虹桥？','CR400AF今天由哪组担当？','为什么CR400AF今天停运？','什么是重联，明天G1用什么车？','只有同车型才能重联吗？','CR400AF和CR400BF哪个好？','CR400AF动力系统现在什么状态？','明天后天G1和G2票价','CR400AF-5033的功率是多少？']:
            self.assertIsNone(closed_knowledge(text),text)
        self.assertIsNone(closed_knowledge('什么是重联？',[{'role':'user','content':'G1今天的交路'}]))
    def test_no_llm_for_accepted_leaf(self):
        async def forbidden(*args,**kw):raise AssertionError('unexpected model call')
        with patch.object(planner,'chat_structured',forbidden),patch.object(planner.rt,'ensure_loaded',forbidden):
            decision=asyncio.run(planner.decide('什么是重联？'))
        self.assertEqual(decision[3],'deterministic')
    def test_switch_and_lm_do_not_use_new_leaf(self):
        async def model(*args,**kw):return {'intent':'general','question_type':'knowledge','target':'重联'}
        s=copy.copy(get_settings()); s.closed_knowledge_tree_enabled=False
        with patch.object(planner,'get_settings',return_value=s),patch.object(planner,'chat_structured',model):
            self.assertEqual(asyncio.run(planner.decide('什么是重联？'))[3],'llm-merged')
        s.closed_knowledge_tree_enabled=True; s.fastpath_enabled=False
        with patch.object(planner,'get_settings',return_value=s),patch.object(planner,'chat_structured',model):
            self.assertEqual(asyncio.run(planner.decide('什么是重联？'))[3],'llm-merged')
        with patch.dict('os.environ',APP_VARIANT='lm'),patch.object(planner,'chat_structured',model):
            self.assertEqual(asyncio.run(planner.decide('什么是重联？'))[3],'llm-merged')

if __name__=='__main__':unittest.main()
