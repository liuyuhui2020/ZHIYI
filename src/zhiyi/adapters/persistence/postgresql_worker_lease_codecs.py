"""Strict format-1 codecs for retained leases and immutable claim receipts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast as type_cast

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
from zhiyi.domain.worker_leases.models import InactiveRunningLease, InactiveRunningReason

RECORD_FORMAT_VERSION = 1
CLAIM_INTENT_FORMAT_VERSION = 1
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class StoredClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    NO_WORK = "no_work"


def claim_id_issued_at(claim_id: LeaseClaimId) -> datetime:
    milliseconds = claim_id.value.int >> 80
    return datetime.fromtimestamp(milliseconds / 1_000, tz=UTC)


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True, repr=False)
class WorkerLeaseRecord:
    tenant_id: TenantId
    run_id: RunId
    worker_id: WorkerId
    claim_id: LeaseClaimId
    token_digest: bytes
    attempt_no: LeaseAttemptNo
    lease_version: LeaseVersion
    duration: LeaseDurationSeconds
    acquired_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    released_at: datetime | None

    def __post_init__(self) -> None:
        for name, value, expected_type in (
            ("tenant_id", self.tenant_id, TenantId),
            ("run_id", self.run_id, RunId),
            ("worker_id", self.worker_id, WorkerId),
            ("claim_id", self.claim_id, LeaseClaimId),
            ("attempt_no", self.attempt_no, LeaseAttemptNo),
            ("lease_version", self.lease_version, LeaseVersion),
            ("duration", self.duration, LeaseDurationSeconds),
        ):
            if not isinstance(value, expected_type):
                raise TypeError(f"{name} must be {expected_type.__name__}")
        if type(self.token_digest) is not bytes or len(self.token_digest) != 32:
            raise ValueError("token_digest must contain exactly 32 bytes")
        for name in ("acquired_at", "heartbeat_at", "lease_expires_at"):
            _require_utc(name, getattr(self, name))
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at must not precede acquired_at")
        if self.lease_expires_at <= self.heartbeat_at:
            raise ValueError("lease_expires_at must follow heartbeat_at")
        if self.released_at is not None:
            _require_utc("released_at", self.released_at)
            if self.released_at < self.acquired_at:
                raise ValueError("released_at must not precede acquired_at")

    def __repr__(self) -> str:
        return (
            "WorkerLeaseRecord("
            f"tenant_id={str(self.tenant_id)!r}, run_id={str(self.run_id)!r}, "
            f"worker_id={str(self.worker_id)!r}, claim_id={str(self.claim_id)!r}, "
            f"attempt_no={self.attempt_no.value}, lease_version={self.lease_version.value})"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class WorkerLeaseClaimReceiptRecord:
    tenant_id: TenantId
    claim_id: LeaseClaimId
    claim_issued_at: datetime
    replay_expires_at: datetime
    worker_id: WorkerId
    duration: LeaseDurationSeconds
    intent_fingerprint: str
    outcome: StoredClaimOutcome
    run_id: RunId | None
    attempt_no: LeaseAttemptNo | None
    initial_lease_version: LeaseVersion | None
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    replay_token: LeaseToken | None
    created_at: datetime

    def __post_init__(self) -> None:
        for name, value, expected_type in (
            ("tenant_id", self.tenant_id, TenantId),
            ("claim_id", self.claim_id, LeaseClaimId),
            ("worker_id", self.worker_id, WorkerId),
            ("duration", self.duration, LeaseDurationSeconds),
            ("outcome", self.outcome, StoredClaimOutcome),
        ):
            if not isinstance(value, expected_type):
                raise TypeError(f"{name} must be {expected_type.__name__}")
        for name in ("claim_issued_at", "replay_expires_at", "created_at"):
            _require_utc(name, getattr(self, name))
        if self.claim_issued_at != claim_id_issued_at(self.claim_id):
            raise ValueError("claim_issued_at must match the UUIDv7 timestamp")
        if self.replay_expires_at != self.claim_issued_at + timedelta(hours=24):
            raise ValueError("replay_expires_at must be exactly 24 hours after issuance")
        if (
            type(self.intent_fingerprint) is not str
            or _FINGERPRINT_PATTERN.fullmatch(self.intent_fingerprint) is None
        ):
            raise ValueError("intent_fingerprint must be a SHA-256 digest")
        result_values = (
            self.run_id,
            self.attempt_no,
            self.initial_lease_version,
            self.lease_acquired_at,
            self.lease_expires_at,
            self.replay_token,
        )
        if self.outcome is StoredClaimOutcome.NO_WORK:
            if any(value is not None for value in result_values):
                raise ValueError("no_work receipt cannot contain claim result fields")
        else:
            if not isinstance(self.run_id, RunId):
                raise TypeError("claimed receipt run_id must be RunId")
            if not isinstance(self.attempt_no, LeaseAttemptNo):
                raise TypeError("claimed receipt attempt_no must be LeaseAttemptNo")
            if not isinstance(self.initial_lease_version, LeaseVersion):
                raise TypeError("claimed receipt initial_lease_version must be LeaseVersion")
            if not isinstance(self.replay_token, LeaseToken):
                raise TypeError("claimed receipt replay_token must be LeaseToken")
            if self.lease_acquired_at is None or self.lease_expires_at is None:
                raise ValueError("claimed receipt timestamps must be complete")
            _require_utc("lease_acquired_at", self.lease_acquired_at)
            _require_utc("lease_expires_at", self.lease_expires_at)
            if self.lease_expires_at <= self.lease_acquired_at:
                raise ValueError("claimed receipt expiry must follow acquisition")

    def __repr__(self) -> str:
        return (
            "WorkerLeaseClaimReceiptRecord("
            f"tenant_id={str(self.tenant_id)!r}, claim_id={str(self.claim_id)!r}, "
            f"worker_id={str(self.worker_id)!r}, outcome={self.outcome.value!r}, "
            f"run_id={str(self.run_id)!r})"
        )

    __str__ = __repr__


def encode_worker_lease(record: WorkerLeaseRecord) -> dict[str, object]:
    if not isinstance(record, WorkerLeaseRecord):
        raise TypeError("record must be WorkerLeaseRecord")
    return {
        "tenant_id": str(record.tenant_id),
        "run_id": str(record.run_id),
        "worker_id": str(record.worker_id),
        "claim_id": record.claim_id.value,
        "token_digest": record.token_digest,
        "attempt_no": record.attempt_no.value,
        "lease_version": record.lease_version.value,
        "duration_seconds": record.duration.value,
        "acquired_at": record.acquired_at,
        "heartbeat_at": record.heartbeat_at,
        "lease_expires_at": record.lease_expires_at,
        "released_at": record.released_at,
        "record_format_version": RECORD_FORMAT_VERSION,
    }


def encode_claim_receipt(record: WorkerLeaseClaimReceiptRecord) -> dict[str, object]:
    if not isinstance(record, WorkerLeaseClaimReceiptRecord):
        raise TypeError("record must be WorkerLeaseClaimReceiptRecord")
    return {
        "tenant_id": str(record.tenant_id),
        "claim_id": record.claim_id.value,
        "claim_issued_at": record.claim_issued_at,
        "replay_expires_at": record.replay_expires_at,
        "worker_id": str(record.worker_id),
        "duration_seconds": record.duration.value,
        "intent_format_version": CLAIM_INTENT_FORMAT_VERSION,
        "intent_fingerprint": record.intent_fingerprint,
        "outcome": record.outcome.value,
        "run_id": str(record.run_id) if record.run_id is not None else None,
        "attempt_no": record.attempt_no.value if record.attempt_no is not None else None,
        "initial_lease_version": (
            record.initial_lease_version.value if record.initial_lease_version is not None else None
        ),
        "lease_acquired_at": record.lease_acquired_at,
        "lease_expires_at": record.lease_expires_at,
        "replay_token": record.replay_token.value if record.replay_token is not None else None,
        "created_at": record.created_at,
        "record_format_version": RECORD_FORMAT_VERSION,
    }


def _require_format(record: Mapping[str, object]) -> None:
    if (
        type(record.get("record_format_version")) is not int
        or record.get("record_format_version") != RECORD_FORMAT_VERSION
    ):
        raise ValueError("unsupported record format")


def decode_worker_lease(record: Mapping[str, object]) -> WorkerLeaseRecord:
    try:
        _require_format(record)
        return WorkerLeaseRecord(
            tenant_id=TenantId(record["tenant_id"]),  # type: ignore[arg-type]
            run_id=RunId(record["run_id"]),  # type: ignore[arg-type]
            worker_id=WorkerId(record["worker_id"]),  # type: ignore[arg-type]
            claim_id=LeaseClaimId(record["claim_id"]),  # type: ignore[arg-type]
            token_digest=record["token_digest"],  # type: ignore[arg-type]
            attempt_no=LeaseAttemptNo(record["attempt_no"]),  # type: ignore[arg-type]
            lease_version=LeaseVersion(record["lease_version"]),  # type: ignore[arg-type]
            duration=LeaseDurationSeconds(record["duration_seconds"]),  # type: ignore[arg-type]
            acquired_at=record["acquired_at"],  # type: ignore[arg-type]
            heartbeat_at=record["heartbeat_at"],  # type: ignore[arg-type]
            lease_expires_at=record["lease_expires_at"],  # type: ignore[arg-type]
            released_at=record["released_at"],  # type: ignore[arg-type]
        )
    except WorkerLeaseError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION) from error


def decode_claim_receipt(
    record: Mapping[str, object],
) -> WorkerLeaseClaimReceiptRecord:
    try:
        _require_format(record)
        if (
            type(record.get("intent_format_version")) is not int
            or record.get("intent_format_version") != CLAIM_INTENT_FORMAT_VERSION
        ):
            raise ValueError("unsupported intent format")
        attempt_no = record["attempt_no"]
        initial_lease_version = record["initial_lease_version"]
        replay_token = record["replay_token"]
        return WorkerLeaseClaimReceiptRecord(
            tenant_id=TenantId(record["tenant_id"]),  # type: ignore[arg-type]
            claim_id=LeaseClaimId(record["claim_id"]),  # type: ignore[arg-type]
            claim_issued_at=record["claim_issued_at"],  # type: ignore[arg-type]
            replay_expires_at=record["replay_expires_at"],  # type: ignore[arg-type]
            worker_id=WorkerId(record["worker_id"]),  # type: ignore[arg-type]
            duration=LeaseDurationSeconds(record["duration_seconds"]),  # type: ignore[arg-type]
            intent_fingerprint=record["intent_fingerprint"],  # type: ignore[arg-type]
            outcome=StoredClaimOutcome(record["outcome"]),  # type: ignore[arg-type]
            run_id=(RunId(record["run_id"]) if record["run_id"] is not None else None),  # type: ignore[arg-type]
            attempt_no=(
                LeaseAttemptNo(type_cast(int, attempt_no)) if attempt_no is not None else None
            ),
            initial_lease_version=(
                LeaseVersion(type_cast(int, initial_lease_version))
                if initial_lease_version is not None
                else None
            ),
            lease_acquired_at=record["lease_acquired_at"],  # type: ignore[arg-type]
            lease_expires_at=record["lease_expires_at"],  # type: ignore[arg-type]
            replay_token=(
                LeaseToken(type_cast(bytes, replay_token)) if replay_token is not None else None
            ),
            created_at=record["created_at"],  # type: ignore[arg-type]
        )
    except WorkerLeaseError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION) from error


def decode_inactive_running(record: Mapping[str, object]) -> InactiveRunningLease:
    """Decode the deliberately minimal inactive-running recovery projection."""

    try:
        return InactiveRunningLease(
            tenant_id=TenantId(record["tenant_id"]),  # type: ignore[arg-type]
            run_id=RunId(record["run_id"]),  # type: ignore[arg-type]
            attempt_no=LeaseAttemptNo(type_cast(int, record["attempt_no"])),
            lease_version=LeaseVersion(type_cast(int, record["lease_version"])),
            acquired_at=record["acquired_at"],  # type: ignore[arg-type]
            heartbeat_at=record["heartbeat_at"],  # type: ignore[arg-type]
            authority_ended_at=record["authority_ended_at"],  # type: ignore[arg-type]
            reason=InactiveRunningReason(record["reason"]),  # type: ignore[arg-type]
        )
    except WorkerLeaseError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION) from error
