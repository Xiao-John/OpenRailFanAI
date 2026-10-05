"""Offline multi-date service delivery and binding regressions."""
from __future__ import annotations

import asyncio
from datetime import date
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.pipeline import orchestrator, service_batch, service_dispatch
from app.pipeline.extract import Slots
from app.pipeline.retrieve import retrieve
from app.pipeline.service_dates import select_dates
from app.tools import _rt12306 as rt
from app.tools.base import ToolResult

TODAY = date(2026, 10, 5)
DAYS = ['2026-10-06', '2026-10-07', '2026-10-08']
EXAMPLE = '明天后天大后天北京到上海虹桥G1次票价'


def result_for(name, params):
    code, day = params['train'], params['date']
    if name == 'train.schedule':
        return ToolResult(ok=True, data={'train_code':code, 'train_date':day, 'source':'12306-timetable',
            'stops_with_times':True, 'stops':[{'station':'北京南','start_time':'10:00'}]})
    if name == 'emu.routing':
        return ToolResult(ok=True, data={'query':code,'focus_date':day,
            'records':[{'train_code':code,'date':day,'time':'12:00','emu_no':'CR400BF1234'}]})
    if name == 'ticket.query':
        return ToolResult(ok=True, data={'train_date':day,
            'trains':[{'train_no':code,'seats':{'second_class':'有'}}]})
    return ToolResult(ok=True, text=f'{day} {code} {params["from_station"]}→{params["to_station"]}：硬座 {int(day[-2:])+35} 元',
                      sources=['https://example.invalid/fare'])


class DateSelectionTests(unittest.TestCase):
    def setUp(self):
        p=patch('app.pipeline.service_dates.railway_today',return_value=TODAY)
        p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{'APP_VARIANT':'main'})
        p.start();self.addCleanup(p.stop)

    def test_relative_list_longest_match_and_normalized_dedup(self):
        selection=select_dates('明天、明日、后天、大后天 G1票价')
        self.assertEqual(selection.days,DAYS)
        self.assertFalse(selection.error)
        self.assertEqual(service_dispatch.train_codes(EXAMPLE),['G1'])

    def test_explicit_month_list_short_days_and_range(self):
        for text in ['10月6日、7日、8日','10月6日到8日','2026-10-06至2026-10-08','明天到大后天']:
            selection=select_dates(text)
            self.assertEqual(selection.days,DAYS,text)
            self.assertFalse(selection.error,text)
        selection=select_dates('2026-10-31至2026-11-02')
        self.assertEqual(selection.days,['2026-10-31','2026-11-01','2026-11-02'])

    def test_calendar_and_range_errors_are_visible_before_expansion(self):
        for text in ['2026-02-30和2026-10-06','10月31日到2日','大后天至明天','2026-10-06到2027-10-06','10月6日、32日']:
            self.assertTrue(select_dates(text).error,text)
        self.assertTrue(select_dates('、'.join(f'2026-10-{i:02}' for i in range(6,17))).error)

    def test_common_lists_can_precede_or_follow_train_collection(self):
        for text in ['明天后天大后天G1、G2的时刻表','G1、G2明天后天大后天的时刻表']:
            frame=service_dispatch.decide(text)[2].raw['service_query']
            self.assertEqual(frame['dates'],DAYS)
            self.assertEqual(frame['trains'],['G1','G2'])
            self.assertNotIn('clarification',frame)

    def test_interleaved_dates_and_trains_are_not_zipped_or_crossed(self):
        for text in ['G1明天和G2后天的时刻表','明天G1、后天G2票价','G1明天后天、G2时刻表','G1明早后天票价','G1明天后天最近交路','G1明天票价后天时刻表','G1明天上午后天下午余票','G1明天二等座后天一等座余票','G1明天傍晚后天上午余票','G1明天无座后天二等座余票','G1明天软卧后天硬卧余票','G1明天特快后天动车余票']:
            frame=service_dispatch.decide(text)[2].raw['service_query']
            self.assertTrue(frame.get('clarification'),text)

    def test_lm_and_action_keep_legacy_single_date_semantics(self):
        with patch.dict(os.environ,{'APP_VARIANT':'lm'}):
            self.assertIsNone(service_dispatch.decide(EXAMPLE))
        action={'kind':'train_schedule_batch','trains':['G1'],'date':DAYS[0]}
        slots=service_dispatch.decide(EXAMPLE,action)[2]
        self.assertEqual(slots.time,DAYS[0])
        self.assertNotIn('service_query',slots.raw)


class MultiDateDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for p in [patch.dict(os.environ,{'APP_VARIANT':'main'}),
                  patch('app.pipeline.service_dates.railway_today',return_value=TODAY),
                  patch('app.dates.railway_today',return_value=TODAY),
                  patch.object(service_batch,'get_settings',return_value=SimpleNamespace(tool_concurrency=2))]:
            p.start();self.addCleanup(p.stop)
        await rt.ensure_loaded()

    async def execute(self,message,mode='stream',action=None,boundary=None,history=None):
        calls=[]
        async def invoke(name,params):
            calls.append((name,dict(params)))
            return boundary(name,params) if boundary else result_for(name,params)
        with patch('app.tools.registry.invoke_by_name',invoke), \
             patch.object(orchestrator.planner,'decide',AsyncMock(side_effect=AssertionError('decision model must not run'))), \
             patch.object(orchestrator.llm_client,'stream_completion',side_effect=AssertionError('generation model must not run')), \
             patch('app.pipeline.generate.chat_with_reasoning',AsyncMock(side_effect=AssertionError('generation model must not run'))):
            if mode=='block':
                result=await orchestrator.run(message,display_action=action,history=history)
                return result.dict(),calls,result.answer
            events=[e async for e in orchestrator.run_stream(message,display_action=action,history=history)]
            self.assertFalse([e for e in events if e['type']=='error'],events)
            done=[e for e in events if e['type']=='done']
            self.assertEqual(len(done),1)
            self.assertTrue(done[0]['answer_done'])
            self.assertFalse(done[0]['degraded'])
            return done[0],calls,''.join(e.get('delta','') for e in events if e['type']=='answer')

    async def test_user_example_three_actual_intervals_dates_without_model(self):
        outputs=[]
        for mode in ['stream','block']:
            _,calls,answer=await self.execute(EXAMPLE,mode)
            self.assertEqual([name for name,_ in calls],['ticket.price']*3)
            self.assertEqual([p['date'] for _,p in calls],DAYS)
            self.assertTrue(all((p['train'],p['from_station'],p['to_station'])==('G1','北京','上海虹桥') for _,p in calls))
            for day in DAYS:
                self.assertIn(f'G1 · {day} · 票价',answer)
                self.assertIn(f'硬座 {int(day[-2:])+35} 元',answer)
            outputs.append(answer)
        self.assertEqual(*outputs)

    async def test_self_contained_multidate_request_overrides_history_without_model(self):
        _,calls,_=await self.execute(EXAMPLE,history=[{'role':'user','content':'昨天G2天津到南京的票价'}])
        self.assertEqual([p['date'] for _,p in calls],DAYS)
        self.assertTrue(all(p['train']=='G1' and p['from_station']=='北京' and p['to_station']=='上海虹桥' for _,p in calls))

    async def test_ranges_do_not_become_multiple_od_intervals(self):
        for text in ['明天到大后天北京到上海虹桥G1票价','G1 10月6日到8日 北京到上海虹桥票价']:
            _,calls,_=await self.execute(text)
            self.assertEqual([p['date'] for _,p in calls],DAYS,text)
            self.assertTrue(all(p['from_station']=='北京' and p['to_station']=='上海虹桥' for _,p in calls),text)

    async def test_common_three_dates_two_trains_all_four_services(self):
        text='G1、G2 2026-10-03、2026-10-04、2026-10-05 北京南到上海虹桥的时刻表、余票、票价和交路'
        done,calls,_=await self.execute(text)
        expected={(tool,code,day) for tool in ['train.schedule','ticket.query','ticket.price','emu.routing']
                  for code in ['G1','G2'] for day in ['2026-10-03','2026-10-04','2026-10-05']}
        self.assertEqual({(n,p['train'],p['date']) for n,p in calls},expected)
        self.assertEqual(len(calls),24)
        batches=[i for i in done['display_results'] if i['kind']=='train_schedule_batch']
        self.assertEqual(len(batches),3)
        self.assertTrue(all(len({i['date'] for i in b['items']})==1 for b in batches))
        items=[i for b in batches for i in b['items']]
        self.assertEqual(len(items),6)
        self.assertTrue(all(i['schema_version']==1 for i in items))
        self.assertEqual({(i['train_code'],i['date']) for i in items}, {(c,d) for c in ['G1','G2'] for d in ['2026-10-03','2026-10-04','2026-10-05']})
        routings=[i for i in done['display_results'] if i['kind']=='emu_routing']
        self.assertEqual(len(routings),6)
        self.assertTrue(all(i['records'][0]['date']==i['focus_date'] for i in routings))

    async def test_partial_same_train_date_failure_preserved_in_both_modes(self):
        def boundary(name,params):
            return ToolResult(ok=False,error='该日期接口超时') if params['date']==DAYS[1] else result_for(name,params)
        outputs=[]
        for mode in ['stream','block']:
            done,calls,answer=await self.execute('G1明天后天大后天的时刻表',mode,boundary=boundary)
            items=done['display_results']
            self.assertEqual(len(items),3)
            self.assertEqual({i['date'] for i in items},set(DAYS))
            self.assertEqual([(i['train_code'],i['date']) for i in items if i['status']=='failed'],[('G1',DAYS[1])])
            self.assertIn(f'G1 · {DAYS[1]} · 时刻',answer)
            outputs.append(done['display_results'])
        self.assertEqual(*outputs)

    async def test_all_failed_dates_remain_three_failed_items(self):
        done,_,_=await self.execute('G1明天后天大后天时刻表',boundary=lambda n,p:ToolResult(ok=False,error='不可达'))
        items=done['display_results']
        self.assertEqual(len(items),3)
        self.assertTrue(all(i['status']=='failed' for i in items))
        self.assertEqual({i['date'] for i in items},set(DAYS))

    async def test_future_routing_skips_only_future_day_schedule_remains(self):
        _,calls,answer=await self.execute('G1昨天今天明天的时刻表和交路')
        self.assertEqual([p['date'] for n,p in calls if n=='emu.routing'],['2026-10-04','2026-10-05'])
        self.assertEqual(len([n for n,p in calls if n=='train.schedule']),3)
        self.assertIn('G1 · 2026-10-06 · 担当/交路',answer)
        self.assertIn('尚未发生',answer)

    async def test_ambiguities_invalid_and_over_limit_make_no_queries(self):
        texts=['G1明天、G2后天的票价','G1 2026-02-30、2026-10-06票价',
               'G1明天后天北京到上海、天津到南京票价',
               'G1 2026-10-06到2027-10-06时刻表','G1明天后天最近交路',
               '、'.join(f'G{i}' for i in range(1,12))+'明天后天时刻表']
        for text in texts:
            _,calls,answer=await self.execute(text)
            self.assertEqual(calls,[],text)
            self.assertIn('未发起',answer,text)

    async def test_actual_fare_tool_keeps_current_card_receipt_format(self):
        from app.tools.ticket_price import TicketPriceTool
        calls=[]
        async def payload(origin,destination,day,code):
            calls.append((origin,destination,day,code))
            return {'from_station':origin,'to_station':destination,'data':[
                {'train_code':code,'from_station':origin,'to_station':destination,
                 'start_time':'07:00','arrive_time':'12:00','duration':'05:00',
                 'prices':{'二等座':int(day[-2:])+35,'商务座':1000}}]}
        async def invoke(name,params):
            self.assertEqual(name,'ticket.price')
            return await TicketPriceTool().invoke(params)
        with patch.object(rt,'query_ticket_prices',payload),patch('app.tools.registry.invoke_by_name',invoke):
            slots=service_dispatch.decide(EXAMPLE)[2]
            result=await retrieve('ticket',slots,'realtime',EXAMPLE)
        self.assertEqual([c[2] for c in calls],DAYS)
        for day in DAYS:
            self.assertIn(f'北京→上海虹桥（{day}）票价：',result['direct_answer'])
            self.assertIn(f'G1 北京→上海虹桥 07:00–12:00（历时 05:00）：二等座 {int(day[-2:])+35} 元、商务座 1000 元',result['direct_answer'])
        self.assertEqual(result['direct_answer'].count('以上为票价信息，不表示该席别当前有票。'),3)

    async def test_explicit_single_date_action_ignores_multidate_text(self):
        action={'kind':'train_schedule_batch','trains':['G2'],'date':DAYS[1]}
        for mode in ['stream','block']:
            done,calls,_=await self.execute(EXAMPLE,mode,action)
            self.assertEqual(len(calls),1)
            self.assertEqual((calls[0][0],calls[0][1]['train'],calls[0][1]['date']),('train.schedule','G2',DAYS[1]))
            self.assertEqual(done['display_results'][0]['date'],DAYS[1])

    async def test_contextual_scalar_slots_cannot_collapse_explicit_dates(self):
        calls=[]
        async def invoke(name,params):
            calls.append(dict(params));return result_for(name,params)
        with patch('app.tools.registry.invoke_by_name',invoke):
            out=await retrieve('ticket',Slots(time='明天',direction='北京→上海虹桥'),'realtime',EXAMPLE)
        self.assertEqual([p['date'] for p in calls],DAYS)
        self.assertTrue(all(day in out['direct_answer'] for day in DAYS))

    async def test_mixed_comparison_cannot_silently_query_only_one_date(self):
        with patch('app.tools.registry.invoke_by_name',AsyncMock()) as invoke:
            out=await retrieve('ticket',Slots(time='明天',direction='北京南→上海虹桥'),'mixed',
                               'G1明天后天北京南到上海虹桥票价为什么不同')
            invoke.assert_not_awaited()
        self.assertIn('确认',out['direct_answer'])

    async def test_scalar_hint_does_not_start_single_date_speculation(self):
        from app.pipeline import prefetch
        for text in ['G1明天后天北京南到上海虹桥余票','G1 2026-10-06到2027-10-06 北京南到上海虹桥余票','G1 2026-02-30 北京南到上海虹桥余票']:
            with patch('app.tools.registry.invoke_by_name',AsyncMock()) as invoke:
                self.assertIsNone(prefetch.start(text,date_hint='明天'))
                await asyncio.sleep(0)
                invoke.assert_not_awaited()

    async def test_planner_callback_prefetch_cannot_bypass_date_admission(self):
        from app.pipeline.intent import Intent
        text='G1 2026-10-06到2027-10-06 北京南到上海虹桥余票'
        async def decide(*args,**kwargs):
            callback=orchestrator.planner.on_llm_decision.get()
            self.assertIsNotNone(callback)
            callback()
            await asyncio.sleep(0)
            return Intent.TICKET,'realtime',Slots(time='明天',direction='北京南→上海虹桥'),'llm-merged',''
        # Exercise the callback even if an upstream planner sends this request
        # to its model path; speculation must still reject the raw date range.
        with patch.object(service_dispatch,'decide',return_value=None), \
             patch.object(orchestrator.planner,'decide',decide), \
             patch('app.tools.registry.invoke_by_name',AsyncMock()) as invoke:
            decision,pf=await orchestrator._decide_with_prefetch(text,[{'role':'user','content':'查余票'}])
            self.assertIsNone(pf)
            invoke.assert_not_awaited()

    async def test_ten_dates_and_ten_trains_are_independent_limits(self):
        text='、'.join(f'G{i}' for i in range(1,11))+' 2026-10-06到2026-10-15的时刻表'
        done,calls,_=await self.execute(text)
        self.assertEqual(len(calls),100)
        self.assertEqual(len(done['display_results']),10)
        self.assertEqual(sum(len(b['items']) for b in done['display_results']),100)

    async def test_executor_rejects_invalid_lists_before_tools(self):
        for dates in [[],['2026-02-30'],['明天后天'],DAYS+['2026-10-32'],[f'2026-10-{i:02}' for i in range(6,17)]]:
            with patch('app.tools.registry.invoke_by_name',AsyncMock()) as invoke:
                out=await service_batch.execute(['G1'],['fare'],date=None,dates=dates,od=('北京','上海虹桥'),message='票价')
                invoke.assert_not_awaited()
                self.assertIn('未发起',out['direct_answer'])

    async def test_cross_date_tool_result_is_failure_not_relabelled(self):
        def boundary(name,params):
            result=result_for(name,params)
            result.data['train_date']='2026-10-01'
            return result
        done,_,answer=await self.execute('G1明天后天时刻表',boundary=boundary)
        self.assertTrue(all(i['status']=='failed' for i in done['display_results']))
        self.assertEqual({i['date'] for i in done['display_results']},set(DAYS[:2]))
        self.assertIn('与请求日期不一致',answer)

    async def test_batch_retry_payload_cannot_mix_dates(self):
        def boundary(name,params):
            return ToolResult(ok=False,error='接口失败')
        done,_,_=await self.execute('G1、G2明天后天时刻表',boundary=boundary)
        self.assertEqual(len(done['display_results']),2)
        for batch in done['display_results']:
            self.assertEqual(batch['kind'],'train_schedule_batch')
            self.assertEqual({i['train_code'] for i in batch['items']},{'G1','G2'})
            self.assertEqual(len({i['date'] for i in batch['items']}),1)

    async def test_multidate_concurrency_cancellation_drains_children(self):
        entered=asyncio.Event();active=peak=0
        async def invoke(name,params):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            if active==2:entered.set()
            try:await asyncio.Event().wait()
            finally:active-=1
        with patch('app.tools.registry.invoke_by_name',invoke):
            task=asyncio.create_task(service_batch.execute(['G1','G2'],['fare','ticket'],date=None,dates=DAYS,od=('北京','上海虹桥'),message='票价余票'))
            await asyncio.wait_for(entered.wait(),1)
            self.assertEqual(peak,2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(active,0)


if __name__=='__main__':
    unittest.main()
