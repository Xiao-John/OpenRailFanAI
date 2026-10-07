"""Photo snapshots survive independent updates and older software bundles."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.data import photo_spots as P
from app.updates import dictionary as D, github as G
from test_github_updates import database, OLD

NEW='gtfs-20261007-040340'


def photo(path, version, url='https://example.org/spot', legacy=False):
    with sqlite3.connect(path) as c:
        c.execute('INSERT INTO photo_spot_doc(url,scope,page_text,fetched_at) VALUES(?,?,?,?)',
                  (url,'黄渡','天桥拍车攻略',version))
        c.execute('INSERT INTO photo_spot_seed(query,ran_at) VALUES(?,?)',('黄渡机位',version))
        if not legacy:
            c.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',
                          [(P.VERSION_KEY,version),(P.SCHEMA_KEY,'1')])


class PhotoUpdates(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.target=Path(tmp.name)/'local.db';self.bundle=Path(tmp.name)/'bundle.db'
        database(self.target);database(self.bundle)

    def rows(self):
        with sqlite3.connect(self.target) as c:
            return list(c.execute('SELECT url,page_text FROM photo_spot_doc'))

    def test_old_install_adds_photo_without_changing_gtfs_or_caches(self):
        with sqlite3.connect(self.target) as c:
            for t in P.TABLES:c.execute(f'DROP TABLE {t}')
        photo(self.bundle,'2026-10-07T08:00:00Z')
        result=D.sync_bundled(self.bundle,self.target)
        self.assertEqual(result['updated_tables'],list(P.TABLES))
        self.assertEqual(result['current']['version'],OLD)
        self.assertEqual(result['current']['photo_spots']['documents'],1)
        with sqlite3.connect(self.target) as c:
            self.assertEqual(c.execute('SELECT station FROM station_profile').fetchone()[0],'保留站')

    def test_newer_local_preserved_with_normalized_timezone(self):
        photo(self.target,'2026-10-07T17:00:00+08:00',url='local')
        photo(self.bundle,'2026-10-07T08:00:00Z')
        self.assertEqual(D.sync_bundled(self.bundle,self.target)['status'],'unchanged')
        self.assertEqual(self.rows()[0][0],'local')

    def test_legacy_local_without_version_is_not_downgraded(self):
        photo(self.target,'2026-10-08T00:00:00Z',url='local',legacy=True)
        photo(self.bundle,'2026-10-07T08:00:00Z')
        self.assertEqual(D.sync_bundled(self.bundle,self.target)['status'],'unchanged')
        self.assertEqual(self.rows()[0][0],'local')

    def test_legacy_source_without_hash_is_migrated(self):
        photo(self.bundle,'2026-10-07T08:00:00Z',legacy=True)
        with sqlite3.connect(self.bundle) as c:c.execute('ALTER TABLE photo_spot_doc DROP COLUMN content_hash')
        self.assertEqual(D.sync_bundled(self.bundle,self.target)['status'],'updated')
        with sqlite3.connect(self.target) as c:
            self.assertIsNone(c.execute('SELECT content_hash FROM photo_spot_doc').fetchone()[0])

    def test_old_local_hash_column_is_added_without_losing_rows(self):
        photo(self.target,'2026-10-08T00:00:00Z',url='local',legacy=True)
        with sqlite3.connect(self.target) as c:c.execute('ALTER TABLE photo_spot_doc DROP COLUMN content_hash')
        photo(self.bundle,'2026-10-07T08:00:00Z')
        self.assertEqual(D.sync_bundled(self.bundle,self.target)['status'],'unchanged')
        self.assertEqual(self.rows()[0][0],'local')
        with sqlite3.connect(self.target) as c:
            self.assertIn('content_hash',{r[1] for r in c.execute('PRAGMA table_info(photo_spot_doc)')})

    def test_gtfs_refresh_preserves_all_photo_rows_and_version(self):
        photo(self.target,'2026-10-07T08:00:00Z',url='local')
        with sqlite3.connect(self.bundle) as c:c.execute("UPDATE meta SET value=? WHERE key='gtfs_tag'",(NEW,))
        result=D.merge(self.bundle,self.target)
        self.assertEqual(result['updated_tables'],list(D.GTFS_TABLES))
        self.assertEqual(self.rows()[0][0],'local')
        self.assertEqual(result['current']['photo_spots']['version'],'2026-10-07T08:00:00Z')

    def test_old_bundle_without_photo_does_not_clear_photo(self):
        photo(self.target,'2026-10-07T08:00:00Z',url='local')
        with sqlite3.connect(self.bundle) as c:
            for t in P.TABLES:c.execute(f'DROP TABLE {t}')
        self.assertEqual(D.sync_bundled(self.bundle,self.target)['status'],'unchanged')
        self.assertEqual(self.rows()[0][0],'local')

    def test_newer_full_snapshot_applies_deletions(self):
        photo(self.target,'2026-10-07T08:00:00Z',url='removed')
        with sqlite3.connect(self.bundle) as c:
            c.executemany('INSERT INTO meta VALUES(?,?)',
                          [(P.VERSION_KEY,'2026-10-08T00:00:00Z'),(P.SCHEMA_KEY,'1')])
        self.assertEqual(D.sync_bundled(self.bundle,self.target)['status'],'updated')
        self.assertEqual(self.rows(),[])

    def test_failure_rolls_back_photo_and_gtfs(self):
        photo(self.target,'2026-10-06T08:00:00Z',url='local')
        photo(self.bundle,'2026-10-07T08:00:00Z')
        before=D.local(self.target)
        original=D._replace_tables
        def fail(conn,source,tables):
            original(conn,source,tables)
            raise sqlite3.OperationalError('injected failure after writes')
        with patch.object(D,'_replace_tables',side_effect=fail),self.assertRaises(G.UpdateError):
            D.sync_bundled(self.bundle,self.target)
        self.assertEqual(D.local(self.target),before)
        self.assertEqual(self.rows()[0][0],'local')

    def test_unsupported_schema_and_invalid_version_preserve_data(self):
        photo(self.bundle,'2026-10-07T08:00:00Z')
        for key,value in [(P.SCHEMA_KEY,'2'),(P.VERSION_KEY,'invalid')]:
            with sqlite3.connect(self.bundle) as c:
                c.execute('UPDATE meta SET value=? WHERE key=?',(value,key))
            with self.assertRaises(G.UpdateError):D.sync_bundled(self.bundle,self.target)
            self.assertEqual(self.rows(),[])
            with sqlite3.connect(self.bundle) as c:
                c.execute('UPDATE meta SET value=? WHERE key=?',('1' if key==P.SCHEMA_KEY else '2026-10-07T08:00:00Z',key))

    def test_missing_required_column_does_not_erase_existing_data(self):
        photo(self.target,'2026-10-06T08:00:00Z',url='local')
        photo(self.bundle,'2026-10-07T08:00:00Z')
        with sqlite3.connect(self.bundle) as c:c.execute('ALTER TABLE photo_spot_doc DROP COLUMN page_text')
        with self.assertRaises(G.UpdateError):D.sync_bundled(self.bundle,self.target)
        self.assertEqual(self.rows()[0][0],'local')

    def test_stamp_is_atomic_and_monotonic_including_deletions(self):
        photo(self.target,'2099-01-01T00:00:00Z')
        with sqlite3.connect(self.target) as c:
            c.execute('DELETE FROM photo_spot_doc')
            P.stamp(c)
        first=D.local(self.target)['photo_spots']['version']
        with sqlite3.connect(self.target) as c:P.stamp(c)
        self.assertGreater(P.utc(D.local(self.target)['photo_spots']['version']),P.utc(first))


if __name__=='__main__':unittest.main()
