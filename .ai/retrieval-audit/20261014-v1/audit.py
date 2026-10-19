"""Read-only production-path audit; artifacts contain counts/timing, not payloads."""
import asyncio
from collections import Counter, defaultdict
from contextlib import ExitStack
import hashlib
import json
import logging
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from app.pipeline import orchestrator, retrieve, service_dispatch
from app.pipeline.extract import Slots
from app.tools import registry, _rt12306 as rt, _http, executed_fare
from app.tools.base import ToolResult
from app.llm import client as llm
from app import metrics

logging.disable(logging.CRITICAL)
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def historical():
    directory = ROOT / '.ai/backend-optimization/after-final'
    samples = json.loads((directory/'samples.json').read_text())
    spans = {s['ordinal']: s for s in map(json.loads, (directory/'telemetry.jsonl').read_text().splitlines())}
    rows = []
    for sample in samples:
        span = spans[sample['ordinal']]
        retrieval = sum(c['elapsed_ms'] for c in span['timed_calls'] if c['name']=='retrieve')
        decision = sum(c['elapsed_ms'] for c in span['timed_calls'] if c['name']=='decision')
        total = span['server_elapsed_ms']
        rows.append(dict(ordinal=sample['ordinal'], scenario=sample['scenario'],
                         retrieve_ms=retrieval, decision_ms=decision, pipeline_ms=total,
                         generation_ms=span.get('generation_elapsed_ms'),
                         retrieve_share=retrieval/total, degraded=sample['degraded'],
                         error=sample['has_error']))
    groups = defaultdict(list)
    for row in rows:
        groups[row['scenario']].append(row)
    def summary(group):
        return dict(n=len(group), retrieve_median_ms=statistics.median(r['retrieve_ms'] for r in group),
                    pipeline_median_ms=statistics.median(r['pipeline_ms'] for r in group),
                    share_median=statistics.median(r['retrieve_share'] for r in group),
                    share_weighted=sum(r['retrieve_ms'] for r in group)/sum(r['pipeline_ms'] for r in group),
                    degraded=sum(r['degraded'] for r in group), errors=sum(r['error'] for r in group))
    save('historical.json', dict(source=str(directory.relative_to(ROOT)),
                                source_hashes={f:hashlib.sha256((directory/f).read_bytes()).hexdigest()
                                               for f in ['samples.json','telemetry.jsonl']},
                                rows=rows, groups={name:summary(group) for name,group in groups.items()},
                                overall=summary(rows)))

async def plans():
    cases = [
        ('explicit_schedule','G1今天的时刻表',None,None,None),
        ('scalar_schedule','G1今天几点出发','schedule','realtime',Slots(target='G1',time='今天')),
        ('scalar_routing','G1今天由谁担当','emu_routing','realtime',Slots(target='G1',time='今天')),
        ('future_schedule','G1明天几点出发','schedule','realtime',Slots(target='G1',time='明天')),
        ('invalid_schedule_target','标杆今天时刻','schedule','realtime',Slots(target='标杆',time='今天')),
        ('mileage_and_stops','北京南到济南西的里程和经过哪些站','rail_line','realtime',Slots(location='北京南',target='济南西')),
        ('general_mixed','铁路信号原理','general','mixed',Slots()),
        ('news_mixed','铁路最新进展','news','mixed',Slots()),
        ('station_code','北京南电报码','station','knowledge',Slots(location='北京南')),
        ('photo','吉林市铁路拍摄机位','photo_spot','realtime',Slots(location='吉林市')),
        ('missing_interval','G1明天还有票吗',None,None,None),
        ('over_limit',' '.join('G'+str(i) for i in range(1,12))+' 明天时刻表',None,None,None),
        ('multi_fare','G1、G2 明天北京南到上海虹桥票价',None,None,None),
    ]
    observations=[]
    for name,message,intent,qtype,slots in cases:
        calls=[]
        async def fake(tool,params):
            calls.append({'tool':tool,'params':params})
            await asyncio.sleep(0)
            return ToolResult(ok=False,error='offline plan audit; no external request')
        if intent is None:
            decision=service_dispatch.decide(message)
            assert decision is not None, name
            intent,qtype,slots=decision[0].value,decision[1],decision[2]
        with patch.object(registry,'invoke_by_name',fake):
            await retrieve.retrieve(intent,slots,question_type=qtype,message=message)
        counts=Counter(json.dumps(c,sort_keys=True) for c in calls)
        observations.append(dict(case=name,mode='explicit_dispatch' if name in {'explicit_schedule','missing_interval','over_limit','multi_fare'} else 'resolved_slots',
                                 calls=calls, exact_duplicate_calls=sum(n-1 for n in counts.values())))
    save('plans.json',observations)

