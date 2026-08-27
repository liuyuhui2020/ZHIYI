"""Framework-neutral coordination service for Worker lease persistence operations."""

from __future__ import annotations

from datetime import datetime

from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.worker_lease_repository import WorkerLeaseRepository
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.identifiers import LeaseClaimId, LeaseDurationSeconds
from zhiyi.domain.worker_leases.models import (
    ConditionalLeaseOutcome,
    InactiveRunningCursor,
    InactiveRunningLease,
    InactiveRunningPage,
    LeaseAuthority,
    LeaseAuthorityProof,
    LeaseClaimOutcome,
    renew_by_at,
)


class WorkerLeaseKernel:
    """Delegate lease operations without polling or executing Worker-owned work."""

    def __init__(self, *, repository: WorkerLeaseRepository) -> None:
        self._repository = repository

    @staticmethod
    def claim_intent_fingerprint(command: ClaimLeaseCommand) -> str:
        if not isinstance(command, ClaimLeaseCommand):
            raise TypeError("command must be ClaimLeaseCommand")
        return command.intent_fingerprint

    @staticmethod
    def calculate_renew_by(
        captured_at: datetime,
        duration: LeaseDurationSeconds,
    ) -> datetime:
        return renew_by_at(captured_at, duration)

    async def issue_claim_id(self) -> LeaseClaimId:
        return await self._repository.issue_claim_id()

    async def claim(self, command: ClaimLeaseCommand) -> LeaseClaimOutcome:
        if not isinstance(command, ClaimLeaseCommand):
            raise TypeError("command must be ClaimLeaseCommand")
        return await self._repository.claim(command)

    async def get_authority(self, proof: LeaseAuthorityProof) -> LeaseAuthority:
        if not isinstance(proof, LeaseAuthorityProof):
            raise TypeError("proof must be LeaseAuthorityProof")
        return await self._repository.get_authority(proof)

    async def renew(self, command: RenewLeaseCommand) -> ConditionalLeaseOutcome:
        if not isinstance(command, RenewLeaseCommand):
            raise TypeError("command must be RenewLeaseCommand")
        return await self._repository.renew(command)

    async def release(self, command: ReleaseLeaseCommand) -> ConditionalLeaseOutcome:
        if not isinstance(command, ReleaseLeaseCommand):
            raise TypeError("command must be ReleaseLeaseCommand")
        return await self._repository.release(command)

    async def get_inactive_running(
        self,
        tenant_id: TenantId,
        run_id: RunId,
    ) -> InactiveRunningLease | None:
        if not isinstance(tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(run_id, RunId):
            raise TypeError("run_id must be RunId")
        return await self._repository.get_inactive_running(tenant_id, run_id)

    async def list_inactive_running(
        self,
        tenant_id: TenantId,
        *,
        cursor: InactiveRunningCursor | None = None,
        limit: int = 100,
    ) -> InactiveRunningPage:
        if not isinstance(tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if cursor is not None:
            if not isinstance(cursor, InactiveRunningCursor):
                raise TypeError("cursor must be InactiveRunningCursor")
            if cursor.tenant_id != tenant_id:
                raise ValueError("cursor tenant must match the requested tenant")
        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("limit must be an integer between 1 and 1000")
        return await self._repository.list_inactive_running(
            tenant_id,
            cursor=cursor,
            limit=limit,
        )
