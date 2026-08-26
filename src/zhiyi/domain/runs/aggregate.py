"""Immutable Run aggregate and its single lifecycle transition model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from zhiyi.domain.runs.budget import (
    BudgetCharge,
    BudgetDimension,
    BudgetSnapshot,
    RunBudget,
    canonical_decimal,
)
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import FrozenJsonValue, RunEvent, RunEventType, RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentVersionRef,
    CorrelationId,
    EventId,
    ReferenceId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.domain.runs.results import RunResult, RunResultDraft, SafeRunError

_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.LIMIT_EXCEEDED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_APPROVAL,
            RunStatus.WAITING_RESOLUTION,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.LIMIT_EXCEEDED,
        }
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.LIMIT_EXCEEDED}
    ),
    RunStatus.WAITING_RESOLUTION: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.LIMIT_EXCEEDED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.LIMIT_EXCEEDED: frozenset(),
}


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True)
class RunMutation:
    run: Run
    events: tuple[RunEvent, ...]


@dataclass(frozen=True, slots=True)
class Run:
    tenant_id: TenantId
    run_id: RunId
    task_id: TaskId
    agent_version: AgentVersionRef
    status: RunStatus
    version: int
    budget: RunBudget
    usage: BudgetSnapshot
    created_at: datetime
    updated_at: datetime
    last_observed_at: datetime
    next_event_sequence: int
    result: RunResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.run_id, RunId):
            raise TypeError("run_id must be RunId")
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be TaskId")
        if not isinstance(self.agent_version, AgentVersionRef):
            raise TypeError("agent_version must be AgentVersionRef")
        if self.agent_version.tenant_id != self.tenant_id:
            raise ValueError("agent version tenant must match run tenant")
        if not isinstance(self.status, RunStatus):
            raise TypeError("status must be RunStatus")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.budget, RunBudget):
            raise TypeError("budget must be RunBudget")
        if not isinstance(self.usage, BudgetSnapshot):
            raise TypeError("usage must be BudgetSnapshot")
        for name in ("created_at", "updated_at", "last_observed_at"):
            _require_utc(name, getattr(self, name))
        if self.updated_at < self.created_at or self.last_observed_at < self.updated_at:
            raise ValueError("run timestamps must move forward")
        if self.budget.deadline_at <= self.created_at:
            raise ValueError("budget deadline must be after run creation")
        if self.next_event_sequence != self.version + 1:
            raise ValueError("event sequence and run version must remain continuous")
        if self.status.is_terminal:
            if self.result is None or self.result.status is not self.status:
                raise ValueError("terminal run must have one matching result")
            if (
                self.result.tenant_id != self.tenant_id
                or self.result.run_id != self.run_id
                or self.result.agent_version != self.agent_version
                or self.result.usage != self.usage
            ):
                raise ValueError("terminal result must match the run snapshot")
        elif self.result is not None:
            raise ValueError("non-terminal run cannot have a result")

    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        run_id: RunId,
        task_id: TaskId,
        agent_version: AgentVersionRef,
        budget: RunBudget,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        _require_utc("observed_at", observed_at)
        run = cls(
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            agent_version=agent_version,
            status=RunStatus.QUEUED,
            version=1,
            budget=budget,
            usage=BudgetSnapshot(),
            created_at=observed_at,
            updated_at=observed_at,
            last_observed_at=observed_at,
            next_event_sequence=2,
        )
        event = RunEvent(
            event_id=event_id,
            tenant_id=tenant_id,
            run_id=run_id,
            sequence=1,
            type=RunEventType.RUN_CREATED,
            occurred_at=observed_at,
            payload_version=1,
            payload={
                "agent_version_id": str(agent_version.version_id),
                "run_version": 1,
                "status": RunStatus.QUEUED.value,
            },
        )
        return RunMutation(run=run, events=(event,))

    def _effective_time(self, observed_at: datetime) -> datetime:
        _require_utc("observed_at", observed_at)
        return max(self.last_observed_at, observed_at)

    def _assert_transition(self, target: RunStatus) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)

    def _change(
        self,
        *,
        target: RunStatus,
        event_type: RunEventType,
        observed_at: datetime,
        event_id: EventId,
        payload: dict[str, FrozenJsonValue] | None = None,
        usage: BudgetSnapshot | None = None,
        result_draft: RunResultDraft | None = None,
        error: SafeRunError | None = None,
        allow_running_self_transition: bool = False,
    ) -> RunMutation:
        if not (allow_running_self_transition and self.status is target is RunStatus.RUNNING):
            self._assert_transition(target)
        effective_at = self._effective_time(observed_at)
        next_version = self.version + 1
        next_usage = usage or self.usage
        result: RunResult | None = None
        if target.is_terminal:
            result = RunResult(
                result_version=1,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                agent_version=self.agent_version,
                status=target,
                draft=result_draft or RunResultDraft(),
                usage=next_usage,
                error=error,
            )
        event_payload: dict[str, FrozenJsonValue] = {
            "run_version": next_version,
            "status": target.value,
        }
        if payload:
            event_payload.update(payload)
        updated = replace(
            self,
            status=target,
            version=next_version,
            usage=next_usage,
            updated_at=effective_at,
            last_observed_at=effective_at,
            next_event_sequence=self.next_event_sequence + 1,
            result=result,
        )
        event = RunEvent(
            event_id=event_id,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            sequence=self.next_event_sequence,
            type=event_type,
            occurred_at=effective_at,
            payload_version=1,
            payload=event_payload,
        )
        return RunMutation(run=updated, events=(event,))

    def start(self, *, observed_at: datetime, event_id: EventId) -> RunMutation:
        return self._change(
            target=RunStatus.RUNNING,
            event_type=RunEventType.RUN_STARTED,
            observed_at=observed_at,
            event_id=event_id,
            payload={"previous_status": self.status.value},
        )

    def wait_for_approval(
        self,
        *,
        reference_id: ReferenceId,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        if self.status is not RunStatus.RUNNING:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)
        return self._change(
            target=RunStatus.WAITING_APPROVAL,
            event_type=RunEventType.RUN_WAITING_APPROVAL,
            observed_at=observed_at,
            event_id=event_id,
            payload={"previous_status": self.status.value, "reference_id": str(reference_id)},
        )

    def wait_for_resolution(
        self,
        *,
        reference_id: ReferenceId,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        if self.status is not RunStatus.RUNNING:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)
        return self._change(
            target=RunStatus.WAITING_RESOLUTION,
            event_type=RunEventType.RUN_WAITING_RESOLUTION,
            observed_at=observed_at,
            event_id=event_id,
            payload={"previous_status": self.status.value, "reference_id": str(reference_id)},
        )

    def resume(self, *, observed_at: datetime, event_id: EventId) -> RunMutation:
        if self.status not in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_RESOLUTION}:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)
        return self._change(
            target=RunStatus.RUNNING,
            event_type=RunEventType.RUN_RESUMED,
            observed_at=observed_at,
            event_id=event_id,
            payload={"previous_status": self.status.value},
        )

    def succeed(
        self,
        *,
        draft: RunResultDraft,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        if self.status is not RunStatus.RUNNING:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)
        return self._change(
            target=RunStatus.SUCCEEDED,
            event_type=RunEventType.RUN_SUCCEEDED,
            observed_at=observed_at,
            event_id=event_id,
            payload={"result_version": 1},
            result_draft=draft,
        )

    def fail(
        self,
        *,
        draft: RunResultDraft,
        error: SafeRunError,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        if self.status is not RunStatus.RUNNING:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)
        if error.code is not RunErrorCode.FAILED:
            raise ValueError("fail transition requires the failed error code")
        return self._change(
            target=RunStatus.FAILED,
            event_type=RunEventType.RUN_FAILED,
            observed_at=observed_at,
            event_id=event_id,
            payload={"error_code": error.code.value, "result_version": 1},
            result_draft=draft,
            error=error,
        )

    def cancel(
        self,
        *,
        observed_at: datetime,
        event_id: EventId,
        correlation_id: CorrelationId | None = None,
    ) -> RunMutation:
        error = SafeRunError(RunErrorCode.CANCELLED, correlation_id=correlation_id)
        payload: dict[str, FrozenJsonValue] = {
            "error_code": error.code.value,
            "result_version": 1,
        }
        if correlation_id is not None:
            payload["correlation_id"] = str(correlation_id)
        return self._change(
            target=RunStatus.CANCELLED,
            event_type=RunEventType.RUN_CANCELLED,
            observed_at=observed_at,
            event_id=event_id,
            payload=payload,
            result_draft=RunResultDraft(correlation_id=correlation_id),
            error=error,
        )

    def _limit(
        self,
        *,
        dimension: BudgetDimension,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        error = SafeRunError(RunErrorCode.BUDGET_LIMIT, limit_dimension=dimension)
        return self._change(
            target=RunStatus.LIMIT_EXCEEDED,
            event_type=RunEventType.RUN_LIMIT_EXCEEDED,
            observed_at=observed_at,
            event_id=event_id,
            payload={
                "error_code": error.code.value,
                "limit_dimension": dimension.value,
                "result_version": 1,
            },
            error=error,
        )

    def consume_budget(
        self,
        *,
        charge: BudgetCharge,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        if self.status is not RunStatus.RUNNING:
            raise RunLifecycleError(RunErrorCode.ILLEGAL_TRANSITION)
        decision = self.usage.apply(charge, self.budget)
        if decision.replayed:
            return RunMutation(run=self, events=())
        effective_at = self._effective_time(observed_at)
        if effective_at >= self.budget.deadline_at:
            return self._limit(
                dimension=BudgetDimension.DEADLINE,
                observed_at=effective_at,
                event_id=event_id,
            )
        if decision.exceeded_dimension is not None:
            return self._limit(
                dimension=decision.exceeded_dimension,
                observed_at=effective_at,
                event_id=event_id,
            )
        usage = decision.snapshot
        return self._change(
            target=RunStatus.RUNNING,
            event_type=RunEventType.RUN_BUDGET_CONSUMED,
            observed_at=effective_at,
            event_id=event_id,
            payload={
                "charge_id": str(charge.charge_id),
                "usage": {
                    "cost": canonical_decimal(usage.cost),
                    "input_tokens": usage.input_tokens,
                    "model_calls": usage.model_calls,
                    "output_tokens": usage.output_tokens,
                    "steps": usage.steps,
                    "tool_calls": usage.tool_calls,
                    "total_tokens": usage.total_tokens,
                },
            },
            usage=usage,
            allow_running_self_transition=True,
        )

    def enforce_deadline(
        self,
        *,
        observed_at: datetime,
        event_id: EventId,
    ) -> RunMutation:
        if self.status.is_terminal:
            return RunMutation(run=self, events=())
        effective_at = self._effective_time(observed_at)
        if effective_at < self.budget.deadline_at:
            return RunMutation(run=self, events=())
        return self._limit(
            dimension=BudgetDimension.DEADLINE,
            observed_at=effective_at,
            event_id=event_id,
        )
