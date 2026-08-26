"""Real PostgreSQL fixtures for the explicitly selected database lane."""

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

from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
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
            text("TRUNCATE TABLE run_command_receipts, run_events, runs RESTART IDENTITY CASCADE")
        )
    try:
        yield engine
    finally:
        await dispose_postgresql_engine(engine)
