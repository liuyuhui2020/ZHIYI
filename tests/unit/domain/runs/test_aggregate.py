from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import BudgetCharge, BudgetDimension, RunBudget
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEvent, RunEventType, RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    ChargeId,
    CorrelationId,
    EventId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.runs.results import RunResultDraft, SafeRunError

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


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


def create_run(*, budget: RunBudget | None = None) -> tuple[Run, RunEvent]:
    tenant_id = TenantId("tenant-1")
    mutation = Run.create(
        tenant_id=tenant_id,
        run_id=RunId("run-1"),
        task_id=TaskId("task-1"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-1"),
            version_id=AgentVersionId("version-1"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=budget or make_budget(),
        observed_at=NOW,
        event_id=EventId("event-1"),
    )
    return mutation.run, mutation.events[0]


def start(run: Run, *, event: int = 2, observed_at: datetime = NOW) -> Run:
    return run.start(observed_at=observed_at, event_id=EventId(f"event-{event}")).run


def build_status(status: RunStatus) -> Run:
    queued = create_run()[0]
    if status is RunStatus.QUEUED:
        return queued
    running = start(queued)
    if status is RunStatus.RUNNING:
        return running
    if status is RunStatus.WAITING_APPROVAL:
        return running.wait_for_approval(
            reference_id=ReferenceId("approval-build"),
            observed_at=NOW,
            event_id=EventId("event-build"),
        ).run
    if status is RunStatus.WAITING_RESOLUTION:
        return running.wait_for_resolution(
            reference_id=ReferenceId("resolution-build"),
            observed_at=NOW,
            event_id=EventId("event-build"),
        ).run
    if status is RunStatus.SUCCEEDED:
        return running.succeed(
            draft=RunResultDraft(), observed_at=NOW, event_id=EventId("event-build")
        ).run
    if status is RunStatus.FAILED:
        return running.fail(
            draft=RunResultDraft(),
            error=SafeRunError(RunErrorCode.FAILED),
            observed_at=NOW,
            event_id=EventId("event-build"),
        ).run
    if status is RunStatus.CANCELLED:
        return queued.cancel(observed_at=NOW, event_id=EventId("event-build")).run
    return queued.enforce_deadline(
        observed_at=queued.budget.deadline_at,
        event_id=EventId("event-build"),
    ).run


def transition_to(run: Run, target: RunStatus) -> RunMutation:
    event_id = EventId("event-matrix")
    if target is RunStatus.RUNNING:
        if run.status is RunStatus.QUEUED:
            return run.start(observed_at=NOW, event_id=event_id)
        return run.resume(observed_at=NOW, event_id=event_id)
    if target is RunStatus.WAITING_APPROVAL:
        return run.wait_for_approval(
            reference_id=ReferenceId("approval-matrix"),
            observed_at=NOW,
            event_id=event_id,
        )
    if target is RunStatus.WAITING_RESOLUTION:
        return run.wait_for_resolution(
            reference_id=ReferenceId("resolution-matrix"),
            observed_at=NOW,
            event_id=event_id,
        )
    if target is RunStatus.SUCCEEDED:
        return run.succeed(draft=RunResultDraft(), observed_at=NOW, event_id=event_id)
    if target is RunStatus.FAILED:
        return run.fail(
            draft=RunResultDraft(),
            error=SafeRunError(RunErrorCode.FAILED),
            observed_at=NOW,
            event_id=event_id,
        )
    if target is RunStatus.CANCELLED:
        return run.cancel(observed_at=NOW, event_id=event_id)
    if target is RunStatus.LIMIT_EXCEEDED:
        return run.enforce_deadline(observed_at=run.budget.deadline_at, event_id=event_id)
    raise AssertionError("queued is only an initial status")


_LEGAL_STATE_PAIRS = {
    (RunStatus.QUEUED, RunStatus.RUNNING),
    (RunStatus.QUEUED, RunStatus.CANCELLED),
    (RunStatus.QUEUED, RunStatus.LIMIT_EXCEEDED),
    (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL),
    (RunStatus.RUNNING, RunStatus.WAITING_RESOLUTION),
    (RunStatus.RUNNING, RunStatus.SUCCEEDED),
    (RunStatus.RUNNING, RunStatus.FAILED),
    (RunStatus.RUNNING, RunStatus.CANCELLED),
    (RunStatus.RUNNING, RunStatus.LIMIT_EXCEEDED),
    (RunStatus.WAITING_APPROVAL, RunStatus.RUNNING),
    (RunStatus.WAITING_APPROVAL, RunStatus.CANCELLED),
    (RunStatus.WAITING_APPROVAL, RunStatus.LIMIT_EXCEEDED),
    (RunStatus.WAITING_RESOLUTION, RunStatus.RUNNING),
    (RunStatus.WAITING_RESOLUTION, RunStatus.CANCELLED),
    (RunStatus.WAITING_RESOLUTION, RunStatus.LIMIT_EXCEEDED),
}


def test_create_pins_agent_version_and_origin_invariants() -> None:
    run, event = create_run()

    assert run.status is RunStatus.QUEUED
    assert run.version == 1
    assert run.next_event_sequence == 2
    assert run.result is None
    assert event.sequence == 1
    assert event.type is RunEventType.RUN_CREATED
    assert event.payload["run_version"] == 1
    assert run.agent_version.version_id == AgentVersionId("version-1")


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source in RunStatus
        for target in RunStatus
        if source is not target and target is not RunStatus.QUEUED
    ],
)
def test_complete_state_pair_matrix(source: RunStatus, target: RunStatus) -> None:
    run = build_status(source)
    if (source, target) in _LEGAL_STATE_PAIRS:
        mutation = transition_to(run, target)
        assert mutation.run.status is target
        return

    if source.is_terminal and target is RunStatus.LIMIT_EXCEEDED:
        mutation = transition_to(run, target)
        assert mutation.run is run
        assert mutation.events == ()
        return
    with pytest.raises(RunLifecycleError) as raised:
        transition_to(run, target)
    assert raised.value.code is RunErrorCode.ILLEGAL_TRANSITION
    assert run.status is source


