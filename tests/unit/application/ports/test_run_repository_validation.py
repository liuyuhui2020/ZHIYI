from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.application.ports.run_repository_validation import validate_commit_candidate
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

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


def _new_run() -> RunMutation:
    tenant_id = TenantId("tenant-validation")
    return Run.create(
        tenant_id=tenant_id,
        run_id=RunId("run-validation"),
        task_id=TaskId("task-validation"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-validation"),
            version_id=AgentVersionId("version-validation"),
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
        event_id=EventId("event-validation-1"),
    )


def _receipt(
    mutation: RunMutation,
    *,
    command_id: str = "command-validation",
    command_type: str = "create_run",
) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=mutation.run.tenant_id,
        command_id=CommandId(command_id),
        run_id=mutation.run.run_id,
        command_type=command_type,
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=tuple(event.event_id for event in mutation.events),
        created_at=NOW,
    )


def _assert_invariant(call: object) -> None:
    with pytest.raises(RunLifecycleError) as raised:
        call()  # type: ignore[operator]
    assert raised.value.code is RunErrorCode.INVARIANT_VIOLATION


def test_create_update_and_zero_event_candidates_are_valid() -> None:
    created = _new_run()
    validate_commit_candidate(
        expected_version=0,
        current=None,
        updated_run=created.run,
        new_events=created.events,
        receipt=_receipt(created),
        occupied_event_ids=frozenset(),
    )

    started = created.run.start(observed_at=NOW, event_id=EventId("event-validation-2"))
    validate_commit_candidate(
        expected_version=1,
        current=created.run,
        updated_run=started.run,
        new_events=started.events,
        receipt=_receipt(started, command_id="command-start", command_type="start_run"),
        occupied_event_ids=frozenset({created.events[0].event_id}),
    )
    validate_commit_candidate(
        expected_version=1,
        current=created.run,
        updated_run=created.run,
        new_events=(),
        receipt=_receipt(
            RunMutation(run=created.run, events=()),
            command_id="command-noop",
            command_type="consume_budget",
        ),
        occupied_event_ids=frozenset({created.events[0].event_id}),
    )


def test_receipt_and_candidate_must_agree() -> None:
    created = _new_run()
    invalid = replace(_receipt(created), resulting_version=created.run.version + 1)

    _assert_invariant(
        lambda: validate_commit_candidate(
            expected_version=0,
            current=None,
            updated_run=created.run,
            new_events=created.events,
            receipt=invalid,
            occupied_event_ids=frozenset(),
        )
    )


def test_creation_and_zero_event_rules_fail_closed() -> None:
    created = _new_run()
    no_events = RunMutation(run=created.run, events=())

    _assert_invariant(
        lambda: validate_commit_candidate(
            expected_version=0,
            current=None,
            updated_run=created.run,
            new_events=(),
            receipt=_receipt(no_events),
            occupied_event_ids=frozenset(),
        )
    )
    changed = created.run.start(observed_at=NOW, event_id=EventId("event-validation-2"))
    _assert_invariant(
        lambda: validate_commit_candidate(
            expected_version=1,
            current=created.run,
            updated_run=changed.run,
            new_events=(),
            receipt=_receipt(
                RunMutation(run=changed.run, events=()),
                command_id="command-invalid-noop",
                command_type="consume_budget",
            ),
            occupied_event_ids=frozenset({created.events[0].event_id}),
        )
    )


def test_event_identity_sequence_and_next_sequence_are_validated() -> None:
    created = _new_run()
    started = created.run.start(observed_at=NOW, event_id=EventId("event-validation-2"))

    for event, occupied in (
        (started.events[0], frozenset({started.events[0].event_id})),
        (replace(started.events[0], sequence=3), frozenset()),
    ):
        mutation = RunMutation(run=started.run, events=(event,))
        _assert_invariant(
            lambda mutation=mutation, occupied=occupied: validate_commit_candidate(
                expected_version=1,
                current=created.run,
                updated_run=mutation.run,
                new_events=mutation.events,
                receipt=_receipt(
                    mutation,
                    command_id="command-bad-event",
                    command_type="start_run",
                ),
                occupied_event_ids=occupied,
            )
        )


def test_validator_never_mutates_supplied_facts_on_failure() -> None:
    created = _new_run()
    before_run: Run = created.run
    before_events = created.events

    _assert_invariant(
        lambda: validate_commit_candidate(
            expected_version=0,
            current=None,
            updated_run=created.run,
            new_events=created.events,
            receipt=replace(_receipt(created), run_id=RunId("wrong-run")),
            occupied_event_ids=frozenset(),
        )
    )
    assert created.run is before_run
    assert created.events is before_events
