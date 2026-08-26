"""Persistence round trips across engine disposal and recreation."""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import BudgetCharge, RunBudget
from zhiyi.domain.runs.errors import RunErrorCode
from zhiyi.domain.runs.events import RunEventType, RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    ChargeId,
    CommandId,
    CorrelationId,
    EventId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.runs.results import RunResultDraft, SafeRunError
from zhiyi.infrastructure.database.engine import create_postgresql_engine, dispose_postgresql_engine

pytestmark = pytest.mark.postgresql
NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _counter() -> int:
    value = 0
    for _ in range(5_000):
        value = value * 10 + 7
    return value


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _new_run(name: str, *, max_steps: int = 10) -> RunMutation:
    tenant = TenantId("tenant-restart-matrix")
    return Run.create(
        tenant_id=tenant,
        run_id=RunId(f"run-{name}"),
        task_id=TaskId(f"task-{name}"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("agent-restart-matrix"),
            version_id=AgentVersionId("version-restart-matrix"),
            build_digest="sha256:" + "c" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=max_steps,
            max_model_calls=10,
            max_tool_calls=10,
            max_input_tokens=10,
            max_output_tokens=10,
            max_total_tokens=20,
            max_cost=Decimal("10"),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId(f"event-create-{name}"),
    )


async def _commit(
    repository: PostgreSQLRunRepository,
    mutation: RunMutation,
    *,
    command: str,
    command_type: str,
) -> None:
    receipt = CommandReceipt(
        tenant_id=mutation.run.tenant_id,
        command_id=CommandId(command),
        run_id=mutation.run.run_id,
        command_type=command_type,
        intent_fingerprint=_fingerprint(command),
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=tuple(event.event_id for event in mutation.events),
        created_at=NOW,
    )
    await repository.commit(
        expected_version=mutation.run.version - (1 if mutation.events else 0),
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=receipt,
    )


async def test_run_event_and_receipt_survive_engine_recreation(
    migrated_postgresql_url: str,
    postgresql_engine: AsyncEngine,
) -> None:
    original_limit = sys.get_int_max_str_digits()
    tenant = TenantId("tenant-restart")
    mutation = Run.create(
        tenant_id=tenant,
        run_id=RunId("run-restart"),
        task_id=TaskId("task-restart"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("agent-restart"),
            version_id=AgentVersionId("version-restart"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=_counter(),
            max_model_calls=10,
            max_tool_calls=10,
            max_input_tokens=10,
            max_output_tokens=10,
            max_total_tokens=20,
            max_cost=Decimal("9." + "9" * 20_000),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId("event-restart"),
    )
    receipt = CommandReceipt(
        tenant_id=tenant,
        command_id=CommandId("command-restart"),
        run_id=mutation.run.run_id,
        command_type="create_run",
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=RunStatus.QUEUED,
        resulting_version=1,
        event_ids=(mutation.events[0].event_id,),
        created_at=NOW,
    )
    repository = PostgreSQLRunRepository(postgresql_engine)
    await repository.commit(
        expected_version=0,
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=receipt,
    )
    await dispose_postgresql_engine(postgresql_engine)

    reopened = create_postgresql_engine(migrated_postgresql_url)
    try:
        repository = PostgreSQLRunRepository(reopened)
        assert await repository.load(tenant, mutation.run.run_id) == mutation.run
        assert await repository.list_events(tenant, mutation.run.run_id) == mutation.events
        replay = await repository.find_command(
            tenant, receipt.command_id, receipt.intent_fingerprint
        )
        assert replay is not None and replay.replayed
        assert sys.get_int_max_str_digits() == original_limit
    finally:
        await dispose_postgresql_engine(reopened)


async def test_all_statuses_terminal_results_event_types_and_references_survive_restart(
    migrated_postgresql_url: str,
    postgresql_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(postgresql_engine)
    final_runs: dict[RunStatus, Run] = {}

    queued = _new_run("queued")
    await _commit(repository, queued, command="command-queued", command_type="create_run")
    final_runs[RunStatus.QUEUED] = queued.run

    running = _new_run("running")
    await _commit(repository, running, command="command-running-create", command_type="create_run")
    running_started = running.run.start(observed_at=NOW, event_id=EventId("event-running"))
    await _commit(repository, running_started, command="command-running", command_type="start_run")
    final_runs[RunStatus.RUNNING] = running_started.run

    approval = _new_run("approval")
    await _commit(
        repository, approval, command="command-approval-create", command_type="create_run"
    )
    approval_started = approval.run.start(observed_at=NOW, event_id=EventId("event-approval-start"))
    await _commit(
        repository, approval_started, command="command-approval-start", command_type="start_run"
    )
    waiting_approval = approval_started.run.wait_for_approval(
        reference_id=ReferenceId("approval-reference"),
        observed_at=NOW,
        event_id=EventId("event-approval"),
    )
    await _commit(
        repository,
        waiting_approval,
        command="command-approval",
        command_type="wait_for_approval",
    )
    final_runs[RunStatus.WAITING_APPROVAL] = waiting_approval.run

    resolution = _new_run("resolution")
    await _commit(
        repository, resolution, command="command-resolution-create", command_type="create_run"
    )
    resolution_started = resolution.run.start(
        observed_at=NOW,
        event_id=EventId("event-resolution-start"),
    )
    await _commit(
        repository,
        resolution_started,
        command="command-resolution-start",
        command_type="start_run",
    )
    waiting_resolution = resolution_started.run.wait_for_resolution(
        reference_id=ReferenceId("resolution-reference"),
        observed_at=NOW,
        event_id=EventId("event-resolution"),
    )
    await _commit(
        repository,
        waiting_resolution,
        command="command-resolution",
        command_type="wait_for_resolution",
    )
    final_runs[RunStatus.WAITING_RESOLUTION] = waiting_resolution.run

    success = _new_run("success")
    await _commit(repository, success, command="command-success-create", command_type="create_run")
    success_started = success.run.start(observed_at=NOW, event_id=EventId("event-success-start"))
    await _commit(
        repository, success_started, command="command-success-start", command_type="start_run"
    )
    success_waiting = success_started.run.wait_for_approval(
        reference_id=ReferenceId("approval-success"),
        observed_at=NOW,
        event_id=EventId("event-success-wait"),
    )
    await _commit(
        repository,
        success_waiting,
        command="command-success-wait",
        command_type="wait_for_approval",
    )
    success_resumed = success_waiting.run.resume(
        observed_at=NOW,
        event_id=EventId("event-success-resume"),
    )
    await _commit(
        repository, success_resumed, command="command-success-resume", command_type="resume_run"
    )
    success_charged = success_resumed.run.consume_budget(
        charge=BudgetCharge(charge_id=ChargeId("charge-success"), steps=1),
        observed_at=NOW,
        event_id=EventId("event-success-charge"),
    )
    await _commit(
        repository,
        success_charged,
        command="command-success-charge",
        command_type="consume_budget",
    )
    succeeded = success_charged.run.succeed(
        draft=RunResultDraft(
            answer="approved answer",
            warning_codes=("warning.safe",),
            citation_ids=(ReferenceId("citation-success"),),
            artifact_ids=(ReferenceId("artifact-success"),),
            approval_ids=(ReferenceId("approval-success"),),
            correlation_id=CorrelationId("correlation-success"),
        ),
        observed_at=NOW,
        event_id=EventId("event-success"),
    )
    await _commit(repository, succeeded, command="command-success", command_type="succeed_run")
    final_runs[RunStatus.SUCCEEDED] = succeeded.run

    failed_base = _new_run("failed")
    await _commit(
        repository, failed_base, command="command-failed-create", command_type="create_run"
    )
    failed_started = failed_base.run.start(observed_at=NOW, event_id=EventId("event-failed-start"))
    await _commit(
        repository, failed_started, command="command-failed-start", command_type="start_run"
    )
    failed = failed_started.run.fail(
        draft=RunResultDraft(correlation_id=CorrelationId("correlation-failed")),
        error=SafeRunError(RunErrorCode.FAILED, CorrelationId("correlation-failed")),
        observed_at=NOW,
        event_id=EventId("event-failed"),
    )
    await _commit(repository, failed, command="command-failed", command_type="fail_run")
    final_runs[RunStatus.FAILED] = failed.run

    cancelled_base = _new_run("cancelled")
    await _commit(
        repository, cancelled_base, command="command-cancel-create", command_type="create_run"
    )
    cancelled = cancelled_base.run.cancel(
        observed_at=NOW,
        event_id=EventId("event-cancelled"),
        correlation_id=CorrelationId("correlation-cancelled"),
    )
    await _commit(repository, cancelled, command="command-cancelled", command_type="cancel_run")
    final_runs[RunStatus.CANCELLED] = cancelled.run

    limited_base = _new_run("limited", max_steps=0)
    await _commit(
        repository, limited_base, command="command-limit-create", command_type="create_run"
    )
    limited_started = limited_base.run.start(observed_at=NOW, event_id=EventId("event-limit-start"))
    await _commit(
        repository, limited_started, command="command-limit-start", command_type="start_run"
    )
    limited = limited_started.run.consume_budget(
        charge=BudgetCharge(charge_id=ChargeId("charge-limit"), steps=1),
        observed_at=NOW,
        event_id=EventId("event-limited"),
    )
    await _commit(repository, limited, command="command-limited", command_type="consume_budget")
    final_runs[RunStatus.LIMIT_EXCEEDED] = limited.run

    await dispose_postgresql_engine(postgresql_engine)
    reopened = create_postgresql_engine(migrated_postgresql_url)
    try:
        restored = PostgreSQLRunRepository(reopened)
        observed_types: set[RunEventType] = set()
        for status, expected in final_runs.items():
            loaded = await restored.load(expected.tenant_id, expected.run_id)
            assert loaded == expected
            assert loaded is not None and loaded.status is status
            observed_types.update(
                event.type
                for event in await restored.list_events(expected.tenant_id, expected.run_id)
            )
        assert observed_types == set(RunEventType)
        assert {status for status in final_runs if status.is_terminal} == {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.LIMIT_EXCEEDED,
        }
    finally:
        await dispose_postgresql_engine(reopened)
