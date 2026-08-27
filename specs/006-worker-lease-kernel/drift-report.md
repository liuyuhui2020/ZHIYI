# Design Drift Report

**Feature**: 006-worker-lease-kernel
**Status**: ALIGNED
**Docs-Impact**: UPDATED
**Docs-Updated**: README.md, doc/AGENTS.md, doc/PROJECT.md, doc/SDD开发规范.md,
doc/功能文档.md, doc/技术方案.md
**Docs-Impact-Reason**: Feature 006 adds a PostgreSQL-backed Worker lease coordination
kernel and a lease-guarded Run write boundary while deliberately excluding every Worker
execution loop, Agent/Graph/Tool execution, Checkpoint, Reconciler, API/SDK, deployment,
and production data operation.
**Reviewed-By**: AI
**Implementation-Approval**: Explicitly approved by the user in the active task on
2026-08-27.

## Implementation Gate

- The active specification, plan, research, data model, contract, quickstart, tasks,
  constitution, and project engineering instructions were read before implementation.
- `checklists/lease-safety.md` is complete at 40/40 items.
- `checklists/requirements.md` is complete at 16/16 items.
- The pre-implementation cross-artifact analysis found zero unresolved conflicts or
  coverage gaps after the approved repairs.
- The user explicitly approved M0 product implementation and directed execution from
  T001, with tests written before production behavior from T002 onward.
- Every required task, test, migration, performance, documentation, governance, and
  convergence gate was complete before the post-merge CI remediation below. Phase 9 is
  also complete; the production enablement blockers below remain deliberately open and
  do not authorize rollout.

### Post-merge CI remediation gate (2026-08-27)

- Main commit `af4fc8f4752b13a85c07abed59c74d563b0f944c` passed SDD Governance,
  Documentation Website, frozen Python quality, all migrations, and 118 real PostgreSQL
  functional tests in GitHub Actions run `33034409677`.
- The same 4-vCPU heterogeneous hosted runner failed only the two absolute-latency
  modules: the 005 page-100/atomic-commit p95 values were 100.153/108.034 ms against
  100 ms, and the 006 full issue+claim p95 was 389.825 ms against 200 ms. The recorded
  fixed 10-logical-CPU local environment had already passed the unchanged workloads,
  including 006 issue+claim p95 133.701 ms.
- The user explicitly approved synchronizing the fixed-environment boundary through Spec
  Kit, retaining every threshold/sample/concurrency/safety requirement, running all
  non-performance PostgreSQL acceptance on shared CI, and then committing the verified
  repair to `main`. No deployment or production operation is authorized.

## Planned Change Set

- Feature artifacts: `specs/006-worker-lease-kernel/spec.md`, `plan.md`, `tasks.md`,
  `research.md`, `data-model.md`, `quickstart.md`, `drift-report.md`,
  `contracts/worker-lease-kernel.md`, `checklists/requirements.md`, and
  `checklists/lease-safety.md`.
- Domain paths: `src/zhiyi/domain/__init__.py` and
  `src/zhiyi/domain/worker_leases/{__init__.py,identifiers.py,models.py,errors.py}`.
- Application paths: `src/zhiyi/application/commands/{__init__.py,worker_leases.py}`,
  `src/zhiyi/application/ports/{__init__.py,lease_token_generator.py,worker_lease_observability.py,worker_lease_repository.py}`,
  and `src/zhiyi/application/services/{__init__.py,worker_lease_kernel.py}`.
- Infrastructure and adapter paths:
  `src/zhiyi/infrastructure/security/{__init__.py,lease_tokens.py}`,
  `src/zhiyi/infrastructure/database/{__init__.py,engine.py,schema_compatibility.py}`,
  `src/zhiyi/adapters/persistence/{__init__.py,postgresql_worker_lease_schema.py,postgresql_worker_lease_codecs.py,postgresql_worker_lease_repository.py,postgresql_transaction_support.py,postgresql_run_repository.py}`.
- Migration and CI paths: `migrations/env.py`,
  `migrations/versions/0002_create_worker_lease_kernel.py`, and
  `.github/workflows/runtime-python.yml`. Revision `0001` is immutable.
