"""Shared short-transaction safety primitives for PostgreSQL repositories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncTransaction

from zhiyi.application.ports.worker_lease_observability import (
    LeaseOperationObservation,
    WorkerLeaseTelemetry,
    deliver_terminal_observation,
)
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode

GUARDED_RUN_LOCK_ORDER = ("receipt", "run", "lease", "event")
LEASE_MUTATION_LOCK_ORDER = ("run", "lease")

_KNOWN_ROLLBACK_SQLSTATES = frozenset({"40001", "40P01", "55P03", "57014", "57P01"})
_UNKNOWN_OUTCOME_SQLSTATES = frozenset({"08007", "40003"})


class TransactionPhase(StrEnum):
    ACQUIRE = "acquire"
    BEGIN = "begin"
    ARBITRATION = "arbitration"
    LOCK = "lock"
    WRITE = "write"
    COMMIT = "commit"
    COMPLETE = "complete"


class StorageFailureDisposition(StrEnum):
    UNAVAILABLE = "storage_unavailable"
    UNKNOWN = "commit_outcome_unknown"


@dataclass(frozen=True, slots=True)
class PostgreSQLTransactionSettings:
    lock_timeout_ms: int = 5_000
    statement_timeout_ms: int = 5_000
    isolation_level: str = "READ COMMITTED"
    synchronous_commit: bool = True

    def __post_init__(self) -> None:
        if type(self.lock_timeout_ms) is not int or not 1 <= self.lock_timeout_ms <= 5_000:
            raise ValueError("lock_timeout_ms must be an integer between 1 and 5000")
        if (
            type(self.statement_timeout_ms) is not int
            or not 1 <= self.statement_timeout_ms <= 10_000
            or self.statement_timeout_ms < self.lock_timeout_ms
        ):
            raise ValueError(
                "statement_timeout_ms must be an integer between 1 and 10000 "
                "and no smaller than lock_timeout_ms"
            )
        if self.isolation_level != "READ COMMITTED":
            raise ValueError("isolation_level must be READ COMMITTED")
        if self.synchronous_commit is not True:
            raise ValueError("synchronous_commit must be enabled")


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
) -> StorageFailureDisposition:
    """Classify by stable phase and SQLSTATE without inspecting driver messages."""

    state = _sqlstate(error)
    if rollback_confirmed or state in _KNOWN_ROLLBACK_SQLSTATES:
        return StorageFailureDisposition.UNAVAILABLE
    if phase is TransactionPhase.COMMIT and (
        state in _UNKNOWN_OUTCOME_SQLSTATES
        or bool(getattr(error, "connection_invalidated", False))
        or state is None
    ):
        return StorageFailureDisposition.UNKNOWN
    return StorageFailureDisposition.UNAVAILABLE


def worker_lease_storage_error(
    disposition: StorageFailureDisposition,
) -> WorkerLeaseError:
    if not isinstance(disposition, StorageFailureDisposition):
        raise TypeError("disposition must be StorageFailureDisposition")
    code = (
        WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
        if disposition is StorageFailureDisposition.UNKNOWN
        else WorkerLeaseErrorCode.STORAGE_UNAVAILABLE
    )
    return WorkerLeaseError(code)


async def rollback_if_active(transaction: AsyncTransaction | None) -> bool:
    if transaction is None or not transaction.is_active:
        return False
    try:
        await transaction.rollback()
    except Exception:
        return False
    return True


async def apply_transaction_settings(
    connection: AsyncConnection,
    settings: PostgreSQLTransactionSettings,
) -> None:
    if not isinstance(settings, PostgreSQLTransactionSettings):
        raise TypeError("settings must be PostgreSQLTransactionSettings")
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await connection.execute(
        text(
            "SELECT "
            "set_config('synchronous_commit', 'on', true), "
            "set_config('lock_timeout', :lock_timeout, true), "
            "set_config('statement_timeout', :statement_timeout, true)"
        ),
        {
            "lock_timeout": f"{settings.lock_timeout_ms}ms",
            "statement_timeout": f"{settings.statement_timeout_ms}ms",
        },
    )


async def execute_once[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Name the no-auto-retry contract at transaction boundaries."""

    return await operation()


async def receipt_first[T](
    find_receipt: Callable[[], Awaitable[T | None]],
    perform_new_write: Callable[[], Awaitable[T]],
) -> T:
    replay = await find_receipt()
    if replay is not None:
        return replay
    return await perform_new_write()


@dataclass(frozen=True, slots=True)
class ReceiptArbitrationResult:
    inserted: bool
    replay: object | None


async def arbitrate_complete_receipt(
    insert_receipt: Callable[[], Awaitable[bool]],
    load_replay: Callable[[], Awaitable[object | None]],
) -> ReceiptArbitrationResult:
    """Insert one complete receipt or load the winner on the same connection."""

    inserted = await insert_receipt()
    if inserted:
        return ReceiptArbitrationResult(inserted=True, replay=None)
    return ReceiptArbitrationResult(inserted=False, replay=await load_replay())


class DatabaseCleanupState(Protocol):
    transaction_active: bool
    connection_open: bool


def deliver_after_database_cleanup(
    telemetry: WorkerLeaseTelemetry,
    observation: LeaseOperationObservation,
    *,
    state: DatabaseCleanupState,
) -> None:
    if state.transaction_active or state.connection_open:
        raise RuntimeError("terminal telemetry requires database cleanup")
    deliver_terminal_observation(telemetry, observation)
