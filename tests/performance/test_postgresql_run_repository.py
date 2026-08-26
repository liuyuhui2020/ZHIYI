"""SC-011 real PostgreSQL latency acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path
from time import perf_counter_ns

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run, RunMutation
from zhiyi.domain.runs.budget import BudgetCharge, RunBudget
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    ChargeId,
    CommandId,
    EventId,
    RunId,
    TaskId,
    TenantId,
)
from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
)

pytestmark = pytest.mark.postgresql
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _new_run(index: str) -> RunMutation:
    tenant = TenantId("tenant-performance")
    return Run.create(
        tenant_id=tenant,
        run_id=RunId(f"run-{index}"),
        task_id=TaskId(f"task-{index}"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("agent-performance"),
            version_id=AgentVersionId("version-performance"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(days=1),
            max_steps=200,
            max_model_calls=200,
            max_tool_calls=200,
            max_input_tokens=200,
            max_output_tokens=200,
            max_total_tokens=400,
            max_cost=Decimal("200"),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId(f"event-create-{index}"),
    )


def _receipt(mutation: RunMutation, command: str, command_type: str) -> CommandReceipt:
    return CommandReceipt(
        tenant_id=mutation.run.tenant_id,
        command_id=CommandId(command),
        run_id=mutation.run.run_id,
        command_type=command_type,
        intent_fingerprint=_fingerprint(command),
        resulting_status=mutation.run.status,
        resulting_version=mutation.run.version,
        event_ids=tuple(event.event_id for event in mutation.events),
        created_at=NOW,
    )


async def _commit(
    repository: PostgreSQLRunRepository,
    mutation: RunMutation,
    command: str,
    command_type: str,
) -> None:
    await repository.commit(
        expected_version=mutation.run.version - (1 if mutation.events else 0),
        updated_run=mutation.run,
        new_events=mutation.events,
        receipt=_receipt(mutation, command, command_type),
    )


async def _seed_run(repository: PostgreSQLRunRepository, index: int) -> Run:
    created = _new_run(f"seed-{index}")
    await _commit(repository, created, f"command-create-{index}", "create_run")
    started = created.run.start(
        observed_at=NOW,
        event_id=EventId(f"event-start-{index}"),
    )
    await _commit(repository, started, f"command-start-{index}", "start_run")
    current = started.run
    for charge_index in range(98):
        charged = current.consume_budget(
            charge=BudgetCharge(charge_id=ChargeId(f"charge-{index}-{charge_index}"), steps=1),
            observed_at=NOW,
            event_id=EventId(f"event-charge-{index}-{charge_index}"),
        )
        await _commit(
            repository,
            charged,
            f"command-charge-{index}-{charge_index}",
            "consume_budget",
        )
        current = charged.run
    return current


def _percentile(samples: Sequence[float], percentile: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


def _physical_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


async def _measure[T](
    operations: Sequence[Callable[[], Awaitable[T]]],
    *,
    clients: int,
) -> list[float]:
    async def worker(assigned: Sequence[Callable[[], Awaitable[T]]]) -> list[float]:
        timings: list[float] = []
        for operation in assigned:
            started = perf_counter_ns()
            await operation()
            timings.append((perf_counter_ns() - started) / 1_000_000)
        return timings

    worker_results = await asyncio.gather(
        *(worker(operations[index::clients]) for index in range(clients))
    )
    return [timing for result in worker_results for timing in result]


@pytest.fixture(scope="module")
def performance_database_url() -> str:
    url = os.environ.get("ZHIYI_TEST_DATABASE_URL")
    if not url:
        pytest.fail("ZHIYI_TEST_DATABASE_URL is required for PostgreSQL performance acceptance")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    return url


@pytest_asyncio.fixture
async def performance_engine(
    performance_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_postgresql_engine(
        performance_database_url,
        pool_size=20,
        pool_timeout_seconds=5,
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE run_command_receipts, run_events, runs RESTART IDENTITY CASCADE")
        )
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)


async def test_sc_011_load_page_and_atomic_commit_p95_below_100_ms(
    performance_engine: AsyncEngine,
) -> None:
    repository = PostgreSQLRunRepository(performance_engine)
    seed_limit = asyncio.Semaphore(20)

    async def seed(index: int) -> Run:
        async with seed_limit:
            return await _seed_run(repository, index)

    seeded = list(await asyncio.gather(*(seed(index) for index in range(100))))
    assert all(run.version == 100 for run in seeded)
    async with performance_engine.begin() as connection:
        await connection.execute(text("ANALYZE runs, run_events, run_command_receipts"))

    load_operations = [
        lambda run=seeded[index % 100]: repository.load(run.tenant_id, run.run_id)
        for index in range(1_100)
    ]
    page_operations = [
        lambda run=seeded[index % 100]: repository.list_events(
            run.tenant_id,
            run.run_id,
            limit=100,
        )
        for index in range(1_100)
    ]
    commit_candidates = [_new_run(f"sample-{index}") for index in range(1_100)]
    commit_operations = [
        lambda mutation=mutation, index=index: _commit(
            repository,
            mutation,
            f"command-sample-{index}",
            "create_run",
        )
        for index, mutation in enumerate(commit_candidates)
    ]

    # Exactly 100 warm-ups per operation class are intentionally excluded.
    await _measure(load_operations[:100], clients=20)
    await _measure(page_operations[:100], clients=20)
    await _measure(commit_operations[:100], clients=20)
    samples = {
        "load": await _measure(load_operations[100:], clients=20),
        "page_100": await _measure(page_operations[100:], clients=20),
        "atomic_commit": await _measure(commit_operations[100:], clients=20),
    }
    latencies = {
        name: {"p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95)}
        for name, values in samples.items()
    }
    evidence = {
        "clients": 20,
        "pool_size": 20,
        "max_overflow": 0,
        "samples_per_operation": 1_000,
        "warmups_per_operation": 100,
        "cpu_count": os.cpu_count(),
        "physical_memory_bytes": _physical_memory_bytes(),
        "machine": platform.machine(),
        "os": platform.platform(),
        "postgresql_image": (
            "postgres:18.6@sha256:1ffbf339f5b8e78c394cfaad3711ef6dbc4e14546bf70428e0bb30cba66e8e4d"
        ),
        "latency_ms": latencies,
    }
    print(json.dumps(evidence, sort_keys=True))
    assert all(values["p95"] < 100 for values in latencies.values())
