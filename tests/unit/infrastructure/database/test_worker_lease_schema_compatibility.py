"""Component-aware compatibility gates for the Worker Lease Kernel."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import ProgrammingError

from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.infrastructure.database import schema_compatibility

COMPLETE_PHYSICAL_INVENTORY = frozenset(
    {
        "worker_leases",
        "worker_lease_claim_receipts",
        "pk_worker_leases",
        "pk_worker_lease_claim_receipts",
        "ix_worker_leases_tenant_inactive_running",
        "ix_worker_lease_claim_receipts_cleanup",
        "ck_worker_leases_token_digest_length",
    }
)


@pytest.fixture(autouse=True)
def complete_physical_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        schema_compatibility,
        "_worker_lease_required_physical_inventory",
        lambda: COMPLETE_PHYSICAL_INVENTORY,
        raising=False,
    )
    monkeypatch.setattr(
        schema_compatibility,
        "_read_worker_lease_physical_inventory",
        AsyncMock(return_value=COMPLETE_PHYSICAL_INVENTORY),
        raising=False,
    )


class _Engine:
    pass


async def test_cache_is_scoped_by_engine_and_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _Engine()
    second = _Engine()
    read = AsyncMock(return_value=1)
    monkeypatch.setattr(schema_compatibility, "_read_contract_version", read)

    await schema_compatibility.ensure_schema_compatible(first)  # type: ignore[arg-type]
    await schema_compatibility.ensure_worker_lease_schema_compatible(first)  # type: ignore[arg-type]
    await schema_compatibility.ensure_schema_compatible(first)  # type: ignore[arg-type]
    await schema_compatibility.ensure_worker_lease_schema_compatible(first)  # type: ignore[arg-type]
    await schema_compatibility.ensure_worker_lease_schema_compatible(second)  # type: ignore[arg-type]

    assert read.await_args_list == [
        ((first,), {"component": "run_repository"}),
        ((first,), {"component": "worker_lease_kernel"}),
        ((second,), {"component": "worker_lease_kernel"}),
    ]


async def test_cached_actual_version_is_rechecked_against_each_accepted_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    read = AsyncMock(return_value=2)
    monkeypatch.setattr(schema_compatibility, "_read_contract_version", read)

    await schema_compatibility.ensure_component_schema_compatible(
        engine,  # type: ignore[arg-type]
        component="worker_lease_kernel",
        accepted_versions=frozenset({1, 2}),
    )
    with pytest.raises(schema_compatibility.SchemaIncompatibleError):
        await schema_compatibility.ensure_component_schema_compatible(
            engine,  # type: ignore[arg-type]
            component="worker_lease_kernel",
            accepted_versions=frozenset({1}),
        )

    read.assert_awaited_once_with(engine, component="worker_lease_kernel")


@pytest.mark.parametrize("version", [None, 0, 2, True, "1", 1.0])
async def test_worker_component_requires_exact_integer_contract_one(
    monkeypatch: pytest.MonkeyPatch,
    version: object,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        schema_compatibility,
        "_read_contract_version",
        AsyncMock(return_value=version),
    )

    with pytest.raises(WorkerLeaseError) as caught:
        await schema_compatibility.ensure_worker_lease_schema_compatible(
            engine  # type: ignore[arg-type]
        )

    assert caught.value.code is WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("postgresql://user:secret@host/db SELECT token")
        self.sqlstate = sqlstate


class _FailingConnection:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate

    async def __aenter__(self) -> _FailingConnection:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def scalar(self, statement: object, parameters: object = None) -> object:
        raise ProgrammingError("statement", {}, _DriverError(self.sqlstate))


class _FailingEngine:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate

    def connect(self) -> _FailingConnection:
        return _FailingConnection(self.sqlstate)


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("42P01", WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE),
        ("42703", WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE),
        ("42501", WorkerLeaseErrorCode.STORAGE_UNAVAILABLE),
        ("08006", WorkerLeaseErrorCode.STORAGE_UNAVAILABLE),
    ],
)
async def test_partial_schema_precedes_business_access_but_operational_failures_do_not(
    sqlstate: str,
    expected: WorkerLeaseErrorCode,
) -> None:
    with pytest.raises(WorkerLeaseError) as caught:
        await schema_compatibility.ensure_worker_lease_schema_compatible(
            _FailingEngine(sqlstate)  # type: ignore[arg-type]
        )

    assert caught.value.code is expected
    assert "secret" not in str(caught.value)
    assert "SELECT" not in repr(caught.value)


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def __aenter__(self) -> _RecordingConnection:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def scalar(self, statement: object, parameters: object = None) -> int:
        self.calls.append((str(statement), parameters))
        return 1


class _RecordingEngine:
    def __init__(self) -> None:
        self.connection = _RecordingConnection()

    def connect(self) -> _RecordingConnection:
        return self.connection


async def test_component_check_executes_one_bound_select_and_zero_ddl() -> None:
    engine = _RecordingEngine()

    await schema_compatibility.ensure_worker_lease_schema_compatible(
        engine  # type: ignore[arg-type]
    )

    assert len(engine.connection.calls) == 1
    statement, parameters = engine.connection.calls[0]
    assert statement.strip().upper().startswith("SELECT")
    assert "INSERT" not in statement.upper()
    assert "UPDATE" not in statement.upper()
    assert "CREATE" not in statement.upper()
    assert "ALTER" not in statement.upper()
    assert parameters == {"component": "worker_lease_kernel"}


async def test_worker_component_requires_complete_physical_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        schema_compatibility,
        "_read_contract_version",
        AsyncMock(return_value=1),
    )
    inventory = AsyncMock(
        return_value=COMPLETE_PHYSICAL_INVENTORY - {"ck_worker_leases_token_digest_length"}
    )
    monkeypatch.setattr(
        schema_compatibility,
        "_read_worker_lease_physical_inventory",
        inventory,
        raising=False,
    )

    with pytest.raises(WorkerLeaseError) as incompatible:
        await schema_compatibility.ensure_worker_lease_schema_compatible(
            engine  # type: ignore[arg-type]
        )

    assert incompatible.value.code is WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE
    inventory.assert_awaited_once_with(engine)
