from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.adapters.persistence.memory_run_repository import MemoryRunRepository
from zhiyi.application.commands.run_lifecycle import (
    CancelRunCommand,
    ConsumeBudgetCommand,
    CreateRunCommand,
    EnforceDeadlineCommand,
    FailRunCommand,
    ResumeRunCommand,
    StartRunCommand,
    SucceedRunCommand,
    WaitForApprovalCommand,
    WaitForResolutionCommand,
)
from zhiyi.application.services.run_lifecycle import DeadlineOutcome, RunLifecycleService
from zhiyi.domain.runs.budget import BudgetCharge, BudgetDimension, RunBudget
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEventType, RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    ChargeId,
    CommandId,
    CorrelationId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.runs.results import RunResultDraft, SafeRunError

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


@dataclass
class ControlledClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


class SequentialIds:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def new_id(self, namespace: str) -> str:
        self.calls.append(namespace)
        return f"{namespace}-{len(self.calls)}"


def make_budget(**overrides: object) -> RunBudget:
    values: dict[str, object] = {
        "deadline_at": NOW + timedelta(minutes=30),
        "max_steps": 5,
        "max_model_calls": 4,
        "max_tool_calls": 3,
        "max_input_tokens": 100,
        "max_output_tokens": 50,
        "max_total_tokens": 120,
        "max_cost": Decimal("1.25"),
        "currency": "USD",
    }
    values.update(overrides)
    return RunBudget(**values)  # type: ignore[arg-type]


def create_command(
    *, command_id: str = "command-create", budget: RunBudget | None = None
) -> CreateRunCommand:
    tenant_id = TenantId("tenant-1")
    return CreateRunCommand(
        tenant_id=tenant_id,
        command_id=CommandId(command_id),
        task_id=TaskId("task-1"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-1"),
            version_id=AgentVersionId("version-1"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=budget or make_budget(),
    )


def service_fixture() -> tuple[RunLifecycleService, ControlledClock, SequentialIds]:
    clock = ControlledClock()
    identifiers = SequentialIds()
    return (
        RunLifecycleService(
            repository=MemoryRunRepository(),
            clock=clock,
            identifier_generator=identifiers,
        ),
        clock,
        identifiers,
    )


async def create_started(
    service: RunLifecycleService,
) -> tuple[TenantId, RunId, int]:
    created = await service.create_run(create_command())
    await service.start_run(
        StartRunCommand(
            tenant_id=created.receipt.tenant_id,
            command_id=CommandId("command-start"),
            run_id=created.receipt.run_id,
            expected_version=1,
        )
    )
    return created.receipt.tenant_id, created.receipt.run_id, 2


async def test_create_replay_returns_stable_receipt_and_does_not_generate_ids() -> None:
    service, _, identifiers = service_fixture()
    command = create_command()

    created = await service.create_run(command)
    calls_after_create = tuple(identifiers.calls)
    replayed = await service.create_run(command)

    assert created.replayed is False
    assert replayed.replayed is True
    assert replayed.receipt is created.receipt
    assert replayed.events == created.events
    assert tuple(identifiers.calls) == calls_after_create


async def test_one_hundred_command_and_charge_replays_are_stable_noops() -> None:
    service, _, identifiers = service_fixture()
    create = create_command()
    created = await service.create_run(create)
    generated_after_create = tuple(identifiers.calls)

    command_replays = [await service.create_run(create) for _ in range(100)]
    assert all(outcome.replayed for outcome in command_replays)
    assert all(outcome.receipt is created.receipt for outcome in command_replays)
    assert all(outcome.events == created.events for outcome in command_replays)
    assert tuple(identifiers.calls) == generated_after_create

    started = await service.start_run(
        StartRunCommand(
            tenant_id=created.receipt.tenant_id,
            command_id=CommandId("command-start-replays"),
            run_id=created.receipt.run_id,
            expected_version=1,
        )
    )
    charge = BudgetCharge(charge_id=ChargeId("charge-repeated"), steps=1)
    consumed = await service.consume_budget(
        ConsumeBudgetCommand(
            tenant_id=created.receipt.tenant_id,
            command_id=CommandId("charge-initial"),
            run_id=created.receipt.run_id,
            expected_version=started.receipt.resulting_version,
            charge=charge,
        )
    )
    for index in range(100):
        replay = await service.consume_budget(
            ConsumeBudgetCommand(
                tenant_id=created.receipt.tenant_id,
                command_id=CommandId(f"charge-replay-{index}"),
                run_id=created.receipt.run_id,
                expected_version=consumed.receipt.resulting_version,
                charge=charge,
            )
        )
        assert replay.events == ()
        assert replay.receipt.resulting_version == consumed.receipt.resulting_version

    run = await service.get_run(created.receipt.tenant_id, created.receipt.run_id)
    events = await service.list_events(created.receipt.tenant_id, created.receipt.run_id)
    assert run.version == 3
    assert run.usage.steps == 1
    assert [event.sequence for event in events] == [1, 2, 3]


async def test_complete_progress_lifecycle_has_continuous_ordered_events() -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)
    waiting = await service.wait_for_approval(
        WaitForApprovalCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-wait"),
            run_id=run_id,
            expected_version=version,
            reference_id=ReferenceId("approval-1"),
        )
    )
    resumed = await service.resume_run(
        ResumeRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-resume"),
            run_id=run_id,
            expected_version=waiting.receipt.resulting_version,
        )
    )
    succeeded = await service.succeed_run(
        SucceedRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-success"),
            run_id=run_id,
            expected_version=resumed.receipt.resulting_version,
            result=RunResultDraft(answer="approved final answer"),
        )
    )

    run = await service.get_run(tenant_id, run_id)
    events = await service.list_events(tenant_id, run_id)
    assert run.status is RunStatus.SUCCEEDED
    assert run.result is not None and run.result.answer == "approved final answer"
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert events[-1].type is RunEventType.RUN_SUCCEEDED
    assert succeeded.receipt.event_ids == (events[-1].event_id,)


