---

description: "Dependency-ordered implementation tasks for PostgreSQL RunRepository"
---

# Tasks: PostgreSQL RunRepository

**Input**: Design documents from `/specs/005-postgresql-run-repository/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`contracts/postgresql-run-repository.md`, `quickstart.md`, and reviewer-approved
`checklists/persistence-safety.md`

**Tests**: Required by the feature specification and constitution. For every behavior
slice, write the named test first, run it to prove the expected failure, implement the
smallest correct change, and refactor only while the focused suite stays green.

**Organization**: Tasks are grouped by user story, use exact implementation/test/doc
paths, and preserve the explicit exclusion of Worker, lease, Reconciler, Checkpoint,
API/SSE, SDK, Model Gateway integration, Tool execution, and background scheduling.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files with no incomplete dependency.
- **[Story]**: Maps to a user story in `spec.md`.

## Phase 1: Setup (Frozen Data-Access Environment)

**Purpose**: Establish the live drift ledger, pinned PostgreSQL development/test
dependencies, and disposable database environment approved by the plan.

- [X] T001 Create an `IN_PROGRESS` design-drift ledger with `Docs-Impact: UPDATED`, the approved source/test/migration/CI/document paths, and explicit Worker/lease exclusions in `specs/005-postgresql-run-repository/drift-report.md`
- [X] T002 Add `sqlalchemy[asyncio]==2.0.52`, `alembic==1.19.1`, `psycopg[binary]==3.3.4`, and the `postgresql` pytest marker in `pyproject.toml`; regenerate and review the complete frozen dependency tree in `uv.lock`
- [X] T003 [P] Add the PostgreSQL 18.6 disposable test service, immutable image digest, isolated test database/user, health check, port, and scoped volume in `compose.test.yaml`; add the Docker build-context exclusions required by the approved Compose workflow in `.dockerignore`

**Checkpoint**: The frozen Python environment and disposable database definition exist,
but no product repository behavior has been added.

---

## Phase 2: Foundational (Shared 004 Contract and Safe Errors)

**Purpose**: Extract adapter-neutral validation and stable persistence errors before any
PostgreSQL adapter code, while proving the Memory adapter remains behaviorally unchanged.

**⚠️ CRITICAL**: No PostgreSQL story starts until the new tests fail for the intended
missing behavior and the refactored Memory contract is green.

- [X] T004 [P] Write failing adapter-neutral commit-validation tests covering create/update/zero-event invariants, receipt agreement, global event identity input, sequence continuity, and invalid-candidate rollback in `tests/unit/application/ports/test_run_repository_validation.py`
- [X] T005 [P] Write failing stable-code, constant-message, optional-correlation-ID, exception-chaining, and sensitive-`str`/`repr` tests for `storage_unavailable`, `commit_outcome_unknown`, `data_corruption`, and `schema_incompatible` in `tests/unit/application/ports/test_run_repository_errors.py`
- [X] T006 Implement the pure shared commit-invariant validator without database imports in `src/zhiyi/application/ports/run_repository_validation.py`
- [X] T007 Refactor the provider-neutral contract from `tests/contract/persistence/test_run_repository_contract.py` into `tests/contract/persistence/run_repository_contract.py`, bind it from `tests/contract/persistence/test_memory_run_repository_contract.py`, delete the legacy binding after migration, and make `src/zhiyi/adapters/persistence/memory_run_repository.py` use the shared validator without changing 004 results
- [X] T008 Implement the repository-specific stable error enum/exception and safe messages, separate from `RunErrorCode`, in `src/zhiyi/application/ports/run_repository.py`
- [X] T009 Export only the framework-neutral repository/error/validator surface from `src/zhiyi/application/ports/__init__.py`
- [X] T010 Run `tests/unit/application/ports/test_run_repository_validation.py`, `tests/unit/application/ports/test_run_repository_errors.py`, `tests/contract/persistence/test_memory_run_repository_contract.py`, and `tests/unit/adapters/persistence/test_memory_run_repository.py`; keep the complete 004 Memory contract green

**Checkpoint**: Both adapters can depend on one application-owned invariant and error
contract; SQLAlchemy/Psycopg still do not enter domain or application types.

---

## Phase 3: User Story 1 - 跨进程持久保存 Run 事实 (Priority: P1) 🎯 MVP

**Goal**: Persist and reopen complete Run snapshots, immutable Events, and CommandReceipts
with exact 004 domain values and no application-startup DDL.

**Independent Test**: Apply the initial migration to an empty disposable PostgreSQL
database, write representative creation/progress/budget/terminal/zero-event facts,
dispose every engine, open a new independent engine, and compare all domain values,
events, receipts, and replay outcomes with the original values.

### Tests for User Story 1

> **NOTE: Write T011-T017 first and record that they fail because the PostgreSQL
> schema, codec, engine, compatibility check, and adapter do not yet exist.**

- [X] T011 [P] [US1] Write failing record-version, canonical Decimal, 5,000-digit counter, positive/negative 5,000-digit nested JSON integer, unchanged process-wide integer-string limit, nested usage/charge, UTC, immutable JSON, terminal result, projection mismatch, unknown-field, malformed-record, and safe-codec tests in `tests/unit/adapters/persistence/test_postgresql_codecs.py`
- [X] T012 [P] [US1] Write failing secret-safe URL, async engine option, finite timeout, single-pool, pre-ping, hidden-parameter, SQL-echo-off, and disposal tests in `tests/unit/infrastructure/database/test_engine.py`
- [X] T013 [P] [US1] Write failing compatible/missing/older/newer/partial/unreachable schema checks and no-create/no-upgrade assertions in `tests/unit/infrastructure/database/test_schema_compatibility.py`
- [X] T014 [P] [US1] Write failing empty `upgrade head`, named-table/key/check/index, compatibility-row, current-head, and disposable `downgrade base` assertions with a module-level `postgresql` marker in `tests/integration/persistence/test_migrations.py`
- [X] T015 [US1] Build real PostgreSQL fixtures that require `ZHIYI_TEST_DATABASE_URL`, apply Alembic explicitly, isolate test facts, provide independent engines/connections, and fail rather than silently skip the PostgreSQL lane in `tests/integration/persistence/conftest.py`
- [X] T016 [P] [US1] Bind the provider-neutral contract to a real PostgreSQL factory, including a module-level `postgresql` marker, create/update/zero-event, command replay, pagination, and exact error parity in `tests/contract/persistence/test_postgresql_run_repository_contract.py`
- [X] T017 [P] [US1] Write failing module-level-`postgresql` engine-dispose/reopen round trips for all eight statuses, four terminal states, event types, receipts, references, UTC values, the 5,000-digit counter and positive/negative nested JSON integers, unchanged process-wide integer-string limit, and the 12 extreme Decimal cases from SC-008 in `tests/integration/persistence/test_postgresql_restart.py`

### Implementation for User Story 1

- [X] T018 [P] [US1] Define SQLAlchemy Core metadata, naming convention, `zhiyi_schema_compatibility`, `runs`, `run_events`, and `run_command_receipts` projections/keys/checks/indexes/deferred relationships in `src/zhiyi/adapters/persistence/postgresql_schema.py`
- [X] T019 [P] [US1] Implement deterministic format-version-1 Run/Event/Receipt codecs, canonical Decimal strings, bounded-chunk signed integer token conversion, canonical JSON text serialization/parsing without changing the process-wide integer-string limit, JSON-type reconstruction, projection validation, and fail-closed corruption mapping in `src/zhiyi/adapters/persistence/postgresql_codecs.py`
- [X] T020 [P] [US1] Implement secret-safe PostgreSQL URL resolution and bounded SQLAlchemy `AsyncEngine` construction/disposal with the approved pool and logging options in `src/zhiyi/infrastructure/database/engine.py`
- [X] T021 [US1] Implement the read-only accepted-contract-version check and per-engine-lifecycle compatibility cache without `create_all`, Alembic invocation, or repair behavior in `src/zhiyi/infrastructure/database/schema_compatibility.py`
- [X] T022 [US1] Configure reviewed SQLAlchemy metadata and explicit Alembic execution in `alembic.ini`, `migrations/env.py`, and `migrations/script.py.mako`; keep migration URL handling secret-safe
- [X] T023 [US1] Implement the initial transactional upgrade and reverse-order disposable downgrade, including compatibility version 1 and every named key/check/index, in `migrations/versions/0001_create_run_repository.py`
- [X] T024 [US1] Implement tenant-scoped `load`, `list_events`, and `find_command` reads that select authoritative PostgreSQL `json` columns as text for strict decode, plus canonical sequence pagination, not-found equivalence, and committed-receipt replay in `src/zhiyi/adapters/persistence/postgresql_run_repository.py`
- [X] T025 [US1] Implement the short `READ COMMITTED` atomic `commit` path with input prevalidation, receipt-first arbitration, deferred ownership checks, Run create/lock/version validation, shared invariant validation, canonical JSON-text binding to PostgreSQL `json`, zero/one Event write, zero-event no-op Run handling, and explicit commit in `src/zhiyi/adapters/persistence/postgresql_run_repository.py`
- [X] T026 [US1] Export the PostgreSQL adapter and database assembly boundary without leaking third-party types through `src/zhiyi/adapters/persistence/__init__.py` and `src/zhiyi/infrastructure/database/__init__.py`
- [X] T027 [US1] Run `tests/unit/adapters/persistence/test_postgresql_codecs.py`, `tests/unit/infrastructure/database/test_engine.py`, `tests/unit/infrastructure/database/test_schema_compatibility.py`, `tests/contract/persistence/test_postgresql_run_repository_contract.py`, `tests/integration/persistence/test_postgresql_restart.py`, and `tests/integration/persistence/test_migrations.py` green against a freshly migrated PostgreSQL 18.6 database

**Checkpoint**: User Story 1 is an independently restart-persistent repository MVP;
it still makes no Worker, lease, Checkpoint, API, or model behavior available.

---

## Phase 4: User Story 2 - 多实例并发下保持原子与幂等 (Priority: P1)

**Goal**: Preserve replay priority, optimistic single-winner state changes, legal
zero-event concurrency, atomic rollback, and conservative unknown commit outcomes over
independent connections and engines.

**Independent Test**: Run the SC-003/SC-004 race matrices and SC-005 three-window
fault matrix against real PostgreSQL; assert deterministic receipts, zero partial
records, exact stable error classification, and original-command convergence.

### Tests for User Story 2

- [X] T028 [P] [US2] Write failing module-level-`postgresql` 100-group/1,000-attempt state-changing races, 100-request/20-live-connection same-command replay, different-intent reuse, concurrent create, zero-event/state-change linearization, and single-winner assertions in `tests/integration/persistence/test_postgresql_concurrency.py`
- [X] T029 [P] [US2] Write failing module-level-`postgresql` receipt/Run/Event statement-boundary rollback, pre-commit disconnect, backend termination, real-commit/lost-acknowledgement, original-command convergence, and 100-iteration-per-window tests plus the test-only transaction-boundary wrapper in `tests/integration/persistence/test_postgresql_faults.py` and `tests/integration/persistence/conftest.py`
- [X] T030 [P] [US2] Write failing transaction-phase + SQLSTATE mapping for acquisition/lock/statement timeout, `40001`, `40P01`, `08007`, `40003`, invalidated connection, confirmed rollback, and unknown status in `tests/unit/adapters/persistence/test_postgresql_error_mapping.py`

### Implementation for User Story 2

- [X] T031 [US2] Implement explicit transaction-phase tracking, best-effort rollback/invalidation, named-constraint mapping, known-noncommit versus unknown-acknowledgement classification, and prohibition of internal write retries in `src/zhiyi/adapters/persistence/postgresql_run_repository.py`
- [X] T032 [US2] Harden command arbitration and Run locking so blocked `ON CONFLICT DO NOTHING` uses a fresh receipt read, same-command replay precedes version access, same-version state changes have one winner, and zero-event receipts linearize without consuming a version in `src/zhiyi/adapters/persistence/postgresql_run_repository.py`
- [X] T033 [US2] Complete immediate command/global-event uniqueness, per-Run sequence uniqueness, deferred receipt ownership, and safe constraint names consistently in `src/zhiyi/adapters/persistence/postgresql_schema.py` and `migrations/versions/0001_create_run_repository.py`
- [X] T034 [US2] Run the complete SC-003 and SC-004 matrices in `tests/integration/persistence/test_postgresql_concurrency.py`; record group, attempt, replay, conflict, duplicate, and partial-row counts in `specs/005-postgresql-run-repository/drift-report.md`
- [X] T035 [US2] Run the complete SC-005 matrix in `tests/integration/persistence/test_postgresql_faults.py`; prove each original-command replay converges and record classification/atomicity evidence in `specs/005-postgresql-run-repository/drift-report.md`

**Checkpoint**: Cross-instance command correctness and storage failure semantics are
independently proven without claiming external side-effect exactly-once behavior.

---

## Phase 5: User Story 3 - 租户安全地查询和回放事件 (Priority: P1)

**Goal**: Make every Run/Event/Receipt path tenant-safe, preserve globally unique event
identity without using it as authorization, and replay stable unbounded event sequences.

**Independent Test**: Create colliding Run/command identities across tenants and a
globally colliding event identity, execute the complete read/write/conflict/pagination
matrix, and inspect all returned values/errors/logs for zero owner or payload leakage.

### Tests for User Story 3

- [X] T036 [P] [US3] Write failing module-level-`postgresql` same-ID cross-tenant load/update/find-command/event-stream, missing-versus-foreign result-shape, conflict-owner non-disclosure, and replay ownership tests in `tests/integration/persistence/test_postgresql_tenant_isolation.py`
- [X] T037 [P] [US3] Extend the PostgreSQL contract with negative/over-tail cursors, 1/100/1,000 limits, multi-page exact-once replay, sequences above signed 64-bit range, and cross-tenant duplicate global `event_id` rejection in `tests/contract/persistence/test_postgresql_run_repository_contract.py`
- [X] T038 [P] [US3] Add fake DSN/password/SQL/parameter/event-payload/final-answer/hidden-reasoning markers across success, conflict, corruption, compatibility, known-failure, and unknown-outcome paths in `tests/integration/persistence/test_postgresql_faults.py`

### Implementation for User Story 3

- [X] T039 [US3] Audit and complete tenant predicates for every Run/Event/Receipt read, replay-event lookup, create/update, and conflict path plus digit-length/lexicographic event ordering in `src/zhiyi/adapters/persistence/postgresql_run_repository.py`
- [X] T040 [US3] Finalize tenant-bearing common/cursor/relationship indexes and global-event ownership defense without adding lease/queue columns in `src/zhiyi/adapters/persistence/postgresql_schema.py` and `migrations/versions/0001_create_run_repository.py`
- [X] T041 [US3] Implement bounded structured repository diagnostics, constant public error messages, correlation-only public `repr`, caller-supplied tenant/Run log fields, and SQL/parameter suppression in `src/zhiyi/adapters/persistence/postgresql_run_repository.py` and `src/zhiyi/infrastructure/database/engine.py`
- [X] T042 [US3] Run SC-006, SC-007, and SC-010 acceptance from `tests/integration/persistence/test_postgresql_tenant_isolation.py`, `tests/contract/persistence/test_postgresql_run_repository_contract.py`, and `tests/integration/persistence/test_postgresql_faults.py`; record zero leakage/duplication/gap counts in `specs/005-postgresql-run-repository/drift-report.md`

**Checkpoint**: Tenant-safe querying and event replay are independently usable; global
event identity never bypasses tenant/Run authorization.

---

## Phase 6: User Story 4 - 安全演进和回退持久化结构 (Priority: P2)

**Goal**: Establish an explicit repeatable schema release path, application compatibility
gate, disposable downgrade, and data-preserving restore exercise without startup DDL.

**Independent Test**: From empty disposable databases, upgrade/check/write/dump,
downgrade/re-upgrade one disposable copy, restore another fresh copy, and compare heads,
compatibility version, row counts, stable fact digests, and domain values.

### Tests for User Story 4

- [X] T043 [US4] Extend migration acceptance with repeated empty upgrade, `alembic check`, `current --check-heads`, no-application-DDL, partial/old/new compatibility states, destructive-target identity guard, dump, fresh restore, row-count/digest/domain comparison, and re-upgrade in `tests/integration/persistence/test_migrations.py`
- [X] T044 [P] [US4] Extend compatibility tests for per-engine cache lifetime, unreachable-before-version precedence, record corruption after compatible schema, and strict mixed-record-format behavior in `tests/unit/infrastructure/database/test_schema_compatibility.py`

### Implementation for User Story 4

- [X] T045 [US4] Complete reviewed expand/contract-compatible upgrade, compatibility version 1 handling, strict old-format emission boundary, reverse-order disposable downgrade, and no `create_all`/stamp path in `migrations/versions/0001_create_run_repository.py` and `src/zhiyi/infrastructure/database/schema_compatibility.py`
- [X] T046 [US4] Add a separate PostgreSQL 18.6 CI service job with health checks, explicit Alembic upgrade/head/autogenerate checks, collection-only assertions that the PostgreSQL set is non-empty and `tests/contract/persistence/test_postgresql_run_repository_contract.py`, `tests/integration/persistence/`, and `tests/performance/test_postgresql_run_repository.py` contribute zero fast-lane nodes, `pytest -m postgresql`, dump/restore tools, Ruff coverage for `migrations`, and a fast `not online and not postgresql` lane in `.github/workflows/runtime-python.yml`
- [X] T047 [US4] Execute the complete migration/restore acceptance in `tests/integration/persistence/test_migrations.py` and record image digest, destructive target proof, heads, compatibility version, row/digest/domain equality, and application-triggered DDL count in `specs/005-postgresql-run-repository/drift-report.md`

**Checkpoint**: Schema operations are explicit, repeatable, and recoverable in the
declared disposable environment; production backup/PITR provisioning remains out of scope.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Prove performance, supply-chain review, documentation alignment,
governance, and exact scope without expanding the Feature.

- [X] T048 [P] Write the non-skipped module-level-`postgresql` SC-011 performance acceptance with PostgreSQL 18.6, 100 Runs × 100 events, pool size 20/no overflow, 20 clients, 100 warm-ups, 1,000 samples per operation, nearest-rank percentiles, and environment evidence in `tests/performance/test_postgresql_run_repository.py`
- [X] T049 Run `pytest -m postgresql tests/performance/test_postgresql_run_repository.py`, preserve all constraints/tenant filters, and record p50/p95 plus CPU/memory/OS/image/pool evidence in `specs/005-postgresql-run-repository/drift-report.md`
- [X] T050 [P] Update the delivered PostgreSQL persistence/restart/error boundary while keeping Worker/lease/API/SSE unavailable in `doc/功能文档.md`
- [X] T051 [P] Document the concrete schema, receipt-first transaction, canonical numeric codec, failure precedence, migration/restore boundary, PostgreSQL support claim, locked dependency/license inventory, required notices, and pre-production SBOM/vulnerability/redistribution obligations in `doc/技术方案.md`
- [X] T052 [P] Update project status, M0 data milestone, supported PostgreSQL baseline, remaining Worker/lease scope, and release risks in `doc/PROJECT.md`
- [X] T053 [P] Update the repository capability/status tables and local PostgreSQL validation entry points without claiming a usable Runtime in `README.md`
- [X] T054 [P] Update PostgreSQL marker ownership, fast/real-database CI partition, explicit Alembic release checks, and the no-application-startup-DDL boundary in `doc/SDD开发规范.md` and `doc/AGENTS.md`
- [X] T055 Execute an explicit SQL/tenant security review covering every tenant-bearing query/index, command and global-event conflict ownership, application/migration privilege boundaries, DSN/SQL/parameter/payload logging, failure classification, and destructive migration guards; resolve and re-verify every critical/high finding and record scope, findings, disposition, reviewer, and evidence in `specs/005-postgresql-run-repository/drift-report.md`
- [X] T056 Finalize requirement/plan/task/test/dependency/architecture/migration/rollback/security evidence, `Docs-Impact: UPDATED`, all changed paths, explicit SC-012 excluded-behavior audit, and `ALIGNED` only after T055 and all other proof complete in `specs/005-postgresql-run-repository/drift-report.md`
- [X] T057 Run every command in `specs/005-postgresql-run-repository/quickstart.md`, then run frozen sync, full non-online tests, Ruff check/format over `src tests migrations`, strict mypy, governance unit tests, Alembic checks, and `scripts/sdd/check_design_drift.py --worktree --gate manual`; record actual exit evidence in `specs/005-postgresql-run-repository/drift-report.md`
- [X] T058 Execute `$speckit-converge`, append any remaining work to `specs/005-postgresql-run-repository/tasks.md`, finish appended tasks test-first, rerun affected checks, and only then mark Feature 005 complete

---

## Phase 8: Convergence — 提交前评审修复

**Purpose**: Close the six implementation gaps found after the first convergence pass,
without changing the approved persistence contract or adding Worker/lease behavior.

> **Test-first rule**: For T059–T064, add the named regression test and observe its
> intended failure before changing production or migration code. Mark this phase complete
> only after the complete PostgreSQL and performance lanes pass and a fresh convergence
> audit finds no remaining gap.

- [X] T059 [US2] Write failing engine-option and real-transaction isolation assertions in `tests/unit/infrastructure/database/test_engine.py` and `tests/integration/persistence/test_postgresql_concurrency.py`, then pin every application engine transaction to `READ COMMITTED` in `src/zhiyi/infrastructure/database/engine.py` so deployment-level defaults cannot change receipt-first replay semantics
- [X] T060 [US4] Write failing migration-source independence and exact physical-object inventory assertions in `tests/integration/persistence/test_migrations.py`, then rewrite `migrations/versions/0001_create_run_repository.py` as a self-contained immutable Alembic revision with no imports from mutable runtime metadata
- [X] T061 [US1] Write failing SQLSTATE classification and strict contract-version type tests in `tests/unit/infrastructure/database/test_schema_compatibility.py`, then make `src/zhiyi/infrastructure/database/schema_compatibility.py` classify only missing relation/column states as `schema_incompatible`, reject boolean/string/numeric impostors, and map permission or other operational failures to `storage_unavailable`
- [X] T062 [US2] Write failing real-PostgreSQL receipt/event tampering tests in `tests/integration/persistence/test_postgresql_faults.py`, then make replay in `src/zhiyi/adapters/persistence/postgresql_run_repository.py` fail closed with `data_corruption` unless the referenced Event type, sequence, status, and Run version agree with the immutable receipt
- [X] T063 [US2] Write a failing commit-preflight ordering test in `tests/unit/adapters/persistence/test_postgresql_preflight.py`, then pre-encode the complete Run/receipt/Event candidate before schema access, receipt arbitration, or Run locks in `src/zhiyi/adapters/persistence/postgresql_run_repository.py`
- [X] T064 [US1] Write failing default/read-timeout option tests in `tests/unit/infrastructure/database/test_engine.py` and a real blocked-read timeout classification test in `tests/integration/persistence/test_postgresql_faults.py`, then configure a finite per-session statement timeout in `src/zhiyi/infrastructure/database/engine.py` while preserving the commit-local timeout and stable `storage_unavailable` mapping

---

## Requirement and Success-Criteria Traceability

| Requirement / criterion | Primary tasks |
|---|---|
| FR-001–FR-002, FR-005, FR-008–FR-009, FR-014, SC-001–SC-002, SC-008 | T003, T006–T027 |
| FR-003–FR-011, FR-015–FR-016, SC-003–SC-005 | T003–T010, T025, T028–T035, T059, T062–T063 |
| FR-010–FR-013, FR-017, SC-006–SC-007, SC-010 | T036–T042, T055–T057 |
| FR-018–FR-021, SC-009 | T003, T013–T015, T018, T022–T023, T043–T047, T054–T057, T060–T061 |
| FR-022–FR-023 | T006–T010, T018–T027, T039–T041, T055–T064 |
| FR-024–FR-025, SC-012 | T001, T026, T040, T042, T047, T050–T058 |
| SC-011 | T048–T049, T057 |

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on Setup and blocks every PostgreSQL story.
- **US1 (Phase 3)**: Depends on Foundational and creates the restart-persistent MVP.
- **US2 (Phase 4)**: Depends on the US1 adapter/schema and hardens the same transaction.
- **US3 (Phase 5)**: Depends on US1; recommended after US2 because both edit the repository transaction/query module.
- **US4 (Phase 6)**: Depends on the US1 migration and may be authored alongside US2/US3 where files do not overlap; final migration revision waits for their constraint/index changes.
- **Polish (Phase 7)**: Depends on every selected story and all reviewer checklist items remaining approved.
- **Convergence repair (Phase 8)**: Depends on the first convergence audit; its six
  regression slices must finish before the final full-lane and convergence gates.

### User story dependencies

```text
Setup -> Foundation -> US1 -> US2 -> US3 -> US4 -> Polish -> Convergence repair -> Final converge
```

US2, US3, and US4 each have an independent acceptance command after US1 supplies the
common database adapter. Sequential execution is recommended because correctness is
more important than parallel edits to `postgresql_run_repository.py` and the initial
migration.

### Within each story

- Write all story test tasks and observe the intended failures before implementation.
- Schema/codec/engine foundations precede repository methods.
- Receipt replay/conflict precedes Run version and invariant checks.
- Constraint and transaction changes precede stress/fault acceptance.
- Documentation and `ALIGNED` drift status follow actual green evidence, never precede it.

## Parallel Opportunities

- After T001 establishes the ledger, T002 and T003 touch independent setup files.
- T004/T005 and T011–T014 are independent test-authoring tasks.
- T018–T020 implement separate schema, codec, and engine modules after tests exist.
- T028–T030 author concurrency, fault, and mapping tests in separate files.
- T036–T038 author tenant, cursor, and leakage acceptance in separate files.
- T043/T044 cover migration integration and compatibility unit requirements separately.
- T048 and T050–T054 touch independent performance/document paths after stories are green.

## Parallel Examples

### User Story 1

```text
Task T011: persistence codec tests
Task T012: engine safety tests
Task T013: schema compatibility tests
Task T014: initial migration tests
```

### User Story 2

```text
Task T028: multi-connection concurrency matrix
Task T029: transaction fault-window matrix
Task T030: phase and SQLSTATE unit mapping
```

### User Story 3

```text
Task T036: tenant-isolation matrix
Task T037: cursor/global-event contract
Task T038: sensitive-marker matrix
```

### User Story 4

```text
Task T043: migration/backup/restore integration requirements
Task T044: compatibility-version unit requirements
```

## Implementation Strategy

### MVP first

1. Complete T001–T010.
2. Complete T011–T027 for User Story 1.
3. Stop and validate restart persistence independently against PostgreSQL 18.6.
4. Do not call it a Runtime: no Worker, lease, Checkpoint, API/SSE, or execution exists.

### Incremental delivery

1. US1: durable facts and restart replay.
2. US2: cross-instance atomicity, idempotency, and unknown-outcome convergence.
3. US3: complete tenant-safe query/event replay and redaction.
4. US4: migration, downgrade, restore, and CI release path.
5. Polish: performance, docs, governance, and converge.

### Completion gate

Implementation begins only after `$speckit-analyze` has no critical finding and the
user gives explicit implementation approval. Completion requires every task checked,
all real PostgreSQL evidence recorded, `drift-report.md` truthfully `ALIGNED`, and a
clean `$speckit-converge` audit.

## Notes

- `[P]` tasks change separate files and have no incomplete dependency.
- Custom checklist markers approve requirements quality only, not implementation.
- Use only disposable PostgreSQL data for downgrade/fault/restore exercises.
- Do not auto-migrate from application startup or retry unknown commits internally.
- Do not add Worker, lease, Reconciler, Checkpoint, API/SSE, SDK, Model Gateway
  integration, Tool execution, background loops, deployment, or production data work.
- Do not commit, push, deploy, or touch production without separate explicit authority.