def test_deadline_must_be_after_creation_and_agent_tenant_must_match() -> None:
    with pytest.raises(ValueError):
        create_run(budget=make_budget(deadline_at=NOW))

    foreign_version = AgentVersionRef(
        tenant_id=TenantId("tenant-2"),
        agent_id=AgentId("agent-1"),
        version_id=AgentVersionId("version-1"),
        build_digest="sha256:" + "a" * 64,
    )
    with pytest.raises(ValueError):
        Run.create(
            tenant_id=TenantId("tenant-1"),
            run_id=RunId("run-1"),
            task_id=TaskId("task-1"),
            agent_version=foreign_version,
            budget=make_budget(),
            observed_at=NOW,
            event_id=EventId("event-1"),
        )


def test_progress_path_has_continuous_versions_sequences_and_forward_time() -> None:
    queued, _ = create_run()
    running_mutation = queued.start(
        observed_at=NOW + timedelta(seconds=1), event_id=EventId("event-2")
    )
    waiting_mutation = running_mutation.run.wait_for_approval(
        reference_id=ReferenceId("approval-1"),
        observed_at=NOW - timedelta(seconds=1),
        event_id=EventId("event-3"),
    )
    resumed_mutation = waiting_mutation.run.resume(
        observed_at=NOW + timedelta(seconds=3), event_id=EventId("event-4")
    )

    assert resumed_mutation.run.status is RunStatus.RUNNING
    assert resumed_mutation.run.version == 4
    assert resumed_mutation.run.next_event_sequence == 5
    assert [
        running_mutation.events[0].sequence,
        waiting_mutation.events[0].sequence,
        resumed_mutation.events[0].sequence,
    ] == [2, 3, 4]
    assert waiting_mutation.events[0].occurred_at == NOW + timedelta(seconds=1)
    assert resumed_mutation.run.last_observed_at == NOW + timedelta(seconds=3)


def test_wait_for_resolution_and_resume_are_legal() -> None:
    running = start(create_run()[0])
    waiting = running.wait_for_resolution(
        reference_id=ReferenceId("resolution-1"),
        observed_at=NOW,
        event_id=EventId("event-3"),
    ).run

    assert waiting.status is RunStatus.WAITING_RESOLUTION
    assert waiting.resume(observed_at=NOW, event_id=EventId("event-4")).run.status is (
        RunStatus.RUNNING
    )


@pytest.mark.parametrize(
    "operation",
    [
        lambda run: run.wait_for_approval(
            reference_id=ReferenceId("approval-1"),
            observed_at=NOW,
            event_id=EventId("illegal"),
        ),
        lambda run: run.resume(observed_at=NOW, event_id=EventId("illegal")),
        lambda run: run.succeed(
            draft=RunResultDraft(), observed_at=NOW, event_id=EventId("illegal")
        ),
        lambda run: run.fail(
            draft=RunResultDraft(),
            error=SafeRunError(RunErrorCode.FAILED),
            observed_at=NOW,
            event_id=EventId("illegal"),
        ),
    ],
)
def test_illegal_transitions_leave_original_unchanged(
    operation: Callable[[Run], object],
) -> None:
    queued, _ = create_run()
    before = queued

    with pytest.raises(RunLifecycleError) as raised:
        operation(queued)

    assert raised.value.code is RunErrorCode.ILLEGAL_TRANSITION
    assert queued is before
    assert queued.status is RunStatus.QUEUED
    assert queued.version == 1


