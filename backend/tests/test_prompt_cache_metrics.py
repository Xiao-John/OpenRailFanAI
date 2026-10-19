import json
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
from app.llm import client
from app import metrics
from test_llm_pool import SETTINGS, PROVIDER, factory, httpx

class PromptCache(unittest.TestCase):
    def test_dialects_and_missing_zero(self):
        f=client._prompt_cache_usage
        self.assertIsNone(f(Obj(prompt_tokens=100),'chat_completions'))
        self.assertEqual(f(Obj(prompt_tokens=100,prompt_cache_hit_tokens=0,prompt_cache_miss_tokens=100),'chat_completions'),(0,100,100))
        self.assertEqual(f(Obj(input_tokens=100,input_tokens_details=Obj(cached_tokens=40)),'responses'),(40,60,100))
        self.assertEqual(f({'prompt_tokens':100,'prompt_tokens_details':{'cached_tokens':40}},'chat_completions'),(40,60,100))
    def test_bad_metadata_is_not_a_miss(self):
        for hit,miss in [(101,0),(-1,101),(True,99),(40,40)]:
            self.assertIsNone(client._prompt_cache_usage(Obj(prompt_tokens=100,prompt_cache_hit_tokens=hit,prompt_cache_miss_tokens=miss),'chat_completions'))
        self.assertIsNone(client._prompt_cache_usage(Obj(prompt_tokens=100,prompt_cache_hit_tokens=40,prompt_tokens_details=Obj(cached_tokens=20)),'chat_completions'))
    def test_rates_exclude_unreported_calls(self):
        key='cache-test-isolated'
        with metrics._LOCK:metrics._LLM.pop(key,None)
        metrics.record_llm(key,1,100,1)
        metrics.record_llm(key,1,100,1,prompt_cache=(0,100,100))
        metrics.record_llm(key,1,100,1,prompt_cache=(40,60,100))
        c=metrics.snapshot()['llm'][key]['prompt_cache']
        self.assertEqual((c['reported_calls'],c['unreported_calls']),(2,1))
        self.assertEqual(c['token_hit_rate'],.2);self.assertEqual(c['request_hit_rate'],.5)
    def test_call_tracks_cache_without_changing_chat_usage(self):
        client.reset_run_metrics();before=set(client.get_run_metrics())
        call=client._Call(); token=client._CALL.set(call)
        try:
            client._record_usage(Obj(usage=Obj(prompt_tokens=100,completion_tokens=2,total_tokens=102,prompt_tokens_details=Obj(cached_tokens=40))),1,'chat_completions')
            self.assertEqual(call.prompt_cache,(40,60,100))
            self.assertEqual(set(client.get_run_metrics()),before)
        finally:client._CALL.reset(token)

class StreamingPromptCache(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_usage_counted_once_and_missing_final_is_unknown(self):
        batches = [
            [{'prompt_tokens': 100, 'completion_tokens': 1, 'total_tokens': 101,
              'prompt_cache_hit_tokens': 40, 'prompt_cache_miss_tokens': 60}] * 2,
            [{'prompt_tokens': 100, 'completion_tokens': 1, 'total_tokens': 101,
              'prompt_cache_hit_tokens': 40, 'prompt_cache_miss_tokens': 60},
             {'prompt_tokens': 120, 'completion_tokens': 2, 'total_tokens': 122}],
            [{'prompt_tokens': 100, 'completion_tokens': 1, 'total_tokens': 101,
              'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 100}],
        ]
        created = []

        def handler(request):
            chunks = []
            for usage in batches.pop(0):
                chunk = {'id': 'offline-cache', 'object': 'chat.completion.chunk',
                         'created': 0, 'model': 'fake-model', 'choices': [], 'usage': usage}
                chunks.append('data: ' + json.dumps(chunk) + '\n\n')
            return httpx.Response(200, request=request,
                                  headers={'content-type': 'text/event-stream'},
                                  content=(''.join(chunks) + 'data: [DONE]\n\n').encode())

        with metrics._LOCK:
            metrics._LLM.pop(PROVIDER.id, None)
        with patch.object(client, 'get_settings', lambda: SETTINGS), \
             patch.object(client, 'current_provider', lambda: PROVIDER), \
             patch.object(client, 'DefaultAsyncHttpxClient', factory(handler, created)):
            await client.shutdown_clients()
            try:
                for _ in range(3):
                    client.reset_run_metrics()
                    self.assertEqual([piece async for piece in client.stream_completion('test')], [])
                row = metrics.snapshot()['llm'][PROVIDER.id]
                cache = row['prompt_cache']
                self.assertEqual(row['calls'], 3)
                self.assertEqual(row['prompt_tokens'], 320)
                self.assertEqual(cache['reported_calls'], 2)
                self.assertEqual(cache['unreported_calls'], 1)
                self.assertEqual(cache['hit_tokens'], 40)
                self.assertEqual(cache['miss_tokens'], 160)
                self.assertEqual(cache['token_hit_rate'], .2)
                self.assertEqual(cache['request_hit_rate'], .5)
            finally:
                await client.shutdown_clients()


if __name__=='__main__':unittest.main()
