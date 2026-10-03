"""Bounded query reuse with per-waiter cancellation and isolated mutable results."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import time
import hashlib
import json
import threading
import weakref
from typing import Any, Awaitable, Callable

from app.metrics import record_cache


def query_scope() -> str:
    from app.config import get_settings
    from app.llm.client import _ACTIVE_PROVIDER

    settings = get_settings()
    config = settings.dict() if hasattr(settings, "dict") else vars(settings)
    return hashlib.sha256(json.dumps([config, _ACTIVE_PROVIDER.get()],
        sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


@dataclass
class _State:
    values: OrderedDict = field(default_factory=OrderedDict)
    flights: dict[Any, _Flight] = field(default_factory=dict)
    closed: bool = False


class QueryCache:
    def __init__(self, name: str, capacity: int = 128):
        self.name = name
        self.capacity = capacity
        self._states: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._states_lock = threading.Lock()

    def _state(self) -> _State:
        loop = asyncio.get_running_loop()
        # Different live loops can call this object from different threads.
        # Only the state lookup is shared; each loop owns all its task/value work.
        with self._states_lock:
            state = self._states.get(loop)
            if state is None:
                state = _State()
                self._states[loop] = state
            return state

    @property
    def _loop(self):
        """Preserve internal inspection without exposing another loop's state."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    @property
    def _values(self):
        return self._state().values if self._loop is not None else OrderedDict()

    @property
    def _flights(self):
        return self._state().flights if self._loop is not None else {}

    async def get(self, key: Any, factory: Callable[[], Awaitable[Any]], *, ttl: float = 0,
                  reusable: Callable[[Any], bool] = lambda value: True) -> Any:
        state = self._state()
        flights, values = state.flights, state.values
        now = time.monotonic()
        for old_key, (deadline, _) in list(values.items()):
            if deadline <= now:
                values.pop(old_key)
                record_cache(self.name, "expired")
        if key in values:
            values.move_to_end(key)
            record_cache(self.name, "hit")
            return deepcopy(values[key][1])
        flight = flights.get(key)
        if flight is None:
            record_cache(self.name, "miss")
            if len(flights) >= self.capacity:
                # Do not grow the in-flight map without bound during a burst.
                return deepcopy(await factory())

            async def run():
                value = await factory()
                if not state.closed and ttl > 0 and reusable(value):
                    while len(values) >= self.capacity:
                        values.popitem(last=False)
                        record_cache(self.name, "evicted")
                    values[key] = (time.monotonic() + ttl, deepcopy(value))
                return value

            flight = _Flight(asyncio.create_task(run()))
            flights[key] = flight
        else:
            record_cache(self.name, "joined")
        flight.waiters += 1
        try:
            return deepcopy(await asyncio.shield(flight.task))
        finally:
            flight.waiters -= 1
            if flight.waiters == 0:
                if flights.get(key) is flight:
                    flights.pop(key)
                if not flight.task.done():
                    record_cache(self.name, "abandoned")
                    flight.task.cancel()
                # Reap errors/cancellation; no unobserved or unconditional background work.
                await asyncio.gather(flight.task, return_exceptions=True)

    async def close(self):
        # Never cancel or await tasks belonging to another still-live loop.
        loop = asyncio.get_running_loop()
        with self._states_lock:
            state = self._states.pop(loop, None)
        if state is None:
            return
        state.closed = True
        tasks = [flight.task for flight in state.flights.values()]
        state.flights.clear()
        state.values.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