- Contract and unit tests:
  `tests/contract/persistence/worker_lease_repository_contract.py`,
  `tests/contract/persistence/test_postgresql_worker_lease_repository_contract.py`,
  `tests/unit/domain/worker_leases/test_values.py`,
  `tests/unit/application/commands/test_worker_leases.py`,
  `tests/unit/application/ports/test_worker_lease_errors.py`,
  `tests/unit/application/ports/test_worker_lease_observability.py`,
  `tests/unit/application/services/test_worker_lease_kernel.py`,
  `tests/unit/infrastructure/security/test_lease_tokens.py`,
  `tests/unit/infrastructure/database/test_worker_lease_schema_compatibility.py`,
  `tests/unit/infrastructure/database/test_schema_compatibility.py`,
  `tests/unit/adapters/persistence/test_postgresql_worker_lease_codecs.py`,
  `tests/unit/adapters/persistence/test_postgresql_worker_lease_error_mapping.py`,
  `tests/unit/adapters/persistence/test_postgresql_preflight.py`, and
  `tests/unit/adapters/persistence/test_postgresql_worker_lease_observability.py`.
- Integration and performance tests: `tests/integration/persistence/conftest.py`,
  `test_postgresql_worker_lease_claim.py`,
  `test_postgresql_worker_lease_concurrency.py`,
  `test_postgresql_worker_lease_guard.py`,
  `test_postgresql_worker_lease_expiry.py`,
  `test_postgresql_worker_lease_faults.py`,
  `test_postgresql_worker_lease_restart.py`,
  `test_postgresql_worker_lease_tenant_isolation.py`,
  `test_worker_lease_migrations.py`, `test_migrations.py`,
  `tests/performance/test_postgresql_run_repository.py`, and
  `tests/performance/test_postgresql_worker_lease_kernel.py`.
- Long-lived documentation: `README.md`, `doc/AGENTS.md`, `doc/PROJECT.md`,
  `doc/SDD开发规范.md`, `doc/功能文档.md`, and `doc/技术方案.md`.

## Required Safety Invariants

- PostgreSQL is the authority for time, lease ownership, expiry, claim replay, and
  fencing; Worker time never participates in authority decisions.
- Claims use database-issued UUIDv7 identifiers, immutable 24-hour receipts, random
  32-byte tokens, token digests on current leases, and exact same-intent replay.
- The claim fast path uses tenant FIFO with `SKIP LOCKED`; before recording no-work it
  performs the approved bounded, ordered, blocking queue-head probe.
- Renew, release, and guarded Run writes require the complete tenant/Run/Worker/claim/
  attempt/lease-version/token proof. Attempt and lease versions never reset.
- Guarded new writes lock in the fixed 004 receipt -> Run -> lease -> Event order and
  preserve ordinary 004 replay priority and zero-event semantics.
- Commit ambiguity is resolved per operation without blind internal retry or blind lease
  extension. Terminal telemetry is emitted only after transaction and connection cleanup,
  with independently isolated log, metric, and trace channels.
- Compatibility is read-only and keyed by engine plus component; application code never
  migrates or creates schema.

## Production Enablement Blockers

- **Raw replay-token retention**: the M0 receipt design intentionally retains a raw token
  for exact claim replay. Production enablement requires an approved physical retention,
  encryption, access-control, and disposal mechanism that Feature 006 does not supply.
- **Clock operations**: production enablement requires monitored PostgreSQL/host clock
  synchronization and alerting. Feature 006 relies on PostgreSQL time but does not install
  or operate NTP monitoring.

Neither blocker may be waived by passing local or CI tests. Feature 006 is an M0 kernel,
not production rollout authorization.

## Explicit Exclusions

No task may add a Worker polling/execution loop, LangGraph, Checkpoint, Agent/model/Tool/
Graph execution, Reconciler, automatic recovery or requeue orchestration, external side
effects, public REST/SSE/API/SDK surface, cleanup daemon, scheduler, deployment,
production schema/data mutation, production secrets, backup service, or NTP service.

