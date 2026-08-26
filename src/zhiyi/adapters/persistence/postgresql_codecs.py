"""Fail-closed format-version-1 codecs for PostgreSQL persistence records."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Never
from typing import cast as type_cast

from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    RunRepositoryError,
    RunRepositoryErrorCode,
)
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.budget import BudgetDimension, BudgetSnapshot, RunBudget, canonical_decimal
from zhiyi.domain.runs.errors import RunErrorCode
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

RECORD_FORMAT_VERSION = 1
_INTEGER_PATTERN = re.compile(r"(?:0|-?[1-9][0-9]*)\Z")
_DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")
_BASE = 1_000_000_000
_CHUNK_DIGITS = 9

_RUN_FIELDS = frozenset(
    {
        "record_format_version",
        "tenant_id",
        "run_id",
        "task_id",
        "agent_id",
        "agent_version_id",
        "agent_build_digest",
        "run_status",
        "run_version",
        "next_event_sequence",
        "created_at",
        "updated_at",
        "last_observed_at",
        "snapshot_format_version",
        "snapshot",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "record_format_version",
        "event_id",
        "tenant_id",
        "run_id",
        "sequence_value",
        "event_type",
        "occurred_at",
        "payload_version",
        "payload",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "record_format_version",
        "tenant_id",
        "command_id",
        "run_id",
        "command_type",
        "intent_fingerprint",
        "resulting_status",
        "resulting_version",
        "event_id",
        "created_at",
    }
)


def _corruption(error: BaseException | None = None) -> Never:
    failure = RunRepositoryError(RunRepositoryErrorCode.DATA_CORRUPTION)
    if error is None:
        raise failure
    raise failure from error


def encode_canonical_integer(value: int) -> str:
    """Encode an arbitrary-size signed integer without process-global limit changes."""

    if type(value) is not int:
        raise TypeError("value must be an integer")
    if value == 0:
        return "0"
    negative = value < 0
    remaining = -value if negative else value
    chunks: list[int] = []
    while remaining:
        remaining, chunk = divmod(remaining, _BASE)
        chunks.append(chunk)
    head = str(chunks.pop())
    body = "".join(f"{chunk:0{_CHUNK_DIGITS}d}" for chunk in reversed(chunks))
    return ("-" if negative else "") + head + body


def decode_canonical_integer(value: str) -> int:
    """Decode an arbitrary-size signed integer in bounded decimal chunks."""

    if type(value) is not str or _INTEGER_PATTERN.fullmatch(value) is None:
        raise ValueError("integer token is not canonical")
    negative = value.startswith("-")
    digits = value[1:] if negative else value
    result = 0
    first_size = len(digits) % _CHUNK_DIGITS or _CHUNK_DIGITS
    positions = [first_size, *range(first_size + _CHUNK_DIGITS, len(digits) + 1, _CHUNK_DIGITS)]
    start = 0
    for end in positions:
        result = result * (10 ** (end - start)) + int(digits[start:end])
        start = end
    return -result if negative else result


def _json_text(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return encode_canonical_integer(value)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, Mapping):
        if not all(type(key) is str for key in value):
            raise TypeError("JSON object keys must be strings")
        return (
            "{"
            + ",".join(f"{_json_text(key)}:{_json_text(value[key])}" for key in sorted(value))
            + "}"
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ",".join(_json_text(item) for item in value) + "]"
    raise TypeError("value is not supported by the canonical JSON codec")


def encode_canonical_json(value: object) -> str:
    return _json_text(value)


def _reject_float(_: str) -> Never:
    raise ValueError("floating-point JSON numbers are not supported")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def decode_canonical_json(value: str) -> object:
    if type(value) is not str:
        raise TypeError("canonical JSON must be text")
    return json.loads(
        value,
        parse_int=decode_canonical_integer,
        parse_float=_reject_float,
        parse_constant=_reject_float,
        object_pairs_hook=_unique_object,
    )


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be aware UTC")
    return value.astimezone(UTC).isoformat()


def _read_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp record is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp record is not UTC")
    return parsed.astimezone(UTC)


def _decimal(value: object) -> Decimal:
    if type(value) is not str or _DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValueError("decimal record is not canonical")
    try:
        decoded = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("decimal record is invalid") from error
    if canonical_decimal(decoded) != value:
        raise ValueError("decimal record is not canonical")
    return decoded


def _mapping(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("record fields are invalid")
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise ValueError("record string is invalid")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("record integer is invalid")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(type(item) is str for item in value):
        raise ValueError("record string sequence is invalid")
    return tuple(value)


def _counter_fields(snapshot: BudgetSnapshot) -> dict[str, object]:
    return {
        "steps": encode_canonical_integer(snapshot.steps),
        "model_calls": encode_canonical_integer(snapshot.model_calls),
        "tool_calls": encode_canonical_integer(snapshot.tool_calls),
        "input_tokens": encode_canonical_integer(snapshot.input_tokens),
        "output_tokens": encode_canonical_integer(snapshot.output_tokens),
        "cost": canonical_decimal(snapshot.cost),
        "charge_fingerprints": {
            str(charge_id): fingerprint for charge_id, fingerprint in snapshot.charge_fingerprints
        },
    }


def _encode_result(result: RunResult | None) -> object:
    if result is None:
        return None
    error = None
    if result.error is not None:
        error = {
            "code": result.error.code.value,
            "correlation_id": (
                str(result.error.correlation_id) if result.error.correlation_id else None
            ),
            "limit_dimension": (
                result.error.limit_dimension.value if result.error.limit_dimension else None
            ),
        }
    return {
        "result_version": result.result_version,
        "tenant_id": str(result.tenant_id),
        "run_id": str(result.run_id),
        "agent_version": {
            "tenant_id": str(result.agent_version.tenant_id),
            "agent_id": str(result.agent_version.agent_id),
            "version_id": str(result.agent_version.version_id),
            "build_digest": result.agent_version.build_digest,
        },
        "status": result.status.value,
        "draft": {
            "answer": result.draft.answer,
            "warning_codes": list(result.draft.warning_codes),
            "citation_ids": [str(value) for value in result.draft.citation_ids],
            "artifact_ids": [str(value) for value in result.draft.artifact_ids],
            "approval_ids": [str(value) for value in result.draft.approval_ids],
            "correlation_id": (
                str(result.draft.correlation_id) if result.draft.correlation_id else None
            ),
        },
        "usage": _counter_fields(result.usage),
        "error": error,
    }


def encode_run(run: Run) -> dict[str, object]:
    if not isinstance(run, Run):
        raise TypeError("run must be Run")
    snapshot = {
        "tenant_id": str(run.tenant_id),
        "run_id": str(run.run_id),
        "task_id": str(run.task_id),
        "agent_version": {
            "tenant_id": str(run.agent_version.tenant_id),
            "agent_id": str(run.agent_version.agent_id),
            "version_id": str(run.agent_version.version_id),
            "build_digest": run.agent_version.build_digest,
        },
        "status": run.status.value,
        "version": encode_canonical_integer(run.version),
        "budget": {
            "deadline_at": _timestamp(run.budget.deadline_at),
            "max_steps": encode_canonical_integer(run.budget.max_steps),
            "max_model_calls": encode_canonical_integer(run.budget.max_model_calls),
            "max_tool_calls": encode_canonical_integer(run.budget.max_tool_calls),
            "max_input_tokens": encode_canonical_integer(run.budget.max_input_tokens),
            "max_output_tokens": encode_canonical_integer(run.budget.max_output_tokens),
            "max_total_tokens": encode_canonical_integer(run.budget.max_total_tokens),
            "max_cost": canonical_decimal(run.budget.max_cost),
            "currency": run.budget.currency,
        },
        "usage": _counter_fields(run.usage),
        "created_at": _timestamp(run.created_at),
        "updated_at": _timestamp(run.updated_at),
        "last_observed_at": _timestamp(run.last_observed_at),
        "next_event_sequence": encode_canonical_integer(run.next_event_sequence),
        "result": _encode_result(run.result),
    }
    return {
        "record_format_version": RECORD_FORMAT_VERSION,
        "tenant_id": str(run.tenant_id),
        "run_id": str(run.run_id),
        "task_id": str(run.task_id),
        "agent_id": str(run.agent_version.agent_id),
        "agent_version_id": str(run.agent_version.version_id),
        "agent_build_digest": run.agent_version.build_digest,
        "run_status": run.status.value,
        "run_version": encode_canonical_integer(run.version),
        "next_event_sequence": encode_canonical_integer(run.next_event_sequence),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "last_observed_at": run.last_observed_at,
        "snapshot_format_version": RECORD_FORMAT_VERSION,
        "snapshot": encode_canonical_json(snapshot),
    }


def _decode_usage(value: object) -> BudgetSnapshot:
    fields = frozenset(
        {
            "steps",
            "model_calls",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "cost",
            "charge_fingerprints",
        }
    )
    raw = _mapping(value, fields)
    charge_data = raw["charge_fingerprints"]
    if not isinstance(charge_data, Mapping):
        raise ValueError("charge fingerprints are invalid")
    return BudgetSnapshot(
        steps=decode_canonical_integer(raw["steps"]),  # type: ignore[arg-type]
        model_calls=decode_canonical_integer(raw["model_calls"]),  # type: ignore[arg-type]
        tool_calls=decode_canonical_integer(raw["tool_calls"]),  # type: ignore[arg-type]
        input_tokens=decode_canonical_integer(raw["input_tokens"]),  # type: ignore[arg-type]
        output_tokens=decode_canonical_integer(raw["output_tokens"]),  # type: ignore[arg-type]
        cost=_decimal(raw["cost"]),
        _charge_fingerprints={ChargeId(key): str(item) for key, item in charge_data.items()},
    )


def _decode_result(value: object) -> RunResult | None:
    if value is None:
        return None
    raw = _mapping(
        value,
        frozenset(
            {
                "result_version",
                "tenant_id",
                "run_id",
                "agent_version",
                "status",
                "draft",
                "usage",
                "error",
            }
        ),
    )
    agent = _mapping(
        raw["agent_version"], frozenset({"tenant_id", "agent_id", "version_id", "build_digest"})
    )
    draft = _mapping(
        raw["draft"],
        frozenset(
            {
                "answer",
                "warning_codes",
                "citation_ids",
                "artifact_ids",
                "approval_ids",
                "correlation_id",
            }
        ),
    )
    error_raw = raw["error"]
    safe_error = None
    if error_raw is not None:
        decoded_error = _mapping(
            error_raw, frozenset({"code", "correlation_id", "limit_dimension"})
        )
        safe_error = SafeRunError(
            code=RunErrorCode(_string(decoded_error["code"])),
            correlation_id=(
                CorrelationId(_string(decoded_error["correlation_id"]))
                if decoded_error["correlation_id"] is not None
                else None
            ),
            limit_dimension=(
                BudgetDimension(_string(decoded_error["limit_dimension"]))
                if decoded_error["limit_dimension"] is not None
                else None
            ),
        )
    return RunResult(
        result_version=_integer(raw["result_version"]),
        tenant_id=TenantId(raw["tenant_id"]),  # type: ignore[arg-type]
        run_id=RunId(raw["run_id"]),  # type: ignore[arg-type]
        agent_version=AgentVersionRef(
            tenant_id=TenantId(agent["tenant_id"]),  # type: ignore[arg-type]
            agent_id=AgentId(agent["agent_id"]),  # type: ignore[arg-type]
            version_id=AgentVersionId(agent["version_id"]),  # type: ignore[arg-type]
            build_digest=agent["build_digest"],  # type: ignore[arg-type]
        ),
        status=RunStatus(_string(raw["status"])),
        draft=RunResultDraft(
            answer=draft["answer"],  # type: ignore[arg-type]
            warning_codes=_strings(draft["warning_codes"]),
            citation_ids=tuple(ReferenceId(item) for item in _strings(draft["citation_ids"])),
            artifact_ids=tuple(ReferenceId(item) for item in _strings(draft["artifact_ids"])),
            approval_ids=tuple(ReferenceId(item) for item in _strings(draft["approval_ids"])),
            correlation_id=(
                CorrelationId(_string(draft["correlation_id"]))
                if draft["correlation_id"] is not None
                else None
            ),
        ),
        usage=_decode_usage(raw["usage"]),
        error=safe_error,
    )


def decode_run(record: Mapping[str, object]) -> Run:
    try:
        raw = _mapping(record, _RUN_FIELDS)
        if (
            raw["record_format_version"] != RECORD_FORMAT_VERSION
            or raw["snapshot_format_version"] != RECORD_FORMAT_VERSION
        ):
            raise ValueError("record version is unsupported")
        snapshot = _mapping(
            decode_canonical_json(raw["snapshot"]),  # type: ignore[arg-type]
            frozenset(
                {
                    "tenant_id",
                    "run_id",
                    "task_id",
                    "agent_version",
                    "status",
                    "version",
                    "budget",
                    "usage",
                    "created_at",
                    "updated_at",
                    "last_observed_at",
                    "next_event_sequence",
                    "result",
                }
            ),
        )
        agent = _mapping(
            snapshot["agent_version"],
            frozenset({"tenant_id", "agent_id", "version_id", "build_digest"}),
        )
        budget = _mapping(
            snapshot["budget"],
            frozenset(
                {
                    "deadline_at",
                    "max_steps",
                    "max_model_calls",
                    "max_tool_calls",
                    "max_input_tokens",
                    "max_output_tokens",
                    "max_total_tokens",
                    "max_cost",
                    "currency",
                }
            ),
        )
        run = Run(
            tenant_id=TenantId(_string(snapshot["tenant_id"])),
            run_id=RunId(_string(snapshot["run_id"])),
            task_id=TaskId(_string(snapshot["task_id"])),
            agent_version=AgentVersionRef(
                tenant_id=TenantId(_string(agent["tenant_id"])),
                agent_id=AgentId(_string(agent["agent_id"])),
                version_id=AgentVersionId(_string(agent["version_id"])),
                build_digest=_string(agent["build_digest"]),
            ),
            status=RunStatus(_string(snapshot["status"])),
            version=decode_canonical_integer(_string(snapshot["version"])),
            budget=RunBudget(
                deadline_at=_read_timestamp(budget["deadline_at"]),
                max_steps=decode_canonical_integer(budget["max_steps"]),  # type: ignore[arg-type]
                max_model_calls=decode_canonical_integer(budget["max_model_calls"]),  # type: ignore[arg-type]
                max_tool_calls=decode_canonical_integer(budget["max_tool_calls"]),  # type: ignore[arg-type]
                max_input_tokens=decode_canonical_integer(budget["max_input_tokens"]),  # type: ignore[arg-type]
                max_output_tokens=decode_canonical_integer(budget["max_output_tokens"]),  # type: ignore[arg-type]
                max_total_tokens=decode_canonical_integer(budget["max_total_tokens"]),  # type: ignore[arg-type]
                max_cost=_decimal(budget["max_cost"]),
                currency=budget["currency"],  # type: ignore[arg-type]
            ),
            usage=_decode_usage(snapshot["usage"]),
            created_at=_read_timestamp(snapshot["created_at"]),
            updated_at=_read_timestamp(snapshot["updated_at"]),
            last_observed_at=_read_timestamp(snapshot["last_observed_at"]),
            next_event_sequence=decode_canonical_integer(_string(snapshot["next_event_sequence"])),
            result=_decode_result(snapshot["result"]),
        )
        projections = (
            (str(run.tenant_id), raw["tenant_id"]),
            (str(run.run_id), raw["run_id"]),
            (str(run.task_id), raw["task_id"]),
            (str(run.agent_version.agent_id), raw["agent_id"]),
            (str(run.agent_version.version_id), raw["agent_version_id"]),
            (run.agent_version.build_digest, raw["agent_build_digest"]),
            (run.status.value, raw["run_status"]),
            (encode_canonical_integer(run.version), raw["run_version"]),
            (encode_canonical_integer(run.next_event_sequence), raw["next_event_sequence"]),
            (run.created_at, _read_timestamp(raw["created_at"])),
            (run.updated_at, _read_timestamp(raw["updated_at"])),
            (run.last_observed_at, _read_timestamp(raw["last_observed_at"])),
        )
        if any(authoritative != projected for authoritative, projected in projections):
            raise ValueError("run projections disagree")
        return run
    except RunRepositoryError:
        raise
    except Exception as error:
        _corruption(error)


def encode_event(event: RunEvent) -> dict[str, object]:
    if not isinstance(event, RunEvent):
        raise TypeError("event must be RunEvent")
    return {
        "record_format_version": RECORD_FORMAT_VERSION,
        "event_id": str(event.event_id),
        "tenant_id": str(event.tenant_id),
        "run_id": str(event.run_id),
        "sequence_value": encode_canonical_integer(event.sequence),
        "event_type": event.type.value,
        "occurred_at": event.occurred_at,
        "payload_version": event.payload_version,
        "payload": encode_canonical_json(thaw_event_payload(event.payload)),
    }


def decode_event(record: Mapping[str, object]) -> RunEvent:
    try:
        raw = _mapping(record, _EVENT_FIELDS)
        if raw["record_format_version"] != RECORD_FORMAT_VERSION:
            raise ValueError("event record version is unsupported")
        payload = decode_canonical_json(raw["payload"])  # type: ignore[arg-type]
        if not isinstance(payload, Mapping):
            raise ValueError("event payload must be an object")
        return RunEvent(
            event_id=EventId(raw["event_id"]),  # type: ignore[arg-type]
            tenant_id=TenantId(raw["tenant_id"]),  # type: ignore[arg-type]
            run_id=RunId(raw["run_id"]),  # type: ignore[arg-type]
            sequence=decode_canonical_integer(raw["sequence_value"]),  # type: ignore[arg-type]
            type=RunEventType(_string(raw["event_type"])),
            occurred_at=_read_timestamp(raw["occurred_at"]),
            payload_version=raw["payload_version"],  # type: ignore[arg-type]
            payload=type_cast(Mapping[str, FrozenJsonValue], payload),
        )
    except RunRepositoryError:
        raise
    except Exception as error:
        _corruption(error)


def encode_receipt(receipt: CommandReceipt) -> dict[str, object]:
    if not isinstance(receipt, CommandReceipt):
        raise TypeError("receipt must be CommandReceipt")
    if len(receipt.event_ids) > 1:
        raise ValueError("receipt cannot reference multiple events")
    return {
        "record_format_version": RECORD_FORMAT_VERSION,
        "tenant_id": str(receipt.tenant_id),
        "command_id": str(receipt.command_id),
        "run_id": str(receipt.run_id),
        "command_type": receipt.command_type,
        "intent_fingerprint": receipt.intent_fingerprint,
        "resulting_status": receipt.resulting_status.value,
        "resulting_version": encode_canonical_integer(receipt.resulting_version),
        "event_id": str(receipt.event_ids[0]) if receipt.event_ids else None,
        "created_at": receipt.created_at,
    }


def decode_receipt(record: Mapping[str, object]) -> CommandReceipt:
    try:
        raw = _mapping(record, _RECEIPT_FIELDS)
        if raw["record_format_version"] != RECORD_FORMAT_VERSION:
            raise ValueError("receipt record version is unsupported")
        event_id = raw["event_id"]
        return CommandReceipt(
            tenant_id=TenantId(raw["tenant_id"]),  # type: ignore[arg-type]
            command_id=CommandId(raw["command_id"]),  # type: ignore[arg-type]
            run_id=RunId(raw["run_id"]),  # type: ignore[arg-type]
            command_type=raw["command_type"],  # type: ignore[arg-type]
            intent_fingerprint=raw["intent_fingerprint"],  # type: ignore[arg-type]
            resulting_status=RunStatus(_string(raw["resulting_status"])),
            resulting_version=decode_canonical_integer(raw["resulting_version"]),  # type: ignore[arg-type]
            event_ids=(EventId(event_id),) if event_id is not None else (),  # type: ignore[arg-type]
            created_at=_read_timestamp(raw["created_at"]),
        )
    except RunRepositoryError:
        raise
    except Exception as error:
        _corruption(error)


def json_text_column(value: Any) -> str:
    """Validate a driver-returned JSON-as-text column at the adapter boundary."""

    if type(value) is not str:
        _corruption()
    return value
