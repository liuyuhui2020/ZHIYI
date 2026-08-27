---

description: "Dependency-ordered, test-first implementation tasks for Worker Lease Kernel"
---

# Tasks: Worker Lease Kernel

**Input**: Design documents from /specs/006-worker-lease-kernel/

**Prerequisites**: spec.md, plan.md, research.md, data-model.md,
contracts/worker-lease-kernel.md, quickstart.md, and reviewer-approved
checklists/requirements.md and checklists/lease-safety.md

**Tests**: Mandatory. For every behavior slice, add the named test first, run it to
prove the intended failure, implement the smallest correct change, then refactor only
while the focused tests remain green. Real PostgreSQL evidence cannot be replaced by
an in-memory substitute.

**Organization**: Tasks are grouped by user story and name every planned implementation,
test, migration, CI, governance, and documentation path. No task implements a Worker
loop, LangGraph, Checkpoint, Agent/model/Tool/Graph execution, Reconciler, recovery,
public API/SDK, cleanup daemon, deployment, or production data operation.

## Format: [ID] [P?] [Story] Description

- **[P]**: Can run in parallel because it changes different files and has no dependency
  on another incomplete task in the same phase.
- **[Story]**: Maps directly to a user story in spec.md.

## Phase 1: Setup (Governed Existing Stack)

**Purpose**: Establish the live implementation ledger without changing the already
approved Python/PostgreSQL dependency set or claiming implementation approval.

- [X] T001 Create an IN_PROGRESS ledger with Docs-Impact: UPDATED, all approved 006 source/test/migration/CI/document paths, the 40/40 checklist result, the raw-token/NTP production blockers, M0 implementation-approval status, and every excluded behavior in specs/006-worker-lease-kernel/drift-report.md

**Checkpoint**: The work is traceable; no product behavior or schema exists yet.

---

## Phase 2: Foundational (Domain Ports, Safety Types, and Shared Transactions)

**Purpose**: Establish framework-neutral lease values, commands, outcomes, errors,
token generation, component compatibility, and transaction support required by every
story while preserving the existing 004/005 contracts.

**CRITICAL**: No user-story implementation begins until T002-T006 fail for the intended
missing behavior and T007-T015 make the focused foundation plus existing 005 tests green.

