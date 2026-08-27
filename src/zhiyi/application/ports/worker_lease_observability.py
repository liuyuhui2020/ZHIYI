"""Safe terminal telemetry facts for Worker lease repository operations."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from zhiyi.domain.runs.identifiers import CorrelationId, RunId, TenantId
from zhiyi.domain.worker_leases.identifiers import LeaseClaimId, WorkerId


class LeaseOperation(StrEnum):
    ISSUE_CLAIM_ID = "issue_claim_id"
    CLAIM = "claim"
    GET_AUTHORITY = "get_authority"
    RENEW = "renew"
    RELEASE = "release"
    GET_INACTIVE_RUNNING = "get_inactive_running"
    LIST_INACTIVE_RUNNING = "list_inactive_running"
    COMMIT_WITH_LEASE = "commit_with_lease"


class LeaseTransactionPhase(StrEnum):
    VALIDATE = "validate"
    SCHEMA = "schema"
    ACQUIRE = "acquire"
    BEGIN = "begin"
    ARBITRATION = "arbitration"
    LOCK = "lock"
    WRITE = "write"
    COMMIT = "commit"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class LeaseOperationObservation:
    operation: LeaseOperation
    terminal_phase: LeaseTransactionPhase
    outcome_code: str
    correlation_id: CorrelationId | None
    tenant_id: TenantId | None
    run_id: RunId | None
    worker_id: WorkerId | None
    claim_id: LeaseClaimId | None
    duration_bucket: str | None
    replayed: bool
    empty: bool
    contended: bool

    def __post_init__(self) -> None:
        if not isinstance(self.operation, LeaseOperation):
            raise TypeError("operation must be LeaseOperation")
        if not isinstance(self.terminal_phase, LeaseTransactionPhase):
            raise TypeError("terminal_phase must be LeaseTransactionPhase")
        if (
            type(self.outcome_code) is not str
            or not self.outcome_code
            or len(self.outcome_code) > 64
        ):
            raise ValueError("outcome_code must be a non-empty value of at most 64 characters")
        optional_types = (
            ("correlation_id", self.correlation_id, CorrelationId),
            ("tenant_id", self.tenant_id, TenantId),
            ("run_id", self.run_id, RunId),
            ("worker_id", self.worker_id, WorkerId),
            ("claim_id", self.claim_id, LeaseClaimId),
        )
        for name, value, expected_type in optional_types:
            if value is not None and not isinstance(value, expected_type):
                raise TypeError(f"{name} must be {expected_type.__name__}")
        if self.duration_bucket is not None and (
            type(self.duration_bucket) is not str
            or not self.duration_bucket
            or len(self.duration_bucket) > 32
        ):
            raise ValueError("duration_bucket must be a bounded non-empty string")
        for name in ("replayed", "empty", "contended"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")


@runtime_checkable
class WorkerLeaseTelemetry(Protocol):
    def record_log(self, observation: LeaseOperationObservation) -> None: ...

    def record_metric(self, observation: LeaseOperationObservation) -> None: ...

    def record_trace(self, observation: LeaseOperationObservation) -> None: ...


def deliver_terminal_observation(
    telemetry: WorkerLeaseTelemetry,
    observation: LeaseOperationObservation,
) -> None:
    """Attempt every terminal channel independently after repository cleanup."""

    if not isinstance(telemetry, WorkerLeaseTelemetry):
        raise TypeError("telemetry must implement WorkerLeaseTelemetry")
    if not isinstance(observation, LeaseOperationObservation):
        raise TypeError("observation must be LeaseOperationObservation")
    for recorder in (
        telemetry.record_log,
        telemetry.record_metric,
        telemetry.record_trace,
    ):
        with suppress(Exception):
            recorder(observation)
