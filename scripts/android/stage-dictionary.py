#!/usr/bin/env python3
"""Stage a complete committed SQLite snapshot without replacing the runtime DB."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import argparse
import json
import os
import sqlite3
import tempfile


def stage_dictionary(source: Path, destination: Path, require_photo_spots=False):
    source, destination = source.resolve(), destination.resolve()
    if source == destination:
        raise ValueError('包内词典与源词典不能使用同一路径')
    if not source.is_file():
        raise ValueError('未找到源词典文件')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='dict-', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as origin, \
                closing(sqlite3.connect(temporary)) as snapshot:
            origin.backup(snapshot)
            if snapshot.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('包内词典完整性检查失败')
            tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'meta', 'g_stop', 'g_trip', 'g_stop_time'} <= tables:
                raise ValueError('包内词典缺少时刻表或元数据')
            meta = dict(snapshot.execute('SELECT key,value FROM meta'))
            photo_tables = {'photo_spot_doc', 'photo_spot_seed'}
            summary = {'gtfs_version': meta.get('gtfs_tag'), 'photo_spots': None}
            if require_photo_spots:
                if not photo_tables <= tables:
                    raise ValueError('Main 正式包缺少机位攻略表，请使用新版完整词典')
                columns = {row[1] for row in snapshot.execute('PRAGMA table_info(photo_spot_doc)')}
                if 'content_hash' not in columns:
                    raise ValueError('Main 正式包机位攻略表结构过旧')
                if meta.get('photo_spots_schema_version') != '1':
                    raise ValueError('Main 正式包缺少受支持的机位结构版本')
                try:
                    revision = datetime.fromisoformat(meta.get('photo_spots_version', '').replace('Z', '+00:00'))
                except ValueError:
                    raise ValueError('Main 正式包缺少有效的机位数据版本') from None
                if revision.tzinfo is None:
                    raise ValueError('机位数据版本缺少时区')
            if photo_tables <= tables:
                documents, scopes = snapshot.execute('SELECT COUNT(*),COUNT(DISTINCT scope) FROM photo_spot_doc').fetchone()
                queries = snapshot.execute('SELECT COUNT(*) FROM photo_spot_seed').fetchone()[0]
                if require_photo_spots and (documents == 0 or scopes == 0 or queries == 0):
                    raise ValueError('Main 正式包机位攻略快照为空')
                summary['photo_spots'] = {'version': meta.get('photo_spots_version'),
                                         'schema_version': meta.get('photo_spots_schema_version'),
                                         'documents': documents, 'scopes': scopes, 'queries': queries}
        os.replace(temporary, destination)
        return summary
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='准备完整包内词典；Main 正式包验证机位数据。')
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--require-photo-spots', action='store_true')
    args = parser.parse_args()
    try:
        result = stage_dictionary(args.source, args.destination, args.require_photo_spots)
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f'词典打包失败：{error}\n')
    print(json.dumps(result, ensure_ascii=False))
