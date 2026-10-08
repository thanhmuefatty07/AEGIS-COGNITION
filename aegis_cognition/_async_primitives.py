"""Small async primitives shared by process-local execution paths."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from contextlib import suppress


class _LockWaiter:
    __slots__ = ("future", "loop", "state")

    def __init__(self, loop: asyncio.AbstractEventLoop, future: asyncio.Future[None]) -> None:
        self.loop = loop
        self.future = future
        self.state = "WAITING"


class CrossLoopAsyncLock:
    """An asyncio-friendly lock that coordinates tasks across event loops."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locked = False
        self._waiters: deque[_LockWaiter] = deque()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._guard:
            if not self._locked:
                self._locked = True
                return
            waiter = _LockWaiter(loop, loop.create_future())
            self._waiters.append(waiter)

        try:
            await waiter.future
        except BaseException:
            self._cancel_waiter(waiter)
            raise

        with self._guard:
            if waiter.state != "GRANTED":
                raise RuntimeError("cross-loop lock handoff was lost")
            waiter.state = "ACQUIRED"

    async def __aenter__(self) -> CrossLoopAsyncLock:
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        self.release()

    def release(self) -> None:
        with self._guard:
            if not self._locked:
                raise RuntimeError("release unlocked cross-loop lock")
            waiter = self._next_waiter_locked()
        self._dispatch(waiter)

    def _next_waiter_locked(self) -> _LockWaiter | None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.state == "WAITING":
                waiter.state = "GRANTED"
                return waiter
        self._locked = False
        return None

    def _cancel_waiter(self, waiter: _LockWaiter) -> None:
        with self._guard:
            if waiter.state == "WAITING":
                waiter.state = "CANCELLED"
                with suppress(ValueError):
                    self._waiters.remove(waiter)
                return
            if waiter.state != "GRANTED":
                return
            waiter.state = "CANCELLED"
            next_waiter = self._next_waiter_locked()
        self._dispatch(next_waiter)

    def _dispatch(self, waiter: _LockWaiter | None) -> None:
        while waiter is not None:
            try:
                waiter.loop.call_soon_threadsafe(self._deliver, waiter)
                return
            except RuntimeError:
                waiter = self._abandon_grant(waiter)

    def _abandon_grant(self, waiter: _LockWaiter) -> _LockWaiter | None:
        with self._guard:
            if waiter.state != "GRANTED":
                return None
            waiter.state = "CANCELLED"
            return self._next_waiter_locked()

    def _deliver(self, waiter: _LockWaiter) -> None:
        with self._guard:
            if waiter.state != "GRANTED":
                return
            if waiter.future.cancelled():
                waiter.state = "CANCELLED"
                next_waiter = self._next_waiter_locked()
            else:
                waiter.future.set_result(None)
                return
        self._dispatch(next_waiter)
