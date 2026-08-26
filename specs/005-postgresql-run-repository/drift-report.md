# Design Drift Report

**Feature**: 005-postgresql-run-repository
**Status**: ALIGNED
**Docs-Impact**: UPDATED
**Docs-Updated**: README.md, doc/PROJECT.md, doc/功能文档.md, doc/技术方案.md, doc/SDD开发规范.md, doc/AGENTS.md
**Docs-Impact-Reason**: The feature adds the first production PostgreSQL RunRepository, schema and migration path, real-database CI lane, and persistence-specific failure boundary while explicitly leaving Worker, leases, Checkpoint, API/SSE, SDK, Model Gateway integration, and background execution unavailable.
**Reviewed-By**: AI
**Implementation-Approval**: Explicitly approved by the user in the active task

## Planned Change Set

- Feature artifacts: `specs/005-postgresql-run-repository/spec.md`, `plan.md`,
  `tasks.md`, `research.md`, `data-model.md`, `quickstart.md`, `drift-report.md`,
  `contracts/postgresql-run-repository.md`, `checklists/requirements.md`, and
  `checklists/persistence-safety.md`.
- Application and adapter paths: `src/zhiyi/application/ports/__init__.py`,
  `src/zhiyi/application/ports/run_repository.py`,
  `src/zhiyi/application/ports/run_repository_validation.py`,
  `src/zhiyi/adapters/persistence/__init__.py`,
  `src/zhiyi/adapters/persistence/memory_run_repository.py`,
  `src/zhiyi/adapters/persistence/postgresql_schema.py`,
  `src/zhiyi/adapters/persistence/postgresql_codecs.py`, and
  `src/zhiyi/adapters/persistence/postgresql_run_repository.py`.
- Infrastructure and migration paths: `src/zhiyi/infrastructure/database/__init__.py`,
  `src/zhiyi/infrastructure/database/engine.py`,
  `src/zhiyi/infrastructure/database/schema_compatibility.py`, `alembic.ini`,
  `migrations/env.py`, `migrations/script.py.mako`,
  `migrations/versions/0001_create_run_repository.py`, `compose.test.yaml`, and
  `.dockerignore`.
- Dependency and CI paths: `pyproject.toml`, `uv.lock`, and
  `.github/workflows/runtime-python.yml`.
- Contract and unit tests: `tests/contract/persistence/run_repository_contract.py`,
  deleted legacy binding `tests/contract/persistence/test_run_repository_contract.py`,
  `tests/contract/persistence/test_memory_run_repository_contract.py`,
  `tests/contract/persistence/test_postgresql_run_repository_contract.py`,
  `tests/unit/application/ports/test_run_repository_validation.py`,
  `tests/unit/application/ports/test_run_repository_errors.py`,
  `tests/unit/adapters/persistence/test_memory_run_repository.py`,
  `tests/unit/adapters/persistence/test_postgresql_codecs.py`,
  `tests/unit/adapters/persistence/test_postgresql_error_mapping.py`,
  `tests/unit/adapters/persistence/test_postgresql_preflight.py`,
  `tests/unit/infrastructure/database/test_engine.py`, and
  `tests/unit/infrastructure/database/test_schema_compatibility.py`.
- Integration and performance tests: `tests/integration/persistence/conftest.py`,
  `tests/integration/persistence/test_postgresql_restart.py`,
  `tests/integration/persistence/test_postgresql_concurrency.py`,
  `tests/integration/persistence/test_postgresql_tenant_isolation.py`,
  `tests/integration/persistence/test_postgresql_faults.py`,
  `tests/integration/persistence/test_migrations.py`, and
  `tests/performance/test_postgresql_run_repository.py`.
- Long-lived documentation: `README.md`, `doc/PROJECT.md`, `doc/功能文档.md`,
  `doc/技术方案.md`, `doc/SDD开发规范.md`, and `doc/AGENTS.md`.

## Alignment Evidence

- Requirements and success criteria checked: the repaired planning set contains 25 FRs,
  12 SCs, 64 dependency-ordered tasks, and 47/47 approved persistence-safety items.
- Plan sections checked: receipt-first `READ COMMITTED` transaction, tenant/global-event
  boundaries, lossless numeric codec, explicit migration/restore, test partition, security
  review, and persistence-only scope are approved.
- Dependency/engine evidence: frozen Python 3.12 sync succeeds with SQLAlchemy 2.0.52,
  Alembic 1.19.1, Psycopg/psycopg-binary 3.3.4 and one bounded async pool configured
  with size 20, zero overflow, pre-ping, explicit `READ COMMITTED`, finite
  acquisition/connect/session-statement timeouts, `hide_parameters=True`, and SQL echo
  disabled. Commit retains its bounded transaction-local lock/statement timeouts.
