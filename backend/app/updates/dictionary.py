"""Independent GTFS refresh; software bundles merge by data age, not APK age."""
from __future__ import annotations

from contextlib import closing
import csv
from datetime import datetime, timezone
import io
import itertools
import math
from pathlib import Path
import re
import sqlite3
import threading
import zipfile

from app.updates import github
from app.updates.dictionary_schema import SCHEMA

LOCK=threading.Lock()
LIMIT=32*1024*1024
EXPANDED_LIMIT=256*1024*1024
GTFS_TABLES=('g_stop','g_trip','g_stop_time')


def tag_key(tag):
    match=re.fullmatch(r'gtfs-(\d{8})-(\d{6})',str(tag or ''))
    if not match:return None
    try:datetime.strptime(''.join(match.groups()),'%Y%m%d%H%M%S')
    except ValueError:return None
    return ''.join(match.groups())


def local(path: Path) -> dict:
    if not path.exists():return {'available':False,'version':None,'pulled_at':None}
    try:
        with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True)) as conn:
            meta=dict(conn.execute('SELECT key,value FROM meta'))
            counts={name:conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in GTFS_TABLES}
        return {'available':True,'version':meta.get('gtfs_tag'),'pulled_at':meta.get('gtfs_pulled_at'),'counts':counts}
    except sqlite3.Error:raise github.UpdateError('local_invalid','本地词典格式无效，未覆盖原文件') from None


async def check(path: Path) -> dict:
    current=local(path);release=await github.metadata(github.DICT_REPO)
    if not isinstance(release,dict):raise github.UpdateError('metadata','词典发布信息格式无效')
    tag=release.get('tag_name');key=tag_key(tag)
    if not key or release.get('draft') or release.get('prerelease'):
        raise github.UpdateError('release','词典发布版本无效')
    raw=next((a for a in release.get('assets',[]) if a.get('name')=='output_gtfs.zip'),None)
    if not raw:raise github.UpdateError('asset_missing','词典发布缺少GTFS更新包')
    asset=github.asset_info(raw,github.DICT_REPO,tag,LIMIT)
    old=tag_key(current['version'])
    return {'component':'dictionary','status':'ok','current':current,'latest_version':tag,
            'update_available':not old or key>old,'asset':asset,
            'release_url':f'https://github.com/{github.DICT_REPO}/releases/tag/{tag}',
            'scope':'gtfs','preserved':['line_master','line_station','station_profile']}


def _replace_tables(conn,source,tables):
    for table in tables:
        columns=[r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
        if not columns:raise github.UpdateError('schema','词典缺少必要数据表')
        conn.execute(f'DELETE FROM {table}')
        values=source.execute(f'SELECT {",".join(columns)} FROM {table}')
        conn.executemany(f'INSERT INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',values)


def _number(value):
    if not value:return None
    number=float(value)
    if not math.isfinite(number):raise ValueError('nonfinite GTFS value')
    return number


def _validate(conn):
    if any(conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]==0 for t in GTFS_TABLES):
        raise github.UpdateError('empty_feed','词典更新包缺少必要记录')
    if conn.execute('SELECT COUNT(*) FROM g_trip WHERE is_dummy=0').fetchone()[0]==0:
        raise github.UpdateError('empty_feed','词典没有有效车次')
    if conn.execute('SELECT COUNT(*) FROM g_stop_time st LEFT JOIN g_trip t ON t.trip_id=st.trip_id LEFT JOIN g_stop s ON s.stop_id=st.stop_id WHERE t.trip_id IS NULL OR s.stop_id IS NULL').fetchone()[0]:
        raise github.UpdateError('references','词典存在无效车站或车次引用')
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':
        raise github.UpdateError('integrity','词典完整性检查失败')


