"""Provider-neutral model contracts.

This module intentionally depends only on the standard library and application ports.
Provider and framework types must be translated before crossing this boundary.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self, runtime_checkable
from urllib.parse import urlparse

from zhiyi.application.ports.secret_provider import SecretReference

_PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def _require_positive_finite(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def thaw_json(value: object) -> object:
    """Return a mutable JSON-compatible copy for an outer adapter."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def _validate_json_schema(
    schema: object,
    *,
    require_object_root: bool,
) -> None:
    if not isinstance(schema, Mapping):
        raise ValueError("schema must be a JSON object")
    schema_type = schema.get("type")
    if require_object_root and schema_type != "object":
        raise ValueError("schema root type must be object")
    if schema_type is not None and not isinstance(schema_type, str | tuple):
        raise ValueError("schema type must be a string or string array")
    if isinstance(schema_type, tuple) and (
        not schema_type or any(not isinstance(item, str) for item in schema_type)
    ):
        raise ValueError("schema type array must contain strings")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ValueError("schema properties must be an object")
        for name, child in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError("schema property names must be non-empty strings")
            if isinstance(child, bool):
                continue
            _validate_json_schema(child, require_object_root=False)

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, tuple) or any(
            not isinstance(name, str) or not name for name in required
        ):
            raise ValueError("schema required must be a string array")
        if len(required) != len(set(required)):
            raise ValueError("schema required entries must be unique")
        if not isinstance(properties, Mapping) or any(name not in properties for name in required):
            raise ValueError("schema required entries must name declared properties")

    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool | Mapping):
        raise ValueError("schema additionalProperties must be boolean or a schema")
    if isinstance(additional, Mapping):
        _validate_json_schema(additional, require_object_root=False)

    items = schema.get("items")
    if items is not None and not isinstance(items, bool | Mapping):
        raise ValueError("schema items must be boolean or a schema")
    if isinstance(items, Mapping):
        _validate_json_schema(items, require_object_root=False)


@dataclass(frozen=True, slots=True, order=True)
class ProviderId:
    """An open, validated provider registration key."""

    value: str

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_PATTERN.fullmatch(self.value):
            raise ValueError("provider id must be a lowercase registration key")

    def __str__(self) -> str:
        return self.value


class InputModality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"


class ModelCapability(StrEnum):
    TEXT = "text"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CAPABILITY_MISMATCH = "capability_mismatch"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    CONTENT_POLICY = "content_policy"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    UNAVAILABLE = "unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    STRUCTURED_OUTPUT_INVALID = "structured_output_invalid"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ChunkKind(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    TERMINAL = "terminal"
    ERROR = "error"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text content must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ImagePart:
    uri: str
    media_type: str

    def __post_init__(self) -> None:
        scheme = urlparse(self.uri).scheme.lower()
        if scheme not in {"https", "data"}:
            raise ValueError("image URI scheme must be https or data")
        if not self.media_type.startswith("image/"):
            raise ValueError("image media_type must start with image/")


@dataclass(frozen=True, slots=True)
class DocumentPart:
    uri: str
    media_type: str
    title: str | None = None

    def __post_init__(self) -> None:
        scheme = urlparse(self.uri).scheme.lower()
        if scheme not in {"https", "data"}:
            raise ValueError("document URI scheme must be https or data")
        if not self.media_type:
            raise ValueError("document media_type must not be empty")


ContentPart = TextPart | ImagePart | DocumentPart
_CONTENT_PART_TYPES = (TextPart, ImagePart, DocumentPart)


@dataclass(frozen=True, slots=True)
class Message:
    role: MessageRole
    content: tuple[ContentPart, ...]
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", tuple(self.content))
        if not isinstance(self.role, MessageRole):
            raise ValueError("message role is invalid")
        if not self.content:
            raise ValueError("message content must not be empty")
        if any(not isinstance(part, _CONTENT_PART_TYPES) for part in self.content):
            raise ValueError("message contains an invalid content part")
        if self.role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("tool message requires tool_call_id")
        if self.role is not MessageRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid for tool messages")
        if self.role in {MessageRole.SYSTEM, MessageRole.TOOL} and any(
            not isinstance(part, TextPart) for part in self.content
        ):
            raise ValueError("system and tool messages only support text content")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, object]
    strict: bool = True

    def __post_init__(self) -> None:
        if not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("tool name is invalid")
        if not self.description.strip():
            raise ValueError("tool description must not be empty")
        frozen = _freeze_json(self.input_schema)
        if not isinstance(frozen, Mapping) or frozen.get("type") != "object":
            raise ValueError("tool input schema root must be an object")
        _validate_json_schema(frozen, require_object_root=True)
        object.__setattr__(self, "input_schema", frozen)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("tool call id must not be empty")
        if not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("tool call name is invalid")
        frozen = _freeze_json(self.arguments)
        if not isinstance(frozen, Mapping):
            raise ValueError("tool call arguments must be an object")
        object.__setattr__(self, "arguments", frozen)


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments_fragment: str = ""

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("tool call delta index must be non-negative")