@pytest.mark.parametrize("terminal", ["success", "failure", "cancel", "limit"])
def test_terminal_result_event_agree_and_cannot_be_rewritten(terminal: str) -> None:
    running = start(create_run()[0])
    expected: tuple[RunStatus, RunEventType, RunErrorCode | None]
    if terminal == "success":
        mutation = running.succeed(
            draft=RunResultDraft(answer="approved answer"),
            observed_at=NOW,
            event_id=EventId("event-3"),
        )
        expected = (RunStatus.SUCCEEDED, RunEventType.RUN_SUCCEEDED, None)
    elif terminal == "failure":
        mutation = running.fail(
            draft=RunResultDraft(correlation_id=CorrelationId("corr-1")),
            error=SafeRunError(RunErrorCode.FAILED, correlation_id=CorrelationId("corr-1")),
            observed_at=NOW,
            event_id=EventId("event-3"),
        )
        expected = (RunStatus.FAILED, RunEventType.RUN_FAILED, RunErrorCode.FAILED)
    elif terminal == "cancel":
        mutation = running.cancel(
            correlation_id=CorrelationId("corr-1"),
            observed_at=NOW,
            event_id=EventId("event-3"),
        )
        expected = (RunStatus.CANCELLED, RunEventType.RUN_CANCELLED, RunErrorCode.CANCELLED)
    else:
        mutation = running.enforce_deadline(
            observed_at=running.budget.deadline_at,
            event_id=EventId("event-3"),
        )
        expected = (
            RunStatus.LIMIT_EXCEEDED,
            RunEventType.RUN_LIMIT_EXCEEDED,
            RunErrorCode.BUDGET_LIMIT,
        )

    assert mutation.run.status is expected[0]
    assert mutation.events[0].type is expected[1]
    assert mutation.run.result is not None
    assert mutation.run.result.status is expected[0]
    assert mutation.run.result.error is None or mutation.run.result.error.code is expected[2]

    with pytest.raises(RunLifecycleError):
        mutation.run.cancel(observed_at=NOW, event_id=EventId("event-4"))


def test_budget_charge_replay_is_noop_and_excess_terminates_without_charge() -> None:
    running = start(create_run()[0])
    charge = BudgetCharge(charge_id=ChargeId("charge-1"), steps=1)
    consumed = running.consume_budget(charge=charge, observed_at=NOW, event_id=EventId("event-3"))
    replayed = consumed.run.consume_budget(
        charge=charge, observed_at=NOW, event_id=EventId("event-unused")
    )
    exceeded = consumed.run.consume_budget(
        charge=BudgetCharge(charge_id=ChargeId("charge-2"), steps=5),
        observed_at=NOW,
        event_id=EventId("event-4"),
    )

    assert consumed.run.usage.steps == 1
    assert replayed.run is consumed.run
    assert replayed.events == ()
    assert exceeded.run.status is RunStatus.LIMIT_EXCEEDED
    assert exceeded.run.usage.steps == 1
    assert exceeded.run.result is not None
    assert exceeded.run.result.error is not None
    assert exceeded.run.result.error.limit_dimension is BudgetDimension.STEPS


@pytest.mark.parametrize(
    "build_waiting",
    [
        lambda run: run,
        lambda run: start(run),
        lambda run: (
            start(run)
            .wait_for_approval(
                reference_id=ReferenceId("approval-1"),
                observed_at=NOW,
                event_id=EventId("event-3"),
            )
            .run
        ),
        lambda run: (
            start(run)
            .wait_for_resolution(
                reference_id=ReferenceId("resolution-1"),
                observed_at=NOW,
                event_id=EventId("event-3"),
            )
            .run
        ),
    ],
)
def test_cancel_is_allowed_from_every_non_terminal_state(
    build_waiting: Callable[[Run], Run],
) -> None:
    run = build_waiting(create_run()[0])
    cancelled = run.cancel(observed_at=NOW, event_id=EventId("event-cancel")).run

    assert cancelled.status is RunStatus.CANCELLED


def test_deadline_before_boundary_is_noop_and_at_boundary_is_terminal() -> None:
    queued, _ = create_run()
    allowed = queued.enforce_deadline(
        observed_at=queued.budget.deadline_at - timedelta(microseconds=1),
        event_id=EventId("unused"),
    )
    exceeded = queued.enforce_deadline(
        observed_at=queued.budget.deadline_at,
        event_id=EventId("event-2"),
    )

    assert allowed.run is queued
    assert allowed.events == ()
    assert exceeded.run.status is RunStatus.LIMIT_EXCEEDED


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_RESOLUTION,
    ],
)
def test_exact_deadline_terminates_every_non_terminal_state(status: RunStatus) -> None:
    run = build_status(status)

    mutation = run.enforce_deadline(
        observed_at=run.budget.deadline_at,
        event_id=EventId("event-deadline-matrix"),
    )

    assert mutation.run.status is RunStatus.LIMIT_EXCEEDED
    assert mutation.run.result is not None
    assert mutation.run.result.usage == run.usage
