"""Deterministic offline model provider."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

from zhiyi.application.models.contracts import (
    ErrorCode,
    ModelError,
    ModelRequest,
    ModelTarget,
    ProviderId,
)
from zhiyi.application.ports.model_provider import ProviderChunk, ProviderResponse
from zhiyi.application.ports.secret_provider import SecretValue


@dataclass(frozen=True, slots=True)
class FakeScript:
    completions: tuple[ProviderResponse | ModelError, ...] = ()
    streams: tuple[tuple[ProviderChunk | ModelError, ...], ...] = ()
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.delay_seconds < 0:
            raise ValueError("fake delay must be non-negative")


class FakeProvider:
    """Replay scripted responses with isolated, lock-protected consumption."""

    def __init__(
        self,
        script: FakeScript | None = None,
        *,
        provider_id: ProviderId | None = None,
    ) -> None:
        script = script or FakeScript()
        self._completions = deque(script.completions)
        self._streams = deque(script.streams)
        self._delay_seconds = script.delay_seconds
        self._lock = asyncio.Lock()
        self._provider_id = provider_id or ProviderId("fake")
        self.complete_calls = 0
        self.stream_calls = 0

    @property
    def provider_id(self) -> ProviderId:
        return self._provider_id

    async def complete(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse:
        del target, credential
        async with self._lock:
            self.complete_calls += 1
            if not self._completions:
                raise ModelError(
                    code=ErrorCode.UNAVAILABLE,
                    message="Fake provider has no scripted completion",
                    request_id=request.request_id,
                )
            outcome = self._completions.popleft()
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if isinstance(outcome, ModelError):
            raise outcome
        return outcome

    async def stream(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]:
        del target, credential
        async with self._lock:
            self.stream_calls += 1
            if not self._streams:
                raise ModelError(
                    code=ErrorCode.UNAVAILABLE,
                    message="Fake provider has no scripted stream",
                    request_id=request.request_id,
                )
            outcomes = self._streams.popleft()
        for outcome in outcomes:
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            if isinstance(outcome, ModelError):
                raise outcome
            yield outcome
