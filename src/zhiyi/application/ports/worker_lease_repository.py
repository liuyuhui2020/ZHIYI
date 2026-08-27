"""Framework-neutral Worker lease persistence and fenced-write contracts."""

from __future__ import annotations

from typing import Protocol

from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    CommitOutcome,
    RunRepository,
)
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.events import RunEvent
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.errors import (
    WorkerLeaseError,
    WorkerLeaseErrorCode,
    safe_worker_lease_error_message,
)
from zhiyi.domain.worker_leases.identifiers import LeaseClaimId
from zhiyi.domain.worker_leases.models import (
    ConditionalLeaseOutcome,
    InactiveRunningCursor,
    InactiveRunningLease,
    InactiveRunningPage,
    LeaseAuthority,
    LeaseAuthorityProof,
    LeaseClaimOutcome,
)


class WorkerLeaseRepository(Protocol):
    async def issue_claim_id(self) -> LeaseClaimId: ...

    async def claim(self, command: ClaimLeaseCommand) -> LeaseClaimOutcome: ...

    async def get_authority(self, proof: LeaseAuthorityProof) -> LeaseAuthority: ...

    async def renew(self, command: RenewLeaseCommand) -> ConditionalLeaseOutcome: ...

    async def release(self, command: ReleaseLeaseCommand) -> ConditionalLeaseOutcome: ...

    async def get_inactive_running(
        self,
        tenant_id: TenantId,
        run_id: RunId,
    ) -> InactiveRunningLease | None: ...

    async def list_inactive_running(
        self,
        tenant_id: TenantId,
        *,
        cursor: InactiveRunningCursor | None = None,
        limit: int = 100,
    ) -> InactiveRunningPage: ...


class LeaseGuardedRunRepository(RunRepository, Protocol):
    async def commit_with_lease(
        self,
        *,
        proof: LeaseAuthorityProof,
        expected_version: int,
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> CommitOutcome: ...


__all__ = [
    "LeaseGuardedRunRepository",
    "WorkerLeaseError",
    "WorkerLeaseErrorCode",
    "WorkerLeaseRepository",
    "safe_worker_lease_error_message",
]
