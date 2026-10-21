"""独立评审修复回归；所有外部响应均受控，不访问公网。"""
import asyncio
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.dates import railway_today
from app.pipeline import fastpath, retrieve
from app.tools import _rt12306 as rt, ticket_snapshot as snap, web_search
from app.tools.ticket_query import TicketQueryTool
from app.tools.rail_mileage import RailMileageTool
from app.data import dict as dictionary
from app.updates import software, github
from app.query_cache import query_scope, scope_context
from app.llm.client import set_active_provider


class ReviewFixes(unittest.IsolatedAsyncioTestCase):
    def test_unknown_prefix_is_not_another_train(self):
        for text in ['X1次列车', 'V6001次', 'DH1次', 'AB123次', 'CR400AF-5054次', 'AG1次', '10001次']:
            with self.subTest(text=text):
                self.assertIsNone(rt.extract_train_code(text))
                self.assertFalse(retrieve._train_code_from_message(text + '今天几点'))
        for text, code in [('G1次列车', 'G1'), ('1461次列车', '1461'), ('T109', 'T109'),
                           ('please check G1次列车', 'G1'), ('AB123次不支持，查G1次', 'G1')]:
            self.assertEqual(rt.extract_train_code(text), code)

    def test_station_order_follows_text_not_set(self):
        with patch.object(fastpath, '_station_names', return_value={'北京', '北京南', '上海南', '上海虹桥'}):
            self.assertEqual(fastpath._station_hits('北京南和上海南哪个近'), ['北京南', '上海南'])
            self.assertEqual(fastpath._station_hits('上海南到北京南'), ['上海南', '北京南'])
            self.assertEqual(fastpath._station_hits('北京南到上海虹桥'), ['北京南', '上海虹桥'])

    async def ticket(self, rows=None, error=None):
        await rt.ensure_loaded()
        day = (railway_today() + timedelta(days=1)).isoformat()
        response = AsyncMock(return_value={'rows': rows or [], 'fetched_at': '2026-10-19T12:00:00Z'}, side_effect=error)
        @snap.request_scope
        async def run():
            return await TicketQueryTool().invoke({'from_station':'北京南', 'to_station':'上海虹桥', 'date':day})
        with patch.object(snap, 'query_rows', response), patch.object(get_settings(), 'main_ticket_shared_rows_enabled', True):
            return await run()

    async def test_empty_rows_keep_empty_semantics_and_timestamp(self):
        result = await self.ticket()
        self.assertFalse(result.ok)
        self.assertEqual(result.data['availability_status'], 'empty')
        self.assertEqual(result.fetched_at, '2026-10-19T12:00:00Z')
        self.assertNotIn('查询失败', result.error)

    async def test_batch_empty_keeps_receipt_and_does_not_claim_transport_failure(self):
        from app.pipeline import service_batch
        from app.tools import registry
        await rt.ensure_loaded()
        await registry.close_queries()
        day=(railway_today()+timedelta(days=1)).isoformat()
        with patch.object(snap, 'query_rows', AsyncMock(return_value={'rows':[],'fetched_at':'fixture-time'})), patch.object(get_settings(), 'main_ticket_shared_rows_enabled', True):
            result=await service_batch.execute(['G1','G2'], ['ticket'],date=day,
                                               od=('北京南','上海虹桥'),message='查询实际票价与余票')
        self.assertEqual(len(result['display_errors']),2)
        for receipt in result['display_errors']:
            self.assertEqual(receipt['data']['availability_status'],'empty')
            self.assertEqual(receipt['fetched_at'],'fixture-time')
        self.assertNotIn('查询失败',result['direct_answer'])
        self.assertNotIn('失败原因',result['note'])
        await registry.close_queries()

    async def test_transport_failure_does_not_become_empty(self):
        result = await self.ticket(error=rt.Realtime12306Error('网络不可用'))
        self.assertFalse(result.ok)
        self.assertIn('查询失败', result.error)
        self.assertFalse((result.data or {}).get('availability_status') == 'empty')

    async def test_malformed_rows_are_not_normal_empty(self):
        result = await self.ticket(rows=['broken'])
        self.assertFalse(result.ok)
        self.assertIn('查询失败', result.error)

    async def test_dictionary_transport_has_typed_failure(self):
        client = SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError('offline')))
        with patch('app.tools._http.get_client', AsyncMock(return_value=client)), patch.object(dictionary, '_last_call', 0):
            with self.assertRaises(dictionary.DictionaryFetchError):
                await dictionary._polite_get({'statinfo':'不存在站'})
        with patch.object(dictionary, 'available', return_value=True), patch.object(dictionary, 'station_profile', AsyncMock(side_effect=dictionary.DictionaryFetchError())):
            result = await RailMileageTool().invoke({'station':'不存在站'})
            self.assertFalse(result.ok)
            self.assertIn('暂时无法访问', result.error)
            self.assertTrue(result.fetched_at)

    async def fetch(self, budget=4000, failed=()):
        calls=[]
        async def page(url, **kwargs):
            calls.append((url, kwargs['max_chars']))
            return {'ok':url not in failed, 'url':url, 'text':'正文', 'truncated':False, 'error':'受控失败'}
        settings=SimpleNamespace(web_search_fetch_top_n=2, web_search_fetch_chars=1800, fact_text_max_chars=budget)
        urls=[{'url':f'https://example.org/{i}'} for i in range(4)]
        with patch.object(web_search, 'get_settings', return_value=settings), patch.object(web_search, 'fetch_page', page):
            pages, note=await web_search.WebSearchTool2()._fetch_top_pages(urls, used_chars=0)
        return calls, pages

    async def test_zero_budget_never_fetches(self):
        calls,pages=await self.fetch(0)
        self.assertEqual((calls,pages),([],[]))

    async def test_fetch_only_needed_pages(self):
        calls,pages=await self.fetch()
        self.assertEqual(len(calls),2)
        self.assertEqual(len(pages),2)
        calls,pages=await self.fetch(failed={'https://example.org/0'})
        self.assertEqual(len(calls),3)
        self.assertEqual([p['url'] for p in pages],['https://example.org/1','https://example.org/2'])
        calls,pages=await self.fetch(700)
        self.assertLessEqual(sum(size for _,size in calls),700)

    async def test_latest_installable_release_and_integrity_failure(self):
        from test_github_updates import release
        older=release(github.APP_REPO, 'v0.1.29', b'apk', 'OpenRailFanAI-0.1.29-arm64-release.apk')
        newer=dict(older, tag_name='v0.1.30', assets=[])
        with patch.object(github, 'metadata', AsyncMock(return_value=[newer,older])):
            result=await software.check('0.1.28')
            self.assertEqual(result['latest_version'],'0.1.29')
            self.assertTrue(result['update_available'])
            missing=await software.check('0.1.28','x86_64')
            self.assertFalse(missing['update_available'])
            self.assertFalse(missing['download_supported'])
        damaged=dict(older, assets=[dict(older['assets'][0], digest='invalid')])
        with patch.object(github,'metadata', AsyncMock(return_value=[damaged])):
            with self.assertRaises(github.UpdateError):
                await software.check('0.1.28')

    def test_settings_deployment_boundary(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, host='0.0.0.0', allow_remote_access=False)
        self.assertTrue(Settings(_env_file=None,host='0.0.0.0',allow_remote_access=True).allow_remote_access)
        self.assertEqual(Settings(_env_file=None,host='::1').host,'::1')
        self.assertFalse(Settings(_env_file=None,main_ticket_shared_rows_enabled=False).main_ticket_shared_rows_enabled)

    def test_metrics_switch_cors_and_openapi_version(self):
        from app import main
        with TestClient(main.app) as client, patch.object(main.settings,'metrics_enabled',False):
            self.assertEqual(client.get('/api/metrics').status_code,404)
            self.assertNotIn('access-control-allow-origin',client.get('/health',headers={'Origin':'https://untrusted.example'}).headers)
        self.assertEqual(main.app.version,(Path(__file__).resolve().parents[2]/'VERSION').read_text().strip())

    def test_runner_modes_skip_missing_node_and_keep_failures(self):
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            fake=Path(temp)/'python'
            calls=Path(temp)/'calls'
            fake.write_text('#!' + sys.executable + '\n' + """
import os, sys
if sys.argv[1] == '-c':
    print('fixture-python')
    raise SystemExit(0)
with open(os.environ['FIXTURE_CALLS'], 'a') as out:
    out.write(sys.argv[1] + chr(10))
raise SystemExit(1 if sys.argv[1].endswith('test_dates.py') else 0)
""")
            fake.chmod(0o755)
            env={**os.environ,'PYTHON':str(fake),'NODE':str(Path(temp)/'missing-node'),'FIXTURE_CALLS':str(calls)}
            command=['bash',str(root/'backend/tests/run_all.sh')]
            result=subprocess.run(command+['--offline'],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('环境跳过：2',result.stdout)
            self.assertIn('失败：test_dates',result.stdout)
            ran=calls.read_text().splitlines()
            self.assertIn('tests/test_multidate_services.py',ran)
            self.assertNotIn('tests/test_api.py',ran)
            self.assertNotIn('tests/test_frontend_store.py',ran)
            calls.write_text('')
            live=subprocess.run(command+['--live'],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(live.returncode,0,live.stdout+live.stderr)
            self.assertIn('tests/test_api.py',calls.read_text())
            self.assertNotIn('tests/test_dates.py',calls.read_text())

    def test_query_config_serialized_once_per_request_provider_isolated(self):
        settings=get_settings()
        original=settings.dict
        spy=Mock(side_effect=original)
        # Settings forbids replacing unknown attrs, so instrument the class method.
        with patch.object(Settings,'dict',lambda self,*args,**kw: spy(*args,**kw)):
            with scope_context():
                a=query_scope()
                self.assertEqual(a,query_scope())
                set_active_provider({'api_key':'fixture-key'})
                self.assertNotEqual(a,query_scope())
                self.assertEqual(spy.call_count,1)
            query_scope()
            self.assertEqual(spy.call_count,2)
        set_active_provider(None)


if __name__ == '__main__':
    unittest.main()
