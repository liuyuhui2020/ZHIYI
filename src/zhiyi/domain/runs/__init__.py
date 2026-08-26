"""Stable, framework-free run lifecycle domain contracts."""

from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import (
    BudgetCharge,
    BudgetDecision,
    BudgetDimension,
    BudgetSnapshot,
    RunBudget,
)
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.events import RunEvent, RunEventType, RunStatus, thaw_event_payload
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

__all__ = [
    "AgentId",
    "AgentVersionId",
    "AgentVersionRef",
    "BudgetCharge",
    "BudgetDecision",
    "BudgetDimension",
    "BudgetSnapshot",
    "ChargeId",
    "CommandId",
    "CorrelationId",
    "EventId",
    "ReferenceId",
    "Run",
    "RunBudget",
    "RunErrorCode",
    "RunEvent",
    "RunEventType",
    "RunId",
    "RunLifecycleError",
    "RunMutation",
    "RunResult",
    "RunResultDraft",
    "RunStatus",
    "SafeRunError",
    "TaskId",
    "TenantId",
    "thaw_event_payload",
]
