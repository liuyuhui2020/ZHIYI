from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from zhiyi.domain.runs.budget import BudgetSnapshot
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import (
    FrozenJsonValue,
    RunEvent,
    RunEventType,
    RunStatus,
    thaw_event_payload,
)
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
from zhiyi.domain.runs.results import RunResult, RunResultDraft, SafeRunError

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "identifier_type",
    [
        TenantId,
        AgentId,
        AgentVersionId,
        TaskId,
        RunId,
        CommandId,
        EventId,
        ChargeId,
        CorrelationId,
        ReferenceId,
    ],
)
def test_identifiers_are_typed_immutable_and_safe(identifier_type: type[object]) -> None:
    value = identifier_type("safe-id_1")  # type: ignore[call-arg]

    assert str(value) == "safe-id_1"
    with pytest.raises((AttributeError, TypeError)):
        value.value = "changed"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "raw",
    ["", " ", " leading", "trailing ", "a" * 129, "line\nbreak", "slash/value", "密钥"],
)
def test_identifiers_reject_unsafe_or_ambiguous_values(raw: str) -> None:
    with pytest.raises(ValueError):
        RunId(raw)


def test_identifier_types_do_not_compare_equal() -> None:
    assert RunId("same") != cast(object, TaskId("same"))


def test_agent_version_reference_requires_same_tenant_and_sha256_digest() -> None:
    reference = AgentVersionRef(
        tenant_id=TenantId("tenant-1"),
        agent_id=AgentId("agent-1"),
        version_id=AgentVersionId("version-1"),
        build_digest=DIGEST,
    )

    assert reference.build_digest == DIGEST
    with pytest.raises(ValueError):
        AgentVersionRef(
            tenant_id=TenantId("tenant-1"),
            agent_id=AgentId("agent-1"),
            version_id=AgentVersionId("version-1"),
            build_digest="not-a-digest",
        )


def test_lifecycle_error_uses_static_safe_message() -> None:
    error = RunLifecycleError(
        RunErrorCode.NOT_FOUND,
        correlation_id=CorrelationId("corr-1"),
    )

    rendered = str(error)
    assert "not found" in rendered.lower()
    assert "corr-1" in rendered
    assert "sk-secret" not in rendered
    assert "sk-secret" not in repr(error)


def test_event_deep_freezes_payload_and_can_thaw_safe_copy() -> None:
    source: dict[str, Any] = {
        "status": "running",
        "run_version": 2,
        "charge_id": "charge-1",
        "usage": {
            "cost": "0.1",
            "input_tokens": 0,
            "model_calls": 0,
            "output_tokens": 0,
            "steps": 1,
            "tool_calls": 0,
            "total_tokens": 0,
        },
    }
    event = RunEvent(
        event_id=EventId("event-1"),
        tenant_id=TenantId("tenant-1"),
        run_id=RunId("run-1"),
        sequence=2,
        type=RunEventType.RUN_BUDGET_CONSUMED,
        occurred_at=NOW,
        payload_version=1,
        payload=cast(Mapping[str, FrozenJsonValue], source),
    )
    source["status"] = "failed"
    source["usage"]["steps"] = 99

    assert event.payload["status"] == "running"
    frozen_usage = event.payload["usage"]
    assert isinstance(frozen_usage, Mapping)
    assert frozen_usage["steps"] == 1
    thawed = thaw_event_payload(event.payload)
    assert thawed == {
        "status": "running",
        "run_version": 2,
        "charge_id": "charge-1",
        "usage": {
            "cost": "0.1",
            "input_tokens": 0,
            "model_calls": 0,
            "output_tokens": 0,
            "steps": 1,
            "tool_calls": 0,
            "total_tokens": 0,
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "running",
            "run_version": 2,
            "charge_id": "charge-1",
            "usage": {"steps": 1, "provider_body": "sk-secret"},
        },
        {"status": "running", "run_version": 2, "previous_status": "sk-secret"},
    ],
)
def test_event_rejects_sensitive_values_hidden_in_allowlisted_fields(
    payload: dict[str, Any],
) -> None:
    event_type = (
        RunEventType.RUN_BUDGET_CONSUMED if "usage" in payload else RunEventType.RUN_STARTED
    )
    with pytest.raises(ValueError):
        RunEvent(
            event_id=EventId("event-safe"),
            tenant_id=TenantId("tenant-1"),
            run_id=RunId("run-1"),
            sequence=2,
            type=event_type,
            occurred_at=NOW,
            payload_version=1,
            payload=cast(Mapping[str, FrozenJsonValue], payload),
        )


