# Phase 0 Research: PostgreSQL RunRepository

**Feature**: `005-postgresql-run-repository`

**Date**: 2026-08-25

**Scope**: PostgreSQL persistence for Run, RunEvent, and CommandReceipt only. Worker,
leases, Checkpoint, API, and background execution remain out of scope.

## Decision 1: Use SQLAlchemy Core, Psycopg 3, and Alembic

**Decision**: Add and lock the following direct dependencies during implementation:

```toml
"sqlalchemy[asyncio]==2.0.52"
"alembic==1.19.1"
"psycopg[binary]==3.3.4"
```

The repository uses SQLAlchemy Core `AsyncEngine` and `AsyncConnection`, not ORM
entities or a shared `AsyncSession`. The application DSN uses the
`postgresql+psycopg://` dialect. Alembic runs as an explicit release/operations
command and can use the same Psycopg package through SQLAlchemy's synchronous
dialect. SQLAlchemy owns the only connection pool; `psycopg_pool` is not added.

**Rationale**:

- SQLAlchemy's Psycopg dialect supports both synchronous and asynchronous engines,
  so the runtime repository and independent migration command do not need two
  PostgreSQL drivers.
- Core keeps table and transaction details inside the adapter without introducing
  ORM identity-map or lazy-loading behavior for immutable aggregates.
- Psycopg exposes SQLSTATE and transaction status needed for conservative
  `storage_unavailable` versus `commit_outcome_unknown` classification.
- Direct dependencies follow the repository's exact-version policy; the generated
  `uv.lock` will freeze transitive packages.

**Maintenance and license review**:

| Dependency | Purpose | Maintenance evidence | License | Decision |
|---|---|---|---|---|
| SQLAlchemy 2.0.52 | Async Core engine, SQL, pooling | Current stable 2.0 release; active upstream | MIT | Accept |
| Alembic 1.19.1 | Versioned schema migration | Official SQLAlchemy migration project; active upstream | MIT | Accept |
| Psycopg 3.3.4 with `binary` extra | PostgreSQL sync/async driver | Supports Python 3.10-3.14 and PostgreSQL 10-18 | LGPL-3.0-only | Accept for M0; record dependency/license inventory and required notices, include bundled native libraries in SBOM/vulnerability scanning, and complete redistribution review before production packaging |

**Alternatives rejected**:

- `asyncpg==0.31.0`: capable and Apache-2.0 licensed, but requires an async Alembic
  bridge or a second sync driver and has additional prepared-statement/type-cache
  considerations when an external process applies DDL.
- Direct Psycopg SQL: removes SQLAlchemy but duplicates metadata, query composition,
  migrations, and error adaptation.
- `psycopg[c]`: a viable future production-image choice after the repository owns a
  reproducible `libpq`/compiler toolchain; it is premature for the current image-less
  project.

**Sources**:

