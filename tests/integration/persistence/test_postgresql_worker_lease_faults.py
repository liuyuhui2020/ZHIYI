"""Real PostgreSQL failure windows for every lease-kernel write operation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    RunRepositoryError,
    RunRepositoryErrorCode,
)
from zhiyi.application.ports.worker_lease_observability import LeaseOperationObservation
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.identifiers import CommandId, EventId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import WorkerId
from zhiyi.domain.worker_leases.models import LeaseClaimOutcome
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

pytestmark = pytest.mark.postgresql

SeedQueuedRuns = Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]


class RecordingTelemetry:
    def __init__(self) -> None:
        self.logs: list[LeaseOperationObservation] = []
        self.metrics: list[LeaseOperationObservation] = []
        self.traces: list[LeaseOperationObservation] = []

    def record_log(self, observation: LeaseOperationObservation) -> None:
        self.logs.append(observation)

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        self.metrics.append(observation)

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        self.traces.append(observation)


class FaultingWorkerLeaseRepository(PostgreSQLWorkerLeaseRepository):
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        terminate: bool = False,
        telemetry: RecordingTelemetry | None = None,
    ) -> None:
        self._fault_engine = engine
        self._terminate = terminate
        super().__init__(
            engine,
            telemetry=telemetry or RecordingTelemetry(),
            token_generator=SecureLeaseTokenGenerator(),
        )

    async def _transaction_boundary(self, name: str, connection: AsyncConnection) -> None:
        if name != "after_lease":
            return
        if not self._terminate:
            raise ConnectionError("fake-password replay_token final-answer hidden_reason")
        backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        async with self._fault_engine.connect() as killer:
            assert (
                await killer.scalar(
                    text("SELECT pg_terminate_backend(:backend_pid)"),
                    {"backend_pid": backend_pid},
                )
                is True
            )


class FaultingGuardedRunRepository(PostgreSQLRunRepository):
    def __init__(self, engine: AsyncEngine, *, terminate: bool = False) -> None:
        self._fault_engine = engine
        self._terminate = terminate
        super().__init__(engine, telemetry=RecordingTelemetry())

    async def _transaction_boundary(self, name: str, connection: AsyncConnection) -> None:
        if name != "before_commit":
            return
        if not self._terminate:
            raise ConnectionError("fake-password replay_token final-answer hidden_reason")
        backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        async with self._fault_engine.connect() as killer:
            assert (
                await killer.scalar(
                    text("SELECT pg_terminate_backend(:backend_pid)"),
                    {"backend_pid": backend_pid},
                )
                is True
            )


class LostAckWorkerLeaseRepository(PostgreSQLWorkerLeaseRepository):
    async def _commit_transaction(self, transaction: AsyncTransaction) -> None:
        await transaction.commit()
        raise ConnectionError("fake-password replay_token final-answer hidden_reason")


class LostAckGuardedRunRepository(PostgreSQLRunRepository):
    async def _commit_transaction(self, transaction: AsyncTransaction) -> None:
        await transaction.commit()
        raise ConnectionError("fake-password replay_token final-answer hidden_reason")


def _worker_repository(engine: AsyncEngine) -> PostgreSQLWorkerLeaseRepository:
    return PostgreSQLWorkerLeaseRepository(
        engine,
        telemetry=RecordingTelemetry(),
        token_generator=SecureLeaseTokenGenerator(),
    )


def _lost_ack_worker(engine: AsyncEngine) -> LostAckWorkerLeaseRepository:
    return LostAckWorkerLeaseRepository(
        engine,
        telemetry=RecordingTelemetry(),
        token_generator=SecureLeaseTokenGenerator(),
    )


def _start_receipt(run: Run, event_id: EventId, suffix: str) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=run.tenant_id,
        command_id=CommandId(f"command-lease-fault-{suffix}"),
        run_id=run.run_id,
        command_type="start_run",
        intent_fingerprint="sha256:" + "c" * 64,
        resulting_status=run.status,
        resulting_version=run.version,
        event_ids=(event_id,),
        created_at=run.updated_at,
    )


async def _claim(
    repository: PostgreSQLWorkerLeaseRepository,
    tenant_id: TenantId,
    index: int,
) -> tuple[ClaimLeaseCommand, LeaseClaimOutcome]:
    command = ClaimLeaseCommand(
        tenant_id,
        WorkerId(f"worker-lease-fault-{index}"),
        await repository.issue_claim_id(),
    )
    return command, await repository.claim(command)


@pytest.mark.parametrize(
    ("lost_ack", "expected_code"),
    [
        (False, WorkerLeaseErrorCode.STORAGE_UNAVAILABLE),
        (True, WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN),
    ],
)
async def test_failure_terminal_observation_is_exactly_once_and_convergence_safe(
    lost_ack: bool,
    expected_code: WorkerLeaseErrorCode,
    seed_queued_runs: SeedQueuedRuns,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId(f"tenant-failure-observation-{lost_ack}")
    await seed_queued_runs(tenant_id, 1)
    telemetry = RecordingTelemetry()
    repository: PostgreSQLWorkerLeaseRepository = (
        LostAckWorkerLeaseRepository(
            postgresql_engine,
            telemetry=telemetry,
            token_generator=SecureLeaseTokenGenerator(),
        )
        if lost_ack
        else FaultingWorkerLeaseRepository(
            postgresql_engine,
            telemetry=telemetry,
        )
    )
    command = ClaimLeaseCommand(
        tenant_id,
        WorkerId("worker-failure-observation"),
        await repository.issue_claim_id(),
    )
    telemetry.logs.clear()
    telemetry.metrics.clear()
    telemetry.traces.clear()

    with pytest.raises(WorkerLeaseError) as caught:
        await repository.claim(command)

    assert caught.value.code is expected_code
    assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 1
    assert telemetry.logs[0] == telemetry.metrics[0] == telemetry.traces[0]
    assert telemetry.logs[0].outcome_code == expected_code.value
    printable = repr((telemetry.logs, telemetry.metrics, telemetry.traces))
    for forbidden in ("fake-password", "replay_token", "final-answer", "hidden_reason"):
        assert forbidden not in printable

    normal = _worker_repository(postgresql_engine)
    converged = await normal.claim(command)
    assert converged.grant is not None
    assert converged.replayed is lost_ack


@pytest.mark.parametrize("operation", ["claim", "renew", "release", "guard"])
@pytest.mark.parametrize("terminate", [False, True], ids=["precommit", "backend-termination"])
async def test_100_known_noncommit_windows_never_leave_partial_facts(
    operation: str,
    terminate: bool,
    seed_queued_runs: SeedQueuedRuns,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId(f"tenant-fault-{operation}-{terminate}")
    queued_runs = await seed_queued_runs(tenant_id, 100)
    normal = _worker_repository(postgresql_engine)
    faulting_worker = FaultingWorkerLeaseRepository(
        postgresql_engine,
        terminate=terminate,
    )
    normal_run = PostgreSQLRunRepository(postgresql_engine)
    faulting_run = FaultingGuardedRunRepository(
        postgresql_engine,
        terminate=terminate,
    )

    for index in range(100):
        if operation == "claim":
            command = ClaimLeaseCommand(
                tenant_id,
                WorkerId(f"worker-fault-claim-{index}"),
                await normal.issue_claim_id(),
            )
            with pytest.raises(WorkerLeaseError) as failed:
                await faulting_worker.claim(command)
            assert failed.value.code is WorkerLeaseErrorCode.STORAGE_UNAVAILABLE
            converged = await normal.claim(command)
            assert converged.grant is not None and converged.replayed is False
            continue

        _, claimed = await _claim(normal, tenant_id, index)
        assert claimed.grant is not None
        if operation == "renew":
            renew = RenewLeaseCommand(
                claimed.grant.proof,
                claimed.grant.lease_version,
            )
            with pytest.raises(WorkerLeaseError) as failed:
                await faulting_worker.renew(renew)
            assert failed.value.code is WorkerLeaseErrorCode.STORAGE_UNAVAILABLE
            authority = await normal.get_authority(claimed.grant.proof)
            assert authority.lease_version == claimed.grant.lease_version
            retried = await normal.renew(renew)
            assert retried.applied is True
            assert retried.authority.lease_version is not None
            assert retried.authority.lease_version.value == claimed.grant.lease_version.value + 1
            continue
        if operation == "release":
            release = ReleaseLeaseCommand(
                claimed.grant.proof,
                claimed.grant.lease_version,
            )
            with pytest.raises(WorkerLeaseError) as failed:
                await faulting_worker.release(release)
            assert failed.value.code is WorkerLeaseErrorCode.STORAGE_UNAVAILABLE
            assert (await normal.get_authority(claimed.grant.proof)).authoritative is True
            retried = await normal.release(release)
            assert retried.applied is True
            continue

        queued = queued_runs[index]
        event_id = EventId(f"event-lease-fault-{terminate}-{index}")
        started = queued.start(observed_at=queued.updated_at, event_id=event_id)
        receipt = _start_receipt(started.run, event_id, f"{terminate}-{index}")
        with pytest.raises(RunRepositoryError) as run_failed:
            await faulting_run.commit_with_lease(
                proof=claimed.grant.proof,
                expected_version=queued.version,
                updated_run=started.run,
                new_events=started.events,
                receipt=receipt,
            )
        assert run_failed.value.code is RunRepositoryErrorCode.STORAGE_UNAVAILABLE
        loaded = await normal_run.load(tenant_id, queued.run_id)
        assert loaded is not None
        assert loaded.status.value == "queued"
        assert (
            await normal_run.find_command(tenant_id, receipt.command_id, receipt.intent_fingerprint)
            is None
        )
        converged_commit = await PostgreSQLRunRepository(
            postgresql_engine,
            telemetry=RecordingTelemetry(),
        ).commit_with_lease(
            proof=claimed.grant.proof,
            expected_version=queued.version,
            updated_run=started.run,
            new_events=started.events,
            receipt=receipt,
        )
        assert converged_commit.replayed is False


@pytest.mark.parametrize("operation", ["claim", "renew", "release", "guard"])
async def test_100_real_commits_with_lost_ack_converge_without_second_mutation(
    operation: str,
    seed_queued_runs: SeedQueuedRuns,
    postgresql_engine: AsyncEngine,
) -> None:
    tenant_id = TenantId(f"tenant-lost-ack-{operation}")
    queued_runs = await seed_queued_runs(tenant_id, 100)
    normal = _worker_repository(postgresql_engine)
    faulting_worker = _lost_ack_worker(postgresql_engine)
    normal_run = PostgreSQLRunRepository(postgresql_engine)
    faulting_run = LostAckGuardedRunRepository(
        postgresql_engine,
        telemetry=RecordingTelemetry(),
    )

    for index in range(100):
        if operation == "claim":
            command = ClaimLeaseCommand(
                tenant_id,
                WorkerId(f"worker-lost-ack-claim-{index}"),
                await normal.issue_claim_id(),
            )
            with pytest.raises(WorkerLeaseError) as failed:
                await faulting_worker.claim(command)
            assert failed.value.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
            claim_replay = await normal.claim(command)
            assert claim_replay.grant is not None and claim_replay.replayed is True
            continue

        _, claimed = await _claim(normal, tenant_id, index)
        assert claimed.grant is not None
        if operation == "renew":
            with pytest.raises(WorkerLeaseError) as failed:
                await faulting_worker.renew(
                    RenewLeaseCommand(claimed.grant.proof, claimed.grant.lease_version)
                )
            assert failed.value.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
            authority = await normal.get_authority(claimed.grant.proof)
            assert authority.lease_version is not None
            assert authority.lease_version.value == claimed.grant.lease_version.value + 1
            continue
        if operation == "release":
            with pytest.raises(WorkerLeaseError) as failed:
                await faulting_worker.release(
                    ReleaseLeaseCommand(claimed.grant.proof, claimed.grant.lease_version)
                )
            assert failed.value.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
            authority = await normal.get_authority(claimed.grant.proof)
            assert authority.authoritative is False
            assert authority.lease_version is not None
            assert authority.lease_version.value == claimed.grant.lease_version.value + 1
            continue

        queued = queued_runs[index]
        event_id = EventId(f"event-lease-lost-ack-{index}")
        started = queued.start(observed_at=queued.updated_at, event_id=event_id)
        receipt = _start_receipt(started.run, event_id, f"lost-ack-{index}")
        with pytest.raises(RunRepositoryError) as run_failed:
            await faulting_run.commit_with_lease(
                proof=claimed.grant.proof,
                expected_version=queued.version,
                updated_run=started.run,
                new_events=started.events,
                receipt=receipt,
            )
        assert run_failed.value.code is RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN
        run_replay = await normal_run.commit(
            expected_version=999,
            updated_run=started.run,
            new_events=started.events,
            receipt=receipt,
        )
        assert run_replay.replayed is True
