"""Reviewed SQLAlchemy Core metadata for the PostgreSQL Worker Lease Kernel."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from zhiyi.adapters.persistence.postgresql_schema import metadata

WORKER_LEASE_SCHEMA_CONTRACT_VERSION = 1

worker_leases = Table(
    "worker_leases",
    metadata,
    Column("tenant_id", String(128), nullable=False),
    Column("run_id", String(128), nullable=False),
    Column("worker_id", String(128), nullable=False),
    Column("claim_id", UUID(as_uuid=True), nullable=False),
    Column("token_digest", LargeBinary, nullable=False),
    Column("attempt_no", BigInteger, nullable=False),
    Column("lease_version", BigInteger, nullable=False),
    Column("duration_seconds", SmallInteger, nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    Column("lease_expires_at", DateTime(timezone=True), nullable=False),
    Column("released_at", DateTime(timezone=True), nullable=True),
    Column("record_format_version", SmallInteger, nullable=False),
    PrimaryKeyConstraint("tenant_id", "run_id", name="pk_worker_leases"),
    UniqueConstraint(
        "tenant_id",
        "claim_id",
        name="uq_worker_leases_tenant_claim",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "run_id"],
        ["runs.tenant_id", "runs.run_id"],
        name="fk_worker_leases_tenant_run_runs",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
        name="worker_id_valid",
    ),
    CheckConstraint(
        "uuid_extract_version(claim_id) = 7 AND (get_byte(uuid_send(claim_id), 8) & 192) = 128",
        name="claim_id_uuidv7",
    ),
    CheckConstraint("octet_length(token_digest) = 32", name="token_digest_length"),
    CheckConstraint("attempt_no > 0", name="attempt_no_positive"),
    CheckConstraint("lease_version > 0", name="lease_version_positive"),
    CheckConstraint(
        "duration_seconds BETWEEN 10 AND 30",
        name="duration_seconds_supported",
    ),
    CheckConstraint(
        "heartbeat_at >= acquired_at",
        name="heartbeat_not_before_acquired",
    ),
    CheckConstraint(
        "lease_expires_at > heartbeat_at",
        name="expiry_after_heartbeat",
    ),
    CheckConstraint(
        "released_at IS NULL OR released_at >= acquired_at",
        name="released_not_before_acquired",
    ),
    CheckConstraint(
        "record_format_version = 1",
        name="record_format_version_supported",
    ),
)
Index(
    "ix_worker_leases_tenant_inactive_running",
    worker_leases.c.tenant_id,
    func.coalesce(worker_leases.c.released_at, worker_leases.c.lease_expires_at),
    worker_leases.c.run_id,
)

worker_lease_claim_receipts = Table(
    "worker_lease_claim_receipts",
    metadata,
    Column("tenant_id", String(128), nullable=False),
    Column("claim_id", UUID(as_uuid=True), nullable=False),
    Column("claim_issued_at", DateTime(timezone=True), nullable=False),
    Column("replay_expires_at", DateTime(timezone=True), nullable=False),
    Column("worker_id", String(128), nullable=False),
    Column("duration_seconds", SmallInteger, nullable=False),
    Column("intent_format_version", SmallInteger, nullable=False),
    Column("intent_fingerprint", String(71), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("run_id", String(128), nullable=True),
    Column("attempt_no", BigInteger, nullable=True),
    Column("initial_lease_version", BigInteger, nullable=True),
    Column("lease_acquired_at", DateTime(timezone=True), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("replay_token", LargeBinary, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("record_format_version", SmallInteger, nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "claim_id",
        name="pk_worker_lease_claim_receipts",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "run_id"],
        ["runs.tenant_id", "runs.run_id"],
        name="fk_worker_lease_claim_receipts_tenant_run_runs",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "uuid_extract_version(claim_id) = 7 AND (get_byte(uuid_send(claim_id), 8) & 192) = 128",
        name="claim_id_uuidv7",
    ),
    CheckConstraint(
        "claim_issued_at = uuid_extract_timestamp(claim_id) "
        "AND replay_expires_at = claim_issued_at + interval '24 hours'",
        name="replay_window_exact",
    ),
    CheckConstraint(
        "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
        name="worker_id_valid",
    ),
    CheckConstraint(
        "duration_seconds BETWEEN 10 AND 30",
        name="duration_seconds_supported",
    ),
    CheckConstraint(
        "intent_format_version = 1",
        name="intent_format_version_supported",
    ),
    CheckConstraint(
        "intent_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="intent_fingerprint_sha256",
    ),
    CheckConstraint("outcome IN ('claimed','no_work')", name="outcome_supported"),
    CheckConstraint(
        "attempt_no IS NULL OR attempt_no > 0",
        name="attempt_no_positive",
    ),
    CheckConstraint(
        "initial_lease_version IS NULL OR initial_lease_version > 0",
        name="initial_lease_version_positive",
    ),
    CheckConstraint(
        "replay_token IS NULL OR octet_length(replay_token) = 32",
        name="replay_token_length",
    ),
    CheckConstraint(
        "(outcome = 'no_work' AND run_id IS NULL AND attempt_no IS NULL "
        "AND initial_lease_version IS NULL AND lease_acquired_at IS NULL "
        "AND lease_expires_at IS NULL AND replay_token IS NULL) OR "
        "(outcome = 'claimed' AND run_id IS NOT NULL AND attempt_no IS NOT NULL "
        "AND initial_lease_version IS NOT NULL AND lease_acquired_at IS NOT NULL "
        "AND lease_expires_at > lease_acquired_at AND replay_token IS NOT NULL)",
        name="complete_outcome",
    ),
    CheckConstraint(
        "record_format_version = 1",
        name="record_format_version_supported",
    ),
)
Index(
    "ix_worker_lease_claim_receipts_cleanup",
    worker_lease_claim_receipts.c.replay_expires_at,
    worker_lease_claim_receipts.c.tenant_id,
    worker_lease_claim_receipts.c.claim_id,
)

__all__ = [
    "WORKER_LEASE_SCHEMA_CONTRACT_VERSION",
    "worker_lease_claim_receipts",
    "worker_leases",
]
