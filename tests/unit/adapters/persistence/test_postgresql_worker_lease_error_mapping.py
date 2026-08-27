"""Shared transaction-phase and SQLSTATE classification for Worker leases."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from zhiyi.adapters.persistence.postgresql_transaction_support import (
    StorageFailureDisposition,
    TransactionPhase,
    classify_storage_failure,
    worker_lease_storage_error,
)
from zhiyi.domain.worker_leases.errors import WorkerLeaseErrorCode


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
        (TransactionPhase.ACQUIRE, None, True, False, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.LOCK, "55P03", False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.WRITE, "57014", False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.WRITE, "40001", False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.WRITE, "40P01", False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.COMMIT, "40001", False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.COMMIT, "40P01", False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.COMMIT, "08007", True, False, StorageFailureDisposition.UNKNOWN),
        (TransactionPhase.COMMIT, "40003", False, False, StorageFailureDisposition.UNKNOWN),
        (TransactionPhase.COMMIT, None, True, False, StorageFailureDisposition.UNKNOWN),
        (TransactionPhase.COMMIT, None, False, True, StorageFailureDisposition.UNAVAILABLE),
        (TransactionPhase.WRITE, "08006", True, False, StorageFailureDisposition.UNAVAILABLE),
    ],
)
def test_storage_failure_uses_phase_sqlstate_and_confirmed_rollback(
    phase: TransactionPhase,
    sqlstate: str | None,
    invalidated: bool,
    rollback_confirmed: bool,
    expected: StorageFailureDisposition,
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


def test_worker_error_maps_only_the_shared_storage_disposition() -> None:
    unavailable = worker_lease_storage_error(StorageFailureDisposition.UNAVAILABLE)
    unknown = worker_lease_storage_error(StorageFailureDisposition.UNKNOWN)

    assert unavailable.code is WorkerLeaseErrorCode.STORAGE_UNAVAILABLE
    assert unknown.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN


def test_localized_driver_message_never_changes_classification_or_public_error() -> None:
    sentinel = "密码 SELECT replay_token postgresql://admin:secret@host/db"
    error = ConnectionError(sentinel)
    disposition = classify_storage_failure(
        error,
        phase=TransactionPhase.COMMIT,
        rollback_confirmed=False,
    )
    public = worker_lease_storage_error(disposition)

    assert disposition is StorageFailureDisposition.UNKNOWN
    assert sentinel not in str(public)
    assert sentinel not in repr(public)