- [X] T002 [P] Write failing immutable-value tests for Worker/claim/token identifiers, complete authority proofs, 10/30-second bounds and default, first/monotonic attempt and lease versions, microsecond-floor renew-by math, inactive cursor tenant binding, redacted token str/repr, and malformed input in tests/unit/domain/worker_leases/test_values.py
- [X] T003 [P] Write failing normalization and intent-format-version-1 tests for claim, renew, and release commands, including default expansion, length-prefixed fingerprints, UUIDv7 type/version/variant checks, and prohibition of Worker time in tests/unit/application/commands/test_worker_leases.py
- [X] T004 [P] Write failing stable-code, constant-message, exception-chaining, caller-ID-only repr, and sensitive-field exclusion tests for invalid_input, idempotency_conflict, idempotency_expired, lease_not_current, lease_expired, storage_unavailable, commit_outcome_unknown, data_corruption, and schema_incompatible in tests/unit/application/ports/test_worker_lease_errors.py
- [X] T005 [P] Write failing service/port-delegation, may_start_new_work, immutable terminal-observation, exactly-once three-channel fan-out, safe-field allowlist, post-cleanup ordering, and independent channel-failure tests proving only current authority is true and every noncurrent, expired, non-applied, storage, and unknown outcome is false without starting or interrupting work in tests/unit/application/services/test_worker_lease_kernel.py and tests/unit/application/ports/test_worker_lease_observability.py
- [X] T006 [P] Write failing 32-byte CSPRNG, injected deterministic generator, 100,000-value uniqueness, no-deliberate-reuse, digest comparison, and zero token/digest printable sentinel tests in tests/unit/infrastructure/security/test_lease_tokens.py
- [X] T007 Implement immutable identifiers, authority proofs, grants/outcomes, conditional mutation results, inactive observations/cursors/pages, record constants, and stable lease errors without outer-layer imports in src/zhiyi/domain/worker_leases/identifiers.py, src/zhiyi/domain/worker_leases/models.py, src/zhiyi/domain/worker_leases/errors.py, and src/zhiyi/domain/worker_leases/__init__.py
- [X] T008 Implement normalized claim/renew/release commands, the LeaseTokenGenerator port, required WorkerLeaseTelemetry port plus immutable terminal observation, WorkerLeaseRepository protocol, and stronger LeaseGuardedRunRepository protocol without changing RunRepository.commit or MemoryRunRepository in src/zhiyi/application/commands/worker_leases.py, src/zhiyi/application/ports/lease_token_generator.py, src/zhiyi/application/ports/worker_lease_observability.py, and src/zhiyi/application/ports/worker_lease_repository.py
- [X] T009 Implement the framework-neutral WorkerLeaseKernel application service, intent fingerprinting, renew-by calculation, and safe outcome propagation without any polling/execution loop in src/zhiyi/application/services/worker_lease_kernel.py
- [X] T010 Implement production 32-byte token generation and constant-time SHA-256 digest comparison with no persistence/logging behavior in src/zhiyi/infrastructure/security/lease_tokens.py and src/zhiyi/infrastructure/security/__init__.py
- [X] T011 [P] Write failing per-engine/per-component cache, accepted-version-set, strict-int contract version, missing/partial/unreachable precedence, zero-DDL, and legacy run_repository wrapper tests in tests/unit/infrastructure/database/test_worker_lease_schema_compatibility.py and tests/unit/infrastructure/database/test_schema_compatibility.py
- [X] T012 Generalize read-only schema compatibility by engine plus component while retaining ensure_schema_compatible(engine) for 005 callers and mapping malformed/operational states safely in src/zhiyi/infrastructure/database/schema_compatibility.py
- [X] T013 [P] Write failing transaction-phase, SQLSTATE, finite 1-5,000 ms lock/1-10,000 ms statement timeout validation, READ COMMITTED, synchronous_commit, receipt-first replay, Run-to-Lease lock-order, no-auto-retry, post-transaction/connection terminal-observation, three-channel delivery, and channel-failure isolation tests in tests/unit/adapters/persistence/test_postgresql_worker_lease_error_mapping.py, tests/unit/adapters/persistence/test_postgresql_preflight.py, and tests/unit/adapters/persistence/test_postgresql_worker_lease_observability.py
- [X] T014 Extract shared transaction phase/settings/SQLSTATE, same-connection 004 receipt arbitration/commit mechanics, safe terminal-observation construction, and post-cleanup isolated telemetry fan-out into src/zhiyi/adapters/persistence/postgresql_transaction_support.py; refactor src/zhiyi/adapters/persistence/postgresql_run_repository.py to use the transaction helpers without changing ordinary 005 behavior, update the shared classification import in tests/unit/adapters/persistence/test_postgresql_error_mapping.py, and keep all existing RunRepository contract/concurrency/fault tests green
- [X] T015 Export only framework-neutral lease commands, ports, service, values, and errors through src/zhiyi/application/commands/__init__.py, src/zhiyi/application/ports/__init__.py, src/zhiyi/application/services/__init__.py, and src/zhiyi/domain/__init__.py; run T002-T006, T011, T013 plus the complete existing 004/005 unit and contract suites

**Checkpoint**: The framework-neutral safety boundary and shared transaction mechanics
exist, but no lease table or claim operation is available.

---

## Phase 3: User Story 1 - 独占领取一个待执行 Run (Priority: P1) 🎯 Claim MVP

**Goal**: Issue an immutable claim ID and atomically return at most one tenant-scoped
queued Run with a persistent, exactly replayable short lease while leaving all 004 Run
lifecycle facts unchanged.

**Independent Test**: Migrate a disposable PostgreSQL 18.6 database, race 20 independent
Worker identities for one queued Run, and prove one current lease, exact same-ID
claimed/no-work replay, deterministic FIFO without contention, zero duplicate/omission,
and zero Run/Event/004 receipt changes.

### Tests for User Story 1

> Write T016-T020 first and observe failure because the two-table schema, codecs,
> migration, fixtures, and claim adapter do not exist.

