from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.adapters.persistence.memory_run_repository import MemoryRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt, CommitOutcome
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


async def test_failed_copy_validate_swap_leaves_no_partial_state() -> None:
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected-before-swap")

    repository = MemoryRunRepository(before_swap=fail_once)
    mutation = new_run()

    with pytest.raises(RuntimeError, match="injected-before-swap"):
        await repository.commit(
            expected_version=0,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=receipt(mutation),
        )

    assert await repository.load(mutation.run.tenant_id, mutation.run.run_id) is None
    assert (
        await repository.find_command(mutation.run.tenant_id, CommandId("command-1"), FINGERPRINT)
        is None
    )


async def test_tenant_keys_are_independent() -> None:
    repository = MemoryRunRepository()
    first = new_run(tenant="tenant-1", run="shared-run", event="event-1")
    second = new_run(tenant="tenant-2", run="shared-run", event="event-2")

    await repository.commit(
        expected_version=0,
        updated_run=first.run,
        new_events=first.events,
        receipt=receipt(first, command="shared-command"),
    )
    await repository.commit(
        expected_version=0,
        updated_run=second.run,
        new_events=second.events,
        receipt=receipt(second, command="shared-command"),
    )

    assert await repository.load(TenantId("tenant-1"), first.run.run_id) == first.run
    assert await repository.load(TenantId("tenant-2"), second.run.run_id) == second.run


async def test_concurrent_same_command_has_one_write_and_stable_replays() -> None:
    repository = MemoryRunRepository()
    mutation = new_run()
    command_receipt = receipt(mutation)

    outcomes = await asyncio.gather(
        *(
            repository.commit(
                expected_version=0,
                updated_run=mutation.run,
                new_events=mutation.events,
                receipt=command_receipt,
            )
            for _ in range(1000)
        )
    )

    assert sum(not outcome.replayed for outcome in outcomes) == 1
    assert all(outcome.receipt is command_receipt for outcome in outcomes)
    assert await repository.list_events(mutation.run.tenant_id, mutation.run.run_id) == (
        mutation.events
    )


async def test_concurrent_different_commands_have_one_version_winner() -> None:
    repository = MemoryRunRepository()
    created = new_run()
    await repository.commit(
        expected_version=0,
        updated_run=created.run,
        new_events=created.events,
        receipt=receipt(created),
    )
    started = created.run.start(
        observed_at=created.run.created_at,
        event_id=EventId("event-2"),
    )

    async def compete(index: int) -> bool:
        try:
            await repository.commit(
                expected_version=1,
                updated_run=started.run,
                new_events=started.events,
                receipt=receipt(
                    started,
                    command=f"command-{index + 2}",
                    fingerprint="sha256:" + f"{index + 1:064x}",
                    command_type="start_run",
                ),
            )
        except RunLifecycleError as error:
            assert error.code is RunErrorCode.VERSION_CONFLICT
            return False
        return True

    results = await asyncio.gather(*(compete(index) for index in range(1000)))

    assert sum(results) == 1


async def test_one_thousand_independent_two_command_races_have_one_winner_each() -> None:
    for index in range(1000):
        repository = MemoryRunRepository()
        created = new_run(
            run=f"race-run-{index}",
            event=f"race-created-{index}",
        )
        await repository.commit(
            expected_version=0,
            updated_run=created.run,
            new_events=created.events,
            receipt=receipt(created),
        )
        first = created.run.start(
            observed_at=NOW,
            event_id=EventId(f"race-first-{index}"),
        )
        second = created.run.start(
            observed_at=NOW,
            event_id=EventId(f"race-second-{index}"),
        )

        def race_receipt(
            mutation: RunMutation,
            contender: str,
            race_index: int,
        ) -> CommandReceipt:
            fingerprint = (
                "sha256:" + hashlib.sha256(f"{race_index}:{contender}".encode()).hexdigest()
            )
            return receipt(
                mutation,
                command=f"race-{contender}-{race_index}",
                fingerprint=fingerprint,
                command_type="start_run",
            )

        outcomes = await asyncio.gather(
            repository.commit(
                expected_version=1,
                updated_run=first.run,
                new_events=first.events,
                receipt=race_receipt(first, "first", index),
            ),
            repository.commit(
                expected_version=1,
                updated_run=second.run,
                new_events=second.events,
                receipt=race_receipt(second, "second", index),
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(outcome, CommitOutcome) for outcome in outcomes) == 1
        failures = [outcome for outcome in outcomes if isinstance(outcome, RunLifecycleError)]
        assert len(failures) == 1
        assert failures[0].code is RunErrorCode.VERSION_CONFLICT
        stored = await repository.load(created.run.tenant_id, created.run.run_id)
        assert stored is not None and stored.version == 2
        events = await repository.list_events(created.run.tenant_id, created.run.run_id)
        assert [event.sequence for event in events] == [1, 2]
