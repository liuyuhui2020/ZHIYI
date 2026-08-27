"""Real PostgreSQL fixtures for the explicitly selected database lane."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.application.ports.worker_lease_observability import LeaseOperationObservation
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.budget import RunBudget
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
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
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

if TYPE_CHECKING:
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

ROOT = Path(__file__).resolve().parents[3]


def require_postgresql_database_url() -> str:
    url = os.environ.get("ZHIYI_TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "ZHIYI_TEST_DATABASE_URL is required for the PostgreSQL lane; "
            "start compose.test.yaml and export its async SQLAlchemy URL"
        )
    return url


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def migrated_postgresql_url() -> str:
    url = require_postgresql_database_url()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest_asyncio.fixture
async def postgresql_engine(migrated_postgresql_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_postgresql_engine(migrated_postgresql_url, pool_size=20)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE worker_lease_claim_receipts, worker_leases, "
                "run_command_receipts, run_events, runs RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)


@pytest_asyncio.fixture
async def independent_postgresql_engine(
    migrated_postgresql_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_postgresql_engine(migrated_postgresql_url, pool_size=20)
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)


class RecordingWorkerLeaseTelemetry:
    def __init__(self) -> None:
        self.logs: list[LeaseOperationObservation] = []
        self.metrics: list[LeaseOperationObservation] = []
        self.traces: list[LeaseOperationObservation] = []

    def record_log(self, observation: LeaseOperationObservation) -> None:
        self.logs.append(observation)

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        self.metrics.append(observation)

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        self.traces.append(observation)


@pytest.fixture
def worker_lease_telemetry() -> RecordingWorkerLeaseTelemetry:
    return RecordingWorkerLeaseTelemetry()


@pytest.fixture
def worker_lease_repository_factory(
    postgresql_engine: AsyncEngine,
) -> Callable[..., PostgreSQLWorkerLeaseRepository]:
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

    def build(**options: object) -> PostgreSQLWorkerLeaseRepository:
        telemetry = options.pop("telemetry", RecordingWorkerLeaseTelemetry())
        token_generator = options.pop("token_generator", SecureLeaseTokenGenerator())
        return PostgreSQLWorkerLeaseRepository(
            postgresql_engine,
            telemetry=telemetry,  # type: ignore[arg-type]
            token_generator=token_generator,  # type: ignore[arg-type]
            **options,  # type: ignore[arg-type]
        )

    return build


@pytest.fixture
def worker_lease_repository(
    postgresql_engine: AsyncEngine,
    worker_lease_telemetry: RecordingWorkerLeaseTelemetry,
) -> PostgreSQLWorkerLeaseRepository:
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

    return PostgreSQLWorkerLeaseRepository(
        postgresql_engine,
        telemetry=worker_lease_telemetry,
        token_generator=SecureLeaseTokenGenerator(),
    )


@pytest.fixture
def worker_lease_repository_at_time(
    postgresql_engine: AsyncEngine,
) -> Callable[[datetime], PostgreSQLWorkerLeaseRepository]:
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

    class FixedDatabaseTimeRepository(PostgreSQLWorkerLeaseRepository):
        def __init__(self, captured_at: datetime) -> None:
            self._captured_at = captured_at
            super().__init__(
                postgresql_engine,
                telemetry=RecordingWorkerLeaseTelemetry(),
                token_generator=SecureLeaseTokenGenerator(),
            )

        async def _database_now(self, connection: AsyncConnection) -> datetime:
            return self._captured_at

    return FixedDatabaseTimeRepository


@pytest.fixture
def worker_lease_repository_with_failing_clock(
    postgresql_engine: AsyncEngine,
) -> PostgreSQLWorkerLeaseRepository:
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

    class FailingClockRepository(PostgreSQLWorkerLeaseRepository):
        async def _database_now(self, connection: AsyncConnection) -> datetime:
            raise ConnectionError("database clock unavailable")

    return FailingClockRepository(
        postgresql_engine,
        telemetry=RecordingWorkerLeaseTelemetry(),
        token_generator=SecureLeaseTokenGenerator(),
    )


@pytest.fixture
def worker_lease_repository_with_boundary(
    postgresql_engine: AsyncEngine,
) -> Callable[[Callable[[str, AsyncConnection], Awaitable[None]]], PostgreSQLWorkerLeaseRepository]:
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

    class BoundaryRepository(PostgreSQLWorkerLeaseRepository):
        def __init__(
            self,
            hook: Callable[[str, AsyncConnection], Awaitable[None]],
        ) -> None:
            self._hook = hook
            super().__init__(
                postgresql_engine,
                telemetry=RecordingWorkerLeaseTelemetry(),
                token_generator=SecureLeaseTokenGenerator(),
            )

        async def _transaction_boundary(
            self,
            name: str,
            connection: AsyncConnection,
        ) -> None:
            await self._hook(name, connection)

    return BoundaryRepository


@pytest.fixture
def seed_queued_runs(
    postgresql_engine: AsyncEngine,
) -> Callable[[TenantId, int], Awaitable[tuple[Run, ...]]]:
    async def seed(tenant_id: TenantId, count: int) -> tuple[Run, ...]:
        repository = PostgreSQLRunRepository(postgresql_engine)
        base = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
        created: list[Run] = []
        for index in range(count):
            observed_at = base + timedelta(microseconds=index)
            run_id = RunId(f"run-{index:06d}")
            mutation = Run.create(
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=TaskId(f"task-{index:06d}"),
                agent_version=AgentVersionRef(
                    tenant_id=tenant_id,
                    agent_id=AgentId("agent-worker-lease-test"),
                    version_id=AgentVersionId("version-worker-lease-test"),
                    build_digest="sha256:" + "a" * 64,
                ),
                budget=RunBudget(
                    deadline_at=base + timedelta(days=1),
                    max_steps=10,
                    max_model_calls=10,
                    max_tool_calls=10,
                    max_input_tokens=100,
                    max_output_tokens=100,
                    max_total_tokens=200,
                    max_cost=Decimal("10"),
                    currency="USD",
                ),
                observed_at=observed_at,
                event_id=EventId(f"event-create-{tenant_id}-{index:06d}"),
            )
            await repository.commit(
                expected_version=0,
                updated_run=mutation.run,
                new_events=mutation.events,
                receipt=CommandReceipt(
                    tenant_id=tenant_id,
                    command_id=CommandId(f"command-create-{tenant_id}-{index:06d}"),
                    run_id=run_id,
                    command_type="create_run",
                    intent_fingerprint="sha256:" + f"{index:064x}"[-64:],
                    resulting_status=mutation.run.status,
                    resulting_version=mutation.run.version,
                    event_ids=(mutation.events[0].event_id,),
                    created_at=observed_at,
                ),
            )
            created.append(mutation.run)
        return tuple(created)

    return seed
