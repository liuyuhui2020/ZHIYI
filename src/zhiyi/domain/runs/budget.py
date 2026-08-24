"""Immutable hard-budget values and exact usage arithmetic."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.identifiers import ChargeId

_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}\Z")


def _validate_counter(name: str, value: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _normalize_decimal(name: str, value: Decimal) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return Decimal(0) if value.is_zero() else value


def canonical_decimal(value: Decimal) -> str:
    """Return an exact, exponent-free representation for hashing and adapters."""

    normalized = _normalize_decimal("decimal", value)
    if normalized.is_zero():
        return "0"
    _, raw_digits, raw_exponent = normalized.as_tuple()
    digits = list(raw_digits)
    exponent = cast(int, raw_exponent)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        return coefficient + "0" * exponent
    decimal_at = len(coefficient) + exponent
    if decimal_at > 0:
        return f"{coefficient[:decimal_at]}.{coefficient[decimal_at:]}"
    return f"0.{('0' * -decimal_at)}{coefficient}"


def _exact_decimal_add(left: Decimal, right: Decimal) -> Decimal:
    left_tuple = left.as_tuple()
    right_tuple = right.as_tuple()
    left_exponent = cast(int, left_tuple.exponent)
    right_exponent = cast(int, right_tuple.exponent)
    exponent = min(left_exponent, right_exponent)
    left_coefficient = int("".join(str(digit) for digit in left_tuple.digits))
    right_coefficient = int("".join(str(digit) for digit in right_tuple.digits))
    left_coefficient *= 10 ** (left_exponent - exponent)
    right_coefficient *= 10 ** (right_exponent - exponent)
    total = left_coefficient + right_coefficient
    return Decimal((0, tuple(int(digit) for digit in str(total)), exponent))


@dataclass(frozen=True, slots=True)
class RunBudget:
    deadline_at: datetime
    max_steps: int
    max_model_calls: int
    max_tool_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_cost: Decimal
    currency: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.deadline_at, datetime)
            or self.deadline_at.tzinfo is None
            or self.deadline_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("deadline_at must be an aware UTC datetime")
        for name in (
            "max_steps",
            "max_model_calls",
            "max_tool_calls",
            "max_input_tokens",
            "max_output_tokens",
            "max_total_tokens",
        ):
            _validate_counter(name, getattr(self, name))
        if self.max_total_tokens < max(self.max_input_tokens, self.max_output_tokens):
            raise ValueError("max_total_tokens must cover each token limit")
        object.__setattr__(self, "max_cost", _normalize_decimal("max_cost", self.max_cost))
        if type(self.currency) is not str or _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be three uppercase ASCII letters")


class BudgetDimension(StrEnum):
    DEADLINE = "deadline"
    STEPS = "steps"
    MODEL_CALLS = "model_calls"
    TOOL_CALLS = "tool_calls"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    TOTAL_TOKENS = "total_tokens"
    COST = "cost"


@dataclass(frozen=True, slots=True)
class BudgetCharge:
    charge_id: ChargeId
    steps: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not isinstance(self.charge_id, ChargeId):
            raise TypeError("charge_id must be ChargeId")
        for name in ("steps", "model_calls", "tool_calls", "input_tokens", "output_tokens"):
            _validate_counter(name, getattr(self, name))
        object.__setattr__(self, "cost", _normalize_decimal("cost", self.cost))
        if not any(
            (
                self.steps,
                self.model_calls,
                self.tool_calls,
                self.input_tokens,
                self.output_tokens,
                self.cost,
            )
        ):
            raise ValueError("a budget charge must consume at least one dimension")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            {
                "charge_id": str(self.charge_id),
                "cost": canonical_decimal(self.cost),
                "input_tokens": self.input_tokens,
                "model_calls": self.model_calls,
                "output_tokens": self.output_tokens,
                "steps": self.steps,
                "tool_calls": self.tool_calls,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return f"sha256:{hashlib.sha256(body).hexdigest()}"


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    snapshot: BudgetSnapshot
    replayed: bool = False
    exceeded_dimension: BudgetDimension | None = None


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    steps: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    _charge_fingerprints: Mapping[ChargeId, str] = field(
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        for name in ("steps", "model_calls", "tool_calls", "input_tokens", "output_tokens"):
            _validate_counter(name, getattr(self, name))
        object.__setattr__(self, "cost", _normalize_decimal("cost", self.cost))
        copied: dict[ChargeId, str] = {}
        for charge_id, fingerprint in self._charge_fingerprints.items():
            if not isinstance(charge_id, ChargeId):
                raise TypeError("charge fingerprint keys must be ChargeId")
            if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
                raise ValueError("charge fingerprint is invalid")
            copied[charge_id] = fingerprint
        object.__setattr__(self, "_charge_fingerprints", MappingProxyType(copied))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def charge_fingerprints(self) -> tuple[tuple[ChargeId, str], ...]:
        return tuple(self._charge_fingerprints.items())

    def apply(self, charge: BudgetCharge, budget: RunBudget) -> BudgetDecision:
        if not isinstance(charge, BudgetCharge):
            raise TypeError("charge must be BudgetCharge")
        if not isinstance(budget, RunBudget):
            raise TypeError("budget must be RunBudget")
        existing = self._charge_fingerprints.get(charge.charge_id)
        if existing is not None:
            if existing != charge.fingerprint:
                raise RunLifecycleError(RunErrorCode.IDEMPOTENCY_CONFLICT)
            return BudgetDecision(snapshot=self, replayed=True)

        candidate = BudgetSnapshot(
            steps=self.steps + charge.steps,
            model_calls=self.model_calls + charge.model_calls,
            tool_calls=self.tool_calls + charge.tool_calls,
            input_tokens=self.input_tokens + charge.input_tokens,
            output_tokens=self.output_tokens + charge.output_tokens,
            cost=_exact_decimal_add(self.cost, charge.cost),
            _charge_fingerprints={
                **self._charge_fingerprints,
                charge.charge_id: charge.fingerprint,
            },
        )
        checks = (
            (candidate.steps, budget.max_steps, BudgetDimension.STEPS),
            (candidate.model_calls, budget.max_model_calls, BudgetDimension.MODEL_CALLS),
            (candidate.tool_calls, budget.max_tool_calls, BudgetDimension.TOOL_CALLS),
            (candidate.input_tokens, budget.max_input_tokens, BudgetDimension.INPUT_TOKENS),
            (candidate.output_tokens, budget.max_output_tokens, BudgetDimension.OUTPUT_TOKENS),
            (candidate.total_tokens, budget.max_total_tokens, BudgetDimension.TOTAL_TOKENS),
            (candidate.cost, budget.max_cost, BudgetDimension.COST),
        )
        for actual, maximum, dimension in checks:
            if actual > maximum:
                return BudgetDecision(snapshot=self, exceeded_dimension=dimension)
        return BudgetDecision(snapshot=candidate)
