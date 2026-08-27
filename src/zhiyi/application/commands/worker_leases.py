"""Normalized commands for the framework-neutral Worker lease boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from zhiyi.domain.runs.identifiers import TenantId
from zhiyi.domain.worker_leases.identifiers import (
    LeaseClaimId,
    LeaseDurationSeconds,
    LeaseVersion,
    WorkerId,
)
from zhiyi.domain.worker_leases.models import LeaseAuthorityProof

CLAIM_INTENT_FORMAT_VERSION = 1


def _length_prefixed(value: bytes) -> bytes:
    return str(len(value)).encode("ascii") + b":" + value


def encode_claim_intent(
    worker_id: WorkerId,
    duration: LeaseDurationSeconds,
) -> bytes:
    """Encode complete version-1 claim intent without its tenant-scoped identity."""

    if not isinstance(worker_id, WorkerId):
        raise TypeError("worker_id must be WorkerId")
    if not isinstance(duration, LeaseDurationSeconds):
        raise TypeError("duration must be LeaseDurationSeconds")
    fields = (
        str(CLAIM_INTENT_FORMAT_VERSION).encode("ascii"),
        str(worker_id).encode("ascii"),
        str(duration.value).encode("ascii"),
    )
    return b"|".join(_length_prefixed(value) for value in fields)


@dataclass(frozen=True, slots=True)
class ClaimLeaseCommand:
    tenant_id: TenantId
    worker_id: WorkerId
    claim_id: LeaseClaimId
    duration: LeaseDurationSeconds = field(default_factory=LeaseDurationSeconds)

    intent_format_version: int = field(default=CLAIM_INTENT_FORMAT_VERSION, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.worker_id, WorkerId):
            raise TypeError("worker_id must be WorkerId")
        if not isinstance(self.claim_id, LeaseClaimId):
            raise TypeError("claim_id must be LeaseClaimId")
        if not isinstance(self.duration, LeaseDurationSeconds):
            raise TypeError("duration must be LeaseDurationSeconds")

    @property
    def normalized_intent(self) -> bytes:
        return encode_claim_intent(self.worker_id, self.duration)

    @property
    def intent_fingerprint(self) -> str:
        return f"sha256:{hashlib.sha256(self.normalized_intent).hexdigest()}"


@dataclass(frozen=True, slots=True)
class RenewLeaseCommand:
    proof: LeaseAuthorityProof
    expected_version: LeaseVersion
    duration: LeaseDurationSeconds = field(default_factory=LeaseDurationSeconds)

    def __post_init__(self) -> None:
        if not isinstance(self.proof, LeaseAuthorityProof):
            raise TypeError("proof must be LeaseAuthorityProof")
        if not isinstance(self.expected_version, LeaseVersion):
            raise TypeError("expected_version must be LeaseVersion")
        if not isinstance(self.duration, LeaseDurationSeconds):
            raise TypeError("duration must be LeaseDurationSeconds")


@dataclass(frozen=True, slots=True)
class ReleaseLeaseCommand:
    proof: LeaseAuthorityProof
    expected_version: LeaseVersion

    def __post_init__(self) -> None:
        if not isinstance(self.proof, LeaseAuthorityProof):
            raise TypeError("proof must be LeaseAuthorityProof")
        if not isinstance(self.expected_version, LeaseVersion):
            raise TypeError("expected_version must be LeaseVersion")
