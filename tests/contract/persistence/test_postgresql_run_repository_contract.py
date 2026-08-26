"""Provider-neutral RunRepository contract bound to PostgreSQL."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from run_repository_contract import RunRepositoryContract
from sqlalchemy import bindparam, cast, insert, text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_codecs import encode_event
from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_schema import run_events
from zhiyi.application.ports.run_repository import RunRepository
from zhiyi.domain.runs.events import RunEvent, RunEventType
from zhiyi.domain.runs.identifiers import EventId
from zhiyi.infrastructure.database.engine import create_postgresql_engine, dispose_postgresql_engine

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
            text("TRUNCATE run_command_receipts, run_events, runs RESTART IDENTITY CASCADE")
        )
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)


class TestPostgreSQLRunRepositoryContract(RunRepositoryContract):
    @pytest_asyncio.fixture
    async def repository(self, contract_engine: AsyncEngine) -> AsyncIterator[RunRepository]:
        yield PostgreSQLRunRepository(contract_engine)

    async def test_sequences_above_signed_bigint_are_replayed_canonically(
        self,
        contract_engine: AsyncEngine,
        repository: RunRepository,
    ) -> None:
        from run_repository_contract import commit_create

        created, _ = await commit_create(repository)
        sequence = 2**127
        event = RunEvent(
            event_id=EventId("event-huge-sequence"),
            tenant_id=created.tenant_id,
            run_id=created.run_id,
            sequence=sequence,
            type=RunEventType.RUN_STARTED,
            occurred_at=created.updated_at,
            payload_version=1,
            payload={"previous_status": "queued", "run_version": sequence, "status": "running"},
        )
        record = encode_event(event)
        payload = record.pop("payload")
        async with contract_engine.begin() as connection:
            await connection.execute(
                insert(run_events).values(
                    **record,
                    payload=cast(bindparam("payload_json"), JSON),
                ),
                {"payload_json": payload},
            )

        assert await repository.list_events(
            created.tenant_id,
            created.run_id,
            after_sequence=2**63,
        ) == (event,)
