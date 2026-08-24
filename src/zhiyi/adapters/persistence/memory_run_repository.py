"""Atomic in-memory RunRepository implementation for local execution and tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeAlias

from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    CommitOutcome,
    RunRepository,
)
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEvent
from zhiyi.domain.runs.identifiers import CommandId, EventId, RunId, TenantId

RunKey: TypeAlias = tuple[TenantId, RunId]  # noqa: UP040
CommandKey: TypeAlias = tuple[TenantId, CommandId]  # noqa: UP040


class MemoryRunRepository(RunRepository):
    """Serialize commits under one lock and publish state with copy-validate-swap."""

    def __init__(self, *, before_swap: Callable[[], None] | None = None) -> None:
        self._runs: dict[RunKey, Run] = {}
        self._events: dict[RunKey, tuple[RunEvent, ...]] = {}
        self._commands: dict[CommandKey, CommitOutcome] = {}
        self._event_ids: frozenset[EventId] = frozenset()
        self._lock = asyncio.Lock()
        self._before_swap = before_swap

    async def load(self, tenant_id: TenantId, run_id: RunId) -> Run | None:
        return self._runs.get((tenant_id, run_id))

    async def list_events(
        self,
        tenant_id: TenantId,
        run_id: RunId,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[RunEvent, ...]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        key = (tenant_id, run_id)
        events = self._events.get(key)
        if events is None:
            raise RunLifecycleError(RunErrorCode.NOT_FOUND)
        return tuple(event for event in events if event.sequence > after_sequence)[:limit]

    async def find_command(
        self,
        tenant_id: TenantId,
        command_id: CommandId,
        intent_fingerprint: str,
    ) -> CommitOutcome | None:
        existing = self._commands.get((tenant_id, command_id))
        if existing is None:
            return None
        if existing.receipt.intent_fingerprint != intent_fingerprint:
            raise RunLifecycleError(RunErrorCode.IDEMPOTENCY_CONFLICT)
        return CommitOutcome(
            receipt=existing.receipt,
            events=existing.events,
            replayed=True,
        )

    async def commit(
        self,
        *,
        expected_version: int,
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> CommitOutcome:
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

        async with self._lock:
            command_key = (receipt.tenant_id, receipt.command_id)
            existing_command = self._commands.get(command_key)
            if existing_command is not None:
                if existing_command.receipt.intent_fingerprint != receipt.intent_fingerprint:
                    raise RunLifecycleError(RunErrorCode.IDEMPOTENCY_CONFLICT)
                return CommitOutcome(
                    receipt=existing_command.receipt,
                    events=existing_command.events,
                    replayed=True,
                )

            run_key = (updated_run.tenant_id, updated_run.run_id)
            current = self._runs.get(run_key)
            current_events = self._events.get(run_key, ())
            current_version = current.version if current is not None else 0
            if expected_version != current_version:
                raise RunLifecycleError(RunErrorCode.VERSION_CONFLICT)

            self._validate_commit(
                expected_version=expected_version,
                current=current,
                current_events=current_events,
                updated_run=updated_run,
                new_events=new_events,
                receipt=receipt,
            )

            next_runs = dict(self._runs)
            next_events = dict(self._events)
            next_commands = dict(self._commands)
            next_event_ids = self._event_ids | frozenset(event.event_id for event in new_events)
            outcome = CommitOutcome(receipt=receipt, events=new_events, replayed=False)
            next_runs[run_key] = updated_run
            next_events[run_key] = current_events + new_events
            next_commands[command_key] = outcome

            if self._before_swap is not None:
                self._before_swap()
            self._runs = next_runs
            self._events = next_events
            self._commands = next_commands
            self._event_ids = next_event_ids
            return outcome

    def _validate_commit(
        self,
        *,
        expected_version: int,
        current: Run | None,
        current_events: tuple[RunEvent, ...],
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> None:
        invalid = RunLifecycleError(RunErrorCode.INVARIANT_VIOLATION)
        if len(new_events) > 1:
            raise invalid
        if (
            receipt.tenant_id != updated_run.tenant_id
            or receipt.run_id != updated_run.run_id
            or receipt.resulting_status is not updated_run.status
            or receipt.resulting_version != updated_run.version
            or receipt.event_ids != tuple(event.event_id for event in new_events)
        ):
            raise invalid
        if expected_version == 0:
            if current is not None or updated_run.version != 1 or len(new_events) != 1:
                raise invalid
        elif current is None:
            raise invalid
        if not new_events:
            if current is None or updated_run != current:
                raise invalid
        elif updated_run.version != expected_version + len(new_events):
            raise invalid

        expected_sequence = len(current_events) + 1
        seen_new_ids: set[EventId] = set()
        for event in new_events:
            if (
                event.tenant_id != updated_run.tenant_id
                or event.run_id != updated_run.run_id
                or event.sequence != expected_sequence
                or event.event_id in self._event_ids
                or event.event_id in seen_new_ids
            ):
                raise invalid
            seen_new_ids.add(event.event_id)
            expected_sequence += 1
        if updated_run.next_event_sequence != len(current_events) + len(new_events) + 1:
            raise invalid