async def test_resolution_path_and_failure_preserve_only_safe_references() -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)
    waiting = await service.wait_for_resolution(
        WaitForResolutionCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-resolution"),
            run_id=run_id,
            expected_version=version,
            reference_id=ReferenceId("resolution-1"),
        )
    )
    resumed = await service.resume_run(
        ResumeRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-resume"),
            run_id=run_id,
            expected_version=waiting.receipt.resulting_version,
        )
    )
    correlation_id = CorrelationId("corr-1")
    failed = await service.fail_run(
        FailRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-fail"),
            run_id=run_id,
            expected_version=resumed.receipt.resulting_version,
            result=RunResultDraft(
                artifact_ids=(ReferenceId("artifact-1"),),
                correlation_id=correlation_id,
            ),
            error=SafeRunError(RunErrorCode.FAILED, correlation_id=correlation_id),
        )
    )

    run = await service.get_run(tenant_id, run_id)
    assert failed.receipt.resulting_status is RunStatus.FAILED
    assert run.result is not None
    assert run.result.draft.artifact_ids == (ReferenceId("artifact-1"),)
    assert run.result.error is not None
    assert run.result.error.correlation_id == correlation_id
    assert [
        event.sequence
        for event in await service.list_events(tenant_id, run_id, after_sequence=2, limit=2)
    ] == [3, 4]


async def test_stale_version_precedes_transition_and_cross_tenant_is_not_found() -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, _ = await create_started(service)

    with pytest.raises(RunLifecycleError) as stale:
        await service.start_run(
            StartRunCommand(
                tenant_id=tenant_id,
                command_id=CommandId("command-stale"),
                run_id=run_id,
                expected_version=1,
            )
        )
    assert stale.value.code is RunErrorCode.VERSION_CONFLICT

    with pytest.raises(RunLifecycleError) as hidden:
        await service.get_run(TenantId("tenant-2"), run_id)
    assert hidden.value.code is RunErrorCode.NOT_FOUND


