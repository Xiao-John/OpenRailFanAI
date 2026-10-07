"""Main fare/availability co-query planning, bounded and free of model output."""
import asyncio
from app.dates import normalize_date
from app.pipeline.fare_basis import select
from app.tools._rt12306 import is_train_code


def _key(name, params):
    return (name, str(params.get('train') or '').upper(), normalize_date(params.get('date')),
            params.get('from_station'), params.get('to_station'))


def expand(plan, message):
    expanded = list(plan)
    seen = {_key(name, params) for name, params in plan}
    for name, params in plan:
        if name not in {'ticket.price', 'ticket.query'} or not params.get('train'):
            continue
        if not params.get('from_station') or not params.get('to_station'):
            continue
        peer = 'ticket.query' if name == 'ticket.price' else 'ticket.price'
        paired = {k: params[k] for k in ('train', 'date', 'from_station', 'to_station')}
        paired['date'] = normalize_date(paired['date'])
        if peer == 'ticket.price':
            paired['fare_basis'] = select(message)
            if paired['fare_basis'] is None:
                continue
        key = _key(peer, paired)
        if key not in seen:
            expanded.append((peer, paired)); seen.add(key)
    return expanded


async def complete_collection(plan, results, runner, message):
    """A route-wide ticket search supplies up to ten actual train identities first."""
    extra = []
    seen = {_key(name, params) for name, params in plan}
    basis = select(message)
    if basis is None:
        return
    for (name, params), result in zip(plan, results):
        if name != 'ticket.query' or params.get('train') or not result.ok:
            continue
        data = result.data if isinstance(result.data, dict) else {}
        day = normalize_date(params.get('date'))
        if data.get('train_date') != day:
            continue
        rows = data.get('trains')
        if not isinstance(rows, list) or len(rows) > 10:
            continue
        for row in rows:
            if not isinstance(row, dict) or not is_train_code(str(row.get('train_no') or '')):
                continue
            # Never turn a nearby station record into the requested interval.
            origin, destination = row.get('from_station'), row.get('to_station')
            if (not origin or not destination or origin != data.get('from_station')
                    or destination != data.get('to_station')):
                continue
            paired = dict(train=row['train_no'], date=day, from_station=origin,
                          to_station=destination, fare_basis=basis)
            key = _key('ticket.price', paired)
            if key not in seen:
                extra.append(('ticket.price', paired)); seen.add(key)
    tasks = [asyncio.create_task(runner(name, params)) for name, params in extra]
    try:
        completed = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    plan.extend(extra); results.extend(completed)
