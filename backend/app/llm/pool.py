"""Bounded, event-loop-local SDK client leases for Main.

An active lease is never evicted. If every slot is busy, the new configuration
gets an ephemeral client, which closes when its final borrower exits.
"""
from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(eq=False)
class Lease:
    client: Any
    key: str
    borrowers: int = 0
    retired: bool = False


class ClientPool:
    def __init__(self, capacity: int = 16):
        self.capacity = capacity
        self.entries: OrderedDict[str, Lease] = OrderedDict()
        self.ephemeral: set[Lease] = set()
        self.lock = asyncio.Lock()
        self.closing = False

    async def acquire(self, key: str, factory: Callable[[], Any]) -> Lease:
        async with self.lock:
            if self.closing:
                raise RuntimeError("LLM client pool is shutting down")
            entry = self.entries.get(key)
            if entry is not None and not entry.retired:
                entry.borrowers += 1
                self.entries.move_to_end(key)
                _record_cache("hit")
                return entry
            _record_cache("miss")
            if len(self.entries) >= self.capacity:
                idle = next((e for e in self.entries.values() if not e.borrowers), None)
                if idle is not None:
                    self.entries.pop(idle.key)
                    idle.retired = True
                    await _close(idle.client)
                    _record_cache("evicted")
            construction = asyncio.create_task(asyncio.to_thread(factory))
            try:
                client = await asyncio.shield(construction)
            except asyncio.CancelledError:
                # A worker thread cannot be cancelled. Collect its result and
                # close it, rather than orphaning a just-built HTTP client.
                client = await construction
                await _close(client)
                raise
            entry = Lease(client, key, borrowers=1)
            if len(self.entries) < self.capacity:
                self.entries[key] = entry
            else:
                entry.retired = True
                self.ephemeral.add(entry)
            return entry

    async def release(self, entry: Lease) -> None:
        async with self.lock:
            entry.borrowers -= 1
            if not entry.borrowers and entry.retired:
                self.ephemeral.discard(entry)
                await _close(entry.client)

    async def shutdown(self) -> None:
        async with self.lock:
            self.closing = True
            entries = list(self.entries.values()) + list(self.ephemeral)
            self.entries.clear()
            for entry in entries:
                entry.retired = True
                if entry.borrowers:
                    self.ephemeral.add(entry)
                else:
                    self.ephemeral.discard(entry)
                    await _close(entry.client)


async def _close(client: Any) -> None:
    await client.close()


def _record_cache(status: str) -> None:
    try:
        from app.metrics import record_cache
        record_cache("llm.clients", status)
    except Exception:
        pass  # Observability must not affect provider requests.


_POOLS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def current_pool() -> ClientPool:
    loop = asyncio.get_running_loop()
    pool = _POOLS.get(loop)
    if pool is None:
        pool = ClientPool()
        _POOLS[loop] = pool
    return pool


async def shutdown_clients() -> None:
    """Close idle clients on this loop; active clients close after release."""
    loop = asyncio.get_running_loop()
    pool = _POOLS.pop(loop, None)
    if pool is not None:
        await pool.shutdown()
