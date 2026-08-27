"""PostgreSQL UUIDv7 age, tenant scope, and immutable claim replay boundaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import RFC_4122, UUID

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.adapters.persistence.postgresql_worker_lease_schema import (
    worker_lease_claim_receipts,
)
from zhiyi.application.commands.worker_leases import ClaimLeaseCommand
from zhiyi.domain.runs.identifiers import TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import LeaseClaimId, LeaseDurationSeconds, WorkerId

pytestmark = pytest.mark.postgresql
CAPTURED_NOW = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)

RepositoryAtTime = Callable[[datetime], PostgreSQLWorkerLeaseRepository]


def uuid7_at(value: datetime, *, tail: int = 1) -> LeaseClaimId:
    milliseconds = int(value.timestamp() * 1_000)
    raw = (milliseconds << 80) | (0x7 << 76) | (0b10 << 62) | tail
    return LeaseClaimId(UUID(int=raw))


async def test_database_issued_claim_id_round_trips_as_rfc_uuidv7(
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
) -> None:
    claim_id = await worker_lease_repository.issue_claim_id()

    assert claim_id.value.version == 7
    assert claim_id.value.variant == RFC_4122
    assert LeaseClaimId(UUID(str(claim_id))) == claim_id


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(seconds=60), "no_work"),
        (timedelta(seconds=60, milliseconds=1), WorkerLeaseErrorCode.INVALID_INPUT),
        (timedelta(hours=-24, milliseconds=1), "no_work"),
        (timedelta(hours=-24), WorkerLeaseErrorCode.IDEMPOTENCY_EXPIRED),
        (timedelta(hours=-24, milliseconds=-1), WorkerLeaseErrorCode.IDEMPOTENCY_EXPIRED),
    ],
)
async def test_exact_future_and_replay_age_boundaries_use_captured_database_time(
    worker_lease_repository_at_time: RepositoryAtTime,
    offset: timedelta,
    expected: str | WorkerLeaseErrorCode,
) -> None:
    repository = worker_lease_repository_at_time(CAPTURED_NOW)
    for iteration in range(100):
        command = ClaimLeaseCommand(
            tenant_id=TenantId("tenant-age-boundary"),
            worker_id=WorkerId("worker-age-boundary"),
            claim_id=uuid7_at(CAPTURED_NOW + offset, tail=iteration + 1),
        )

        if isinstance(expected, WorkerLeaseErrorCode):
            with pytest.raises(WorkerLeaseError) as caught:
                await repository.claim(command)
            assert caught.value.code is expected
        else:
            assert (await repository.claim(command)).code.value == expected


@pytest.mark.parametrize("duration", [None, 10, 30])
async def test_100_default_and_supported_duration_boundaries_are_exact(
    worker_lease_repository_at_time: RepositoryAtTime,
    duration: int | None,
) -> None:
    repository = worker_lease_repository_at_time(CAPTURED_NOW)
    for iteration in range(100):
        command = ClaimLeaseCommand(
            TenantId(f"tenant-duration-{duration}"),
            WorkerId("worker-duration-boundary"),
            uuid7_at(CAPTURED_NOW, tail=iteration + 1),
            LeaseDurationSeconds(duration) if duration is not None else LeaseDurationSeconds(),
        )
        outcome = await repository.claim(command)
        assert outcome.code.value == "no_work"
        assert command.duration.value == (30 if duration is None else duration)


@pytest.mark.parametrize("duration", [9, 31])
def test_100_out_of_range_duration_boundaries_are_rejected(duration: int) -> None:
    for _ in range(100):
        with pytest.raises(ValueError, match="10 and 30"):
            LeaseDurationSeconds(duration)


async def test_database_clock_failure_precedes_age_or_receipt_guessing(
    worker_lease_repository_with_failing_clock: PostgreSQLWorkerLeaseRepository,
) -> None:
    command = ClaimLeaseCommand(
        tenant_id=TenantId("tenant-clock-failure"),
        worker_id=WorkerId("worker-clock-failure"),
        claim_id=uuid7_at(CAPTURED_NOW),
    )

    with pytest.raises(WorkerLeaseError) as caught:
        await worker_lease_repository_with_failing_clock.claim(command)

    assert caught.value.code is WorkerLeaseErrorCode.STORAGE_UNAVAILABLE


async def test_same_uuid_is_tenant_scoped_but_same_tenant_changed_intent_conflicts(
    worker_lease_repository_at_time: RepositoryAtTime,
) -> None:
    repository = worker_lease_repository_at_time(CAPTURED_NOW)
    claim_id = uuid7_at(CAPTURED_NOW)
    first = ClaimLeaseCommand(TenantId("tenant-a"), WorkerId("worker-a"), claim_id)
    other_tenant = ClaimLeaseCommand(TenantId("tenant-b"), WorkerId("worker-a"), claim_id)
    changed = ClaimLeaseCommand(TenantId("tenant-a"), WorkerId("worker-b"), claim_id)

    assert (await repository.claim(first)).code.value == "no_work"
    assert (await repository.claim(other_tenant)).code.value == "no_work"
    with pytest.raises(WorkerLeaseError) as caught:
        await repository.claim(changed)
    assert caught.value.code is WorkerLeaseErrorCode.IDEMPOTENCY_CONFLICT


async def test_claimed_and_no_work_receipts_replay_after_repository_recreation(
    worker_lease_repository_at_time: RepositoryAtTime,
) -> None:
    repository = worker_lease_repository_at_time(CAPTURED_NOW)
    command = ClaimLeaseCommand(
        TenantId("tenant-persistent-no-work"),
        WorkerId("worker-persistent-no-work"),
        uuid7_at(CAPTURED_NOW),
    )
    first = await repository.claim(command)
    recreated = worker_lease_repository_at_time(CAPTURED_NOW + timedelta(seconds=1))

    replay = await recreated.claim(command)

    assert first.code.value == replay.code.value == "no_work"
    assert replay.replayed is True


async def test_receipt_deletion_does_not_make_an_expired_claim_id_reusable(
    postgresql_engine: AsyncEngine,
    worker_lease_repository_at_time: RepositoryAtTime,
) -> None:
    claim_id = uuid7_at(CAPTURED_NOW)
    command = ClaimLeaseCommand(
        TenantId("tenant-cleaned-receipt"), WorkerId("worker-cleaned-receipt"), claim_id
    )
    repository = worker_lease_repository_at_time(CAPTURED_NOW)
    await repository.claim(command)
    async with postgresql_engine.begin() as connection:
        await connection.execute(
            delete(worker_lease_claim_receipts).where(
                worker_lease_claim_receipts.c.tenant_id == str(command.tenant_id),
                worker_lease_claim_receipts.c.claim_id == claim_id.value,
            )
        )
    expired = worker_lease_repository_at_time(CAPTURED_NOW + timedelta(hours=24))

    with pytest.raises(WorkerLeaseError) as caught:
        await expired.claim(command)

    assert caught.value.code is WorkerLeaseErrorCode.IDEMPOTENCY_EXPIRED
