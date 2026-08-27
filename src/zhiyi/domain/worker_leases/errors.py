"""Stable, non-disclosing errors for the Worker lease boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from zhiyi.domain.runs.identifiers import CorrelationId


class WorkerLeaseErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_EXPIRED = "idempotency_expired"
    LEASE_NOT_CURRENT = "lease_not_current"
    LEASE_EXPIRED = "lease_expired"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    COMMIT_OUTCOME_UNKNOWN = "commit_outcome_unknown"
    DATA_CORRUPTION = "data_corruption"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"


_SAFE_MESSAGES: dict[WorkerLeaseErrorCode, str] = {
    WorkerLeaseErrorCode.INVALID_INPUT: "Worker lease input is invalid",
    WorkerLeaseErrorCode.IDEMPOTENCY_CONFLICT: "Worker lease idempotency conflict",
    WorkerLeaseErrorCode.IDEMPOTENCY_EXPIRED: "Worker lease idempotency window expired",
    WorkerLeaseErrorCode.LEASE_NOT_CURRENT: "Worker lease is not current",
    WorkerLeaseErrorCode.LEASE_EXPIRED: "Worker lease expired",
    WorkerLeaseErrorCode.STORAGE_UNAVAILABLE: "Worker lease storage is unavailable",
    WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN: ("Worker lease storage commit outcome is unknown"),
    WorkerLeaseErrorCode.DATA_CORRUPTION: "Worker lease storage data is invalid",
    WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE: ("Worker lease storage schema is incompatible"),
}


def safe_worker_lease_error_message(code: WorkerLeaseErrorCode) -> str:
    if not isinstance(code, WorkerLeaseErrorCode):
        raise TypeError("code must be WorkerLeaseErrorCode")
    return _SAFE_MESSAGES[code]


@dataclass(frozen=True, slots=True)
class WorkerLeaseErrorContext:
    correlation_id: CorrelationId | None = None

    def __post_init__(self) -> None:
        if self.correlation_id is not None and not isinstance(self.correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")


class WorkerLeaseError(Exception):
    """Public error containing only a stable code and caller correlation ID."""

    def __init__(
        self,
        code: WorkerLeaseErrorCode,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if not isinstance(code, WorkerLeaseErrorCode):
            raise TypeError("code must be WorkerLeaseErrorCode")
        self.code = code
        self.context = WorkerLeaseErrorContext(correlation_id=correlation_id)
        super().__init__(safe_worker_lease_error_message(code))

    @property
    def correlation_id(self) -> CorrelationId | None:
        return self.context.correlation_id

    def __str__(self) -> str:
        suffix = (
            f" (correlation_id={self.correlation_id})" if self.correlation_id is not None else ""
        )
        return f"{safe_worker_lease_error_message(self.code)}{suffix}"

    def __repr__(self) -> str:
        correlation_id = (
            repr(str(self.correlation_id)) if self.correlation_id is not None else "None"
        )
        return f"WorkerLeaseError(code={self.code.value!r}, correlation_id={correlation_id})"
