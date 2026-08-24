from __future__ import annotations

import asyncio

import pytest

from zhiyi.application.services.rate_limiter import AsyncTokenBucket


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_token_bucket_allows_burst_then_waits_for_refill() -> None:
    fake = FakeTime()
    bucket = AsyncTokenBucket(
        rate_per_second=2,
        burst=2,
        clock=fake.monotonic,
        sleeper=fake.sleep,
    )

    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()

    assert fake.sleeps == [pytest.approx(0.5)]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_a_future_token() -> None:
    fake = FakeTime()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_sleep(delay: float) -> None:
        entered.set()
        await release.wait()
        fake.now += delay

    bucket = AsyncTokenBucket(
        rate_per_second=1,
        burst=1,
        clock=fake.monotonic,
        sleeper=blocking_sleep,
    )
    await bucket.acquire()
    cancelled = asyncio.create_task(bucket.acquire())
    await entered.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    replacement = asyncio.create_task(bucket.acquire())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(replacement, timeout=1)


@pytest.mark.asyncio
async def test_waiters_are_admitted_in_arrival_order() -> None:
    fake = FakeTime()
    bucket = AsyncTokenBucket(
        rate_per_second=10,
        burst=1,
        clock=fake.monotonic,
        sleeper=fake.sleep,
    )
    await bucket.acquire()
    order: list[int] = []

    async def wait(index: int) -> None:
        await bucket.acquire()
        order.append(index)

    await asyncio.gather(*(wait(index) for index in range(5)))

    assert order == list(range(5))
