"""Immutable Worker lease grants, authority results, and recovery observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import (
    LeaseAttemptNo,
    LeaseClaimId,
    LeaseDurationSeconds,
    LeaseToken,
    LeaseVersion,
    WorkerId,
)

LEASE_RECORD_FORMAT_VERSION = 1
LEASE_CLAIM_RECEIPT_RECORD_FORMAT_VERSION = 1


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an aware UTC datetime")


def renew_by_at(captured_at: datetime, duration: LeaseDurationSeconds) -> datetime:
    """Return captured DB time plus one-third duration, floored to a microsecond."""

    _require_utc("captured_at", captured_at)
    if not isinstance(duration, LeaseDurationSeconds):
        raise TypeError("duration must be LeaseDurationSeconds")
    microseconds = duration.value * 1_000_000 // 3
    return captured_at + timedelta(microseconds=microseconds)


@dataclass(frozen=True, slots=True)
class LeaseAuthorityProof:
    tenant_id: TenantId
    run_id: RunId
    worker_id: WorkerId
    claim_id: LeaseClaimId
    attempt_no: LeaseAttemptNo
    token: LeaseToken

    def __post_init__(self) -> None:
        expected = (
            ("tenant_id", self.tenant_id, TenantId),
            ("run_id", self.run_id, RunId),
            ("worker_id", self.worker_id, WorkerId),
            ("claim_id", self.claim_id, LeaseClaimId),
            ("attempt_no", self.attempt_no, LeaseAttemptNo),
            ("token", self.token, LeaseToken),
        )
        for name, value, value_type in expected:
            if not isinstance(value, value_type):
                raise TypeError(f"{name} must be {value_type.__name__}")


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    tenant_id: TenantId
    run_id: RunId
    worker_id: WorkerId
    claim_id: LeaseClaimId
    token: LeaseToken
    attempt_no: LeaseAttemptNo
    lease_version: LeaseVersion
    duration: LeaseDurationSeconds
    acquired_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    renew_by_at: datetime
    currently_authoritative: bool

    def __post_init__(self) -> None:
        LeaseAuthorityProof(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            worker_id=self.worker_id,
            claim_id=self.claim_id,
            attempt_no=self.attempt_no,
            token=self.token,
        )
        if not isinstance(self.lease_version, LeaseVersion):
            raise TypeError("lease_version must be LeaseVersion")
        if not isinstance(self.duration, LeaseDurationSeconds):
            raise TypeError("duration must be LeaseDurationSeconds")
        for name in ("acquired_at", "heartbeat_at", "lease_expires_at", "renew_by_at"):
            _require_utc(name, getattr(self, name))
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at must not precede acquired_at")
        if self.lease_expires_at <= self.heartbeat_at:
            raise ValueError("lease_expires_at must follow heartbeat_at")
        if not self.heartbeat_at < self.renew_by_at < self.lease_expires_at:
            raise ValueError("renew_by_at must be within the current lease interval")
        if type(self.currently_authoritative) is not bool:
            raise TypeError("currently_authoritative must be bool")

    @property
    def may_start_new_work(self) -> bool:
        return self.currently_authoritative

    @property
    def proof(self) -> LeaseAuthorityProof:
        return LeaseAuthorityProof(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            worker_id=self.worker_id,
            claim_id=self.claim_id,
            attempt_no=self.attempt_no,
            token=self.token,
        )


class LeaseClaimOutcomeCode(StrEnum):
    CLAIMED = "claimed"
    NO_WORK = "no_work"


@dataclass(frozen=True, slots=True)
class LeaseClaimOutcome:
    code: LeaseClaimOutcomeCode
    grant: LeaseGrant | None
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, LeaseClaimOutcomeCode):
            raise TypeError("code must be LeaseClaimOutcomeCode")
        if self.code is LeaseClaimOutcomeCode.CLAIMED:
            if not isinstance(self.grant, LeaseGrant):
                raise TypeError("claimed outcome requires LeaseGrant")
        elif self.grant is not None:
            raise ValueError("no_work outcome cannot contain a grant")
        if type(self.replayed) is not bool:
            raise TypeError("replayed must be bool")

    @classmethod
    def claimed(cls, grant: LeaseGrant, *, replayed: bool = False) -> LeaseClaimOutcome:
        return cls(code=LeaseClaimOutcomeCode.CLAIMED, grant=grant, replayed=replayed)

    @classmethod
    def no_work(cls, *, replayed: bool = False) -> LeaseClaimOutcome:
        return cls(code=LeaseClaimOutcomeCode.NO_WORK, grant=None, replayed=replayed)

    @property
    def may_start_new_work(self) -> bool:
        return self.grant is not None and self.grant.currently_authoritative


@dataclass(frozen=True, slots=True)
class LeaseAuthority:
    authoritative: bool
    reason: WorkerLeaseErrorCode | None
    lease_version: LeaseVersion | None = None
    acquired_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.authoritative) is not bool:
            raise TypeError("authoritative must be bool")
        if self.authoritative:
            if self.reason is not None:
                raise ValueError("authoritative result cannot contain a failure reason")
        elif self.reason not in {
            WorkerLeaseErrorCode.LEASE_NOT_CURRENT,
            WorkerLeaseErrorCode.LEASE_EXPIRED,
        }:
            raise ValueError("non-authoritative result requires a safe lease reason")
        optional = (self.lease_version, self.acquired_at, self.heartbeat_at, self.lease_expires_at)
        if any(value is not None for value in optional):
            if not isinstance(self.lease_version, LeaseVersion):
                raise TypeError("lease_version must be LeaseVersion")
            for name in ("acquired_at", "heartbeat_at", "lease_expires_at"):
                value = getattr(self, name)
                if value is None:
                    raise ValueError("authority timestamps must be complete")
                _require_utc(name, value)
            assert self.acquired_at is not None
            assert self.heartbeat_at is not None
            assert self.lease_expires_at is not None
            if not self.acquired_at <= self.heartbeat_at <= self.lease_expires_at:
                raise ValueError("authority timestamps are inconsistent")

    @classmethod
    def current(
        cls,
        *,
        lease_version: LeaseVersion,
        acquired_at: datetime,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> LeaseAuthority:
        return cls(
            authoritative=True,
            reason=None,
            lease_version=lease_version,
            acquired_at=acquired_at,
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
        )

    @classmethod
    def not_current(cls) -> LeaseAuthority:
        return cls(authoritative=False, reason=WorkerLeaseErrorCode.LEASE_NOT_CURRENT)

    @classmethod
    def expired(
        cls,
        *,
        lease_version: LeaseVersion,
        acquired_at: datetime,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> LeaseAuthority:
        return cls(
            authoritative=False,
            reason=WorkerLeaseErrorCode.LEASE_EXPIRED,
            lease_version=lease_version,
            acquired_at=acquired_at,
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
        )

    @property
    def may_start_new_work(self) -> bool:
        return self.authoritative


@dataclass(frozen=True, slots=True)
class ConditionalLeaseOutcome:
    applied: bool
    authority: LeaseAuthority
    renew_by_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.applied) is not bool:
            raise TypeError("applied must be bool")
        if not isinstance(self.authority, LeaseAuthority):
            raise TypeError("authority must be LeaseAuthority")
        if self.renew_by_at is not None:
            _require_utc("renew_by_at", self.renew_by_at)

    @property
    def may_start_new_work(self) -> bool:
        return self.applied and self.authority.authoritative


class InactiveRunningReason(StrEnum):
    EXPIRED = "expired"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class InactiveRunningLease:
    tenant_id: TenantId
    run_id: RunId
    attempt_no: LeaseAttemptNo
    lease_version: LeaseVersion
    acquired_at: datetime
    heartbeat_at: datetime
    authority_ended_at: datetime
    reason: InactiveRunningReason

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.run_id, RunId):
            raise TypeError("run_id must be RunId")
        if not isinstance(self.attempt_no, LeaseAttemptNo):
            raise TypeError("attempt_no must be LeaseAttemptNo")
        if not isinstance(self.lease_version, LeaseVersion):
            raise TypeError("lease_version must be LeaseVersion")
        for name in ("acquired_at", "heartbeat_at", "authority_ended_at"):
            _require_utc(name, getattr(self, name))
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at must not precede acquired_at")
        if self.authority_ended_at < self.acquired_at:
            raise ValueError("authority_ended_at must not precede acquired_at")
        if not isinstance(self.reason, InactiveRunningReason):
            raise TypeError("reason must be InactiveRunningReason")


@dataclass(frozen=True, slots=True)
class InactiveRunningCursor:
    tenant_id: TenantId
    as_of: datetime
    last_authority_ended_at: datetime
    last_run_id: RunId

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.last_run_id, RunId):
            raise TypeError("last_run_id must be RunId")
        _require_utc("as_of", self.as_of)
        _require_utc("last_authority_ended_at", self.last_authority_ended_at)
        if self.last_authority_ended_at > self.as_of:
            raise ValueError("cursor key cannot be after as_of")


@dataclass(frozen=True, slots=True)
class InactiveRunningPage:
    items: tuple[InactiveRunningLease, ...]
    next_cursor: InactiveRunningCursor | None

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, InactiveRunningLease) for item in self.items
        ):
            raise TypeError("items must be a tuple of InactiveRunningLease")
        if self.next_cursor is not None and not isinstance(self.next_cursor, InactiveRunningCursor):
            raise TypeError("next_cursor must be InactiveRunningCursor")
