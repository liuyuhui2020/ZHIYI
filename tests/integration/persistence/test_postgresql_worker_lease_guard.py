"""Atomic Worker-lease fencing for new Run lifecycle writes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_schema import run_command_receipts, run_events
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.run_repository import CommandReceipt, CommitOutcome
from zhiyi.application.ports.worker_lease_observability import WorkerLeaseTelemetry
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.identifiers import CommandId, CorrelationId, EventId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import LeaseToken, WorkerId
from zhiyi.domain.worker_leases.models import LeaseAuthorityProof, LeaseClaimOutcome

pytestmark = pytest.mark.postgresql

SeedQueuedRuns = Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]
RepositoryFactory = Callable[..., PostgreSQLWorkerLeaseRepository]


def _receipt(mutation: RunMutation, suffix: str, command_type: str) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=mutation.run.tenant_id,
        command_id=CommandId(f"command-guard-{suffix}"),
        run_id=mutation.run.run_id,
        command_type=command_type,
        intent_fingerprint="sha256:" + "a" * 64,
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=tuple(event.event_id for event in mutation.events),
        created_at=mutation.run.updated_at,
    )


async def _claim_one(
    repository: PostgreSQLWorkerLeaseRepository,
    tenant_id: TenantId,
) -> LeaseClaimOutcome:
    return await repository.claim(
        ClaimLeaseCommand(
            tenant_id,
            WorkerId("worker-guard"),
            await repository.issue_claim_id(),
        )
    )


def _guarded_repository(
    engine: AsyncEngine,
    telemetry: WorkerLeaseTelemetry,
) -> PostgreSQLRunRepository:
    return PostgreSQLRunRepository(engine, telemetry=telemetry)


async def test_valid_guarded_write_and_zero_event_receipt_are_atomic(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: Any,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-guard-valid")
    queued = (await seed_queued_runs(tenant_id, 1))[0]
    claim = await _claim_one(worker_lease_repository, tenant_id)
    assert claim.grant is not None
    mutation = queued.start(
        observed_at=queued.updated_at,
        event_id=EventId("event-guard-start"),
    )
    repository = _guarded_repository(postgresql_engine, worker_lease_telemetry)
    worker_lease_telemetry.logs.clear()
    worker_lease_telemetry.metrics.clear()
    worker_lease_telemetry.traces.clear()

    started = await repository.commit_with_lease(
        proof=claim.grant.proof,
        expected_version=queued.version,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=_receipt(mutation, "start", "start_run"),
    )
    no_change = RunMutation(run=mutation.run, events=())
    zero = await repository.commit_with_lease(
        proof=claim.grant.proof,
        expected_version=mutation.run.version,
        updated_run=mutation.run,
        new_events=(),
        receipt=_receipt(no_change, "zero", "consume_budget"),
    )

    assert started.replayed is False
    assert len(started.events) == 1
    assert zero.events == ()
    assert zero.receipt.resulting_version == mutation.run.version
    loaded = await repository.load(tenant_id, queued.run_id)
    assert loaded is not None
    assert loaded.version == mutation.run.version
    assert len(worker_lease_telemetry.logs) == 2
    assert len(worker_lease_telemetry.metrics) == 2
    assert len(worker_lease_telemetry.traces) == 2
    assert all(
        observation.operation.value == "commit_with_lease"
        for observation in worker_lease_telemetry.logs
    )


async def test_existing_lifecycle_replay_precedes_released_or_stale_lease(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-guard-replay")
    queued = (await seed_queued_runs(tenant_id, 1))[0]
    claim = await _claim_one(worker_lease_repository, tenant_id)
    assert claim.grant is not None
    mutation = queued.start(
        observed_at=queued.updated_at,
        event_id=EventId("event-guard-replay"),
    )
    receipt = _receipt(mutation, "replay", "start_run")
    repository = _guarded_repository(postgresql_engine, worker_lease_telemetry)
    first = await repository.commit_with_lease(
        proof=claim.grant.proof,
        expected_version=queued.version,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=receipt,
    )
    await worker_lease_repository.release(
        ReleaseLeaseCommand(claim.grant.proof, claim.grant.lease_version)
    )

    replay = await repository.commit_with_lease(
        proof=LeaseAuthorityProof(
            tenant_id=claim.grant.tenant_id,
            run_id=claim.grant.run_id,
            worker_id=claim.grant.worker_id,
            claim_id=claim.grant.claim_id,
            attempt_no=claim.grant.attempt_no,
            token=LeaseToken(b"wrong-proof-cannot-matter".ljust(32, b"!")),
        ),
        expected_version=999,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=receipt,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.receipt == first.receipt


async def test_expiry_equality_or_wrong_proof_leaves_no_partial_lifecycle_fact(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: Any,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-guard-expiry")
    queued = (await seed_queued_runs(tenant_id, 1))[0]
    claim = await _claim_one(worker_lease_repository, tenant_id)
    assert claim.grant is not None
    mutation = queued.start(
        observed_at=queued.updated_at,
        event_id=EventId("event-guard-expired"),
    )
    expires_at = claim.grant.lease_expires_at

    class FixedDatabaseTimeRunRepository(PostgreSQLRunRepository):
        @staticmethod
        async def _database_now(connection: AsyncConnection) -> datetime:
            return expires_at

    repository = FixedDatabaseTimeRunRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
    )
    worker_lease_telemetry.logs.clear()
    worker_lease_telemetry.metrics.clear()
    worker_lease_telemetry.traces.clear()
    with pytest.raises(WorkerLeaseError) as expired:
        await repository.commit_with_lease(
            proof=claim.grant.proof,
            expected_version=queued.version,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=_receipt(mutation, "expired", "start_run"),
        )
    assert expired.value.code is WorkerLeaseErrorCode.LEASE_EXPIRED
    assert len(worker_lease_telemetry.logs) == 1
    assert worker_lease_telemetry.logs == worker_lease_telemetry.metrics
    assert worker_lease_telemetry.logs == worker_lease_telemetry.traces
    assert worker_lease_telemetry.logs[0].outcome_code == "lease_expired"

    wrong = LeaseAuthorityProof(
        tenant_id=claim.grant.tenant_id,
        run_id=claim.grant.run_id,
        worker_id=claim.grant.worker_id,
        claim_id=claim.grant.claim_id,
        attempt_no=claim.grant.attempt_no,
        token=LeaseToken(b"x" * 32),
    )
    worker_lease_telemetry.logs.clear()
    worker_lease_telemetry.metrics.clear()
    worker_lease_telemetry.traces.clear()
    with pytest.raises(WorkerLeaseError) as stale:
        await repository.commit_with_lease(
            proof=wrong,
            expected_version=queued.version,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=_receipt(mutation, "wrong", "start_run"),
        )
    assert stale.value.code is WorkerLeaseErrorCode.LEASE_NOT_CURRENT
    assert len(worker_lease_telemetry.logs) == 1
    assert worker_lease_telemetry.logs == worker_lease_telemetry.metrics
    assert worker_lease_telemetry.logs == worker_lease_telemetry.traces
    assert worker_lease_telemetry.logs[0].outcome_code == "lease_not_current"

    async with postgresql_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(run_events)
                .where(run_events.c.tenant_id == str(tenant_id))
            )
            == 1
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(run_command_receipts)
                .where(run_command_receipts.c.tenant_id == str(tenant_id))
            )
            == 1
        )


async def test_guarded_write_racing_renew_or_release_has_no_partial_combination(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    for operation in ("renew", "release"):
        tenant_id = TenantId(f"tenant-guard-race-{operation}")
        queued = (await seed_queued_runs(tenant_id, 1))[0]
        lease_repository = worker_lease_repository_factory()
        claim = await _claim_one(lease_repository, tenant_id)
        assert claim.grant is not None
        mutation = queued.start(
            observed_at=queued.updated_at,
            event_id=EventId(f"event-guard-race-{operation}"),
        )
        receipt = _receipt(mutation, operation, "start_run")
        run_repository = _guarded_repository(postgresql_engine, worker_lease_telemetry)
        conditional = (
            RenewLeaseCommand(claim.grant.proof, claim.grant.lease_version)
            if operation == "renew"
            else ReleaseLeaseCommand(claim.grant.proof, claim.grant.lease_version)
        )
        conditional_call = (
            lease_repository.renew(conditional)
            if isinstance(conditional, RenewLeaseCommand)
            else lease_repository.release(conditional)
        )

        guarded, changed = await asyncio.gather(
            run_repository.commit_with_lease(
                proof=claim.grant.proof,
                expected_version=queued.version,
                updated_run=mutation.run,
                new_events=mutation.events,
                receipt=receipt,
            ),
            conditional_call,
            return_exceptions=True,
        )

        if isinstance(guarded, CommitOutcome):
            loaded = await run_repository.load(tenant_id, queued.run_id)
            assert loaded is not None
            assert loaded.status.value == "running"
            assert (
                await run_repository.find_command(
                    tenant_id, receipt.command_id, receipt.intent_fingerprint
                )
                is not None
            )
        else:
            assert isinstance(guarded, WorkerLeaseError)
            assert guarded.code is WorkerLeaseErrorCode.LEASE_NOT_CURRENT
            loaded = await run_repository.load(tenant_id, queued.run_id)
            assert loaded is not None
            assert loaded.status.value == "queued"
            assert (
                await run_repository.find_command(
                    tenant_id, receipt.command_id, receipt.intent_fingerprint
                )
                is None
            )
        assert not isinstance(changed, BaseException)


async def test_guarded_start_racing_cancel_commits_exactly_one_complete_lifecycle(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-guard-cancel-race")
    queued = (await seed_queued_runs(tenant_id, 1))[0]
    claim = await _claim_one(worker_lease_repository, tenant_id)
    assert claim.grant is not None
    started = queued.start(
        observed_at=queued.updated_at,
        event_id=EventId("event-guard-cancel-race-start"),
    )
    cancelled = queued.cancel(
        observed_at=queued.updated_at,
        event_id=EventId("event-guard-cancel-race-cancel"),
        correlation_id=CorrelationId("correlation-guard-cancel-race"),
    )
    start_receipt = _receipt(started, "cancel-race-start", "start_run")
    cancel_receipt = _receipt(cancelled, "cancel-race-cancel", "cancel_run")
    guarded_repository = _guarded_repository(postgresql_engine, worker_lease_telemetry)
    ordinary_repository = PostgreSQLRunRepository(postgresql_engine)

    results = await asyncio.gather(
        guarded_repository.commit_with_lease(
            proof=claim.grant.proof,
            expected_version=queued.version,
            updated_run=started.run,
            new_events=started.events,
            receipt=start_receipt,
        ),
        ordinary_repository.commit(
            expected_version=queued.version,
            updated_run=cancelled.run,
            new_events=cancelled.events,
            receipt=cancel_receipt,
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, CommitOutcome) for result in results) == 1
    losing = next(result for result in results if not isinstance(result, CommitOutcome))
    assert isinstance(losing, RunLifecycleError)
    assert losing.code is RunErrorCode.VERSION_CONFLICT
    loaded = await ordinary_repository.load(tenant_id, queued.run_id)
    assert loaded is not None
    assert loaded.status.value in {"running", "cancelled"}
    async with postgresql_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(run_events)
                .where(run_events.c.tenant_id == str(tenant_id))
            )
            == 2
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(run_command_receipts)
                .where(run_command_receipts.c.tenant_id == str(tenant_id))
            )
            == 2
        )


async def test_guarded_new_write_rejects_stale_run_version_without_receipt(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-guard-version")
    queued = (await seed_queued_runs(tenant_id, 1))[0]
    claim = await _claim_one(worker_lease_repository, tenant_id)
    assert claim.grant is not None
    started = queued.start(
        observed_at=queued.updated_at,
        event_id=EventId("event-guard-version"),
    )
    receipt = _receipt(started, "version", "start_run")
    repository = _guarded_repository(postgresql_engine, worker_lease_telemetry)

    with pytest.raises(RunLifecycleError) as stale:
        await repository.commit_with_lease(
            proof=claim.grant.proof,
            expected_version=queued.version - 1,
            updated_run=started.run,
            new_events=started.events,
            receipt=receipt,
        )
    assert stale.value.code is RunErrorCode.VERSION_CONFLICT
    assert (
        await repository.find_command(tenant_id, receipt.command_id, receipt.intent_fingerprint)
        is None
    )


async def test_100_complete_ownership_fencing_cycles_cover_every_safety_step(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: RepositoryFactory,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId("tenant-complete-fencing-matrix")
    queued_runs = await seed_queued_runs(tenant_id, 100)
    lease_repository = worker_lease_repository_factory()

    class AtExpiryRunRepository(PostgreSQLRunRepository):
        captured_at: datetime

        @staticmethod
        async def _database_now(connection: AsyncConnection) -> datetime:
            return AtExpiryRunRepository.captured_at

    for cycle, queued in enumerate(queued_runs):
        first = await _claim_one(lease_repository, tenant_id)
        assert first.grant is not None
        renewed = await lease_repository.renew(
            RenewLeaseCommand(first.grant.proof, first.grant.lease_version)
        )
        assert renewed.applied is True
        assert renewed.authority.lease_version is not None
        repeated = await lease_repository.renew(
            RenewLeaseCommand(first.grant.proof, first.grant.lease_version)
        )
        assert repeated.applied is False

        wrong_proof = LeaseAuthorityProof(
            tenant_id=first.grant.tenant_id,
            run_id=first.grant.run_id,
            worker_id=first.grant.worker_id,
            claim_id=first.grant.claim_id,
            attempt_no=first.grant.attempt_no,
            token=LeaseToken(bytes([cycle % 251]) * 32),
        )
        assert (await lease_repository.get_authority(wrong_proof)).authoritative is False

        released = await lease_repository.release(
            ReleaseLeaseCommand(first.grant.proof, renewed.authority.lease_version)
        )
        assert released.applied is True
        repeated_release = await lease_repository.release(
            ReleaseLeaseCommand(first.grant.proof, renewed.authority.lease_version)
        )
        assert repeated_release.applied is False

        replacement = await _claim_one(lease_repository, tenant_id)
        assert replacement.grant is not None
        assert replacement.grant.run_id == queued.run_id
        assert replacement.grant.attempt_no.value == first.grant.attempt_no.value + 1
        assert replacement.grant.lease_version.value == renewed.authority.lease_version.value + 2
        assert replacement.grant.token != first.grant.token
        assert (await lease_repository.get_authority(first.grant.proof)).authoritative is False

        started = queued.start(
            observed_at=queued.updated_at,
            event_id=EventId(f"event-complete-fencing-{cycle}"),
        )
        run_repository = _guarded_repository(postgresql_engine, worker_lease_telemetry)
        await run_repository.commit_with_lease(
            proof=replacement.grant.proof,
            expected_version=queued.version,
            updated_run=started.run,
            new_events=started.events,
            receipt=_receipt(started, f"complete-{cycle}", "start_run"),
        )

        AtExpiryRunRepository.captured_at = replacement.grant.lease_expires_at

        expired_repository = AtExpiryRunRepository(
            postgresql_engine,
            telemetry=worker_lease_telemetry,
        )
        unchanged = RunMutation(run=started.run, events=())
        expired_receipt = _receipt(unchanged, f"expired-{cycle}", "consume_budget")
        with pytest.raises(WorkerLeaseError) as expired:
            await expired_repository.commit_with_lease(
                proof=replacement.grant.proof,
                expected_version=started.run.version,
                updated_run=started.run,
                new_events=(),
                receipt=expired_receipt,
            )
        assert expired.value.code is WorkerLeaseErrorCode.LEASE_EXPIRED
        assert (
            await run_repository.find_command(
                tenant_id,
                expired_receipt.command_id,
                expired_receipt.intent_fingerprint,
            )
            is None
        )