@runtime_checkable
class StructuredOutputContract(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def json_schema(self) -> Mapping[str, object]: ...

    def validate(self, value: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ModelCapabilityProfile:
    tool_calling: bool = False
    structured_output: bool = False
    input_modalities: frozenset[InputModality] = field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )
    modality_token_upper_bounds: Mapping[InputModality, int] = field(default_factory=dict)
    max_context_tokens: int = 8_192
    max_output_tokens: int = 1_024
    usage_available: bool = True

    def __post_init__(self) -> None:
        modalities = frozenset(self.input_modalities)
        if InputModality.TEXT not in modalities:
            raise ValueError("text input modality is mandatory")
        if self.max_context_tokens <= 0 or self.max_output_tokens <= 0:
            raise ValueError("token limits must be positive")
        if self.max_output_tokens > self.max_context_tokens:
            raise ValueError("max output tokens cannot exceed context tokens")
        bounds = dict(self.modality_token_upper_bounds)
        for modality in modalities - {InputModality.TEXT}:
            if bounds.get(modality, 0) <= 0:
                raise ValueError(f"token upper bound is required for {modality.value}")
        if any(modality not in modalities or value <= 0 for modality, value in bounds.items()):
            raise ValueError("modality token upper bounds must match supported modalities")
        object.__setattr__(self, "input_modalities", modalities)
        object.__setattr__(self, "modality_token_upper_bounds", MappingProxyType(bounds))

    def supports(self, capability: ModelCapability) -> bool:
        if capability is ModelCapability.TEXT:
            return True
        if capability is ModelCapability.TOOL_CALLING:
            return self.tool_calling
        return self.structured_output


@dataclass(frozen=True, slots=True)
class ModelLimits:
    attempt_timeout_seconds: float = 30.0
    stream_first_byte_timeout_seconds: float = 15.0
    stream_idle_timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 0.25
    retry_max_delay_seconds: float = 2.0
    rate_limit_per_second: float | None = None
    rate_limit_burst: int = 1
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "attempt_timeout_seconds",
            "stream_first_byte_timeout_seconds",
            "stream_idle_timeout_seconds",
            "retry_base_delay_seconds",
            "retry_max_delay_seconds",
            "circuit_recovery_seconds",
        ):
            _require_positive_finite(getattr(self, name), name)
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.retry_max_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError("retry max delay must not be below base delay")
        if self.rate_limit_per_second is not None:
            _require_positive_finite(self.rate_limit_per_second, "rate_limit_per_second")
        if self.rate_limit_burst <= 0:
            raise ValueError("rate_limit_burst must be positive")
        if self.circuit_failure_threshold <= 0:
            raise ValueError("circuit failure threshold must be positive")


@dataclass(frozen=True, slots=True)
class ModelTarget:
    provider: ProviderId
    model_id: str
    credential: SecretReference | None
    capabilities: ModelCapabilityProfile
    limits: ModelLimits = field(default_factory=ModelLimits)

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")

    @property
    def key(self) -> tuple[ProviderId, str]:
        return (self.provider, self.model_id)


