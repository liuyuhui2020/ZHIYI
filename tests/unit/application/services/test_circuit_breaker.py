from __future__ import annotations

import pytest

from zhiyi.application.models.contracts import ErrorCode, ModelError
from zhiyi.application.services.circuit_breaker import CircuitBreaker, CircuitState


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def assert_state(breaker: CircuitBreaker, expected: CircuitState) -> None:
    assert breaker.state == expected


@pytest.mark.asyncio
async def test_breaker_opens_at_threshold_and_recovers_after_half_open_success() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=2, recovery_seconds=10, clock=clock)

    await breaker.before_call("req-1")
    await breaker.record_failure(ErrorCode.UNAVAILABLE)
    await breaker.before_call("req-2")
    await breaker.record_failure(ErrorCode.TIMEOUT)
    assert_state(breaker, CircuitState.OPEN)

    with pytest.raises(ModelError) as opened:
        await breaker.before_call("req-3")
    assert opened.value.code is ErrorCode.CIRCUIT_OPEN

    clock.now = 10
    await breaker.before_call("probe")
    assert_state(breaker, CircuitState.HALF_OPEN)
    await breaker.record_success()
    assert_state(breaker, CircuitState.CLOSED)


@pytest.mark.asyncio
async def test_half_open_allows_only_one_concurrent_probe() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, recovery_seconds=5, clock=clock)
    await breaker.before_call("initial")
    await breaker.record_failure(ErrorCode.UNAVAILABLE)
    clock.now = 5

    await breaker.before_call("probe-1")
    with pytest.raises(ModelError) as second:
        await breaker.before_call("probe-2")

    assert second.value.code is ErrorCode.CIRCUIT_OPEN


@pytest.mark.asyncio
async def test_non_transient_errors_do_not_trip_or_poison_breaker() -> None:
    breaker = CircuitBreaker(threshold=1, recovery_seconds=5)

    for code in (
        ErrorCode.AUTHENTICATION,
        ErrorCode.INVALID_REQUEST,
        ErrorCode.CONTENT_POLICY,
    ):
        await breaker.before_call("business-error")
        await breaker.record_failure(code)

    assert_state(breaker, CircuitState.CLOSED)


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_releases_probe_slot() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, recovery_seconds=5, clock=clock)
    await breaker.before_call("initial")
    await breaker.record_failure(ErrorCode.UNAVAILABLE)
    clock.now = 5
    await breaker.before_call("cancelled-probe")

    await breaker.record_cancelled()
    await breaker.before_call("replacement-probe")
    await breaker.record_success()

    assert_state(breaker, CircuitState.CLOSED)
