"""Normalized, versioned intent contracts for Worker lease commands."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from uuid import UUID

import pytest

from zhiyi.application.commands.worker_leases import (
    CLAIM_INTENT_FORMAT_VERSION,
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
    encode_claim_intent,
)
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.identifiers import (
    LeaseAttemptNo,
    LeaseClaimId,
    LeaseDurationSeconds,
    LeaseToken,
    LeaseVersion,
    WorkerId,
)
from zhiyi.domain.worker_leases.models import LeaseAuthorityProof


def _claim_id() -> LeaseClaimId:
    return LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000001"))


def _proof() -> LeaseAuthorityProof:
    return LeaseAuthorityProof(
        tenant_id=TenantId("tenant-1"),
        run_id=RunId("run-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=_claim_id(),
        attempt_no=LeaseAttemptNo(1),
        token=LeaseToken(b"t" * 32),
    )


def test_claim_expands_default_duration_before_fingerprinting() -> None:
    implicit = ClaimLeaseCommand(
        tenant_id=TenantId("tenant-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=_claim_id(),
    )
    explicit = ClaimLeaseCommand(
        tenant_id=TenantId("tenant-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=_claim_id(),
        duration=LeaseDurationSeconds(30),
    )

    assert implicit.duration == LeaseDurationSeconds(30)
    assert implicit.intent_format_version == CLAIM_INTENT_FORMAT_VERSION == 1
    assert implicit.intent_fingerprint == explicit.intent_fingerprint
    assert implicit.normalized_intent == explicit.normalized_intent


def test_claim_intent_uses_unambiguous_length_prefixed_version_1_bytes() -> None:
    worker_id = WorkerId("worker:10")
    encoded = encode_claim_intent(worker_id, LeaseDurationSeconds(10))

    assert encoded == b"1:1|9:worker:10|2:10"
    command = ClaimLeaseCommand(
        tenant_id=TenantId("tenant-1"),
        worker_id=worker_id,
        claim_id=_claim_id(),
        duration=LeaseDurationSeconds(10),
    )
    assert command.normalized_intent == encoded
    assert command.intent_fingerprint.startswith("sha256:")
    assert len(command.intent_fingerprint) == 71


def test_tenant_and_claim_id_scope_identity_but_do_not_change_claim_intent() -> None:
    first = ClaimLeaseCommand(
        tenant_id=TenantId("tenant-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=_claim_id(),
    )
    second = ClaimLeaseCommand(
        tenant_id=TenantId("tenant-2"),
        worker_id=WorkerId("worker-1"),
        claim_id=LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000002")),
    )

    assert first.normalized_intent == second.normalized_intent
    assert first.intent_fingerprint == second.intent_fingerprint


def test_worker_or_duration_changes_claim_fingerprint() -> None:
    base = ClaimLeaseCommand(TenantId("tenant-1"), WorkerId("worker-1"), _claim_id())
    other_worker = ClaimLeaseCommand(TenantId("tenant-1"), WorkerId("worker-2"), _claim_id())
    other_duration = ClaimLeaseCommand(
        TenantId("tenant-1"),
        WorkerId("worker-1"),
        _claim_id(),
        LeaseDurationSeconds(10),
    )

    assert base.intent_fingerprint != other_worker.intent_fingerprint
    assert base.intent_fingerprint != other_duration.intent_fingerprint


def test_renew_expands_default_and_requires_an_expected_lease_version() -> None:
    implicit = RenewLeaseCommand(proof=_proof(), expected_version=LeaseVersion(1))
    explicit = RenewLeaseCommand(
        proof=_proof(),
        expected_version=LeaseVersion(1),
        duration=LeaseDurationSeconds(30),
    )

    assert implicit.duration == LeaseDurationSeconds(30)
    assert implicit == explicit


def test_release_contains_only_proof_and_expected_version() -> None:
    command = ReleaseLeaseCommand(proof=_proof(), expected_version=LeaseVersion(1))

    assert {item.name for item in fields(command)} == {"proof", "expected_version"}


@pytest.mark.parametrize(
    ("command_type", "arguments", "message"),
    [
        (
            ClaimLeaseCommand,
            {"tenant_id": "tenant-1", "worker_id": WorkerId("w"), "claim_id": _claim_id()},
            "TenantId",
        ),
        (
            ClaimLeaseCommand,
            {
                "tenant_id": TenantId("tenant-1"),
                "worker_id": "worker-1",
                "claim_id": _claim_id(),
            },
            "WorkerId",
        ),
        (
            ClaimLeaseCommand,
            {
                "tenant_id": TenantId("tenant-1"),
                "worker_id": WorkerId("w"),
                "claim_id": UUID("0198f1c1-8c80-7000-8000-000000000001"),
            },
            "LeaseClaimId",
        ),
        (
            RenewLeaseCommand,
            {"proof": "proof", "expected_version": LeaseVersion(1)},
            "LeaseAuthorityProof",
        ),
        (
            RenewLeaseCommand,
            {"proof": _proof(), "expected_version": 1},
            "LeaseVersion",
        ),
        (
            ReleaseLeaseCommand,
            {"proof": _proof(), "expected_version": 1},
            "LeaseVersion",
        ),
    ],
)
def test_commands_reject_untyped_parts(
    command_type: type[ClaimLeaseCommand] | type[RenewLeaseCommand] | type[ReleaseLeaseCommand],
    arguments: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        command_type(**arguments)  # type: ignore[arg-type]


def test_commands_are_immutable() -> None:
    command = ClaimLeaseCommand(TenantId("tenant-1"), WorkerId("worker-1"), _claim_id())

    with pytest.raises(FrozenInstanceError):
        command.worker_id = WorkerId("worker-2")  # type: ignore[misc]


def test_commands_have_no_worker_supplied_time_field() -> None:
    claim_fields = {item.name for item in fields(ClaimLeaseCommand)}
    renew_fields = {item.name for item in fields(RenewLeaseCommand)}
    release_fields = {item.name for item in fields(ReleaseLeaseCommand)}

    forbidden = {"now", "observed_at", "issued_at", "heartbeat_at", "lease_expires_at"}
    assert claim_fields.isdisjoint(forbidden)
    assert renew_fields.isdisjoint(forbidden)
    assert release_fields.isdisjoint(forbidden)