- [X] T016 [P] [US1] Create the non-collected provider-neutral repository contract and failing PostgreSQL binding for required telemetry injection, issue_claim_id, claim/no_work, exact immutable replay plus recomputed currently_authoritative, different-intent conflict, duration/default validation, lease-only lifecycle immutability, and one safe terminal observation per channel in tests/contract/persistence/worker_lease_repository_contract.py and tests/contract/persistence/test_postgresql_worker_lease_repository_contract.py
- [X] T017 [P] [US1] Write failing PostgreSQL UUIDv7 round-trip, exact +60s/+60s+1ms and 24h-1ms/24h/24h+1ms boundaries, database-clock-unavailable precedence, same/different-tenant ID scope, claimed/no-work receipt replay, and post-receipt-deletion expiry tests in tests/integration/persistence/test_postgresql_worker_lease_claim.py
- [X] T018 [P] [US1] Write failing 100 one-Run groups by 20 independent Workers, 100-Run drain by 20 clients, 100-way same-claim claimed/no-work arbitration, deterministic uncontended FIFO, temporary-lock fairness, and zero lifecycle mutation tests; additionally lock the sole oldest eligible Run from an external connection and prove the bounded blocking head probe returns storage_unavailable with zero no-work receipt until the lock is released in tests/integration/persistence/test_postgresql_worker_lease_concurrency.py
- [X] T019 [P] [US1] Write failing 0001-to-0002/empty-head smoke tests for exactly worker_leases plus worker_lease_claim_receipts, every named key/check/expression/cleanup index, both compatibility rows, unchanged 0001 history, and disposable downgrade in tests/integration/persistence/test_worker_lease_migrations.py and tests/integration/persistence/test_migrations.py
- [X] T020 [P] [US1] Write failing format-version-1 lease/claimed/no-work receipt codec, intent-version/nullability/time/counter/digest/token projection, corruption, safe repr, and SQLAlchemy metadata inventory tests in tests/unit/adapters/persistence/test_postgresql_worker_lease_codecs.py

### Implementation for User Story 1

- [X] T021 [US1] Define the two tenant-bearing SQLAlchemy Core tables, named constraints, current-lease keys, retained counters, inactive expression index, receipt cleanup index, restricted replay-token column, and complete record codecs in src/zhiyi/adapters/persistence/postgresql_worker_lease_schema.py and src/zhiyi/adapters/persistence/postgresql_worker_lease_codecs.py
- [X] T022 [US1] Load the complete reviewed metadata in migrations/env.py and add the self-contained additive upgrade plus reverse disposable downgrade and worker_lease_kernel=1 compatibility row in migrations/versions/0002_create_worker_lease_kernel.py without editing migrations/versions/0001_create_run_repository.py
- [X] T023 [US1] Extend real PostgreSQL fixtures for explicit 0002 migration, independent connections/engines, deterministic captured database time and commit-boundary injection, and explicit cleanup of both 006 tables including no-work receipts in tests/integration/persistence/conftest.py and tests/contract/persistence/test_postgresql_run_repository_contract.py
- [X] T024 [US1] Implement component-gated PostgreSQL UUIDv7 issuance and the complete short READ COMMITTED claim transaction with local UUID validation, database age precedence, tenant FIFO SKIP LOCKED fast-path selection, a same-transaction ordered blocking oldest-eligible probe before any no-work receipt, timeout-to-storage_unavailable mapping, Run-to-Lease recheck, random token generation before locks, monotonic replacement, complete receipt-at-end arbitration, loser rollback/new-transaction replay, no lifecycle mutation, and post-cleanup terminal telemetry in src/zhiyi/adapters/persistence/postgresql_worker_lease_repository.py
- [X] T025 [US1] Export PostgreSQL lease assembly without leaking SQLAlchemy/Psycopg types through src/zhiyi/adapters/persistence/__init__.py and src/zhiyi/infrastructure/database/__init__.py
- [X] T026 [US1] Run the US1 contract, claim, concurrency, codec, and migration suites against fresh PostgreSQL 18.6; record SC-001/SC-002/SC-004 claim counts, UUID boundaries, fast-path plus bounded-head-probe behavior, receipt race rollback, terminal telemetry evidence, and zero lifecycle changes in specs/006-worker-lease-kernel/drift-report.md

**Checkpoint**: User Story 1 is an independently testable claim coordination slice.
It is not a safety-complete Worker runtime and starts no work.

---

## Phase 4: User Story 2 - 续租、释放与 fencing 旧所有者 (Priority: P1) 🎯 Safety MVP

