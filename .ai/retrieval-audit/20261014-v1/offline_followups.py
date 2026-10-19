"""Controlled evidence for candidate overfetch and equivalent-date cache keys."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.tools import web_search, registry
from app.tools.base import ToolResult
from app.dates import railway_today

logging.disable(logging.CRITICAL)
OUT=Path(__file__).resolve().parent

async def main():
    fetches=[]
    async def page(url,*,max_chars):
        fetches.append({'candidate':url.rsplit('/',1)[-1],'max_chars':max_chars})
        return dict(url=url,title='controlled fixture',ok=True,text='x'*max_chars,
                    truncated=True,error='')
    settings=SimpleNamespace(web_search_fetch_top_n=2,fact_text_max_chars=4000,web_search_fetch_chars=1800)
    results=[{'url':f'https://fixture.invalid/{i}'} for i in range(4)]
    with patch.object(web_search,'get_settings',lambda:settings),patch.object(web_search,'fetch_page',page):
        kept,_=await web_search.WebSearchTool2()._fetch_top_pages(results,used_chars=5000)
    assert len(fetches)==4 and len(kept)==2
    assert all(f['max_chars']==400 for f in fetches)

    calls=[]
    class Fake:
        enabled=True
        async def invoke(self,params):
            calls.append(params)
            return ToolResult(ok=True,data={'fixture':True})
    await registry.close_queries()
    with patch.object(registry,'get',lambda name:Fake()):
        params={'from_station':'北京南','to_station':'上海虹桥','date':'今天'}
        await registry.invoke_by_name('ticket.query',params)
        await registry.invoke_by_name('ticket.query',{**params,'date':railway_today().isoformat()})
        await registry.invoke_by_name('ticket.query',{**params,'date':railway_today().isoformat()})
    assert len(calls)==2
    await registry.close_queries()
    evidence=dict(mode='offline controlled fixtures; no network',
                  web=dict(fetched=4,kept=2,discarded_successful=2,remaining_budget=0,fetches=fetches),
                  date_cache=dict(logical_calls=3,physical_fixture_invocations=len(calls),
                                  dates=[p['date'] for p in calls],explanation='relative and absolute same-day keys differ; repeated absolute date hits'))
    (OUT/'offline-followups.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(evidence,ensure_ascii=False))

asyncio.run(main())
