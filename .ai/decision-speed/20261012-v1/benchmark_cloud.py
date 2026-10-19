import asyncio,json,logging,statistics,time
from pathlib import Path
from app.pipeline.planner import _merged_static_prompt,_merged_user_tail
from app.pipeline.schemas import COMBINED_JSON_SCHEMA
from app.llm.client import chat_structured
from app.config import get_settings
logging.disable(logging.CRITICAL)
OUT=Path(__file__).resolve().parent
CASES=[('knowledge','CR400AF和CR400BF有什么区别？',None,'general','knowledge'),('station','北京南站的电报码是多少？',None,'station','realtime'),('multi_train','G1和G2明天的时刻表',None,'schedule','realtime'),('followup','那商务座呢？',[{'role':'user','content':'明天G1北京南到上海虹桥二等座票价'}],'ticket','realtime')]
async def run():
 if get_settings().llm_mock:raise RuntimeError('real API required')
 samples=[]
 for round_index in range(2):
  for ident,text,history,want_intent,want_q in CASES:
   for compact in ([False,True] if round_index%2==0 else [True,False]):
    start=time.perf_counter();row={'id':ident,'round':round_index,'compact':compact}
    try:
     async with asyncio.timeout(35):
      data=await chat_structured(_merged_static_prompt(compact=compact),schema=COMBINED_JSON_SCHEMA,no_think=True,schema_in_prompt=not compact,prompt_tail=_merged_user_tail(text,history))
     row.update(ok=True,intent=data.get('intent'),question_type=data.get('question_type'),basic_labels_match=data.get('intent')==want_intent and data.get('question_type')==want_q,slots={k:data.get(k) for k in ['location','target','time','direction','extra']})
    except Exception as e:row.update(ok=False,error_type=type(e).__name__)
    row['elapsed_ms']=(time.perf_counter()-start)*1000;samples.append(row)
    (OUT/'cloud-samples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2))
    if not row['ok']:
     (OUT/'cloud-benchmark.json').write_text(json.dumps({'status':'stopped_on_error','error_type':row['error_type'],'completed_samples':len(samples)},indent=2));print('cloud experiment stopped:',row['error_type']);return
 results={}
 for compact in [False,True]:
  rs=[s for s in samples if s['compact']==compact]
  results['compact' if compact else 'current']={'calls':len(rs),'mean_ms':statistics.mean(s['elapsed_ms'] for s in rs),'basic_labels_correct':sum(s['basic_labels_match'] for s in rs)}
 report={'status':'complete','scope':'model decision only; 4 public probe cases, 2 rounds; not full accuracy certification','results':results,'mean_latency_reduction_pct':100*(1-results['compact']['mean_ms']/results['current']['mean_ms'])}
 (OUT/'cloud-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
asyncio.run(run())