**Goal**: Provide current-authority reads, monotonic renew/release, explicit stop-new-work
signals, and a same-transaction fencing guard for new Worker-produced 004 writes while
preserving ordinary 004 replay and zero-event semantics.

**Independent Test**: Claim one Run, exercise valid/stale/wrong/expired/replaced proofs,
100 same-version renew/release races, active release, waiting/terminal cleanup, and
guarded writes racing expiry/renew/release/cancel/terminal transitions; prove no old
proof regains authority and no partial Run/Event/receipt/lease combination survives.

### Tests for User Story 2

- [X] T027 [P] [US2] Extend application-service tests with current/noncurrent/expired authority, valid/stale renew, release, waiting/terminal cleanup, exact retry conditions, microsecond renew-by, may_start_new_work outcomes, and safe terminal observation outcomes in tests/unit/application/services/test_worker_lease_kernel.py
- [X] T028 [P] [US2] Extend the provider-neutral/PostgreSQL contract with get_authority, renew, release, complete proof matching, stale expected version confirmation, default/10/30-second boundaries, expiry equality, no lease-operation receipts, and one safe terminal observation per channel in tests/contract/persistence/worker_lease_repository_contract.py and tests/contract/persistence/test_postgresql_worker_lease_repository_contract.py
- [X] T029 [P] [US2] Write failing 100 ownership cycles, 100 same-version renew races, 100 same-version release races, random-token collision injection, Run status races, active-release re-claim, never-reset attempt/version, and no-ABA assertions in tests/integration/persistence/test_postgresql_worker_lease_concurrency.py
- [X] T030 [P] [US2] Write failing guarded new-write/replay tests for full proof, expiry equality, replacement, concurrent valid renewal, release, cancel/terminal transition, expected Run version, zero-event no-version-consumption, ordinary 004/rolling-005 replay priority, every partial fact combination, and post-cleanup terminal telemetry in tests/integration/persistence/test_postgresql_worker_lease_guard.py

### Implementation for User Story 2

- [X] T031 [US2] Implement tenant-safe get_authority plus Run-to-Lease locked conditional renew/release with captured PostgreSQL time, complete proof digest comparison, never-reset lease version, allowed status matrix, release cleanup exception, microsecond-floor guidance, safe stale-version confirmation, and post-cleanup terminal telemetry in src/zhiyi/adapters/persistence/postgresql_worker_lease_repository.py
- [X] T032 [US2] Implement commit_with_lease in src/zhiyi/adapters/persistence/postgresql_run_repository.py using the shared receipt-arbiter-to-Run-to-Lease lock order and same connection: preserve ordinary 004 replay first, validate current complete proof and expected Run version only for new writes, commit Run/zero-or-one Event/004 receipt atomically, never renew/release the lease, and emit terminal telemetry only after transaction/connection cleanup
- [X] T033 [US2] Run focused service, contract, ownership-cycle, same-version race, and guarded-commit suites; record SC-003 matrices, zero unauthorized success, monotonic counters, collision-safe old-proof fencing, zero-event behavior, terminal telemetry, and zero partial writes in specs/006-worker-lease-kernel/drift-report.md

**Checkpoint**: US1 plus US2 is the smallest safety-complete persistence kernel. It
still contains no Worker loop, Agent execution, external effect, or recovery behavior.

---

## Phase 5: User Story 3 - 在进程故障与结果未知后安全收敛 (Priority: P1)

**Goal**: Preserve lease facts across process recreation, recover from operation-specific
unknown commits without blind extension, reclaim only expired/released queued work, and
observe inactive running candidates without changing or recovering them.

**Independent Test**: Dispose and recreate engines around expiry, execute every
pre-commit/confirmed-rollback/real-commit-lost-ack window 100 times for claim, renew,
release, and guarded commit, and page 1,000 inactive running candidates with fixed
as_of semantics; prove one converged fact set and zero recovery side effects.

### Tests for User Story 3

