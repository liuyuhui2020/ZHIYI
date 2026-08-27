"""Commit preflight ordering for the PostgreSQL adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.adapters.persistence import postgresql_run_repository as repository_module
from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_transaction_support import (
    GUARDED_RUN_LOCK_ORDER,
    LEASE_MUTATION_LOCK_ORDER,
    PostgreSQLTransactionSettings,
    apply_transaction_settings,
    execute_once,
    receipt_first,
)
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run
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


async def test_complete_candidate_is_encoded_before_any_schema_or_storage_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
    tenant_id = TenantId("tenant-preflight")
    mutation = Run.create(
        tenant_id=tenant_id,
        run_id=RunId("run-preflight"),
        task_id=TaskId("task-preflight"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-preflight"),
            version_id=AgentVersionId("version-preflight"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=now + timedelta(days=1),
            max_steps=10,
            max_model_calls=10,
            max_tool_calls=10,
            max_input_tokens=10,
            max_output_tokens=10,
            max_total_tokens=20,
            max_cost=Decimal("10"),
            currency="USD",
        ),
        observed_at=now,
        event_id=EventId("event-preflight"),
    )
    receipt = CommandReceipt(
        tenant_id=tenant_id,
        command_id=CommandId("command-preflight"),
        run_id=mutation.run.run_id,
        command_type="create_run",
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=(mutation.events[0].event_id,),
        created_at=now,
    )
    calls: list[str] = []

    for name in ("encode_run", "encode_receipt", "encode_event"):
        original = getattr(repository_module, name)

        def record(value: object, *, _name: str = name, _original: object = original) -> object:
            calls.append(_name)
            return _original(value)  # type: ignore[operator]

        monkeypatch.setattr(repository_module, name, record)

    async def stop_before_storage(engine: object) -> None:
        calls.append("schema")
        raise RuntimeError("stop after preflight")

    monkeypatch.setattr(repository_module, "ensure_schema_compatible", stop_before_storage)
    repository = PostgreSQLRunRepository(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="stop after preflight"):
        await repository.commit(
            expected_version=0,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=receipt,
        )

    assert calls == ["encode_run", "encode_receipt", "encode_event", "schema"]


@pytest.mark.parametrize("value", [True, 0, -1, 5_001, 1.5, "5000"])
def test_worker_lock_timeout_is_finite_and_bounded(value: object) -> None:
    with pytest.raises(ValueError, match="lock_timeout_ms"):
        PostgreSQLTransactionSettings(lock_timeout_ms=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, 0, -1, 10_001, 1.5, "5000"])
def test_worker_statement_timeout_is_finite_and_bounded(value: object) -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        PostgreSQLTransactionSettings(statement_timeout_ms=value)  # type: ignore[arg-type]


def test_statement_timeout_cannot_be_less_than_lock_timeout() -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        PostgreSQLTransactionSettings(lock_timeout_ms=2_000, statement_timeout_ms=1_999)


def test_default_settings_are_read_committed_synchronous_and_five_seconds() -> None:
    settings = PostgreSQLTransactionSettings()

    assert settings.isolation_level == "READ COMMITTED"
    assert settings.synchronous_commit is True
    assert settings.lock_timeout_ms == 5_000
    assert settings.statement_timeout_ms == 5_000


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement: object, parameters: object = None) -> None:
        self.calls.append((str(statement), parameters))


async def test_transaction_settings_are_applied_before_business_statements() -> None:
    connection = _RecordingConnection()

    await apply_transaction_settings(
        connection,  # type: ignore[arg-type]
        PostgreSQLTransactionSettings(lock_timeout_ms=123, statement_timeout_ms=456),
    )

    assert len(connection.calls) == 2
    assert connection.calls[0][0] == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
    assert "synchronous_commit" in connection.calls[1][0]
    assert connection.calls[1][1] == {
        "lock_timeout": "123ms",
        "statement_timeout": "456ms",
    }


def test_receipt_run_lease_event_lock_order_is_a_constant_contract() -> None:
    assert GUARDED_RUN_LOCK_ORDER == ("receipt", "run", "lease", "event")
    assert LEASE_MUTATION_LOCK_ORDER == ("run", "lease")


async def test_receipt_replay_precedes_new_run_and_lease_work() -> None:
    calls: list[str] = []
    replay = object()

    async def find_receipt() -> object:
        calls.append("receipt")
        return replay

    async def write_new() -> object:
        calls.append("run")
        calls.append("lease")
        return object()

    assert await receipt_first(find_receipt, write_new) is replay
    assert calls == ["receipt"]


async def test_transaction_operation_is_never_automatically_retried() -> None:
    calls = 0

    async def fail_once() -> object:
        nonlocal calls
        calls += 1
        raise ConnectionError("lost")

    with pytest.raises(ConnectionError, match="lost"):
        await execute_once(fail_once)

    assert calls == 1
