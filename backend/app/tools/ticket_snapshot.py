"""Main-only request-local raw sale rows, shared before train/seat filtering."""
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import asyncio
import os

import httpx

from app.config import get_settings
from app.dates import normalize_date
from app.query_cache import QueryCache, query_scope
from app.tools._http import BROWSER_HEADERS, get_client

INIT = 'https://kyfw.12306.cn/otn/leftTicket/init'
ROWS = 'https://kyfw.12306.cn/otn/leftTicket/queryI'
class RequestStore(QueryCache):
    def __init__(self):
        super().__init__('rt.sale_snapshot', capacity=128)
        self.borrowers = 0
        self.ending = False

    async def finish(self):
        self.ending = True
        if self.borrowers == 0:
            await self.close()


_STORE: ContextVar[RequestStore | None] = ContextVar('ticket_snapshot_store', default=None)
_REFRESH: ContextVar[object | None] = ContextVar('ticket_snapshot_refresh', default=None)


def active() -> bool:
    return (_STORE.get() is not None and os.environ.get('APP_VARIANT', 'main').lower() != 'lm'
            and get_settings().main_ticket_shared_rows_enabled)


def refresh_key():
    return _REFRESH.get() if active() else None


def scope_key():
    return _STORE.get() if active() else None


@asynccontextmanager
async def retain_scope():
    """A shared tool factory can outlive its first caller if another waiter remains."""
    store = _STORE.get() if active() else None
    if store is not None:
        store.borrowers += 1
    try:
        yield
    finally:
        if store is not None:
            store.borrowers -= 1
            if store.ending and store.borrowers == 0:
                await store.close()


@contextmanager
def force_refresh():
    """A verification is a distinct network observation, never the original snapshot."""
    token = _REFRESH.set(object())
    try:
        yield
    finally:
        _REFRESH.reset(token)


def request_scope(fn):
    @wraps(fn)
    async def scoped(*args, **kwargs):
        if _STORE.get() is not None or os.environ.get('APP_VARIANT', 'main').lower() == 'lm':
            return await fn(*args, **kwargs)
        from app.query_cache import scope_context
        store = RequestStore()
        token = _STORE.set(store)
        try:
            with scope_context():
                return await fn(*args, **kwargs)
        finally:
            _STORE.reset(token)
            await store.finish()
    return scoped


async def query_rows(from_code, to_code, day, *, purpose='ADULT'):
    from app.tools import _rt12306 as rt

    store = _STORE.get()
    if not active() or store is None:
        raise RuntimeError('sale snapshot requires an active Main request scope')
    origin, destination = str(from_code).strip().upper(), str(to_code).strip().upper()
    day = normalize_date(day)
    key = (query_scope(), origin, destination, day, purpose, refresh_key())

    async def fetch():
        client = await get_client()
        headers = {**BROWSER_HEADERS, 'Referer': INIT}
        # Preserve initialization/Cookie and the availability path's three network
        # attempts. Business/format errors are not retried or stored as success.
        for attempt in range(3):
            try:
                response = await rt._operation('sale.init', client.get(INIT, headers=headers, timeout=15),
                                               success=lambda r: r.status_code < 400)
                response.raise_for_status()
                response = await rt._operation('sale.rows', client.get(ROWS, headers=headers, timeout=15, params={
                    'leftTicketDTO.train_date': day, 'leftTicketDTO.from_station': origin,
                    'leftTicketDTO.to_station': destination, 'purpose_codes': purpose}),
                    success=lambda r: r.status_code < 400)
                response.raise_for_status()
                payload = response.json()
                data = payload.get('data') if isinstance(payload, dict) else None
                rows = data.get('result') if isinstance(data, dict) else None
                if (not isinstance(payload, dict) or payload.get('status') is not True or not isinstance(rows, list)
                        or any(not isinstance(row, str) for row in rows)):
                    raise rt.Realtime12306Error('售票查询未返回有效原始车次记录')
                return {'rows': rows, 'fetched_at': datetime.now(timezone.utc).isoformat()}
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise
                await asyncio.sleep(1)

    # No process-wide raw-row TTL. A scope is cleared on completion, or after
    # its last retained tool factory finishes for other still-active waiters.
    return await store.get(key, fetch, ttl=3600)


class SnapshotTrains(list):
    def __init__(self, values, fetched_at):
        super().__init__(values)
        self.fetched_at = fetched_at


async def availability(from_code, to_code, day):
    from mcp_12306.services import ticket_service as ts
    from app.tools import _rt12306 as rt

    valid, error = ts.validate_date_not_past(day)
    if not valid:
        raise rt.Realtime12306Error(error)
    snapshot = await query_rows(from_code, to_code, day)
    trains = []
    for raw in snapshot['rows']:
        record = ts.parse_ticket_string(raw, dict(from_station=from_code, to_station=to_code, train_date=day))
        if record is None:
            continue
        parts = raw.split('|')
        origin = await ts.station_service.get_station_by_code(parts[6])
        destination = await ts.station_service.get_station_by_code(parts[7])
        trains.append(dict(train_no=record['train_no'], from_station=origin.name if origin else parts[6],
                           from_station_code=parts[6], to_station=destination.name if destination else parts[7],
                           to_station_code=parts[7], start_time=record['start_time'],
                           arrive_time=record['arrive_time'], duration=record['duration'],
                           seats=ts._extract_seats(record, ts._SEAT_FIELD_MAP)))
    if snapshot['rows'] and not trains:
        raise rt.Realtime12306Error('余票响应包含记录，但记录格式无法解析')
    return SnapshotTrains(trains, snapshot['fetched_at'])
