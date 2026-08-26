from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.domain.runs.budget import (
    BudgetCharge,
    BudgetDimension,
    BudgetSnapshot,
    RunBudget,
)
from zhiyi.domain.runs.errors import RunErrorCode, RunLifecycleError
from zhiyi.domain.runs.identifiers import ChargeId

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def make_budget(**overrides: object) -> RunBudget:
    values: dict[str, object] = {
        "deadline_at": NOW + timedelta(minutes=30),
        "max_steps": 5,
        "max_model_calls": 4,
        "max_tool_calls": 3,
        "max_input_tokens": 100,
        "max_output_tokens": 50,
        "max_total_tokens": 120,
        "max_cost": Decimal("1.25"),
        "currency": "USD",
    }
    values.update(overrides)
    return RunBudget(**values)  # type: ignore[arg-type]


def make_charge(charge_id: str = "charge-1", **overrides: object) -> BudgetCharge:
    values: dict[str, object] = {
        "charge_id": ChargeId(charge_id),
        "steps": 1,
        "model_calls": 1,
        "tool_calls": 0,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost": Decimal("0.10"),
    }
    values.update(overrides)
    return BudgetCharge(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_steps", -1),
        ("max_model_calls", -1),
        ("max_tool_calls", -1),
        ("max_input_tokens", -1),
        ("max_output_tokens", -1),
        ("max_total_tokens", -1),
        ("max_cost", Decimal("-0.01")),
        ("max_cost", Decimal("NaN")),
        ("max_cost", 1.0),
        ("currency", "usd"),
    ],
)
def test_budget_rejects_invalid_limits(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        make_budget(**{field: value})


def test_budget_requires_utc_deadline_and_coherent_token_limits() -> None:
    with pytest.raises(ValueError):
        make_budget(deadline_at=datetime(2026, 8, 24, 8, 30))
    with pytest.raises(ValueError):
        make_budget(max_total_tokens=49)


def test_budget_allows_zero_limits_and_normalizes_negative_zero() -> None:
    budget = make_budget(
        max_steps=0,
        max_model_calls=0,
        max_tool_calls=0,
        max_input_tokens=0,
        max_output_tokens=0,
        max_total_tokens=0,
        max_cost=Decimal("-0"),
    )

    assert budget.max_cost == Decimal(0)
    assert str(budget.max_cost) == "0"


def test_charge_requires_exact_non_negative_decimal_and_some_consumption() -> None:
    with pytest.raises(TypeError):
        make_charge(cost=0.1)
    with pytest.raises(ValueError):
        make_charge(cost=Decimal("Infinity"))
    with pytest.raises(ValueError):
        make_charge(
            steps=0,
            model_calls=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            cost=Decimal(0),
        )


def test_equivalent_decimal_charges_have_same_safe_fingerprint() -> None:
    first = make_charge(cost=Decimal("0.10"))
    second = make_charge(cost=Decimal("0.100"))

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint.startswith("sha256:")
    assert "0.10" not in first.fingerprint


def test_snapshot_applies_charge_and_derives_total_tokens() -> None:
    decision = BudgetSnapshot().apply(make_charge(), make_budget())

    assert decision.exceeded_dimension is None
    assert decision.replayed is False
    assert decision.snapshot.steps == 1
    assert decision.snapshot.total_tokens == 15
    assert decision.snapshot.cost == Decimal("0.10")


def test_same_charge_replays_and_changed_charge_conflicts() -> None:
    charge = make_charge()
    first = BudgetSnapshot().apply(charge, make_budget()).snapshot

    replay = first.apply(charge, make_budget())
    assert replay.replayed is True
    assert replay.snapshot is first

    with pytest.raises(RunLifecycleError) as raised:
        first.apply(make_charge(steps=2), make_budget())
    assert raised.value.code is RunErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize(
    ("charge", "dimension"),
    [
        ({"steps": 6}, BudgetDimension.STEPS),
        ({"model_calls": 5}, BudgetDimension.MODEL_CALLS),
        ({"tool_calls": 4}, BudgetDimension.TOOL_CALLS),
        ({"input_tokens": 101}, BudgetDimension.INPUT_TOKENS),
        ({"output_tokens": 51}, BudgetDimension.OUTPUT_TOKENS),
        ({"input_tokens": 80, "output_tokens": 41}, BudgetDimension.TOTAL_TOKENS),
        ({"cost": Decimal("1.26")}, BudgetDimension.COST),
    ],
)
def test_snapshot_reports_each_exceeded_dimension(
    charge: dict[str, object], dimension: BudgetDimension
) -> None:
    values = {
        "steps": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": Decimal(0),
    }
    values.update(charge)
    decision = BudgetSnapshot().apply(
        make_charge(**values),  # type: ignore[arg-type]
        make_budget(),
    )

    assert decision.exceeded_dimension is dimension
    assert decision.snapshot == BudgetSnapshot()


def test_exact_limits_are_allowed() -> None:
    charge = make_charge(
        steps=5,
        model_calls=4,
        tool_calls=3,
        input_tokens=70,
        output_tokens=50,
        cost=Decimal("1.25"),
    )

    decision = BudgetSnapshot().apply(charge, make_budget())

    assert decision.exceeded_dimension is None
    assert decision.snapshot.total_tokens == 120


@pytest.mark.parametrize(
    ("below", "equal", "over", "dimension"),
    [
        ({"steps": 4}, {"steps": 5}, {"steps": 6}, BudgetDimension.STEPS),
        (
            {"model_calls": 3},
            {"model_calls": 4},
            {"model_calls": 5},
            BudgetDimension.MODEL_CALLS,
        ),
        (
            {"tool_calls": 2},
            {"tool_calls": 3},
            {"tool_calls": 4},
            BudgetDimension.TOOL_CALLS,
        ),
        (
            {"input_tokens": 99},
            {"input_tokens": 100},
            {"input_tokens": 101},
            BudgetDimension.INPUT_TOKENS,
        ),
        (
            {"output_tokens": 49},
            {"output_tokens": 50},
            {"output_tokens": 51},
            BudgetDimension.OUTPUT_TOKENS,
        ),
        (
            {"input_tokens": 70, "output_tokens": 49},
            {"input_tokens": 70, "output_tokens": 50},
            {"input_tokens": 71, "output_tokens": 50},
            BudgetDimension.TOTAL_TOKENS,
        ),
        (
            {"cost": Decimal("1.24")},
            {"cost": Decimal("1.25")},
            {"cost": Decimal("1.26")},
            BudgetDimension.COST,
        ),
    ],
)
def test_each_budget_dimension_has_below_equal_and_over_boundaries(
    below: dict[str, object],
    equal: dict[str, object],
    over: dict[str, object],
    dimension: BudgetDimension,
) -> None:
    base = {
        "steps": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": Decimal(0),
    }
    decisions = []
    for index, values in enumerate((below, equal, over)):
        charge_values = {**base, **values}
        decisions.append(
            BudgetSnapshot().apply(
                make_charge(
                    charge_id=f"boundary-{index}",
                    **charge_values,
                ),
                make_budget(),
            )
        )

    assert decisions[0].exceeded_dimension is None
    assert decisions[1].exceeded_dimension is None
    assert decisions[2].exceeded_dimension is dimension


def test_cost_addition_and_canonicalization_do_not_use_decimal_context_rounding() -> None:
    precise = Decimal("0.123456789012345678901234567890123456789")
    budget = make_budget(max_cost=Decimal("1"))
    first = BudgetSnapshot().apply(make_charge(cost=precise), budget).snapshot
    second = first.apply(
        make_charge(
            charge_id="charge-2", cost=Decimal("0.000000000000000000000000000000000000001")
        ),
        budget,
    ).snapshot

    assert second.cost == Decimal("0.123456789012345678901234567890123456790")
