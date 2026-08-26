"""Read-only schema compatibility checks."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import ProgrammingError

from zhiyi.adapters.persistence.postgresql_codecs import decode_run
from zhiyi.application.ports.run_repository import RunRepositoryError, RunRepositoryErrorCode
from zhiyi.infrastructure.database import schema_compatibility


class _Engine:
    pass


async def test_compatible_schema_is_cached_for_engine_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    read = AsyncMock(return_value=1)
    monkeypatch.setattr(schema_compatibility, "_read_contract_version", read)

    await schema_compatibility.ensure_schema_compatible(engine)  # type: ignore[arg-type]
    await schema_compatibility.ensure_schema_compatible(engine)  # type: ignore[arg-type]

    read.assert_awaited_once_with(engine)


@pytest.mark.parametrize("version", [None, 0, 2, True, "1", 1.0])
async def test_missing_older_or_newer_schema_is_incompatible(
    monkeypatch: pytest.MonkeyPatch,
    version: object,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        schema_compatibility,
        "_read_contract_version",
        AsyncMock(return_value=version),
    )

    with pytest.raises(RunRepositoryError) as caught:
        await schema_compatibility.ensure_schema_compatible(engine)  # type: ignore[arg-type]
    assert caught.value.code is RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE


class _DriverProgrammingError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("redacted driver error")
        self.sqlstate = sqlstate


class _FailingConnection:
    def __init__(self, sqlstate: str) -> None:
        self._sqlstate = sqlstate

    async def __aenter__(self) -> _FailingConnection:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def scalar(self, statement: object) -> object:
        raise ProgrammingError(
            "redacted statement",
            {},
            _DriverProgrammingError(self._sqlstate),
        )


class _FailingEngine:
    def __init__(self, sqlstate: str) -> None:
        self._sqlstate = sqlstate

    def connect(self) -> _FailingConnection:
        return _FailingConnection(self._sqlstate)


@pytest.mark.parametrize("sqlstate", ["42P01", "42703"])
async def test_only_missing_relation_or_column_is_partial_schema(sqlstate: str) -> None:
    with pytest.raises(schema_compatibility.PartialSchemaError):
        await schema_compatibility._read_contract_version(
            _FailingEngine(sqlstate)  # type: ignore[arg-type]
        )


async def test_permission_failure_is_storage_unavailable() -> None:
    with pytest.raises(RunRepositoryError) as caught:
        await schema_compatibility.ensure_schema_compatible(
            _FailingEngine("42501")  # type: ignore[arg-type]
        )
    assert caught.value.code is RunRepositoryErrorCode.STORAGE_UNAVAILABLE


async def test_partial_schema_and_unreachable_storage_are_distinguished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    for error, expected in (
        (schema_compatibility.PartialSchemaError(), RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE),
        (
            ConnectionError("postgresql://user:secret@db"),
            RunRepositoryErrorCode.STORAGE_UNAVAILABLE,
        ),
    ):
        monkeypatch.setattr(
            schema_compatibility,
            "_read_contract_version",
            AsyncMock(side_effect=error),
        )
        with pytest.raises(RunRepositoryError) as caught:
            await schema_compatibility.ensure_schema_compatible(engine)  # type: ignore[arg-type]
        assert caught.value.code is expected
        assert "secret" not in str(caught.value)


async def test_check_never_creates_or_upgrades_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _Engine()
    read = AsyncMock(return_value=1)
    monkeypatch.setattr(schema_compatibility, "_read_contract_version", read)
    monkeypatch.delattr(schema_compatibility, "command", raising=False)
    monkeypatch.delattr(schema_compatibility, "create_all", raising=False)
    await schema_compatibility.ensure_schema_compatible(engine)  # type: ignore[arg-type]
    assert read.await_count == 1


async def test_compatibility_cache_is_scoped_to_each_engine_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _Engine()
    second = _Engine()
    read = AsyncMock(return_value=1)
    monkeypatch.setattr(schema_compatibility, "_read_contract_version", read)
    await schema_compatibility.ensure_schema_compatible(first)  # type: ignore[arg-type]
    await schema_compatibility.ensure_schema_compatible(second)  # type: ignore[arg-type]
    assert read.await_count == 2


async def test_compatible_schema_does_not_mask_mixed_record_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        schema_compatibility,
        "_read_contract_version",
        AsyncMock(return_value=1),
    )
    await schema_compatibility.ensure_schema_compatible(engine)  # type: ignore[arg-type]
    with pytest.raises(RunRepositoryError) as caught:
        decode_run({"record_format_version": 0})
    assert caught.value.code is RunRepositoryErrorCode.DATA_CORRUPTION