def test_event_rejects_unknown_payload_fields_and_unsupported_values() -> None:
    with pytest.raises(ValueError):
        RunEvent(
            event_id=EventId("event-1"),
            tenant_id=TenantId("tenant-1"),
            run_id=RunId("run-1"),
            sequence=1,
            type=RunEventType.RUN_CREATED,
            occurred_at=NOW,
            payload_version=1,
            payload={"status": "queued", "secret": "sk-secret"},
        )
    with pytest.raises(TypeError):
        RunEvent(
            event_id=EventId("event-1"),
            tenant_id=TenantId("tenant-1"),
            run_id=RunId("run-1"),
            sequence=1,
            type=RunEventType.RUN_CREATED,
            occurred_at=NOW,
            payload_version=1,
            payload={"status": cast(Any, object())},
        )


@pytest.mark.parametrize("payload_version", [0, 2])
def test_event_rejects_unsupported_payload_versions(payload_version: int) -> None:
    with pytest.raises(ValueError):
        RunEvent(
            event_id=EventId("event-version"),
            tenant_id=TenantId("tenant-1"),
            run_id=RunId("run-1"),
            sequence=1,
            type=RunEventType.RUN_CREATED,
            occurred_at=NOW,
            payload_version=payload_version,
            payload={
                "agent_version_id": "version-1",
                "run_version": 1,
                "status": "queued",
            },
        )


def test_result_allows_explicit_answer_but_redacts_repr() -> None:
    tenant_id = TenantId("tenant-1")
    run_id = RunId("run-1")
    agent_version = AgentVersionRef(
        tenant_id=tenant_id,
        agent_id=AgentId("agent-1"),
        version_id=AgentVersionId("version-1"),
        build_digest=DIGEST,
    )
    approved_answer = "approved-final-answer-sk-secret"
    result = RunResult(
        result_version=1,
        tenant_id=tenant_id,
        run_id=run_id,
        agent_version=agent_version,
        status=RunStatus.SUCCEEDED,
        draft=RunResultDraft(answer=approved_answer),
        usage=BudgetSnapshot(),
        error=None,
    )

    assert result.answer == approved_answer
    assert approved_answer not in repr(result)
    assert approved_answer not in repr(result.draft)


@pytest.mark.parametrize(
    "forbidden_field",
    ["auth_header", "full_prompt", "provider_body", "raw_output", "reasoning"],
)
def test_result_draft_rejects_unstructured_sensitive_fields(forbidden_field: str) -> None:
    with pytest.raises(TypeError):
        RunResultDraft(**cast(Any, {forbidden_field: "sk-secret"}))


def test_public_error_does_not_accept_downstream_error_text() -> None:
    with pytest.raises(TypeError):
        RunLifecycleError(
            RunErrorCode.FAILED,
            **cast(Any, {"message": "authorization: bearer sk-secret"}),
        )


def test_result_status_and_error_must_agree() -> None:
    tenant_id = TenantId("tenant-1")
    agent_version = AgentVersionRef(
        tenant_id=tenant_id,
        agent_id=AgentId("agent-1"),
        version_id=AgentVersionId("version-1"),
        build_digest=DIGEST,
    )
    with pytest.raises(ValueError):
        RunResult(
            result_version=1,
            tenant_id=tenant_id,
            run_id=RunId("run-1"),
            agent_version=agent_version,
            status=RunStatus.SUCCEEDED,
            draft=RunResultDraft(),
            usage=BudgetSnapshot(),
            error=SafeRunError(RunErrorCode.FAILED),
        )
    with pytest.raises(ValueError):
        RunResult(
            result_version=1,
            tenant_id=tenant_id,
            run_id=RunId("run-1"),
            agent_version=agent_version,
            status=RunStatus.FAILED,
            draft=RunResultDraft(),
            usage=BudgetSnapshot(),
            error=None,
        )
    with pytest.raises(ValueError):
        RunResult(
            result_version=1,
            tenant_id=tenant_id,
            run_id=RunId("run-1"),
            agent_version=agent_version,
            status=RunStatus.CANCELLED,
            draft=RunResultDraft(),
            usage=BudgetSnapshot(),
            error=SafeRunError(RunErrorCode.FAILED),
        )


def test_domain_modules_do_not_import_outer_frameworks() -> None:
    root = Path(__file__).parents[4] / "src" / "zhiyi" / "domain"
    forbidden = {
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "langchain",
        "langgraph",
        "langfuse",
        "zhiyi.application",
        "zhiyi.adapters",
        "zhiyi.infrastructure",
    }

    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(
            name == prefix or name.startswith(prefix + ".")
            for name in imported
            for prefix in forbidden
        ), path


def test_decimal_import_is_used_by_result_fixture() -> None:
    assert Decimal("0") == BudgetSnapshot().cost