async def fare_fanout():
    calls=[]
    class Response:
        status_code=200
        def __init__(self,payload):self.payload=payload
        def raise_for_status(self):pass
        def json(self):return self.payload
    class Http:
        async def get(self,url,**kw):
            phase = 'init' if url==executed_fare.INIT else 'rows' if url==executed_fare.ROWS else 'execution'
            calls.append(phase)
            await asyncio.sleep(.001)
            if phase=='rows':
                rows=[]
                for code in ['G1','G2']:
                    p=['']*36
                    for i,v in {2:'internal-'+code,3:code,6:'VNP',7:'AOH',8:'08:00',9:'13:00',10:'05:00',16:'01',17:'02',35:'O'}.items():p[i]=v
                    rows.append('|'.join(p))
                return Response({'status':True,'data':{'result':rows}})
            if phase=='execution':return Response({'status':True,'data':{'train_no':kw['params']['train_no'],'O':'100'}})
            return Response({})
    async def station(name):return ('VNP','北京南') if name=='北京南' else ('AOH','上海虹桥')
    async def http():return Http()
    with patch.object(rt,'resolve_station_code',station),patch.object(executed_fare,'get_client',http):
        results=await asyncio.gather(*(executed_fare.query('北京南','上海虹桥','2026-10-15',code) for code in ['G1','G2']))
    assert all(len(r['data'])==1 for r in results)
    save('fare-fanout.json',dict(mode='offline transport; real executed_fare.query',trains=2,
                               counts=dict(Counter(calls)),query_date='2026-10-15'))

async def live():
    cases=[('schedule','G1明天的时刻表'),('fare_pair','G1明天北京南到上海虹桥票价'),
           ('two_train_fare','G1、G2 明天北京南到上海虹桥票价')]
    samples=[]
    original=registry.invoke_by_name
    operation=rt._operation
    overall=time.perf_counter()
    async def forbidden(*args,**kwargs):
        raise AssertionError('cloud model prohibited in retrieval audit')
        yield
    for case,message in cases:
        if time.perf_counter()-overall>120:break
        tool_calls=[];operations=[];started=time.perf_counter()
        async def invoke(name,params):
            t=time.perf_counter();result=None
            try:
                result=await original(name,params)
                return result
            finally:tool_calls.append(dict(name=name,start_ms=(t-started)*1000,
                                          elapsed_ms=(time.perf_counter()-t)*1000,
                                          ok=bool(result and result.ok)))
        async def op(name,awaitable,**kwargs):
            t=time.perf_counter()
            try:return await operation(name,awaitable,**kwargs)
            finally:operations.append(dict(name=name,start_ms=(t-started)*1000,
                                           elapsed_ms=(time.perf_counter()-t)*1000))
        row=dict(case=case,tools=tool_calls,operations=operations,stages={})
        with patch.object(registry,'invoke_by_name',invoke),patch.object(rt,'_operation',op),patch.object(llm,'stream_completion',forbidden):
            try:
                async with asyncio.timeout(40):
                    async for event in orchestrator.run_stream(message):
                        if event['type']=='stage':row['stages'][event['stage']]=event.get('ms')
                        if event['type']=='done':
                            row.update(done=True,pipeline_ms=event['latency_ms'],degraded=event['degraded'],
                                       error=bool(event.get('error')),tool_trace=event.get('tool_trace'),
                                       total_tokens=event['usage']['total_tokens'])
                        if event['type']=='error':row['error_event']=True
            except Exception as e:row.update(exception=type(e).__name__,done=False)
        row['wall_ms']=(time.perf_counter()-started)*1000
        if row.get('done') and row['pipeline_ms']:
            row['retrieve_share']=row['stages'].get('retrieve',0)/row['pipeline_ms']
        samples.append(row);save('live.json',samples)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    # Earlier controlled probes share this process; this aggregate is diagnostic
    # only. Use live.json per-call spans for real timing and counts.
    save('mixed-diagnostic-metrics.json',metrics.snapshot())

async def main():
    historical()
    await rt.ensure_loaded()
    await plans()
    await fare_fanout()
    try:await live()
    finally:
        await registry.close_queries()
        await rt.close_query_flights()
        await _http.aclose_client()
        await llm.shutdown_clients()

asyncio.run(main())