- [X] T034 [P] [US3] Write failing engine/repository dispose-and-reopen tests before and after expiry for retained leases, receipts, queued re-claim, higher attempts/versions, exact replay, and zero implicit release/extension in tests/integration/persistence/test_postgresql_worker_lease_restart.py
- [X] T035 [P] [US3] Write failing single/list inactive-running tests for natural expiry and active release, minimal projection, expression-keyset order, 1/100/1,000 bounds, invalid/cross-tenant cursors, fixed as_of, deterministic repeat, static zero-gap, allowed concurrent-removal gaps, zero mutation/recovery, and one safe terminal observation per channel in tests/integration/persistence/test_postgresql_worker_lease_expiry.py
- [X] T036 [P] [US3] Write failing 100-iteration pre-commit, confirmed rollback/backend termination, and real-COMMIT/suppressed-ack windows for claim, renew, release, and guarded commit, including unchanged/advanced/changed proof, expiry/status transition, second failure, original-ID/intent convergence, no internal retry, terminal failure observations, and telemetry-channel exception isolation in tests/integration/persistence/test_postgresql_worker_lease_faults.py

### Implementation for User Story 3

- [X] T037 [US3] Implement tenant-bound get_inactive_running and list_inactive_running using one captured database as_of, COALESCE(released_at, lease_expires_at) keyset order, limit+1, no OFFSET/fallback scan, minimal projection, no locks/writes, and post-cleanup terminal telemetry in src/zhiyi/adapters/persistence/postgresql_worker_lease_repository.py and src/zhiyi/adapters/persistence/postgresql_worker_lease_codecs.py
- [X] T038 [US3] Complete phase-plus-SQLSTATE failure mapping, connection invalidation, known-rollback versus unknown-ack handling, claim original-receipt convergence, renew/release read-before-same-condition retry, guarded 004 replay convergence, fail-closed second-error safety, and isolated post-cleanup error telemetry in src/zhiyi/adapters/persistence/postgresql_transaction_support.py, src/zhiyi/adapters/persistence/postgresql_worker_lease_repository.py, and src/zhiyi/adapters/persistence/postgresql_run_repository.py
- [X] T039 [US3] Run restart, inactive-observation, and full fault-window suites; record SC-005/SC-006/SC-013 iteration counts, classifications, convergence states, pagination gaps/duplicates/order, old-proof failures, terminal failure telemetry, telemetry-channel isolation, and zero Run recovery mutations in specs/006-worker-lease-kernel/drift-report.md

**Checkpoint**: Process loss and database uncertainty converge safely; running work is
only observed, never reclaimed, rewound, or resumed.

---

## Phase 6: User Story 4 - 租户安全地观察和演进租约事实 (Priority: P2)

**Goal**: Prove every operation is tenant-indistinguishable and redacted, migrate and
roll back explicitly without application DDL, preserve 005 rolling compatibility, and
restore logical facts without resuming stale authority.

**Independent Test**: Use two tenants with colliding identifiers across every operation,
inject sensitive sentinels and partial schemas, then upgrade/dump/disposable-downgrade/
re-upgrade/fresh-restore; prove zero leakage, zero startup DDL, preserved 005 facts, and
no restored lease authority.

### Tests for User Story 4

- [X] T040 [P] [US4] Write failing cross-tenant matrices for issue/claim/replay/authority/renew/release/guarded commit/inactive single/list/cursor with caller-ID-only echo and no discovered owner/claim/version/expiry/token disclosure in tests/integration/persistence/test_postgresql_worker_lease_tenant_isolation.py
- [X] T041 [P] [US4] Extend migration tests with 0001-only old-005/new-006 behavior, additive 0002 rolling coexistence, missing compatibility/table/index/constraint partial states, no constructor DDL, current/check-heads/alembic-check, sensitive temporary dump, disposable downgrade/re-upgrade, fresh restore, database-clock quarantine, old-connection drain evidence, and preserved 005 facts in tests/integration/persistence/test_worker_lease_migrations.py and tests/integration/persistence/test_migrations.py
- [X] T042 [P] [US4] Extend compatibility tests for independent run_repository/worker_lease_kernel cache keys, strict accepted versions, malformed physical inventory, permission/unreachable precedence, and no repair/create/upgrade behavior in tests/unit/infrastructure/database/test_worker_lease_schema_compatibility.py
- [X] T043 [P] [US4] Plant raw token, digest, claim fingerprint, DSN/credentials, SQL/parameters, Worker/claim owner, Run payload/result, final answer, and hidden-reasoning sentinels through success/conflict/expiry/corruption/schema/storage/unknown paths and printable values; prove every public operation emits exactly one immutable terminal observation to each required log/metric/trace channel after cleanup, exposes only allowlisted safe fields, attempts remaining channels after one channel raises, preserves the business outcome, and performs no retry/write in tests/integration/persistence/test_postgresql_worker_lease_tenant_isolation.py, tests/integration/persistence/test_postgresql_worker_lease_faults.py, tests/unit/adapters/persistence/test_postgresql_worker_lease_codecs.py, and tests/unit/adapters/persistence/test_postgresql_worker_lease_observability.py

