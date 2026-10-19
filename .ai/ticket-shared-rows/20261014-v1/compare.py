"""Alternating live Main rollback/shared comparison; no cloud model calls."""
import asyncio
from collections import Counter, defaultdict
import json
import logging
from pathlib import Path
import statistics
import time
from unittest.mock import patch
from app.pipeline import orchestrator
from app.tools import registry, _rt12306 as rt, _http
from app.llm import client as llm

logging.disable(logging.CRITICAL)
OUT=Path(__file__).resolve().parent

async def main():
    samples=[]
    old=registry.invoke_by_name
    operation=rt._operation
    started=time.perf_counter()
    async def no_cloud(*args,**kwargs):
        raise AssertionError('cloud generation prohibited')
        yield
    try:
        for case,message in [('single','G1明天北京南到上海虹桥票价'),
                             ('two','G1、G3 明天北京南到上海虹桥票价')]:
            for repeat in range(3):
                for enabled in ([False,True] if repeat%2==0 else [True,False]):
                    if time.perf_counter()-started>80:break
                    await registry.close_queries()
                    await rt.close_query_flights()
                    row=dict(case=case,repeat=repeat+1,enabled=enabled,operations=[],tools=[])
                    async def invoke(name,params):
                        result=await old(name,params)
                        data=result.data if isinstance(result.data,dict) else {}
                        entries=data.get('trains') if name=='ticket.query' else data.get('data')
                        row['tools'].append(dict(name=name,train=params.get('train'),ok=result.ok,
                                                 rows=len(entries) if isinstance(entries,list) else None))
                        return result
                    async def op(name,awaitable,**kwargs):
                        row['operations'].append(name)
                        return await operation(name,awaitable,**kwargs)
                    with patch.dict('os.environ',MAIN_TICKET_SHARED_ROWS_ENABLED=str(enabled).lower()), \
                         patch.object(registry,'invoke_by_name',invoke),patch.object(rt,'_operation',op), \
                         patch.object(llm,'stream_completion',no_cloud):
                        try:
                            async with asyncio.timeout(20):
                                async for event in orchestrator.run_stream(message):
                                    if event['type']=='stage' and event['stage']=='retrieve':row['retrieve_ms']=event['ms']
                                    if event['type']=='done':row.update(pipeline_ms=event['latency_ms'],tokens=event['usage']['total_tokens'],error=bool(event.get('error')))
                        except Exception as exc:row['exception']=type(exc).__name__
                    row['counts']=dict(Counter(row['operations']))
                    samples.append(row)
                    (OUT/'comparison-samples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2)+'\n')
                    print(json.dumps(row,ensure_ascii=False),flush=True)
        summary={}
        for case in ['single','two']:
            groups={}
            for enabled in [False,True]:
                rows=[r for r in samples if r['case']==case and r['enabled']==enabled and 'retrieve_ms' in r]
                groups[str(enabled)]=dict(n=len(rows),retrieve_median_ms=statistics.median(r['retrieve_ms'] for r in rows) if rows else None,
                                         all_tools_have_rows=bool(rows) and all(t['ok'] and t['rows'] for r in rows for t in r['tools']))
            before,after=groups['False']['retrieve_median_ms'],groups['True']['retrieve_median_ms']
            summary[case]=dict(groups=groups,observed_reduction=1-after/before if before and after else None)
        (OUT/'comparison-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    finally:
        await registry.close_queries()
        await rt.close_query_flights()
        await _http.aclose_client()
        await llm.shutdown_clients()

asyncio.run(main())
