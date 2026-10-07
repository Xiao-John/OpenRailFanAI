"""Shared photo dictionary schema and independent UTC data revisions."""
from datetime import datetime, timezone, timedelta

TABLES = ('photo_spot_doc', 'photo_spot_seed')
VERSION_KEY = 'photo_spots_version'
SCHEMA_KEY = 'photo_spots_schema_version'
SCHEMA = """
-- 机位线索文档库（由 scripts/build_photo_spots.py 维护）
-- 一行 = 一个"确实在讲某地点拍车"的网页；正文原文入库供生成层阅读，不做结构化点位抽取。
CREATE TABLE IF NOT EXISTS photo_spot_doc (
    url         TEXT PRIMARY KEY,
    domain      TEXT,
    title       TEXT,
    source      TEXT,              -- 来源标签（bilibili/sohu/zhihu…）
    scope       TEXT,              -- 该文档对应的地方（站名/线路名/城市）
    scope_kind  TEXT,              -- city / station / line
    entities    TEXT,              -- 命中并校验通过的具体铁路实体（JSON 数组）
    reasons     TEXT,              -- 判定依据（可复核；回答时用于标注来源可信度）
    content_hash TEXT,             -- 正文指纹（去重用：同一篇攻略常有多条 URL，如 B站 read/opus/mobile）
    page_text   TEXT,              -- 抓取到的正文原文（未抓到则为空，snippet 兜底）
    snippet     TEXT,              -- 搜索引擎摘要（正文抓取失败时的替代证据）
    fetch_ok    INTEGER NOT NULL DEFAULT 0,
    char_count  INTEGER NOT NULL DEFAULT 0,
    published   TEXT,
    fetched_at  TEXT NOT NULL
);

-- 已跑过的发现查询（让建库可续跑，并提供覆盖率口径）
CREATE TABLE IF NOT EXISTS photo_spot_seed (
    query      TEXT PRIMARY KEY,
    scope      TEXT,
    scope_kind TEXT,
    ok         INTEGER NOT NULL DEFAULT 0,
    found      INTEGER NOT NULL DEFAULT 0,   -- 通过三道闸门的文档数
    raw        INTEGER NOT NULL DEFAULT 0,    -- 引擎返回的原始结果数
    note       TEXT,
    ran_at     TEXT NOT NULL
);
"""
SCHEMA += "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"


def migrate(conn):
    # Caller creates SCHEMA first; do not executescript inside a transaction here.
    cols = {row[1] for row in conn.execute('PRAGMA table_info(photo_spot_doc)')}
    if cols and 'content_hash' not in cols:
        conn.execute('ALTER TABLE photo_spot_doc ADD COLUMN content_hash TEXT')
    for name, column in [('scope','scope'),('domain','domain'),('fetched','fetched_at'),('hash','content_hash')]:
        conn.execute(f'CREATE INDEX IF NOT EXISTS ix_psd_{name} ON photo_spot_doc({column})')


def utc(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def revision(conn, meta):
    """Legacy row dates protect newer local data before its first version stamp."""
    values = []
    explicit = meta.get(VERSION_KEY)
    if explicit:
        parsed = utc(explicit)
        if not parsed:
            raise ValueError('invalid photo dictionary revision')
        values.append(parsed)
    for table, column in [('photo_spot_doc','fetched_at'),('photo_spot_seed','ran_at')]:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            for (value,) in conn.execute(f'SELECT DISTINCT {column} FROM {table}'):
                parsed = utc(value)
                if not parsed:
                    raise ValueError('invalid legacy photo timestamp')
                values.append(parsed)
    return max(values) if values else None


def stamp(conn):
    meta = dict(conn.execute('SELECT key,value FROM meta'))
    previous = revision(conn, meta)
    now = datetime.now(timezone.utc)
    if previous and now <= previous:
        now = previous + timedelta(microseconds=1)
    conn.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',
                     [(VERSION_KEY,now.isoformat()),(SCHEMA_KEY,'1')])


def adopt(conn):
    """Attach metadata to an existing snapshot without claiming fresh collection."""
    meta = dict(conn.execute('SELECT key,value FROM meta'))
    value = revision(conn, meta)
    if value and VERSION_KEY not in meta:
        conn.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',
                         [(VERSION_KEY,value.isoformat()),(SCHEMA_KEY,'1')])
