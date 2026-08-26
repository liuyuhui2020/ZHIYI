from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.adapters.persistence.memory_run_repository import MemoryRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import RunBudget
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    CommandId,
    EventId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
FINGERPRINT = "sha256:" + "b" * 64


def new_run(*, tenant: str = "tenant-1", run: str = "run-1", event: str = "event-1") -> RunMutation:
    tenant_id = TenantId(tenant)
    return Run.create(
        tenant_id=tenant_id,
        run_id=RunId(run),
        task_id=TaskId("task-1"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-1"),
            version_id=AgentVersionId("version-1"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(hours=1),
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
        event_id=EventId(event),
    )


def receipt(
    mutation: RunMutation,
    *,
    command: str = "command-1",
    fingerprint: str = FINGERPRINT,
    command_type: str = "create_run",
) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=mutation.run.tenant_id,
        command_id=CommandId(command),
        run_id=mutation.run.run_id,
        command_type=command_type,
        intent_fingerprint=fingerprint,
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=tuple(event.event_id for event in mutation.events),
        created_at=NOW,
    )


@pytest.fixture
def repository() -> MemoryRunRepository:
    return MemoryRunRepository()


async def commit_create(repository: MemoryRunRepository) -> tuple[Run, CommandReceipt]:
    mutation = new_run()
    command_receipt = receipt(mutation)
    outcome = await repository.commit(
        expected_version=0,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=command_receipt,
    )
    assert outcome.replayed is False
    return mutation.run, command_receipt


async def test_create_load_and_ordered_event_read_are_atomic(
    repository: MemoryRunRepository,
) -> None:
    created, command_receipt = await commit_create(repository)

    assert await repository.load(created.tenant_id, created.run_id) == created
    assert await repository.list_events(created.tenant_id, created.run_id) == (new_run().events[0],)
    found = await repository.find_command(
        created.tenant_id,
        command_receipt.command_id,
        command_receipt.intent_fingerprint,
    )
    assert found is not None
    assert found.replayed is True
    assert found.receipt is command_receipt
    assert found.events == new_run().events


async def test_receipt_replay_precedes_stale_version_and_changed_intent_conflicts(
    repository: MemoryRunRepository,
) -> None:
    created, original_receipt = await commit_create(repository)

    replay = await repository.commit(
        expected_version=999,
        updated_run=created,
        new_events=(),
        receipt=original_receipt,
    )
    assert replay.replayed is True

    with pytest.raises(RunLifecycleError) as changed:
        await repository.find_command(
            created.tenant_id,
            original_receipt.command_id,
            "sha256:" + "c" * 64,
        )
    assert changed.value.code is RunErrorCode.IDEMPOTENCY_CONFLICT


async def test_new_command_requires_current_version_and_zero_event_commit_is_allowed(
    repository: MemoryRunRepository,
) -> None:
    created, _ = await commit_create(repository)
    no_change = receipt(
        RunMutation(run=created, events=()),
        command="command-2",
        fingerprint="sha256:" + "d" * 64,
        command_type="consume_budget",
    )

    with pytest.raises(RunLifecycleError) as stale:
        await repository.commit(
            expected_version=0,
            updated_run=created,
            new_events=(),
            receipt=no_change,
        )
    assert stale.value.code is RunErrorCode.VERSION_CONFLICT

    outcome = await repository.commit(
        expected_version=1,
        updated_run=created,
        new_events=(),
        receipt=no_change,
    )
    assert outcome.receipt.resulting_version == 1
    assert await repository.list_events(created.tenant_id, created.run_id) == new_run().events


async def test_update_rejects_stale_version_and_inconsistent_receipt(
    repository: MemoryRunRepository,
) -> None:
    created, _ = await commit_create(repository)
    started = created.start(observed_at=NOW, event_id=EventId("event-2"))
    start_receipt = receipt(
        started,
        command="command-2",
        fingerprint="sha256:" + "e" * 64,
        command_type="start_run",
    )

    with pytest.raises(RunLifecycleError) as stale:
        await repository.commit(
            expected_version=0,
            updated_run=started.run,
            new_events=started.events,
            receipt=start_receipt,
        )
    assert stale.value.code is RunErrorCode.VERSION_CONFLICT

    inconsistent = CommandReceipt(
        tenant_id=start_receipt.tenant_id,
        command_id=CommandId("command-3"),
        run_id=start_receipt.run_id,
        command_type=start_receipt.command_type,
        intent_fingerprint="sha256:" + "f" * 64,
        resulting_status=RunStatus.QUEUED,
        resulting_version=start_receipt.resulting_version,
        event_ids=start_receipt.event_ids,
        created_at=NOW,
    )
    with pytest.raises(RunLifecycleError) as invalid:
        await repository.commit(
            expected_version=1,
            updated_run=started.run,
            new_events=started.events,
            receipt=inconsistent,
        )
    assert invalid.value.code is RunErrorCode.INVARIANT_VIOLATION
    assert await repository.load(created.tenant_id, created.run_id) == created


async def test_duplicate_event_id_is_rejected_without_partial_update(
    repository: MemoryRunRepository,
) -> None:
    created, _ = await commit_create(repository)
    duplicate = created.start(observed_at=NOW, event_id=EventId("event-1"))

    with pytest.raises(RunLifecycleError) as invalid:
        await repository.commit(
            expected_version=1,
            updated_run=duplicate.run,
            new_events=duplicate.events,
            receipt=receipt(
                duplicate,
                command="command-duplicate-event",
                fingerprint="sha256:" + "9" * 64,
                command_type="start_run",
            ),
        )
    assert invalid.value.code is RunErrorCode.INVARIANT_VIOLATION
    assert await repository.load(created.tenant_id, created.run_id) == created


async def test_single_command_cannot_commit_multiple_events(
    repository: MemoryRunRepository,
) -> None:
    created, _ = await commit_create(repository)
    started = created.start(observed_at=NOW, event_id=EventId("event-2"))
    waiting = started.run.wait_for_approval(
        reference_id=ReferenceId("approval-multi-event"),
        observed_at=NOW,
        event_id=EventId("event-3"),
    )
    combined = RunMutation(run=waiting.run, events=started.events + waiting.events)

    with pytest.raises(RunLifecycleError) as invalid:
        await repository.commit(
            expected_version=1,
            updated_run=combined.run,
            new_events=combined.events,
            receipt=receipt(
                combined,
                command="command-multi-event",
                fingerprint="sha256:" + "8" * 64,
                command_type="start_run",
            ),
        )
    assert invalid.value.code is RunErrorCode.INVARIANT_VIOLATION
    assert await repository.load(created.tenant_id, created.run_id) == created


async def test_event_pagination_bounds_and_tenant_non_disclosure(
    repository: MemoryRunRepository,
) -> None:
    created, _ = await commit_create(repository)
    started = created.start(observed_at=NOW, event_id=EventId("event-2"))
    await repository.commit(
        expected_version=1,
        updated_run=started.run,
        new_events=started.events,
        receipt=receipt(
            started,
            command="command-2",
            fingerprint="sha256:" + "e" * 64,
            command_type="start_run",
        ),
    )

    assert (
        await repository.list_events(created.tenant_id, created.run_id, after_sequence=1, limit=1)
        == started.events
    )
    for limit in (0, 1001):
        with pytest.raises(ValueError):
            await repository.list_events(created.tenant_id, created.run_id, limit=limit)
    with pytest.raises(ValueError):
        await repository.list_events(created.tenant_id, created.run_id, after_sequence=-1)

    foreign_tenant = TenantId("tenant-2")
    assert await repository.load(foreign_tenant, created.run_id) is None
    with pytest.raises(RunLifecycleError) as hidden:
        await repository.list_events(foreign_tenant, created.run_id)
    assert hidden.value.code is RunErrorCode.NOT_FOUND
