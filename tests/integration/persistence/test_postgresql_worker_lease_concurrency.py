"""Real PostgreSQL claim linearization, FIFO, and bounded-head-probe matrices."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_schema import run_command_receipts, run_events, runs
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.adapters.persistence.postgresql_worker_lease_schema import (
    worker_lease_claim_receipts,
    worker_leases,
)
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.worker_lease_observability import WorkerLeaseTelemetry
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.identifiers import EventId, RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import LeaseToken, LeaseVersion, WorkerId
from zhiyi.domain.worker_leases.models import ConditionalLeaseOutcome, LeaseClaimOutcome

pytestmark = pytest.mark.postgresql

SeedQueuedRuns = Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]
RepositoryFactory = Callable[..., PostgreSQLWorkerLeaseRepository]


async def _claim(
    repository: PostgreSQLWorkerLeaseRepository,
    tenant_id: TenantId,
    worker: str,
) -> LeaseClaimOutcome:
    return await repository.claim(
        ClaimLeaseCommand(
            tenant_id=tenant_id,
            worker_id=WorkerId(worker),
            claim_id=await repository.issue_claim_id(),
        )
    )


async def test_100_one_run_groups_by_20_workers_have_exactly_one_winner(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
) -> None:
    winner_count = 0
    for group in range(100):
        tenant_id = TenantId(f"tenant-one-run-{group}")
        runs_for_group = await seed_queued_runs(tenant_id, 1)
        repositories = [worker_lease_repository_factory() for _ in range(20)]
        outcomes = await asyncio.gather(
            *(
                _claim(repository, tenant_id, f"worker-{index}")
                for index, repository in enumerate(repositories)
            )
        )
        claimed = [outcome for outcome in outcomes if outcome.code.value == "claimed"]
        assert len(claimed) == 1
        assert claimed[0].grant is not None
        assert claimed[0].grant.run_id == runs_for_group[0].run_id
        winner_count += len(claimed)

    assert winner_count == 100


async def test_100_runs_drain_once_by_20_clients_without_duplicates_or_omissions(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
) -> None:
    tenant_id = TenantId("tenant-drain")
    seeded = await seed_queued_runs(tenant_id, 100)
    repositories = [worker_lease_repository_factory() for _ in range(20)]

    async def drain(client: int) -> list[RunId]:
        claimed: list[RunId] = []
        while True:
            outcome = await _claim(repositories[client], tenant_id, f"worker-{client}")
            if outcome.grant is None:
                return claimed
            claimed.append(outcome.grant.run_id)

    claimed_pages = await asyncio.gather(*(drain(index) for index in range(20)))
    claimed = [run_id for page in claimed_pages for run_id in page]

    assert len(claimed) == 100
    assert len(set(claimed)) == 100
    assert set(claimed) == {run.run_id for run in seeded}


@pytest.mark.parametrize("with_work", [False, True])
async def test_100_way_same_claim_id_arbitrates_one_immutable_result(
    with_work: bool,
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId(f"tenant-same-claim-{with_work}")
    if with_work:
        await seed_queued_runs(tenant_id, 2)
    issuer = worker_lease_repository_factory()
    command = ClaimLeaseCommand(
        tenant_id,
        WorkerId("worker-same-claim"),
        await issuer.issue_claim_id(),
    )
    repositories = [worker_lease_repository_factory() for _ in range(100)]

    outcomes = await asyncio.gather(*(repository.claim(command) for repository in repositories))

    assert {outcome.code.value for outcome in outcomes} == {"claimed" if with_work else "no_work"}
    assert sum(not outcome.replayed for outcome in outcomes) == 1
    if with_work:
        grants = [outcome.grant for outcome in outcomes]
        assert all(grant is not None for grant in grants)
        assert len({grant.token.value for grant in grants if grant is not None}) == 1
        assert len({grant.run_id for grant in grants if grant is not None}) == 1
    async with postgresql_engine.connect() as connection:
        receipt_count = await connection.scalar(
            select(func.count())
            .select_from(worker_lease_claim_receipts)
            .where(worker_lease_claim_receipts.c.tenant_id == str(tenant_id))
        )
        lease_count = await connection.scalar(
            select(func.count())
            .select_from(worker_leases)
            .where(worker_leases.c.tenant_id == str(tenant_id))
        )
    assert receipt_count == 1
    assert lease_count == (1 if with_work else 0)


async def test_uncontended_claims_follow_updated_at_then_run_id_fifo(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
) -> None:
    tenant_id = TenantId("tenant-fifo")
    seeded = await seed_queued_runs(tenant_id, 100)
    repository = worker_lease_repository_factory()

    outcomes = [await _claim(repository, tenant_id, f"worker-{index}") for index in range(100)]

    assert [outcome.grant.run_id for outcome in outcomes if outcome.grant is not None] == [
        run.run_id for run in seeded
    ]


async def test_temporarily_locked_head_is_revisited_after_skip_locked_progress(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-fairness")
    seeded = await seed_queued_runs(tenant_id, 2)
    connection = await postgresql_engine.connect()
    transaction = await connection.begin()
    try:
        await connection.execute(
            select(runs.c.run_id)
            .where(
                runs.c.tenant_id == str(tenant_id),
                runs.c.run_id == str(seeded[0].run_id),
            )
            .with_for_update()
        )
        first = await _claim(worker_lease_repository_factory(), tenant_id, "worker-fast")
        assert first.grant is not None
        assert first.grant.run_id == seeded[1].run_id
    finally:
        await transaction.rollback()
        await connection.close()

    next_outcome = await _claim(worker_lease_repository_factory(), tenant_id, "worker-head")
    assert next_outcome.grant is not None
    assert next_outcome.grant.run_id == seeded[0].run_id


async def test_locked_sole_head_times_out_without_persisting_false_no_work(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-locked-head")
    seeded = await seed_queued_runs(tenant_id, 1)
    repository = worker_lease_repository_factory(lock_timeout_ms=100, statement_timeout_ms=200)
    claim_id = await repository.issue_claim_id()
    command = ClaimLeaseCommand(tenant_id, WorkerId("worker-locked-head"), claim_id)
    connection = await postgresql_engine.connect()
    transaction = await connection.begin()
    try:
        await connection.execute(
            select(runs.c.run_id)
            .where(
                runs.c.tenant_id == str(tenant_id),
                runs.c.run_id == str(seeded[0].run_id),
            )
            .with_for_update()
        )
        with pytest.raises(WorkerLeaseError) as caught:
            await repository.claim(command)
        assert caught.value.code is WorkerLeaseErrorCode.STORAGE_UNAVAILABLE
        async with postgresql_engine.connect() as observer:
            assert (
                await observer.scalar(
                    select(func.count())
                    .select_from(worker_lease_claim_receipts)
                    .where(
                        worker_lease_claim_receipts.c.tenant_id == str(tenant_id),
                        worker_lease_claim_receipts.c.claim_id == claim_id.value,
                    )
                )
                == 0
            )
    finally:
        await transaction.rollback()
        await connection.close()

    recovered = await repository.claim(command)
    assert recovered.grant is not None
    assert recovered.grant.run_id == seeded[0].run_id


async def test_claim_matrix_never_mutates_lifecycle_tables(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-zero-lifecycle")
    await seed_queued_runs(tenant_id, 10)
    async with postgresql_engine.connect() as connection:
        before = tuple(
            [
                await connection.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.tenant_id == str(tenant_id))
                )
                for table in (runs, run_events, run_command_receipts)
            ]
        )
    repository = worker_lease_repository_factory()
    await asyncio.gather(*(_claim(repository, tenant_id, f"worker-{index}") for index in range(10)))
    async with postgresql_engine.connect() as connection:
        after = tuple(
            [
                await connection.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.tenant_id == str(tenant_id))
                )
                for table in (runs, run_events, run_command_receipts)
            ]
        )

    assert after == before == (10, 10, 10)


async def test_100_ownership_cycles_never_reset_attempt_or_lease_version(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
) -> None:
    tenant_id = TenantId("tenant-ownership-cycles")
    await seed_queued_runs(tenant_id, 1)
    repository = worker_lease_repository_factory()
    first_proof = None

    for cycle in range(100):
        claimed = await _claim(repository, tenant_id, f"worker-cycle-{cycle}")
        assert claimed.grant is not None
        assert claimed.grant.attempt_no.value == cycle + 1
        assert claimed.grant.lease_version.value == cycle * 2 + 1
        if first_proof is None:
            first_proof = claimed.grant.proof
        released = await repository.release(
            ReleaseLeaseCommand(
                proof=claimed.grant.proof,
                expected_version=claimed.grant.lease_version,
            )
        )
        assert released.applied is True
        assert released.authority.authoritative is False

    assert first_proof is not None
    assert (await repository.get_authority(first_proof)).authoritative is False


async def test_100_same_version_renew_requests_advance_exactly_once(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
) -> None:
    tenant_id = TenantId("tenant-renew-race")
    await seed_queued_runs(tenant_id, 100)
    repository = worker_lease_repository_factory()
    for group in range(100):
        claimed = await _claim(repository, tenant_id, f"worker-renew-race-{group}")
        assert claimed.grant is not None
        command = RenewLeaseCommand(claimed.grant.proof, LeaseVersion(1))

        results = await asyncio.gather(
            *(worker_lease_repository_factory().renew(command) for _ in range(2))
        )

        assert sum(result.applied for result in results) == 1
        assert {result.authority.lease_version for result in results} == {LeaseVersion(2)}


async def test_100_same_version_release_requests_release_exactly_once(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
) -> None:
    tenant_id = TenantId("tenant-release-race")
    await seed_queued_runs(tenant_id, 100)
    repository = worker_lease_repository_factory()
    for group in range(100):
        claimed = await _claim(repository, tenant_id, f"worker-release-race-{group}")
        assert claimed.grant is not None
        command = ReleaseLeaseCommand(claimed.grant.proof, claimed.grant.lease_version)

        results = await asyncio.gather(
            *(worker_lease_repository_factory().release(command) for _ in range(2))
        )

        assert sum(result.applied for result in results) == 1
        assert all(result.may_start_new_work is False for result in results)


async def test_random_token_collision_cannot_reactivate_an_old_claim(
    seed_queued_runs: SeedQueuedRuns,
    postgresql_engine: AsyncEngine,
    worker_lease_telemetry: WorkerLeaseTelemetry,
) -> None:
    class CollidingTokenGenerator:
        def new_token(self) -> LeaseToken:
            return LeaseToken(b"c" * 32)

    tenant_id = TenantId("tenant-token-collision")
    await seed_queued_runs(tenant_id, 1)
    repository = PostgreSQLWorkerLeaseRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
        token_generator=CollidingTokenGenerator(),
    )
    first = await _claim(repository, tenant_id, "worker-collision")
    assert first.grant is not None
    await repository.release(ReleaseLeaseCommand(first.grant.proof, first.grant.lease_version))
    second = await _claim(repository, tenant_id, "worker-collision")
    assert second.grant is not None

    assert first.grant.token == second.grant.token
    assert second.grant.attempt_no.value == 2
    assert second.grant.lease_version.value == 3
    assert (await repository.get_authority(first.grant.proof)).authoritative is False
    assert (await repository.get_authority(second.grant.proof)).authoritative is True


async def test_terminal_status_race_cannot_leave_authority_and_release_can_cleanup(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    postgresql_engine: AsyncEngine,
) -> None:
    from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
    from zhiyi.application.ports.run_repository import CommandReceipt
    from zhiyi.domain.runs.identifiers import CommandId, CorrelationId

    tenant_id = TenantId("tenant-status-race")
    queued = (await seed_queued_runs(tenant_id, 1))[0]
    repository = worker_lease_repository_factory()
    claimed = await _claim(repository, tenant_id, "worker-status-race")
    assert claimed.grant is not None
    cancelled = queued.cancel(
        observed_at=queued.updated_at,
        event_id=EventId("event-status-race-cancel"),
        correlation_id=CorrelationId("correlation-status-race"),
    )
    run_repository = PostgreSQLRunRepository(postgresql_engine)
    receipt = CommandReceipt(
        tenant_id=tenant_id,
        command_id=CommandId("command-status-race-cancel"),
        run_id=queued.run_id,
        command_type="cancel_run",
        intent_fingerprint="sha256:" + "d" * 64,
        resulting_status=cancelled.run.status,
        resulting_version=cancelled.run.version,
        event_ids=(cancelled.events[0].event_id,),
        created_at=queued.updated_at,
    )

    results = await asyncio.gather(
        repository.renew(RenewLeaseCommand(claimed.grant.proof, LeaseVersion(1))),
        run_repository.commit(
            expected_version=queued.version,
            updated_run=cancelled.run,
            new_events=cancelled.events,
            receipt=receipt,
        ),
        return_exceptions=True,
    )
    assert not any(isinstance(result, BaseException) for result in results)
    assert (await repository.get_authority(claimed.grant.proof)).authoritative is False
    renew_result = results[0]
    assert isinstance(renew_result, ConditionalLeaseOutcome)

    cleanup = await repository.release(
        ReleaseLeaseCommand(
            claimed.grant.proof,
            LeaseVersion(2 if renew_result.applied else 1),
        )
    )
    assert cleanup.applied is True
    assert cleanup.may_start_new_work is False