- Codec evidence: 29 focused codec/engine/compatibility tests pass, including the 12
  SC-008 Decimal cases (20,000 fractional and 200,000 integer digits), signed 5,000
  digit JSON integers, a 5,000 digit counter, terminal results/references, strict record
  versions, projection disagreement, malformed records, and an unchanged process-wide
  integer-string limit.
- Shared contract/restart evidence: Memory and PostgreSQL run the same provider-neutral
  contract. PostgreSQL additionally survives engine disposal/recreation for all eight Run
  statuses, all four terminal states, every RunEventType, receipts, references, UTC values,
  and canonical values above signed 64-bit sequence range.
- SC-003/SC-004 evidence: `test_postgresql_concurrency.py` passes 100 state-change
  groups/1,000 attempts with 100 winners, 900 `version_conflict` results, 0 duplicate
  winners, and exact final counts of 100 Runs, 200 Events, and 200 receipts. The
  100-request same-command matrix produces 1 write and 99 replays through a 20-connection
  pool with exactly one Run/Event/receipt. Concurrent create, different-intent reuse, and
  zero-event/state-change linearization also pass with 0 partial combinations.
- SC-005 evidence: three statement-boundary rollback probes plus 100 pre-commit failures
  and 100 real backend terminations classify 100% as `storage_unavailable` with 0 committed
  partial rows. One hundred real commits with deliberately lost acknowledgements classify
  100% as `commit_outcome_unknown`; replaying every original command returns its one
  committed outcome, with final counts 100/100/100 and no repository write retry.
- SC-006/SC-007/SC-010 evidence: same Run/command identifiers coexist across tenants;
  missing and foreign reads have identical shapes; a cross-tenant global event collision
  fails without disclosing its owner; cursor pages are ordered exactly once, including
  values above `2**127`; planted DSN/password/SQL/payload/final-answer/hidden-reasoning
  markers occur zero times in public errors, reprs, or captured repository logs.
- SC-009 evidence: empty/repeated upgrade, `current --check-heads`, `alembic check`, named
  physical objects, compatibility row 1, identity-guarded disposable downgrade/re-upgrade,
  custom-format `pg_dump`, fresh-database `pg_restore`, equal row counts, equal stable fact
  digests, and equal decoded domain values pass. Application compatibility checking emits
  zero DDL statements.
- SC-011 post-repair full-lane evidence on macOS 15.5 arm64, 10 logical CPUs, 16 GiB physical
  memory, PostgreSQL 18.6 image digest
  `sha256:1ffbf339f5b8e78c394cfaad3711ef6dbc4e14546bf70428e0bb30cba66e8e4d`, pool
  20/overflow 0, 20 clients, 100 Runs x 100 Events, 100 warm-ups and 1,000 samples per
  operation: load p50/p95 10.75/14.94 ms, 100-event page 45.52/73.35 ms, and
  different-Run atomic commit 25.40/68.19 ms. Every p95 is below 100 ms.
- Final test partition evidence: frozen sync passes; `pytest -m "not online and not
  postgresql"` reports 347 passed and 35 deselected; the complete real PostgreSQL lane
  reports 33 passed and 349 deselected. Collection finds exactly 33 PostgreSQL nodes and
  zero database-dependent nodes in the fast marker expression.
- Quickstart migration evidence: all 5 migration tests pass; explicit upgrade, repeated
  upgrade, `current --check-heads`, and `alembic check` pass; the identity-checked disposable
  database produces a valid custom-format dump; destructive `downgrade base`, clean
  `upgrade head`, and fresh-database restore all pass with equal row counts, stable fact
  digests, and decoded domain values. Revision `0001` has no runtime `zhiyi` imports and
  creates the exact 29-object named constraint/index inventory.
- Final static/governance evidence: Ruff check passes, all 89 scoped files are formatted,
  strict mypy passes over 87 source files, `git diff --check` passes, and all 29 governance
  tests pass. Pre-implementation `$speckit-analyze` had zero critical findings; the manual
  drift gate passes with every implementation path traced. The post-review
  `$speckit-converge` checks 25 FRs, 12 SCs, 64 tasks, 17 acceptance scenarios, the planned
  architecture/transaction/migration/CI decisions, eight constitution principles, and
  database rules with zero missing, partial, contradicting, or unrequested findings; it
  appends no further Convergence tasks.

## SQL and Tenant Security Review

**Reviewer**: Codex primary implementation agent

**Scope**: every Run/Event/Receipt query and index, receipt/global-event conflicts,
transaction phase/SQLSTATE handling, application versus migration privileges, DSN/SQL/
parameter/payload logging, schema compatibility, and destructive migration guards.

- **HIGH resolved — incomplete authoritative Run document**: the initial codec stored budget,
  usage, and result in JSON but relied on relational identity/status/version projections. It
  could not fail closed on every projection mutation required by the data model. Format 1 now
  stores the complete Run document and verifies every projected identity, AgentVersion,
  status, version, sequence, and timestamp before returning a domain object. Focused corruption
  tests and real-database tampering return only `data_corruption`.
