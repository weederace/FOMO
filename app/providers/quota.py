"""Async request throttling and rolling quota enforcement."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class QuotaExceeded(RuntimeError):
    """The configured provider quota has been consumed."""


class RequestQuota:
    def __init__(self, per_second: int, window_seconds: int, window_limit: int) -> None:
        self.per_second = max(per_second, 1)
        self.window_seconds = window_seconds
        self.window_limit = max(window_limit, 1)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def acquire(self) -> None:
        interval = 1 / self.per_second
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) >= self.window_limit:
                    raise QuotaExceeded("provider rolling quota has been reached")
                wait = interval - (now - self._last_call)
                if wait <= 0:
                    self._last_call = now
                    self._calls.append(now)
                    return
            await asyncio.sleep(min(wait, 1.0))

    @property
    def used(self) -> int:
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.window_seconds:
            self._calls.popleft()
        return len(self._calls)
