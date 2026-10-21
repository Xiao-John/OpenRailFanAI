"""Request-local raw rows: real tools/dispatch with controlled HTTP, no outbound IO."""
import asyncio
from collections import Counter
from datetime import timedelta
import unittest
from unittest.mock import patch

import httpx

from app.dates import railway_today
from app.pipeline import service_batch
from app.tools import ticket_snapshot as snapshot, executed_fare as fare, registry, _rt12306 as rt
from app.tools.base import ToolResult
from app.tools.ticket_query import TicketQueryTool


class TicketSnapshot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await rt.ensure_loaded()
        await registry.close_queries()
        await rt.close_query_flights()
        self.day = (railway_today() + timedelta(days=1)).isoformat()
        self.calls = []
        self.low = False
        self.bad = False
        self.failed_price = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stall = False
        self.stores = []
        async def handler(request):
            phase = ('rows' if request.url.path.endswith('queryI') else
                     'price' if request.url.path.endswith('queryTicketPrice') else 'init')
            self.calls.append(phase)
            await asyncio.sleep(0)
            if phase == 'rows':
                self.started.set()
                if self.stall:
                    await self.release.wait()
                if self.bad:
                    return httpx.Response(200, json={'status': False})
                rows = []
                for i in range(1, 11):
                    parts = [''] * 36
                    for index, value in {2: 'internal-G'+str(i), 3: 'G'+str(i), 6: 'VNP', 7: 'AOH',
                                         8: '08:00', 9: '13:00', 10: '05:00', 16: '01', 17: '07',
                                         30: ('1' if self.low and self.calls.count('rows') == 1 else '有'),
                                         35: 'O'}.items():
                        parts[index] = value
                    rows.append('|'.join(parts))
                return httpx.Response(200, json={'status': True, 'data': {'result': rows}})
            if phase == 'price':
                internal = request.url.params['train_no']
                if internal == self.failed_price:
                    return httpx.Response(503)
                return httpx.Response(200, json={'status': True, 'data': {'train_no': internal, 'O': '100'}})
            return httpx.Response(200, text='init')
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(self.client.aclose)
        for p in [patch.dict('os.environ', {'APP_VARIANT': 'main'}),
                  patch.object(snapshot, 'get_client', return_value=self.client),
                  patch.object(fare, 'get_client', return_value=self.client)]:
            p.start()
            self.addCleanup(p.stop)
        setting = patch.object(snapshot.get_settings(), 'main_ticket_shared_rows_enabled', True)
        setting.start()
        self.addCleanup(setting.stop)
        self.addAsyncCleanup(registry.close_queries)
        self.addAsyncCleanup(rt.close_query_flights)

    async def test_ten_trains_share_rows_across_concurrency_waves(self):
        result = await service_batch.execute(['G'+str(i) for i in range(1,11)], ['fare'],
                                             date=self.day, od=('北京南','上海虹桥'), message='实际票价')
        self.assertEqual(len(result['data']), 20)
        self.assertEqual(Counter(self.calls), {'init': 1, 'rows': 1, 'price': 10})
        for item in result['data']:
            if item['tool']=='ticket.query':
                rows=item['data']['trains']
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['train_no'],item['query_context']['train'])
                self.assertEqual((rows[0]['from_station'],rows[0]['to_station']),('北京南','上海虹桥'))
                self.assertEqual(rows[0]['seats']['second_class'],'有')
        self.assertFalse(snapshot.active())

    async def test_multiple_dates_are_separate_and_keep_query_binding(self):
        days = [self.day, (railway_today()+timedelta(days=2)).isoformat()]
        result = await service_batch.execute(['G1','G2'], ['fare'],date=self.day,dates=days,
                                             od=('北京南','上海虹桥'),message='实际票价')
        self.assertEqual(Counter(self.calls), {'init':2,'rows':2,'price':4})
        self.assertEqual(Counter(item['query_date'] for item in result['data']), {day:4 for day in days})

    async def test_low_seat_verification_is_a_fresh_network_observation(self):
        self.low = True
        @snapshot.request_scope
        async def run():
            return await TicketQueryTool().invoke(dict(from_station='北京南',to_station='上海虹桥',date=self.day,train='G1'))
        result = await run()
        self.assertTrue(result.ok, result.error)
        self.assertEqual(Counter(self.calls), {'init':2,'rows':2})
        self.assertIn('发生变化',result.note)
        self.assertTrue(result.fetched_at)

    async def test_price_failure_does_not_discard_peer_availability(self):
        self.failed_price = 'internal-G2'
        result = await service_batch.execute(['G1','G2'],['fare'],date=self.day,
                                             od=('北京南','上海虹桥'),message='实际票价')
        self.assertEqual(len(result['data']),3)
        self.assertEqual(len(result['display_errors']),1)
        self.assertEqual(result['display_errors'][0]['train_code'],'G2')
        self.assertEqual(result['display_errors'][0]['tool'],'ticket.price')
        self.assertEqual(Counter(self.calls),{'init':1,'rows':1,'price':2})

    async def test_failed_rows_are_not_retained_and_values_are_isolated(self):
        @snapshot.request_scope
        async def run():
            self.bad = True
            with self.assertRaises(rt.Realtime12306Error):
                await snapshot.query_rows('VNP','AOH',self.day)
            self.bad = False
            first = await snapshot.query_rows('VNP','AOH',self.day)
            first['rows'].clear()
            second = await snapshot.query_rows('VNP','AOH',self.day)
            self.assertEqual(len(second['rows']),10)
        await run()
        self.assertEqual(self.calls.count('rows'),2)

    async def test_interval_purpose_config_and_request_isolation(self):
        @snapshot.request_scope
        async def run():
            for a,b,purpose in [('VNP','AOH','ADULT'),('VNP','AOH','ADULT'),
                                ('VNP','AOH','0X'),('AOH','VNP','ADULT')]:
                await snapshot.query_rows(a,b,self.day,purpose=purpose)
            with patch.object(snapshot,'query_scope',lambda:'another-config'):
                await snapshot.query_rows('VNP','AOH',self.day)
        await run()
        self.assertEqual(self.calls.count('rows'),4)
        await run()
        self.assertEqual(self.calls.count('rows'),8)

    async def test_cancel_one_waiter_preserves_other_and_scope_cleanup(self):
        self.stall = True
        @snapshot.request_scope
        async def run():
            store = snapshot.scope_key()
            first=asyncio.create_task(snapshot.query_rows('VNP','AOH',self.day))
            await self.started.wait()
            second=asyncio.create_task(snapshot.query_rows('VNP','AOH',self.day))
            await asyncio.sleep(.01)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            self.release.set()
            self.assertEqual(len((await second)['rows']),10)
            return store
        store=await run()
        self.assertEqual(self.calls.count('rows'),1)
        self.assertFalse(store._values)
        self.assertFalse(store._flights)

    async def test_cross_request_tool_waiter_survives_first_request_cancel(self):
        self.stall = True
        class FakeTool:
            enabled=True
            async def invoke(tool,params):
                store=snapshot.scope_key()
                self.stores.append(store)
                rows=await snapshot.query_rows('VNP','AOH',self.day)
                return ToolResult(ok=True,data=rows)
        @snapshot.request_scope
        async def run():return await registry.invoke_by_name('fixture.shared',{})
        with patch.object(registry,'get',lambda name:FakeTool()):
            first=asyncio.create_task(run())
            await self.started.wait()
            second=asyncio.create_task(run())
            await asyncio.sleep(.01)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            self.release.set()
            self.assertTrue((await second).ok)
        self.assertEqual(self.calls.count('rows'),1)
        self.assertFalse(self.stores[0]._values)
        self.assertFalse(self.stores[0]._flights)

    async def test_cancel_entire_request_releases_raw_work(self):
        self.stall = True
        stores=[]
        @snapshot.request_scope
        async def run():
            stores.append(snapshot.scope_key())
            await snapshot.query_rows('VNP','AOH',self.day)
        task=asyncio.create_task(run())
        await self.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(stores[0]._flights)
        self.assertFalse(stores[0]._values)

    async def test_network_retries_keep_initialization_and_business_failure_does_not_retry(self):
        real_get=self.client.get
        failures=0
        async def get(url,**kwargs):
            nonlocal failures
            if url==snapshot.ROWS and failures<2:
                failures+=1
                raise httpx.ConnectError('controlled transport failure')
            return await real_get(url,**kwargs)
        real_sleep=asyncio.sleep
        async def sleep(delay):await real_sleep(0 if delay==1 else delay)
        @snapshot.request_scope
        async def run():return await snapshot.query_rows('VNP','AOH',self.day)
        with patch.object(self.client,'get',get),patch.object(snapshot.asyncio,'sleep',sleep):
            self.assertEqual(len((await run())['rows']),10)
        self.assertEqual(self.calls.count('init'),3)
        self.bad=True
        with self.assertRaises(rt.Realtime12306Error):await run()
        self.assertEqual(self.calls.count('init'),4)

    async def test_rollback_and_lm_use_original_path(self):
        @snapshot.request_scope
        async def run():
            self.assertFalse(snapshot.active())
            return await fare.query('北京南','上海虹桥',self.day,'G1')
        with patch.object(snapshot.get_settings(), 'main_ticket_shared_rows_enabled', False):
            self.assertEqual(len((await run())['data']),1)
        with patch.dict('os.environ',APP_VARIANT='lm'):
            self.assertEqual(len((await run())['data']),1)
        self.assertEqual(Counter(self.calls),{'init':2,'rows':2,'price':2})

    async def test_nearby_station_rows_are_not_relabelled_as_requested_interval(self):
        get=self.client.get
        async def neighbor(url,**kwargs):
            response=await get(url,**kwargs)
            if url!=snapshot.ROWS:return response
            payload=response.json()
            parts=payload['data']['result'][0].split('|')
            parts[7]='SHH'  # Shanghai is not Shanghai Hongqiao.
            payload['data']['result']=['|'.join(parts)]
            return httpx.Response(200,json=payload,request=response.request)
        with patch.object(self.client,'get',neighbor):
            result=await service_batch.execute(['G1'],['fare'],date=self.day,
                                               od=('北京南','上海虹桥'),message='实际票价')
        self.assertEqual(self.calls.count('price'),0)
        self.assertTrue(any(e['tool']=='ticket.query' for e in result['display_errors']))
        for item in result['data']:
            self.assertEqual(item['tool'],'ticket.price')
            self.assertEqual(item['data']['data'],[])


if __name__=='__main__':unittest.main()
