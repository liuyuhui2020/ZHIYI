"""Per-target async circuit breaker."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from enum import StrEnum

from zhiyi.application.models.contracts import ErrorCode, ModelError

MonotonicClock = Callable[[], float]

_TRANSIENT_CODES = frozenset({ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE})


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Open after consecutive transient failures and admit one recovery probe."""

    def __init__(
        self,
        *,
        threshold: int,
        recovery_seconds: float,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if recovery_seconds <= 0:
            raise ValueError("recovery_seconds must be positive")
        self._threshold = threshold
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._probe_active = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def before_call(self, request_id: str) -> None:
        async with self._lock:
            if self._state is CircuitState.OPEN:
                opened_at = self._opened_at
                if opened_at is not None and (self._clock() - opened_at >= self._recovery_seconds):
                    self._state = CircuitState.HALF_OPEN
                    self._probe_active = False
                else:
                    raise self._open_error(request_id)
            if self._state is CircuitState.HALF_OPEN:
                if self._probe_active:
                    raise self._open_error(request_id)
                self._probe_active = True

    async def record_success(self) -> None:
        async with self._lock:
            self._close()

    async def record_failure(self, code: ErrorCode) -> None:
        async with self._lock:
            if code not in _TRANSIENT_CODES:
                if self._state is CircuitState.HALF_OPEN:
                    self._close()
                return
            if self._state is CircuitState.HALF_OPEN:
                self._open()
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                self._open()

    async def record_cancelled(self) -> None:
        async with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._probe_active = False

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._probe_active = False

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._probe_active = False

    @staticmethod
    def _open_error(request_id: str) -> ModelError:
        return ModelError(
            code=ErrorCode.CIRCUIT_OPEN,
            message="Model target circuit is open",
            request_id=request_id,
        )
