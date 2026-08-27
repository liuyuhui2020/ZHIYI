"""SC-008 real PostgreSQL Worker Lease Kernel latency acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import platform
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from math import ceil
from pathlib import Path
from time import perf_counter_ns
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.worker_lease_observability import LeaseOperationObservation
from zhiyi.domain.runs.identifiers import TenantId
from zhiyi.domain.worker_leases.identifiers import LeaseVersion, WorkerId
from zhiyi.domain.worker_leases.models import LeaseGrant
from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
)
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

pytestmark = pytest.mark.postgresql
ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = TenantId("tenant-worker-lease-performance")
WARMUP_SAMPLES = 100
MEASURED_SAMPLES = 1_000
CLIENTS = 20
POSTGRES_IMAGE = (
    "postgres:18.6@sha256:1ffbf339f5b8e78c394cfaad3711ef6dbc4e14546bf70428e0bb30cba66e8e4d"
)


class CountingTelemetry:
    def __init__(self) -> None:
        self.logs = 0
        self.metrics = 0
        self.traces = 0

    def record_log(self, observation: LeaseOperationObservation) -> None:
        self.logs += 1

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        self.metrics += 1

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        self.traces += 1


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
) -> tuple[list[float], list[T]]:
    async def worker(
        assigned: Sequence[Callable[[], Awaitable[T]]],
    ) -> tuple[list[float], list[T]]:
        timings: list[float] = []
        results: list[T] = []
        for operation in assigned:
            started = perf_counter_ns()
            results.append(await operation())
            timings.append((perf_counter_ns() - started) / 1_000_000)
        return timings, results

    worker_results = await asyncio.gather(
        *(worker(operations[index::CLIENTS]) for index in range(CLIENTS))
    )
    return (
        [sample for timings, _ in worker_results for sample in timings],
        [result for _, results in worker_results for result in results],
    )


async def _checkpoint(engine: AsyncEngine) -> None:
    """Drain prior acceptance-test writes outside the measured interval."""
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text("CHECKPOINT"))


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
    await _checkpoint(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE worker_lease_claim_receipts, worker_leases, "
                "run_command_receipts, run_events, runs RESTART IDENTITY CASCADE"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO runs ("
                "tenant_id, run_id, task_id, agent_id, agent_version_id, "
                "agent_build_digest, run_status, run_version, next_event_sequence, "
                "created_at, updated_at, last_observed_at, snapshot_format_version, snapshot"
                ") SELECT :tenant_id, "
                "'run-performance-' || lpad(series::text, 5, '0'), "
                "'task-performance-' || lpad(series::text, 5, '0'), "
                "'agent-performance', 'version-performance', :digest, 'queued', '1', '2', "
                "clock_timestamp() - interval '1 hour' + series * interval '1 microsecond', "
                "clock_timestamp() - interval '1 hour' + series * interval '1 microsecond', "
                "clock_timestamp() - interval '1 hour' + series * interval '1 microsecond', "
                "1, '{}'::json FROM generate_series(1, 10000) AS series"
            ),
            {
                "tenant_id": str(TENANT_ID),
                "digest": "sha256:" + "a" * 64,
            },
        )
        await connection.execute(text("ANALYZE runs, worker_leases"))
    await _checkpoint(engine)
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)


async def test_sc_008_all_required_operations_p95_below_200_ms(
    performance_engine: AsyncEngine,
) -> None:
    telemetry = CountingTelemetry()
    repository = PostgreSQLWorkerLeaseRepository(
        performance_engine,
        telemetry=telemetry,
        token_generator=SecureLeaseTokenGenerator(),
    )
    sample_count = WARMUP_SAMPLES + MEASURED_SAMPLES

    async def issue_and_claim(index: int) -> LeaseGrant:
        outcome = await repository.claim(
            ClaimLeaseCommand(
                TENANT_ID,
                WorkerId(f"worker-performance-{index:04d}"),
                await repository.issue_claim_id(),
            )
        )
        assert outcome.grant is not None
        return outcome.grant

    claim_operations = [lambda index=index: issue_and_claim(index) for index in range(sample_count)]
    claim_warmup, warmup_grants = await _measure(claim_operations[:WARMUP_SAMPLES])
    claim_samples, measured_grants = await _measure(claim_operations[WARMUP_SAMPLES:])
    grants = warmup_grants + measured_grants
    assert len(claim_warmup) == WARMUP_SAMPLES
    assert len(grants) == sample_count

    renew_operations = [
        lambda grant=grant: repository.renew(RenewLeaseCommand(grant.proof, LeaseVersion(1)))
        for grant in grants
    ]
    renew_warmup, _ = await _measure(renew_operations[:WARMUP_SAMPLES])
    renew_samples, renew_results = await _measure(renew_operations[WARMUP_SAMPLES:])
    assert len(renew_warmup) == WARMUP_SAMPLES
    assert all(result.applied for result in renew_results)

    authority_operations = [
        lambda grant=grant: repository.get_authority(grant.proof) for grant in grants
    ]
    authority_warmup, _ = await _measure(authority_operations[:WARMUP_SAMPLES])
    authority_samples, authority_results = await _measure(authority_operations[WARMUP_SAMPLES:])
    assert len(authority_warmup) == WARMUP_SAMPLES
    assert all(result.authoritative for result in authority_results)

    release_operations = [
        lambda grant=grant: repository.release(ReleaseLeaseCommand(grant.proof, LeaseVersion(2)))
        for grant in grants
    ]
    release_warmup, _ = await _measure(release_operations[:WARMUP_SAMPLES])
    release_samples, release_results = await _measure(release_operations[WARMUP_SAMPLES:])
    assert len(release_warmup) == WARMUP_SAMPLES
    assert all(result.applied for result in release_results)

    measurements = {
        "issue_claim_id_plus_claim": claim_samples,
        "renew": renew_samples,
        "get_authority": authority_samples,
        "release": release_samples,
    }
    percentiles = {
        name: {
            "samples": len(samples),
            "p50_ms": round(_percentile(samples, 0.50), 3),
            "p95_ms": round(_percentile(samples, 0.95), 3),
        }
        for name, samples in measurements.items()
    }
    async with performance_engine.connect() as connection:
        database_version = await connection.scalar(text("SHOW server_version"))
        count_rows = await connection.execute(
            text(
                "SELECT 'runs', count(*) FROM runs "
                "UNION ALL SELECT 'worker_leases', count(*) FROM worker_leases "
                "UNION ALL SELECT 'claim_receipts', count(*) "
                "FROM worker_lease_claim_receipts"
            )
        )
        row_counts: dict[str, int] = {str(row[0]): int(row[1]) for row in count_rows.all()}
        lock_waits = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
        claim_plan = await connection.scalar(
            text(
                "EXPLAIN (FORMAT JSON) SELECT run_id FROM runs "
                "WHERE tenant_id = :tenant_id AND run_status = 'queued' "
                "ORDER BY updated_at, run_id LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"tenant_id": str(TENANT_ID)},
        )

    evidence: dict[str, Any] = {
        "postgresql": database_version,
        "image": POSTGRES_IMAGE,
        "host": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "physical_memory_bytes": _physical_memory_bytes(),
        },
        "pool": {"size": 20, "max_overflow": 0, "clients": CLIENTS},
        "timeouts_ms": {"lock": 5_000, "statement": 5_000},
        "warmups_per_operation": WARMUP_SAMPLES,
        "rows": row_counts,
        "lock_waits": lock_waits,
        "claim_plan": claim_plan,
        "percentiles": percentiles,
    }
    print("WORKER_LEASE_PERFORMANCE=" + json.dumps(evidence, default=str, sort_keys=True))

    assert row_counts["runs"] == 10_000
    assert telemetry.logs == telemetry.metrics == telemetry.traces
    assert all(result["samples"] == MEASURED_SAMPLES for result in percentiles.values())
    assert all(result["p95_ms"] < 200 for result in percentiles.values()), percentiles
