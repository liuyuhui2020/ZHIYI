"""Provider-neutral WorkerLeaseRepository contract bound to PostgreSQL."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from worker_lease_repository_contract import (
    RecordingWorkerLeaseTelemetry,
    WorkerLeaseRepositoryContract,
)

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.ports.worker_lease_repository import WorkerLeaseRepository
from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
)
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

pytestmark = pytest.mark.postgresql
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def migrated_url() -> str:
    url = os.environ.get("ZHIYI_TEST_DATABASE_URL")
    if not url:
        pytest.fail("ZHIYI_TEST_DATABASE_URL is required for the PostgreSQL contract lane")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    return url


@pytest_asyncio.fixture
async def contract_engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_postgresql_engine(migrated_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE worker_lease_claim_receipts, worker_leases, "
                "run_command_receipts, run_events, runs RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)


class TestPostgreSQLWorkerLeaseRepositoryContract(WorkerLeaseRepositoryContract):
    @pytest.fixture
    def telemetry(self) -> RecordingWorkerLeaseTelemetry:
        return RecordingWorkerLeaseTelemetry()

    @pytest_asyncio.fixture
    async def repository(
        self,
        contract_engine: AsyncEngine,
        telemetry: RecordingWorkerLeaseTelemetry,
    ) -> AsyncIterator[WorkerLeaseRepository]:
        yield PostgreSQLWorkerLeaseRepository(
            contract_engine,
            telemetry=telemetry,
            token_generator=SecureLeaseTokenGenerator(),
        )

    @pytest.fixture
    def run_repository(self, contract_engine: AsyncEngine) -> PostgreSQLRunRepository:
        return PostgreSQLRunRepository(contract_engine)

    def test_telemetry_is_a_required_constructor_dependency(
        self,
        contract_engine: AsyncEngine,
    ) -> None:
        with pytest.raises(TypeError):
            PostgreSQLWorkerLeaseRepository(  # type: ignore[call-arg]
                contract_engine,
                token_generator=SecureLeaseTokenGenerator(),
            )
