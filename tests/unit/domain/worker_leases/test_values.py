"""Safety contracts for framework-neutral Worker lease values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.identifiers import (
    LeaseAttemptNo,
    LeaseClaimId,
    LeaseDurationSeconds,
    LeaseToken,
    LeaseVersion,
    WorkerId,
)
from zhiyi.domain.worker_leases.models import (
    InactiveRunningCursor,
    LeaseAuthorityProof,
    renew_by_at,
)


def _claim_id() -> LeaseClaimId:
    return LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000001"))


def _proof() -> LeaseAuthorityProof:
    return LeaseAuthorityProof(
        tenant_id=TenantId("tenant-1"),
        run_id=RunId("run-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=_claim_id(),
        attempt_no=LeaseAttemptNo(1),
        token=LeaseToken(b"s" * 32),
    )


@pytest.mark.parametrize("value", ["worker", "worker.1:a-b_c", "w" * 128])
def test_worker_id_accepts_bounded_safe_ascii(value: str) -> None:
    assert str(WorkerId(value)) == value


@pytest.mark.parametrize(
    "value",
    ["", " worker", "worker/1", "工作者", "w" * 129, 1, None, True],
)
def test_worker_id_rejects_malformed_values(value: object) -> None:
    with pytest.raises(ValueError, match="worker identifier"):
        WorkerId(value)  # type: ignore[arg-type]


def test_claim_id_requires_an_rfc_variant_uuidv7() -> None:
    claim_id = _claim_id()

    assert claim_id.value.version == 7
    assert claim_id.value.variant == "specified in RFC 4122"
    assert str(claim_id) == "0198f1c1-8c80-7000-8000-000000000001"


@pytest.mark.parametrize(
    "value",
    [
        "0198f1c1-8c80-7000-8000-000000000001",
        UUID("0198f1c1-8c80-4000-8000-000000000001"),
        UUID("0198f1c1-8c80-7000-c000-000000000001"),
    ],
)
def test_claim_id_rejects_non_uuid_or_non_v7_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="UUIDv7"):
        LeaseClaimId(value)  # type: ignore[arg-type]


def test_lease_token_is_immutable_and_always_prints_a_constant_sentinel() -> None:
    raw = bytes(range(32))
    token = LeaseToken(raw)

    assert token.value == raw
    assert str(token) == "<lease-token:redacted>"
    assert repr(token) == "LeaseToken(<redacted>)"
    assert raw.hex() not in str(token)
    assert raw.hex() not in repr(token)
    with pytest.raises(FrozenInstanceError):
        token.value = b"x" * 32  # type: ignore[misc]


@pytest.mark.parametrize("value", [b"", b"x" * 31, b"x" * 33, bytearray(32), "x" * 32])
def test_lease_token_requires_exactly_32_immutable_bytes(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="32 bytes"):
        LeaseToken(value)  # type: ignore[arg-type]


def test_duration_defaults_to_30_and_accepts_closed_10_to_30_second_range() -> None:
    assert LeaseDurationSeconds().value == 30
    assert LeaseDurationSeconds(10).value == 10
    assert LeaseDurationSeconds(30).value == 30


@pytest.mark.parametrize("value", [9, 31, 10.0, True, "10", None])
def test_duration_rejects_out_of_range_or_non_integer_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="10 and 30"):
        LeaseDurationSeconds(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value_type", [LeaseAttemptNo, LeaseVersion])
def test_attempt_and_lease_versions_start_positive_and_are_monotonic_values(
    value_type: type[LeaseAttemptNo] | type[LeaseVersion],
) -> None:
    first = value_type(1)
    second = first.next()

    assert first.value == 1
    assert second.value == 2
    assert first.value == 1


@pytest.mark.parametrize("value_type", [LeaseAttemptNo, LeaseVersion])
@pytest.mark.parametrize("value", [0, -1, 1.0, True, "1"])
def test_attempt_and_lease_versions_reject_non_positive_integers(
    value_type: type[LeaseAttemptNo] | type[LeaseVersion],
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="positive integer"):
        value_type(value)  # type: ignore[arg-type]


def test_authority_proof_requires_every_capability_part_and_is_immutable() -> None:
    proof = _proof()

    assert proof.tenant_id == TenantId("tenant-1")
    assert proof.run_id == RunId("run-1")
    assert proof.worker_id == WorkerId("worker-1")
    assert proof.claim_id == _claim_id()
    assert proof.attempt_no == LeaseAttemptNo(1)
    assert proof.token.value == b"s" * 32
    assert "ssss" not in repr(proof)
    with pytest.raises(FrozenInstanceError):
        proof.run_id = RunId("other")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tenant_id", "tenant-1", "TenantId"),
        ("run_id", "run-1", "RunId"),
        ("worker_id", "worker-1", "WorkerId"),
        ("claim_id", UUID("0198f1c1-8c80-7000-8000-000000000001"), "LeaseClaimId"),
        ("attempt_no", 1, "LeaseAttemptNo"),
        ("token", b"s" * 32, "LeaseToken"),
    ],
)
def test_authority_proof_rejects_untyped_parts(field: str, value: object, message: str) -> None:
    arguments = {
        "tenant_id": TenantId("tenant-1"),
        "run_id": RunId("run-1"),
        "worker_id": WorkerId("worker-1"),
        "claim_id": _claim_id(),
        "attempt_no": LeaseAttemptNo(1),
        "token": LeaseToken(b"s" * 32),
    }
    arguments[field] = value

    with pytest.raises(TypeError, match=message):
        LeaseAuthorityProof(**arguments)  # type: ignore[arg-type]


def test_renew_by_uses_exact_integer_microsecond_floor() -> None:
    captured_at = datetime(2026, 8, 27, 1, 2, 3, 123456, tzinfo=UTC)

    assert renew_by_at(captured_at, LeaseDurationSeconds(10)) == captured_at + timedelta(
        microseconds=3_333_333
    )
    assert renew_by_at(captured_at, LeaseDurationSeconds(30)) == captured_at + timedelta(seconds=10)


def test_renew_by_rejects_worker_local_or_naive_time() -> None:
    with pytest.raises(ValueError, match="aware UTC"):
        renew_by_at(datetime(2026, 8, 27), LeaseDurationSeconds())
    with pytest.raises(TypeError, match="LeaseDurationSeconds"):
        renew_by_at(datetime.now(UTC), 30)  # type: ignore[arg-type]


def test_inactive_cursor_is_tenant_bound_utc_and_immutable() -> None:
    as_of = datetime(2026, 8, 27, tzinfo=UTC)
    ended_at = as_of - timedelta(seconds=1)
    cursor = InactiveRunningCursor(
        tenant_id=TenantId("tenant-1"),
        as_of=as_of,
        last_authority_ended_at=ended_at,
        last_run_id=RunId("run-1"),
    )

    assert cursor.tenant_id == TenantId("tenant-1")
    assert cursor.as_of == as_of
    assert cursor.last_authority_ended_at == ended_at
    with pytest.raises(FrozenInstanceError):
        cursor.as_of = as_of + timedelta(seconds=1)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tenant_id", "tenant-1", "TenantId"),
        ("as_of", datetime(2026, 8, 27), "aware UTC"),
        ("last_authority_ended_at", datetime(2026, 8, 27), "aware UTC"),
        ("last_run_id", "run-1", "RunId"),
    ],
)
def test_inactive_cursor_rejects_malformed_or_untyped_parts(
    field: str,
    value: object,
    message: str,
) -> None:
    arguments = {
        "tenant_id": TenantId("tenant-1"),
        "as_of": datetime(2026, 8, 27, tzinfo=UTC),
        "last_authority_ended_at": datetime(2026, 8, 26, tzinfo=UTC),
        "last_run_id": RunId("run-1"),
    }
    arguments[field] = value

    with pytest.raises((TypeError, ValueError), match=message):
        InactiveRunningCursor(**arguments)  # type: ignore[arg-type]


def test_inactive_cursor_rejects_a_key_after_its_fixed_as_of() -> None:
    as_of = datetime(2026, 8, 27, tzinfo=UTC)

    with pytest.raises(ValueError, match="after as_of"):
        InactiveRunningCursor(
            tenant_id=TenantId("tenant-1"),
            as_of=as_of,
            last_authority_ended_at=as_of + timedelta(microseconds=1),
            last_run_id=RunId("run-1"),
        )
