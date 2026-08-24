"""Provider adapter port and internal provider-neutral results."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from zhiyi.application.models.contracts import (
    FinishReason,
    ModelRequest,
    ModelTarget,
    ModelUsage,
    ProviderId,
    TextPart,
    ToolCall,
    ToolCallDelta,
)
from zhiyi.application.ports.secret_provider import SecretValue


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: tuple[TextPart, ...] = ()
    finish_reason: FinishReason = FinishReason.UNKNOWN
    usage: ModelUsage | None = None
    provider_request_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    structured_output: object | None = None


@dataclass(frozen=True, slots=True)
class ProviderChunk:
    text_delta: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage: ModelUsage | None = None
    finish_reason: FinishReason | None = None
    provider_request_id: str | None = None
    structured_output: object | None = None


class ModelProvider(Protocol):
    @property
    def provider_id(self) -> ProviderId: ...

    async def complete(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse: ...

    def stream(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]: ...