async def test_reused_command_with_changed_intent_conflicts() -> None:
    service, _, _ = service_fixture()
    command = create_command()
    await service.create_run(command)

    with pytest.raises(RunLifecycleError) as conflict:
        await service.create_run(
            CreateRunCommand(
                tenant_id=command.tenant_id,
                command_id=command.command_id,
                task_id=TaskId("task-2"),
                agent_version=command.agent_version,
                budget=command.budget,
            )
        )
    assert conflict.value.code is RunErrorCode.IDEMPOTENCY_CONFLICT


async def test_same_charge_new_command_requires_current_version_then_commits_no_event() -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)
    charge = BudgetCharge(charge_id=ChargeId("charge-1"), steps=1)
    consumed = await service.consume_budget(
        ConsumeBudgetCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-charge-1"),
            run_id=run_id,
            expected_version=version,
            charge=charge,
        )
    )
    retry_command_id = CommandId("command-charge-replay")
    with pytest.raises(RunLifecycleError) as stale:
        await service.consume_budget(
            ConsumeBudgetCommand(
                tenant_id=tenant_id,
                command_id=retry_command_id,
                run_id=run_id,
                expected_version=version,
                charge=charge,
            )
        )
    assert stale.value.code is RunErrorCode.VERSION_CONFLICT

    replay = await service.consume_budget(
        ConsumeBudgetCommand(
            tenant_id=tenant_id,
            command_id=retry_command_id,
            run_id=run_id,
            expected_version=consumed.receipt.resulting_version,
            charge=charge,
        )
    )
    assert replay.events == ()
    assert replay.receipt.resulting_version == consumed.receipt.resulting_version
    assert (await service.get_run(tenant_id, run_id)).usage.steps == 1


async def test_over_limit_and_deadline_create_one_terminal_result_without_overcharge() -> None:
    service, clock, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)
    exceeded = await service.consume_budget(
        ConsumeBudgetCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-over"),
            run_id=run_id,
            expected_version=version,
            charge=BudgetCharge(charge_id=ChargeId("charge-over"), steps=6),
        )
    )
    over_run = await service.get_run(tenant_id, run_id)
    assert exceeded.receipt.resulting_status is RunStatus.LIMIT_EXCEEDED
    assert over_run.usage.steps == 0
    assert over_run.result is not None and over_run.result.error is not None
    assert over_run.result.error.limit_dimension is BudgetDimension.STEPS

    second_service, second_clock, _ = service_fixture()
    second_tenant, second_run, second_version = await create_started(second_service)
    second_clock.current = NOW + timedelta(minutes=30)
    deadline = await second_service.enforce_deadline(
        EnforceDeadlineCommand(
            tenant_id=second_tenant,
            command_id=CommandId("command-deadline"),
            run_id=second_run,
            expected_version=second_version,
        )
    )
    assert isinstance(deadline, DeadlineOutcome)
    assert deadline.run.status is RunStatus.LIMIT_EXCEEDED
    assert deadline.command_outcome is not None
    assert clock.current == NOW


async def test_deadline_before_boundary_is_read_only_and_cancel_is_idempotent() -> None:
    service, _, _ = service_fixture()
    created = await service.create_run(create_command())
    tenant_id, run_id = created.receipt.tenant_id, created.receipt.run_id
    deadline_command = EnforceDeadlineCommand(
        tenant_id=tenant_id,
        command_id=CommandId("command-deadline"),
        run_id=run_id,
        expected_version=1,
    )
    allowed = await service.enforce_deadline(deadline_command)
    assert allowed.run.status is RunStatus.QUEUED
    assert allowed.command_outcome is None

    cancel_command = CancelRunCommand(
        tenant_id=tenant_id,
        command_id=CommandId("command-cancel"),
        run_id=run_id,
        expected_version=1,
    )
    cancelled = await service.cancel_run(cancel_command)
    replayed = await service.cancel_run(cancel_command)
    assert cancelled.receipt.resulting_status is RunStatus.CANCELLED
    assert replayed.replayed is True


