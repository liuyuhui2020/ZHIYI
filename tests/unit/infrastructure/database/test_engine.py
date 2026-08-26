"""Async PostgreSQL engine construction tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zhiyi.infrastructure.database import engine as engine_module


def test_engine_uses_one_bounded_secret_safe_async_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create(url: str, **options: object) -> object:
        captured["url"] = url
        captured.update(options)
        return sentinel

    monkeypatch.setattr(engine_module, "create_async_engine", fake_create)
    result = engine_module.create_postgresql_engine(
        "postgresql+psycopg://user:super-secret@db.example/zhiyi",
        pool_size=20,
        pool_timeout_seconds=3,
        connect_timeout_seconds=4,
        statement_timeout_ms=3210,
    )

    assert result is sentinel
    assert captured["pool_size"] == 20
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 3
    assert captured["pool_pre_ping"] is True
    assert captured["hide_parameters"] is True
    assert captured["echo"] is False
    assert captured["isolation_level"] == "READ COMMITTED"
    assert captured["connect_args"] == {
        "connect_timeout": 4,
        "options": "-c statement_timeout=3210",
    }


@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_engine_rejects_invalid_statement_timeout(value: object) -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        engine_module.create_postgresql_engine(
            "postgresql+psycopg://user:secret@db.example/zhiyi",
            statement_timeout_ms=value,  # type: ignore[arg-type]
        )


def test_invalid_url_error_never_echoes_credentials() -> None:
    secret = "do-not-leak"
    with pytest.raises(ValueError) as caught:
        engine_module.create_postgresql_engine(f"postgresql://user:{secret}@db/zhiyi")
    assert secret not in str(caught.value)


async def test_dispose_awaits_engine_once() -> None:
    fake_engine = AsyncMock()
    await engine_module.dispose_postgresql_engine(fake_engine)
    fake_engine.dispose.assert_awaited_once_with()
