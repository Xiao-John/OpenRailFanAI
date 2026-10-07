"""Dictionary checks and explicit local refresh, independent of software releases."""
import asyncio
import ipaddress
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, constr
from app.data import dict as data
from app.updates import dictionary, github

router=APIRouter(prefix='/updates/dictionary',tags=['dictionary-updates'])


def local_mutation(request: Request):
    try:local=ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError,AttributeError):local=False
    origin=request.headers.get('origin')
    if (not local or request.headers.get('sec-fetch-site')=='cross-site'
            or (origin and (urlparse(origin).scheme,urlparse(origin).netloc)!=(request.url.scheme,request.url.netloc))):
        raise HTTPException(403,detail={'code':'local_only','message':'词典写入仅允许本机应用明确发起'})


def failure(error):
    return HTTPException(409 if error.code=='busy' else 502,detail={'code':error.code,'message':error.message})


@router.get('/local')
def current():
    try:return {'component':'dictionary','status':'ok','current':dictionary.local(data.db_path())}
    except github.UpdateError as error:raise failure(error)


@router.get('')
async def check():
    try:return await dictionary.check(data.db_path())
    except github.UpdateError as error:raise failure(error)


class ApplyRequest(BaseModel):
    latest_version: constr(regex=r'^gtfs-\d{8}-\d{6}$')


@router.post('/apply')
async def apply(body: ApplyRequest,request: Request):
    local_mutation(request)
    try:
        info=await dictionary.check(data.db_path())
        if info['latest_version']!=body.latest_version:
            raise HTTPException(409,detail={'code':'release_changed','message':'词典版本已变化，请重新检查'})
        if not info['update_available']:return {'component':'dictionary','status':'unchanged','current':info['current']}
        with tempfile.TemporaryDirectory(prefix='railfan-dictionary-') as folder:
            archive=await asyncio.wait_for(github.download(info['asset'],Path(folder)),timeout=180)
            snapshot=Path(folder)/'snapshot.db'
            await dictionary.run_io(dictionary.build_feed,archive,snapshot,body.latest_version)
            result=await dictionary.run_io(dictionary.merge,snapshot,data.db_path())
        return {'component':'dictionary',**result}
    except asyncio.TimeoutError:raise HTTPException(504,detail={'code':'timeout','message':'词典下载超时'})
    except github.UpdateError as error:raise failure(error)


@router.post('/apply/stream')
async def apply_stream(body: ApplyRequest,request: Request):
    local_mutation(request)
    from fastapi.responses import StreamingResponse
    from app.updates.dictionary_progress import stream
    return StreamingResponse(stream(body.latest_version,data.db_path()),media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
