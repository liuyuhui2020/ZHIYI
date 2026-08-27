"""Additive 0002 Worker Lease Kernel migration contract."""

from __future__ import annotations

import ast
import hashlib

import pytest
from alembic import command
from conftest import ROOT, alembic_config, require_postgresql_database_url
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
    PostgreSQLWorkerLeaseRepository,
)
from zhiyi.application.ports.worker_lease_observability import LeaseOperationObservation
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.infrastructure.database.engine import (
    create_postgresql_engine,
    dispose_postgresql_engine,
)
from zhiyi.infrastructure.security.lease_tokens import SecureLeaseTokenGenerator

pytestmark = pytest.mark.postgresql

EXPECTED_WORKER_LEASE_OBJECTS = {
    "pk_worker_leases",
    "uq_worker_leases_tenant_claim",
    "fk_worker_leases_tenant_run_runs",
    "ck_worker_leases_worker_id_valid",
    "ck_worker_leases_claim_id_uuidv7",
    "ck_worker_leases_token_digest_length",
    "ck_worker_leases_attempt_no_positive",
    "ck_worker_leases_lease_version_positive",
    "ck_worker_leases_duration_seconds_supported",
    "ck_worker_leases_heartbeat_not_before_acquired",
    "ck_worker_leases_expiry_after_heartbeat",
    "ck_worker_leases_released_not_before_acquired",
    "ck_worker_leases_record_format_version_supported",
    "ix_worker_leases_tenant_inactive_running",
    "pk_worker_lease_claim_receipts",
    "fk_worker_lease_claim_receipts_tenant_run_runs",
    "ck_worker_lease_claim_receipts_claim_id_uuidv7",
    "ck_worker_lease_claim_receipts_replay_window_exact",
    "ck_worker_lease_claim_receipts_worker_id_valid",
    "ck_worker_lease_claim_receipts_duration_seconds_supported",
    "ck_worker_lease_claim_receipts_intent_format_version_supported",
    "ck_worker_lease_claim_receipts_intent_fingerprint_sha256",
    "ck_worker_lease_claim_receipts_outcome_supported",
    "ck_worker_lease_claim_receipts_attempt_no_positive",
    "ck_worker_lease_claim_receipts_initial_lease_version_positive",
    "ck_worker_lease_claim_receipts_replay_token_length",
    "ck_worker_lease_claim_receipts_complete_outcome",
    "ck_worker_lease_claim_receipts_record_format_version_supported",
    "ix_worker_lease_claim_receipts_cleanup",
}
IMMUTABLE_0001_SHA256 = "b46613f79b6cddea2695e249821c36c6dacc57017f03bcdce9d853ef87f500d9"


def test_0001_history_is_byte_for_byte_unchanged() -> None:
    contents = (ROOT / "migrations/versions/0001_create_run_repository.py").read_bytes()

    assert hashlib.sha256(contents).hexdigest() == IMMUTABLE_0001_SHA256


def test_0002_revision_is_self_contained_and_runtime_import_free() -> None:
    revision = ROOT / "migrations/versions/0002_create_worker_lease_kernel.py"
    tree = ast.parse(revision.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)
    assert not any(module == "zhiyi" or module.startswith("zhiyi.") for module in imported_modules)


class _MigrationTelemetry:
    def record_log(self, observation: LeaseOperationObservation) -> None:
        pass

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        pass

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        pass


async def _worker_object_names(connection: AsyncConnection) -> set[str]:
    def inspect_names(sync: Connection) -> set[str]:
        database = inspect(sync)
        names: set[str] = set()
        for table in ("worker_leases", "worker_lease_claim_receipts"):
            primary = database.get_pk_constraint(table).get("name")
            if isinstance(primary, str):
                names.add(primary)
            for getter in (
                database.get_unique_constraints,
                database.get_check_constraints,
                database.get_indexes,
                database.get_foreign_keys,
            ):
                for item in getter(table):
                    name = item.get("name")
                    if isinstance(name, str):
                        names.add(name)
        return names

    return await connection.run_sync(inspect_names)


async def test_empty_database_upgrade_head_creates_exact_two_tables_and_components() -> None:
    url = require_postgresql_database_url()
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_postgresql_engine(url)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert {
                "alembic_version",
                "zhiyi_schema_compatibility",
                "runs",
                "run_events",
                "run_command_receipts",
                "worker_leases",
                "worker_lease_claim_receipts",
            } == tables
            component_rows = (
                await connection.execute(
                    text(
                        "SELECT component, contract_version "
                        "FROM zhiyi_schema_compatibility ORDER BY component"
                    )
                )
            ).all()
            components = [(str(row[0]), int(row[1])) for row in component_rows]
            assert components == [("run_repository", 1), ("worker_lease_kernel", 1)]
            assert await _worker_object_names(connection) == EXPECTED_WORKER_LEASE_OBJECTS
    finally:
        await dispose_postgresql_engine(engine)


