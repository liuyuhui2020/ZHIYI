"""Stable, non-disclosing errors for the run lifecycle boundary."""

from __future__ import annotations

from enum import StrEnum

from zhiyi.domain.runs.identifiers import CorrelationId


class RunErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    ILLEGAL_TRANSITION = "illegal_transition"
    VERSION_CONFLICT = "version_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    BUDGET_LIMIT = "budget_limit"
    INVARIANT_VIOLATION = "invariant_violation"
    CANCELLED = "cancelled"
    FAILED = "failed"


_SAFE_MESSAGES: dict[RunErrorCode, str] = {
    RunErrorCode.INVALID_INPUT: "Run input is invalid",
    RunErrorCode.NOT_FOUND: "Run was not found",
    RunErrorCode.ILLEGAL_TRANSITION: "Run transition is not allowed",
    RunErrorCode.VERSION_CONFLICT: "Run version conflict",
    RunErrorCode.IDEMPOTENCY_CONFLICT: "Run idempotency conflict",
    RunErrorCode.BUDGET_LIMIT: "Run budget limit was reached",
    RunErrorCode.INVARIANT_VIOLATION: "Run invariant was violated",
    RunErrorCode.CANCELLED: "Run was cancelled",
    RunErrorCode.FAILED: "Run failed",
}


def safe_error_message(code: RunErrorCode) -> str:
    """Return the static public message for a stable error code."""

    return _SAFE_MESSAGES[code]


class RunLifecycleError(Exception):
    """A public domain/application error that never echoes caller data."""

    def __init__(
        self,
        code: RunErrorCode,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if not isinstance(code, RunErrorCode):
            raise TypeError("code must be RunErrorCode")
        if correlation_id is not None and not isinstance(correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")
        self.code = code
        self.correlation_id = correlation_id
        super().__init__(safe_error_message(code))

    def __str__(self) -> str:
        suffix = (
            f" (correlation_id={self.correlation_id})" if self.correlation_id is not None else ""
        )
        return f"{safe_error_message(self.code)}{suffix}"

    def __repr__(self) -> str:
        return (
            f"RunLifecycleError(code={self.code.value!r}, "
            f"correlation_id={str(self.correlation_id)!r})"
        )