- **HIGH resolved — incomplete physical defense constraints**: the first metadata draft lacked
  digest/time-order/command/event/status checks and used cascading product-fact deletion.
  Named checks now cover these projections; Run/Event/receipt relationships use restrictive
  deletion; command/global event/per-Run sequence uniqueness remains immediate where required;
  the receipt ownership relationships remain deferred; a tenant/Run receipt index was added.
  Migration introspection and `alembic check` pass.
- **MEDIUM resolved — normal event reads used a redundant existence query and row-by-row
  transport**: the query was tenant-safe but caused the 100-event p95 to miss SC-011. The
  adapter now performs the tenant-scoped existence query only when the event page is empty,
  uses a composite `(sequence_digits, sequence_value)` range comparison matching the cursor
  index, and returns each ordered page as one server-side JSON aggregate while preserving
  strict per-event decoding. Foreign and missing shapes remain identical; final full-lane
  page p95 is 68.19 ms.
- **MEDIUM resolved — compatibility cache lock crossed event loops**: one module-level async
  lock could bind to an obsolete loop. The cache now owns one weak-reference lock per Engine;
  the complete PostgreSQL lane and cache-lifecycle tests cover separate Engine lifetimes.
- All tenant-owned reads and mutations include tenant_id; replay-event lookup additionally
  includes run_id. The only unscoped event lookup asks whether the caller-supplied globally
  unique event_id is occupied and returns only that same identifier, never the existing owner.
- All caller values are SQLAlchemy-bound parameters. Engine SQL echo is off and parameters are
  hidden. Diagnostics emit only a stable error code, bounded transaction phase, and the
  caller-supplied tenant/Run; they never log exceptions, commands, SQL, parameters, payloads,
  results, credentials, or conflict-owner facts.
- Application code has no DDL/migration path. Production application/migration role creation,
  Secret management, backups/PITR, SBOM, vulnerability scanning, and redistribution sign-off
  remain explicit pre-production obligations, not silently claimed by Feature 005. The test
  database user is intentionally disposable and privileged only inside the isolated fixture.
- Destructive downgrade and restore verify the exact disposable database, user, local/service
  host, and resolved container before execution. No production database or deployment was
  accessed.

**Disposition at the first convergence pass**: The findings above were implemented and
re-verified through T058. A subsequent pre-commit review opened the six blocking findings
tracked by T059–T064 below; the earlier `ALIGNED` conclusion is therefore withdrawn until
those tasks and the complete acceptance gates pass again. Worker/lease/Checkpoint/API
concerns remain excluded rather than waived.

## Explicit Exclusions

No task may add Worker, lease, Reconciler, Checkpoint, REST/SSE, SDK, Model Gateway
integration, Tool execution, background scheduling, deployment, or production data work.

SC-012 audit: schema inspection finds only compatibility, Run, Event, and Receipt columns;
there are no worker_id, lease_token, lease expiry/heartbeat/attempt, queue-claim, Checkpoint,
Graph, API/SSE, model, Tool, or background-execution fields. Product source imports no
LangGraph, FastAPI, Model Gateway, Tool Runtime, scheduler, or Worker module. The internal
`_transaction_boundary` method is a deterministic storage-fault test seam only; it stores
nothing and is not a LangGraph Checkpoint. Compose/CI starts only the disposable PostgreSQL
test service and performs no deployment.

## Intentional Differences

The adapter's normal populated `list_events` path checks event rows first and performs the
tenant-scoped Run-existence query only for an empty page. This preserves the approved
missing/foreign result contract while avoiding one redundant query; it is an implementation
optimization within the approved indexed cursor design.

## Blocking Findings

None. T059–T064 are test-first complete:

- Engine construction pins `READ COMMITTED` and a finite session statement timeout; a real
  transaction reports `read committed`, and a real `ACCESS EXCLUSIVE` blocked read times out
  as the stable redacted `storage_unavailable` error.
- Revision `0001` is self-contained and runtime-import-free; exact schema inventory,
  downgrade/re-upgrade, head, autogenerate, dump/restore, and domain round-trip checks pass.
- Compatibility accepts only exact integer contract version 1; `42P01`/`42703` identify
  partial schema, while `42501` and other operational failures remain storage failures.
- Replay cross-checks the referenced Event's command-derived type, sequence, status, and Run
  version; a valid but mismatched same-Run Event is classified as `data_corruption`.
- Run, receipt, and every Event are fully encoded before schema access, receipt arbitration,
  or Run locking. The focused ordering test proves this boundary.

All implementation, migration, recovery, concurrency, failure, performance, static,
security, scope, manual drift, and final convergence evidence through T064 is complete. The
report is `ALIGNED`.
