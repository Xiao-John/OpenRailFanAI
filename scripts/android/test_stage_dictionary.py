"""Packaging checks: committed WAL, complete photo snapshot and atomic rejection."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('stage_dictionary', Path(__file__).with_name('stage-dictionary.py'))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class StageDictionaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.db'
        self.target = Path(self.temp.name) / 'assets' / 'dict.db'
        self.db = sqlite3.connect(self.source)
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE g_stop(id TEXT);
            CREATE TABLE g_trip(id TEXT);
            CREATE TABLE g_stop_time(id TEXT);
            CREATE TABLE photo_spot_doc(url TEXT,scope TEXT,content_hash TEXT,page_text TEXT);
            CREATE TABLE photo_spot_seed(query TEXT);
            INSERT INTO photo_spot_doc VALUES('fixture-url','夹具范围','fixture-hash','夹具正文');
            INSERT INTO photo_spot_seed VALUES('夹具发现记录');
            INSERT INTO meta VALUES('photo_spots_version','2026-10-07T07:59:19+00:00');
            INSERT INTO meta VALUES('photo_spots_schema_version','1');
        """)

    def test_committed_wal_and_metadata_survive_without_source_checkpoint(self):
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        main_bytes = self.source.read_bytes()
        self.db.execute("INSERT INTO photo_spot_doc VALUES('new-url','新范围','new-hash','新正文')")
        self.db.commit()
        self.assertEqual(self.source.read_bytes(), main_bytes)
        summary = stage.stage_dictionary(self.source, self.target, True)
        self.assertEqual(self.source.read_bytes(), main_bytes)
        self.assertEqual(summary['photo_spots']['documents'], 2)
        self.assertEqual(summary['photo_spots']['scopes'], 2)
        with sqlite3.connect(self.target) as snapshot:
            for table in ('meta', 'photo_spot_doc', 'photo_spot_seed'):
                self.assertEqual(snapshot.execute(f'SELECT * FROM {table}').fetchall(),
                                 self.db.execute(f'SELECT * FROM {table}').fetchall())

    def test_rejects_invalid_revision_without_replacing_previous_asset(self):
        stage.stage_dictionary(self.source, self.target, True)
        previous = self.target.read_bytes()
        self.db.execute("UPDATE meta SET value='not-a-date' WHERE key='photo_spots_version'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, '机位数据版本'):
            stage.stage_dictionary(self.source, self.target, True)
        self.assertEqual(self.target.read_bytes(), previous)
        self.assertEqual(list(self.target.parent.glob('dict-*.tmp')), [])

    def test_old_dictionary_allowed_only_without_release_requirement(self):
        self.db.executescript('DROP TABLE photo_spot_doc; DROP TABLE photo_spot_seed;')
        self.assertIsNone(stage.stage_dictionary(self.source, self.target)['photo_spots'])
        with self.assertRaisesRegex(ValueError, '机位攻略表'):
            stage.stage_dictionary(self.source, self.target, True)

    def test_refuses_source_as_destination(self):
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, '同一路径'):
            stage.stage_dictionary(self.source, self.source, True)
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
