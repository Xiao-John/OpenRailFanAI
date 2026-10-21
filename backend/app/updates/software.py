"""Software update checks never install files or overwrite the running backend."""
import re
from app.updates import github

LIMIT = 512 * 1024 * 1024


def version(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value or ''))
    return tuple(map(int,match.groups())) if match else None


async def check(current: str, abi='arm64') -> dict:
    abi={'arm64-v8a':'arm64'}.get(abi,abi)
    if abi not in ('arm64','armeabi-v7a','x86_64'):raise github.UpdateError('abi','不支持的处理器架构')
    releases=await github.metadata(github.APP_REPO,'releases?per_page=100')
    if not isinstance(releases,list):raise github.UpdateError('metadata','软件发布信息格式无效')
    valid=[r for r in releases if isinstance(r,dict) and not r.get('draft') and not r.get('prerelease') and version(r.get('tag_name'))]
    if not valid:raise github.UpdateError('not_published','尚无可用的正式软件版本')
    # 缺少本架构安装包的较新发布不应遮挡仍可下载的正式版本。
    asset = None
    release = max(valid, key=lambda r: version(r['tag_name']))
    for candidate in sorted(valid, key=lambda r: version(r['tag_name']), reverse=True):
        tag = candidate['tag_name']
        latest = tag.removeprefix('v')
        name = f'OpenRailFanAI-{latest}-{abi}-release.apk'
        raw = next((a for a in candidate.get('assets', []) if isinstance(a, dict) and a.get('name') == name), None)
        if raw is not None:
            # 存在但校验不合法时必须报错，不能用旧版掩盖完整性问题。
            asset = github.asset_info(raw, github.APP_REPO, tag, LIMIT)
            release = candidate
            break
    tag = release['tag_name']
    latest = tag.removeprefix('v')
    known=version(current)
    return {'component':'software','status':'ok','current_version':current,'latest_version':latest,
            'update_available':(version(latest)>known if known else None) if asset else False,'comparison':'known' if known else 'unknown',
            'release_url':f'https://github.com/{github.APP_REPO}/releases/tag/{tag}',
            'published_at':release.get('published_at'),'notes':release.get('body') or '',
            'asset':asset,'download_supported':asset is not None,'installation':'android_system_installer',
            'dictionary_policy':'merge_newer_bundled_snapshot'}
