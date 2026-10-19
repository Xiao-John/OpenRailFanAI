import asyncio,json,logging,time,statistics
from pathlib import Path
from app import metrics
from app.config import get_settings
from app.llm import client
from app.pipeline.planner import _merged_static_prompt,_merged_user_tail
from app.pipeline.schemas import COMBINED_JSON_SCHEMA
from app.pipeline.generate import build_prompt
from app.pipeline.extract import Slots
logging.disable(logging.CRITICAL)
OUT=Path(__file__).resolve().parent
QUESTIONS=['CR400AF和CR400BF有什么区别？','高铁和动车有什么不同？','动车组再生制动的原理是什么？','CR400AF和CR400BF有什么区别？']
async def run():
 if get_settings().llm_mock:raise RuntimeError('real API required')
 observed=[];old=metrics.record_llm
 def record(*args,**kwargs):
  old(*args,**kwargs);observed.append({'prompt_tokens':args[2] if len(args)>2 else 0,'cache':kwargs.get('prompt_cache')})
 metrics.record_llm=record
 samples=[]
 try:
  for group in ['decision','generation']:
   for i,question in enumerate(QUESTIONS):
    observed.clear();first=None;t=time.perf_counter();sample={'group':group,'index':i,'repeat_first_question':i==3}
    try:
     async with asyncio.timeout(45):
      if group=='decision':
       await client.chat_structured(_merged_static_prompt(compact=False),schema=COMBINED_JSON_SCHEMA,no_think=True,prompt_tail=_merged_user_tail(question,None))
      else:
       prompt=build_prompt(question,Slots(),{'data':[],'sources':[],'tool_trace':[]},question_type='knowledge')
       async for kind,text in client.stream_completion(prompt,max_tokens=96,no_think=True):
        if text and first is None:first=(time.perf_counter()-t)*1000
     sample.update(ok=True,elapsed_ms=(time.perf_counter()-t)*1000,first_content_ms=first,usage_observations=list(observed))
    except Exception as e:sample.update(ok=False,error_type=type(e).__name__,http_status=client._status_of(e.__cause__ or e),elapsed_ms=(time.perf_counter()-t)*1000,usage_observations=list(observed))
    samples.append(sample);(OUT/'samples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2))
    print(json.dumps({'group':group,'index':i,'ok':sample['ok'],'usage':sample['usage_observations']}),flush=True)
    if not sample['ok']:break
  summary={}
  for group in ['decision','generation']:
   rs=[s for s in samples if s['group']==group];us=[u for s in rs for u in s['usage_observations']];known=[u['cache'] for u in us if u['cache'] is not None]
   total=sum(x[2] for x in known);hit=sum(x[0] for x in known)
   summary[group]={'samples':len(rs),'successful':sum(s['ok'] for s in rs),'reported_calls':len(known),'unreported_calls':len(us)-len(known),'hit_tokens':hit,'miss_tokens':sum(x[1] for x in known),'reported_prompt_tokens':total,'token_hit_rate':hit/total if total else None,'request_hit_rate':sum(x[0]>0 for x in known)/len(known) if known else None,'mean_elapsed_ms':statistics.mean(s['elapsed_ms'] for s in rs) if rs else None}
  (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(OUT/'metrics.json').write_text(json.dumps(metrics.snapshot(),ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False))
 finally:metrics.record_llm=old;await client.shutdown_clients()
asyncio.run(run())