async def test_upgrade_from_0001_is_additive_and_downgrade_removes_only_006() -> None:
    url = require_postgresql_database_url()
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "0001_run_repository")
    engine = create_postgresql_engine(url)
    try:
        async with engine.connect() as connection:
            before = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert "runs" in before
        assert "worker_leases" not in before
    finally:
        await dispose_postgresql_engine(engine)

    command.upgrade(config, "head")
    engine = create_postgresql_engine(url)
    try:
        async with engine.connect() as connection:
            after = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        assert {"worker_leases", "worker_lease_claim_receipts"} <= after
    finally:
        await dispose_postgresql_engine(engine)

    command.downgrade(config, "0001_run_repository")
    engine = create_postgresql_engine(url)
    try:
        async with engine.connect() as connection:
            downgraded = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            components = (
                (
                    await connection.execute(
                        text("SELECT component FROM zhiyi_schema_compatibility ORDER BY component")
                    )
                )
                .scalars()
                .all()
            )
        assert {"runs", "run_events", "run_command_receipts"} <= downgraded
        assert "worker_leases" not in downgraded
        assert "worker_lease_claim_receipts" not in downgraded
        assert components == ["run_repository"]
    finally:
        await dispose_postgresql_engine(engine)
        command.upgrade(config, "head")


async def test_expression_and_cleanup_indexes_have_reviewed_column_order() -> None:
    url = require_postgresql_database_url()
    command.upgrade(alembic_config(url), "head")
    engine = create_postgresql_engine(url)
    try:
        async with engine.connect() as connection:
            definition_rows = await connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE indexname IN "
                    "('ix_worker_leases_tenant_inactive_running', "
                    "'ix_worker_lease_claim_receipts_cleanup')"
                )
            )
            definitions: dict[str, str] = {
                str(row[0]): str(row[1]) for row in definition_rows.all()
            }
        assert (
            "tenant_id, COALESCE(released_at, lease_expires_at), run_id"
            in definitions["ix_worker_leases_tenant_inactive_running"]
        )
        assert (
            "replay_expires_at, tenant_id, claim_id"
            in definitions["ix_worker_lease_claim_receipts_cleanup"]
        )
    finally:
        await dispose_postgresql_engine(engine)


async def test_0001_binary_remains_usable_while_006_waits_for_additive_0002() -> None:
    url = require_postgresql_database_url()
    config = alembic_config(url)
    command.downgrade(config, "0001_run_repository")
    engine = create_postgresql_engine(url)
    try:
        run_repository = PostgreSQLRunRepository(engine)
        assert (
            await run_repository.load(
                TenantId("tenant-rolling-0001"),
                RunId("run-rolling-0001"),
            )
            is None
        )
        lease_repository = PostgreSQLWorkerLeaseRepository(
            engine,
            telemetry=_MigrationTelemetry(),
            token_generator=SecureLeaseTokenGenerator(),
        )
        with pytest.raises(WorkerLeaseError) as incompatible:
            await lease_repository.issue_claim_id()
        assert incompatible.value.code is WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE

        command.upgrade(config, "head")
        assert (
            await run_repository.load(
                TenantId("tenant-rolling-0001"),
                RunId("run-rolling-0001"),
            )
            is None
        )
        assert (await lease_repository.issue_claim_id()).value.version == 7
    finally:
        await dispose_postgresql_engine(engine)
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    ("damage", "repair"),
    [
        (
            "ALTER TABLE worker_leases RENAME TO worker_leases_missing",
            "ALTER TABLE worker_leases_missing RENAME TO worker_leases",
        ),
        (
            "ALTER INDEX ix_worker_leases_tenant_inactive_running "
            "RENAME TO ix_worker_leases_missing",
            "ALTER INDEX ix_worker_leases_missing "
            "RENAME TO ix_worker_leases_tenant_inactive_running",
        ),
        (
            "ALTER TABLE worker_leases RENAME CONSTRAINT "
            "ck_worker_leases_token_digest_length TO ck_worker_leases_missing",
            "ALTER TABLE worker_leases RENAME CONSTRAINT "
            "ck_worker_leases_missing TO ck_worker_leases_token_digest_length",
        ),
    ],
    ids=["table", "index", "constraint"],
)
async def test_partial_0002_physical_inventory_fails_closed_before_business_access(
    damage: str,
    repair: str,
) -> None:
    url = require_postgresql_database_url()
    command.upgrade(alembic_config(url), "head")
    setup_engine = create_postgresql_engine(url)
    try:
        async with setup_engine.begin() as connection:
            await connection.execute(text(damage))
    finally:
        await dispose_postgresql_engine(setup_engine)

    fresh_engine = create_postgresql_engine(url)
    try:
        repository = PostgreSQLWorkerLeaseRepository(
            fresh_engine,
            telemetry=_MigrationTelemetry(),
            token_generator=SecureLeaseTokenGenerator(),
        )
        with pytest.raises(WorkerLeaseError) as incompatible:
            await repository.issue_claim_id()
        assert incompatible.value.code is WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE
    finally:
        await dispose_postgresql_engine(fresh_engine)
        repair_engine = create_postgresql_engine(url)
        try:
            async with repair_engine.begin() as connection:
                await connection.execute(text(repair))
        finally:
            await dispose_postgresql_engine(repair_engine)
