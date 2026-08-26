"""Deterministic real-transaction failure-window acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_schema import (
    run_command_receipts,
    run_events,
    runs,
)
from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    RunRepositoryError,
    RunRepositoryErrorCode,
)
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
from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
)

pytestmark = pytest.mark.postgresql
NOW = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def _candidate(index: str) -> tuple[RunMutation, CommandReceipt]:
    tenant = TenantId("tenant-fault")
    mutation = Run.create(
        tenant_id=tenant,
        run_id=RunId(f"run-{index}"),
        task_id=TaskId(f"task-{index}"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("agent-fault"),
            version_id=AgentVersionId("version-fault"),
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
        event_id=EventId(f"event-{index}"),
    )
    receipt = CommandReceipt(
        tenant_id=tenant,
        command_id=CommandId(f"command-{index}"),
        run_id=mutation.run.run_id,
        command_type="create_run",
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=(mutation.events[0].event_id,),
        created_at=NOW,
    )
    return mutation, receipt


async def _commit(
    repository: PostgreSQLRunRepository,
    mutation: RunMutation,
    receipt: CommandReceipt,
) -> object:
    return await repository.commit(
        expected_version=0,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=receipt,
    )


class _CheckpointFailureRepository(PostgreSQLRunRepository):
    def __init__(self, engine: AsyncEngine, checkpoint: str) -> None:
        super().__init__(engine)
        self._failing_checkpoint = checkpoint

    async def _transaction_boundary(self, name: str, connection: AsyncConnection) -> None:
        if name == self._failing_checkpoint:
            raise ConnectionError(
                "postgresql://fake-user:fake-password@db SELECT hidden_reason final-answer"
            )


class _TerminateBeforeCommitRepository(PostgreSQLRunRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._fault_engine = engine

    async def _transaction_boundary(self, name: str, connection: AsyncConnection) -> None:
        if name != "before_commit":
            return
        backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        async with self._fault_engine.connect() as killer:
            terminated = await killer.scalar(
                text("SELECT pg_terminate_backend(:backend_pid)"),
                {"backend_pid": backend_pid},
            )
        assert terminated is True


class _LostAcknowledgementRepository(PostgreSQLRunRepository):
    async def _commit_transaction(self, transaction: AsyncTransaction) -> None:
        await transaction.commit()
        raise ConnectionError(
            "postgresql://fake-user:fake-password@db payload final-answer hidden_reason"
        )


async def _counts(engine: AsyncEngine) -> tuple[int, int, int]:
    async with engine.connect() as connection:
        values = []
        for table in (runs, run_events, run_command_receipts):
            values.append(await connection.scalar(select(func.count()).select_from(table)))
    return tuple(values)  # type: ignore[return-value]


@pytest.mark.parametrize("checkpoint", ["after_receipt", "after_run", "after_event"])
async def test_statement_boundary_failures_roll_back_every_fact(
    postgresql_engine: AsyncEngine,
    checkpoint: str,
) -> None:
    mutation, receipt = _candidate(checkpoint)
    repository = _CheckpointFailureRepository(postgresql_engine, checkpoint)
    with pytest.raises(RunRepositoryError) as caught:
        await _commit(repository, mutation, receipt)
    assert caught.value.code is RunRepositoryErrorCode.STORAGE_UNAVAILABLE
    assert await _counts(postgresql_engine) == (0, 0, 0)


async def test_100_precommit_failures_are_known_rollbacks_and_leave_no_partials(
    postgresql_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _CheckpointFailureRepository(postgresql_engine, "before_commit")
    for index in range(100):
        mutation, receipt = _candidate(f"precommit-{index}")
        with pytest.raises(RunRepositoryError) as caught:
            await _commit(repository, mutation, receipt)
        assert caught.value.code is RunRepositoryErrorCode.STORAGE_UNAVAILABLE
        assert "fake-password" not in str(caught.value)
        assert "final-answer" not in repr(caught.value)
    assert await _counts(postgresql_engine) == (0, 0, 0)
    captured = caplog.text
    for marker in (
        "fake-password",
        "SELECT",
        "payload",
        "final-answer",
        "hidden_reason",
    ):
        assert marker not in captured


async def test_100_backend_terminations_before_commit_are_known_noncommits(
    postgresql_engine: AsyncEngine,
) -> None:
    repository = _TerminateBeforeCommitRepository(postgresql_engine)
    for index in range(100):
        mutation, receipt = _candidate(f"terminate-{index}")
        with pytest.raises(RunRepositoryError) as caught:
            await _commit(repository, mutation, receipt)
        assert caught.value.code is RunRepositoryErrorCode.STORAGE_UNAVAILABLE
    assert await _counts(postgresql_engine) == (0, 0, 0)


async def test_100_real_commits_with_lost_ack_converge_by_original_command_replay(
    postgresql_engine: AsyncEngine,
) -> None:
    faulting = _LostAcknowledgementRepository(postgresql_engine)
    normal = PostgreSQLRunRepository(postgresql_engine)
    for index in range(100):
        mutation, receipt = _candidate(f"lost-ack-{index}")
        with pytest.raises(RunRepositoryError) as caught:
            await _commit(faulting, mutation, receipt)
        assert caught.value.code is RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN
        assert "fake-password" not in str(caught.value)
        replay = await normal.commit(
            expected_version=0,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=receipt,
        )
        assert replay.replayed is True
    assert await _counts(postgresql_engine) == (100, 100, 100)


async def test_corruption_and_compatibility_errors_are_distinct_and_redacted(
    postgresql_engine: AsyncEngine,
    migrated_postgresql_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mutation, receipt = _candidate("corruption")
    repository = PostgreSQLRunRepository(postgresql_engine)
    await _commit(repository, mutation, receipt)
    async with postgresql_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE runs SET snapshot = CAST(:snapshot AS json) "
                "WHERE tenant_id = :tenant_id AND run_id = :run_id"
            ),
            {
                "snapshot": '{"final-answer-secret":"hidden-reasoning-secret"}',
                "tenant_id": str(mutation.run.tenant_id),
                "run_id": str(mutation.run.run_id),
            },
        )
    with pytest.raises(RunRepositoryError) as corrupted:
        await repository.load(mutation.run.tenant_id, mutation.run.run_id)
    assert corrupted.value.code is RunRepositoryErrorCode.DATA_CORRUPTION

    async with postgresql_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE zhiyi_schema_compatibility SET contract_version = 2 "
                "WHERE component = 'run_repository'"
            )
        )
    fresh_engine = create_postgresql_engine(migrated_postgresql_url)
    try:
        incompatible = PostgreSQLRunRepository(fresh_engine)
        with pytest.raises(RunRepositoryError) as schema_error:
            await incompatible.load(mutation.run.tenant_id, mutation.run.run_id)
        assert schema_error.value.code is RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE
    finally:
        await dispose_postgresql_engine(fresh_engine)
        async with postgresql_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE zhiyi_schema_compatibility SET contract_version = 1 "
                    "WHERE component = 'run_repository'"
                )
            )

    public_output = " ".join(
        (str(corrupted.value), repr(corrupted.value), str(schema_error.value), caplog.text)
    )
    for marker in (
        "final-answer-secret",
        "hidden-reasoning-secret",
        "zhiyi_test_password",
        "UPDATE runs",
    ):
        assert marker not in public_output


async def test_replay_rejects_semantically_inconsistent_referenced_event(
    postgresql_engine: AsyncEngine,
) -> None:
    created, create_receipt = _candidate("replay-consistency")
    repository = PostgreSQLRunRepository(postgresql_engine)
    await _commit(repository, created, create_receipt)

    started = created.run.start(
        observed_at=NOW,
        event_id=EventId("event-replay-consistency-start"),
    )
    start_receipt = CommandReceipt(
        tenant_id=created.run.tenant_id,
        command_id=CommandId("command-replay-consistency-start"),
        run_id=created.run.run_id,
        command_type="start_run",
        intent_fingerprint="sha256:" + "c" * 64,
        resulting_status=started.run.status,
        resulting_version=started.run.version,
        event_ids=(started.events[0].event_id,),
        created_at=NOW,
    )
    await repository.commit(
        expected_version=1,
        updated_run=started.run,
        new_events=started.events,
        receipt=start_receipt,
    )
    async with postgresql_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE run_command_receipts SET event_id = :event_id "
                "WHERE tenant_id = :tenant_id AND command_id = :command_id"
            ),
            {
                "event_id": str(started.events[0].event_id),
                "tenant_id": str(create_receipt.tenant_id),
                "command_id": str(create_receipt.command_id),
            },
        )

    with pytest.raises(RunRepositoryError) as caught:
        await repository.find_command(
            create_receipt.tenant_id,
            create_receipt.command_id,
            create_receipt.intent_fingerprint,
        )
    assert caught.value.code is RunRepositoryErrorCode.DATA_CORRUPTION


async def test_blocked_read_uses_finite_timeout_and_safe_error(
    postgresql_engine: AsyncEngine,
    migrated_postgresql_url: str,
) -> None:
    reader_engine = create_postgresql_engine(
        migrated_postgresql_url,
        statement_timeout_ms=50,
    )
    repository = PostgreSQLRunRepository(reader_engine)
    tenant_id = TenantId("tenant-blocked-read")
    run_id = RunId("run-blocked-read")
    try:
        # Populate the compatibility cache before taking the product-table lock.
        assert await repository.load(tenant_id, run_id) is None
        async with postgresql_engine.connect() as locker:
            transaction = await locker.begin()
            try:
                await locker.execute(text("LOCK TABLE runs IN ACCESS EXCLUSIVE MODE"))
                with pytest.raises(RunRepositoryError) as caught:
                    await repository.load(tenant_id, run_id)
                assert caught.value.code is RunRepositoryErrorCode.STORAGE_UNAVAILABLE
            finally:
                await transaction.rollback()
    finally:
        await dispose_postgresql_engine(reader_engine)
