"""Read-only application-schema compatibility gate."""

from __future__ import annotations

import asyncio
import weakref
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_schema import SCHEMA_CONTRACT_VERSION
from zhiyi.application.ports.run_repository import RunRepositoryError, RunRepositoryErrorCode


class PartialSchemaError(Exception):
    """Internal marker for an installed but incomplete physical schema."""


_compatible_engines: weakref.WeakSet[AsyncEngine] = weakref.WeakSet()
_engine_locks: weakref.WeakKeyDictionary[AsyncEngine, asyncio.Lock] = weakref.WeakKeyDictionary()
_PARTIAL_SCHEMA_SQLSTATES = frozenset({"42P01", "42703"})


def _sqlstate(error: BaseException) -> str | None:
    candidate: object = error
    for _ in range(3):
        value = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if type(value) is str:
            return value
        nested = getattr(candidate, "orig", None)
        if not isinstance(nested, BaseException):
            break
        candidate = nested
    return None


async def _read_contract_version(engine: AsyncEngine) -> int | None:
    try:
        async with engine.connect() as connection:
            return cast(
                int | None,
                await connection.scalar(
                    text(
                        "SELECT contract_version FROM zhiyi_schema_compatibility "
                        "WHERE component = 'run_repository'"
                    )
                ),
            )
    except ProgrammingError as error:
        if _sqlstate(error) in _PARTIAL_SCHEMA_SQLSTATES:
            raise PartialSchemaError from error
        raise


async def ensure_schema_compatible(engine: AsyncEngine) -> None:
    """Verify exactly one accepted contract version without mutating the database."""

    if engine in _compatible_engines:
        return
    engine_lock = _engine_locks.get(engine)
    if engine_lock is None:
        engine_lock = asyncio.Lock()
        _engine_locks[engine] = engine_lock
    async with engine_lock:
        if engine in _compatible_engines:
            return
        try:
            version = await _read_contract_version(engine)
        except PartialSchemaError as error:
            raise RunRepositoryError(RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE) from error
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            raise RunRepositoryError(RunRepositoryErrorCode.STORAGE_UNAVAILABLE) from error
        if type(version) is not int or version != SCHEMA_CONTRACT_VERSION:
            raise RunRepositoryError(RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE)
        _compatible_engines.add(engine)
