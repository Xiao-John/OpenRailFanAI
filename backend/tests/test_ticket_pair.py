"""Bounded co-query planning and independent receipt execution."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from app.pipeline import ticket_pair, service_batch
from app.tools.base import ToolResult

DAY='2026-10-07'
PARAMS=dict(train='G1',date=DAY,from_station='北京南',to_station='上海虹桥')
class PairPlanning(unittest.IsolatedAsyncioTestCase):
    def test_explicit_both_deduplicated_and_filters_preserved(self):
        params=dict(PARAMS,seat='second_class',after_time='10:00')
        out=ticket_pair.expand([('ticket.query',params),('ticket.price',dict(PARAMS))],'余票和票价')
        self.assertEqual(len(out),2)
        self.assertEqual(out[0][1],params)
        single=ticket_pair.expand([('ticket.query',params)],'二等座还有票吗')
        self.assertEqual(single[1][0],'ticket.price');self.assertEqual(single[1][1]['fare_basis'],'executed')
        self.assertNotIn('seat',single[1][1])
        self.assertEqual(params['after_time'],'10:00')

    async def test_route_collection_actual_identity_bound_and_cancellation(self):
        params=dict(PARAMS,train=None)
        rows=[dict(train_no='G1',from_station='北京南',to_station='上海虹桥'),
              dict(train_no='G2',from_station='北京南',to_station='南京南')]
        plan=[('ticket.query',params)];results=[ToolResult(ok=True,data=dict(train_date=DAY,trains=rows,from_station='北京南',to_station='上海虹桥'))]
        calls=[]
        async def invoke(n,p):
            calls.append((n,p));return ToolResult(ok=False,error='price failed')
        await ticket_pair.complete_collection(plan,results,invoke,'余票')
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][1]['to_station'],'上海虹桥')
        self.assertEqual(len(plan),len(results));self.assertFalse(results[-1].ok)
        for invalid in [dict(train_date='2026-10-08',trains=rows),dict(train_date=DAY,trains=rows*6)]:
            calls.clear();await ticket_pair.complete_collection([('ticket.query',params)],[ToolResult(ok=True,data=invalid)],invoke,'余票')
            self.assertEqual(calls,[])
        live=set(); started=asyncio.Event()
        async def blocking(n,p):
            t=asyncio.current_task();live.add(t);started.set()
            try: await asyncio.Event().wait()
            finally: live.remove(t)
        task=asyncio.create_task(ticket_pair.complete_collection([('ticket.query',params)],[ToolResult(ok=True,data=dict(train_date=DAY,trains=rows,from_station='北京南',to_station='上海虹桥'))],blocking,'余票'))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(live,set())

    async def test_pair_concurrency_dedup_and_failure_isolation(self):
        active=peak=0;calls=[]
        async def invoke(name,params):
            nonlocal active,peak
            active+=1;peak=max(peak,active);calls.append((name,dict(params)))
            try:
                await asyncio.sleep(.001)
                return ToolResult(ok=name=='ticket.query',data=dict(train_date=params['date'],trains=[]),error='price timeout' if name=='ticket.price' else '')
            finally:active-=1
        with patch.dict(os.environ,APP_VARIANT='main'),patch.object(service_batch,'get_settings',return_value=SimpleNamespace(tool_concurrency=3)),patch.object(service_batch.registry,'invoke_by_name',invoke):
            out=await service_batch.execute(['G1','G2'],['fare','ticket'],date=DAY,od=('北京南','上海虹桥'),message='票价余票',dates=[DAY,'2026-10-08'])
        self.assertEqual(len(calls),8);self.assertEqual(peak,3)
        self.assertEqual(len(out['data']),4);self.assertEqual(len(out['display_errors']),4)
        self.assertTrue(all(i['query_context']['date']==i['data']['train_date'] for i in out['data']))
        self.assertTrue(all(p['from_station']=='北京南' and p['to_station']=='上海虹桥' for _,p in calls))

    async def test_real_ticket_tool_rejects_nearby_station_and_marks_filtered_empty(self):
        from app.tools.ticket_query import TicketQueryTool
        from app.tools import _rt12306 as rt
        async def resolve(name):return ('VNP','北京南') if name=='北京南' else ('AOH','上海虹桥')
        correct=dict(train_no='G1',from_station='北京南',to_station='上海虹桥',seats={'second_class':'无'})
        neighbor=dict(correct,to_station='南京南',seats={'second_class':'有'})
        with patch.dict(os.environ,APP_VARIANT='main'),patch.object(rt,'resolve_station_code',resolve),patch.object(rt,'query_tickets',AsyncMock(return_value=[neighbor,correct])):
            result=await TicketQueryTool().invoke(dict(PARAMS,seat='second_class'))
        self.assertFalse(result.ok);self.assertEqual(result.data['availability_status'],'empty')
        self.assertEqual(result.data['to_station'],'上海虹桥');self.assertTrue(result.fetched_at)
        self.assertEqual(result.total,1);self.assertIn('没有符合筛选条件',result.error)
        with patch.dict(os.environ,APP_VARIANT='main'),patch.object(rt,'resolve_station_code',resolve),patch.object(rt,'query_tickets',AsyncMock(return_value=[neighbor])):
            result=await TicketQueryTool().invoke(PARAMS)
        self.assertFalse(result.ok);self.assertEqual(result.total,0)
        self.assertEqual(result.data['availability_status'],'empty')

    async def test_fare_auto_pair_does_not_hide_unavailable_requested_seat(self):
        calls=[]
        async def invoke(name,params):
            calls.append((name,params));return ToolResult(ok=False,error='fixed boundary')
        with patch.dict(os.environ,APP_VARIANT='main'),patch.object(service_batch,'get_settings',return_value=SimpleNamespace(tool_concurrency=2)),patch.object(service_batch.registry,'invoke_by_name',invoke):
            await service_batch.execute(['G1'],['fare'],date=DAY,od=('北京南','上海虹桥'),message='二等座票价')
        self.assertEqual([n for n,_ in calls],['ticket.price','ticket.query'])
        self.assertNotIn('seat',calls[1][1]);self.assertNotIn('after_time',calls[1][1])

if __name__=='__main__':unittest.main()
