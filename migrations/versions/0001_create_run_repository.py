"""Create the RunRepository schema contract.

Revision ID: 0001_run_repository
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_run_repository"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the reviewed empty-database schema in dependency order."""

    op.create_table(
        "zhiyi_schema_compatibility",
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "contract_version > 0",
            name=op.f("ck_zhiyi_schema_compatibility_contract_version_positive"),
        ),
        sa.PrimaryKeyConstraint("component", name="pk_zhiyi_schema_compatibility"),
    )
    op.create_table(
        "runs",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("agent_version_id", sa.String(length=128), nullable=False),
        sa.Column("agent_build_digest", sa.String(length=71), nullable=False),
        sa.Column("run_status", sa.String(length=32), nullable=False),
        sa.Column("run_version", sa.String(), nullable=False),
        sa.Column("next_event_sequence", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_format_version", sa.SmallInteger(), nullable=False),
        sa.Column("snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "agent_build_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_runs_agent_build_digest_sha256"),
        ),
        sa.CheckConstraint(
            "next_event_sequence ~ '^[1-9][0-9]*$'",
            name=op.f("ck_runs_next_event_sequence_canonical"),
        ),
        sa.CheckConstraint(
            "last_observed_at >= updated_at",
            name=op.f("ck_runs_observed_not_before_updated"),
        ),
        sa.CheckConstraint(
            "run_status IN ('queued','running','waiting_approval','waiting_resolution',"
            "'succeeded','failed','cancelled','limit_exceeded')",
            name=op.f("ck_runs_run_status_supported"),
        ),
        sa.CheckConstraint(
            "run_version ~ '^[1-9][0-9]*$'",
            name=op.f("ck_runs_run_version_canonical"),
        ),
        sa.CheckConstraint(
            "snapshot_format_version = 1",
            name=op.f("ck_runs_snapshot_format_version_supported"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_runs_updated_not_before_created"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "run_id", name="pk_runs"),
    )
    op.create_index(
        "ix_runs_tenant_status_updated_run",
        "runs",
        ["tenant_id", "run_status", "updated_at", "run_id"],
        unique=False,
    )
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("sequence_value", sa.String(), nullable=False),
        sa.Column(
            "sequence_digits",
            sa.Integer(),
            sa.Computed("char_length(sequence_value)", persisted=True),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_version", sa.SmallInteger(), nullable=False),
        sa.Column("record_format_version", sa.SmallInteger(), nullable=False),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('run.created','run.started','run.waiting_approval',"
            "'run.waiting_resolution','run.resumed','run.budget_consumed','run.succeeded',"
            "'run.failed','run.cancelled','run.limit_exceeded')",
            name=op.f("ck_run_events_event_type_supported"),
        ),
        sa.CheckConstraint(
            "payload_version = 1",
            name=op.f("ck_run_events_payload_version_supported"),
        ),
        sa.CheckConstraint(
            "record_format_version = 1",
            name=op.f("ck_run_events_record_format_version_supported"),
        ),
        sa.CheckConstraint(
            "sequence_value ~ '^[1-9][0-9]*$'",
            name=op.f("ck_run_events_sequence_value_canonical"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.run_id"],
            name="fk_run_events_tenant_run_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_run_events"),
        sa.UniqueConstraint(
            "event_id",
            "tenant_id",
            "run_id",
            name="uq_run_events_event_tenant_run",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence_value",
            name="uq_run_events_tenant_run_sequence",
        ),
    )
    op.create_index(
        "ix_run_events_tenant_run_sequence_cursor",
        "run_events",
        ["tenant_id", "run_id", "sequence_digits", "sequence_value"],
        unique=False,
    )
    op.create_table(
        "run_command_receipts",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("intent_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("resulting_status", sa.String(length=32), nullable=False),
        sa.Column("resulting_version", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_format_version", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "command_type IN ('cancel_run','consume_budget','create_run','enforce_deadline',"
            "'fail_run','resume_run','start_run','succeed_run','wait_for_approval',"
            "'wait_for_resolution')",
            name=op.f("ck_run_command_receipts_command_type_supported"),
        ),
        sa.CheckConstraint(
            "intent_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_run_command_receipts_intent_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "record_format_version = 1",
            name=op.f("ck_run_command_receipts_record_format_version_supported"),
        ),
        sa.CheckConstraint(
            "resulting_status IN ('queued','running','waiting_approval','waiting_resolution',"
            "'succeeded','failed','cancelled','limit_exceeded')",
            name=op.f("ck_run_command_receipts_resulting_status_supported"),
        ),
        sa.CheckConstraint(
            "resulting_version ~ '^[1-9][0-9]*$'",
            name=op.f("ck_run_command_receipts_resulting_version_canonical"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "tenant_id", "run_id"],
            ["run_events.event_id", "run_events.tenant_id", "run_events.run_id"],
            name="fk_run_command_receipts_event_tenant_run_events",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.run_id"],
            name="fk_run_command_receipts_tenant_run_runs",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "command_id",
            name="pk_run_command_receipts",
        ),
    )
    op.create_index(
        "ix_run_command_receipts_tenant_run_created_command",
        "run_command_receipts",
        ["tenant_id", "run_id", "created_at", "command_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO zhiyi_schema_compatibility "
            "(component, contract_version, installed_at) "
            "VALUES ('run_repository', 1, CURRENT_TIMESTAMP)"
        )
    )


def downgrade() -> None:
    """Destroy all RunRepository data; disposable/empty environments only."""

    op.drop_index(
        "ix_run_command_receipts_tenant_run_created_command",
        table_name="run_command_receipts",
    )
    op.drop_table("run_command_receipts")
    op.drop_index(
        "ix_run_events_tenant_run_sequence_cursor",
        table_name="run_events",
    )
    op.drop_table("run_events")
    op.drop_index("ix_runs_tenant_status_updated_run", table_name="runs")
    op.drop_table("runs")
    op.drop_table("zhiyi_schema_compatibility")
