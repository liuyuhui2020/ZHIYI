"""Provider-neutral behavioral contract for WorkerLeaseRepository adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.worker_lease_observability import LeaseOperationObservation
from zhiyi.application.ports.worker_lease_repository import WorkerLeaseRepository
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import RunBudget
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    CommandId,
    EventId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import (
    LeaseAttemptNo,
    LeaseClaimId,
    LeaseDurationSeconds,
    LeaseToken,
    LeaseVersion,
    WorkerId,
)
from zhiyi.domain.worker_leases.models import LeaseAuthority, LeaseAuthorityProof

NOW = datetime(2026, 8, 27, tzinfo=UTC)


class RecordingWorkerLeaseTelemetry:
    def __init__(self) -> None:
        self.logs: list[LeaseOperationObservation] = []
        self.metrics: list[LeaseOperationObservation] = []
        self.traces: list[LeaseOperationObservation] = []
        self.fail_channel: str | None = None
        self.attempted_channels: list[str] = []

    def _record(
        self,
        channel: str,
        target: list[LeaseOperationObservation],
        observation: LeaseOperationObservation,
    ) -> None:
        self.attempted_channels.append(channel)
        target.append(observation)
        if self.fail_channel == channel:
            raise RuntimeError(f"{channel} unavailable")

    def record_log(self, observation: LeaseOperationObservation) -> None:
        self._record("log", self.logs, observation)

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        self._record("metric", self.metrics, observation)

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        self._record("trace", self.traces, observation)

    def clear(self) -> None:
        self.logs.clear()
        self.metrics.clear()
        self.traces.clear()
        self.attempted_channels.clear()


def queued_run(*, tenant: str = "tenant-contract", run: str = "run-contract") -> RunMutation:
    tenant_id = TenantId(tenant)
    return Run.create(
        tenant_id=tenant_id,
        run_id=RunId(run),
        task_id=TaskId(f"task-{run}"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-contract"),
            version_id=AgentVersionId("version-contract"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=10,
            max_model_calls=10,
            max_tool_calls=10,
            max_input_tokens=100,
            max_output_tokens=100,
            max_total_tokens=200,
            max_cost=Decimal("10"),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId(f"event-{run}"),
    )


async def persist_queued(
    repository: PostgreSQLRunRepository,
    mutation: RunMutation,
) -> Run:
    from zhiyi.application.ports.run_repository import CommandReceipt

    await repository.commit(
        expected_version=0,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=CommandReceipt(
            tenant_id=mutation.run.tenant_id,
            command_id=CommandId(f"command-{mutation.run.run_id}"),
            run_id=mutation.run.run_id,
            command_type="create_run",
            intent_fingerprint="sha256:" + "b" * 64,
            resulting_status=mutation.run.status,
            resulting_version=mutation.run.version,
            event_ids=tuple(event.event_id for event in mutation.events),
            created_at=NOW,
        ),
    )
    return mutation.run


class WorkerLeaseRepositoryContract:
    """Inherited by each durable provider binding; currently PostgreSQL only."""

    @pytest.fixture
    def repository(self) -> WorkerLeaseRepository:
        raise NotImplementedError

    @pytest.fixture
    def run_repository(self) -> PostgreSQLRunRepository:
        raise NotImplementedError

    @pytest.fixture
    def telemetry(self) -> RecordingWorkerLeaseTelemetry:
        raise NotImplementedError

    async def test_issue_claim_id_and_claim_one_queued_run(
        self,
        repository: WorkerLeaseRepository,
        run_repository: PostgreSQLRunRepository,
    ) -> None:
        queued = await persist_queued(run_repository, queued_run())
        claim_id = await repository.issue_claim_id()

        outcome = await repository.claim(
            ClaimLeaseCommand(
                tenant_id=queued.tenant_id,
                worker_id=WorkerId("worker-contract"),
                claim_id=claim_id,
            )
        )

        assert claim_id.value.version == 7
        assert outcome.code.value == "claimed"
        assert outcome.grant is not None
        assert outcome.grant.run_id == queued.run_id
        assert outcome.grant.tenant_id == queued.tenant_id
        assert outcome.grant.worker_id == WorkerId("worker-contract")
        assert outcome.grant.claim_id == claim_id
        assert outcome.grant.duration == LeaseDurationSeconds(30)
        assert outcome.grant.attempt_no.value == 1
        assert outcome.grant.lease_version.value == 1
        assert outcome.grant.currently_authoritative is True
        assert outcome.may_start_new_work is True

    async def test_no_work_is_an_immutable_exact_replay(
        self,
        repository: WorkerLeaseRepository,
    ) -> None:
        claim_id = await repository.issue_claim_id()
        command = ClaimLeaseCommand(
            tenant_id=TenantId("tenant-empty"),
            worker_id=WorkerId("worker-empty"),
            claim_id=claim_id,
        )

        first = await repository.claim(command)
        replay = await repository.claim(command)

        assert first.code.value == "no_work"
        assert first.replayed is False
        assert replay.code.value == "no_work"
        assert replay.replayed is True
        assert replay.grant is None
        assert replay.may_start_new_work is False

    async def test_success_replay_returns_exact_original_token_and_current_authority(
        self,
        repository: WorkerLeaseRepository,
        run_repository: PostgreSQLRunRepository,
    ) -> None:
        queued = await persist_queued(
            run_repository,
            queued_run(tenant="tenant-replay", run="run-replay"),
        )
        command = ClaimLeaseCommand(
            tenant_id=queued.tenant_id,
            worker_id=WorkerId("worker-replay"),
            claim_id=await repository.issue_claim_id(),
            duration=LeaseDurationSeconds(10),
        )

        first = await repository.claim(command)
        replay = await repository.claim(command)

        assert first.grant is not None
        assert replay.grant is not None
        assert replay.replayed is True
        assert replay.grant.token.value == first.grant.token.value
        assert replay.grant.run_id == first.grant.run_id
        assert replay.grant.attempt_no == first.grant.attempt_no
        assert replay.grant.lease_version == first.grant.lease_version
        assert replay.grant.acquired_at == first.grant.acquired_at
        assert replay.grant.lease_expires_at == first.grant.lease_expires_at
        assert replay.grant.currently_authoritative is True

    async def test_same_claim_id_with_different_intent_conflicts_without_disclosure(
        self,
        repository: WorkerLeaseRepository,
    ) -> None:
        claim_id = await repository.issue_claim_id()
        first = ClaimLeaseCommand(TenantId("tenant-conflict"), WorkerId("worker-a"), claim_id)
        changed = ClaimLeaseCommand(TenantId("tenant-conflict"), WorkerId("worker-b"), claim_id)
        await repository.claim(first)

        with pytest.raises(WorkerLeaseError) as caught:
            await repository.claim(changed)

        assert caught.value.code is WorkerLeaseErrorCode.IDEMPOTENCY_CONFLICT
        assert "worker-a" not in str(caught.value)
        assert "worker-a" not in repr(caught.value)

    async def test_claim_changes_only_lease_facts_not_run_lifecycle(
        self,
        repository: WorkerLeaseRepository,
        run_repository: PostgreSQLRunRepository,
    ) -> None:
        queued = await persist_queued(
            run_repository,
            queued_run(tenant="tenant-immutable", run="run-immutable"),
        )
        before_events = await run_repository.list_events(queued.tenant_id, queued.run_id)
        before = await run_repository.load(queued.tenant_id, queued.run_id)

        await repository.claim(
            ClaimLeaseCommand(
                queued.tenant_id,
                WorkerId("worker-immutable"),
                await repository.issue_claim_id(),
            )
        )

        assert await run_repository.load(queued.tenant_id, queued.run_id) == before
        assert await run_repository.list_events(queued.tenant_id, queued.run_id) == before_events

    async def test_each_public_operation_emits_one_safe_terminal_observation_per_channel(
        self,
        repository: WorkerLeaseRepository,
        telemetry: RecordingWorkerLeaseTelemetry,
    ) -> None:
        telemetry.clear()
        claim_id = await repository.issue_claim_id()

        assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 1
        assert telemetry.logs[0].operation.value == "issue_claim_id"
        telemetry.clear()
        await repository.claim(
            ClaimLeaseCommand(TenantId("tenant-telemetry"), WorkerId("worker-telemetry"), claim_id)
        )
        assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 1
        assert telemetry.logs[0] == telemetry.metrics[0] == telemetry.traces[0]
        assert telemetry.logs[0].operation.value == "claim"
        assert telemetry.logs[0].empty is True
        printable = repr(telemetry.logs[0])
        assert "token" not in printable.lower()
        assert "digest" not in printable.lower()
        assert "fingerprint" not in printable.lower()

    async def test_authority_renew_stale_confirmation_and_release(
        self,
        repository: WorkerLeaseRepository,
        run_repository: PostgreSQLRunRepository,
    ) -> None:
        queued = await persist_queued(
            run_repository,
            queued_run(tenant="tenant-conditional", run="run-conditional"),
        )
        claimed = await repository.claim(
            ClaimLeaseCommand(
                queued.tenant_id,
                WorkerId("worker-conditional"),
                await repository.issue_claim_id(),
                LeaseDurationSeconds(10),
            )
        )
        assert claimed.grant is not None
        proof = claimed.grant.proof

        current = await repository.get_authority(proof)
        assert current.authoritative is True
        assert current.lease_version == LeaseVersion(1)

        renewed = await repository.renew(
            RenewLeaseCommand(
                proof=proof,
                expected_version=LeaseVersion(1),
                duration=LeaseDurationSeconds(30),
            )
        )
        assert renewed.applied is True
        assert renewed.authority.authoritative is True
        assert renewed.authority.lease_version == LeaseVersion(2)
        assert renewed.renew_by_at is not None
        assert renewed.authority.heartbeat_at is not None
        assert renewed.renew_by_at == renewed.authority.heartbeat_at + timedelta(seconds=10)

        stale = await repository.renew(
            RenewLeaseCommand(
                proof=proof,
                expected_version=LeaseVersion(1),
                duration=LeaseDurationSeconds(30),
            )
        )
        assert stale.applied is False
        assert stale.authority.authoritative is True
        assert stale.authority.lease_version == LeaseVersion(2)
        assert stale.renew_by_at is None

        released = await repository.release(
            ReleaseLeaseCommand(proof=proof, expected_version=LeaseVersion(2))
        )
        assert released.applied is True
        assert released.authority.authoritative is False
        assert released.authority.reason is WorkerLeaseErrorCode.LEASE_NOT_CURRENT
        assert released.may_start_new_work is False

        repeated = await repository.release(
            ReleaseLeaseCommand(proof=proof, expected_version=LeaseVersion(2))
        )
        assert repeated.applied is False
        assert repeated.authority.authoritative is False

    async def test_wrong_complete_proof_is_indistinguishable_and_cannot_mutate(
        self,
        repository: WorkerLeaseRepository,
        run_repository: PostgreSQLRunRepository,
    ) -> None:
        queued = await persist_queued(
            run_repository,
            queued_run(tenant="tenant-proof", run="run-proof"),
        )
        claimed = await repository.claim(
            ClaimLeaseCommand(
                queued.tenant_id,
                WorkerId("worker-proof"),
                await repository.issue_claim_id(),
            )
        )
        assert claimed.grant is not None
        valid = claimed.grant.proof
        wrong = LeaseAuthorityProof(
            tenant_id=valid.tenant_id,
            run_id=valid.run_id,
            worker_id=valid.worker_id,
            claim_id=valid.claim_id,
            attempt_no=LeaseAttemptNo(valid.attempt_no.value),
            token=LeaseToken(b"z" * 32),
        )

        authority = await repository.get_authority(wrong)
        renewal = await repository.renew(RenewLeaseCommand(wrong, LeaseVersion(1)))
        release = await repository.release(ReleaseLeaseCommand(wrong, LeaseVersion(1)))

        assert authority.authoritative is False
        assert authority.reason is WorkerLeaseErrorCode.LEASE_NOT_CURRENT
        assert authority.lease_version is None
        assert renewal.applied is False
        assert renewal.authority == LeaseAuthority.not_current()
        assert release.applied is False
        assert release.authority == LeaseAuthority.not_current()

    async def test_authority_renew_and_release_each_emit_one_terminal_observation(
        self,
        repository: WorkerLeaseRepository,
        run_repository: PostgreSQLRunRepository,
        telemetry: RecordingWorkerLeaseTelemetry,
    ) -> None:
        queued = await persist_queued(
            run_repository,
            queued_run(tenant="tenant-conditional-observe", run="run-conditional-observe"),
        )
        claimed = await repository.claim(
            ClaimLeaseCommand(
                queued.tenant_id,
                WorkerId("worker-conditional-observe"),
                await repository.issue_claim_id(),
            )
        )
        assert claimed.grant is not None
        proof = claimed.grant.proof

        for operation in (
            lambda: repository.get_authority(proof),
            lambda: repository.renew(RenewLeaseCommand(proof, LeaseVersion(1))),
            lambda: repository.release(ReleaseLeaseCommand(proof, LeaseVersion(2))),
        ):
            telemetry.clear()
            await operation()
            assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 1
            assert telemetry.logs[0] == telemetry.metrics[0] == telemetry.traces[0]

    @pytest.mark.parametrize("failed_channel", ["log", "metric", "trace"])
    async def test_telemetry_channel_failure_preserves_public_business_result(
        self,
        failed_channel: str,
        repository: WorkerLeaseRepository,
        telemetry: RecordingWorkerLeaseTelemetry,
    ) -> None:
        claim_id = await repository.issue_claim_id()
        telemetry.clear()
        telemetry.fail_channel = failed_channel
        try:
            outcome = await repository.claim(
                ClaimLeaseCommand(
                    TenantId(f"tenant-channel-{failed_channel}"),
                    WorkerId("worker-channel-failure"),
                    claim_id,
                )
            )
        finally:
            telemetry.fail_channel = None

        assert outcome.code.value == "no_work"
        assert outcome.replayed is False
        assert telemetry.attempted_channels == ["log", "metric", "trace"]
        assert len(telemetry.logs) == len(telemetry.metrics) == len(telemetry.traces) == 1
        assert telemetry.logs[0] == telemetry.metrics[0] == telemetry.traces[0]


def fixed_claim_id() -> LeaseClaimId:
    return LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000001"))
