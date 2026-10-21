#!/usr/bin/env python3
"""一份带 release-config 的 Markdown 驱动 Main 正式发布；不保存凭据。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler
import zipfile

ROOT = Path(__file__).resolve().parents[2]
ENV = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}


def run(*args, capture=True, env=None):
    result = subprocess.run(args, cwd=ROOT, env=env or ENV, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        # Git/build 命令不带凭据参数，错误输出只打印末段。
        raise RuntimeError(f'命令失败：{args[0]} {args[1]}\n{(result.stderr or result.stdout or "请查看构建日志")[-1600:]}')
    return (result.stdout or '').strip()


def git(*args):
    return run('git', '-c', 'http.sslVersion=tlsv1.2', *args)


def digest(path):
    with Path(path).open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    return value


class GitHub:
    def __init__(self):
        self.token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if not self.token:
            result = subprocess.run(['git', 'credential', 'fill'], cwd=ROOT, env=ENV,
                                    input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True)
            credentials = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            self.token = credentials.get('password')
        if not self.token:
            raise RuntimeError('找不到 GitHub 凭据；请配置 Git credential helper 或临时 GH_TOKEN，不要写入发布文件。')
        context = ssl.create_default_context()
        # 此机器网络的兼容路径，仍校验系统 CA 和主机名，不关闭 TLS 验证。
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))

    def request(self, method, url, data=None, content_type='application/json', allow404=False):
        if not url.startswith('https://'):
            raise RuntimeError('GitHub 地址必须使用 HTTPS')
        if urlparse(url).hostname not in {'api.github.com', 'uploads.github.com'}:
            raise RuntimeError('拒绝向非 GitHub API 地址发送认证信息')
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode()
        for attempt in range(3):
            request = Request(url, data=data, method=method, headers={
                'Accept': 'application/vnd.github+json', 'User-Agent': 'OpenRailFanAI-release-script',
                'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': content_type})
            request.add_unredirected_header('Authorization', 'Bearer ' + self.token)
            try:
                with self.opener.open(request, timeout=180) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except HTTPError as error:
                if error.code == 404 and allow404:
                    return None
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f'GitHub 操作失败：HTTP {error.code}，未输出响应中的敏感内容') from None
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RuntimeError('GitHub 网络请求失败；进度已保留，可用同一文件重试') from None
            print('网络暂不可用，正在重试…', flush=True)
            time.sleep(2 ** attempt)


def clean():
    if git('status', '--porcelain'):
        raise RuntimeError('工作区存在未提交改动；停止以免包与源码脱节')


def safe_stage():
    git('add', '-A')
    names = git('diff', '--cached', '--name-only', '-z').split('\0')
    for name in filter(None, names):
        path = Path(name)
        if path.name in {'.env', '.env.local', 'keystore.properties'} or path.suffix in {'.jks', '.keystore', '.pem', '.key', '.apk'}:
            raise RuntimeError(f'暂存区包含不应公开的文件：{name}')
        if not (ROOT / path).is_file() or (ROOT / path).stat().st_size > 2_000_000:
            continue
        raw = (ROOT / path).read_bytes()
        if b'\0' in raw:
            continue
        if re.search(rb'ghp_[A-Za-z0-9]{36}\b|github_pat_[A-Za-z0-9_]{60,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', raw):
            raise RuntimeError(f'文件疑似含真实凭据，停止：{name}')
    git('diff', '--cached', '--check')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_file', type=Path)
    args = parser.parse_args()
    release_file = args.release_file.resolve()
    release_file.relative_to(ROOT)
    text = release_file.read_text()
    match = re.match(r'\s*<!--\s*release-config\s*(\{.*?\})\s*-->\s*', text, re.S)
    if not match:
        raise RuntimeError('发布文件首部缺少 release-config JSON 注释')
    config = json.loads(match.group(1)); notes = text[match.end():].strip()
    version = config['version']; previous = config['previous_version']
    def number(v):
        if not re.fullmatch(r'\d+\.\d+\.\d+', v):
            raise RuntimeError('版本必须为三段数字')
        return tuple(map(int, v.split('.')))
    if number(version) <= number(previous):
        raise RuntimeError('新版本必须高于 previous_version')
    repo = config['repository']; target = config.get('target_branch', 'main')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise RuntimeError('仓库名称不合法')
    branch = git('branch', '--show-current')
    if not branch:
        raise RuntimeError('不在分支上，停止发布')
    if git('remote', 'get-url', 'origin') not in {f'https://github.com/{repo}.git', f'https://github.com/{repo}', f'git@github.com:{repo}.git'}:
        raise RuntimeError('origin 与发布仓库不一致')
    if config.get('include_dictionary') is not True:
        raise RuntimeError('Main 公开发布必须包含离线词典')
    tag = 'v' + version
    directory = ROOT / '.ai/releases' / tag
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    manifest_hash = hashlib.sha256(text.encode()).hexdigest()
    if state and state.get('manifest_hash') != manifest_hash:
        raise RuntimeError('已开始发布的说明文件发生变化，停止；请先核对原发布状态')
    def save(**values):
        state.update(values)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(state_path)
    save(manifest_hash=manifest_hash, version=version)
    api = GitHub(); base = f'https://api.github.com/repos/{repo}'
    metadata = api.request('GET', base)
    if not metadata.get('permissions', {}).get('push'):
        raise RuntimeError('当前 GitHub 凭据不具备此仓库写入权限')
    print('[1/7] 核对分支、版本与源码', flush=True)
    git('fetch', 'origin', target, branch)
    source = state.get('source_commit')
    if not source:
        git('merge-base', '--is-ancestor', f'origin/{target}', 'HEAD')
        git('merge-base', '--is-ancestor', f'origin/{branch}', 'HEAD')
        current = (ROOT / 'VERSION').read_text().strip()
        if current not in {previous, version}:
            raise RuntimeError('本地 VERSION 与发布配置不匹配')
        (ROOT / 'VERSION').write_text(version + '\n')
        safe_stage()
        if git('diff', '--cached', '--name-only'):
            git('commit', '-m', f'release: prepare Main {version}')
        source = git('rev-parse', 'HEAD'); save(source_commit=source, source_branch=branch)
    elif state.get('source_branch') != branch:
        raise RuntimeError('续传必须在开始发布的同一分支进行')
    clean()
    if not state.get('published') and git('rev-parse', 'HEAD') != source:
        raise RuntimeError('待发布源码 HEAD 已变化，停止，不自动重建其它提交')
    existing_tag = git('tag', '--list', tag)
    if existing_tag:
        if git('rev-list', '-n', '1', tag) != source:
            raise RuntimeError('同名标签已指向其它源码')
    else:
        git('tag', tag, source)
    apk = ROOT / 'dist/android' / f'OpenRailFanAI-{version}-arm64-release.apk'
    if not (state.get('apk_sha256') and apk.is_file() and digest(apk) == state['apk_sha256']):
        if state.get('published'):
            raise RuntimeError('已发布包在本机缺失或不一致，禁止覆盖公开资产')
        print('[2/7] 构建包含词典的正式签名包', flush=True)
        run('bash', 'scripts/android/build.sh', '-PincludeDict=true', 'assembleRelease', capture=False)
    print('[3/7] 核对签名、包内版本、构建提交和词典', flush=True)
    sdk = ROOT / '.android-build/sdk/build-tools/35.0.0'
    jdk = ROOT / '.android-build/jdk17/Contents/Home'
    env = {**ENV, 'JAVA_HOME': str(jdk), 'PATH': str(jdk / 'bin') + ':' + ENV['PATH']}
    signature = run(str(sdk / 'apksigner'), 'verify', '--print-certs', str(apk), env=env)
    certificate = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)', signature)
    prior = json.loads((ROOT / f'docs/verification/release-{previous}.json').read_text())
    if not certificate or certificate.group(1).lower() != prior['signing_certificate_sha256'].lower():
        raise RuntimeError('签名不匹配上一正式版本，停止发布')
    badging = run(str(sdk / 'aapt'), 'dump', 'badging', str(apk), env=env)
    if f"versionName='{version}'" not in badging or "name='org.openrailfanai.app'" not in badging or 'application-debuggable' in badging:
        raise RuntimeError('APK 版本、包名或正式构建属性不正确')
    with zipfile.ZipFile(apk) as archive:
        build = json.loads(archive.read('assets/webapp/build.json'))
        if build.get('version') != version or '-dirty' in build.get('commit', '') or not source.startswith(build.get('commit') or '?'):
            raise RuntimeError('APK 不是由指定干净源码构建')
        dictionary_bytes = archive.getinfo('assets/dict/dict.db').file_size
        if dictionary_bytes < 100000:
            raise RuntimeError('包内词典异常')
    clean()
    checksum = digest(apk); sidecar = apk.with_suffix('.apk.sha256')
    sidecar.write_text(checksum + '  ' + apk.name + '\n')
    save(apk_sha256=checksum)
    print('[4/7] 推送源码、main 和版本标签（不强制覆盖）', flush=True)
    refs = [f'HEAD:refs/heads/{b}' for b in dict.fromkeys([branch, target])]
    git('push', '--atomic', 'origin', *refs, f'refs/tags/{tag}')
    print('[5/7] 创建或恢复草稿，上传安装包及校验文件', flush=True)
    release = api.request('GET', base + '/releases/tags/' + tag, allow404=True)
    if release is None:
        release = api.request('POST', base + '/releases', {'tag_name': tag, 'target_commitish': source,
            'name': config.get('title', f'Main {version}'), 'body': notes, 'draft': True, 'prerelease': False})
    if release.get('target_commitish') not in {source, branch, target}:
        raise RuntimeError('现有 Release 的源码目标不一致')
    if release.get('body', '').strip() != notes:
        raise RuntimeError('同名 Release 的说明不同，拒绝覆盖')
    owned = state.get('release_id') == release['id']
    save(release_id=release['id'])
    assets = []
    for path in (apk, sidecar):
        expected = 'sha256:' + digest(path)
        asset = next((a for a in release.get('assets', []) if a.get('name') == path.name), None)
        if asset and (asset.get('digest') != expected or asset.get('size') != path.stat().st_size):
            if not release.get('draft') or not owned:
                raise RuntimeError('远端同名资产摘要不同，拒绝覆盖')
            api.request('DELETE', base + f'/releases/assets/{asset["id"]}'); asset = None
        if asset is None:
            print(f'上传 {path.name}（{path.stat().st_size / 1024 / 1024:.1f} MiB）…', flush=True)
            url = release['upload_url'].split('{')[0] + '?name=' + quote(path.name)
            asset = api.request('POST', url, path.read_bytes(), 'application/vnd.android.package-archive' if path == apk else 'text/plain')
        if asset.get('digest') != expected or asset.get('size') != path.stat().st_size:
            raise RuntimeError('GitHub 资产摘要或大小与本地不一致，保持草稿')
        assets.append({k: asset[k] for k in ('name', 'size', 'digest', 'browser_download_url')})
    current_latest = api.request('GET', base + '/releases/latest', allow404=True)
    if current_latest and number(current_latest.get('tag_name', '').removeprefix('v')) > number(version):
        raise RuntimeError('已有更高正式版本，拒绝把旧版设为 latest')
    print('[6/7] 校验完成，公开 Release', flush=True)
    release = api.request('PATCH', base + f'/releases/{release["id"]}', {'draft': False, 'make_latest': 'true'})
    save(published=True)
    latest = api.request('GET', base + '/releases/latest')
    if latest.get('tag_name') != tag:
        raise RuntimeError('Release 已发布，但 latest 尚未指向本版本，请核对后重试')
    # Draft 上传阶段的资产 URL 是 untagged-<hash>；公开后必须重新取一次发布态资产，
    # 否则回执里会留下 untagged URL（0.1.30/0.1.31 已出现）。
    published = api.request('GET', base + '/releases/tags/' + tag)
    published_assets = []
    for path in (apk, sidecar):
        asset = next((a for a in published.get('assets', []) if a.get('name') == path.name), None)
        expected = 'sha256:' + digest(path)
        if not asset or asset.get('digest') != expected or asset.get('size') != path.stat().st_size:
            raise RuntimeError('公开 Release 的资产摘要或大小与本地不一致')
        url = asset.get('browser_download_url', '')
        if f'/releases/download/{tag}/' not in url:
            raise RuntimeError('公开 Release 的资产 URL 不是版本标签路径：' + url)
        published_assets.append({k: asset[k] for k in ('name', 'size', 'digest', 'browser_download_url')})
    assets = published_assets
    release = published
    receipt = {'url': release['html_url'], 'id': release['id'], 'tag': tag, 'latest': latest['tag_name'],
        'source_commit': source, 'build': build, 'local_size': apk.stat().st_size, 'local_sha256': checksum,
        'signing_certificate_sha256': certificate.group(1).lower(), 'dictionary_bytes': dictionary_bytes,
        'assets': assets, 'verified_at': datetime.now().astimezone().isoformat(),
        'validation': config.get('validation', '构建及发布产物核验；未自动执行产品测试或真机验收')}
    print('[7/7] 保存发布回执并同步分支', flush=True)
    receipt_path = ROOT / f'docs/verification/release-{version}.json'
    if not state.get('receipt_commit'):
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        git('add', str(receipt_path.relative_to(ROOT)))
        if git('diff', '--cached', '--name-only'):
            git('commit', '-m', f'docs: record Main {version} publication')
        save(receipt_commit=git('rev-parse', 'HEAD'))
    git('push', '--atomic', 'origin', *refs)
    clean()
    print('发布完成：' + release['html_url'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('发布已停止：' + str(error), file=sys.stderr)
        sys.exit(1)
