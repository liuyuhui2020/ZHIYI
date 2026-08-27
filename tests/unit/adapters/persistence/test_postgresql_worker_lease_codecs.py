"""Strict format-1 Worker lease persistence codecs and metadata inventory."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from zhiyi.adapters.persistence.postgresql_worker_lease_codecs import (
    CLAIM_INTENT_FORMAT_VERSION,
    RECORD_FORMAT_VERSION,
    StoredClaimOutcome,
    WorkerLeaseClaimReceiptRecord,
    WorkerLeaseRecord,
    decode_claim_receipt,
    decode_worker_lease,
    encode_claim_receipt,
    encode_worker_lease,
)
from zhiyi.adapters.persistence.postgresql_worker_lease_schema import (
    worker_lease_claim_receipts,
    worker_leases,
)
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import (
    LeaseAttemptNo,
    LeaseClaimId,
    LeaseDurationSeconds,
    LeaseToken,
    LeaseVersion,
    WorkerId,
)

NOW = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
_NOW_MILLISECONDS = int(NOW.timestamp() * 1_000)
CLAIM_ID = LeaseClaimId(UUID(int=(_NOW_MILLISECONDS << 80) | (0x7 << 76) | (0b10 << 62) | 1))


def _lease() -> WorkerLeaseRecord:
    return WorkerLeaseRecord(
        tenant_id=TenantId("tenant-codec"),
        run_id=RunId("run-codec"),
        worker_id=WorkerId("worker-codec"),
        claim_id=CLAIM_ID,
        token_digest=b"d" * 32,
        attempt_no=LeaseAttemptNo(2),
        lease_version=LeaseVersion(5),
        duration=LeaseDurationSeconds(30),
        acquired_at=NOW,
        heartbeat_at=NOW + timedelta(seconds=5),
        lease_expires_at=NOW + timedelta(seconds=35),
        released_at=None,
    )


def _claimed_receipt() -> WorkerLeaseClaimReceiptRecord:
    return WorkerLeaseClaimReceiptRecord(
        tenant_id=TenantId("tenant-codec"),
        claim_id=CLAIM_ID,
        claim_issued_at=NOW,
        replay_expires_at=NOW + timedelta(hours=24),
        worker_id=WorkerId("worker-codec"),
        duration=LeaseDurationSeconds(30),
        intent_fingerprint="sha256:" + "a" * 64,
        outcome=StoredClaimOutcome.CLAIMED,
        run_id=RunId("run-codec"),
        attempt_no=LeaseAttemptNo(2),
        initial_lease_version=LeaseVersion(5),
        lease_acquired_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
        replay_token=LeaseToken(b"r" * 32),
        created_at=NOW,
    )


def _no_work_receipt() -> WorkerLeaseClaimReceiptRecord:
    return WorkerLeaseClaimReceiptRecord(
        tenant_id=TenantId("tenant-codec"),
        claim_id=CLAIM_ID,
        claim_issued_at=NOW,
        replay_expires_at=NOW + timedelta(hours=24),
        worker_id=WorkerId("worker-codec"),
        duration=LeaseDurationSeconds(10),
        intent_fingerprint="sha256:" + "b" * 64,
        outcome=StoredClaimOutcome.NO_WORK,
        run_id=None,
        attempt_no=None,
        initial_lease_version=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        replay_token=None,
        created_at=NOW,
    )


def test_worker_lease_format_1_round_trip_is_lossless() -> None:
    record = _lease()
    encoded = encode_worker_lease(record)

    assert encoded["record_format_version"] == RECORD_FORMAT_VERSION == 1
    assert encoded["claim_id"] == CLAIM_ID.value
    assert encoded["token_digest"] == b"d" * 32
    assert encoded["attempt_no"] == 2
    assert encoded["lease_version"] == 5
    assert encoded["duration_seconds"] == 30
    assert decode_worker_lease(encoded) == record


@pytest.mark.parametrize("factory", [_claimed_receipt, _no_work_receipt])
def test_claimed_and_no_work_receipt_format_1_round_trip_is_lossless(
    factory: object,
) -> None:
    record = factory()  # type: ignore[operator]
    encoded = encode_claim_receipt(record)

    assert encoded["record_format_version"] == RECORD_FORMAT_VERSION == 1
    assert encoded["intent_format_version"] == CLAIM_INTENT_FORMAT_VERSION == 1
    assert decode_claim_receipt(encoded) == record


def test_claimed_projection_contains_every_result_while_no_work_is_all_null() -> None:
    claimed = encode_claim_receipt(_claimed_receipt())
    no_work = encode_claim_receipt(_no_work_receipt())
    result_columns = {
        "run_id",
        "attempt_no",
        "initial_lease_version",
        "lease_acquired_at",
        "lease_expires_at",
        "replay_token",
    }

    assert all(claimed[column] is not None for column in result_columns)
    assert all(no_work[column] is None for column in result_columns)


def test_record_repr_never_prints_raw_token_digest_or_replay_token() -> None:
    digest = b"DIGEST-SENTINEL".ljust(32, b"!")
    token = b"TOKEN-SENTINEL".ljust(32, b"!")
    lease = replace(_lease(), token_digest=digest)
    receipt = replace(_claimed_receipt(), replay_token=LeaseToken(token))
    printable = f"{lease!s} {lease!r} {receipt!s} {receipt!r}"

    assert digest.hex() not in printable
    assert token.hex() not in printable
    assert "DIGEST-SENTINEL" not in printable
    assert "TOKEN-SENTINEL" not in printable


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_format_version", 0),
        ("token_digest", b"x" * 31),
        ("attempt_no", 0),
        ("lease_version", True),
        ("duration_seconds", 31),
        ("heartbeat_at", datetime(2026, 8, 27)),
        ("lease_expires_at", NOW),
        ("claim_id", UUID("0198f1c1-8c80-4000-8000-000000000001")),
    ],
)
def test_malformed_lease_projection_is_data_corruption(field: str, value: object) -> None:
    encoded = dict(encode_worker_lease(_lease()))
    encoded[field] = value

    with pytest.raises(WorkerLeaseError) as caught:
        decode_worker_lease(encoded)

    assert caught.value.code is WorkerLeaseErrorCode.DATA_CORRUPTION


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_format_version", 0),
        ("intent_format_version", 2),
        ("duration_seconds", 9),
        ("intent_fingerprint", "secret"),
        ("outcome", "pending"),
        ("attempt_no", 0),
        ("initial_lease_version", True),
        ("replay_token", b"x" * 31),
        ("claim_issued_at", datetime(2026, 8, 27)),
        ("replay_expires_at", NOW + timedelta(hours=25)),
    ],
)
def test_malformed_claim_receipt_projection_is_data_corruption(
    field: str,
    value: object,
) -> None:
    encoded = dict(encode_claim_receipt(_claimed_receipt()))
    encoded[field] = value

    with pytest.raises(WorkerLeaseError) as caught:
        decode_claim_receipt(encoded)

    assert caught.value.code is WorkerLeaseErrorCode.DATA_CORRUPTION


def test_incomplete_claimed_and_nonempty_no_work_projections_are_corruption() -> None:
    claimed = dict(encode_claim_receipt(_claimed_receipt()))
    claimed["replay_token"] = None
    no_work = dict(encode_claim_receipt(_no_work_receipt()))
    no_work["run_id"] = "run-leak"

    for encoded in (claimed, no_work):
        with pytest.raises(WorkerLeaseError) as caught:
            decode_claim_receipt(encoded)
        assert caught.value.code is WorkerLeaseErrorCode.DATA_CORRUPTION


def test_sqlalchemy_metadata_contains_only_the_reviewed_columns() -> None:
    assert set(worker_leases.c.keys()) == {
        "tenant_id",
        "run_id",
        "worker_id",
        "claim_id",
        "token_digest",
        "attempt_no",
        "lease_version",
        "duration_seconds",
        "acquired_at",
        "heartbeat_at",
        "lease_expires_at",
        "released_at",
        "record_format_version",
    }
    assert set(worker_lease_claim_receipts.c.keys()) == {
        "tenant_id",
        "claim_id",
        "claim_issued_at",
        "replay_expires_at",
        "worker_id",
        "duration_seconds",
        "intent_format_version",
        "intent_fingerprint",
        "outcome",
        "run_id",
        "attempt_no",
        "initial_lease_version",
        "lease_acquired_at",
        "lease_expires_at",
        "replay_token",
        "created_at",
        "record_format_version",
    }
