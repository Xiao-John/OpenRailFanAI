"""Software releases: read-only checks and verified package delivery."""
import asyncio
from pathlib import Path
import tempfile
import threading
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, constr
from app.updates import github, software

router=APIRouter(prefix='/updates/software',tags=['software-updates'])
DOWNLOAD_SLOTS=threading.BoundedSemaphore(2)


def failure(error):
    return HTTPException(status_code=502,detail={'code':error.code,'message':error.message})


@router.get('')
async def check(current_version: str=Query(...,max_length=40),abi: str=Query('arm64',max_length=20)):
    try:return await software.check(current_version,abi)
    except github.UpdateError as error:raise failure(error)


class DownloadRequest(BaseModel):
    current_version: constr(max_length=40)
    latest_version: constr(max_length=40)
    abi: constr(max_length=20)='arm64'


@router.post('/download')
async def download(body: DownloadRequest):
    try:
        info=await software.check(body.current_version,body.abi)
        if info['latest_version']!=body.latest_version:
            raise HTTPException(409,detail={'code':'release_changed','message':'发布版本已变化，请重新检查'})
        if info['update_available'] is False:
            raise HTTPException(409,detail={'code':'not_newer','message':'没有比当前软件更新的版本'})
        if not info['asset']:raise HTTPException(409,detail={'code':'asset_missing','message':'该架构没有可下载安装包'})
        if not DOWNLOAD_SLOTS.acquire(blocking=False):
            raise HTTPException(429,detail={'code':'busy','message':'安装包下载繁忙，请稍后重试'})
        try:directory=Path(tempfile.mkdtemp(prefix='railfan-package-'))
        except BaseException:
            DOWNLOAD_SLOTS.release();raise
        import shutil
        try:path=await asyncio.wait_for(github.download(info['asset'],directory),timeout=600)
        except BaseException:
            shutil.rmtree(directory,ignore_errors=True);DOWNLOAD_SLOTS.release();raise
        return PackageResponse(path,directory=directory,media_type='application/vnd.android.package-archive',filename=info['asset']['name'],
                            headers={'X-Content-SHA256':info['asset']['sha256'],'Cache-Control':'no-store'})
    except asyncio.TimeoutError:raise HTTPException(504,detail={'code':'timeout','message':'安装包下载超时'})
    except github.UpdateError as error:raise failure(error)


class PackageResponse(FileResponse):
    def __init__(self,*args,directory,**kwargs):
        self.directory=directory
        super().__init__(*args,**kwargs)

    async def __call__(self,scope,receive,send):
        import shutil
        try:await super().__call__(scope,receive,send)
        finally:
            shutil.rmtree(self.directory,ignore_errors=True)
            DOWNLOAD_SLOTS.release()