- [SQLAlchemy Psycopg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg)
- [SQLAlchemy asyncio installation and concurrency](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic version policy](https://alembic.sqlalchemy.org/en/latest/front.html#versioning-scheme)
- [Psycopg installation choices](https://www.psycopg.org/psycopg3/docs/basic/install.html)
- [Psycopg error classes and SQLSTATE](https://www.psycopg.org/psycopg3/docs/api/errors.html)

## Decision 2: Support PostgreSQL 18.x, verified on 18.6

**Decision**: The initial supported database line is PostgreSQL 18.x. Local and CI
acceptance use PostgreSQL 18.6; the implementation task must pin the official image
by immutable digest as well as the human-readable tag. The repository will not claim
support for untested older majors or PostgreSQL 19 development builds.

**Rationale**: This is a new schema with no production compatibility baseline.
PostgreSQL 18 is the current supported major and remains supported through 2030. A
single pinned reference version keeps the M0 acceptance matrix deterministic; more
majors can be added only after contract and migration tests prove them.

**Alternative rejected**: Testing a broad 16-18 matrix now adds CI cost without an
existing deployment requirement and would create a compatibility promise before it
is needed.

**Source**: [PostgreSQL versioning policy](https://www.postgresql.org/support/versioning/)

## Decision 3: Preserve domain numbers through versioned text-safe codecs

**Decision**:

- Every finite non-negative `Decimal` is encoded with the existing
  `canonical_decimal()` representation and stored as a JSON string or `TEXT`, never
  as PostgreSQL `numeric`, `double precision`, or a JSON number.
- Domain counters, Run versions, and event sequences are Python integers without a
  004 application bound. Authoritative records encode them as canonical decimal
  strings. Equality/CAS uses the canonical text. Event ordering uses
  `(length(sequence), sequence)` over canonical non-negative digit strings.
- Full versioned aggregate snapshots and event payloads use PostgreSQL `json`, not
  `jsonb`. The `json` representation validates JSON syntax while retaining the input
  text and does not impose `jsonb`'s PostgreSQL-`numeric` conversion limit.
- The adapter binds canonical JSON text into PostgreSQL `json` columns and selects
  those columns back as text before decoding. Signed arbitrary-size JSON integers are
  emitted and parsed in bounded base-10 chunks; the codec never relies on naive
  unbounded `str(int)`, `int(digit_string)`, or default JSON integer conversion, and
  never changes `sys.set_int_max_str_digits()` process-wide.
- UTC datetimes use explicit RFC 3339 strings in the authoritative document and
  `timestamptz` projections for indexes. Identifiers and enums remain readable
  strings. Python pickle, executable serialization, and opaque blobs are forbidden.
- No application precision/scale/counter bound, rounding, or truncation is added.
  Physical storage exhaustion remains an infrastructure failure, not a new domain
  validation rule.

**Rationale**: PostgreSQL `numeric` and `jsonb` numbers have finite implementation
ranges that are narrower than the existing Decimal contract. Canonical strings
preserve value equality and remain inspectable and versionable. Text-encoded
unbounded integers also avoid silently narrowing the existing Python contract to
`bigint`.

**Alternatives rejected**:

- `numeric(p,s)` or unconstrained `numeric`: both introduce a database-native range;
  fixed precision also rounds values.
- `jsonb` authoritative documents: JSON numbers outside PostgreSQL `numeric` range
  are rejected.
- Store only normalized columns: duplicates a large reconstruction surface and makes
  future additive snapshot versions harder to roll out.

**Sources**:

- [PostgreSQL numeric types and limits](https://www.postgresql.org/docs/current/datatype-numeric.html)
- [PostgreSQL `json` and `jsonb` numeric behavior](https://www.postgresql.org/docs/current/datatype-json.html)

## Decision 4: Use READ COMMITTED with receipt-first arbitration

**Decision**: Every `commit()` is one short explicit `READ COMMITTED` transaction,
with no external calls and a single lock order:

1. Set local statement/lock timeouts and `synchronous_commit=on`.
2. Insert the complete candidate receipt with
   `ON CONFLICT (tenant_id, command_id) DO NOTHING RETURNING ...`.
3. If no row is returned, run a new `SELECT` for the committed receipt. A different
   fingerprint returns `idempotency_conflict` without reading the Run; the same
   fingerprint returns the original receipt/event as a replay.
4. If this transaction owns the command, lock the existing Run row with
   `SELECT ... FOR UPDATE`, or attempt a create insert for expected version zero.
5. Compare expected version, decode current facts, and run the shared 004 commit
   invariant validator.
6. Update/insert the Run, insert zero or one immutable event, and commit. A zero-event
   command does not update the Run row and only persists its receipt.

The receipt primary key is immediate so PostgreSQL can use it as the conflict
arbiter. Receipt-to-Run and receipt-to-Event foreign keys are
`DEFERRABLE INITIALLY DEFERRED`, allowing `receipt -> run -> event` for creation while
still rejecting orphan facts at commit.

**Rationale**:

- Command arbitration occurs before version access, preserving 004's replay and
  idempotency-conflict priority across processes.
- Under READ COMMITTED, the statement after a blocked `ON CONFLICT DO NOTHING` sees
  a new snapshot and can read the winner's committed immutable receipt.
- The Run row lock serializes same-Run writers. A waiter observes the latest row and
  fails the expected-version check, preventing lost updates.
- Immediate unique constraints enforce global event identity and per-Run sequence
  uniqueness even if another write path is introduced later.
- Serializable isolation would require whole-transaction retries, which conflict
  with conservative unknown-outcome semantics and are unnecessary for predetermined
  command and Run keys.

**Important semantic boundary**: The single-winner rule applies to commands that
change the Run or append an event. Distinct valid zero-event commands may each add an
immutable receipt at the same unchanged Run version, as required by 004. The spec's
acceptance scenario, FR-007, and SC-003 were narrowed during planning to remove the
contradiction with FR-009 and FR-023.

**Alternatives rejected**:

- Select-before-insert: has a cross-process TOCTOU race.
- Lock Run before checking the command: changes replay priority into version conflict.
- `ON CONFLICT DO UPDATE` as a no-op: creates unnecessary row versions and audit
  noise for immutable receipts.
- Transaction-level advisory locks: correct if every writer follows the protocol,
  but the immediate receipt unique constraint is simpler and independently enforced.
- Automatic repository retries: unsafe after a commit acknowledgement is lost.

**Sources**:

- [PostgreSQL READ COMMITTED and `ON CONFLICT` visibility](https://www.postgresql.org/docs/current/transaction-iso.html)
- [PostgreSQL `INSERT ... ON CONFLICT`](https://www.postgresql.org/docs/current/sql-insert.html)
- [PostgreSQL row and advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html)
- [PostgreSQL WAL and `synchronous_commit`](https://www.postgresql.org/docs/current/runtime-config-wal.html)

## Decision 5: Share 004 validation and keep persistence failures outside the domain

**Decision**: Extract the adapter-neutral aggregate commit validation currently
embedded in `MemoryRunRepository` into an application-level helper consumed by both
adapters. Database uniqueness and foreign keys remain defense in depth; they do not
redefine lifecycle behavior.

Version/idempotency/not-found/invariant failures continue to use the existing
`RunLifecycleError` codes. Add a separate application-port `RunRepositoryError` with
four stable codes:

| Code | Meaning |
|---|---|
| `storage_unavailable` | The operation failed and the transaction is known not to have committed, or a read could not reach storage |
| `commit_outcome_unknown` | COMMIT may have reached PostgreSQL but no definitive commit/rollback acknowledgement was received |
| `data_corruption` | Rows were readable but could not be decoded into a valid 004 fact or projections disagreed with the authoritative document |
| `schema_incompatible` | Connected storage is not at an application-supported schema contract version |

All public messages and `repr` values are constant and carry only a safe correlation
identifier. SQLAlchemy is configured with `hide_parameters=True` and SQL echo off;
logs never include a DSN, SQL text/parameters, fingerprints, complete payloads,
answers, or hidden reasoning.

**Rationale**: Storage availability and record/schema health are adapter boundary
failures, not Run lifecycle states. Keeping the two error families separate prevents
database details from entering the domain while preserving stable caller behavior.

**Source**: [SQLAlchemy exception model](https://docs.sqlalchemy.org/en/20/core/exceptions.html)

## Decision 6: Classify failures by transaction phase plus SQLSTATE

**Decision**:

- Failures before `commit()` is invoked, or failures for which the server/driver
  explicitly confirms abort/rollback, map to `storage_unavailable` after best-effort
  rollback and connection invalidation as needed.
- A disconnect/timeout after entering the commit call, SQLSTATE `08007`, SQLSTATE
  `40003`, or an unknown libpq transaction status maps conservatively to
  `commit_outcome_unknown`.
- `connection_invalidated` alone is not proof of rollback.
- The repository never retries an unknown write. The caller must open a new
  connection and replay the original `command_id` plus identical normalized intent.
- Constraint violations raised before commit map through named constraints to the
  existing safe domain conflict/invariant codes; raw database messages never escape.

**Rationale**: Driver exception classes alone cannot prove whether PostgreSQL applied
a transaction. Phase tracking is required to distinguish a known failed attempt from
a lost acknowledgement.

**Sources**:

- [PostgreSQL SQLSTATE list](https://www.postgresql.org/docs/current/errcodes-appendix.html)
- [PostgreSQL/libpq transaction status](https://www.postgresql.org/docs/current/libpq-status.html)
- [SQLAlchemy disconnect handling](https://docs.sqlalchemy.org/en/20/core/pooling.html#disconnect-handling-pessimistic)

## Decision 7: Separate migration revision from application compatibility

**Decision**:

- Alembic's `alembic_version` records migration graph position.
- An application-owned single-row `zhiyi_schema_compatibility` table records the
  RunRepository `contract_version`, initially `1`.
- The repository performs a read-only compatibility check when it is constructed or
  first used and accepts an explicit set of contract versions. It never calls
  `create_all()`, `upgrade`, or `stamp`.
- Future changes follow expand -> deploy code compatible with old/new structures ->
  drain old replicas -> contract. Compatibility-preserving revisions keep contract
  version 1; a breaking contract migration advances it only after old replicas are
  gone.
- The initial downgrade removes receipt, event, Run, and compatibility structures in
  dependency order. It is destructive and allowed only in disposable databases.
  Production data-preserving rollback uses a verified backup restored to a new
  database, followed by controlled cutover.

**Rationale**: Requiring the database to equal one exact Alembic head would reject a
migrate-first rolling deployment even when a new revision is backward compatible.
The separate contract version expresses the application's read/write compatibility,
while Alembic still provides a complete migration audit graph.

**Alternatives rejected**:

- Exact Alembic-head check in the application: blocks compatible expand migrations.
- Application startup migration: violates FR-018 and creates multi-replica DDL races.
- `create_all()` plus stamping: establishes a second schema creation path.
- Production `downgrade base`: destroys data and is not a valid rollback strategy.

**Sources**:

- [Alembic tutorial and version table](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Alembic `current --check-heads`](https://alembic.sqlalchemy.org/en/latest/cookbook.html#test-current-database-revision-is-at-head-s)
- [PostgreSQL logical backup](https://www.postgresql.org/docs/current/backup-dump.html)

## Decision 8: Use a real disposable PostgreSQL test lane

**Decision**:

- Add `compose.test.yaml` with a digest-pinned PostgreSQL 18.6 service for local
  acceptance. CI adds an isolated PostgreSQL service job with health checks.
- Add a `postgresql` pytest marker. The fast lane runs
  `-m "not online and not postgresql"`; the PostgreSQL lane runs `-m postgresql`.
- Every real-PostgreSQL contract, integration, migration, fault, and performance test
  module owns a module-level `postgresql` marker. CI first performs collection-only
  assertions that the PostgreSQL set is non-empty and the database-dependent path
  allowlist contributes no node to the fast lane.
- Extract the current hard-coded memory contract into a provider-neutral contract
  suite, then run it against both Memory and PostgreSQL factories.
- PostgreSQL tests use separate connections/engines, apply migrations explicitly,
  rebuild adapters to prove restart persistence, and never substitute SQLite or an
  in-memory fake for transaction/concurrency claims.
- Migration CI runs empty upgrade, head check, representative write, custom-format
  dump, disposable downgrade/re-upgrade, restore into a fresh database, and domain
  round-trip verification.
- Performance acceptance uses 20 concurrent clients and records p50/p95 without
  weakening correctness checks.

Deterministic failure tests cover three windows:

1. Connection/statement failure before commit: prove zero rows and expect
   `storage_unavailable`.
2. Terminate a backend before commit from a second connection: prove rollback and
   expect `storage_unavailable`.
3. A test transaction-boundary wrapper performs a real PostgreSQL commit, suppresses
   the acknowledgement, and raises a disconnect: expect `commit_outcome_unknown`,
   then replay the original command through a new adapter and prove one final write.

The third test deliberately controls only acknowledgement delivery; the storage and
commit are real. A network proxy may be added later as a nightly smoke test, but is
not deterministic enough for the required 100% classification gate.

**Alternatives rejected**:

- Testcontainers: Compose and the CI service already provide lifecycle control with
  fewer Python dependencies.
- Mock-only faults: cannot prove PostgreSQL atomicity, locks, or commit behavior.
- Toxiproxy as the primary gate: useful for exploratory/network testing but timing a
  lost COMMIT acknowledgement reproducibly is difficult.

**Sources**:

- [GitHub Actions PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- [Alembic autogenerate check](https://alembic.sqlalchemy.org/en/latest/autogenerate.html#running-alembic-check-to-test-for-new-upgrade-operations)
- [PostgreSQL dump and restore](https://www.postgresql.org/docs/current/backup-dump.html)

## Resolved Unknowns

No unresolved planning question remains. Dependency versions, database
baseline, transaction isolation/lock order, numeric representation, stable error
classification, schema compatibility, rollback, and real-database acceptance have
concrete decisions. Exact container digest resolution and lockfile regeneration are
mechanical implementation tasks and do not change product behavior.