def build_feed(archive: Path, output: Path, tag: str):
    if not tag_key(tag):raise github.UpdateError('release','词典版本无效')
    try:
        with zipfile.ZipFile(archive) as z, closing(sqlite3.connect(output)) as conn, conn:
            entries=z.infolist()
            if len(entries)>100 or sum(e.file_size for e in entries)>EXPANDED_LIMIT:
                raise github.UpdateError('expanded_size','词典解压大小超出范围')
            required=('stops.txt','trips.txt','stop_times.txt')
            if any(z.namelist().count(name)!=1 for name in required):
                raise github.UpdateError('feed','词典更新包缺少或重复GTFS文件')
            conn.executescript(SCHEMA)
            specs=[('stops.txt','g_stop',lambda r:(r['stop_id'],r['stop_name'],_number(r.get('stop_lat')),_number(r.get('stop_lon')))),
                   ('trips.txt','g_trip',lambda r:(r['trip_id'],r.get('route_id',''),r.get('trip_short_name',''),int(r['trip_id'].startswith('DUMMY')))),
                   ('stop_times.txt','g_stop_time',lambda r:(r['trip_id'],int(r['stop_sequence']),r['stop_id'],r.get('arrival_time',''),r.get('departure_time',''),_number(r.get('shape_dist_traveled'))))]
            for name,table,convert in specs:
                with z.open(name) as raw:
                    rows=(convert(r) for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig')))
                    count=4 if table in ('g_stop','g_trip') else 6
                    while batch:=list(itertools.islice(rows,1000)):
                        conn.executemany(f'INSERT INTO {table} VALUES({",".join("?" for _ in range(count))})',batch)
            _validate(conn)
            conn.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',
                             [('gtfs_tag',tag),('gtfs_pulled_at',datetime.now(timezone.utc).isoformat())])
    except github.UpdateError:raise
    except (sqlite3.Error,zipfile.BadZipFile,KeyError,ValueError,UnicodeError,RuntimeError,csv.Error,OSError,OverflowError,NotImplementedError):
        raise github.UpdateError('feed','词典更新包格式无效，原词典保持不变') from None


def merge(snapshot: Path, target: Path, *, bundled=False) -> dict:
    """Commit all replacements in one SQLite transaction; preserve local caches."""
    if not LOCK.acquire(blocking=False):raise github.UpdateError('busy','已有词典更新正在执行')
    try:
        with closing(sqlite3.connect(f'file:{snapshot}?mode=ro',uri=True)) as source:
            _validate(source);new_meta=dict(source.execute('SELECT key,value FROM meta'))
            if not tag_key(new_meta.get('gtfs_tag')):raise github.UpdateError('release','包内词典版本无效')
            target.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(target,timeout=10)) as conn, conn:
                conn.executescript("BEGIN IMMEDIATE;\n"+SCHEMA)
                old_meta=dict(conn.execute('SELECT key,value FROM meta'))
                tables=[];meta=[]
                if (tag_key(old_meta.get('gtfs_tag')) or '')<tag_key(new_meta['gtfs_tag']):
                    tables.extend(GTFS_TABLES);meta.extend((k,new_meta[k]) for k in ('gtfs_tag','gtfs_pulled_at') if k in new_meta)
                # A software bundle may also refresh its line summary, never local detail caches.
                if bundled and new_meta.get('lines_pulled_at','')>old_meta.get('lines_pulled_at',''):
                    tables.append('line_master');meta.append(('lines_pulled_at',new_meta['lines_pulled_at']))
                if bundled and new_meta.get('published_fares_pulled_at','') > old_meta.get('published_fares_pulled_at',''):
                    exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='published_fare'").fetchone()
                    if exists:
                        tables.append('published_fare')
                        meta.append(('published_fares_pulled_at',new_meta['published_fares_pulled_at']))
                if not tables:return {'status':'unchanged','current':local(target)}
                backup=target.with_name(target.stem+'.update-backup.db')
                with closing(sqlite3.connect(f'file:{target}?mode=ro',uri=True)) as prior, closing(sqlite3.connect(backup)) as saved:
                    prior.backup(saved)
                _replace_tables(conn,source,tables)
                conn.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',meta)
                _validate(conn)
        return {'status':'updated','current':local(target),'updated_tables':tables}
    except github.UpdateError:raise
    except sqlite3.Error:raise github.UpdateError('database','词典提交失败，数据库事务已回滚') from None
    finally:LOCK.release()


def sync_bundled(snapshot: Path, target: Path) -> dict:
    if not snapshot.is_file():return {'status':'not_bundled'}
    if snapshot.resolve()==target.resolve():raise github.UpdateError('path','包内词典与运行词典必须分开存放')
    return merge(snapshot,target,bundled=True)


async def run_io(function,*args):
    """Drain a local worker before callers remove its files, including cancellation."""
    import asyncio
    task=asyncio.create_task(asyncio.to_thread(function,*args))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task,return_exceptions=True)
        raise
