"""Tenant-indistinguishable Worker lease operations and safe projections."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.application.ports.worker_lease_observability import WorkerLeaseTelemetry
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.identifiers import CommandId, EventId, RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import LeaseToken, WorkerId
from zhiyi.domain.worker_leases.models import InactiveRunningCursor, LeaseAuthorityProof
from zhiyi.infrastructure.security.lease_tokens import digest_lease_token

pytestmark = pytest.mark.postgresql
SeedQueuedRuns = Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]


def _start_receipt(run: Run, event_id: EventId) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=run.tenant_id,
        command_id=CommandId(f"command-tenant-start-{run.tenant_id}"),
        run_id=run.run_id,
        command_type="start_run",
        intent_fingerprint="sha256:" + "d" * 64,
        resulting_status=run.status,
        resulting_version=run.version,
        event_ids=(event_id,),
        created_at=run.updated_at,
    )


async def test_colliding_identifiers_never_cross_tenant_for_any_public_operation(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository: PostgreSQLWorkerLeaseRepository,
    worker_lease_telemetry: WorkerLeaseTelemetry,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_a = TenantId("tenant-isolation-a")
    tenant_b = TenantId("tenant-isolation-b")
    run_a = (await seed_queued_runs(tenant_a, 1))[0]
    run_b = (await seed_queued_runs(tenant_b, 1))[0]
    assert run_a.run_id == run_b.run_id
    shared_claim_id = await worker_lease_repository.issue_claim_id()
    command_a = ClaimLeaseCommand(tenant_a, WorkerId("worker-shared"), shared_claim_id)
    command_b = ClaimLeaseCommand(tenant_b, WorkerId("worker-shared"), shared_claim_id)
    claim_a = await worker_lease_repository.claim(command_a)
    claim_b = await worker_lease_repository.claim(command_b)
    assert claim_a.grant is not None and claim_b.grant is not None
    assert claim_a.grant.token != claim_b.grant.token
    replay_a = await worker_lease_repository.claim(command_a)
    replay_b = await worker_lease_repository.claim(command_b)
    assert replay_a.grant is not None and replay_b.grant is not None
    assert replay_a.grant.token == claim_a.grant.token
    assert replay_b.grant.token == claim_b.grant.token

    foreign = LeaseAuthorityProof(
        tenant_id=tenant_b,
        run_id=RunId(str(run_a.run_id)),
        worker_id=claim_a.grant.worker_id,
        claim_id=claim_a.grant.claim_id,
        attempt_no=claim_a.grant.attempt_no,
        token=claim_a.grant.token,
    )
    authority = await worker_lease_repository.get_authority(foreign)
    renewal = await worker_lease_repository.renew(
        RenewLeaseCommand(foreign, claim_a.grant.lease_version)
    )
    release = await worker_lease_repository.release(
        ReleaseLeaseCommand(foreign, claim_a.grant.lease_version)
    )
    assert authority.authoritative is False and authority.lease_version is None
    assert renewal.applied is False and renewal.authority.lease_version is None
    assert release.applied is False and release.authority.lease_version is None

    started_b = run_b.start(
        observed_at=run_b.updated_at,
        event_id=EventId("event-tenant-isolation-b"),
    )
    run_repository = PostgreSQLRunRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
    )
    receipt_b = _start_receipt(started_b.run, started_b.events[0].event_id)
    with pytest.raises(WorkerLeaseError) as guarded:
        await run_repository.commit_with_lease(
            proof=foreign,
            expected_version=run_b.version,
            updated_run=started_b.run,
            new_events=started_b.events,
            receipt=receipt_b,
        )
    assert guarded.value.code is WorkerLeaseErrorCode.LEASE_NOT_CURRENT
    assert (
        await run_repository.find_command(
            tenant_b,
            receipt_b.command_id,
            receipt_b.intent_fingerprint,
        )
        is None
    )

    started_a = run_a.start(
        observed_at=run_a.updated_at,
        event_id=EventId("event-tenant-isolation-a"),
    )
    await run_repository.commit_with_lease(
        proof=claim_a.grant.proof,
        expected_version=run_a.version,
        updated_run=started_a.run,
        new_events=started_a.events,
        receipt=_start_receipt(started_a.run, started_a.events[0].event_id),
    )
    await worker_lease_repository.release(
        ReleaseLeaseCommand(claim_a.grant.proof, claim_a.grant.lease_version)
    )
    assert await worker_lease_repository.get_inactive_running(tenant_b, run_b.run_id) is None
    assert (await worker_lease_repository.list_inactive_running(tenant_b)).items == ()

    candidate = await worker_lease_repository.get_inactive_running(tenant_a, run_a.run_id)
    assert candidate is not None
    foreign_cursor = InactiveRunningCursor(
        tenant_id=tenant_a,
        as_of=datetime.now(UTC),
        last_authority_ended_at=candidate.authority_ended_at,
        last_run_id=candidate.run_id,
    )
    with pytest.raises(WorkerLeaseError) as cursor_error:
        await worker_lease_repository.list_inactive_running(
            tenant_b,
            cursor=foreign_cursor,
        )
    assert cursor_error.value.code is WorkerLeaseErrorCode.INVALID_INPUT

    printable = " ".join(
        (
            str(guarded.value),
            repr(guarded.value),
            str(cursor_error.value),
            repr(cursor_error.value),
        )
    )
    for forbidden in (
        str(claim_a.grant.claim_id),
        str(claim_a.grant.worker_id),
        str(claim_a.grant.lease_version.value),
        claim_a.grant.token.value.hex(),
    ):
        assert forbidden not in printable


async def test_sensitive_sentinels_never_enter_public_results_or_terminal_channels(
    seed_queued_runs: SeedQueuedRuns,
    worker_lease_repository_factory: Callable[..., PostgreSQLWorkerLeaseRepository],
) -> None:
    class SentinelTokenGenerator:
        def new_token(self) -> LeaseToken:
            return LeaseToken(b"RAW-TOKEN-SENTINEL".ljust(32, b"!"))

    class RecordingTelemetry:
        def __init__(self) -> None:
            self.logs: list[Any] = []
            self.metrics: list[Any] = []
            self.traces: list[Any] = []

        def record_log(self, observation: object) -> None:
            self.logs.append(observation)

        def record_metric(self, observation: object) -> None:
            self.metrics.append(observation)

        def record_trace(self, observation: object) -> None:
            self.traces.append(observation)

    tenant_id = TenantId("tenant-sensitive-sentinels")
    await seed_queued_runs(tenant_id, 1)
    telemetry = RecordingTelemetry()
    repository = worker_lease_repository_factory(
        telemetry=telemetry,
        token_generator=SentinelTokenGenerator(),
    )
    claim_id = await repository.issue_claim_id()
    telemetry.logs.clear()
    telemetry.metrics.clear()
    telemetry.traces.clear()
    command = ClaimLeaseCommand(
        tenant_id,
        WorkerId("worker-sensitive-sentinels"),
        claim_id,
    )
    outcome = await repository.claim(command)
    assert outcome.grant is not None
    assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 1
    with pytest.raises(WorkerLeaseError) as conflict:
        await repository.claim(
            ClaimLeaseCommand(tenant_id, WorkerId("worker-changed-intent"), claim_id)
        )
    assert conflict.value.code is WorkerLeaseErrorCode.IDEMPOTENCY_CONFLICT
    assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 2

    raw_token = outcome.grant.token.value
    digest = digest_lease_token(outcome.grant.token)
    printable = " ".join(
        (
            str(outcome),
            repr(outcome),
            str(conflict.value),
            repr(conflict.value),
            repr(telemetry.logs),
            repr(telemetry.metrics),
            repr(telemetry.traces),
        )
    )
    for forbidden in (
        raw_token.hex(),
        digest.hex(),
        command.intent_fingerprint,
        "RAW-TOKEN-SENTINEL",
        "postgresql://admin:secret@host/db",
        "final-answer",
        "hidden_reason",
    ):
        assert forbidden not in printable
