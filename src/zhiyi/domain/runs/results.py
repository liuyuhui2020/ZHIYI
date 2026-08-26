"""Safe immutable terminal result contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from zhiyi.domain.runs.budget import BudgetDimension, BudgetSnapshot
from zhiyi.domain.runs.errors import RunErrorCode, safe_error_message
from zhiyi.domain.runs.events import RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentVersionRef,
    CorrelationId,
    ReferenceId,
    RunId,
    TenantId,
)

_WARNING_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _normalize_references(name: str, values: tuple[ReferenceId, ...]) -> tuple[ReferenceId, ...]:
    if not isinstance(values, tuple) or not all(isinstance(value, ReferenceId) for value in values):
        raise TypeError(f"{name} must be a tuple of ReferenceId")
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class SafeRunError:
    code: RunErrorCode
    correlation_id: CorrelationId | None = None
    limit_dimension: BudgetDimension | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, RunErrorCode):
            raise TypeError("code must be RunErrorCode")
        if self.correlation_id is not None and not isinstance(self.correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")
        if self.limit_dimension is not None and not isinstance(
            self.limit_dimension, BudgetDimension
        ):
            raise TypeError("limit_dimension must be BudgetDimension")
        if self.code is RunErrorCode.BUDGET_LIMIT and self.limit_dimension is None:
            raise ValueError("budget-limit errors require a limit dimension")
        if self.code is not RunErrorCode.BUDGET_LIMIT and self.limit_dimension is not None:
            raise ValueError("limit dimension is only valid for budget-limit errors")

    @property
    def message(self) -> str:
        return safe_error_message(self.code)


@dataclass(frozen=True, slots=True)
class RunResultDraft:
    answer: str | None = field(default=None, repr=False)
    warning_codes: tuple[str, ...] = ()
    citation_ids: tuple[ReferenceId, ...] = ()
    artifact_ids: tuple[ReferenceId, ...] = ()
    approval_ids: tuple[ReferenceId, ...] = ()
    correlation_id: CorrelationId | None = None

    def __post_init__(self) -> None:
        if self.answer is not None and type(self.answer) is not str:
            raise TypeError("answer must be a string or None")
        if not isinstance(self.warning_codes, tuple) or not all(
            type(code) is str and _WARNING_PATTERN.fullmatch(code) is not None
            for code in self.warning_codes
        ):
            raise ValueError("warning_codes must contain safe identifiers")
        object.__setattr__(self, "warning_codes", tuple(dict.fromkeys(self.warning_codes)))
        for name in ("citation_ids", "artifact_ids", "approval_ids"):
            object.__setattr__(self, name, _normalize_references(name, getattr(self, name)))
        if self.correlation_id is not None and not isinstance(self.correlation_id, CorrelationId):
            raise TypeError("correlation_id must be CorrelationId")


@dataclass(frozen=True, slots=True)
class RunResult:
    result_version: int
    tenant_id: TenantId
    run_id: RunId
    agent_version: AgentVersionRef
    status: RunStatus
    draft: RunResultDraft
    usage: BudgetSnapshot
    error: SafeRunError | None

    def __post_init__(self) -> None:
        if self.result_version != 1:
            raise ValueError("result_version must be 1")
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.run_id, RunId):
            raise TypeError("run_id must be RunId")
        if not isinstance(self.agent_version, AgentVersionRef):
            raise TypeError("agent_version must be AgentVersionRef")
        if self.agent_version.tenant_id != self.tenant_id:
            raise ValueError("agent version tenant must match result tenant")
        if not isinstance(self.status, RunStatus) or not self.status.is_terminal:
            raise ValueError("result status must be terminal")
        if not isinstance(self.draft, RunResultDraft):
            raise TypeError("draft must be RunResultDraft")
        if not isinstance(self.usage, BudgetSnapshot):
            raise TypeError("usage must be BudgetSnapshot")
        if self.status is RunStatus.SUCCEEDED:
            if self.error is not None:
                raise ValueError("successful results cannot contain an error")
        elif not isinstance(self.error, SafeRunError):
            raise ValueError("non-success results require a safe error")
        else:
            expected_code = {
                RunStatus.FAILED: RunErrorCode.FAILED,
                RunStatus.CANCELLED: RunErrorCode.CANCELLED,
                RunStatus.LIMIT_EXCEEDED: RunErrorCode.BUDGET_LIMIT,
            }[self.status]
            if self.error.code is not expected_code:
                raise ValueError("result error code does not match terminal status")

    @property
    def answer(self) -> str | None:
        return self.draft.answer

    @property
    def correlation_id(self) -> CorrelationId | None:
        return self.draft.correlation_id
