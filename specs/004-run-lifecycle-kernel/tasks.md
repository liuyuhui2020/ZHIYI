---

description: "Dependency-ordered implementation tasks for the Run Lifecycle Kernel"
---

# Tasks: Run Lifecycle Kernel

**Input**: Design documents from `/specs/004-run-lifecycle-kernel/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`contracts/run-lifecycle.md`, and reviewer disposition of
`checklists/lifecycle-safety.md`

**Tests**: Required by the feature specification and constitution. Within each behavior slice,
write the named test first, run it to prove the expected failure, then implement the smallest
correct change and refactor green.

**Organization**: Tasks are grouped by user story and use exact implementation, test, feature,
and synchronized-document paths.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files with no incomplete dependency.
- **[Story]**: Maps to a user story in `spec.md`.

## Phase 1: Setup (Shared Package Boundaries)

**Purpose**: Create only the domain, command, and persistence-adapter package boundaries named by the approved plan.

- [x] T001 Create explicit package boundaries and initial exports in `src/zhiyi/domain/__init__.py`, `src/zhiyi/domain/runs/__init__.py`, `src/zhiyi/application/commands/__init__.py`, and `src/zhiyi/adapters/persistence/__init__.py`; do not change dependencies or `uv.lock`

---

## Phase 2: Foundational (Blocking Values and Ports)

**Purpose**: Establish immutable identifiers, errors, budget values, event/result values, command envelopes, and ports required by all stories.

**⚠️ CRITICAL**: No aggregate or application behavior starts until the value/contract tests fail for the intended missing modules and the foundation is green.

- [x] T002 [P] Write failing identifier, AgentVersionRef, UTC timestamp, digest, static-safe-error, cross-type, invalid/blank/oversized value, and sensitive-`repr` tests in `tests/unit/domain/runs/test_events_results.py`
- [x] T003 [P] Write failing RunBudget, BudgetCharge, BudgetSnapshot, exact Decimal/currency, float rejection, negative/non-finite/negative-zero normalization, total-Token, zero-boundary, and charge-fingerprint construction tests in `tests/unit/domain/runs/test_budget.py`
- [x] T004 Implement immutable typed identifiers and AgentVersionRef in `src/zhiyi/domain/runs/identifiers.py`, plus stable error codes/exceptions with static safe messages in `src/zhiyi/domain/runs/errors.py`
- [x] T005 Implement validated RunBudget, BudgetDimension, BudgetCharge, BudgetSnapshot, safe charge fingerprinting, and immutable usage arithmetic in `src/zhiyi/domain/runs/budget.py`
- [x] T006 Implement frozen JSON allowlisting, RunEventType/RunEvent, SafeRunError, RunResultDraft, and RunResult base invariants in `src/zhiyi/domain/runs/events.py` and `src/zhiyi/domain/runs/results.py`
- [x] T007 [P] Define framework-neutral `Clock` and `IdentifierGenerator` Protocols in `src/zhiyi/application/ports/clock.py` and `src/zhiyi/application/ports/identifier_generator.py`
- [x] T008 [P] Write failing command immutability, expected-version exclusion from normalized-intent fingerprints, generated-field exclusion, no-sensitive-repr, target-state, result-draft, and budget-command tests in `tests/unit/application/commands/test_run_lifecycle_commands.py`
- [x] T009 Implement create/start/wait/resume/succeed/fail/cancel/consume/deadline command values and safe canonical fingerprints in `src/zhiyi/application/commands/run_lifecycle.py`
- [x] T010 Define CommandReceipt, CommitOutcome, tenant-scoped pre-transition `find_command`, atomic async `RunRepository` Protocol, and typed repository conflicts in `src/zhiyi/application/ports/run_repository.py`
- [x] T011 Export only the stable domain, command, and port surface from `src/zhiyi/domain/runs/__init__.py`, `src/zhiyi/application/commands/__init__.py`, and `src/zhiyi/application/ports/__init__.py`; run the foundational tests green

**Checkpoint**: Framework-free immutable values and the future persistence boundary are stable.

---

## Phase 3: User Story 1 - 创建并推进可解释的 Run (Priority: P1) 🎯 MVP

**Goal**: Create a Run pinned to AgentVersion and enforce the complete legal transition matrix with immutable terminal results and ordered events.

**Independent Test**: Construct Runs without repository/network/framework dependencies, execute every legal and illegal state pair, and verify version, event, AgentVersion, and terminal invariants.

### Tests for User Story 1

- [x] T012 [US1] Write failing queued creation, version/event-sequence origin, fixed AgentVersion, legal transition matrix, illegal transition, non-terminal progress-path, and terminal-immutability tests in `tests/unit/domain/runs/test_aggregate.py`
- [x] T013 [US1] Extend failing success/failure/cancel/limit result consistency, empty-success-answer, failure-error, reference-only, and one-terminal-event tests in `tests/unit/domain/runs/test_aggregate.py` and `tests/unit/domain/runs/test_events_results.py`
- [x] T014 [US1] Run `tests/unit/domain/runs/test_aggregate.py` and `tests/unit/domain/runs/test_events_results.py`; record that they fail because `Run` aggregate behavior is absent

### Implementation for User Story 1

- [x] T015 [US1] Implement immutable RunStatus, the single allowed-transition table, Run creation, non-terminal transitions, forward-only observed time, version/sequence increments, event payload allowlists, terminal builders, and aggregate invariant validation in `src/zhiyi/domain/runs/aggregate.py`
- [x] T016 [US1] Complete result/event cross-invariants and safe immutable copies in `src/zhiyi/domain/runs/events.py` and `src/zhiyi/domain/runs/results.py`; export `Run` and lifecycle values from `src/zhiyi/domain/runs/__init__.py`
- [x] T017 [US1] Run the US1 test set green and prove all invalid transitions, terminal rewrites, AgentVersion replacement attempts, and backward timestamps leave the original Run unchanged

**Checkpoint**: The pure domain lifecycle is independently usable and testable without persistence.

---

## Phase 4: User Story 2 - 安全处理重复与并发命令 (Priority: P1)

**Goal**: Atomically persist Run/event/receipt changes, replay identical commands, reject reused intent, and serialize same-version competitors.

**Independent Test**: Run the shared repository contract and at least 1,000 deterministic async races against the in-memory adapter; assert one winner, stable replay, tenant isolation, and zero partial writes.

### Tests for User Story 2

- [x] T018 [P] [US2] Write the reusable failing repository contract for pre-transition `find_command` replay/conflict, create/update atomicity, commit-time receipt-before-version recheck, expected-version-excluded intent retry, zero-event current-version charge replay, command-intent conflict, stale version, event append/sequence invariants, pagination default and `1..1000` bounds, not-found equivalence, and injected pre-commit failure in `tests/contract/persistence/test_run_repository_contract.py`
- [x] T019 [P] [US2] Write failing in-memory adapter snapshot isolation, tenant-key separation, concurrent same-command replay, concurrent different-command single-winner, and failed-copy-swap tests in `tests/unit/adapters/persistence/test_memory_run_repository.py`
- [x] T020 [US2] Write failing application create/start/wait/resume lifecycle, stable outcome, replayed-event, stale-version, reused-command, tenant-not-found, and no-partial-update tests in `tests/unit/application/services/test_run_lifecycle.py`
- [x] T021 [US2] Run the US2 contract, adapter, and service tests; record expected failures before adding the adapter and service

### Implementation for User Story 2

- [x] T022 [US2] Implement the async-lock, copy-validate-swap, tenant-scoped in-memory repository with atomic Run/event/receipt commit and deterministic replay in `src/zhiyi/adapters/persistence/memory_run_repository.py`; expose it from `src/zhiyi/adapters/persistence/__init__.py`
- [x] T023 [US2] Implement RunLifecycleService pre-transition command replay, create/get/list-events/start/wait/resume orchestration, injected clock/IDs, safe outcomes, receipt construction, commit-time race handling, and domain-to-repository error preservation in `src/zhiyi/application/services/run_lifecycle.py`; export it from `src/zhiyi/application/services/__init__.py`
- [x] T024 [US2] Add and execute the 1,000-race same-version/different-command and duplicate-command acceptance matrix in `tests/unit/adapters/persistence/test_memory_run_repository.py` and `tests/unit/application/services/test_run_lifecycle.py`
- [x] T025 [US2] Run the full US2 set green and prove conflict/replay ordering, event stability, tenant non-disclosure, and atomic rollback match `contracts/run-lifecycle.md`

**Checkpoint**: Idempotent application commands and the persistence contract are safe under local concurrency.

---

## Phase 5: User Story 3 - 通过硬预算和取消保证 Run 可终止 (Priority: P1)

**Goal**: Enforce exact hard-budget boundaries, idempotent charges, deadlines, time regression defense, and cancellation from every non-terminal state.

**Independent Test**: Use a controlled UTC clock across below/equal/over boundaries for every dimension, duplicate/conflicting charges, all non-terminal cancellations, deadlines, and post-terminal work attempts.

### Tests for User Story 3

- [x] T026 [US3] Extend failing per-dimension below/equal/over, derived total-Token, Decimal cost, same-charge replay, changed-charge conflict, zero-budget, and no-overlimit-charge-accounting tests in `tests/unit/domain/runs/test_budget.py` and `tests/unit/domain/runs/test_aggregate.py`
- [x] T027 [US3] Add failing queued/running/waiting deadline, exact-deadline, backward-clock, cancel-from-every-non-terminal, cancel-after-terminal, and post-cancel work tests in `tests/unit/domain/runs/test_aggregate.py`
- [x] T028 [US3] Add failing service consume-budget, over-limit terminal, deadline enforcement, cancellation replay/conflict, stale-version, and concurrent cancel-vs-charge tests in `tests/unit/application/services/test_run_lifecycle.py`
- [x] T029 [US3] Run the US3 tests and record the expected missing budget/cancel/deadline behavior before implementation

### Implementation for User Story 3

- [x] T030 [US3] Implement exact-limit assessment, idempotent confirmed charge application, exceeded-dimension reporting, and no-charge-on-exceed behavior in `src/zhiyi/domain/runs/budget.py`
- [x] T031 [US3] Add Run consume-budget, deadline enforcement, forward-only clock handling, cancel-from-all-non-terminal, and limit_exceeded terminal operations to `src/zhiyi/domain/runs/aggregate.py`
- [x] T032 [US3] Add consume_budget, enforce_deadline, and cancel_run application methods with atomic receipt/event persistence to `src/zhiyi/application/services/run_lifecycle.py`
- [x] T033 [US3] Run all US3 tests green and prove equality remains allowed, excess/deadline stops before new work, confirmed usage is preserved, and terminal Runs cannot restart

**Checkpoint**: Every Run has deterministic cost/time termination and cancellation behavior.

---

## Phase 6: User Story 4 - 输出稳定且安全的事件与结果 (Priority: P2)

**Goal**: Finalize replayable events and one stable RunResult per terminal state without leaking sensitive or framework objects.

**Independent Test**: Replay complete success/failure/cancel/limit lifecycles with sentinel secrets, Prompt/error/reasoning content, mutable payload inputs, duplicates, and event pagination; inspect all public values and representations.

### Tests for User Story 4

- [x] T034 [P] [US4] Extend failing deep-freeze/thaw, unsupported JSON object, payload field allowlist, payload version, duplicate ID/sequence, mutation-after-construction, and event ordering tests in `tests/unit/domain/runs/test_events_results.py`
- [x] T035 [P] [US4] Add failing sentinel credential/auth-header/full-Prompt/unapproved-raw-output/Provider-body/hidden-reasoning assertions plus an explicit approved-final-answer allowance across errors, commands, fingerprints, receipts, events, results, service outcomes, and repr in `tests/unit/domain/runs/test_events_results.py`, `tests/unit/application/commands/test_run_lifecycle_commands.py`, and `tests/unit/application/services/test_run_lifecycle.py`
- [x] T036 [US4] Add failing complete lifecycle and replay assertions for continuous sequence, one terminal result/event, cumulative usage, safe error correlation, reference-only fields, and paginated event reads in `tests/unit/application/services/test_run_lifecycle.py`
- [x] T037 [US4] Run the US4 set and record expected safety/contract failures before hardening production values

### Implementation for User Story 4

- [x] T038 [US4] Finalize event payload schemas, deep immutability, adapter-safe thawing, result/reference normalization, error/status consistency, and redacted representations in `src/zhiyi/domain/runs/events.py`, `src/zhiyi/domain/runs/results.py`, and `src/zhiyi/domain/runs/errors.py`
- [x] T039 [US4] Complete succeed_run/fail_run, replayed event retrieval, safe command outcomes, and validated event pagination in `src/zhiyi/application/services/run_lifecycle.py` and `src/zhiyi/adapters/persistence/memory_run_repository.py`
- [x] T040 [US4] Run all four user-story suites green and prove zero sensitive sentinel or third-party object leakage and exact event/result agreement

**Checkpoint**: Later Worker, PostgreSQL, API, SSE, and observability slices have a stable safe contract.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Prove performance, architecture, documentation alignment, governance, and convergence without expanding the Feature.

- [x] T041 [P] Add the mandatory 10,000-transition warm-up/monotonic/p95 performance acceptance test with no skip in `tests/performance/test_run_lifecycle_overhead.py`
- [x] T042 [P] Add a domain dependency/forbidden-object architecture test covering every new `src/zhiyi/domain/**/*.py` module in `tests/unit/domain/runs/test_events_results.py`
- [x] T043 Update implemented Run lifecycle/budget/idempotency/event/result boundaries and the complete cancellation/timeout transition matrix without claiming persistence/Worker/API completion in `doc/功能文档.md` and `doc/技术方案.md`
- [x] T044 [P] Update the repository stage from one to two completed M0 foundation slices, preserve the incomplete Runtime warning, and identify PostgreSQL/lease/Worker as next scope in `README.md` and `doc/PROJECT.md`
- [x] T045 Re-run every command in `specs/004-run-lifecycle-kernel/quickstart.md` and keep the guide aligned with actual test paths and outcomes
- [x] T046 Update final requirement/plan/task/test/architecture/dependency/rollback evidence, exact docs impact, and blocking findings in `specs/004-run-lifecycle-kernel/drift-report.md`
- [x] T047 Run `uv sync --all-groups --frozen --python 3.12`, `uv run pytest -m "not online"`, Ruff check/format, strict mypy, governance unit tests, and `scripts/sdd/check_design_drift.py --worktree --gate manual`; record actual results in `specs/004-run-lifecycle-kernel/drift-report.md`
- [x] T048 Execute `$speckit-converge`, append any remaining work to `specs/004-run-lifecycle-kernel/tasks.md`, finish appended tasks test-first, rerun affected checks, and only then mark the feature complete

---

## Requirement and Success-Criteria Traceability

| Requirement / criterion | Primary tasks |
|---|---|
| FR-001–FR-005, SC-001–SC-002 | T002–T017 |
| FR-006–FR-010, SC-003–SC-004 | T008–T011, T018–T025 |
| FR-011–FR-015, SC-005–SC-006 | T003, T005, T026–T033 |
| FR-016–FR-020, SC-007–SC-008 | T006, T012–T017, T034, T036, T038–T040 |
| FR-021–FR-023, SC-009–SC-010 | T002, T008–T011, T018–T025, T034–T040, T042 |
| FR-024–FR-025 | T001–T048 and explicit scope/docs checks T043–T047 |
| SC-011 | T041, T047 |
| SC-012 | T010, T018–T025, T047–T048 |

## Dependencies & Execution Order

### Phase dependencies

- **Setup**: starts immediately.
- **Foundational**: depends on Setup and blocks all stories.
- **US1**: depends on Foundational and establishes the pure domain lifecycle MVP.
- **US2**: depends on US1 aggregate behavior and adds persistence/idempotency orchestration.
- **US3**: depends on US1/US2 so budget and cancellation commands can commit atomically.
- **US4**: depends on all P1 stories and hardens their shared event/result boundary.
- **Polish**: depends on all stories; final validation requires reviewer-approved checklist and all tasks.

### User story dependencies

```text
Setup -> Foundation -> US1 -> US2 -> US3 -> US4 -> Polish -> Converge
```

The ordering is deliberate because each later story exercises the same Run aggregate and atomic commit boundary. Each story remains independently testable at its checkpoint.

### Within each story

- Tests are written and observed failing for the intended missing behavior before product code.
- Domain values precede aggregate behavior; aggregate behavior precedes repository/application orchestration.
- Repository replay checks precede expected-version checks.
- No story is marked complete until its focused tests pass without external services.

## Parallel Opportunities

- T002/T003 and T007/T008 touch separate foundational files.
- T018/T019 can be authored in parallel before T022; T024 then stresses their integration.
- T034/T035 can be authored in parallel.
- T041/T042/T044 touch separate performance, architecture, and documentation paths after stories are green.

## Parallel Examples

### Foundation

```text
Task T002: identifier/error/event-result construction tests
Task T003: budget construction tests
Task T007: clock and identifier-generator ports
Task T008: command contract tests
```

### User Story 2

```text
Task T018: reusable repository contract
Task T019: in-memory adapter-specific concurrency tests
```

## Implementation Strategy

### MVP first

1. Complete T001–T011.
2. Complete T012–T017 for the pure Run lifecycle.
3. Stop and validate US1 without repository, network, database, Graph, or Provider.
4. Add atomic idempotency, then budget/cancellation, then safe integration contracts.

### Completion gate

Implementation starts only after `$speckit-analyze` has no critical findings and the reviewer-owned
`checklists/lifecycle-safety.md` has a reviewer disposition. Completion requires every task checked,
green validation evidence, `drift-report.md` set to `ALIGNED`, and a clean convergence audit.

## Notes

- `[P]` tasks change separate files and have no incomplete dependency.
- No dependency, migration, online call, deployment, push, or production state change belongs here.
- Do not integrate Model Gateway, LangGraph, SQLAlchemy, FastAPI, lease, or SSE as “helpful” extras.
- Do not commit or push without a separate explicit user instruction.

## Phase 8: Convergence

- [x] T049 Add 1,000 independent deterministic two-command same-version races and assert one winner, one version conflict, continuous events, and no partial writes in `tests/unit/adapters/persistence/test_memory_run_repository.py` per SC-003 (partial)
- [x] T050 Add at least 100 sequential same-command replays and 100 same-charge replays, proving stable receipts/events and unchanged usage/version in `tests/unit/application/services/test_run_lifecycle.py` per SC-004 (partial)
- [x] T051 Add application-level replay and post-terminal mutation acceptance for succeeded, failed, cancelled, and limit_exceeded Runs in `tests/unit/application/services/test_run_lifecycle.py` per SC-002 (partial)
- [x] T052 Reject commits containing more than one new event and cover atomic rollback in `src/zhiyi/adapters/persistence/memory_run_repository.py` and `tests/contract/persistence/test_run_repository_contract.py` per plan decision 5 (partial)
