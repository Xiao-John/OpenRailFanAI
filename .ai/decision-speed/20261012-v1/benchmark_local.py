import asyncio,importlib.util,sys,json,time,statistics,hashlib
from pathlib import Path
from app.pipeline import fastpath as new
from app.tools import _rt12306 as rt
out=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('decision_before',out/'fastpath-before.py');old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
records=[json.loads(s) for s in Path('backend/tests/corpus/intent_corpus.jsonl').read_text().splitlines() if s.strip()]
def normalize(result):
 fp,defer=result
 return {'intent':fp.intent,'question_type':fp.question_type,'slots':fp.slots.non_empty(),'reason':fp.reason,'matched':fp.matched} if fp else {'defer':defer.reason,'detail':defer.detail}
async def run():
 await rt.ensure_loaded()
 differences=[];takes={'before':[],'after':[]};semantics=[]
 for r in records:
  a=normalize(await old.plan_with_reason(r['message'],r.get('history')));b=normalize(await new.plan_with_reason(r['message'],r.get('history')))
  if a!=b:differences.append(r['id'])
  semantics.append({'id':r['id'],'same':a==b,'deterministic':'intent' in b})
 assert not differences,differences
 for iteration in range(12):
  for label,m in ([('before',old),('after',new)] if iteration%2==0 else [('after',new),('before',old)]):
   for r in records:
    t=time.perf_counter_ns();await m.plan_with_reason(r['message'],r.get('history'));takes[label].append((time.perf_counter_ns()-t)/1e6)
 metrics={k:{'mean_ms':statistics.mean(v),'p50_ms':statistics.median(v),'p95_ms':sorted(v)[int(.95*len(v))],'calls':len(v)} for k,v in takes.items()}
 report={'scope':'local deterministic decision only; no LLM/network calls','records':len(records),'semantic_differences':len(differences),'deterministic_cases':sum(s['deterministic'] for s in semantics),'metrics':metrics,'mean_latency_reduction_pct':100*(1-metrics['after']['mean_ms']/metrics['before']['mean_ms']),'before_sha256':hashlib.sha256((out/'fastpath-before.py').read_bytes()).hexdigest(),'after_sha256':hashlib.sha256(Path('backend/app/pipeline/fastpath.py').read_bytes()).hexdigest()}
 (out/'local-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(out/'semantic-parity.json').write_text(json.dumps(semantics,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
asyncio.run(run())