### Implementation for User Story 4

- [X] T044 [US4] Audit and harden tenant predicates, missing-versus-foreign shapes, caller-supplied-only diagnostics, restricted replay-token projection, digest/fingerprint handling, safe exception chaining, SQL/parameter suppression, required telemetry injection, safe terminal-observation construction, and post-cleanup independently isolated log/metric/trace fan-out across src/zhiyi/application/ports/worker_lease_observability.py, src/zhiyi/adapters/persistence/postgresql_transaction_support.py, src/zhiyi/adapters/persistence/postgresql_worker_lease_repository.py, src/zhiyi/adapters/persistence/postgresql_worker_lease_codecs.py, src/zhiyi/adapters/persistence/postgresql_run_repository.py, src/zhiyi/infrastructure/security/lease_tokens.py, and src/zhiyi/infrastructure/database/engine.py
- [X] T045 [US4] Complete component-aware physical-inventory fail-closed checks and immutable 0002 upgrade/downgrade compatibility without changing 0001 or auto-migrating in src/zhiyi/infrastructure/database/schema_compatibility.py, src/zhiyi/adapters/persistence/postgresql_worker_lease_schema.py, migrations/env.py, and migrations/versions/0002_create_worker_lease_kernel.py
- [X] T046 [US4] Run tenant, positive telemetry, telemetry-channel isolation, redaction, compatibility, migration, downgrade/re-upgrade, and fresh-restore acceptance; securely remove temporary dumps and record SC-007/SC-009/SC-010/SC-014 evidence, zero DDL/leaks, both compatibility versions, preserved 005 facts, restored-clock proof, and explicit non-production retention/NTP blockers in specs/006-worker-lease-kernel/drift-report.md

**Checkpoint**: The kernel is tenant-safe and structurally repeatable in the disposable
test boundary; it is still blocked from production by raw-token retention and NTP
operational prerequisites.

---

## Phase 7: Polish and Cross-Cutting Verification

**Purpose**: Close performance, documentation, security, governance, and convergence evidence without expanding Feature 006 into Worker execution.

- [X] T047 [P] Add PostgreSQL lease-kernel performance coverage for the complete issue_claim_id-plus-claim flow, renew, get_authority, and release in tests/performance/test_postgresql_worker_lease_kernel.py: use exactly 10,000 eligible Runs, a pool of 20 with zero overflow, 20 concurrent clients, 100 warmups and 1,000 measured samples per operation, nearest-rank p50/p95 calculations, and a p95 below 200 ms for every required operation; record the PostgreSQL image, host resources, pool/timeouts, row counts, query plans, and lock waits, with guarded-write latency optional and never a replacement for a required operation
- [X] T048 Run the Feature 006 performance suite, record every required operation's p50/p95, sample count, environment, query plan, lock-wait evidence, and SC-008 pass/fail thresholds in specs/006-worker-lease-kernel/drift-report.md, and adjust only the measured CI timeout budget in .github/workflows/runtime-python.yml if required
- [X] T049 Extend the existing PostgreSQL CI job only after the performance module exists, adding explicit 0002 upgrade/head/autogenerate checks, nonempty module collection assertions for every 006 contract/integration/performance path, the unchanged fast-lane exclusion, migration Ruff coverage, and no skipped acceptance in .github/workflows/runtime-python.yml
- [X] T050 [P] Document local migration, focused test, fault-injection, and performance commands plus the M0-only token-retention warning in README.md
- [X] T051 [P] Synchronize the Worker Lease Kernel scope, exclusions, and production blockers in doc/PROJECT.md
- [X] T052 [P] Synchronize claim, renew, release, guarded-write, and replay behavior in doc/功能文档.md
- [X] T053 [P] Synchronize the two-table model, transaction order, fencing rules, compatibility component, and rollout boundary in doc/技术方案.md
- [X] T054 Review token secrecy, receipt retention, timeout bounds, SQLSTATE mapping, least-privilege migration separation, positive terminal telemetry, telemetry-channel failure isolation, and log/metric/trace redaction; record findings and dispositions in specs/006-worker-lease-kernel/drift-report.md
- [X] T055 Complete specs/006-worker-lease-kernel/drift-report.md with task, test, migration, documentation, and scope evidence; mark ALIGNED only after every required gate is green
- [X] T056 Execute all static, focused, full PostgreSQL, performance, migration, quickstart, design-drift, and SDD governance commands documented in specs/006-worker-lease-kernel/quickstart.md and record the results in specs/006-worker-lease-kernel/drift-report.md
- [X] T057 Execute speckit-converge for specs/006-worker-lease-kernel, append any missing work as new unchecked tasks in specs/006-worker-lease-kernel/tasks.md, complete and verify those tasks, and only then mark them checked before claiming completion

