"""Atomic PostgreSQL RunRepository with receipt-first command arbitration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from typing import cast as type_cast

from sqlalchemy import Text, bindparam, cast, insert, literal, select, text, update
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from zhiyi.adapters.persistence.postgresql_codecs import (
    RECORD_FORMAT_VERSION,
    decode_event,
    decode_receipt,
    decode_run,
    encode_canonical_integer,
    encode_event,
    encode_receipt,
    encode_run,
)
from zhiyi.adapters.persistence.postgresql_schema import (
    run_command_receipts,
    run_events,
    runs,
)
from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    CommitOutcome,
    RunRepository,
    RunRepositoryError,
    RunRepositoryErrorCode,
)
from zhiyi.application.ports.run_repository_validation import validate_commit_candidate
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEvent, RunEventType
from zhiyi.domain.runs.identifiers import CommandId, RunId, TenantId
from zhiyi.infrastructure.database.schema_compatibility import ensure_schema_compatible

_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KNOWN_ROLLBACK_SQLSTATES = frozenset({"40001", "40P01", "55P03", "57014", "57P01"})
_UNKNOWN_OUTCOME_SQLSTATES = frozenset({"08007", "40003"})
_LOGGER = logging.getLogger(__name__)
_COMMAND_EVENT_TYPES = {
    "create_run": RunEventType.RUN_CREATED,
    "start_run": RunEventType.RUN_STARTED,
    "wait_for_approval": RunEventType.RUN_WAITING_APPROVAL,
    "wait_for_resolution": RunEventType.RUN_WAITING_RESOLUTION,
    "resume_run": RunEventType.RUN_RESUMED,
    "consume_budget": RunEventType.RUN_BUDGET_CONSUMED,
    "succeed_run": RunEventType.RUN_SUCCEEDED,
    "fail_run": RunEventType.RUN_FAILED,
    "cancel_run": RunEventType.RUN_CANCELLED,
    "enforce_deadline": RunEventType.RUN_LIMIT_EXCEEDED,
}


@dataclass(frozen=True, slots=True)
class _EncodedCommitCandidate:
    run: Mapping[str, object]
    receipt: Mapping[str, object]
    events: tuple[Mapping[str, object], ...]


class TransactionPhase(StrEnum):
    ACQUIRE = "acquire"
    BEGIN = "begin"
    ARBITRATION = "arbitration"
    LOCK = "lock"
    WRITE = "write"
    COMMIT = "commit"
    COMPLETE = "complete"


def _sqlstate(error: BaseException) -> str | None:
    candidate: object = error
    for _ in range(3):
        value = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if type(value) is str:
            return value
        nested = getattr(candidate, "orig", None)
        if not isinstance(nested, BaseException):
            break
        candidate = nested
    return None


def classify_storage_failure(
    error: BaseException,
    *,
    phase: TransactionPhase,
    rollback_confirmed: bool,
) -> RunRepositoryErrorCode:
    """Classify a storage failure without inspecting or exposing query values."""

    state = _sqlstate(error)
    if rollback_confirmed or state in _KNOWN_ROLLBACK_SQLSTATES:
        return RunRepositoryErrorCode.STORAGE_UNAVAILABLE
    if phase is TransactionPhase.COMMIT and (
        state in _UNKNOWN_OUTCOME_SQLSTATES
        or bool(getattr(error, "connection_invalidated", False))
        or state is None
    ):
        return RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN
    return RunRepositoryErrorCode.STORAGE_UNAVAILABLE


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if type(value) is str else None


def _log_storage_failure(
    code: RunRepositoryErrorCode,
    *,
    phase: TransactionPhase,
    tenant_id: TenantId,
    run_id: RunId | None,
) -> None:
    _LOGGER.warning(
        "run_repository_storage_failure",
        extra={
            "repository_error_code": code.value,
            "transaction_phase": phase.value,
            "tenant_id": str(tenant_id),
            "run_id": str(run_id) if run_id is not None else None,
        },
    )


async def _rollback_if_active(transaction: AsyncTransaction | None) -> bool:
    if transaction is None or not transaction.is_active:
        return False
    try:
        await transaction.rollback()
    except Exception:
        return False
    return True


def _validate_identity(tenant_id: TenantId, run_id: RunId | None = None) -> None:
    if not isinstance(tenant_id, TenantId):
        raise TypeError("tenant_id must be TenantId")
    if run_id is not None and not isinstance(run_id, RunId):
        raise TypeError("run_id must be RunId")


def _run_select() -> Any:
    return select(
        literal(RECORD_FORMAT_VERSION).label("record_format_version"),
        runs.c.tenant_id,
        runs.c.run_id,
        runs.c.task_id,
        runs.c.agent_id,
        runs.c.agent_version_id,
        runs.c.agent_build_digest,
        runs.c.run_status,
        runs.c.run_version,
        runs.c.next_event_sequence,
        runs.c.created_at,
        runs.c.updated_at,
        runs.c.last_observed_at,
        runs.c.snapshot_format_version,
        cast(runs.c.snapshot, Text).label("snapshot"),
    )


def _event_select() -> Any:
    return select(
        run_events.c.record_format_version,
        run_events.c.event_id,
        run_events.c.tenant_id,
        run_events.c.run_id,
        run_events.c.sequence_value,
        run_events.c.event_type,
        run_events.c.occurred_at,
        run_events.c.payload_version,
        cast(run_events.c.payload, Text).label("payload"),
    )


def _receipt_select() -> Any:
    return select(
        run_command_receipts.c.record_format_version,
        run_command_receipts.c.tenant_id,
        run_command_receipts.c.command_id,
        run_command_receipts.c.run_id,
        run_command_receipts.c.command_type,
        run_command_receipts.c.intent_fingerprint,
        run_command_receipts.c.resulting_status,
        run_command_receipts.c.resulting_version,
        run_command_receipts.c.event_id,
        run_command_receipts.c.created_at,
    )


_EVENT_PAGE_SELECT = text(
    """
    WITH page AS (
        SELECT
            record_format_version,
            event_id,
            tenant_id,
            run_id,
            sequence_value,
            sequence_digits,
            event_type,
            occurred_at,
            payload_version,
            payload::text AS payload
        FROM run_events
        WHERE tenant_id = :tenant_id
          AND run_id = :run_id
          AND (sequence_digits, sequence_value)
              > (:after_sequence_digits, :after_sequence_value)
        ORDER BY sequence_digits, sequence_value
        LIMIT :limit
    )
    SELECT COALESCE(
        json_agg(
            json_build_object(
                'record_format_version', record_format_version,
                'event_id', event_id,
                'tenant_id', tenant_id,
                'run_id', run_id,
                'sequence_value', sequence_value,
                'event_type', event_type,
                'occurred_at', occurred_at,
                'payload_version', payload_version,
                'payload', payload
            )
            ORDER BY sequence_digits, sequence_value
        ),
        '[]'::json
    )
    FROM page
    """
)


def _record(row: Any) -> Mapping[str, object]:
    return type_cast(Mapping[str, object], row._mapping)


def _encode_commit_candidate(
    updated_run: Run,
    receipt: CommandReceipt,
    new_events: tuple[RunEvent, ...],
) -> _EncodedCommitCandidate:
    """Encode all potentially expensive values before any storage access or lock."""

    return _EncodedCommitCandidate(
        run=encode_run(updated_run),
        receipt=encode_receipt(receipt),
        events=tuple(encode_event(event) for event in new_events),
    )


def _validate_replay_event(receipt: CommandReceipt, event: RunEvent) -> None:
    expected_type = _COMMAND_EVENT_TYPES.get(receipt.command_type)
    if (
        expected_type is None
        or event.type is not expected_type
        or event.sequence != receipt.resulting_version
        or event.payload.get("status") != receipt.resulting_status.value
        or event.payload.get("run_version") != receipt.resulting_version
    ):
        raise RunRepositoryError(RunRepositoryErrorCode.DATA_CORRUPTION)


class PostgreSQLRunRepository(RunRepository):
    """Persist one Run, zero/one Event, and one receipt in a short transaction."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        lock_timeout_ms: int = 5_000,
        statement_timeout_ms: int = 5_000,
    ) -> None:
        if type(lock_timeout_ms) is not int or lock_timeout_ms < 1:
            raise ValueError("lock_timeout_ms must be positive")
        if type(statement_timeout_ms) is not int or statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be positive")
        self._engine = engine
        self._lock_timeout_ms = lock_timeout_ms
        self._statement_timeout_ms = statement_timeout_ms

    async def load(self, tenant_id: TenantId, run_id: RunId) -> Run | None:
        _validate_identity(tenant_id, run_id)
        await ensure_schema_compatible(self._engine)
        try:
            async with self._engine.connect() as connection:
                row = (
                    await connection.execute(
                        _run_select().where(
                            runs.c.tenant_id == str(tenant_id),
                            runs.c.run_id == str(run_id),
                        )
                    )
                ).first()
            return None if row is None else decode_run(_record(row))
        except RunRepositoryError:
            raise
        except (DBAPIError, SQLAlchemyError) as error:
            _log_storage_failure(
                RunRepositoryErrorCode.STORAGE_UNAVAILABLE,
                phase=TransactionPhase.ACQUIRE,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            raise RunRepositoryError(RunRepositoryErrorCode.STORAGE_UNAVAILABLE) from error

    async def list_events(
        self,
        tenant_id: TenantId,
        run_id: RunId,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[RunEvent, ...]:
        _validate_identity(tenant_id, run_id)
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        await ensure_schema_compatible(self._engine)
        cursor = encode_canonical_integer(after_sequence)
        try:
            async with self._engine.connect() as connection:
                page = await connection.scalar(
                    _EVENT_PAGE_SELECT,
                    {
                        "tenant_id": str(tenant_id),
                        "run_id": str(run_id),
                        "after_sequence_digits": len(cursor),
                        "after_sequence_value": cursor,
                        "limit": limit,
                    },
                )
                if not isinstance(page, list) or not all(
                    isinstance(record, Mapping) for record in page
                ):
                    raise RunRepositoryError(RunRepositoryErrorCode.DATA_CORRUPTION)
                if not page:
                    exists = await connection.scalar(
                        select(literal(True)).where(
                            select(runs.c.run_id)
                            .where(
                                runs.c.tenant_id == str(tenant_id),
                                runs.c.run_id == str(run_id),
                            )
                            .exists()
                        )
                    )
                    if not exists:
                        raise RunLifecycleError(RunErrorCode.NOT_FOUND)
            return tuple(decode_event(record) for record in page)
        except (RunLifecycleError, RunRepositoryError):
            raise
        except (DBAPIError, SQLAlchemyError) as error:
            _log_storage_failure(
                RunRepositoryErrorCode.STORAGE_UNAVAILABLE,
                phase=TransactionPhase.ACQUIRE,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            raise RunRepositoryError(RunRepositoryErrorCode.STORAGE_UNAVAILABLE) from error

    async def find_command(
        self,
        tenant_id: TenantId,
        command_id: CommandId,
        intent_fingerprint: str,
    ) -> CommitOutcome | None:
        _validate_identity(tenant_id)
        if not isinstance(command_id, CommandId):
            raise TypeError("command_id must be CommandId")
        if (
            type(intent_fingerprint) is not str
            or _FINGERPRINT_PATTERN.fullmatch(intent_fingerprint) is None
        ):
            raise ValueError("intent_fingerprint must be a SHA-256 digest")
        await ensure_schema_compatible(self._engine)
        try:
            async with self._engine.connect() as connection:
                return await self._find_command(
                    connection, tenant_id, command_id, intent_fingerprint
                )
        except (RunLifecycleError, RunRepositoryError):
            raise
        except (DBAPIError, SQLAlchemyError) as error:
            _log_storage_failure(
                RunRepositoryErrorCode.STORAGE_UNAVAILABLE,
                phase=TransactionPhase.ACQUIRE,
                tenant_id=tenant_id,
                run_id=None,
            )
            raise RunRepositoryError(RunRepositoryErrorCode.STORAGE_UNAVAILABLE) from error

    async def _find_command(
        self,
        connection: AsyncConnection,
        tenant_id: TenantId,
        command_id: CommandId,
        intent_fingerprint: str,
    ) -> CommitOutcome | None:
        row = (
            await connection.execute(
                _receipt_select().where(
                    run_command_receipts.c.tenant_id == str(tenant_id),
                    run_command_receipts.c.command_id == str(command_id),
                )
            )
        ).first()
        if row is None:
            return None
        receipt = decode_receipt(_record(row))
        if receipt.intent_fingerprint != intent_fingerprint:
            raise RunLifecycleError(RunErrorCode.IDEMPOTENCY_CONFLICT)
        events: tuple[RunEvent, ...] = ()
        if receipt.event_ids:
            event_row = (
                await connection.execute(
                    _event_select().where(
                        run_events.c.event_id == str(receipt.event_ids[0]),
                        run_events.c.tenant_id == str(receipt.tenant_id),
                        run_events.c.run_id == str(receipt.run_id),
                    )
                )
            ).first()
            if event_row is None:
                raise RunRepositoryError(RunRepositoryErrorCode.DATA_CORRUPTION)
            event = decode_event(_record(event_row))
            _validate_replay_event(receipt, event)
            events = (event,)
        return CommitOutcome(receipt=receipt, events=events, replayed=True)

    async def commit(
        self,
        *,
        expected_version: int,
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> CommitOutcome:
        self._validate_commit_input(expected_version, updated_run, new_events, receipt)
        encoded = _encode_commit_candidate(updated_run, receipt, new_events)
        await ensure_schema_compatible(self._engine)
        connection: AsyncConnection | None = None
        transaction: AsyncTransaction | None = None
        phase = TransactionPhase.ACQUIRE
        try:
            connection = await self._engine.connect()
            phase = TransactionPhase.BEGIN
            transaction = await connection.begin()
            await connection.execute(
                text(
                    "SELECT "
                    "set_config('synchronous_commit', 'on', true), "
                    "set_config('lock_timeout', :lock_timeout, true), "
                    "set_config('statement_timeout', :statement_timeout, true)"
                ),
                {
                    "lock_timeout": f"{self._lock_timeout_ms}ms",
                    "statement_timeout": f"{self._statement_timeout_ms}ms",
                },
            )
            phase = TransactionPhase.ARBITRATION
            inserted = await self._insert_receipt(connection, encoded.receipt)
            await self._transaction_boundary("after_receipt", connection)
            if not inserted:
                replay = await self._find_command(
                    connection,
                    receipt.tenant_id,
                    receipt.command_id,
                    receipt.intent_fingerprint,
                )
                if replay is None:
                    raise RunRepositoryError(RunRepositoryErrorCode.DATA_CORRUPTION)
                await transaction.commit()
                return replay

            current = None
            is_structural_create = (
                expected_version == 0 and updated_run.version == 1 and len(new_events) == 1
            )
            if not is_structural_create:
                phase = TransactionPhase.LOCK
                current_row = (
                    await connection.execute(
                        _run_select()
                        .where(
                            runs.c.tenant_id == str(updated_run.tenant_id),
                            runs.c.run_id == str(updated_run.run_id),
                        )
                        .with_for_update()
                    )
                ).first()
                current = None if current_row is None else decode_run(_record(current_row))
            current_version = 0 if current is None else current.version
            if current_version != expected_version:
                raise RunLifecycleError(RunErrorCode.VERSION_CONFLICT)

            validate_commit_candidate(
                expected_version=expected_version,
                current=current,
                updated_run=updated_run,
                new_events=new_events,
                receipt=receipt,
                # PostgreSQL's immediate global event PK is the atomic occupancy
                # arbiter. Integrity mapping preserves the same public invariant error.
                occupied_event_ids=frozenset(),
            )

            phase = TransactionPhase.WRITE
            if current is None:
                await self._insert_run(connection, encoded.run)
            elif new_events:
                await self._update_run(connection, updated_run, encoded.run)
            await self._transaction_boundary("after_run", connection)
            for event_record in encoded.events:
                await self._insert_event(connection, event_record)
                await self._transaction_boundary("after_event", connection)
            await self._transaction_boundary("before_commit", connection)
            phase = TransactionPhase.COMMIT
            await self._commit_transaction(transaction)
            phase = TransactionPhase.COMPLETE
            return CommitOutcome(receipt=receipt, events=new_events, replayed=False)
        except (RunLifecycleError, RunRepositoryError):
            await _rollback_if_active(transaction)
            raise
        except IntegrityError as error:
            await _rollback_if_active(transaction)
            domain_code = (
                RunErrorCode.VERSION_CONFLICT
                if _constraint_name(error) == "pk_runs"
                else RunErrorCode.INVARIANT_VIOLATION
            )
            raise RunLifecycleError(domain_code) from error
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            rollback_confirmed = await _rollback_if_active(transaction)
            if connection is not None and (
                bool(getattr(error, "connection_invalidated", False))
                or phase is TransactionPhase.COMMIT
            ):
                with suppress(Exception):
                    await connection.invalidate(error)
            storage_code = classify_storage_failure(
                error,
                phase=phase,
                rollback_confirmed=rollback_confirmed,
            )
            _log_storage_failure(
                storage_code,
                phase=phase,
                tenant_id=receipt.tenant_id,
                run_id=receipt.run_id,
            )
            raise RunRepositoryError(storage_code) from error
        finally:
            if connection is not None:
                with suppress(Exception):
                    await connection.close()

    async def _transaction_boundary(self, name: str, connection: AsyncConnection) -> None:
        """Internal deterministic fault-test seam; production implementation is a no-op."""

    async def _commit_transaction(self, transaction: AsyncTransaction) -> None:
        await transaction.commit()

    @staticmethod
    def _validate_commit_input(
        expected_version: int,
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> None:
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer")
        if not isinstance(updated_run, Run):
            raise TypeError("updated_run must be Run")
        if not isinstance(new_events, tuple) or not all(
            isinstance(event, RunEvent) for event in new_events
        ):
            raise TypeError("new_events must be a tuple of RunEvent")
        if not isinstance(receipt, CommandReceipt):
            raise TypeError("receipt must be CommandReceipt")
        if len(new_events) > 1 or len(receipt.event_ids) > 1:
            raise RunLifecycleError(RunErrorCode.INVARIANT_VIOLATION)

    @staticmethod
    async def _insert_receipt(
        connection: AsyncConnection,
        encoded_receipt: Mapping[str, object],
    ) -> bool:
        row = (
            await connection.execute(
                postgresql_insert(run_command_receipts)
                .values(**encoded_receipt)
                .on_conflict_do_nothing(
                    index_elements=[
                        run_command_receipts.c.tenant_id,
                        run_command_receipts.c.command_id,
                    ]
                )
                .returning(run_command_receipts.c.command_id)
            )
        ).first()
        return row is not None

    @staticmethod
    async def _insert_run(
        connection: AsyncConnection,
        encoded_run: Mapping[str, object],
    ) -> None:
        record = dict(encoded_run)
        record.pop("record_format_version")
        snapshot = record.pop("snapshot")
        statement = insert(runs).values(
            **record,
            snapshot=cast(bindparam("snapshot_json"), JSON),
        )
        await connection.execute(statement, {"snapshot_json": snapshot})

    @staticmethod
    async def _update_run(
        connection: AsyncConnection,
        run: Run,
        encoded_run: Mapping[str, object],
    ) -> None:
        record = dict(encoded_run)
        record.pop("record_format_version")
        snapshot = record.pop("snapshot")
        record.pop("tenant_id")
        record.pop("run_id")
        statement = (
            update(runs)
            .where(runs.c.tenant_id == str(run.tenant_id), runs.c.run_id == str(run.run_id))
            .values(**record, snapshot=cast(bindparam("snapshot_json"), JSON))
        )
        result = await connection.execute(statement, {"snapshot_json": snapshot})
        if result.rowcount != 1:
            raise RunRepositoryError(RunRepositoryErrorCode.DATA_CORRUPTION)

    @staticmethod
    async def _insert_event(
        connection: AsyncConnection,
        encoded_event: Mapping[str, object],
    ) -> None:
        record = dict(encoded_event)
        payload = record.pop("payload")
        statement = insert(run_events).values(
            **record,
            payload=cast(bindparam("payload_json"), JSON),
        )
        await connection.execute(statement, {"payload_json": payload})