@pytest.mark.parametrize(
    "terminal",
    [
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.LIMIT_EXCEEDED,
    ],
)
async def test_every_terminal_command_replays_and_rejects_later_mutation(
    terminal: RunStatus,
) -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)

    if terminal is RunStatus.SUCCEEDED:
        success_command = SucceedRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("terminal-success"),
            run_id=run_id,
            expected_version=version,
            result=RunResultDraft(answer="approved"),
        )
        first = await service.succeed_run(success_command)
        replay = await service.succeed_run(success_command)
    elif terminal is RunStatus.FAILED:
        failure_command = FailRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("terminal-failure"),
            run_id=run_id,
            expected_version=version,
            result=RunResultDraft(),
            error=SafeRunError(RunErrorCode.FAILED),
        )
        first = await service.fail_run(failure_command)
        replay = await service.fail_run(failure_command)
    elif terminal is RunStatus.CANCELLED:
        cancel_command = CancelRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("terminal-cancel"),
            run_id=run_id,
            expected_version=version,
        )
        first = await service.cancel_run(cancel_command)
        replay = await service.cancel_run(cancel_command)
    else:
        limit_command = ConsumeBudgetCommand(
            tenant_id=tenant_id,
            command_id=CommandId("terminal-limit"),
            run_id=run_id,
            expected_version=version,
            charge=BudgetCharge(charge_id=ChargeId("terminal-over"), steps=6),
        )
        first = await service.consume_budget(limit_command)
        replay = await service.consume_budget(limit_command)

    snapshot = await service.get_run(tenant_id, run_id)
    events = await service.list_events(tenant_id, run_id)
    assert snapshot.status is terminal
    assert replay.replayed is True
    assert replay.receipt is first.receipt
    assert replay.events == first.events

    with pytest.raises(RunLifecycleError) as rejected:
        await service.start_run(
            StartRunCommand(
                tenant_id=tenant_id,
                command_id=CommandId(f"post-terminal-{terminal.value}"),
                run_id=run_id,
                expected_version=snapshot.version,
            )
        )
    assert rejected.value.code is RunErrorCode.ILLEGAL_TRANSITION
    assert await service.get_run(tenant_id, run_id) is snapshot
    assert await service.list_events(tenant_id, run_id) == events


async def test_concurrent_cancel_and_charge_have_exactly_one_winner() -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)

    async def cancel(index: int) -> bool:
        try:
            await service.cancel_run(
                CancelRunCommand(
                    tenant_id=tenant_id,
                    command_id=CommandId(f"cancel-{index}"),
                    run_id=run_id,
                    expected_version=version,
                )
            )
        except RunLifecycleError as error:
            assert error.code is RunErrorCode.VERSION_CONFLICT
            return False
        return True

    async def charge(index: int) -> bool:
        try:
            await service.consume_budget(
                ConsumeBudgetCommand(
                    tenant_id=tenant_id,
                    command_id=CommandId(f"charge-command-{index}"),
                    run_id=run_id,
                    expected_version=version,
                    charge=BudgetCharge(charge_id=ChargeId(f"charge-{index}"), steps=1),
                )
            )
        except RunLifecycleError as error:
            assert error.code is RunErrorCode.VERSION_CONFLICT
            return False
        return True

    results = await asyncio.gather(
        *(cancel(index) if index % 2 == 0 else charge(index) for index in range(1000))
    )
    assert sum(results) == 1


async def test_sensitive_answer_never_enters_receipt_event_or_service_outcome_repr() -> None:
    service, _, _ = service_fixture()
    tenant_id, run_id, version = await create_started(service)
    sentinel = "approved-final-answer-sk-secret"
    outcome = await service.succeed_run(
        SucceedRunCommand(
            tenant_id=tenant_id,
            command_id=CommandId("command-success"),
            run_id=run_id,
            expected_version=version,
            result=RunResultDraft(answer=sentinel),
        )
    )

    assert sentinel not in repr(outcome)
    assert all(sentinel not in repr(event) for event in outcome.events)
    assert sentinel not in repr(outcome.receipt)
