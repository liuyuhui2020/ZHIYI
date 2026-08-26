"""Atomic persistence contract for run snapshots, events, and command receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from zhiyi.domain.runs.events import RunEvent, RunStatus
from zhiyi.domain.runs.identifiers import CommandId, CorrelationId, EventId, RunId, TenantId

if TYPE_CHECKING:
    from zhiyi.domain.runs.aggregate import Run

_COMMAND_TYPES = frozenset(
    {
        "cancel_run",
        "consume_budget",
        "create_run",
        "enforce_deadline",
        "fail_run",
        "resume_run",
        "start_run",
        "succeed_run",
        "wait_for_approval",
        "wait_for_resolution",
    }
)
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class RunRepositoryErrorCode(StrEnum):
    STORAGE_UNAVAILABLE = "storage_unavailable"
    COMMIT_OUTCOME_UNKNOWN = "commit_outcome_unknown"
    DATA_CORRUPTION = "data_corruption"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"


_SAFE_REPOSITORY_MESSAGES: dict[RunRepositoryErrorCode, str] = {
    RunRepositoryErrorCode.STORAGE_UNAVAILABLE: "Run storage is unavailable",
    RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN: "Run storage commit outcome is unknown",
    RunRepositoryErrorCode.DATA_CORRUPTION: "Run storage data is invalid",
    RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE: "Run storage schema is incompatible",
}


def safe_repository_error_message(code: RunRepositoryErrorCode) -> str:
    """Return the constant public message for a stable repository error."""

    if not isinstance(code, RunRepositoryErrorCode):
        raise TypeError("code must be RunRepositoryErrorCode")
    return _SAFE_REPOSITORY_MESSAGES[code]


class RunRepositoryError(Exception):
    """A stable storage-boundary error that never echoes database details."""

    def __init__(
        self,
        code: RunRepositoryErrorCode,
        *,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        if not isinstance(code, RunRepositoryErrorCode):
            raise TypeError("code must be RunRepositoryErrorCode")
        if correlation_id is not None and not isinstance(correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")
        self.code = code
        self.correlation_id = correlation_id
        super().__init__(safe_repository_error_message(code))

    def __str__(self) -> str:
        suffix = (
            f" (correlation_id={self.correlation_id})" if self.correlation_id is not None else ""
        )
        return f"{safe_repository_error_message(self.code)}{suffix}"

    def __repr__(self) -> str:
        correlation_id = (
            repr(str(self.correlation_id)) if self.correlation_id is not None else "None"
        )
        return f"RunRepositoryError(code={self.code.value!r}, correlation_id={correlation_id})"


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    tenant_id: TenantId
    command_id: CommandId
    run_id: RunId
    command_type: str
    intent_fingerprint: str
    resulting_status: RunStatus
    resulting_version: int
    event_ids: tuple[EventId, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.command_id, CommandId):
            raise TypeError("command_id must be CommandId")
        if not isinstance(self.run_id, RunId):
            raise TypeError("run_id must be RunId")
        if self.command_type not in _COMMAND_TYPES:
            raise ValueError("command_type is not supported")
        if (
            type(self.intent_fingerprint) is not str
            or _FINGERPRINT_PATTERN.fullmatch(self.intent_fingerprint) is None
        ):
            raise ValueError("intent_fingerprint must be a SHA-256 digest")
        if not isinstance(self.resulting_status, RunStatus):
            raise TypeError("resulting_status must be RunStatus")
        if type(self.resulting_version) is not int or self.resulting_version < 1:
            raise ValueError("resulting_version must be positive")
        if not isinstance(self.event_ids, tuple) or not all(
            isinstance(event_id, EventId) for event_id in self.event_ids
        ):
            raise TypeError("event_ids must be a tuple of EventId")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("created_at must be an aware UTC datetime")


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    receipt: CommandReceipt
    events: tuple[RunEvent, ...]
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, CommandReceipt):
            raise TypeError("receipt must be CommandReceipt")
        if not isinstance(self.events, tuple) or not all(
            isinstance(event, RunEvent) for event in self.events
        ):
            raise TypeError("events must be a tuple of RunEvent")
        if self.receipt.event_ids != tuple(event.event_id for event in self.events):
            raise ValueError("receipt event ids must match outcome events")
        if type(self.replayed) is not bool:
            raise TypeError("replayed must be bool")


class RunRepository(Protocol):
    async def load(self, tenant_id: TenantId, run_id: RunId) -> Run | None: ...

    async def list_events(
        self,
        tenant_id: TenantId,
        run_id: RunId,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[RunEvent, ...]: ...

    async def find_command(
        self,
        tenant_id: TenantId,
        command_id: CommandId,
        intent_fingerprint: str,
    ) -> CommitOutcome | None: ...

    async def commit(
        self,
        *,
        expected_version: int,
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> CommitOutcome: ...
