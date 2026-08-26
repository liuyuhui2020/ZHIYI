"""Secret-safe, bounded SQLAlchemy async-engine construction."""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_postgresql_engine(
    database_url: str,
    *,
    pool_size: int = 20,
    pool_timeout_seconds: float = 5,
    connect_timeout_seconds: int = 5,
    statement_timeout_ms: int = 5_000,
) -> AsyncEngine:
    """Create the one bounded application pool for a PostgreSQL DSN."""

    if type(database_url) is not str:
        raise TypeError("database_url must be a string")
    try:
        parsed = make_url(database_url)
    except Exception as error:
        raise ValueError("database_url is invalid") from error
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("database_url must use the PostgreSQL Psycopg async dialect")
    if type(pool_size) is not int or pool_size < 1:
        raise ValueError("pool_size must be positive")
    if type(pool_timeout_seconds) not in {int, float} or pool_timeout_seconds <= 0:
        raise ValueError("pool_timeout_seconds must be positive")
    if type(connect_timeout_seconds) is not int or connect_timeout_seconds < 1:
        raise ValueError("connect_timeout_seconds must be positive")
    if type(statement_timeout_ms) is not int or statement_timeout_ms < 1:
        raise ValueError("statement_timeout_ms must be positive")
    return create_async_engine(
        database_url,
        isolation_level="READ COMMITTED",
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout_seconds,
        pool_pre_ping=True,
        hide_parameters=True,
        echo=False,
        connect_args={
            "connect_timeout": connect_timeout_seconds,
            "options": f"-c statement_timeout={statement_timeout_ms}",
        },
    )


async def dispose_postgresql_engine(engine: AsyncEngine) -> None:
    """Release every connection owned by an application engine."""

    await engine.dispose()
