"""Offline updater tests with real SQLite transactions and HTTP protocol boundaries."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi import FastAPI
from app.updates import dictionary as D,github as G,software as S
from app.api.dictionary_updates import router as dict_router
from app.api.software_updates import router as app_router
from app.updates.dictionary_schema import SCHEMA

TAG='gtfs-20261004-052300'
OLD='gtfs-20260913-040340'


def feed(broken=False):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('stops.txt','stop_id,stop_name,stop_lat,stop_lon\nA,北京南,39,116\n')
        z.writestr('trips.txt','trip_id,route_id,trip_short_name\nG1,R,G1\n')
        z.writestr('stop_times.txt',f'trip_id,stop_sequence,stop_id,arrival_time,departure_time,shape_dist_traveled\nG1,1,{"B" if broken else "A"},06:30,06:30,0\n')
    return out.getvalue()


def asset(blob,repo,tag,name):
    return dict(id=1,name=name,size=len(blob),digest='sha256:'+hashlib.sha256(blob).hexdigest(),
                browser_download_url=f'https://github.com/{repo}/releases/download/{tag}/{name}')


def release(repo,tag,blob,name):
    return dict(tag_name=tag,assets=[asset(blob,repo,tag,name)],draft=False,prerelease=False,body='发布说明')


def database(path,tag=OLD):
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA)
        c.execute('INSERT INTO meta VALUES(?,?)',('gtfs_tag',tag))
        c.execute('INSERT INTO meta VALUES(?,?)',('gtfs_pulled_at','2026-09-15T00:00:00+00:00'))
        c.execute('INSERT INTO g_stop VALUES(?,?,?,?)',('A','旧站',39,116))
        c.execute('INSERT INTO g_trip VALUES(?,?,?,?)',('G1','R','G1',0))
        c.execute('INSERT INTO g_stop_time VALUES(?,?,?,?,?,?)',('G1',1,'A','06:00','06:00',0))
        c.execute('INSERT INTO station_profile(station,telecode) VALUES(?,?)',('保留站','AAA'))
        c.execute('INSERT INTO line_station(line,seq,station) VALUES(?,?,?)',('保留线',1,'保留站'))
        c.execute('INSERT INTO line_master(line) VALUES(?)',('旧线路',))


class DictionaryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.target=self.root/'dict.db';database(self.target)
        self.archive=self.root/'feed.zip';self.archive.write_bytes(feed());self.snapshot=self.root/'new.db'
        D.build_feed(self.archive,self.snapshot,TAG)

    def test_atomic_update_preserves_caches_and_old_reader(self):
        old=sqlite3.connect(f'file:{self.target}?mode=ro',uri=True)
        self.addCleanup(old.close)
        self.assertEqual(old.execute('SELECT name FROM g_stop').fetchone()[0],'旧站')
        result=D.merge(self.snapshot,self.target)
        self.assertEqual(result['status'],'updated');self.assertEqual(D.local(self.target)['version'],TAG)
        self.assertEqual(old.execute('SELECT name FROM g_stop').fetchone()[0],'北京南')
        self.assertEqual(old.execute('SELECT telecode FROM station_profile').fetchone()[0],'AAA')
        self.assertEqual(old.execute('SELECT line FROM line_station').fetchone()[0],'保留线')
        self.assertEqual(old.execute('SELECT line FROM line_master').fetchone()[0],'旧线路')
        self.assertTrue(self.target.with_name('dict.update-backup.db').exists())

    def test_invalid_feed_keeps_original(self):
        original=self.target.read_bytes();self.archive.write_bytes(feed(True))
        with self.assertRaises(G.UpdateError):D.build_feed(self.archive,self.root/'bad.db',TAG)
        self.assertEqual(self.target.read_bytes(),original)

    def test_commit_failure_rolls_back_all_tables(self):
        original=D.local(self.target)
        with patch.object(D,'_replace_tables',side_effect=sqlite3.OperationalError('failure')):
            with self.assertRaises(G.UpdateError):D.merge(self.snapshot,self.target)
        self.assertEqual(D.local(self.target),original)
        with sqlite3.connect(self.target) as c:self.assertEqual(c.execute('SELECT name FROM g_stop').fetchone()[0],'旧站')

    def test_bundle_merges_newer_and_never_downgrades(self):
        self.assertEqual(D.sync_bundled(self.snapshot,self.target)['status'],'updated')
        older=self.root/'old.db';database(older,OLD)
        self.assertEqual(D.sync_bundled(older,self.target)['status'],'unchanged')
        self.assertEqual(D.local(self.target)['version'],TAG)
        self.assertEqual(D.sync_bundled(self.root/'absent',self.target)['status'],'not_bundled')

    def test_bundle_updates_line_summary_independently(self):
        with sqlite3.connect(self.snapshot) as c:
            c.execute('UPDATE meta SET value=? WHERE key=?',(OLD,'gtfs_tag'))
            c.execute('INSERT INTO meta VALUES(?,?)',('lines_pulled_at','2026-10-06T00:00:00+00:00'))
            c.execute('INSERT INTO line_master(line) VALUES(?)',('新线路',))
        result=D.sync_bundled(self.snapshot,self.target)
        self.assertEqual(result['updated_tables'],['line_master'])
        with sqlite3.connect(self.target) as c:
            self.assertEqual(c.execute('SELECT line FROM line_master').fetchone()[0],'新线路')
            self.assertEqual(c.execute('SELECT station FROM line_station').fetchone()[0],'保留站')

    def test_version_check_waits_for_database_write_lock(self):
        import concurrent.futures,threading
        owner=sqlite3.connect(self.target)
        owner.execute('BEGIN IMMEDIATE')
        owner.execute('UPDATE meta SET value=? WHERE key=?',('gtfs-20261005-052300','gtfs_tag'))
        ready=threading.Event();validate=D._validate
        def observed(conn):
            validate(conn);ready.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool,patch.object(D,'_validate',observed):
            job=pool.submit(D.merge,self.snapshot,self.target)
            self.assertTrue(ready.wait(1));owner.commit();owner.close()
            self.assertEqual(job.result(timeout=3)['status'],'unchanged')
        self.assertEqual(D.local(self.target)['version'],'gtfs-20261005-052300')

    def test_new_install_and_zip_limit(self):
        fresh=self.root/'fresh.db'
        self.assertEqual(D.sync_bundled(self.snapshot,fresh)['status'],'updated')
        self.assertEqual(D.local(fresh)['version'],TAG)
        with patch.object(D,'EXPANDED_LIMIT',1):
            with self.assertRaises(G.UpdateError):D.build_feed(self.archive,self.root/'large.db',TAG)


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.target=self.root/'dict.db';database(self.target)
        self.apk=b'fixture APK';self.zip=feed()
        self.apprelease=release(G.APP_REPO,'v0.1.18',self.apk,'OpenRailFanAI-0.1.18-arm64-release.apk')
        self.dictrelease=release(G.DICT_REPO,TAG,self.zip,'output_gtfs.zip')
        self.requests=[];self.failure=None
        async def handler(request):
            self.requests.append(request)
            if self.failure:return httpx.Response(self.failure)
            if request.url.host=='api.github.com':
                return httpx.Response(200,json=[self.apprelease] if G.APP_REPO in str(request.url) else self.dictrelease)
            if request.url.host=='github.com':return httpx.Response(302,headers={'location':'https://release-assets.githubusercontent.com/file'})
            return httpx.Response(200,content=self.downloading)
        original=httpx.AsyncClient
        transport=httpx.MockTransport(handler)
        p=patch.object(G.httpx,'AsyncClient',lambda **kwargs:original(transport=transport,**kwargs));p.start();self.addCleanup(p.stop)
        self.original=original

    async def test_software_startup_merges_bundled_dictionary(self):
        from app import main
        from types import SimpleNamespace
        archive=self.root/'bundle.zip';archive.write_bytes(self.zip)
        snapshot=self.root/'bundle.db';D.build_feed(archive,snapshot,TAG)
        with patch.object(main,'settings',SimpleNamespace(dict_bundled_db_path=str(snapshot))), \
             patch('app.data.dict.db_path',return_value=self.target),patch.dict(os.environ,APP_VARIANT='main'):
            async with main._lifespan(main.app):
                self.assertEqual(D.local(self.target)['version'],TAG)

    async def test_local_worker_is_drained_before_cancel_returns(self):
        import threading
        started=threading.Event();release=threading.Event();finished=threading.Event()
        def worker():
            started.set();release.wait(2);finished.set()
        task=asyncio.create_task(D.run_io(worker))
        await asyncio.to_thread(started.wait,1)
        task.cancel();await asyncio.sleep(0)
        self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(finished.is_set())

    async def test_download_cancellation_cleans_partial_file(self):
        entered=asyncio.Event()
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'a';entered.set();await asyncio.Event().wait()
        async def handler(request):return httpx.Response(200,stream=Slow())
        original=self.original
        with patch.object(G.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            info=G.asset_info(self.apprelease['assets'][0],G.APP_REPO,'v0.1.18',S.LIMIT)
            task=asyncio.create_task(G.download(info,self.root))
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(list(self.root.glob('.update-*')))

    async def test_software_version_selection_and_unknown_current(self):
        values=[self.apprelease,dict(self.apprelease,tag_name='v0.1.100',prerelease=True),dict(self.apprelease,tag_name='v0.1.6-debug')]
        with patch.object(G,'metadata',return_value=values):
            self.assertTrue((await S.check('0.1.9'))['update_available'])
            self.assertIsNotNone((await S.check('0.1.9','arm64-v8a'))['asset'])
            self.assertFalse((await S.check('0.1.18'))['update_available'])
            self.assertFalse((await S.check('0.1.19'))['update_available'])
            self.assertIsNone((await S.check('dev'))['update_available'])
            self.assertIsNone((await S.check('0.1.9','x86_64'))['asset'])

    async def test_download_size_checksum_redirect_and_cleanup(self):
        self.downloading=self.apk;info=G.asset_info(self.apprelease['assets'][0],G.APP_REPO,'v0.1.18',S.LIMIT)
        path=await G.download(info,self.root);self.assertEqual(path.read_bytes(),self.apk);path.unlink()
        self.downloading=b'corrupted'
        with self.assertRaises(G.UpdateError):await G.download(info,self.root)
        self.assertFalse(list(self.root.glob('.update-*')))
        for url in ['http://github.com/file','https://127.0.0.1/file','https://github.com.evil/file']:
            with self.assertRaises(G.UpdateError):G.validate_url(url,asset=True)
        with self.assertRaises(G.UpdateError):G.asset_info(dict(self.apprelease['assets'][0],digest=None),G.APP_REPO,'v0.1.18',S.LIMIT)

    async def test_errors_are_not_no_update(self):
        for status,code in [(404,'not_published'),(403,'rate_limited'),(429,'rate_limited'),(500,'network')]:
            self.failure=status
            with self.assertRaises(G.UpdateError) as caught:await S.check('0.1.9')
            self.assertEqual(caught.exception.code,code)

    async def test_apis_download_apply_and_cross_site_guard(self):
        app=FastAPI();app.include_router(app_router,prefix='/api');app.include_router(dict_router,prefix='/api')
        with patch('app.api.dictionary_updates.data.db_path',return_value=self.target):
            async with self.original(transport=httpx.ASGITransport(app,client=('127.0.0.1',1)),base_url='http://localhost') as client:
                self.downloading=self.apk
                check=await client.get('/api/updates/software',params={'current_version':'0.1.9'})
                self.assertEqual(check.status_code,200);self.assertTrue(check.json()['update_available'])
                package=await client.post('/api/updates/software/download',json={'current_version':'0.1.9','latest_version':'0.1.18'})
                self.assertEqual(package.content,self.apk);self.assertEqual(package.headers['x-content-sha256'],hashlib.sha256(self.apk).hexdigest())
                changed=await client.post('/api/updates/software/download',json={'current_version':'0.1.9','latest_version':'0.1.17'})
                self.assertEqual(changed.status_code,409)
                checked=await client.get('/api/updates/dictionary');self.assertTrue(checked.json()['update_available'])
                forbidden=await client.post('/api/updates/dictionary/apply',json={'latest_version':TAG},headers={'Origin':'https://evil.example'})
                self.assertEqual(forbidden.status_code,403)
                self.downloading=self.zip
                applied=await client.post('/api/updates/dictionary/apply',json={'latest_version':TAG})
                self.assertEqual(applied.status_code,200);self.assertEqual(applied.json()['status'],'updated')
                unchanged=await client.post('/api/updates/dictionary/apply',json={'latest_version':TAG})
                self.assertEqual(unchanged.json()['status'],'unchanged')
                self.assertEqual((await client.get('/api/updates/dictionary/local')).json()['current']['version'],TAG)


if __name__=='__main__':unittest.main()
