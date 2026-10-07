"""Public GitHub release metadata and bounded, checksum-verified downloads."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse

import httpx

APP_REPO = 'Xiao-John/OpenRailFanAI'
DICT_REPO = 'wensimehrp/chinese-railway-gtfs'
HEADERS = {'User-Agent':'OpenRailFanAI-updater','Accept':'application/vnd.github+json',
           'X-GitHub-Api-Version':'2022-11-28'}
MAX_METADATA = 2 * 1024 * 1024


class UpdateError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def validate_url(url: str, *, asset=False) -> None:
    parsed = urlparse(url)
    hosts = {'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'} if asset else {'api.github.com'}
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password
            or parsed.port not in (None,443)):
        raise UpdateError('unsafe_url','更新地址不属于允许的GitHub服务')


async def metadata(repo: str, endpoint='releases/latest'):
    if repo not in (APP_REPO,DICT_REPO):raise UpdateError('repository','未知更新仓库')
    url = f'https://api.github.com/repos/{repo}/{endpoint}'
    try:
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as client:
            async with client.stream('GET',url,headers=HEADERS) as response:
                if response.status_code == 404:raise UpdateError('not_published','尚无可用的正式更新')
                if response.status_code in (403,429):raise UpdateError('rate_limited','GitHub访问受限，请稍后重试')
                response.raise_for_status()
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>MAX_METADATA:raise UpdateError('metadata_size','更新信息超过允许大小')
        import json
        return json.loads(body)
    except UpdateError:raise
    except (httpx.HTTPError,ValueError,TypeError):raise UpdateError('network','无法读取GitHub更新信息') from None


def asset_info(raw: dict, repo: str, tag: str, limit: int) -> dict:
    name=raw.get('name','');digest=raw.get('digest') or ''
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise UpdateError('asset','更新文件名无效')
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):raise UpdateError('checksum_missing','发布文件缺少SHA-256校验信息')
    size=raw.get('size');aid=raw.get('id');url=raw.get('browser_download_url','')
    if type(size) is not int or not 0<size<=limit or type(aid) is not int or aid<=0:
        raise UpdateError('asset','发布文件大小或标识无效')
    validate_url(url,asset=True)
    if urlparse(url).netloc!='github.com' or urlparse(url).path!=f'/{repo}/releases/download/{tag}/{name}':
        raise UpdateError('asset','下载文件不属于目标发布版本')
    return {'id':aid,'name':name,'size':size,'sha256':digest[7:].lower(),'url':url}


async def download(asset: dict, directory: Path, *, progress=None) -> Path:
    directory.mkdir(parents=True,exist_ok=True)
    fd,path=tempfile.mkstemp(prefix='.update-',suffix='.part',dir=directory)
    import os
    os.close(fd);target=Path(path)
    try:
        url=asset['url'];count=0;digest=hashlib.sha256()
        if progress:progress({'stage':'download','completed':0,'total':asset['size'],'unit':'bytes'})
        async with httpx.AsyncClient(timeout=httpx.Timeout(30,read=60),trust_env=False,follow_redirects=False) as client:
            for attempt in range(6):
                validate_url(url,asset=True)
                async with client.stream('GET',url,headers={'User-Agent':HEADERS['User-Agent'],'Accept-Encoding':'identity'}) as response:
                    if response.status_code in (301,302,303,307,308):
                        from urllib.parse import urljoin
                        url=urljoin(url,response.headers.get('location',''));continue
                    response.raise_for_status()
                    with target.open('wb') as file:
                        async for chunk in response.aiter_bytes():
                            count+=len(chunk)
                            if count>asset['size']:raise UpdateError('size','下载文件超过声明大小')
                            digest.update(chunk);file.write(chunk)
                            if progress:progress({'stage':'download','completed':count,'total':asset['size'],'unit':'bytes'})
                    break
            else:raise UpdateError('redirect','下载重定向过多')
        if progress:progress({'stage':'verify','completed':count,'total':asset['size'],'unit':'bytes'})
        if count!=asset['size'] or digest.hexdigest()!=asset['sha256']:
            raise UpdateError('checksum','下载文件大小或SHA-256不匹配')
        return target
    except BaseException as error:
        target.unlink(missing_ok=True)
        if isinstance(error,(UpdateError,KeyboardInterrupt,SystemExit)):raise
        import asyncio
        if isinstance(error,asyncio.CancelledError):raise
        raise UpdateError('download','更新下载失败，请重试') from None
