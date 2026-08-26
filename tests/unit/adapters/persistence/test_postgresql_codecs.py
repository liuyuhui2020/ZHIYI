"""Strict PostgreSQL record-codec tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

import pytest

from zhiyi.adapters.persistence.postgresql_codecs import (
    decode_canonical_integer,
    decode_canonical_json,
    decode_event,
    decode_receipt,
    decode_run,
    encode_canonical_integer,
    encode_canonical_json,
    encode_event,
    encode_receipt,
    encode_run,
)
from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    RunRepositoryError,
    RunRepositoryErrorCode,
)
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.budget import BudgetSnapshot, RunBudget
from zhiyi.domain.runs.events import RunEvent, RunEventType, RunStatus
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
from zhiyi.domain.runs.results import RunResultDraft

NOW = datetime(2026, 8, 25, 1, 2, 3, 456789, tzinfo=UTC)
FINGERPRINT = "sha256:" + "f" * 64


def _huge_integer(digit: str = "9", *, negative: bool = False) -> int:
    value = 0
    for _ in range(5_000):
        value = value * 10 + int(digit)
    return -value if negative else value


def _run(*, counter: int = 7, max_cost: Decimal = Decimal("10.2500")) -> Run:
    tenant_id = TenantId("tenant-codec")
    return Run(
        tenant_id=tenant_id,
        run_id=RunId("run-codec"),
        task_id=TaskId("task-codec"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-codec"),
            version_id=AgentVersionId("version-codec"),
            build_digest="sha256:" + "a" * 64,
        ),
        status=RunStatus.RUNNING,
        version=2,
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=counter,
            max_model_calls=counter,
            max_tool_calls=counter,
            max_input_tokens=counter,
            max_output_tokens=counter,
            max_total_tokens=counter,
            max_cost=max_cost,
            currency="USD",
        ),
        usage=BudgetSnapshot(
            steps=counter,
            model_calls=counter,
            tool_calls=counter,
            input_tokens=counter,
            output_tokens=0,
            cost=Decimal("0.0001000"),
            _charge_fingerprints={ChargeId("charge-1"): FINGERPRINT},
        ),
        created_at=NOW,
        updated_at=NOW,
        last_observed_at=NOW,
        next_event_sequence=3,
    )


def _receipt() -> CommandReceipt:
    return CommandReceipt(
        tenant_id=TenantId("tenant-codec"),
        command_id=CommandId("command-codec"),
        run_id=RunId("run-codec"),
        command_type="start_run",
        intent_fingerprint=FINGERPRINT,
        resulting_status=RunStatus.RUNNING,
        resulting_version=2,
        event_ids=(EventId("event-codec"),),
        created_at=NOW,
    )


def test_signed_5000_digit_integer_round_trip_does_not_change_process_limit() -> None:
    original_limit = sys.get_int_max_str_digits()
    for value in (_huge_integer(), _huge_integer("8", negative=True), 0, -1):
        assert decode_canonical_integer(encode_canonical_integer(value)) == value
    assert sys.get_int_max_str_digits() == original_limit


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-0"),
        Decimal("1"),
        Decimal("1.2300"),
        Decimal("1E+100"),
        Decimal("1E-100"),
        Decimal("9." + "9" * 20_000),
        Decimal("1" + "0" * 200_000),
        Decimal("0.00000100"),
        Decimal("999999999999999999.00001"),
        Decimal("42.500000"),
        Decimal("1000000000000000000000000.0000000000000000001"),
    ],
)
def test_run_codec_preserves_extreme_decimal_values(value: Decimal) -> None:
    run = _run(max_cost=value)
    assert decode_run(encode_run(run)) == run


def test_run_codec_preserves_5000_digit_counters_nested_usage_and_charge_data() -> None:
    run = _run(counter=_huge_integer("7"))
    record = encode_run(run)

    decoded = decode_run(record)

    assert decoded == run
    assert decoded.usage.charge_fingerprints == run.usage.charge_fingerprints
    assert decoded.created_at.tzinfo is UTC


def test_event_codec_preserves_signed_huge_nested_json_and_immutability() -> None:
    positive = _huge_integer("7")
    negative = _huge_integer("6", negative=True)
    original_limit = sys.get_int_max_str_digits()
    decoded_json = decode_canonical_json(
        encode_canonical_json({"nested": {"negative": negative}, "positive": positive})
    )
    assert decoded_json == {"nested": {"negative": negative}, "positive": positive}

    event = RunEvent(
        event_id=EventId("event-codec"),
        tenant_id=TenantId("tenant-codec"),
        run_id=RunId("run-codec"),
        sequence=2,
        type=RunEventType.RUN_STARTED,
        occurred_at=NOW,
        payload_version=1,
        payload={"previous_status": "queued", "run_version": 2, "status": "running"},
    )
    record = encode_event(event)
    decoded = decode_event(record)

    assert decoded == event
    assert isinstance(decoded.payload, MappingProxyType)
    assert sys.get_int_max_str_digits() == original_limit


def test_receipt_and_terminal_result_round_trip_preserve_references_and_utc() -> None:
    receipt = _receipt()
    assert decode_receipt(encode_receipt(receipt)) == receipt

    running = _run()
    terminal = running.succeed(
        draft=RunResultDraft(
            answer="done",
            warning_codes=("warning.safe",),
            citation_ids=(ReferenceId("citation-1"),),
            artifact_ids=(ReferenceId("artifact-1"),),
            approval_ids=(ReferenceId("approval-1"),),
            correlation_id=CorrelationId("correlation-1"),
        ),
        observed_at=NOW,
        event_id=EventId("event-terminal"),
    ).run
    assert decode_run(encode_run(terminal)) == terminal


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: {**record, "record_format_version": 2},
        lambda record: {**record, "unknown": "secret-value"},
        lambda record: {**record, "run_status": "succeeded"},
        lambda record: {**record, "snapshot": "{malformed"},
    ],
)
def test_run_codec_fails_closed_with_safe_corruption_error(mutation: object) -> None:
    record = mutation(encode_run(_run()))  # type: ignore[operator]
    with pytest.raises(RunRepositoryError) as caught:
        decode_run(record)
    assert caught.value.code is RunRepositoryErrorCode.DATA_CORRUPTION
    assert "secret-value" not in str(caught.value)
    assert "secret-value" not in repr(caught.value)
