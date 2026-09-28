"""In-process wake-up of SSE streams after events are persisted.

The database remains the source of truth: a stream re-reads it after every
notification or heartbeat timeout, so a missed wake-up only delays delivery.
"""

from __future__ import annotations

import asyncio


class Notifier:
    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Future[None]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, key: str) -> asyncio.Future[None]:
        """Register before reading the database, so no event is missed."""

        loop = asyncio.get_running_loop()
        self._loop = loop
        future: asyncio.Future[None] = loop.create_future()
        self._waiters.setdefault(key, set()).add(future)
        return future

    def unsubscribe(self, key: str, future: asyncio.Future[None]) -> None:
        waiters = self._waiters.get(key)
        if waiters is not None:
            waiters.discard(future)
            if not waiters:
                self._waiters.pop(key, None)
        if not future.done():
            future.cancel()

    def notify(self, key: str) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake(key)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._wake, key)

    def _wake(self, key: str) -> None:
        for future in self._waiters.pop(key, set()):
            if not future.done():
                future.set_result(None)