---

## Requirement Traceability

| Requirement group | Primary tasks |
|---|---|
| FR-001 through FR-008, FR-014 through FR-015, FR-022 through FR-023; SC-001, SC-002, SC-004, SC-011 | T002-T026 |
| FR-009 through FR-012, FR-016 through FR-017; SC-003 | T002-T015, T027-T033 |
| FR-007, FR-013, FR-020 through FR-021, FR-026; SC-005, SC-006, SC-013 | T034-T039 |
| FR-018 through FR-020, FR-024 through FR-027; SC-007, SC-009, SC-010 | T011-T014, T019-T025, T040-T046, T049, T054-T056 |
| FR-028 through FR-029; SC-012 | T001, T050-T056 |
| SC-008 | T047-T048 |
| FR-030; SC-014 | T004-T005, T008-T014, T016, T018, T024, T027-T044, T046, T054-T056 |

## Dependencies and Execution Order

- Phase 1 Setup precedes Phase 2 Foundation.
- Phase 2 Foundation blocks all user stories.
- User Story 1 Claim precedes User Story 2 Fencing because guarded writes require an issued lease.
- User Story 2 Fencing precedes User Story 3 Convergence because convergence tests depend on authoritative fencing behavior.
- User Story 3 Convergence precedes User Story 4 Safety and Rollout because failure classification must be stable before final operational gates.
- Phase 7 follows all user stories.
- Phase 7 performance implementation T047 precedes measured execution T048, and both precede the CI collection/timeout gate T049.
- User Story 1 is independently demonstrable, but the recommended safety MVP is User Story 1 plus User Story 2 so claims cannot be used without write fencing.

## Parallel Opportunities

- T002-T006 can proceed in parallel after T001.
- T011 and T013 can proceed in parallel after the schema and error vocabulary are fixed.
- T016-T020 can proceed in parallel as failing claim-contract tests before claim implementation begins.
- T027-T030 can proceed in parallel as failing fencing tests before guarded-write implementation begins.
- T034-T036 can proceed in parallel as failing convergence tests before error-classification implementation begins.
- T040-T043 can proceed in parallel as safety and rollout test additions.
- T047 and T050-T053 can proceed in parallel after all functional stories are green; T048 follows T047, and T049 follows both the performance file and measured timeout evidence.

## Implementation Strategy

- Complete T001-T015 first to freeze schema, ports, error vocabulary, compatibility, and shared test infrastructure.
- Implement each story test-first: write and observe the required failing tests, add the minimum production code, then rerun the focused and PostgreSQL suites.
- Treat T001-T033 as the recommended safety MVP: claim plus lease-guarded writes. Do not ship claim capability without fencing.
- Run speckit-analyze and obtain explicit implementation approval before starting T002 or any other product-code task.
- Completion requires every task checked, real PostgreSQL evidence recorded, production blockers left explicit, the drift report aligned, and speckit-converge clean.

## Notes

- Do not edit the existing 0001 migration; Feature 006 uses a new expand-only migration.
- Do not add a worker lease command guard table; the approved physical model contains only worker_leases and worker_lease_claim_receipts.
- Do not add an observability exporter, telemetry database table, or silent no-op telemetry default; Feature 006 defines a required framework-neutral port and host-provided log/metric/trace channels only.
- Do not let the application create or migrate schema at runtime.
- Do not claim production readiness while raw lease tokens have no bounded retention or encryption-at-rest design.
- Do not implement Worker loops, LangGraph, Checkpoint, Agent or model execution, tool or graph execution, Reconciler, recovery orchestration, public API or SDK, cleanup daemons, or deployment in Feature 006.
- Commit, push, merge, and deployment remain separate user-authorized actions.