## Task and Evidence Ledger

- T001: implementation approval, checklist gate, planned paths, safety invariants,
  production blockers, and exclusions recorded here.
- T002-T006 red phase: each named test was added first and failed because its 006 module
  did not yet exist.
- T007-T015 foundation: 55 domain-value tests, 47 command/error/telemetry tests,
  6 service tests, 12 token tests, 28 compatibility tests, and 49 shared transaction/
  preflight/observability tests pass. The complete fast unit plus Memory contract lane
  reports 490 passed; the unchanged real PostgreSQL RunRepository contract,
  concurrency, and fault selection reports 22 passed. Ruff passes and strict mypy
  reports no issues in 60 source files.
- T016-T026 claim slice: the provider-neutral contract (7), database clock/UUID/tenant/
  replay boundaries (10), and real contention matrix (8) report 25 passed. The matrix
  covers 100 independent one-Run races with 20 Workers, a 100-Run drain, 100-way same-ID
  claimed and no-work receipt arbitration, uncontended FIFO, temporary-lock fairness,
  the bounded blocking head probe, and zero lifecycle mutation. Receipt losers roll back
  the whole transaction and replay the winner from a new transaction; no business
  transaction is retried internally.
- T019-T023/T026 storage evidence: 10 migration/restore checks pass on PostgreSQL 18.6,
  including the immutable 0001 history, exact 0002 inventory, both compatibility rows,
  upgrade paths, and disposable downgrade. All 25 strict format-1 codec/metadata tests
  pass. UUIDv7 +60-second and 24-hour boundaries, database-clock precedence, exact
  claimed/no-work replay, terminal telemetry, and zero Run/Event/004-receipt mutation
  are covered by the passing contract and integration set. Ruff and strict mypy are
  green for the completed US1 implementation.
- T027-T033 fencing slice: 9 application-service tests and 29 real PostgreSQL contract,
  ownership/concurrency, and guarded-write tests pass. One hundred same-version renew
  requests and 100 same-version release requests each advance exactly once; 100 complete
  ownership cycles retain strictly increasing attempts and never-reset lease versions.
  Injected random-token reuse cannot reactivate the first proof. Run-status, renewal,
  release, and cancel races leave either one complete guarded lifecycle fact set or no
  guarded receipt/event; zero-event guarded commands retain the Run version. Existing
  004 replay precedes the lease guard, every tested unauthorized new write fails, and
  terminal log/metric/trace observations are emitted after cleanup. The unchanged 004/
  005 PostgreSQL contract, concurrency, and fault selection remains green at 22 passed;
  Ruff and strict mypy are green.
- T034-T039 convergence slice: 2 engine/repository-recreation tests, 9 inactive-running
  observation tests, and 12 failure-window matrix cases report 23 passed. The fault
  matrix executes 100 iterations for each of claim, renew, release, and guarded commit
  in each pre-commit, backend-termination, and real-COMMIT/lost-ack window (1,200 real
  transaction windows total). Known noncommits preserve the original condition and
  converge once; unknown acknowledgements confirm an advanced lease version or replay
  the original claim/004 receipt without a second mutation. Restart preserves exact
  tokens/receipts and replacement increases attempt/version without implicit release or
  extension. A static set of 1,000 inactive running candidates traverses ten 100-row
  keyset pages with zero gaps, duplicates, reverse order, tenant leakage, locks, Run
  recovery writes, or authority restoration.
- T040-T046 tenant/rollout slice: 79 focused tenant-isolation, positive telemetry,
  channel-isolation, token/digest/fingerprint/DSN/SQL/payload redaction, strict codec,
  compatibility, migration, downgrade/re-upgrade, and fresh logical-restore checks pass.
  Colliding tenant/Run/claim identifiers remain isolated across every public operation;
  foreign or missing facts disclose no stored owner/version/expiry/token. Renamed tables,
  indexes, or constraints fail the complete physical-inventory gate before business
  access, while 0001-only RunRepository callers remain usable until additive 0002 lands.
  Alembic head/check and immutable revision checks pass, both compatibility components
  equal 1, no application DDL is emitted, restored 005 facts and exact 006 receipts are
  preserved, and a restored clock at lease expiry proves old authority false. The dump
  is held only in process memory and the disposable restore database is removed in test
  cleanup. Raw-token retention/encryption and monitored NTP remain explicit production
  blockers.
