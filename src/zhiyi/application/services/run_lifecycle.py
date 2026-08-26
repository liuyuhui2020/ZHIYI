"""Application orchestration for atomic, tenant-scoped Run lifecycle commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from zhiyi.application.commands.run_lifecycle import (
    CancelRunCommand,
    ConsumeBudgetCommand,
    CreateRunCommand,
    EnforceDeadlineCommand,
    FailRunCommand,
    ResumeRunCommand,
    StartRunCommand,
    SucceedRunCommand,
    WaitForApprovalCommand,
    WaitForResolutionCommand,
)
from zhiyi.application.ports.clock import Clock
from zhiyi.application.ports.identifier_generator import IdentifierGenerator
from zhiyi.application.ports.run_repository import (
    CommandReceipt,
    CommitOutcome,
    RunRepository,
)
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEvent
from zhiyi.domain.runs.identifiers import EventId, RunId, TenantId

ExistingCommand: TypeAlias = (  # noqa: UP040
    StartRunCommand
    | WaitForApprovalCommand
    | WaitForResolutionCommand
    | ResumeRunCommand
    | ConsumeBudgetCommand
    | CancelRunCommand
    | SucceedRunCommand
    | FailRunCommand
    | EnforceDeadlineCommand
)
MutationFactory: TypeAlias = Callable[[Run, datetime, EventId], RunMutation]  # noqa: UP040


@dataclass(frozen=True, slots=True)
class DeadlineOutcome:
    run: Run
    command_outcome: CommitOutcome | None


class RunLifecycleService:
    def __init__(
        self,
        *,
        repository: RunRepository,
        clock: Clock,
        identifier_generator: IdentifierGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._identifiers = identifier_generator

    def _event_id(self) -> EventId:
        return EventId(self._identifiers.new_id("event"))

    def _receipt(
        self,
        command: CreateRunCommand | ExistingCommand,
        mutation: RunMutation,
        created_at: datetime,
    ) -> CommandReceipt:
        return CommandReceipt(
            tenant_id=command.tenant_id,
            command_id=command.command_id,
            run_id=mutation.run.run_id,
            command_type=command.command_type,
            intent_fingerprint=command.intent_fingerprint,
            resulting_status=mutation.run.status,
            resulting_version=mutation.run.version,
            event_ids=tuple(event.event_id for event in mutation.events),
            created_at=created_at,
        )

    async def create_run(self, command: CreateRunCommand) -> CommitOutcome:
        replayed = await self._repository.find_command(
            command.tenant_id,
            command.command_id,
            command.intent_fingerprint,
        )
        if replayed is not None:
            return replayed
        observed_at = self._clock.now()
        mutation = Run.create(
            tenant_id=command.tenant_id,
            run_id=RunId(self._identifiers.new_id("run")),
            task_id=command.task_id,
            agent_version=command.agent_version,
            budget=command.budget,
            observed_at=observed_at,
            event_id=self._event_id(),
        )
        return await self._repository.commit(
            expected_version=0,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=self._receipt(command, mutation, observed_at),
        )

    async def get_run(self, tenant_id: TenantId, run_id: RunId) -> Run:
        run = await self._repository.load(tenant_id, run_id)
        if run is None:
            raise RunLifecycleError(RunErrorCode.NOT_FOUND)
        return run

    async def list_events(
        self,
        tenant_id: TenantId,
        run_id: RunId,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[RunEvent, ...]:
        return await self._repository.list_events(
            tenant_id,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def _mutate(
        self,
        command: ExistingCommand,
        mutation_factory: MutationFactory,
    ) -> CommitOutcome:
        replayed = await self._repository.find_command(
            command.tenant_id,
            command.command_id,
            command.intent_fingerprint,
        )
        if replayed is not None:
            return replayed
        current = await self._repository.load(command.tenant_id, command.run_id)
        if current is None:
            raise RunLifecycleError(RunErrorCode.NOT_FOUND)
        if current.version != command.expected_version:
            raise RunLifecycleError(RunErrorCode.VERSION_CONFLICT)
        observed_at = self._clock.now()
        mutation = mutation_factory(current, observed_at, self._event_id())
        return await self._repository.commit(
            expected_version=command.expected_version,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=self._receipt(command, mutation, observed_at),
        )

    async def start_run(self, command: StartRunCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.start(observed_at=now, event_id=event_id),
        )

    async def wait_for_approval(self, command: WaitForApprovalCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.wait_for_approval(
                reference_id=command.reference_id,
                observed_at=now,
                event_id=event_id,
            ),
        )

    async def wait_for_resolution(self, command: WaitForResolutionCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.wait_for_resolution(
                reference_id=command.reference_id,
                observed_at=now,
                event_id=event_id,
            ),
        )

    async def resume_run(self, command: ResumeRunCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.resume(observed_at=now, event_id=event_id),
        )

    async def consume_budget(self, command: ConsumeBudgetCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.consume_budget(
                charge=command.charge,
                observed_at=now,
                event_id=event_id,
            ),
        )

    async def cancel_run(self, command: CancelRunCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.cancel(
                correlation_id=command.correlation_id,
                observed_at=now,
                event_id=event_id,
            ),
        )

    async def succeed_run(self, command: SucceedRunCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.succeed(
                draft=command.result,
                observed_at=now,
                event_id=event_id,
            ),
        )

    async def fail_run(self, command: FailRunCommand) -> CommitOutcome:
        return await self._mutate(
            command,
            lambda run, now, event_id: run.fail(
                draft=command.result,
                error=command.error,
                observed_at=now,
                event_id=event_id,
            ),
        )

    async def enforce_deadline(self, command: EnforceDeadlineCommand) -> DeadlineOutcome:
        replayed = await self._repository.find_command(
            command.tenant_id,
            command.command_id,
            command.intent_fingerprint,
        )
        if replayed is not None:
            return DeadlineOutcome(
                run=await self.get_run(command.tenant_id, replayed.receipt.run_id),
                command_outcome=replayed,
            )
        current = await self._repository.load(command.tenant_id, command.run_id)
        if current is None:
            raise RunLifecycleError(RunErrorCode.NOT_FOUND)
        if current.version != command.expected_version:
            raise RunLifecycleError(RunErrorCode.VERSION_CONFLICT)
        observed_at = self._clock.now()
        mutation = current.enforce_deadline(
            observed_at=observed_at,
            event_id=self._event_id(),
        )
        if not mutation.events:
            return DeadlineOutcome(run=mutation.run, command_outcome=None)
        committed = await self._repository.commit(
            expected_version=command.expected_version,
            updated_run=mutation.run,
            new_events=mutation.events,
            receipt=self._receipt(command, mutation, observed_at),
        )
        return DeadlineOutcome(run=mutation.run, command_outcome=committed)
