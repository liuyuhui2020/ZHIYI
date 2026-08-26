"""Framework-neutral validation shared by RunRepository adapters."""

from __future__ import annotations

from collections.abc import Set

from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEvent
from zhiyi.domain.runs.identifiers import EventId


def _invariant_violation() -> RunLifecycleError:
    return RunLifecycleError(RunErrorCode.INVARIANT_VIOLATION)


def validate_commit_candidate(
    *,
    expected_version: int,
    current: Run | None,
    updated_run: Run,
    new_events: tuple[RunEvent, ...],
    receipt: CommandReceipt,
    occupied_event_ids: Set[EventId] = frozenset(),
) -> None:
    """Validate one already-version-arbitrated atomic commit candidate.

    Adapters must perform public input validation, command replay/conflict handling,
    and expected-version arbitration before calling this helper. Database constraints
    remain defense in depth for writers outside the repository protocol.
    """

    if len(new_events) > 1:
        raise _invariant_violation()
    if (
        receipt.tenant_id != updated_run.tenant_id
        or receipt.run_id != updated_run.run_id
        or receipt.resulting_status is not updated_run.status
        or receipt.resulting_version != updated_run.version
        or receipt.event_ids != tuple(event.event_id for event in new_events)
    ):
        raise _invariant_violation()

    if expected_version == 0:
        if current is not None or updated_run.version != 1 or len(new_events) != 1:
            raise _invariant_violation()
        expected_sequence = 1
    else:
        if current is None:
            raise _invariant_violation()
        expected_sequence = current.next_event_sequence

    if not new_events:
        if current is None or updated_run != current:
            raise _invariant_violation()
    elif updated_run.version != expected_version + 1:
        raise _invariant_violation()

    seen_new_ids: set[EventId] = set()
    for event in new_events:
        if (
            event.tenant_id != updated_run.tenant_id
            or event.run_id != updated_run.run_id
            or event.sequence != expected_sequence
            or event.event_id in occupied_event_ids
            or event.event_id in seen_new_ids
        ):
            raise _invariant_violation()
        seen_new_ids.add(event.event_id)
        expected_sequence += 1

    if updated_run.next_event_sequence != expected_sequence:
        raise _invariant_violation()