- T047-T048 performance: PostgreSQL 18.6 on the digest-pinned Compose image, macOS
  15.5 arm64, 10 logical CPUs, 16 GiB physical memory, pool 20/max-overflow 0, 20
  clients, 5,000 ms lock/statement timeouts, exactly 10,000 queued Runs, 100 warmups,
  and 1,000 nearest-rank measured samples per operation reports in the final full-suite
  order: full issue+claim p50 68.438 ms/p95 133.701 ms; renew p50 25.555/p95 39.397
  ms; authority read p50 13.963/p95 22.118 ms; release p50 28.945/p95 44.553 ms.
  Every required p95 is below 200 ms. The disposable performance fixture explicitly
  drains prior acceptance-test writes with PostgreSQL `CHECKPOINT` before setup and
  after seeding, outside every measured interval; thresholds, concurrency, durability,
  samples, and production transaction behavior are unchanged. The claim plan uses
  `ix_runs_tenant_status_updated_run`, measured lock waits are zero, and final rows are
  10,000 Runs, 1,100 retained leases, and 1,100 claim receipts. The measured CI timeout
  did not require adjustment.
- T049 CI: the existing PostgreSQL lane explicitly upgrades 0002, checks current heads
  and autogenerate drift, asserts nonempty collection of every 006 contract/integration/
  performance module, rejects skipped acceptance, includes migrations in Ruff, and keeps
  the fast lane free of PostgreSQL tests. Local YAML parsing and collection partition
  checks pass: 120 PostgreSQL tests collect, every required module is present, and the
  fast selection collects no PostgreSQL test.
- T050-T053 documentation: README now contains the local migration, focused contract,
  fault-window, performance, and Alembic verification commands plus the M0 retention/NTP
  warning. PROJECT, 功能文档, and 技术方案 now consistently describe the implemented
  scope, two-table model, claim/renew/release/replay/guard semantics, component-aware
  compatibility and rolling boundary, explicit exclusions, and production blockers.
- T054 security review: no unresolved implementation finding was identified. The review
  verified 32-byte CSPRNG tokens, constant-time digest comparison, digest-only current
  lease storage, restricted receipt token projection, redacted token/value/error shapes,
  bounded 1-5,000 ms lock and 1-10,000 ms statement timeouts with statement timeout not
  below lock timeout, `READ COMMITTED` plus synchronous commit, phase-aware `08007`/
  `40003` unknown-outcome classification, and no internal write retry. Runtime code has
  no DDL path; 0002 is an explicit migration and production application/migration role
  separation remains an operational prerequisite. Every public operation constructs one
  allowlisted immutable terminal fact after database cleanup; log, metric, and trace are
  attempted independently and channel exceptions cannot change the business outcome.
  Sentinel tests cover token/digest/fingerprint/DSN/credentials/SQL/parameters/payload/
  final-answer/hidden-reasoning leakage. The known raw replay-token physical retention,
  encryption/key-rotation, and NTP monitoring gaps remain hard production blockers, not
  review waivers.
- T058-T062 quantitative convergence: uncontended FIFO covers exactly 100 Runs; every
  duration and UUID age boundary, complete ownership/fencing cycle, same-version renew,
  and same-version release group runs 100 times; repository/engine reconstruction runs
  100 cycles; inactive observation traverses 1,000 real candidates with default, 1,
  100, and 1,000 page sizes and fixed-`as_of` mutation; all 8 public operation names by
  11 terminal outcomes are covered, along with three independently failing telemetry
  channels. These additions pass inside the final 120-test PostgreSQL lane and the
  638-test fast lane.