@dataclass(frozen=True, slots=True)
class ModelRoute:
    primary: ModelTarget
    fallbacks: tuple[ModelTarget, ...] = ()
    total_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "fallbacks", tuple(self.fallbacks))
        _require_positive_finite(self.total_timeout_seconds, "total_timeout_seconds")
        keys = [self.primary.key, *(target.key for target in self.fallbacks)]
        if len(keys) != len(set(keys)):
            raise ValueError("route contains duplicate targets")

    @property
    def targets(self) -> tuple[ModelTarget, ...]:
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    messages: tuple[Message, ...]
    max_output_tokens: int
    required_capabilities: frozenset[ModelCapability] = field(default_factory=frozenset)
    tools: tuple[ToolDefinition, ...] = ()
    structured_output: StructuredOutputContract | None = None
    temperature: float | None = None
    stop: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "stop", tuple(self.stop))
        required = frozenset(self.required_capabilities)
        if not self.request_id.strip():
            raise ValueError("request_id must not be empty")
        if not self.messages:
            raise ValueError("messages must not be empty")
        if not any(
            not isinstance(part, TextPart) or bool(part.text.strip())
            for message in self.messages
            for part in message.content
        ):
            raise ValueError("request must contain non-whitespace content")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.temperature is not None and (
            not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2
        ):
            raise ValueError("temperature must be between 0 and 2")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        if any(not value for value in self.stop) or len(self.stop) != len(set(self.stop)):
            raise ValueError("stop sequences must be non-empty and unique")
        if self.tools and self.structured_output is not None:
            raise ValueError("tools and structured output are separate portable call modes")
        if self.tools:
            required = required | {ModelCapability.TOOL_CALLING}
        if self.structured_output is not None:
            try:
                contract_name = self.structured_output.name
                schema = _freeze_json(self.structured_output.json_schema)
            except Exception:
                raise ValueError("structured output contract is invalid") from None
            if not _TOOL_NAME_PATTERN.fullmatch(contract_name):
                raise ValueError("structured output name is invalid")
            try:
                _validate_json_schema(schema, require_object_root=True)
            except ValueError:
                raise ValueError("structured output schema is invalid") from None
            required = required | {ModelCapability.STRUCTURED_OUTPUT}
        object.__setattr__(self, "required_capabilities", required)

    @property
    def input_modalities(self) -> frozenset[InputModality]:
        modalities = {InputModality.TEXT}
        for message in self.messages:
            for part in message.content:
                if isinstance(part, ImagePart):
                    modalities.add(InputModality.IMAGE)
                elif isinstance(part, DocumentPart):
                    modalities.add(InputModality.DOCUMENT)
        return frozenset(modalities)


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    input_details: Mapping[str, int] = field(default_factory=dict)
    output_details: Mapping[str, int] = field(default_factory=dict)
    amount: Decimal | None = None
    currency: str | None = None
    incomplete: bool = False

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens, self.total_tokens):
            if value is not None and value < 0:
                raise ValueError("token usage must be non-negative")
        if self.total_tokens is not None and not self.incomplete:
            known_parts = (self.input_tokens or 0) + (self.output_tokens or 0)
            if self.total_tokens < known_parts:
                raise ValueError("total token usage is below known input and output")
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be provided together")
        if self.amount is not None and self.amount < 0:
            raise ValueError("amount must be non-negative")
        object.__setattr__(self, "input_details", MappingProxyType(dict(self.input_details)))
        object.__setattr__(self, "output_details", MappingProxyType(dict(self.output_details)))

    @classmethod
    def aggregate(cls, usages: tuple[ModelUsage, ...]) -> Self:
        if not usages:
            return cls(incomplete=True)

        def aggregate_optional(name: str) -> tuple[int | None, bool]:
            values = [getattr(usage, name) for usage in usages]
            known = [value for value in values if isinstance(value, int)]
            return (sum(known) if known else None, len(known) != len(values))

        input_tokens, input_incomplete = aggregate_optional("input_tokens")
        output_tokens, output_incomplete = aggregate_optional("output_tokens")
        total_tokens, total_incomplete = aggregate_optional("total_tokens")
        amounts = [usage.amount for usage in usages]
        currencies = {usage.currency for usage in usages if usage.currency is not None}
        amount = None
        currency = None
        amount_incomplete = any(value is None for value in amounts) or len(currencies) != 1
        if not amount_incomplete:
            amount = sum((value for value in amounts if value is not None), Decimal())
            currency = next(iter(currencies))
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            amount=amount,
            currency=currency,
            incomplete=any(usage.incomplete for usage in usages)
            or input_incomplete
            or output_incomplete
            or total_incomplete
            or amount_incomplete,
        )


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_no: int
    provider: ProviderId
    model_id: str
    started_at: datetime
    duration_ms: float
    outcome: AttemptOutcome
    error_code: ErrorCode | None = None
    provider_request_id: str | None = None
    usage: ModelUsage | None = None
    fallback_reason: ErrorCode | None = None

    def __post_init__(self) -> None:
        if self.attempt_no <= 0 or self.duration_ms < 0:
            raise ValueError("attempt number and duration are invalid")
        if self.started_at.tzinfo is None:
            raise ValueError("attempt timestamp must be timezone-aware")
        if self.outcome is AttemptOutcome.FAILED and self.error_code is None:
            raise ValueError("failed attempt requires an error code")
        if self.outcome is AttemptOutcome.CANCELLED and self.error_code is not ErrorCode.CANCELLED:
            raise ValueError("cancelled attempt requires the cancelled error code")
        if self.outcome is AttemptOutcome.SUCCEEDED and self.error_code is not None:
            raise ValueError("successful attempt cannot contain an error code")

    @classmethod
    def started_now(
        cls,
        *,
        attempt_no: int,
        target: ModelTarget,
        duration_ms: float,
        outcome: AttemptOutcome,
        error_code: ErrorCode | None = None,
        provider_request_id: str | None = None,
        usage: ModelUsage | None = None,
        fallback_reason: ErrorCode | None = None,
    ) -> Self:
        return cls(
            attempt_no=attempt_no,
            provider=target.provider,
            model_id=target.model_id,
            started_at=datetime.now(UTC),
            duration_ms=duration_ms,
            outcome=outcome,
            error_code=error_code,
            provider_request_id=provider_request_id,
            usage=usage,
            fallback_reason=fallback_reason,
        )


