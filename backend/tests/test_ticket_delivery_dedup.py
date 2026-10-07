"""Capability-gated, record-proven ticket presentation before public SSE text."""
import copy
from datetime import date
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from app.api import chat
from app.display_result import serialize_display_results
from app.models import ChatRequest, PipelineResult
from app.pipeline import orchestrator, service_batch, ticket_delivery
from app.tools import _rt12306 as rt
from app.tools.base import ToolResult

CAP = ticket_delivery.CAPABILITY
DAY = '2026-10-07'
CTX = dict(train='G1', date=DAY, from_station='北京南', to_station='上海虹桥')
MESSAGE = '2026-10-07北京南到上海虹桥G1票价'


def receipt(name, params, *, fail=None, filtered=False):
    if fail == name:
        return ToolResult(ok=False, error='独立组件超时', sources=[name+'-source'], fetched_at=name+'-time', note='真实失败说明')
    scope = dict(from_station=params['from_station'], to_station=params['to_station'])
    if name == 'ticket.price':
        data = dict(query_date=params['date'], fare_basis='executed', **scope,
                    data=[dict(train_code=params['train'], prices={'二等座':661}, start_time='09:00', **scope)])
        return ToolResult(ok=True, data=data, text='二等座 661 元', sources=['fare-source'], fetched_at='fare-time', note='票价原始说明')
    data = dict(train_date=params['date'], **scope,
                trains=[dict(train_no=params['train'], seats={'second_class':'有'}, start_time='09:00', **scope)])
    return ToolResult(ok=True, data=data, sources=['ticket-source'], fetched_at='ticket-time', note='余票原始说明',
                      total=3 if filtered else 1, shown=1, truncated=filtered,
                      filters={'seat':'二等座'} if filtered else {})


def retrieval_fixture():
    pieces = [ticket_delivery.fragment(name, CTX, receipt(name,CTX), name+'重复正文')
              for name in ['ticket.price','ticket.query']]
    retrieval = dict(data=[p['record'] for p in pieces], display_errors=[], direct_fragments=pieces,
                     direct_answer='\n\n'.join(p['text'] for p in pieces))
    return retrieval, serialize_display_results(retrieval['data'],[])


class RecordProof(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,{'APP_VARIANT':'main'});p.start();self.addCleanup(p.stop)

    def test_exact_record_coverage_removes_text_without_mutating_receipts(self):
        retrieval,cards=retrieval_fixture();original=copy.deepcopy(retrieval)
        result=ticket_delivery.prepare(retrieval,[CAP],cards)
        self.assertEqual(result['direct_answer'],'')
        self.assertTrue(result['direct_complete'])
        self.assertEqual(retrieval,original)
        self.assertEqual(result['data'],original['data'])

    def test_unadvertised_unknown_and_lm_keep_legacy_text(self):
        retrieval,cards=retrieval_fixture()
        for caps in [None,[],['future_card'],CAP]:
            self.assertIs(ticket_delivery.prepare(retrieval,caps,cards),retrieval)
        with patch.dict(os.environ,{'APP_VARIANT':'lm'}):
            self.assertIs(ticket_delivery.prepare(retrieval,[CAP],cards),retrieval)

    def test_missing_card_or_wrong_date_interval_provenance_retains_uncovered_text(self):
        retrieval,cards=retrieval_fixture()
        for mutation in [lambda c:c.update(date='2026-10-08'), lambda c:c.update(to_station='南京南'),
                         lambda c:c.update(schema_version=2), lambda c:c.update(sources=['other']),
                         lambda c:c.update(fetched_at='other')]:
            damaged=copy.deepcopy(cards);mutation(damaged[0])
            result=ticket_delivery.prepare(retrieval,[CAP],damaged)
            self.assertIn('ticket.price重复正文',result['direct_answer'])
        self.assertIs(ticket_delivery.prepare(retrieval,[CAP],[]),retrieval)

    def test_untracked_clarification_and_extra_prose_cannot_be_dropped(self):
        retrieval,cards=retrieval_fixture();retrieval['direct_answer']+='\n\n请补充实际区间'
        self.assertIs(ticket_delivery.prepare(retrieval,[CAP],cards),retrieval)

    def test_malformed_success_receipt_never_proves_card_coverage(self):
        params=dict(CTX)
        bad=ToolResult(ok=True,data={'query_date':DAY,'data':[{'train_code':'G2','prices':{'二等座':1}}]})
        piece=ticket_delivery.fragment('ticket.price',params,bad,'原始不可确认记录')
        retrieval=dict(data=[piece['record']],direct_fragments=[piece],direct_answer=piece['text'])
        cards=serialize_display_results(retrieval['data'],[])
        self.assertIs(ticket_delivery.prepare(retrieval,[CAP],cards),retrieval)


class RequestCapabilities(unittest.TestCase):
    def test_optional_strict_bounded_deduplicated_capabilities(self):
        self.assertEqual(ChatRequest(message='你好').client_capabilities,[])
        self.assertEqual(ChatRequest(message='你好',client_capabilities=[CAP,CAP,'unknown']).client_capabilities,[CAP,'unknown'])
        for invalid in [None,CAP,[1],[''],['x'*81],['x']*33]:
            with self.assertRaises(ValidationError):
                ChatRequest(message='你好',client_capabilities=invalid)


class DeliveryIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for p in [patch.dict(os.environ,{'APP_VARIANT':'main'}),
                  patch('app.pipeline.service_dates.railway_today',return_value=date(2026,10,6)),
                  patch('app.dates.railway_today',return_value=date(2026,10,6)),
                  patch.object(service_batch,'get_settings',return_value=SimpleNamespace(tool_concurrency=2))]:
            p.start();self.addCleanup(p.stop)
        await rt.ensure_loaded()

    async def execute(self,message=MESSAGE,*,caps=None,mode='stream',fail=None,filtered=False):
        calls=[]
        async def invoke(name,params):
            calls.append((name,copy.deepcopy(params)))
            return receipt(name,params,fail=fail,filtered=filtered)
        with patch('app.tools.registry.invoke_by_name',invoke), \
             patch.object(orchestrator.planner,'decide',AsyncMock(side_effect=AssertionError('decision model invoked'))), \
             patch.object(orchestrator.llm_client,'stream_completion',side_effect=AssertionError('generation model invoked')), \
             patch('app.pipeline.generate.chat_with_reasoning',AsyncMock(side_effect=AssertionError('generation model invoked'))):
            if mode=='block':
                result=await orchestrator.run(message,client_capabilities=caps)
                return result.dict(),result.answer,[],calls
            events=[e async for e in orchestrator.run_stream(message,client_capabilities=caps)]
            self.assertFalse([e for e in events if e['type']=='error'],events)
            done=[e for e in events if e['type']=='done']
            self.assertEqual(len(done),1)
            self.assertTrue(done[0]['answer_done'])
            self.assertFalse(done[0]['degraded'])
            return done[0],''.join(e.get('delta','') for e in events if e['type']=='answer'),events,calls

    async def test_capable_client_gets_one_card_without_any_answer_or_replace(self):
        done,answer,events,calls=await self.execute(caps=[CAP])
        self.assertEqual(answer,'')
        self.assertFalse([e for e in events if e['type'] in ['answer','replace']])
        self.assertEqual(len(done['display_results']),1)
        card=done['display_results'][0]
        self.assertEqual(card['prices'][0]['amount'],'661')
        self.assertEqual(card['availability']['seats'][0]['status'],'available')
        self.assertEqual({n for n,p in calls},{'ticket.price','ticket.query'})

    async def test_legacy_unknown_clients_keep_both_component_bodies(self):
        for caps in [None,[],['unknown']]:
            done,answer,events,_=await self.execute(caps=caps)
            self.assertIn('661',answer)
            self.assertIn('余票',answer)
            self.assertIn('有',answer)
            self.assertTrue([e for e in events if e['type']=='answer'])
            self.assertEqual(len(done['display_results']),1)

    async def test_block_and_stream_use_identical_cards_and_empty_text(self):
        stream,answer,_,_=await self.execute(caps=[CAP])
        block,block_answer,_,_=await self.execute(caps=[CAP],mode='block')
        self.assertEqual(answer,block_answer)
        self.assertEqual(stream['display_results'],block['display_results'])
        self.assertEqual(block['usage']['total_tokens'],0)

    async def test_multifuture_dates_keep_three_cards_without_duplicate_text(self):
        done,answer,events,calls=await self.execute('明天后天大后天北京南到上海虹桥G1票价',caps=[CAP])
        self.assertEqual(answer,'')
        self.assertFalse([e for e in events if e['type'] in ['answer','replace']])
        self.assertEqual([c['date'] for c in done['display_results']],['2026-10-07','2026-10-08','2026-10-09'])
        self.assertEqual(len(calls),6)

    async def test_partial_failures_both_directions_preserved_in_cards_not_duplicate_prose(self):
        for failure in ['ticket.price','ticket.query']:
            done,answer,events,_=await self.execute(caps=[CAP],fail=failure)
            self.assertEqual(answer,'')
            self.assertFalse([e for e in events if e['type'] in ['answer','replace']])
            card=done['display_results'][0]
            self.assertEqual(card['status'],'partial')
            if failure=='ticket.price':
                self.assertEqual(card['fare_error'],'独立组件超时')
                self.assertEqual(card['availability']['seats'][0]['status'],'available')
            else:
                self.assertEqual(card['availability']['error'],'独立组件超时')
                self.assertEqual(card['prices'][0]['amount'],'661')

    async def test_filter_truncation_supplement_survives_without_duplicate_prices(self):
        _,answer,_,_=await self.execute(caps=[CAP],filtered=True)
        self.assertIn('已截断',answer)
        self.assertIn('seat=二等座',answer)
        self.assertIn('命中 3 趟',answer)
        self.assertNotIn('661',answer)

    async def test_missing_interval_clarification_stays_visible_without_tools(self):
        done,answer,_,calls=await self.execute('明天G1票价',caps=[CAP])
        self.assertIn('实际乘车',answer)
        self.assertEqual(calls,[])
        self.assertEqual(done['display_results'],[])

    async def test_api_routes_forward_capabilities_before_streaming(self):
        req=ChatRequest(message='你好',client_capabilities=[CAP])
        run=AsyncMock(return_value=PipelineResult(intent='闲聊'))
        with patch.object(chat.orchestrator,'run',run):
            await chat.chat(req)
        self.assertEqual(run.call_args.kwargs['client_capabilities'],[CAP])
        captured=[]
        async def stream(message,**kwargs):
            captured.append(kwargs)
            yield {'type':'done','display_results':[]}
        request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        with patch.object(chat.orchestrator,'run_stream',stream):
            response=await chat.chat_stream(req,request)
            chunks=[chunk async for chunk in response.body_iterator]
        self.assertEqual(captured[0]['client_capabilities'],[CAP])
        self.assertEqual(json.loads(chunks[0].split('data: ',1)[1])['type'],'done')

    async def test_sessions_advertisement_is_main_only(self):
        self.assertIn(CAP,(await chat.sessions_info())['supported_client_capabilities'])
        with patch.dict(os.environ,{'APP_VARIANT':'lm'}):
            self.assertNotIn('supported_client_capabilities',await chat.sessions_info())


if __name__=='__main__':
    unittest.main()
