"""Alembic lifecycle, destructive guard, and logical-restore acceptance."""

from __future__ import annotations

import ast
import os
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from alembic import command
from conftest import ROOT, alembic_config, require_postgresql_database_url
from psycopg import sql
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
from zhiyi.application.ports.run_repository import CommandReceipt
from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.budget import RunBudget
from zhiyi.domain.runs.events import RunEvent
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
from zhiyi.infrastructure.database.schema_compatibility import ensure_schema_compatible

pytestmark = pytest.mark.postgresql
RESTORE_DATABASE = "zhiyi_005_restore"
EXPECTED_PHYSICAL_OBJECT_NAMES = {
    "ck_run_command_receipts_command_type_supported",
    "ck_run_command_receipts_intent_fingerprint_sha256",
    "ck_run_command_receipts_record_format_version_supported",
    "ck_run_command_receipts_resulting_status_supported",
    "ck_run_command_receipts_resulting_version_canonical",
    "ck_run_events_event_type_supported",
    "ck_run_events_payload_version_supported",
    "ck_run_events_record_format_version_supported",
    "ck_run_events_sequence_value_canonical",
    "ck_runs_agent_build_digest_sha256",
    "ck_runs_next_event_sequence_canonical",
    "ck_runs_observed_not_before_updated",
    "ck_runs_run_status_supported",
    "ck_runs_run_version_canonical",
    "ck_runs_snapshot_format_version_supported",
    "ck_runs_updated_not_before_created",
    "ck_zhiyi_schema_compatibility_contract_version_positive",
    "fk_run_command_receipts_event_tenant_run_events",
    "fk_run_command_receipts_tenant_run_runs",
    "fk_run_events_tenant_run_runs",
    "ix_run_command_receipts_tenant_run_created_command",
    "ix_run_events_tenant_run_sequence_cursor",
    "ix_runs_tenant_status_updated_run",
    "pk_run_command_receipts",
    "pk_run_events",
    "pk_runs",
    "pk_zhiyi_schema_compatibility",
    "uq_run_events_event_tenant_run",
    "uq_run_events_tenant_run_sequence",
}


def _physical_object_names(sync: Connection) -> set[str]:
    database = inspect(sync)
    names: set[str] = set()
    for table in (
        "zhiyi_schema_compatibility",
        "runs",
        "run_events",
        "run_command_receipts",
    ):
        primary_key = database.get_pk_constraint(table)
        primary_name = primary_key.get("name")
        if isinstance(primary_name, str):
            names.add(primary_name)
        for getter in (
            database.get_unique_constraints,
            database.get_check_constraints,
            database.get_indexes,
            database.get_foreign_keys,
        ):
            for item in getter(table):
                item_name = item.get("name")
                if isinstance(item_name, str):
                    names.add(item_name)
    return names


def test_initial_revision_is_self_contained_and_runtime_import_free() -> None:
    revision = ROOT / "migrations/versions/0001_create_run_repository.py"
    tree = ast.parse(revision.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)
    assert not any(module == "zhiyi" or module.startswith("zhiyi.") for module in imported_modules)


def _connection_info(database_url: str, *, database: str | None = None) -> str:
    parsed = make_url(database_url)
    return parsed.set(
        drivername="postgresql", database=database or parsed.database
    ).render_as_string(hide_password=False)


def _assert_disposable_identity(database_url: str) -> None:
    with psycopg.connect(_connection_info(database_url)) as connection:
        identity = connection.execute("SELECT current_database(), current_user").fetchone()
    assert identity == ("zhiyi_test", "zhiyi_test")
    assert make_url(database_url).host in {"127.0.0.1", "localhost", "postgresql"}


def _postgres_container() -> str:
    configured = os.environ.get("ZHIYI_TEST_POSTGRESQL_CONTAINER")
    if configured:
        return configured
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.test.yaml", "ps", "-q", "postgresql"],
        check=True,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )
    container = result.stdout.strip()
    if not container:
        pytest.fail("the disposable PostgreSQL container identity could not be resolved")
    return container


def _logical_dump(container: str) -> bytes:
    result = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "pg_dump",
            "-U",
            "zhiyi_test",
            "-d",
            "zhiyi_test",
            "-Fc",
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _restore_dump(container: str, dump: bytes) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "pg_restore",
            "-U",
            "zhiyi_test",
            "-d",
            RESTORE_DATABASE,
            "--exit-on-error",
        ],
        check=True,
        input=dump,
        capture_output=True,
    )


def _replace_database(database_url: str, database: str) -> str:
    return make_url(database_url).set(database=database).render_as_string(hide_password=False)


