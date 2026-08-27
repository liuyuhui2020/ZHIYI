"""Read-only inactive-running observation and keyset pagination."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_schema import runs
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.adapters.persistence.postgresql_worker_lease_schema import worker_leases
from zhiyi.application.commands.worker_leases import ClaimLeaseCommand, ReleaseLeaseCommand
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.application.ports.worker_lease_observability import WorkerLeaseTelemetry
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.identifiers import CommandId, EventId, RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import WorkerId
from zhiyi.domain.worker_leases.models import (
    InactiveRunningCursor,
    InactiveRunningLease,
    InactiveRunningReason,
    LeaseGrant,
)
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

pytestmark = pytest.mark.postgresql

SeedQueuedRuns = Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]


def _start_receipt(run: Run, event_id: EventId, suffix: str) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=run.tenant_id,
        command_id=CommandId(f"command-inactive-start-{suffix}"),
        run_id=run.run_id,
        command_type="start_run",
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=run.status,
        resulting_version=run.version,
        event_ids=(event_id,),
        created_at=run.updated_at,
    )


async def _claim_and_start(
    queued: Run,
    lease_repository: PostgreSQLWorkerLeaseRepository,
    run_repository: PostgreSQLRunRepository,
    suffix: str,
) -> LeaseGrant:
    claim = await lease_repository.claim(
        ClaimLeaseCommand(
            queued.tenant_id,
            WorkerId(f"worker-inactive-{suffix}"),
            await lease_repository.issue_claim_id(),
        )
    )
    assert claim.grant is not None
    event_id = EventId(f"event-inactive-start-{suffix}")
    started = queued.start(observed_at=queued.updated_at, event_id=event_id)
    await run_repository.commit_with_lease(
        proof=claim.grant.proof,
        expected_version=queued.version,
        updated_run=started.run,
        new_events=started.events,
        receipt=_start_receipt(started.run, event_id, suffix),
    )
    return claim.grant


async def test_single_observation_reports_natural_expiry_and_active_release(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: Any,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-inactive-single")
    expired_run, released_run = await seed_queued_runs(tenant_id, 2)
    run_repository = PostgreSQLRunRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
    )
    expired_grant = await _claim_and_start(
        expired_run,
        worker_lease_repository,
        run_repository,
        "expired",
    )
    released_grant = await _claim_and_start(
        released_run,
        worker_lease_repository,
        run_repository,
        "released",
    )
    await worker_lease_repository.release(
        ReleaseLeaseCommand(released_grant.proof, released_grant.lease_version)
    )

    class AtExpiryRepository(PostgreSQLWorkerLeaseRepository):
        async def _database_now(self, connection: AsyncConnection) -> datetime:
            return max(expired_grant.lease_expires_at, released_grant.lease_expires_at)

    observer = AtExpiryRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
        token_generator=SecureLeaseTokenGenerator(),
    )
    worker_lease_telemetry.logs.clear()
    worker_lease_telemetry.metrics.clear()
    worker_lease_telemetry.traces.clear()
    expired = await observer.get_inactive_running(tenant_id, expired_run.run_id)
    released = await observer.get_inactive_running(tenant_id, released_run.run_id)

    assert expired is not None and expired.reason is InactiveRunningReason.EXPIRED
    assert released is not None and released.reason is InactiveRunningReason.RELEASED
    assert expired.authority_ended_at == expired_grant.lease_expires_at
    for candidate in (expired, released):
        assert candidate.tenant_id == tenant_id
        assert not hasattr(candidate, "worker_id")
        assert not hasattr(candidate, "claim_id")
        assert not hasattr(candidate, "token")
        assert not hasattr(candidate, "token_digest")
    assert [item.operation.value for item in worker_lease_telemetry.logs] == [
        "get_inactive_running",
        "get_inactive_running",
    ]
    assert worker_lease_telemetry.logs == worker_lease_telemetry.metrics
    assert worker_lease_telemetry.logs == worker_lease_telemetry.traces


async def test_keyset_pages_are_tenant_bound_ordered_and_repeatable(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-inactive-page")
    queued_runs = await seed_queued_runs(tenant_id, 3)
    run_repository = PostgreSQLRunRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
    )
    for index, queued in enumerate(queued_runs):
        grant = await _claim_and_start(
            queued,
            worker_lease_repository,
            run_repository,
            str(index),
        )
        await worker_lease_repository.release(ReleaseLeaseCommand(grant.proof, grant.lease_version))

    async with postgresql_engine.connect() as connection:
        fixed_as_of_value = await connection.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(fixed_as_of_value, datetime)
    fixed_as_of = fixed_as_of_value

    class FixedAsOfRepository(PostgreSQLWorkerLeaseRepository):
        async def _database_now(self, connection: AsyncConnection) -> datetime:
            return fixed_as_of

    observer = FixedAsOfRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
        token_generator=SecureLeaseTokenGenerator(),
    )
    first = await observer.list_inactive_running(tenant_id, limit=1)
    repeated = await observer.list_inactive_running(tenant_id, limit=1)
    assert first == repeated
    assert len(first.items) == 1
    assert first.next_cursor is not None
    pages = [first]
    while pages[-1].next_cursor is not None:
        pages.append(
            await observer.list_inactive_running(
                tenant_id,
                cursor=pages[-1].next_cursor,
                limit=1,
            )
        )
    items = tuple(item for page in pages for item in page.items)
    assert len(items) == 3
    assert len({item.run_id for item in items}) == 3
    assert [(item.authority_ended_at, str(item.run_id)) for item in items] == sorted(
        (item.authority_ended_at, str(item.run_id)) for item in items
    )
    assert all(
        page.next_cursor is None or page.next_cursor.as_of == first.next_cursor.as_of
        for page in pages
    )

    foreign_cursor = InactiveRunningCursor(
        tenant_id=TenantId("tenant-other"),
        as_of=first.next_cursor.as_of,
        last_authority_ended_at=first.items[0].authority_ended_at,
        last_run_id=RunId("run-other"),
    )
    with pytest.raises(WorkerLeaseError) as invalid:
        await observer.list_inactive_running(
            tenant_id,
            cursor=foreign_cursor,
            limit=1,
        )
    assert invalid.value.code is WorkerLeaseErrorCode.INVALID_INPUT


@pytest.mark.parametrize("limit", [1, 100, 1_000])
async def test_supported_page_bounds_are_accepted_without_mutation(
    limit: int,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: Any,
) -> None:
    worker_lease_telemetry.logs.clear()
    worker_lease_telemetry.metrics.clear()
    worker_lease_telemetry.traces.clear()
    page = await worker_lease_repository.list_inactive_running(
        TenantId(f"tenant-inactive-limit-{limit}"),
        limit=limit,
    )
    assert page.items == ()
    assert page.next_cursor is None
    assert len(worker_lease_telemetry.logs) == 1
    assert worker_lease_telemetry.logs == worker_lease_telemetry.metrics
    assert worker_lease_telemetry.logs == worker_lease_telemetry.traces
    assert worker_lease_telemetry.logs[0].operation.value == "list_inactive_running"


@pytest.mark.parametrize("limit", [0, 1_001, True])
async def test_invalid_page_bounds_fail_before_broad_query(
    limit: object,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
) -> None:
    with pytest.raises(WorkerLeaseError) as invalid:
        await worker_lease_repository.list_inactive_running(
            TenantId("tenant-inactive-invalid-limit"),
            limit=limit,  # type: ignore[arg-type]
        )
    assert invalid.value.code is WorkerLeaseErrorCode.INVALID_INPUT


async def test_1000_static_candidates_page_without_gaps_duplicates_or_reverse_order(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-inactive-1000")
    other_tenant = TenantId("tenant-inactive-1000-other")
    queued_runs = await seed_queued_runs(tenant_id, 1_002)
    other_run = (await seed_queued_runs(other_tenant, 1))[0]
    async with postgresql_engine.begin() as connection:
        as_of = await connection.scalar(text("SELECT clock_timestamp()"))
        assert isinstance(as_of, datetime)
        await connection.execute(
            update(runs).where(runs.c.tenant_id == str(tenant_id)).values(run_status="running")
        )
        await connection.execute(
            update(runs)
            .where(
                runs.c.tenant_id == str(tenant_id),
                runs.c.run_id == str(queued_runs[1_000].run_id),
            )
            .values(run_status="queued")
        )
        await connection.execute(
            update(runs).where(runs.c.tenant_id == str(other_tenant)).values(run_status="running")
        )
        milliseconds = int(as_of.timestamp() * 1_000)
        await connection.execute(
            insert(worker_leases),
            [
                {
                    "tenant_id": str(tenant_id),
                    "run_id": str(run.run_id),
                    "worker_id": f"worker-page-{index}",
                    "claim_id": UUID(
                        int=(milliseconds << 80) | (0x7 << 76) | (0b10 << 62) | (index + 1)
                    ),
                    "token_digest": bytes([index % 251]) * 32,
                    "attempt_no": 1,
                    "lease_version": 1,
                    "duration_seconds": 30,
                    "acquired_at": as_of - timedelta(seconds=2_000),
                    "heartbeat_at": as_of - timedelta(seconds=2_000),
                    "lease_expires_at": (
                        as_of - timedelta(seconds=max(1, 1_000 - index))
                        if index <= 1_000
                        else as_of + timedelta(seconds=30)
                    ),
                    "released_at": None,
                    "record_format_version": 1,
                }
                for index, run in enumerate(queued_runs)
            ],
        )
        await connection.execute(
            insert(worker_leases),
            {
                "tenant_id": str(other_tenant),
                "run_id": str(other_run.run_id),
                "worker_id": "worker-page-other",
                "claim_id": UUID(int=(milliseconds << 80) | (0x7 << 76) | (0b10 << 62) | 2_000),
                "token_digest": b"o" * 32,
                "attempt_no": 1,
                "lease_version": 1,
                "duration_seconds": 30,
                "acquired_at": as_of - timedelta(seconds=2_000),
                "heartbeat_at": as_of - timedelta(seconds=2_000),
                "lease_expires_at": as_of - timedelta(seconds=1),
                "released_at": None,
                "record_format_version": 1,
            },
        )

    async def collect_all(limit: int | None) -> list[InactiveRunningLease]:
        items: list[InactiveRunningLease] = []
        cursor: InactiveRunningCursor | None = None
        captured_as_of: datetime | None = None
        while True:
            page = (
                await worker_lease_repository.list_inactive_running(
                    tenant_id,
                    cursor=cursor,
                )
                if limit is None
                else await worker_lease_repository.list_inactive_running(
                    tenant_id,
                    cursor=cursor,
                    limit=limit,
                )
            )
            items.extend(page.items)
            if page.next_cursor is not None:
                if captured_as_of is None:
                    captured_as_of = page.next_cursor.as_of
                assert page.next_cursor.as_of == captured_as_of
            cursor = page.next_cursor
            if cursor is None:
                return items

    expected_ids = {run.run_id for run in queued_runs[:1_000]}
    traversals = [await collect_all(limit) for limit in (None, 1, 100, 1_000)]
    for items in traversals:
        assert len(items) == 1_000
        assert {item.run_id for item in items} == expected_ids
        assert [(item.authority_ended_at, str(item.run_id)) for item in items] == sorted(
            (item.authority_ended_at, str(item.run_id)) for item in items
        )

    assert (
        await worker_lease_repository.get_inactive_running(tenant_id, queued_runs[0].run_id)
        is not None
    )
    assert (
        await worker_lease_repository.get_inactive_running(tenant_id, queued_runs[1_000].run_id)
        is None
    )
    assert (
        await worker_lease_repository.get_inactive_running(tenant_id, queued_runs[1_001].run_id)
        is None
    )
    async with postgresql_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(worker_leases)
                .where(worker_leases.c.tenant_id == str(tenant_id))
            )
            == 1_002
        )

    first_page = await worker_lease_repository.list_inactive_running(tenant_id, limit=1)
    assert first_page.next_cursor is not None
    removed_run_id = queued_runs[500].run_id
    newly_inactive_run_id = queued_runs[1_001].run_id
    async with postgresql_engine.begin() as connection:
        await connection.execute(
            update(runs)
            .where(
                runs.c.tenant_id == str(tenant_id),
                runs.c.run_id == str(removed_run_id),
            )
            .values(run_status="cancelled")
        )
        await connection.execute(
            update(worker_leases)
            .where(
                worker_leases.c.tenant_id == str(tenant_id),
                worker_leases.c.run_id == str(newly_inactive_run_id),
            )
            .values(lease_expires_at=first_page.next_cursor.as_of + timedelta(microseconds=1))
        )

    remaining: list[InactiveRunningLease] = []
    cursor: InactiveRunningCursor | None = first_page.next_cursor
    while cursor is not None:
        page = await worker_lease_repository.list_inactive_running(
            tenant_id,
            cursor=cursor,
            limit=100,
        )
        remaining.extend(page.items)
        cursor = page.next_cursor
    mutated_ids = {first_page.items[0].run_id, *(item.run_id for item in remaining)}
    assert len(mutated_ids) == len(first_page.items) + len(remaining)
    assert removed_run_id not in mutated_ids
    assert newly_inactive_run_id not in mutated_ids
    assert mutated_ids <= expected_ids
