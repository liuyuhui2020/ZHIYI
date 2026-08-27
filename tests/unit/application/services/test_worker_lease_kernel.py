"""Framework-neutral Worker Lease Kernel orchestration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.worker_lease_repository import (
    WorkerLeaseError,
    WorkerLeaseErrorCode,
)
from zhiyi.application.services.worker_lease_kernel import WorkerLeaseKernel
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
    ConditionalLeaseOutcome,
    InactiveRunningCursor,
    InactiveRunningLease,
    InactiveRunningPage,
    LeaseAuthority,
    LeaseAuthorityProof,
    LeaseClaimOutcome,
    LeaseGrant,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)


def _claim_id() -> LeaseClaimId:
    return LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000001"))


def _proof() -> LeaseAuthorityProof:
    return LeaseAuthorityProof(
        tenant_id=TenantId("tenant-1"),
        run_id=RunId("run-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=_claim_id(),
        attempt_no=LeaseAttemptNo(1),
        token=LeaseToken(b"x" * 32),
    )


def _grant(*, current: bool = True) -> LeaseGrant:
    proof = _proof()
    return LeaseGrant(
        tenant_id=proof.tenant_id,
        run_id=proof.run_id,
        worker_id=proof.worker_id,
        claim_id=proof.claim_id,
        token=proof.token,
        attempt_no=proof.attempt_no,
        lease_version=LeaseVersion(1),
        duration=LeaseDurationSeconds(30),
        acquired_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
        renew_by_at=NOW + timedelta(seconds=10),
        currently_authoritative=current,
    )


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.results: dict[str, object] = {}
        self.errors: dict[str, WorkerLeaseError] = {}

    async def _invoke(self, name: str, *args: object, **kwargs: object) -> object:
        self.calls.append((name, args, kwargs))
        error = self.errors.get(name)
        if error is not None:
            raise error
        return self.results[name]

    async def issue_claim_id(self) -> LeaseClaimId:
        return cast(LeaseClaimId, await self._invoke("issue_claim_id"))

    async def claim(self, command: ClaimLeaseCommand) -> LeaseClaimOutcome:
        return cast(LeaseClaimOutcome, await self._invoke("claim", command))

    async def get_authority(self, proof: LeaseAuthorityProof) -> LeaseAuthority:
        return cast(LeaseAuthority, await self._invoke("get_authority", proof))

    async def renew(self, command: RenewLeaseCommand) -> ConditionalLeaseOutcome:
        return cast(ConditionalLeaseOutcome, await self._invoke("renew", command))

    async def release(self, command: ReleaseLeaseCommand) -> ConditionalLeaseOutcome:
        return cast(ConditionalLeaseOutcome, await self._invoke("release", command))

    async def get_inactive_running(
        self, tenant_id: TenantId, run_id: RunId
    ) -> InactiveRunningLease | None:
        return cast(
            InactiveRunningLease | None,
            await self._invoke("get_inactive_running", tenant_id, run_id),
        )

    async def list_inactive_running(
        self,
        tenant_id: TenantId,
        *,
        cursor: InactiveRunningCursor | None = None,
        limit: int = 100,
    ) -> InactiveRunningPage:
        return cast(
            InactiveRunningPage,
            await self._invoke("list_inactive_running", tenant_id, cursor=cursor, limit=limit),
        )


@pytest.mark.asyncio
async def test_service_delegates_without_starting_or_interrupting_work() -> None:
    repository = RecordingRepository()
    claim_id = _claim_id()
    claim = ClaimLeaseCommand(TenantId("tenant-1"), WorkerId("worker-1"), claim_id)
    proof = _proof()
    renew = RenewLeaseCommand(proof, LeaseVersion(1))
    release = ReleaseLeaseCommand(proof, LeaseVersion(1))
    no_work = LeaseClaimOutcome.no_work()
    not_current = LeaseAuthority.not_current()
    conditional = ConditionalLeaseOutcome(applied=False, authority=not_current)
    empty_page = InactiveRunningPage(items=(), next_cursor=None)
    repository.results.update(
        issue_claim_id=claim_id,
        claim=no_work,
        get_authority=not_current,
        renew=conditional,
        release=conditional,
        get_inactive_running=None,
        list_inactive_running=empty_page,
    )
    kernel = WorkerLeaseKernel(repository=repository)

    assert await kernel.issue_claim_id() == claim_id
    assert await kernel.claim(claim) is no_work
    assert await kernel.get_authority(proof) is not_current
    assert await kernel.renew(renew) is conditional
    assert await kernel.release(release) is conditional
    assert await kernel.get_inactive_running(proof.tenant_id, proof.run_id) is None
    assert await kernel.list_inactive_running(proof.tenant_id) is empty_page
    assert [name for name, _, _ in repository.calls] == [
        "issue_claim_id",
        "claim",
        "get_authority",
        "renew",
        "release",
        "get_inactive_running",
        "list_inactive_running",
    ]
    assert not hasattr(kernel, "start_work")
    assert not hasattr(kernel, "interrupt_work")
    assert not hasattr(kernel, "poll")


def test_only_a_current_grant_or_authority_may_start_new_work() -> None:
    current_grant = LeaseClaimOutcome.claimed(_grant(current=True))
    replayed_expired = LeaseClaimOutcome.claimed(_grant(current=False), replayed=True)
    no_work = LeaseClaimOutcome.no_work()
    current = LeaseAuthority.current(
        lease_version=LeaseVersion(2),
        acquired_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=5),
    )
    not_current = LeaseAuthority.not_current()
    expired = LeaseAuthority.expired(
        lease_version=LeaseVersion(2),
        acquired_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW,
    )

    assert current_grant.may_start_new_work is True
    assert current.may_start_new_work is True
    assert replayed_expired.may_start_new_work is False
    assert no_work.may_start_new_work is False
    assert not_current.may_start_new_work is False
    assert expired.may_start_new_work is False
    assert ConditionalLeaseOutcome(applied=False, authority=current).may_start_new_work is False
    assert ConditionalLeaseOutcome(applied=True, authority=not_current).may_start_new_work is False


@pytest.mark.asyncio
async def test_renew_and_release_delegate_exact_conditions_and_do_not_retry() -> None:
    repository = RecordingRepository()
    proof = _proof()
    renew = RenewLeaseCommand(
        proof=proof,
        expected_version=LeaseVersion(7),
        duration=LeaseDurationSeconds(10),
    )
    release = ReleaseLeaseCommand(proof=proof, expected_version=LeaseVersion(8))
    renewed_authority = LeaseAuthority.current(
        lease_version=LeaseVersion(8),
        acquired_at=NOW,
        heartbeat_at=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(seconds=13),
    )
    renewed = ConditionalLeaseOutcome(
        applied=True,
        authority=renewed_authority,
        renew_by_at=NOW + timedelta(seconds=3, microseconds=333_333),
    )
    released = ConditionalLeaseOutcome(
        applied=True,
        authority=LeaseAuthority.not_current(),
    )
    repository.results.update(renew=renewed, release=released)
    kernel = WorkerLeaseKernel(repository=repository)

    assert await kernel.renew(renew) is renewed
    assert await kernel.release(release) is released
    assert repository.calls == [
        ("renew", (renew,), {}),
        ("release", (release,), {}),
    ]
    assert renewed.may_start_new_work is True
    assert released.may_start_new_work is False
    assert not hasattr(kernel, "retry_renew")
    assert not hasattr(kernel, "retry_release")


def test_conditional_outcomes_fail_closed_for_every_nonapplied_authority_state() -> None:
    current = LeaseAuthority.current(
        lease_version=LeaseVersion(3),
        acquired_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=10),
    )
    expired = LeaseAuthority.expired(
        lease_version=LeaseVersion(3),
        acquired_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW,
    )

    assert ConditionalLeaseOutcome(applied=False, authority=current).may_start_new_work is False
    assert (
        ConditionalLeaseOutcome(
            applied=False, authority=LeaseAuthority.not_current()
        ).may_start_new_work
        is False
    )
    assert ConditionalLeaseOutcome(applied=False, authority=expired).may_start_new_work is False
    assert ConditionalLeaseOutcome(applied=True, authority=expired).may_start_new_work is False


def test_renew_by_uses_microsecond_floor_for_default_and_minimum_duration() -> None:
    captured = NOW + timedelta(microseconds=1)

    assert WorkerLeaseKernel.calculate_renew_by(
        captured, LeaseDurationSeconds(10)
    ) == captured + timedelta(microseconds=3_333_333)
    assert WorkerLeaseKernel.calculate_renew_by(
        captured, LeaseDurationSeconds()
    ) == captured + timedelta(seconds=10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        WorkerLeaseErrorCode.LEASE_NOT_CURRENT,
        WorkerLeaseErrorCode.LEASE_EXPIRED,
        WorkerLeaseErrorCode.STORAGE_UNAVAILABLE,
        WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN,
    ],
)
async def test_service_propagates_safe_failures_without_work_side_effects(
    code: WorkerLeaseErrorCode,
) -> None:
    repository = RecordingRepository()
    repository.errors["claim"] = WorkerLeaseError(code)
    kernel = WorkerLeaseKernel(repository=repository)
    command = ClaimLeaseCommand(TenantId("tenant-1"), WorkerId("worker-1"), _claim_id())

    with pytest.raises(WorkerLeaseError) as raised:
        await kernel.claim(command)

    assert raised.value.code is code
    assert repository.calls == [("claim", (command,), {})]
    assert not hasattr(kernel, "start_work")
    assert not hasattr(kernel, "interrupt_work")
