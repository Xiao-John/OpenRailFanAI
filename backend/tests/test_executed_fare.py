"""Execution price uses sale-page parameters; public references stay local."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

import httpx
from app.tools import executed_fare as F,_rt12306 as rt
from app.tools.ticket_price import TicketPriceTool
from app.data import published_fares as P
from app.pipeline import service_batch
from app.updates.dictionary_schema import SCHEMA

DAY='2026-10-07'


def raw(code='G1',origin='VNP',destination='AOH'):
    row=['']*36
    for i,value in {2:'24000000G10L',3:code,6:origin,7:destination,8:'06:30',9:'11:24',10:'04:54',16:'01',17:'07',35:'9MOO'}.items():row[i]=value
    return '|'.join(row)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        p=patch.dict(os.environ,APP_VARIANT='main');p.start();self.addCleanup(p.stop)
        self.responses={'rows':{'status':True,'data':{'result':[raw()]}},
                        'price':{'status':True,'data':{'train_no':'24000000G10L','O':'¥661.0','M':'¥1058.0','A9':'¥2315.0','9':'23150'}}}
        self.calls=[]
        async def handler(request):
            self.calls.append(request)
            if request.url.path.endswith('queryI'):return httpx.Response(200,json=self.responses['rows'])
            if request.url.path.endswith('queryTicketPrice'):return httpx.Response(200,json=self.responses['price'])
            if 'queryAllPublicPrice' in str(request.url):raise AssertionError('public API must not run')
            return httpx.Response(200,text='init')
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(handler));self.addAsyncCleanup(self.client.aclose)
        async def station(name):return ('VNP','北京南') if name=='北京南' else ('AOH','上海虹桥')
        for p in [patch.object(F,'get_client',return_value=self.client),patch.object(rt,'resolve_station_code',side_effect=station),patch.object(rt,'ensure_loaded',AsyncMock())]:
            p.start();self.addCleanup(p.stop)

    async def test_exact_sale_row_parameters_and_unit_are_preserved(self):
        result=await TicketPriceTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':DAY,'train':'G1'})
        self.assertTrue(result.ok,result.error);self.assertEqual(result.data['fare_basis'],'executed')
        self.assertEqual(result.data['data'][0]['prices'],{'商务座':'2315.0','一等座':'1058.0','二等座':'661.0'})
        request=next(r for r in self.calls if r.url.path.endswith('queryTicketPrice'))
        self.assertEqual(dict(request.url.params),{'train_no':'24000000G10L','from_station_no':'01','to_station_no':'07','seat_types':'9MOO','train_date':DAY})
        self.assertEqual(result.sources,[F.PRICE_URL]);self.assertIn('实际执行票价',result.text)
        self.assertNotIn('795',result.text)

    async def test_boarding_day_is_not_replaced_by_service_start_day(self):
        parts=raw().split('|');parts[13]='20261005'
        self.responses['rows']['data']['result']=['|'.join(parts)]
        await F.query('北京南','上海虹桥',DAY,'G1')
        request=next(r for r in self.calls if r.url.path.endswith('queryTicketPrice'))
        self.assertEqual(request.url.params['train_date'],DAY)

    async def test_neighbor_and_other_train_rows_are_not_queried(self):
        self.responses['rows']['data']['result']=[raw(destination='SHH'),raw(code='G2'),raw()]
        data=await F.query('北京南','上海虹桥',DAY,'G1')
        self.assertEqual(len(data['data']),1)
        self.assertEqual(len([r for r in self.calls if r.url.path.endswith('queryTicketPrice')]),1)

    async def test_missing_train_and_malformed_rows_do_not_fan_out(self):
        with self.assertRaises(rt.Realtime12306Error):await F.query('北京南','上海虹桥',DAY,'')
        self.assertEqual(self.calls,[])
        self.responses['rows']['data']['result']=['broken']
        with self.assertRaises(rt.Realtime12306Error):await F.query('北京南','上海虹桥',DAY,'G1')
        self.assertFalse(any(r.url.path.endswith('queryTicketPrice') for r in self.calls))

    async def test_upstream_failure_missing_identity_and_invalid_price_never_fallback(self):
        for payload in [{'status':False}, {'status':True,'data':{'train_no':'wrong','O':'¥661'}},
                        {'status':True,'data':{'train_no':'24000000G10L','O':'--'}}]:
            self.responses['price']=payload
            result=await TicketPriceTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':DAY,'train':'G1'})
            self.assertFalse(result.ok);self.assertIn('未使用公布票价替代',result.note)
        self.assertFalse(any('queryAllPublicPrice' in str(r.url) for r in self.calls))

    async def test_empty_success_and_network_failure_are_distinct(self):
        self.responses['rows']['data']['result']=[]
        self.assertEqual((await F.query('北京南','上海虹桥',DAY,'G1'))['data'],[])
        self.responses['rows']={'status':False,'data':{}}
        with self.assertRaises(rt.Realtime12306Error):await F.query('北京南','上海虹桥',DAY,'G1')

    async def test_invalid_amount_remains_missing_not_zero(self):
        self.responses['price']['data']['M']='--'
        result=await TicketPriceTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':DAY,'train':'G1'})
        self.assertTrue(result.ok);self.assertIsNone(result.data['data'][0]['prices']['一等座'])
        self.assertIn('一等座 未返回有效金额',result.text);self.assertNotIn('None 元',result.text)

    async def test_published_reference_is_local_only_and_date_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'dict.db'
            with sqlite3.connect(db) as c:
                c.executescript(P.SCHEMA)
                c.execute('INSERT INTO published_fare VALUES(?,?,?,?,?,?,?,?,?,?)',('G1','北京南','上海虹桥','2026-10-01','2026-10-31','二等座','795','CNY','official-reference','2026-10-01T00:00:00Z'))
            with patch('app.data.dict.db_path',return_value=db):
                result=await TicketPriceTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':DAY,'train':'G1','fare_basis':'published'})
                self.assertTrue(result.ok);self.assertEqual(result.data['fare_basis'],'published')
                self.assertEqual(result.sources,['official-reference']);self.assertEqual(self.calls,[])
                missing=await TicketPriceTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':'2026-11-01','train':'G1','fare_basis':'published'})
                self.assertFalse(missing.ok);self.assertIn('未发起网络查询',missing.error)
        self.assertEqual(self.calls,[])

    async def test_software_bundle_can_carry_reference_fares(self):
        from app.updates.dictionary import sync_bundled
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'bundle.db';target=Path(folder)/'dict.db'
            for path in [source,target]:
                with sqlite3.connect(path) as c:
                    c.executescript(SCHEMA)
                    c.execute("INSERT INTO meta VALUES('gtfs_tag','gtfs-20261004-052300')")
                    c.execute("INSERT INTO g_stop VALUES('A','北京南',39,116)")
                    c.execute("INSERT INTO g_trip VALUES('G1','R','G1',0)")
                    c.execute("INSERT INTO g_stop_time VALUES('G1',1,'A','06:30','06:30',0)")
            with sqlite3.connect(source) as c:
                c.execute("INSERT INTO meta VALUES('published_fares_pulled_at','2026-10-01T00:00:00Z')")
                c.execute('INSERT INTO published_fare VALUES(?,?,?,?,?,?,?,?,?,?)',('G1','北京南','上海虹桥','2026-10-01','2026-10-31','二等座','795','CNY','official-reference','2026-10-01T00:00:00Z'))
            result=sync_bundled(source,target)
            self.assertEqual(result['updated_tables'],['published_fare'])
            self.assertEqual(P.lookup(target,'G1','北京南','上海虹桥',DAY)['data'][0]['prices'],{'二等座':'795'})

    async def test_lm_retains_legacy_public_tool(self):
        payload={'success':True,'data':[]}
        with patch.dict(os.environ,APP_VARIANT='lm'),patch.object(rt,'query_ticket_price_validated',AsyncMock(return_value=payload)),patch.object(rt,'parse_mcp_result',side_effect=lambda x:x):
            result=await TicketPriceTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':DAY,'train':'G1'})
            self.assertTrue(result.ok);self.assertNotIn('fare_basis',result.data)
            self.assertIn('queryAllPublicPrice',result.sources[0])
        self.assertEqual(self.calls,[])

    async def test_negated_public_request_and_mixed_basis_are_not_misrouted(self):
        from app.pipeline.fare_basis import select
        self.assertEqual(select('不要公布票价，查实际执行票价'),'executed')
        self.assertEqual(select('不要实际执行票价，只查公布票价'),'published')
        self.assertIsNone(select('查询执行票价和公布票价'))
        with patch('app.tools.registry.invoke_by_name',AsyncMock()) as invoked:
            result=await service_batch.execute(['G1'],['fare'],date=DAY,od=('北京南','上海虹桥'),message='查执行票价和公布票价')
            invoked.assert_not_awaited();self.assertIn('分别查询',result['direct_answer'])

    async def test_reference_seats_have_independent_valid_periods(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fare.db'
            with sqlite3.connect(path) as c:
                c.executescript(P.SCHEMA)
                for seat,start,price in [('二等座','2026-10-01','795'),('一等座','2026-10-05','1200')]:
                    c.execute('INSERT INTO published_fare VALUES(?,?,?,?,?,?,?,?,?,?)',('G1','北京南','上海虹桥',start,'2026-10-31',seat,price,'CNY','official','2026-10-01T00:00:00Z'))
            self.assertEqual(P.lookup(path,'G1','北京南','上海虹桥',DAY)['data'][0]['prices'],{'一等座':'1200','二等座':'795'})

    async def test_explicit_public_batch_dispatches_local_basis(self):
        invoked=[]
        async def invoke(name,params):
            invoked.append((name,params))
            from app.tools.base import ToolResult
            return ToolResult(ok=False,error='本地无记录')
        with patch('app.tools.registry.invoke_by_name',invoke):
            result=await service_batch.execute(['G1','G2'],['fare'],date=DAY,od=('北京南','上海虹桥'),message='G1 G2公布票价')
        self.assertEqual([(n,p['train']) for n,p in invoked],[(n,c) for c in ['G1','G2'] for n in ['ticket.price','ticket.query']])
        self.assertTrue(all(p['fare_basis']=='published' for n,p in invoked if n=='ticket.price'))
        self.assertTrue(all('fare_basis' not in p for n,p in invoked if n=='ticket.query'))
        self.assertEqual(len(result['display_errors']),4)


if __name__=='__main__':unittest.main()