_RETRYABLE_CODES = frozenset({ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE})
_FALLBACK_CODES = _RETRYABLE_CODES | {ErrorCode.CIRCUIT_OPEN}


class ModelError(Exception):
    """A safe, stable model failure that contains no provider response body."""

    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str,
        request_id: str,
        correlation_id: str | None = None,
        provider_request_id: str | None = None,
        retryable: bool | None = None,
        fallback_allowed: bool | None = None,
        usage: ModelUsage | None = None,
        attempts: tuple[AttemptRecord, ...] = (),
    ) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.provider_request_id = provider_request_id
        self.retryable = code in _RETRYABLE_CODES if retryable is None else retryable
        self.fallback_allowed = (
            code in _FALLBACK_CODES if fallback_allowed is None else fallback_allowed
        )
        self.usage = usage
        self.attempts = tuple(attempts)
        super().__init__(f"{code.value}: {message} [request_id={request_id}]")

    def with_attempts(self, attempts: tuple[AttemptRecord, ...]) -> ModelError:
        return ModelError(
            code=self.code,
            message=self.message,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            provider_request_id=self.provider_request_id,
            retryable=self.retryable,
            fallback_allowed=self.fallback_allowed,
            usage=self.usage,
            attempts=attempts,
        )


@dataclass(frozen=True, slots=True)
class ModelResponse:
    request_id: str
    provider: ProviderId
    model_id: str
    content: tuple[TextPart, ...]
    finish_reason: FinishReason
    usage: ModelUsage | None
    total_usage: ModelUsage
    attempts: tuple[AttemptRecord, ...]
    provider_request_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    structured_output: object | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", tuple(self.content))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "attempts", tuple(self.attempts))
        if not self.attempts:
            raise ValueError("model response requires at least one attempt")
        if self.attempts[-1].outcome is not AttemptOutcome.SUCCEEDED:
            raise ValueError("model response requires a successful final attempt")
        if (
            self.attempts[-1].provider != self.provider
            or self.attempts[-1].model_id != self.model_id
        ):
            raise ValueError("model response target must match the final attempt")
        if not self.content and not self.tool_calls and self.structured_output is None:
            raise ValueError("model response has no usable content")
        if self.tool_calls and self.finish_reason is not FinishReason.TOOL_CALLS:
            raise ValueError("tool calls require a tool_calls finish reason")
        if self.finish_reason is FinishReason.TOOL_CALLS and not self.tool_calls:
            raise ValueError("tool_calls finish reason requires at least one tool call")
        if self.tool_calls and self.structured_output is not None:
            raise ValueError("tool calls and structured output cannot be mixed")


@dataclass(frozen=True, slots=True)
class StreamTerminal:
    response: ModelResponse


@dataclass(frozen=True, slots=True)
class ModelChunk:
    sequence: int
    kind: ChunkKind
    text_delta: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage: ModelUsage | None = None
    terminal: StreamTerminal | None = None
    error: ModelError | None = None

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("chunk sequence must be non-negative")
        payloads = (
            self.text_delta,
            self.tool_call_delta,
            self.usage,
            self.terminal,
            self.error,
        )
        if sum(value is not None for value in payloads) != 1:
            raise ValueError("model chunk must have exactly one payload")
        expected_payload = {
            ChunkKind.TEXT_DELTA: self.text_delta,
            ChunkKind.TOOL_CALL_DELTA: self.tool_call_delta,
            ChunkKind.USAGE: self.usage,
            ChunkKind.TERMINAL: self.terminal,
            ChunkKind.ERROR: self.error,
        }[self.kind]
        if expected_payload is None:
            raise ValueError("model chunk kind does not match its payload")
