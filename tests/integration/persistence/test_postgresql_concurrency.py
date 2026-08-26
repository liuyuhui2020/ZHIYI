"""Real PostgreSQL command and version linearization matrices."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_schema import (
    run_command_receipts,
    run_events,
    runs,
)
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

pytestmark = pytest.mark.postgresql
NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


async def test_application_engine_transactions_are_read_committed(
    postgresql_engine: AsyncEngine,
) -> None:
    async with postgresql_engine.connect() as connection:
        isolation = await connection.scalar(text("SHOW transaction_isolation"))
    assert isolation == "read committed"


def _create(index: str) -> RunMutation:
    tenant = TenantId("tenant-concurrency")
    return Run.create(
        tenant_id=tenant,
        run_id=RunId(f"run-{index}"),
        task_id=TaskId(f"task-{index}"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("agent-concurrency"),
            version_id=AgentVersionId("version-concurrency"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=100,
            max_model_calls=100,
            max_tool_calls=100,
            max_input_tokens=100,
            max_output_tokens=100,
            max_total_tokens=200,
            max_cost=Decimal("100"),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId(f"event-create-{index}"),
    )


def _receipt(
    mutation: RunMutation,
    *,
    command: str,
    fingerprint_digit: str,
    command_type: str,
) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=mutation.run.tenant_id,
        command_id=CommandId(command),
        run_id=mutation.run.run_id,
        command_type=command_type,
        intent_fingerprint="sha256:" + fingerprint_digit * 64,
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=tuple(event.event_id for event in mutation.events),
        created_at=NOW,
    )


async def _commit_create(
    repository: PostgreSQLRunRepository,
    index: str,
) -> RunMutation:
    mutation = _create(index)
    await repository.commit(
        expected_version=0,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=_receipt(
            mutation,
            command=f"command-create-{index}",
            fingerprint_digit="a",
            command_type="create_run",
        ),
    )
    return mutation


async def test_100_same_command_requests_replay_through_pool_of_20(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    mutation = _create("same-command")
    command_receipt = _receipt(
        mutation,
        command="command-same",
        fingerprint_digit="b",
        command_type="create_run",
    )

    results = await asyncio.gather(
        *(
            repository.commit(
                expected_version=0,
                updated_run=mutation.run,
                new_events=mutation.events,
                receipt=command_receipt,
            )
            for _ in range(100)
        )
    )

    assert sum(not result.replayed for result in results) == 1
    assert sum(result.replayed for result in results) == 99
    async with postgresql_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(runs)) == 1
        assert await connection.scalar(select(func.count()).select_from(run_events)) == 1
        assert await connection.scalar(select(func.count()).select_from(run_command_receipts)) == 1


async def test_100_groups_1000_state_change_attempts_have_one_winner_each(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    successes = conflicts = 0
    for group in range(100):
        created = await _commit_create(repository, f"race-{group}")
        attempts = []
        for attempt in range(10):
            started = created.run.start(
                observed_at=NOW,
                event_id=EventId(f"event-start-{group}-{attempt}"),
            )
            attempts.append(
                repository.commit(
                    expected_version=1,
                    updated_run=started.run,
                    new_events=started.events,
                    receipt=_receipt(
                        started,
                        command=f"command-start-{group}-{attempt}",
                        fingerprint_digit=str(attempt),
                        command_type="start_run",
                    ),
                )
            )
        results = await asyncio.gather(*attempts, return_exceptions=True)
        group_successes = sum(isinstance(result, CommitOutcome) for result in results)
        group_conflicts = sum(
            isinstance(result, RunLifecycleError) and result.code is RunErrorCode.VERSION_CONFLICT
            for result in results
        )
        assert group_successes == 1
        assert group_conflicts == 9
        successes += group_successes
        conflicts += group_conflicts

    assert successes == 100
    assert conflicts == 900
    async with postgresql_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(runs)) == 100
        assert await connection.scalar(select(func.count()).select_from(run_events)) == 200
        assert (
            await connection.scalar(select(func.count()).select_from(run_command_receipts)) == 200
        )


async def test_concurrent_create_different_intent_and_zero_event_linearization(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    candidate = _create("create-race")
    create_results = await asyncio.gather(
        *(
            repository.commit(
                expected_version=0,
                updated_run=candidate.run,
                new_events=candidate.events,
                receipt=_receipt(
                    candidate,
                    command=f"command-create-race-{index}",
                    fingerprint_digit=str(index),
                    command_type="create_run",
                ),
            )
            for index in range(10)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, CommitOutcome) for result in create_results) == 1

    winning = next(result for result in create_results if isinstance(result, CommitOutcome))
    with pytest.raises(RunLifecycleError) as reused:
        await repository.find_command(
            winning.receipt.tenant_id,
            winning.receipt.command_id,
            "sha256:" + "f" * 64,
        )
    assert reused.value.code is RunErrorCode.IDEMPOTENCY_CONFLICT

    created = await _commit_create(repository, "zero-state")
    started = created.run.start(observed_at=NOW, event_id=EventId("event-zero-state"))
    no_change = RunMutation(run=created.run, events=())
    results = await asyncio.gather(
        repository.commit(
            expected_version=1,
            updated_run=created.run,
            new_events=(),
            receipt=_receipt(
                no_change,
                command="command-zero",
                fingerprint_digit="e",
                command_type="consume_budget",
            ),
        ),
        repository.commit(
            expected_version=1,
            updated_run=started.run,
            new_events=started.events,
            receipt=_receipt(
                started,
                command="command-state",
                fingerprint_digit="d",
                command_type="start_run",
            ),
        ),
        return_exceptions=True,
    )
    assert any(isinstance(result, CommitOutcome) and result.events for result in results)
    loaded = await repository.load(created.run.tenant_id, created.run.run_id)
    assert loaded is not None and loaded.version == 2
