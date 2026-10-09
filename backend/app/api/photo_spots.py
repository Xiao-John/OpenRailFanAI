"""Additive read-only endpoints; chat/SSE contracts remain unchanged."""
import sqlite3
from contextlib import closing
from fastapi import APIRouter, HTTPException, Query
from app.data.dict import db_path
from app.data import photo_annotations as annotations

router=APIRouter(prefix='/photo-spots',tags=['photo-spots'])

def connection():
    path=db_path()
    if not path.exists(): raise HTTPException(503,'机位词典尚未就绪')
    conn=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    return conn

@router.get('/documents')
def document(url: str = Query(min_length=1,max_length=2048)):
    with closing(connection()) as conn:
        return annotations.document_index(conn,url)

@router.get('/issues/{issue_id}')
def issue(issue_id: str):
    with closing(connection()) as conn:
        result=annotations.issue_detail(conn,issue_id)
        if result is None: raise HTTPException(404,'说明索引不存在或已更新')
        return result

@router.get('/search')
def search(scope: str = Query(default='',max_length=100), city: str | None=None,
           point_type: str | None=None, view_target: str | None=None,
           season: str | None=None, time_of_day: str | None=None,
           limit: int = Query(default=3,ge=1,le=20)):
    filters={k:v for k,v in dict(city=city,point_type=point_type,view_target=view_target,season=season,time_of_day=time_of_day).items() if v}
    enums={'point_type':{'park','bridge','roadside','station_platform','viewing_platform','museum','other'},
           'view_target':{'operating_railway','yard_or_depot','static_rolling_stock','mixed','railway_remains'},
           'season':{'spring','summer','autumn','winter','all_year'},
           'time_of_day':{'dawn','morning','noon','afternoon','dusk','night','any'}}
    if any(k in enums and v not in enums[k] for k,v in filters.items()):
        raise HTTPException(422,'不支持的过滤条件')
    if not scope and not filters: raise HTTPException(422,'请提供地点或过滤条件')
    with closing(connection()) as conn:
        result=annotations.candidates(conn,scope,filters,limit)
        # Normal retrieval never exposes long explanations/evidence.
        for item in result['items']:
            for c in item['claims']: c.pop('evidence',None)
        return result
