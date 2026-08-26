"""Commit preflight ordering for the PostgreSQL adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.adapters.persistence import postgresql_run_repository as repository_module
from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
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
