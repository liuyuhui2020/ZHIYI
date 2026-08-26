"""Transaction-phase and SQLSTATE failure-classification tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from zhiyi.adapters.persistence.postgresql_run_repository import (
    TransactionPhase,
    classify_storage_failure,
)
from zhiyi.application.ports.run_repository import RunRepositoryErrorCode


@dataclass
class _DriverError(Exception):
    sqlstate: str | None = None


@dataclass
class _DatabaseError(Exception):
    orig: BaseException
    connection_invalidated: bool = False


@pytest.mark.parametrize(
    ("phase", "sqlstate", "invalidated", "rollback_confirmed", "expected"),
    [
        (TransactionPhase.ACQUIRE, None, True, False, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (TransactionPhase.LOCK, "55P03", False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (TransactionPhase.WRITE, "57014", False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (TransactionPhase.WRITE, "40001", False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (TransactionPhase.WRITE, "40P01", False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (TransactionPhase.COMMIT, "40001", False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (TransactionPhase.COMMIT, "40P01", False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
        (
            TransactionPhase.COMMIT,
            "08007",
            True,
            False,
            RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN,
        ),
        (
            TransactionPhase.COMMIT,
            "40003",
            False,
            False,
            RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN,
        ),
        (TransactionPhase.COMMIT, None, True, False, RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN),
        (TransactionPhase.COMMIT, None, False, True, RunRepositoryErrorCode.STORAGE_UNAVAILABLE),
    ],
)
def test_storage_failure_classification(
    phase: TransactionPhase,
    sqlstate: str | None,
    invalidated: bool,
    rollback_confirmed: bool,
    expected: RunRepositoryErrorCode,
) -> None:
    error = _DatabaseError(_DriverError(sqlstate), connection_invalidated=invalidated)
    assert (
        classify_storage_failure(
            error,
            phase=phase,
            rollback_confirmed=rollback_confirmed,
        )
        is expected
    )


def test_unknown_status_never_guesses_a_domain_conflict() -> None:
    error = ConnectionError("postgresql://user:do-not-leak@db")
    assert (
        classify_storage_failure(
            error,
            phase=TransactionPhase.COMMIT,
            rollback_confirmed=False,
        )
        is RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN
    )
