"""Durable Worker lease facts across engine and repository recreation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.commands.worker_leases import ClaimLeaseCommand
from zhiyi.application.ports.worker_lease_observability import LeaseOperationObservation
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.identifiers import TenantId
from zhiyi.domain.worker_leases.identifiers import WorkerId
from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
)
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

pytestmark = pytest.mark.postgresql
SeedQueuedRuns = Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]


class RecordingTelemetry:
    def record_log(self, observation: LeaseOperationObservation) -> None:
        pass

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        pass

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        pass


def _repository(engine: AsyncEngine) -> PostgreSQLWorkerLeaseRepository:
    return PostgreSQLWorkerLeaseRepository(
        engine,
        telemetry=RecordingTelemetry(),
        token_generator=SecureLeaseTokenGenerator(),
    )


async def test_restart_retains_exact_replay_and_never_extends_or_releases(
    seed_queued_runs: SeedQueuedRuns,
    postgresql_engine: AsyncEngine,
    migrated_postgresql_url: str,
) -> None:
    tenant_id = TenantId("tenant-lease-restart")
    seeded = await seed_queued_runs(tenant_id, 1)
    original_repository = _repository(postgresql_engine)
    command = ClaimLeaseCommand(
        tenant_id,
        WorkerId("worker-lease-restart"),
        await original_repository.issue_claim_id(),
    )
    first = await original_repository.claim(command)
    assert first.grant is not None

    reopened_engine = create_postgresql_engine(migrated_postgresql_url, pool_size=4)
    try:
        reopened = _repository(reopened_engine)
        authority = await reopened.get_authority(first.grant.proof)
        replay = await reopened.claim(command)

        assert authority.authoritative is True
        assert authority.lease_version == first.grant.lease_version
        assert authority.heartbeat_at == first.grant.heartbeat_at
        assert authority.lease_expires_at == first.grant.lease_expires_at
        assert replay.replayed is True
        assert replay.grant is not None
        assert replay.grant.token == first.grant.token
        assert replay.grant.lease_expires_at == first.grant.lease_expires_at
        assert replay.grant.currently_authoritative is True
        assert replay.grant.run_id == seeded[0].run_id
    finally:
        await dispose_postgresql_engine(reopened_engine)


async def test_restart_after_expiry_reclaims_queued_run_with_higher_counters(
    seed_queued_runs: SeedQueuedRuns,
    postgresql_engine: AsyncEngine,
    migrated_postgresql_url: str,
) -> None:
    tenant_id = TenantId("tenant-lease-restart-expired")
    await seed_queued_runs(tenant_id, 1)
    original = _repository(postgresql_engine)
    original_command = ClaimLeaseCommand(
        tenant_id,
        WorkerId("worker-before-restart"),
        await original.issue_claim_id(),
    )
    first = await original.claim(original_command)
    assert first.grant is not None
    first_expires_at = first.grant.lease_expires_at

    reopened_engine = create_postgresql_engine(migrated_postgresql_url, pool_size=4)
    try:

        class AfterExpiryRepository(PostgreSQLWorkerLeaseRepository):
            async def _database_now(self, connection: AsyncConnection) -> datetime:
                return first_expires_at + timedelta(microseconds=1)

        reopened = AfterExpiryRepository(
            reopened_engine,
            telemetry=RecordingTelemetry(),
            token_generator=SecureLeaseTokenGenerator(),
        )
        replacement = await reopened.claim(
            ClaimLeaseCommand(
                tenant_id,
                WorkerId("worker-after-restart"),
                await reopened.issue_claim_id(),
            )
        )
        old_replay = await reopened.claim(original_command)

        assert replacement.grant is not None
        assert replacement.grant.attempt_no.value == first.grant.attempt_no.value + 1
        assert replacement.grant.lease_version.value == first.grant.lease_version.value + 1
        assert replacement.grant.token != first.grant.token
        assert old_replay.grant is not None
        assert old_replay.grant.token == first.grant.token
        assert old_replay.grant.currently_authoritative is False
        assert (await reopened.get_authority(first.grant.proof)).authoritative is False
    finally:
        await dispose_postgresql_engine(reopened_engine)


async def test_100_dispose_recreate_cycles_preserve_and_then_fence_authority(
    seed_queued_runs: SeedQueuedRuns,
    migrated_postgresql_url: str,
) -> None:
    class AfterExpiryRepository(PostgreSQLWorkerLeaseRepository):
        captured_at: datetime

        async def _database_now(self, connection: AsyncConnection) -> datetime:
            return self.captured_at

    for cycle in range(100):
        tenant_id = TenantId(f"tenant-restart-cycle-{cycle}")
        await seed_queued_runs(tenant_id, 1)

        original_engine = create_postgresql_engine(migrated_postgresql_url, pool_size=1)
        original_command: ClaimLeaseCommand
        try:
            original = _repository(original_engine)
            original_command = ClaimLeaseCommand(
                tenant_id,
                WorkerId(f"worker-before-cycle-{cycle}"),
                await original.issue_claim_id(),
            )
            first = await original.claim(original_command)
            assert first.grant is not None
        finally:
            await dispose_postgresql_engine(original_engine)

        reopened_engine = create_postgresql_engine(migrated_postgresql_url, pool_size=1)
        try:
            reopened = _repository(reopened_engine)
            authority = await reopened.get_authority(first.grant.proof)
            replay = await reopened.claim(original_command)
            preexpiry = await reopened.claim(
                ClaimLeaseCommand(
                    tenant_id,
                    WorkerId(f"worker-too-early-{cycle}"),
                    await reopened.issue_claim_id(),
                )
            )
            assert authority.authoritative is True
            assert authority.lease_version == first.grant.lease_version
            assert replay.grant is not None and replay.replayed is True
            assert replay.grant.token == first.grant.token
            assert preexpiry.grant is None

            AfterExpiryRepository.captured_at = first.grant.lease_expires_at + timedelta(
                microseconds=1
            )
            after_expiry = AfterExpiryRepository(
                reopened_engine,
                telemetry=RecordingTelemetry(),
                token_generator=SecureLeaseTokenGenerator(),
            )
            replacement = await after_expiry.claim(
                ClaimLeaseCommand(
                    tenant_id,
                    WorkerId(f"worker-after-cycle-{cycle}"),
                    await after_expiry.issue_claim_id(),
                )
            )
            old_replay = await after_expiry.claim(original_command)

            assert replacement.grant is not None
            assert replacement.grant.attempt_no.value == first.grant.attempt_no.value + 1
            assert replacement.grant.lease_version.value == first.grant.lease_version.value + 1
            assert replacement.grant.token != first.grant.token
            assert old_replay.grant is not None and old_replay.replayed is True
            assert old_replay.grant.token == first.grant.token
            assert old_replay.grant.currently_authoritative is False
            assert (await after_expiry.get_authority(first.grant.proof)).authoritative is False
        finally:
            await dispose_postgresql_engine(reopened_engine)
