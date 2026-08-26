"""Immutable commands for the run lifecycle application boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar

from zhiyi.domain.runs.budget import BudgetCharge, RunBudget, canonical_decimal
from zhiyi.domain.runs.events import RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentVersionRef,
    CommandId,
    CorrelationId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.runs.results import RunResultDraft, SafeRunError

CanonicalValue = str | int | bool | None | list["CanonicalValue"] | dict[str, "CanonicalValue"]


def _canonicalize(value: object) -> CanonicalValue:
    if value is None or type(value) in {str, int, bool}:
        return value  # type: ignore[return-value]
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (TenantId, RunId, TaskId, ReferenceId, CorrelationId)):
        return str(value)
    if isinstance(value, AgentVersionRef):
        return {
            "agent_id": str(value.agent_id),
            "build_digest": value.build_digest,
            "tenant_id": str(value.tenant_id),
            "version_id": str(value.version_id),
        }
    if isinstance(value, RunBudget):
        return {
            "currency": value.currency,
            "deadline_at": value.deadline_at.isoformat(),
            "max_cost": canonical_decimal(value.max_cost),
            "max_input_tokens": value.max_input_tokens,
            "max_model_calls": value.max_model_calls,
            "max_output_tokens": value.max_output_tokens,
            "max_steps": value.max_steps,
            "max_tool_calls": value.max_tool_calls,
            "max_total_tokens": value.max_total_tokens,
        }
    if isinstance(value, BudgetCharge):
        return {
            "charge_id": str(value.charge_id),
            "cost": canonical_decimal(value.cost),
            "input_tokens": value.input_tokens,
            "model_calls": value.model_calls,
            "output_tokens": value.output_tokens,
            "steps": value.steps,
            "tool_calls": value.tool_calls,
        }
    if isinstance(value, RunResultDraft):
        return {
            "answer": value.answer,
            "approval_ids": [str(item) for item in value.approval_ids],
            "artifact_ids": [str(item) for item in value.artifact_ids],
            "citation_ids": [str(item) for item in value.citation_ids],
            "correlation_id": (
                str(value.correlation_id) if value.correlation_id is not None else None
            ),
            "warning_codes": list(value.warning_codes),
        }
    if isinstance(value, SafeRunError):
        return {
            "code": value.code.value,
            "correlation_id": (
                str(value.correlation_id) if value.correlation_id is not None else None
            ),
            "limit_dimension": (
                value.limit_dimension.value if value.limit_dimension is not None else None
            ),
        }
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        if not all(type(key) is str for key in value):
            raise TypeError("canonical mappings require string keys")
        return {key: _canonicalize(nested) for key, nested in value.items()}
    raise TypeError("unsupported command intent value")


def _fingerprint(body: dict[str, object]) -> str:
    encoded = json.dumps(
        _canonicalize(body),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_envelope(
    tenant_id: TenantId,
    command_id: CommandId,
    expected_version: int,
) -> None:
    if not isinstance(tenant_id, TenantId):
        raise TypeError("tenant_id must be TenantId")
    if not isinstance(command_id, CommandId):
        raise TypeError("command_id must be CommandId")
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CreateRunCommand:
    tenant_id: TenantId
    command_id: CommandId
    task_id: TaskId
    agent_version: AgentVersionRef
    budget: RunBudget
    correlation_id: CorrelationId | None = None
    expected_version: int = field(default=0, init=False)

    command_type: ClassVar[str] = "create_run"
    target_status: ClassVar[RunStatus] = RunStatus.QUEUED

    def __post_init__(self) -> None:
        _validate_envelope(self.tenant_id, self.command_id, self.expected_version)
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be TaskId")
        if not isinstance(self.agent_version, AgentVersionRef):
            raise TypeError("agent_version must be AgentVersionRef")
        if self.agent_version.tenant_id != self.tenant_id:
            raise ValueError("agent version tenant must match command tenant")
        if not isinstance(self.budget, RunBudget):
            raise TypeError("budget must be RunBudget")
        if self.correlation_id is not None and not isinstance(self.correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")

    @property
    def intent_fingerprint(self) -> str:
        return _fingerprint(
            {
                "agent_version": self.agent_version,
                "budget": self.budget,
                "command_type": self.command_type,
                "task_id": self.task_id,
                "tenant_id": self.tenant_id,
            }
        )


@dataclass(frozen=True, slots=True)
class _ExistingRunCommand:
    tenant_id: TenantId
    command_id: CommandId
    run_id: RunId
    expected_version: int

    command_type: ClassVar[str]
    target_status: ClassVar[RunStatus | None] = None

    def __post_init__(self) -> None:
        _validate_envelope(self.tenant_id, self.command_id, self.expected_version)
        if not isinstance(self.run_id, RunId):
            raise TypeError("run_id must be RunId")

    def _business_payload(self) -> dict[str, object]:
        return {}

    @property
    def intent_fingerprint(self) -> str:
        return _fingerprint(
            {
                "command_type": self.command_type,
                "payload": self._business_payload(),
                "run_id": self.run_id,
                "tenant_id": self.tenant_id,
            }
        )


@dataclass(frozen=True, slots=True)
class StartRunCommand(_ExistingRunCommand):
    command_type: ClassVar[str] = "start_run"
    target_status: ClassVar[RunStatus] = RunStatus.RUNNING


@dataclass(frozen=True, slots=True)
class WaitForApprovalCommand(_ExistingRunCommand):
    reference_id: ReferenceId

    command_type: ClassVar[str] = "wait_for_approval"
    target_status: ClassVar[RunStatus] = RunStatus.WAITING_APPROVAL

    def __post_init__(self) -> None:
        super(WaitForApprovalCommand, self).__post_init__()
        if not isinstance(self.reference_id, ReferenceId):
            raise TypeError("reference_id must be ReferenceId")

    def _business_payload(self) -> dict[str, object]:
        return {"reference_id": self.reference_id}


@dataclass(frozen=True, slots=True)
class WaitForResolutionCommand(_ExistingRunCommand):
    reference_id: ReferenceId

    command_type: ClassVar[str] = "wait_for_resolution"
    target_status: ClassVar[RunStatus] = RunStatus.WAITING_RESOLUTION

    def __post_init__(self) -> None:
        super(WaitForResolutionCommand, self).__post_init__()
        if not isinstance(self.reference_id, ReferenceId):
            raise TypeError("reference_id must be ReferenceId")

    def _business_payload(self) -> dict[str, object]:
        return {"reference_id": self.reference_id}


@dataclass(frozen=True, slots=True)
class ResumeRunCommand(_ExistingRunCommand):
    command_type: ClassVar[str] = "resume_run"
    target_status: ClassVar[RunStatus] = RunStatus.RUNNING


@dataclass(frozen=True, slots=True)
class CancelRunCommand(_ExistingRunCommand):
    correlation_id: CorrelationId | None = None

    command_type: ClassVar[str] = "cancel_run"
    target_status: ClassVar[RunStatus] = RunStatus.CANCELLED

    def __post_init__(self) -> None:
        super(CancelRunCommand, self).__post_init__()
        if self.correlation_id is not None and not isinstance(self.correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")


@dataclass(frozen=True, slots=True)
class ConsumeBudgetCommand(_ExistingRunCommand):
    charge: BudgetCharge

    command_type: ClassVar[str] = "consume_budget"

    def __post_init__(self) -> None:
        super(ConsumeBudgetCommand, self).__post_init__()
        if not isinstance(self.charge, BudgetCharge):
            raise TypeError("charge must be BudgetCharge")

    def _business_payload(self) -> dict[str, object]:
        return {"charge": self.charge}


@dataclass(frozen=True, slots=True)
class SucceedRunCommand(_ExistingRunCommand):
    result: RunResultDraft = field(repr=False)

    command_type: ClassVar[str] = "succeed_run"
    target_status: ClassVar[RunStatus] = RunStatus.SUCCEEDED

    def __post_init__(self) -> None:
        super(SucceedRunCommand, self).__post_init__()
        if not isinstance(self.result, RunResultDraft):
            raise TypeError("result must be RunResultDraft")

    def _business_payload(self) -> dict[str, object]:
        return {"result": self.result}


@dataclass(frozen=True, slots=True)
class FailRunCommand(_ExistingRunCommand):
    result: RunResultDraft = field(repr=False)
    error: SafeRunError = field(repr=False)

    command_type: ClassVar[str] = "fail_run"
    target_status: ClassVar[RunStatus] = RunStatus.FAILED

    def __post_init__(self) -> None:
        super(FailRunCommand, self).__post_init__()
        if not isinstance(self.result, RunResultDraft):
            raise TypeError("result must be RunResultDraft")
        if not isinstance(self.error, SafeRunError):
            raise TypeError("error must be SafeRunError")

    def _business_payload(self) -> dict[str, object]:
        return {"error": self.error, "result": self.result}


@dataclass(frozen=True, slots=True)
class EnforceDeadlineCommand(_ExistingRunCommand):
    command_type: ClassVar[str] = "enforce_deadline"
    target_status: ClassVar[RunStatus] = RunStatus.LIMIT_EXCEEDED
