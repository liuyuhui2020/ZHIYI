"""Cancellation-safe in-process token bucket."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class AsyncTokenBucket:
    """A fair-enough FIFO token bucket scoped by the owning gateway target."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        clock: MonotonicClock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        if not math.isfinite(rate_per_second) or rate_per_second <= 0:
            raise ValueError("rate_per_second must be a positive finite number")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._rate = rate_per_second
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._sleeper = sleeper
        self._updated_at = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated_at = now

    async def acquire(self) -> None:
        """Wait for one token; cancellation never reserves or consumes a future token."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1 - 1e-12:
                    self._tokens = max(0.0, self._tokens - 1)
                    return
                delay = (1 - self._tokens) / self._rate
                await self._sleeper(delay)
