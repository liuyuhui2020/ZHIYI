"""Tenant non-disclosure and global-event ownership acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import RunBudget
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
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

pytestmark = pytest.mark.postgresql
NOW = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)


def _candidate(tenant_name: str, event_name: str) -> tuple[RunMutation, CommandReceipt]:
    tenant = TenantId(tenant_name)
    mutation = Run.create(
        tenant_id=tenant,
        run_id=RunId("shared-run"),
        task_id=TaskId("shared-task"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("shared-agent"),
            version_id=AgentVersionId("shared-version"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=10,
            max_model_calls=10,
            max_tool_calls=10,
            max_input_tokens=10,
            max_output_tokens=10,
            max_total_tokens=20,
            max_cost=Decimal("10"),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId(event_name),
    )
    receipt = CommandReceipt(
        tenant_id=tenant,
        command_id=CommandId("shared-command"),
        run_id=mutation.run.run_id,
        command_type="create_run",
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=mutation.run.status,
        resulting_version=1,
        event_ids=(mutation.events[0].event_id,),
        created_at=NOW,
    )
    return mutation, receipt


async def _commit(
    repository: PostgreSQLRunRepository,
    mutation: RunMutation,
    receipt: CommandReceipt,
) -> None:
    await repository.commit(
        expected_version=0,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=receipt,
    )


async def test_same_run_and_command_ids_are_strictly_tenant_scoped(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    first, first_receipt = _candidate("tenant-first", "event-first")
    second, second_receipt = _candidate("tenant-second", "event-second")
    await _commit(repository, first, first_receipt)
    await _commit(repository, second, second_receipt)

    assert await repository.load(first.run.tenant_id, first.run.run_id) == first.run
    assert await repository.load(second.run.tenant_id, second.run.run_id) == second.run
    first_replay = await repository.find_command(
        first.run.tenant_id,
        first_receipt.command_id,
        first_receipt.intent_fingerprint,
    )
    second_replay = await repository.find_command(
        second.run.tenant_id,
        second_receipt.command_id,
        second_receipt.intent_fingerprint,
    )
    assert first_replay is not None and first_replay.events == first.events
    assert second_replay is not None and second_replay.events == second.events


async def test_missing_and_foreign_reads_have_the_same_public_shape(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    owned, receipt = _candidate("tenant-owner", "event-owner")
    await _commit(repository, owned, receipt)
    foreign = TenantId("tenant-foreign")

    assert await repository.load(foreign, owned.run.run_id) is None
    assert await repository.load(foreign, RunId("missing-run")) is None
    assert (
        await repository.find_command(
            foreign,
            receipt.command_id,
            receipt.intent_fingerprint,
        )
        is None
    )
    for run_id in (owned.run.run_id, RunId("missing-run")):
        with pytest.raises(RunLifecycleError) as caught:
            await repository.list_events(foreign, run_id)
        assert caught.value.code is RunErrorCode.NOT_FOUND


async def test_global_event_conflict_never_discloses_the_existing_owner(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    first, first_receipt = _candidate("tenant-secret-owner", "event-global")
    second, second_receipt = _candidate("tenant-caller", "event-global")
    await _commit(repository, first, first_receipt)

    with pytest.raises(RunLifecycleError) as caught:
        await _commit(repository, second, second_receipt)
    assert caught.value.code is RunErrorCode.INVARIANT_VIOLATION
    assert "tenant-secret-owner" not in str(caught.value)
    assert "tenant-secret-owner" not in repr(caught.value)
    assert await repository.load(second.run.tenant_id, second.run.run_id) is None
