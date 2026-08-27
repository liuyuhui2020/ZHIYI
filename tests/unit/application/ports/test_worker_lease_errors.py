"""Stable, non-disclosing Worker lease error boundary."""

from __future__ import annotations

from dataclasses import fields

import pytest

from zhiyi.application.ports.worker_lease_repository import (
    WorkerLeaseError,
    WorkerLeaseErrorCode,
    safe_worker_lease_error_message,
)
from zhiyi.domain.runs.identifiers import CorrelationId

EXPECTED_MESSAGES = {
    WorkerLeaseErrorCode.INVALID_INPUT: "Worker lease input is invalid",
    WorkerLeaseErrorCode.IDEMPOTENCY_CONFLICT: "Worker lease idempotency conflict",
    WorkerLeaseErrorCode.IDEMPOTENCY_EXPIRED: "Worker lease idempotency window expired",
    WorkerLeaseErrorCode.LEASE_NOT_CURRENT: "Worker lease is not current",
    WorkerLeaseErrorCode.LEASE_EXPIRED: "Worker lease expired",
    WorkerLeaseErrorCode.STORAGE_UNAVAILABLE: "Worker lease storage is unavailable",
    WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN: ("Worker lease storage commit outcome is unknown"),
    WorkerLeaseErrorCode.DATA_CORRUPTION: "Worker lease storage data is invalid",
    WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE: "Worker lease storage schema is incompatible",
}


def test_error_codes_are_complete_and_stable() -> None:
    assert {code.value for code in WorkerLeaseErrorCode} == {
        "invalid_input",
        "idempotency_conflict",
        "idempotency_expired",
        "lease_not_current",
        "lease_expired",
        "storage_unavailable",
        "commit_outcome_unknown",
        "data_corruption",
        "schema_incompatible",
    }


@pytest.mark.parametrize(("code", "message"), EXPECTED_MESSAGES.items())
def test_public_messages_are_constant(code: WorkerLeaseErrorCode, message: str) -> None:
    error = WorkerLeaseError(code)

    assert safe_worker_lease_error_message(code) == message
    assert str(error) == message
    assert error.args == (message,)


@pytest.mark.parametrize("code", list(WorkerLeaseErrorCode))
def test_repr_contains_only_stable_code_and_caller_correlation_id(
    code: WorkerLeaseErrorCode,
) -> None:
    error = WorkerLeaseError(code, correlation_id=CorrelationId("caller-correlation"))

    assert repr(error) == (
        f"WorkerLeaseError(code={code.value!r}, correlation_id='caller-correlation')"
    )
    assert str(error) == f"{EXPECTED_MESSAGES[code]} (correlation_id=caller-correlation)"
    assert {item.name for item in fields(error.context)} == {"correlation_id"}


def test_exception_chaining_preserves_internal_cause_without_printing_it() -> None:
    sentinel = "postgresql://admin:password@secret/db SELECT raw_token"
    cause = RuntimeError(sentinel)

    try:
        raise WorkerLeaseError(
            WorkerLeaseErrorCode.STORAGE_UNAVAILABLE,
            correlation_id=CorrelationId("caller-1"),
        ) from cause
    except WorkerLeaseError as error:
        assert error.__cause__ is cause
        assert sentinel not in str(error)
        assert sentinel not in repr(error)
        assert "caller-1" in repr(error)


def test_error_has_no_sensitive_context_fields() -> None:
    error = WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)

    assert not hasattr(error, "token")
    assert not hasattr(error, "token_digest")
    assert not hasattr(error, "intent_fingerprint")
    assert not hasattr(error, "sql")
    assert not hasattr(error, "parameters")
    assert not hasattr(error, "dsn")
    assert not hasattr(error, "worker_id")
    assert not hasattr(error, "claim_id")
    assert not hasattr(error, "run_id")
    assert not hasattr(error, "tenant_id")


@pytest.mark.parametrize("value", ["invalid_input", 1, None, True])
def test_error_code_type_is_strict(value: object) -> None:
    with pytest.raises(TypeError, match="WorkerLeaseErrorCode"):
        WorkerLeaseError(value)  # type: ignore[arg-type]


def test_correlation_id_type_is_strict() -> None:
    with pytest.raises(TypeError, match="CorrelationId"):
        WorkerLeaseError(
            WorkerLeaseErrorCode.INVALID_INPUT,
            correlation_id="caller-1",  # type: ignore[arg-type]
        )
