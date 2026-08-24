from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.application.commands.run_lifecycle import (
    CancelRunCommand,
    ConsumeBudgetCommand,
    CreateRunCommand,
    FailRunCommand,
    StartRunCommand,
    SucceedRunCommand,
    WaitForApprovalCommand,
)
from zhiyi.domain.runs.budget import BudgetCharge, RunBudget
from zhiyi.domain.runs.errors import RunErrorCode
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    ChargeId,
    CommandId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.runs.results import RunResultDraft, SafeRunError

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def version_ref() -> AgentVersionRef:
    return AgentVersionRef(
        tenant_id=TenantId("tenant-1"),
        agent_id=AgentId("agent-1"),
        version_id=AgentVersionId("version-1"),
        build_digest="sha256:" + "a" * 64,
    )


def budget() -> RunBudget:
    return RunBudget(
        deadline_at=NOW + timedelta(minutes=30),
        max_steps=5,
        max_model_calls=4,
        max_tool_calls=3,
        max_input_tokens=100,
        max_output_tokens=50,
        max_total_tokens=120,
        max_cost=Decimal("1.25"),
        currency="USD",
    )


def test_expected_version_and_generated_fields_do_not_change_intent() -> None:
    first = StartRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-1"),
        run_id=RunId("run-1"),
        expected_version=1,
    )
    retried = StartRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-2"),
        run_id=RunId("run-1"),
        expected_version=2,
    )

    assert first.intent_fingerprint == retried.intent_fingerprint


def test_business_payload_changes_intent_fingerprint() -> None:
    first = WaitForApprovalCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-1"),
        run_id=RunId("run-1"),
        expected_version=1,
        reference_id=ReferenceId("approval-1"),
    )
    second = WaitForApprovalCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-2"),
        run_id=RunId("run-1"),
        expected_version=1,
        reference_id=ReferenceId("approval-2"),
    )

    assert first.intent_fingerprint != second.intent_fingerprint


def test_create_intent_excludes_command_id_and_contains_business_values() -> None:
    first = CreateRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-1"),
        task_id=TaskId("task-1"),
        agent_version=version_ref(),
        budget=budget(),
    )
    replay = CreateRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-2"),
        task_id=TaskId("task-1"),
        agent_version=version_ref(),
        budget=budget(),
    )

    assert first.expected_version == 0
    assert first.intent_fingerprint == replay.intent_fingerprint


def test_commands_are_immutable_and_validate_expected_version() -> None:
    command = CancelRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-1"),
        run_id=RunId("run-1"),
        expected_version=1,
    )
    with pytest.raises((AttributeError, TypeError)):
        command.expected_version = 2  # type: ignore[misc]
    with pytest.raises(ValueError):
        StartRunCommand(
            tenant_id=TenantId("tenant-1"),
            command_id=CommandId("command-2"),
            run_id=RunId("run-1"),
            expected_version=-1,
        )


def test_result_commands_redact_answer_from_repr_and_fingerprint_text() -> None:
    sentinel = "unapproved-raw-output-sk-secret"
    success = SucceedRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-1"),
        run_id=RunId("run-1"),
        expected_version=2,
        result=RunResultDraft(answer=sentinel),
    )
    failed = FailRunCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-2"),
        run_id=RunId("run-1"),
        expected_version=2,
        result=RunResultDraft(),
        error=SafeRunError(RunErrorCode.FAILED),
    )

    assert sentinel not in repr(success)
    assert sentinel not in success.intent_fingerprint
    assert sentinel not in repr(failed)


def test_budget_command_fingerprint_uses_charge_content() -> None:
    first = ConsumeBudgetCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-1"),
        run_id=RunId("run-1"),
        expected_version=2,
        charge=BudgetCharge(
            charge_id=ChargeId("charge-1"),
            model_calls=1,
            input_tokens=10,
            cost=Decimal("0.1"),
        ),
    )
    changed = ConsumeBudgetCommand(
        tenant_id=TenantId("tenant-1"),
        command_id=CommandId("command-2"),
        run_id=RunId("run-1"),
        expected_version=2,
        charge=BudgetCharge(
            charge_id=ChargeId("charge-1"),
            model_calls=1,
            input_tokens=11,
            cost=Decimal("0.1"),
        ),
    )

    assert first.intent_fingerprint != changed.intent_fingerprint