def _representative_run() -> tuple[Run, CommandReceipt, tuple[RunEvent, ...]]:
    now = datetime(2026, 8, 25, 11, 0, tzinfo=UTC)
    tenant = TenantId("tenant-migration")
    mutation = Run.create(
        tenant_id=tenant,
        run_id=RunId("run-migration"),
        task_id=TaskId("task-migration"),
        agent_version=AgentVersionRef(
            tenant_id=tenant,
            agent_id=AgentId("agent-migration"),
            version_id=AgentVersionId("version-migration"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=now + timedelta(days=1),
            max_steps=10,
            max_model_calls=10,
            max_tool_calls=10,
            max_input_tokens=10,
            max_output_tokens=10,
            max_total_tokens=20,
            max_cost=Decimal("10.0001"),
            currency="USD",
        ),
        observed_at=now,
        event_id=EventId("event-migration"),
    )
    receipt = CommandReceipt(
        tenant_id=tenant,
        command_id=CommandId("command-migration"),
        run_id=mutation.run.run_id,
        command_type="create_run",
        intent_fingerprint="sha256:" + "b" * 64,
        resulting_status=mutation.run.status,
        resulting_version=1,
        event_ids=(mutation.events[0].event_id,),
        created_at=now,
    )
    return mutation.run, receipt, mutation.events


async def _fact_digests(engine: AsyncEngine) -> tuple[str | None, ...]:
    statements = (
        "SELECT md5(COALESCE(string_agg(row_to_json(fact)::text, '' "
        "ORDER BY tenant_id, run_id), '')) FROM runs fact",
        "SELECT md5(COALESCE(string_agg(row_to_json(fact)::text, '' "
        "ORDER BY event_id), '')) FROM run_events fact",
        "SELECT md5(COALESCE(string_agg(row_to_json(fact)::text, '' "
        "ORDER BY tenant_id, command_id), '')) FROM run_command_receipts fact",
    )
    async with engine.connect() as connection:
        digests: list[str | None] = []
        for statement in statements:
            digests.append(await connection.scalar(text(statement)))
        return tuple(digests)


async def test_upgrade_head_creates_named_schema_and_compatibility_row() -> None:
    url = require_postgresql_database_url()
    _assert_disposable_identity(url)
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
            } <= tables
            version = await connection.scalar(
                text(
                    "SELECT contract_version FROM zhiyi_schema_compatibility "
                    "WHERE component = 'run_repository'"
                )
            )
            assert version == 1
            names = await connection.run_sync(_physical_object_names)
            assert names == EXPECTED_PHYSICAL_OBJECT_NAMES
    finally:
        await dispose_postgresql_engine(engine)


def test_repeated_upgrade_head_check_and_disposable_downgrade_base() -> None:
    url = require_postgresql_database_url()
    _assert_disposable_identity(url)
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.current(config, check_heads=True)
    command.check(config)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


async def test_application_compatibility_check_executes_no_ddl() -> None:
    engine = create_postgresql_engine(require_postgresql_database_url())
    ddl_statements: list[str] = []

    def capture_ddl(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ", "TRUNCATE ")):
            ddl_statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_ddl)
    try:
        await ensure_schema_compatible(engine)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_ddl)
        await dispose_postgresql_engine(engine)
    assert ddl_statements == []


async def test_seed_representative_data_dump_restore_and_domain_round_trip(
    postgresql_engine: AsyncEngine,
) -> None:
    url = require_postgresql_database_url()
    _assert_disposable_identity(url)
    expected_run, receipt, events = _representative_run()
    repository = PostgreSQLRunRepository(postgresql_engine)
    await repository.commit(
        expected_version=0,
        updated_run=expected_run,
        new_events=events,
        receipt=receipt,
    )
    source_digests = await _fact_digests(postgresql_engine)
    container = _postgres_container()
    dump = _logical_dump(container)
    assert dump.startswith(b"PGDMP")

    admin = _connection_info(url, database="postgres")
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(RESTORE_DATABASE))
        )
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(RESTORE_DATABASE)))
    _restore_dump(container, dump)

    restored_url = _replace_database(url, RESTORE_DATABASE)
    command.current(alembic_config(restored_url), check_heads=True)
    restored_engine = create_postgresql_engine(restored_url)
    try:
        restored = PostgreSQLRunRepository(restored_engine)
        assert await restored.load(expected_run.tenant_id, expected_run.run_id) == expected_run
        assert await restored.list_events(expected_run.tenant_id, expected_run.run_id) == events
        replay = await restored.find_command(
            receipt.tenant_id,
            receipt.command_id,
            receipt.intent_fingerprint,
        )
        assert replay is not None and replay.receipt == receipt
        restored_counts: list[int | None] = []
        async with restored_engine.connect() as connection:
            for table in ("runs", "run_events", "run_command_receipts"):
                restored_counts.append(
                    await connection.scalar(text(f"SELECT count(*) FROM {table}"))
                )
        assert restored_counts == [1, 1, 1]
        assert await _fact_digests(restored_engine) == source_digests
    finally:
        await dispose_postgresql_engine(restored_engine)
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(RESTORE_DATABASE))
            )