## Phase 8: Convergence

- [X] T058 Extend tests/integration/persistence/test_postgresql_worker_lease_concurrency.py to prove the uncontended stable FIFO order across exactly 100 queued Runs, retaining the bounded locked-head probe and zero omission/duplicate assertions per SC-002 (partial)
- [X] T059 Extend tests/integration/persistence/test_postgresql_worker_lease_claim.py, tests/integration/persistence/test_postgresql_worker_lease_concurrency.py, and tests/integration/persistence/test_postgresql_worker_lease_guard.py with 100-iteration complete ownership/renew/repeat/wrong-token/expiry/old-token/release/reclaim/guard matrices, 100 repetitions of every default/10/30/under/over duration and UUID future/replay boundary, and 100 independent concurrent same-version renew plus release groups per SC-003 and SC-004 (partial)
- [X] T060 Extend tests/integration/persistence/test_postgresql_worker_lease_restart.py with at least 100 dispose/recreate acceptance cycles proving zero pre-expiry takeover, post-expiry queued reclaim, monotonic counters, exact receipt replay, and permanent old-proof fencing per SC-006 (partial)
- [X] T061 Extend tests/integration/persistence/test_postgresql_worker_lease_expiry.py so a mixed 1,000-candidate real PostgreSQL set is traversed with default, 1, 100, and 1,000 page sizes plus fixed-as_of mutation, single-read, order, gap/duplicate/noncandidate/tenant and zero-write assertions per SC-013 (partial)
- [X] T062 Extend tests/contract/persistence/worker_lease_repository_contract.py, tests/integration/persistence/test_postgresql_worker_lease_faults.py, tests/integration/persistence/test_postgresql_worker_lease_tenant_isolation.py, tests/integration/persistence/test_postgresql_worker_lease_expiry.py, tests/integration/persistence/test_postgresql_worker_lease_guard.py, and tests/unit/application/ports/test_worker_lease_observability.py with a traceable public-operation/outcome matrix proving exactly one post-cleanup safe terminal observation in log/metric/trace, independent failure of each channel, unchanged business outcomes, no retry/partial write, and zero forbidden fields per SC-014 (partial)

## Phase 9: Hosted CI Performance-Boundary Repair

**Purpose**: Repair the post-merge CI policy mismatch without changing any product
behavior, performance threshold, sample count, concurrency, durability, or security
invariant. The shared runner remains a complete PostgreSQL functional gate; absolute
latency remains a fixed-environment acceptance gate.

- [X] T063 Synchronize the approved fixed-performance-environment and shared-CI boundary in specs/006-worker-lease-kernel/spec.md, specs/006-worker-lease-kernel/plan.md, specs/006-worker-lease-kernel/checklists/requirements.md, specs/006-worker-lease-kernel/quickstart.md, specs/006-worker-lease-kernel/drift-report.md, README.md, doc/AGENTS.md, doc/PROJECT.md, and doc/SDD开发规范.md
- [X] T064 Run speckit-analyze against the updated Feature 006 artifacts and resolve any critical conflict before implementation
- [X] T065 Test-first prove `postgresql and performance` does not yet collect the 005/006 latency modules, then add the registered module-level performance marker without changing their workloads or assertions in tests/performance/test_postgresql_run_repository.py and tests/performance/test_postgresql_worker_lease_kernel.py
- [X] T066 Update .github/workflows/runtime-python.yml so the shared PostgreSQL job separately proves the performance selection is nonempty and isolated, runs all `postgresql and not performance` nodes with zero skips, and never claims fixed-environment latency acceptance
- [X] T067 Run frozen sync, collection-partition checks, fast/static/SDD gates, the full non-performance PostgreSQL lane, and both unchanged performance modules in the recorded local acceptance environment; record exact evidence in specs/006-worker-lease-kernel/drift-report.md
- [X] T068 Run speckit-converge after all Phase 9 evidence is recorded, resolve every missing/partial/contradictory/unrequested finding, restore specs/006-worker-lease-kernel/drift-report.md to ALIGNED, and pass the manual design-drift gate before the separately authorized commit/push and main-CI verification
