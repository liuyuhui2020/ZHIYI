"""Create the additive Worker Lease Kernel schema contract.

Revision ID: 0002_worker_lease_kernel
Revises: 0001_run_repository
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_worker_lease_kernel"
down_revision: str | None = "0001_run_repository"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add lease coordination facts without changing any 005 object."""

    op.create_table(
        "worker_leases",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(), nullable=False),
        sa.Column("attempt_no", sa.BigInteger(), nullable=False),
        sa.Column("lease_version", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_format_version", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "attempt_no > 0",
            name=op.f("ck_worker_leases_attempt_no_positive"),
        ),
        sa.CheckConstraint(
            "uuid_extract_version(claim_id) = 7 AND (get_byte(uuid_send(claim_id), 8) & 192) = 128",
            name=op.f("ck_worker_leases_claim_id_uuidv7"),
        ),
        sa.CheckConstraint(
            "duration_seconds BETWEEN 10 AND 30",
            name=op.f("ck_worker_leases_duration_seconds_supported"),
        ),
        sa.CheckConstraint(
            "lease_expires_at > heartbeat_at",
            name=op.f("ck_worker_leases_expiry_after_heartbeat"),
        ),
        sa.CheckConstraint(
            "heartbeat_at >= acquired_at",
            name=op.f("ck_worker_leases_heartbeat_not_before_acquired"),
        ),
        sa.CheckConstraint(
            "lease_version > 0",
            name=op.f("ck_worker_leases_lease_version_positive"),
        ),
        sa.CheckConstraint(
            "record_format_version = 1",
            name=op.f("ck_worker_leases_record_format_version_supported"),
        ),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= acquired_at",
            name=op.f("ck_worker_leases_released_not_before_acquired"),
        ),
        sa.CheckConstraint(
            "octet_length(token_digest) = 32",
            name=op.f("ck_worker_leases_token_digest_length"),
        ),
        sa.CheckConstraint(
            "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name=op.f("ck_worker_leases_worker_id_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.run_id"],
            name="fk_worker_leases_tenant_run_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "run_id", name="pk_worker_leases"),
        sa.UniqueConstraint(
            "tenant_id",
            "claim_id",
            name="uq_worker_leases_tenant_claim",
        ),
    )
    op.create_index(
        "ix_worker_leases_tenant_inactive_running",
        "worker_leases",
        ["tenant_id", sa.text("COALESCE(released_at, lease_expires_at)"), "run_id"],
        unique=False,
    )
    op.create_table(
        "worker_lease_claim_receipts",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replay_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("intent_format_version", sa.SmallInteger(), nullable=False),
        sa.Column("intent_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("attempt_no", sa.BigInteger(), nullable=True),
        sa.Column("initial_lease_version", sa.BigInteger(), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_token", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_format_version", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "attempt_no IS NULL OR attempt_no > 0",
            name=op.f("ck_worker_lease_claim_receipts_attempt_no_positive"),
        ),
        sa.CheckConstraint(
            "uuid_extract_version(claim_id) = 7 AND (get_byte(uuid_send(claim_id), 8) & 192) = 128",
            name=op.f("ck_worker_lease_claim_receipts_claim_id_uuidv7"),
        ),
        sa.CheckConstraint(
            "(outcome = 'no_work' AND run_id IS NULL AND attempt_no IS NULL "
            "AND initial_lease_version IS NULL AND lease_acquired_at IS NULL "
            "AND lease_expires_at IS NULL AND replay_token IS NULL) OR "
            "(outcome = 'claimed' AND run_id IS NOT NULL AND attempt_no IS NOT NULL "
            "AND initial_lease_version IS NOT NULL AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at > lease_acquired_at AND replay_token IS NOT NULL)",
            name=op.f("ck_worker_lease_claim_receipts_complete_outcome"),
        ),
        sa.CheckConstraint(
            "duration_seconds BETWEEN 10 AND 30",
            name=op.f("ck_worker_lease_claim_receipts_duration_seconds_supported"),
        ),
        sa.CheckConstraint(
            "initial_lease_version IS NULL OR initial_lease_version > 0",
            name=op.f("ck_worker_lease_claim_receipts_initial_lease_version_positive"),
        ),
        sa.CheckConstraint(
            "intent_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_worker_lease_claim_receipts_intent_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "intent_format_version = 1",
            name=op.f("ck_worker_lease_claim_receipts_intent_format_version_supported"),
        ),
        sa.CheckConstraint(
            "outcome IN ('claimed','no_work')",
            name=op.f("ck_worker_lease_claim_receipts_outcome_supported"),
        ),
        sa.CheckConstraint(
            "record_format_version = 1",
            name=op.f("ck_worker_lease_claim_receipts_record_format_version_supported"),
        ),
        sa.CheckConstraint(
            "replay_token IS NULL OR octet_length(replay_token) = 32",
            name=op.f("ck_worker_lease_claim_receipts_replay_token_length"),
        ),
        sa.CheckConstraint(
            "claim_issued_at = uuid_extract_timestamp(claim_id) "
            "AND replay_expires_at = claim_issued_at + interval '24 hours'",
            name=op.f("ck_worker_lease_claim_receipts_replay_window_exact"),
        ),
        sa.CheckConstraint(
            "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name=op.f("ck_worker_lease_claim_receipts_worker_id_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.run_id"],
            name="fk_worker_lease_claim_receipts_tenant_run_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "claim_id",
            name="pk_worker_lease_claim_receipts",
        ),
    )
    op.create_index(
        "ix_worker_lease_claim_receipts_cleanup",
        "worker_lease_claim_receipts",
        ["replay_expires_at", "tenant_id", "claim_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO zhiyi_schema_compatibility "
            "(component, contract_version, installed_at) "
            "VALUES ('worker_lease_kernel', 1, CURRENT_TIMESTAMP)"
        )
    )


def downgrade() -> None:
    """Destroy only 006 coordination facts; disposable environments only."""

    op.execute(
        sa.text("DELETE FROM zhiyi_schema_compatibility WHERE component = 'worker_lease_kernel'")
    )
    op.drop_index(
        "ix_worker_lease_claim_receipts_cleanup",
        table_name="worker_lease_claim_receipts",
    )
    op.drop_table("worker_lease_claim_receipts")
    op.drop_index(
        "ix_worker_leases_tenant_inactive_running",
        table_name="worker_leases",
    )
    op.drop_table("worker_leases")