- T055-T056 final gates: frozen dependency sync passes for 60 packages; Ruff passes;
  all 126 checked files are formatted; strict mypy reports no issues in 123 source
  files; the fast lane reports 638 passed/122 deselected; the real PostgreSQL lane
  reports 120 passed/640 deselected in 132.89 seconds; the standalone performance test,
  explicit `downgrade 0001`/`upgrade head`, `current --check-heads`, `alembic check`, and
  9 migration/restore tests pass; all 29 SDD governance tests and the manual worktree
  design-drift gate pass.
- T057 final convergence: 30 functional requirements, 14 success criteria, 20 user-story
  acceptance scenarios, 10 plan decision/constraint groups, and all 8 constitution
  principles were rechecked. Missing, partial, contradictory, and unrequested finding
  counts are all zero; no severity bucket contains a finding. `tasks.md` remained
  byte-for-byte unchanged during this final converge pass, so no new phase or task was
  appended.
- T063-T064 post-merge design synchronization: the approved boundary is now explicit in
  31 functional requirements and 15 success criteria plus Plan, Tasks, requirements
  checklist, Quickstart, README, AGENTS, PROJECT, and the SDD operating guide. The
  speckit-analyze pass found zero critical, high, medium, or low conflicts; requirement
  and criterion identifiers are unique and continuous, and no clarification marker,
  placeholder, or constitution conflict remains.
- T065 marker red/green: before implementation, `postgresql and performance` collected
  zero nodes and both latency modules leaked into `postgresql and not performance`.
  After adding only the registered module markers, the performance selection collects
  exactly the 005 and 006 nodes and the functional selection collects zero performance
  nodes. No benchmark workload, assertion, threshold, warmup, sample, concurrency,
  durability, tenant, or fencing behavior changed.
- T066 shared-CI partition: local execution of the workflow's exact collection logic
  reports 120 PostgreSQL nodes total, 118 functional nodes, 2 performance nodes, and zero
  database nodes in the fast selection. YAML parsing passes. The shared job upgrades and
  verifies Alembic, runs all 118 functional nodes with zero skips, and only collects the
  two fixed-environment performance nodes; it makes no SC-008/SC-011 latency claim.
- T067 final local gates: frozen sync reports 60 packages; Alembic upgrades to
  `0002_worker_lease_kernel`, `current --check-heads` and `alembic check` pass; the real
  functional lane reports 118 passed/642 deselected in 114.26 seconds; the fast lane
  reports 638 passed/122 deselected in 3.36 seconds; Ruff passes, all 126 checked files
  are formatted, strict mypy reports no issues in 123 source files, and all 29 SDD unit
  tests pass.
- T067 fixed-environment latency evidence uses the recorded PostgreSQL 18.6 digest,
  macOS 15.5 arm64 host with 10 logical CPUs and 16 GiB memory, pool 20/zero overflow,
  20 clients, 100 warmups, and 1,000 measured samples per operation. Independent pytest
  processes report 005 p95 load 13.952 ms/page-100 71.719 ms/atomic commit 38.987 ms,
  all below 100 ms; 006 p95 full issue+claim 117.207 ms/renew 94.749 ms/authority read
  47.371 ms/release 39.448 ms, all below 200 ms, with zero lock waits. Exploratory
  combined-process runs observed 005 page-100 p95 variance of 128.655-138.500 ms while
  the host load average reached 10.46; those runs are not counted as a pass. The accepted
  procedure therefore preserves each original workload in an independent pytest process
  and never changes a threshold, sample, concurrency, or safety requirement.
- T068 convergence: all 31 functional requirements, 15 success criteria, 20 original
  user-story acceptance scenarios, the Phase 9 six-task ledger, Plan constraints, and all
  8 constitution principles were rechecked. Missing, partial, contradictory, unrequested,
  and untraced-path finding counts are all zero. The implementation diff changes only two
  module marker declarations and the shared-CI workflow; no production source, migration,
  schema, transaction, benchmark workload, or product behavior changed. The manual
  design-drift gate passes with this report restored to `ALIGNED`.

## Blocking Findings

None. Feature 006 is aligned with its approved M0 scope. Raw replay-token physical
retention/encryption/key rotation and monitored clock synchronization remain explicit
production enablement blockers; they are not implementation drift and are not waived by
this local acceptance.
