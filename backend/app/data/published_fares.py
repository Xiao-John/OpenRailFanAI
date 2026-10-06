"""Published reference fares are local facts, never guessed from GTFS mileage."""
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

SCHEMA='''CREATE TABLE IF NOT EXISTS published_fare (
 train_code TEXT NOT NULL, from_station TEXT NOT NULL, to_station TEXT NOT NULL,
 valid_from TEXT NOT NULL, valid_until TEXT NOT NULL, seat TEXT NOT NULL,
 amount TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'CNY', source TEXT NOT NULL,
 fetched_at TEXT NOT NULL,
 PRIMARY KEY(train_code,from_station,to_station,valid_from,seat)
);'''


def lookup(path: Path,train,origin,destination,day):
    if not path.is_file():return None
    with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='published_fare'").fetchone():return None
        records=c.execute('SELECT * FROM published_fare WHERE train_code=? AND from_station=? AND to_station=? AND valid_from<=? AND valid_until>=? ORDER BY valid_from DESC',
                          (train,origin,destination,day,day)).fetchall()
    if not records:return None
    latest_by_seat={}
    for record in records:
        latest_by_seat.setdefault(record['seat'], record)
    records=list(latest_by_seat.values())
    if any(r['currency']!='CNY' or not r['source'] or not r['fetched_at'] for r in records):return None
    try:
        if any(not Decimal(r['amount']).is_finite() or Decimal(r['amount'])<0 for r in records):return None
        for r in records:
            date.fromisoformat(r['valid_from']);date.fromisoformat(r['valid_until'])
    except (InvalidOperation,ValueError):return None
    return {'success':True,'fare_basis':'published','from_station':origin,'to_station':destination,
            'sources':list(dict.fromkeys(r['source'] for r in records)),
            'fetched_at':min(r['fetched_at'] for r in records),'query_date':day,
            'data':[{'train_code':train,'from_station':origin,'to_station':destination,
                     'prices':{r['seat']:r['amount'] for r in records},'fare_basis':'published'}]}
