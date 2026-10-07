"""Structured fare contract: fact fidelity, incomplete data and delivery parity."""
import os
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.display_result import serialize_display_results
from app.pipeline import orchestrator, service_batch
from app.pipeline.retrieve import retrieve
from app.pipeline.extract import Slots
from app.tools import _rt12306 as rt
from app.tools.base import ToolResult
from app.tools.ticket_price import TicketPriceTool

DAY = '2026-10-07'
SOURCE = 'https://kyfw.12306.cn/otn/leftTicketPrice/queryAllPublicPrice'
CONTEXT = dict(train='G1', date=DAY, from_station='北京南', to_station='上海虹桥')
ROW = dict(train_code='G1', from_station='北京南', to_station='上海虹桥', start_time='09:00',
           arrive_time='13:30', duration='04:30', prices={'二等座':795.0, '商务座':'2782.00'})


def receipt(rows):
    return dict(tool='ticket.price', query_context=dict(CONTEXT),
                data={'data':rows, 'query_date':DAY}, sources=[SOURCE], fetched_at='2026-10-06T11:00:00+08:00')


class FareProjection(unittest.TestCase):
    def setUp(self):
        p = patch.dict(os.environ, APP_VARIANT='main');p.start();self.addCleanup(p.stop)

    def test_values_sources_and_no_availability(self):
        item = serialize_display_results([receipt([dict(ROW)])])[0]
        self.assertEqual(item['schema_version'],1)
        self.assertEqual(item['status'],'success')
        for key in ['train_code','from_station','to_station','start_time','arrive_time','duration']:
            self.assertEqual(item[key],ROW[key])
        self.assertEqual(item['date'],DAY)
        self.assertEqual(item['prices'],[{'seat':'二等座','amount':'795.0','currency':'CNY','raw_amount':795.0},
                                        {'seat':'商务座','amount':'2782.00','currency':'CNY','raw_amount':'2782.00'}])
        self.assertEqual(item['sources'],[SOURCE]);self.assertEqual(item['fetched_at'],receipt([])['fetched_at'])
        self.assertNotIn('availability',item)

    def test_actual_intervals_are_not_query_substitutions(self):
        items = serialize_display_results([receipt([dict(ROW),dict(ROW,to_station='南京南',prices={'二等座':500})])])
        self.assertEqual([i['to_station'] for i in items],['上海虹桥','南京南'])
        self.assertEqual(items[1]['prices'][0]['amount'],'500')

    def test_missing_and_invalid_amounts_are_not_zero(self):
        for value in [None, '', 'NaN', 'Infinity', True, -1, '待定']:
            item=serialize_display_results([receipt([dict(ROW,prices={'硬座':value})])])[0]
            self.assertEqual(item['status'],'partial');self.assertIsNone(item['prices'][0]['amount'])
            self.assertEqual(item['prices'][0]['raw_amount'],value)
        item=serialize_display_results([receipt([dict(ROW,prices={'硬座':0})])])[0]
        self.assertEqual(item['status'],'success');self.assertEqual(item['prices'][0]['amount'],'0')

    def test_missing_times_and_prices(self):
        item=serialize_display_results([receipt([dict(ROW,start_time=None,prices={})])])[0]
        self.assertEqual(item['status'],'partial');self.assertIsNone(item['start_time']);self.assertEqual(item['prices'],[])

    def test_empty_failure_and_malformed_are_distinct(self):
        empty=serialize_display_results([receipt([])])[0]
        error=dict(tool='ticket.price',query_context=CONTEXT,message='超时',sources=[],note='失败说明')
        failure=serialize_display_results([], [error])[0]
        self.assertEqual((empty['status'],failure['status']),('empty','failed'))
        for item in [empty,failure]:
            self.assertEqual(item['train_code'],'G1');self.assertEqual(item['date'],DAY)
            self.assertEqual(item['from_station'],'北京南');self.assertEqual(item['to_station'],'上海虹桥')
        self.assertIsNone(failure['fetched_at']);self.assertEqual(failure['error'],'超时')
        bad=receipt([]);bad['data']={}
        self.assertEqual(serialize_display_results([bad])[0]['status'],'failed')

    def test_missing_or_wrong_identity_cannot_be_success(self):
        for row in [dict(ROW,from_station=None),dict(ROW,train_code='G2'),dict(ROW,date='2026-10-08')]:
            item=serialize_display_results([receipt([row])])[0]
            self.assertEqual(item['status'],'failed');self.assertTrue(item['error'])
            self.assertEqual(item['train_code'],'G1');self.assertEqual(item['date'],DAY)
            self.assertEqual(item['prices'],[])
        data=receipt([dict(ROW)]);data.pop('query_context');data['data'].pop('query_date')
        self.assertIsNone(serialize_display_results([data])[0]['date'])

    def test_nonfinite_raw_amounts_remain_strict_json(self):
        for raw in [float('nan'),float('inf'),float('-inf')]:
            value=serialize_display_results([receipt([dict(ROW,prices={'硬座':raw})])])[0]
            self.assertEqual(value['status'],'partial');self.assertIsNone(value['prices'][0]['amount'])
            self.assertEqual(value['prices'][0]['raw_amount'],str(raw))
            json.dumps(value,allow_nan=False)

    def test_payload_date_cannot_override_request_even_for_empty(self):
        for rows in [[],[dict(ROW)],[dict(ROW,date='2026-10-06')]]:
            item=receipt(rows);item['data']['query_date']='2026-10-06'
            value=serialize_display_results([item])[0]
            self.assertEqual(value['status'],'failed');self.assertEqual(value['date'],DAY)
            self.assertEqual(value['prices'],[])

    def test_lm_contract_is_unchanged(self):
        with patch.dict(os.environ,APP_VARIANT='lm'):
            self.assertEqual(serialize_display_results([receipt([ROW])]),[])
            item=serialize_display_results([],[{'tool':'ticket.price','message':'失败'}])[0]
            self.assertEqual(item['kind'],'error')


class FareDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        p=patch.dict(os.environ, APP_VARIANT='main');p.start();self.addCleanup(p.stop)
        await rt.ensure_loaded()

    async def test_real_tool_normalized_date_and_original_text(self):
        with patch.object(rt,'query_ticket_prices',AsyncMock(return_value={'data':[dict(ROW)],'from_station':'北京南','to_station':'上海虹桥'})):
            tool=await TicketPriceTool().invoke(CONTEXT)
        self.assertEqual(tool.data['query_date'],DAY)
        self.assertTrue(tool.fetched_at.endswith('+00:00'))
        self.assertIn('二等座 795 元',tool.text)
        self.assertIn('票价可作为购票参考，具体以购票页面为准。',tool.text)

    async def test_legacy_scalar_retrieve_success_and_failure_keep_scope(self):
        slots=Slots(target='G1',time=DAY,direction='北京南到上海虹桥')
        for ok in [True,False]:
            tool=ToolResult(ok=ok,data={'data':[dict(ROW)],'query_date':DAY} if ok else None,
                            text='原始票价795元',error='' if ok else '接口超时',sources=[SOURCE])
            async def invoke(name, params):
                if name == 'ticket.query':
                    return ToolResult(ok=ok,data={'train_date':DAY,'from_station':'北京南','to_station':'上海虹桥',
                        'trains':[dict(train_no='G1',from_station='北京南',to_station='上海虹桥',seats={'second_class':'候补'})]} if ok else None,
                        error='' if ok else '余票超时')
                self.assertEqual(name,'ticket.price')
                return tool
            with patch('app.tools.registry.invoke_by_name',invoke):
                response=await retrieve('ticket',slots,'realtime','G1北京南到上海虹桥票价')
            value=serialize_display_results(response['data'],response['display_errors'])[0]
            self.assertEqual(value['status'],'success' if ok else 'failed')
            self.assertEqual(value['date'],DAY);self.assertEqual(value['train_code'],'G1')
            self.assertEqual(value['fare_status'],'success' if ok else 'failed')
            self.assertEqual(value['availability']['status'],'success' if ok else 'failed')
            self.assertIn('原始票价' if ok else '接口超时',response['direct_answer'])

    async def test_stream_block_partial_multitrain_multidate(self):
        async def invoke(name, params):
            self.assertIn(name,{'ticket.price','ticket.query'})
            if name=='ticket.query':
                if params['train']=='G2':return ToolResult(ok=False,error='余票超时')
                rows=[] if params['date']=='2026-10-08' else [dict(train_no='G1',from_station='北京南',to_station='上海虹桥',seats={'second_class':'无'})]
                return ToolResult(ok=True,data={'train_date':params['date'],'from_station':'北京南','to_station':'上海虹桥','trains':rows})
            if params['train']=='G2':return ToolResult(ok=False,error='接口超时',note='票价不代表余票')
            rows=[] if params['date']=='2026-10-08' else [dict(ROW)]
            return ToolResult(ok=True,data={'data':rows,'query_date':params['date']},
                              text='原始票价回执：二等座 795 元' if rows else '未查到精确票价记录',sources=[SOURCE],fetched_at='采样时间')
        outputs=[]
        with patch('app.tools.registry.invoke_by_name',invoke), \
             patch.object(orchestrator.planner,'decide',AsyncMock(side_effect=AssertionError('no model'))), \
             patch.object(orchestrator.llm_client,'stream_completion',side_effect=AssertionError('no model')), \
             patch('app.pipeline.generate.chat_with_reasoning',AsyncMock(side_effect=AssertionError('no model'))):
            text='G1、G2 2026-10-07、2026-10-08 北京南到上海虹桥的票价'
            block=await orchestrator.run(text);outputs.append(block.display_results)
            events=[e async for e in orchestrator.run_stream(text)]
            done=next(e for e in events if e['type']=='done');outputs.append(done['display_results'])
            answer=''.join(e.get('delta','') for e in events if e['type']=='answer')
            for prose in [block.answer,answer]:
                self.assertIn('原始票价回执',prose);self.assertIn('接口超时',prose);self.assertIn('未查到精确票价记录',prose)
        self.assertEqual(*outputs)
        self.assertEqual([(i['train_code'],i['date'],i['status']) for i in outputs[0]],
                         [('G1','2026-10-07','success'),('G1','2026-10-08','empty'),
                          ('G2','2026-10-07','failed'),('G2','2026-10-08','failed')])


if __name__=='__main__':unittest.main()
