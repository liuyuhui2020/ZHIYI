"""Versioned, deeply immutable lifecycle events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from zhiyi.domain.runs.identifiers import (
    AgentVersionId,
    ChargeId,
    CorrelationId,
    EventId,
    ReferenceId,
    RunId,
    TenantId,
)

FrozenJsonScalar: TypeAlias = str | int | bool | None  # noqa: UP040
FrozenJsonValue: TypeAlias = (  # noqa: UP040
    FrozenJsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_RESOLUTION = "waiting_resolution"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_EXCEEDED = "limit_exceeded"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.LIMIT_EXCEEDED,
        }


class RunEventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_WAITING_APPROVAL = "run.waiting_approval"
    RUN_WAITING_RESOLUTION = "run.waiting_resolution"
    RUN_RESUMED = "run.resumed"
    RUN_BUDGET_CONSUMED = "run.budget_consumed"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_LIMIT_EXCEEDED = "run.limit_exceeded"


_COMMON_PAYLOAD_FIELDS = frozenset({"status", "run_version"})
_EVENT_PAYLOAD_FIELDS: dict[RunEventType, frozenset[str]] = {
    RunEventType.RUN_CREATED: _COMMON_PAYLOAD_FIELDS | {"agent_version_id"},
    RunEventType.RUN_STARTED: _COMMON_PAYLOAD_FIELDS | {"previous_status"},
    RunEventType.RUN_WAITING_APPROVAL: _COMMON_PAYLOAD_FIELDS | {"previous_status", "reference_id"},
    RunEventType.RUN_WAITING_RESOLUTION: _COMMON_PAYLOAD_FIELDS
    | {"previous_status", "reference_id"},
    RunEventType.RUN_RESUMED: _COMMON_PAYLOAD_FIELDS | {"previous_status"},
    RunEventType.RUN_BUDGET_CONSUMED: _COMMON_PAYLOAD_FIELDS | {"usage", "charge_id"},
    RunEventType.RUN_SUCCEEDED: _COMMON_PAYLOAD_FIELDS | {"result_version"},
    RunEventType.RUN_FAILED: _COMMON_PAYLOAD_FIELDS | {"result_version", "error_code"},
    RunEventType.RUN_CANCELLED: _COMMON_PAYLOAD_FIELDS
    | {"result_version", "error_code", "correlation_id"},
    RunEventType.RUN_LIMIT_EXCEEDED: _COMMON_PAYLOAD_FIELDS
    | {"result_version", "error_code", "limit_dimension"},
}
_REQUIRED_PAYLOAD_FIELDS: dict[RunEventType, frozenset[str]] = {
    RunEventType.RUN_CREATED: _COMMON_PAYLOAD_FIELDS | {"agent_version_id"},
    RunEventType.RUN_STARTED: _COMMON_PAYLOAD_FIELDS | {"previous_status"},
    RunEventType.RUN_WAITING_APPROVAL: _COMMON_PAYLOAD_FIELDS | {"previous_status", "reference_id"},
    RunEventType.RUN_WAITING_RESOLUTION: _COMMON_PAYLOAD_FIELDS
    | {"previous_status", "reference_id"},
    RunEventType.RUN_RESUMED: _COMMON_PAYLOAD_FIELDS | {"previous_status"},
    RunEventType.RUN_BUDGET_CONSUMED: _COMMON_PAYLOAD_FIELDS | {"usage", "charge_id"},
    RunEventType.RUN_SUCCEEDED: _COMMON_PAYLOAD_FIELDS | {"result_version"},
    RunEventType.RUN_FAILED: _COMMON_PAYLOAD_FIELDS | {"result_version", "error_code"},
    RunEventType.RUN_CANCELLED: _COMMON_PAYLOAD_FIELDS | {"result_version", "error_code"},
    RunEventType.RUN_LIMIT_EXCEEDED: _COMMON_PAYLOAD_FIELDS
    | {"result_version", "error_code", "limit_dimension"},
}
_EXPECTED_STATUS = {
    RunEventType.RUN_CREATED: RunStatus.QUEUED,
    RunEventType.RUN_STARTED: RunStatus.RUNNING,
    RunEventType.RUN_WAITING_APPROVAL: RunStatus.WAITING_APPROVAL,
    RunEventType.RUN_WAITING_RESOLUTION: RunStatus.WAITING_RESOLUTION,
    RunEventType.RUN_RESUMED: RunStatus.RUNNING,
    RunEventType.RUN_BUDGET_CONSUMED: RunStatus.RUNNING,
    RunEventType.RUN_SUCCEEDED: RunStatus.SUCCEEDED,
    RunEventType.RUN_FAILED: RunStatus.FAILED,
    RunEventType.RUN_CANCELLED: RunStatus.CANCELLED,
    RunEventType.RUN_LIMIT_EXCEEDED: RunStatus.LIMIT_EXCEEDED,
}
_EXPECTED_PREVIOUS_STATUS = {
    RunEventType.RUN_STARTED: frozenset({RunStatus.QUEUED.value}),
    RunEventType.RUN_WAITING_APPROVAL: frozenset({RunStatus.RUNNING.value}),
    RunEventType.RUN_WAITING_RESOLUTION: frozenset({RunStatus.RUNNING.value}),
    RunEventType.RUN_RESUMED: frozenset(
        {RunStatus.WAITING_APPROVAL.value, RunStatus.WAITING_RESOLUTION.value}
    ),
}
_USAGE_FIELDS = frozenset(
    {
        "cost",
        "input_tokens",
        "model_calls",
        "output_tokens",
        "steps",
        "tool_calls",
        "total_tokens",
    }
)
_CANONICAL_DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")


def _freeze_json(value: object) -> FrozenJsonValue:
    if value is None or type(value) in {str, int, bool}:
        return value  # type: ignore[return-value]
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, nested in value.items():
            if type(key) is not str:
                raise TypeError("event payload object keys must be strings")
            frozen[key] = _freeze_json(nested)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError("event payload contains an unsupported value")


def thaw_event_payload(payload: Mapping[str, FrozenJsonValue]) -> dict[str, object]:
    """Return a detached JSON-compatible mutable copy for outer adapters."""

    def thaw(value: FrozenJsonValue) -> object:
        if isinstance(value, Mapping):
            return {key: thaw(nested) for key, nested in value.items()}
        if isinstance(value, tuple):
            return [thaw(item) for item in value]
        return value

    return {key: thaw(value) for key, value in payload.items()}


def _validate_payload_schema(
    event_type: RunEventType,
    payload: Mapping[str, FrozenJsonValue],
) -> None:
    if not _REQUIRED_PAYLOAD_FIELDS[event_type].issubset(payload):
        raise ValueError("event payload is missing required fields")
    if payload["status"] != _EXPECTED_STATUS[event_type].value:
        raise ValueError("event status does not match its event type")
    run_version = payload["run_version"]
    if type(run_version) is not int or run_version < 1:
        raise ValueError("event run_version must be positive")

    previous = _EXPECTED_PREVIOUS_STATUS.get(event_type)
    if previous is not None and payload["previous_status"] not in previous:
        raise ValueError("event previous_status is invalid")
    if event_type is RunEventType.RUN_CREATED:
        agent_version_id = payload["agent_version_id"]
        if type(agent_version_id) is not str:
            raise ValueError("event agent_version_id is invalid")
        AgentVersionId(agent_version_id)
    if event_type in {
        RunEventType.RUN_WAITING_APPROVAL,
        RunEventType.RUN_WAITING_RESOLUTION,
    }:
        reference_id = payload["reference_id"]
        if type(reference_id) is not str:
            raise ValueError("event reference_id is invalid")
        ReferenceId(reference_id)
    if event_type is RunEventType.RUN_BUDGET_CONSUMED:
        charge_id = payload["charge_id"]
        if type(charge_id) is not str:
            raise ValueError("event charge_id is invalid")
        ChargeId(charge_id)
        usage = payload["usage"]
        if not isinstance(usage, Mapping) or set(usage) != _USAGE_FIELDS:
            raise ValueError("budget event usage fields are invalid")
        for name in _USAGE_FIELDS - {"cost"}:
            value = usage[name]
            if type(value) is not int or value < 0:
                raise ValueError("budget event usage counters must be non-negative")
        input_tokens = usage["input_tokens"]
        output_tokens = usage["output_tokens"]
        total_tokens = usage["total_tokens"]
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or type(total_tokens) is not int
        ):
            raise ValueError("budget event token counters must be integers")
        if total_tokens != input_tokens + output_tokens:
            raise ValueError("budget event total_tokens is inconsistent")
        cost = usage["cost"]
        if type(cost) is not str or _CANONICAL_DECIMAL_PATTERN.fullmatch(cost) is None:
            raise ValueError("budget event cost must be a canonical decimal string")
    if "result_version" in payload and payload["result_version"] != 1:
        raise ValueError("event result_version must be 1")
    expected_errors = {
        RunEventType.RUN_FAILED: "failed",
        RunEventType.RUN_CANCELLED: "cancelled",
        RunEventType.RUN_LIMIT_EXCEEDED: "budget_limit",
    }
    expected_error = expected_errors.get(event_type)
    if expected_error is not None and payload["error_code"] != expected_error:
        raise ValueError("event error_code does not match its event type")
    if "correlation_id" in payload:
        correlation_id = payload["correlation_id"]
        if type(correlation_id) is not str:
            raise ValueError("event correlation_id is invalid")
        CorrelationId(correlation_id)
    if "limit_dimension" in payload and payload["limit_dimension"] not in {
        "deadline",
        "steps",
        "model_calls",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost",
    }:
        raise ValueError("event limit_dimension is invalid")


@dataclass(frozen=True, slots=True)
class RunEvent:
    event_id: EventId
    tenant_id: TenantId
    run_id: RunId
    sequence: int
    type: RunEventType
    occurred_at: datetime
    payload_version: int
    payload: Mapping[str, FrozenJsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("event_id must be EventId")
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.run_id, RunId):
            raise TypeError("run_id must be RunId")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if not isinstance(self.type, RunEventType):
            raise TypeError("type must be RunEventType")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("occurred_at must be an aware UTC datetime")
        if self.payload_version != 1:
            raise ValueError("payload_version must be 1")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        unknown = set(self.payload) - _EVENT_PAYLOAD_FIELDS[self.type]
        if unknown:
            raise ValueError("event payload contains fields not allowed for this event type")
        frozen = _freeze_json(self.payload)
        if not isinstance(frozen, Mapping):
            raise TypeError("payload must be a JSON object")
        _validate_payload_schema(self.type, frozen)
        object.__setattr__(self, "payload", frozen)
