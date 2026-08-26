"""Reviewed SQLAlchemy Core metadata for the PostgreSQL RunRepository."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON

SCHEMA_CONTRACT_VERSION = 1
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

schema_compatibility = Table(
    "zhiyi_schema_compatibility",
    metadata,
    Column("component", String(64), nullable=False),
    Column("contract_version", Integer, nullable=False),
    Column("installed_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("component", name="pk_zhiyi_schema_compatibility"),
    CheckConstraint("contract_version > 0", name="contract_version_positive"),
)

runs = Table(
    "runs",
    metadata,
    Column("tenant_id", String(128), nullable=False),
    Column("run_id", String(128), nullable=False),
    Column("task_id", String(128), nullable=False),
    Column("agent_id", String(128), nullable=False),
    Column("agent_version_id", String(128), nullable=False),
    Column("agent_build_digest", String(71), nullable=False),
    Column("run_status", String(32), nullable=False),
    Column("run_version", String, nullable=False),
    Column("next_event_sequence", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_observed_at", DateTime(timezone=True), nullable=False),
    Column("snapshot_format_version", SmallInteger, nullable=False),
    Column("snapshot", JSON, nullable=False),
    PrimaryKeyConstraint("tenant_id", "run_id", name="pk_runs"),
    CheckConstraint("run_version ~ '^[1-9][0-9]*$'", name="run_version_canonical"),
    CheckConstraint(
        "next_event_sequence ~ '^[1-9][0-9]*$'",
        name="next_event_sequence_canonical",
    ),
    CheckConstraint("snapshot_format_version = 1", name="snapshot_format_version_supported"),
    CheckConstraint(
        "agent_build_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="agent_build_digest_sha256",
    ),
    CheckConstraint("updated_at >= created_at", name="updated_not_before_created"),
    CheckConstraint("last_observed_at >= updated_at", name="observed_not_before_updated"),
    CheckConstraint(
        "run_status IN ('queued','running','waiting_approval','waiting_resolution',"
        "'succeeded','failed','cancelled','limit_exceeded')",
        name="run_status_supported",
    ),
)
Index(
    "ix_runs_tenant_status_updated_run",
    runs.c.tenant_id,
    runs.c.run_status,
    runs.c.updated_at,
    runs.c.run_id,
)

run_events = Table(
    "run_events",
    metadata,
    Column("event_id", String(128), nullable=False),
    Column("tenant_id", String(128), nullable=False),
    Column("run_id", String(128), nullable=False),
    Column("sequence_value", String, nullable=False),
    Column("sequence_digits", Integer, Computed("char_length(sequence_value)", persisted=True)),
    Column("event_type", String(64), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("payload_version", SmallInteger, nullable=False),
    Column("record_format_version", SmallInteger, nullable=False),
    Column("payload", JSON, nullable=False),
    PrimaryKeyConstraint("event_id", name="pk_run_events"),
    UniqueConstraint(
        "tenant_id",
        "run_id",
        "sequence_value",
        name="uq_run_events_tenant_run_sequence",
    ),
    UniqueConstraint(
        "event_id",
        "tenant_id",
        "run_id",
        name="uq_run_events_event_tenant_run",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "run_id"],
        ["runs.tenant_id", "runs.run_id"],
        name="fk_run_events_tenant_run_runs",
        ondelete="RESTRICT",
    ),
    CheckConstraint("sequence_value ~ '^[1-9][0-9]*$'", name="sequence_value_canonical"),
    CheckConstraint("payload_version = 1", name="payload_version_supported"),
    CheckConstraint("record_format_version = 1", name="record_format_version_supported"),
    CheckConstraint(
        "event_type IN ('run.created','run.started','run.waiting_approval',"
        "'run.waiting_resolution','run.resumed','run.budget_consumed','run.succeeded',"
        "'run.failed','run.cancelled','run.limit_exceeded')",
        name="event_type_supported",
    ),
)
Index(
    "ix_run_events_tenant_run_sequence_cursor",
    run_events.c.tenant_id,
    run_events.c.run_id,
    run_events.c.sequence_digits,
    run_events.c.sequence_value,
)

run_command_receipts = Table(
    "run_command_receipts",
    metadata,
    Column("tenant_id", String(128), nullable=False),
    Column("command_id", String(128), nullable=False),
    Column("run_id", String(128), nullable=False),
    Column("command_type", String(64), nullable=False),
    Column("intent_fingerprint", String(71), nullable=False),
    Column("resulting_status", String(32), nullable=False),
    Column("resulting_version", String, nullable=False),
    Column("event_id", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("record_format_version", SmallInteger, nullable=False),
    PrimaryKeyConstraint("tenant_id", "command_id", name="pk_run_command_receipts"),
    ForeignKeyConstraint(
        ["tenant_id", "run_id"],
        ["runs.tenant_id", "runs.run_id"],
        name="fk_run_command_receipts_tenant_run_runs",
        deferrable=True,
        initially="DEFERRED",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["event_id", "tenant_id", "run_id"],
        ["run_events.event_id", "run_events.tenant_id", "run_events.run_id"],
        name="fk_run_command_receipts_event_tenant_run_events",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint("resulting_version ~ '^[1-9][0-9]*$'", name="resulting_version_canonical"),
    CheckConstraint("record_format_version = 1", name="record_format_version_supported"),
    CheckConstraint(
        "intent_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="intent_fingerprint_sha256",
    ),
    CheckConstraint(
        "command_type IN ('cancel_run','consume_budget','create_run','enforce_deadline',"
        "'fail_run','resume_run','start_run','succeed_run','wait_for_approval',"
        "'wait_for_resolution')",
        name="command_type_supported",
    ),
    CheckConstraint(
        "resulting_status IN ('queued','running','waiting_approval','waiting_resolution',"
        "'succeeded','failed','cancelled','limit_exceeded')",
        name="resulting_status_supported",
    ),
)
Index(
    "ix_run_command_receipts_tenant_run_created_command",
    run_command_receipts.c.tenant_id,
    run_command_receipts.c.run_id,
    run_command_receipts.c.created_at,
    run_command_receipts.c.command_id,
)
