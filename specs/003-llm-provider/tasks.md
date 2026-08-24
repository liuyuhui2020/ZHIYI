---

description: "Dependency-ordered implementation tasks for the LLM Provider Gateway"
---

# Tasks: LLM Provider Gateway

**Input**: Design documents from `/specs/003-llm-provider/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`contracts/model-gateway.md`, and reviewer disposition of `checklists/security.md`

**Tests**: Required by the feature specification and constitution. Within each behavior slice,
write the named test first, run it to prove the expected failure, then implement and refactor green.

**Organization**: Tasks are grouped by user story and use exact implementation/test/document paths.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files with no incomplete dependency.
- **[Story]**: Maps to a user story in `spec.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Initialize the first product Python package with reproducible dependencies and quality tooling.

- [x] T001 Create Python 3.12 package metadata, exact direct dependencies, pytest markers, Ruff rules, and mypy strict configuration in `pyproject.toml`; add Python/uv artifacts to `.gitignore`
- [x] T002 [P] Create package boundaries and explicit exports in `src/zhiyi/__init__.py`, `src/zhiyi/application/__init__.py`, `src/zhiyi/application/models/__init__.py`, `src/zhiyi/application/ports/__init__.py`, `src/zhiyi/application/services/__init__.py`, `src/zhiyi/adapters/__init__.py`, `src/zhiyi/adapters/models/__init__.py`, `src/zhiyi/adapters/secrets/__init__.py`, `src/zhiyi/infrastructure/__init__.py`, and `src/zhiyi/infrastructure/config/__init__.py`
- [x] T003 Resolve and commit the complete Python dependency graph with hashes in `uv.lock`; verify `uv sync --all-groups --frozen` succeeds

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish provider-neutral contracts and ports required by all user stories.

**⚠️ CRITICAL**: No adapter or Gateway behavior starts until the contract tests fail for the expected reason and the foundational contract is green.

- [x] T004 [P] Write failing construction, immutability, validation, Usage aggregation, error-sanitization, and capability-compatibility tests in `tests/unit/application/models/test_contracts.py`
- [x] T005 Implement provider-neutral enums, content/message values, Tool/stream values, capability/route/request/response/Usage/Attempt/Error contracts, validation helpers, and safe exception behavior in `src/zhiyi/application/models/contracts.py`
- [x] T006 [P] Define framework-neutral async `ModelProvider`/ProviderResponse/ProviderChunk contracts in `src/zhiyi/application/ports/model_provider.py`, secret-reference/value/provider contracts with redacted representations in `src/zhiyi/application/ports/secret_provider.py`, and conservative preflight `TokenEstimator`/`TokenEstimate` contracts in `src/zhiyi/application/ports/token_estimator.py`
- [x] T007 [P] Write the shared deterministic Provider behavior fixture and failing Fake Provider tests in `tests/contract/models/test_provider_contract.py`
- [x] T008 Implement the scripted, concurrency-safe Fake Provider for success, streaming, Tool, Usage, delay, cancellation, and injected ModelError outcomes in `src/zhiyi/adapters/models/fake.py`

**Checkpoint**: Platform contracts and an offline Provider are stable; all four stories can build on them.

---

## Phase 3: User Story 1 - 通过统一契约调用不同模型 (Priority: P1) 🎯 MVP

**Goal**: Complete and stream the same platform request through Fake, OpenAI, or Anthropic without exposing third-party objects.

**Independent Test**: Replay equivalent offline text and streaming fixtures through all adapters and assert identical ordered platform results, terminal semantics, model/Provider identity, and Usage.

### Tests for User Story 1

- [x] T009 [P] [US1] Write failing role/content conversion, ordered text streaming, Usage/finish-reason mapping, proprietary-block filtering, empty-response, malformed-response, UTF-8/Tool/Schema/multimodal conservative Token upper-bound, and capacity-preflight tests in `tests/unit/adapters/models/test_langchain_mapping.py` and `tests/unit/adapters/models/test_token_estimator.py`
- [x] T010 [P] [US1] Write failing OpenAI offline complete/stream/Usage/error-boundary contract tests using injected LangChain model doubles in `tests/contract/models/test_openai_contract.py`
- [x] T011 [P] [US1] Write failing Anthropic offline complete/stream/Usage/error-boundary contract tests using injected LangChain model doubles in `tests/contract/models/test_anthropic_contract.py`
- [x] T012 [US1] Run `tests/contract/models/test_provider_contract.py`, `tests/contract/models/test_openai_contract.py`, `tests/contract/models/test_anthropic_contract.py`, and `tests/unit/adapters/models/test_langchain_mapping.py`; record that they fail because real mapping/Gateway code is absent

### Implementation for User Story 1

- [x] T013 [US1] Implement shared LangChain message/content conversion, async complete/stream mapping, allowlisted metadata, Usage normalization, finish-reason normalization, iterator cleanup, malformed-response defense in `src/zhiyi/adapters/models/langchain_base.py`, and the offline conservative Token estimator in `src/zhiyi/adapters/models/token_estimator.py`
- [x] T014 [P] [US1] Implement official OpenAI `ChatOpenAI` construction with hidden retries disabled plus OpenAI-specific response/request-ID and exception mapping in `src/zhiyi/adapters/models/openai.py`
- [x] T015 [P] [US1] Implement official Anthropic `ChatAnthropic` construction with hidden retries disabled plus Anthropic-specific response/request-ID and exception mapping in `src/zhiyi/adapters/models/anthropic.py`
- [x] T016 [US1] Implement open Provider-key registration, route/deadline validation, conservative Token preflight, credential hand-off, base complete/stream orchestration, terminal generation, and third-party exception containment in `src/zhiyi/application/services/model_gateway.py`
- [x] T017 [US1] Run the US1 test set in `tests/contract/models/test_provider_contract.py`, `tests/contract/models/test_openai_contract.py`, `tests/contract/models/test_anthropic_contract.py`, `tests/unit/adapters/models/test_langchain_mapping.py`, and `tests/unit/adapters/models/test_token_estimator.py` green and prove no returned object is a LangChain, OpenAI, or Anthropic type

**Checkpoint**: The provider-neutral text/streaming MVP works with deterministic offline evidence.

---

## Phase 4: User Story 2 - 使用 Tool Calling 与结构化输出 (Priority: P1)

**Goal**: Normalize and validate multiple/streamed Tool Calls and final structured results on both providers.

**Independent Test**: Replay single, multiple, fragmented, duplicate, incomplete, valid structured, and invalid structured fixtures for both providers and compare platform terminal results/errors.

### Tests for User Story 2

- [x] T018 [P] [US2] Extend failing OpenAI Tool Calling, streamed Tool fragments, strict Schema, Structured Output success/failure, and unknown Tool tests in `tests/contract/models/test_openai_contract.py`
- [x] T019 [P] [US2] Extend failing Anthropic Tool Calling, fine-grained input JSON fragments, strict Schema, Structured Output success/failure, and reasoning-block filtering tests in `tests/contract/models/test_anthropic_contract.py`
- [x] T020 [P] [US2] Write failing Pydantic v2 Schema generation, local validation, safe validation-error, and non-serialization tests in `tests/unit/adapters/models/test_structured_output.py`
- [x] T021 [US2] Run `tests/contract/models/test_openai_contract.py`, `tests/contract/models/test_anthropic_contract.py`, and `tests/unit/adapters/models/test_structured_output.py` and record expected failures before changing production mapping code

### Implementation for User Story 2

- [x] T022 [P] [US2] Implement `PydanticOutputContract` with JSON Schema export, final local validation, JSON-safe dump, and sanitized errors in `src/zhiyi/adapters/models/structured_output.py`
- [x] T023 [US2] Add Tool Schema binding, Provider-specific strict structured output, Tool delta assembly, duplicate/unknown Tool rejection, incomplete JSON handling, and final platform validation to `src/zhiyi/adapters/models/langchain_base.py`, `src/zhiyi/adapters/models/openai.py`, and `src/zhiyi/adapters/models/anthropic.py`
- [x] T024 [US2] Strengthen cross-field Tool/Structured Output invariants and terminal validation in `src/zhiyi/application/models/contracts.py` and `src/zhiyi/application/services/model_gateway.py`
- [x] T025 [US2] Run `tests/contract/models/test_openai_contract.py`, `tests/contract/models/test_anthropic_contract.py`, and `tests/unit/adapters/models/test_structured_output.py` green and prove Provider reasoning/thinking blocks and unverified Structured Output never enter platform responses

**Checkpoint**: Both P1 stories independently satisfy text, streaming, Tool, Structured Output, Usage, and error contracts.

---

## Phase 5: User Story 3 - 在故障下受控重试与降级 (Priority: P2)

**Goal**: Add bounded timeout, retry, rate-limit, circuit-breaker, compatible Fallback, and cancellation behavior with deterministic attempt accounting.

**Independent Test**: Script timeouts, rate limits, transient errors, auth/input/policy errors, cancellation, half-open probes, incompatible fallbacks, and pre/post-first-delta stream failures; assert exact attempt order and terminal result.

### Tests for User Story 3

- [x] T026 [P] [US3] Write failing token-bucket burst/refill/fair-cancellation tests with a fake monotonic clock in `tests/unit/application/services/test_rate_limiter.py`
- [x] T027 [P] [US3] Write failing CLOSED/OPEN/HALF_OPEN threshold, cooldown, single-probe, recovery, and non-transient-error tests in `tests/unit/application/services/test_circuit_breaker.py`
- [x] T028 [US3] Write failing route-total-deadline/per-attempt/first-block/idle timeout, bounded retry/backoff, compatible Fallback, incompatible-Fallback rejection, cumulative Usage, and cancellation tests in `tests/unit/application/services/test_model_gateway.py`
- [x] T029 [US3] Add failing stream-before-first-delta retry and stream-after-first-delta no-replay cases in `tests/unit/application/services/test_model_gateway.py`
- [x] T030 [US3] Run `tests/unit/application/services/test_rate_limiter.py`, `tests/unit/application/services/test_circuit_breaker.py`, and `tests/unit/application/services/test_model_gateway.py` and record expected policy/state-machine failures before implementation

### Implementation for User Story 3

- [x] T031 [P] [US3] Implement cancellation-safe per-target async token buckets with injected monotonic clock/sleeper in `src/zhiyi/application/services/rate_limiter.py`
- [x] T032 [P] [US3] Implement concurrency-safe CLOSED/OPEN/HALF_OPEN circuit breakers with one half-open probe and injected clock in `src/zhiyi/application/services/circuit_breaker.py`
- [x] T033 [US3] Add a hard route-level deadline covering rate wait, secret resolution, all attempt/stream timeouts, finite exponential backoff+jitter and ordered Fallback, plus circuit/rate integration, cancellation propagation, safe iterator close, and per-attempt/cumulative Usage to `src/zhiyi/application/services/model_gateway.py`
- [x] T034 [US3] Complete OpenAI/Anthropic SDK exception matrices without original-body leakage in `src/zhiyi/adapters/models/openai.py` and `src/zhiyi/adapters/models/anthropic.py`
- [x] T035 [US3] Run `tests/unit/application/services/test_rate_limiter.py`, `tests/unit/application/services/test_circuit_breaker.py`, and `tests/unit/application/services/test_model_gateway.py` green and prove attempt counts never exceed hard limits and no new attempt begins after cancellation or a visible stream delta

**Checkpoint**: Failure behavior is bounded, deterministic, observable, and safe to integrate into a later Runtime.

---

## Phase 6: User Story 4 - 安全配置与可观测用量 (Priority: P2)

**Goal**: Resolve credentials only at the adapter boundary, safely load curated model profiles, and emit leakage-free per-attempt/aggregate summaries.

**Independent Test**: Use sentinel credentials and sensitive messages through success/error/retry/Fallback/concurrency cases; inspect public values, repr, captured logs, config dumps, and Usage summaries.

### Tests for User Story 4

- [x] T036 [P] [US4] Write failing allowlisted environment lookup, missing/empty secret, redacted `str/repr`, no-enumeration, and concurrent credential isolation tests in `tests/unit/adapters/secrets/test_environment.py`
- [x] T037 [P] [US4] Write failing curated target/profile, required per-modality Token upper bounds, duplicate/cyclic route, invalid total/attempt/stream timeout, retry/rate/circuit limit, default Provider credential reference, and safe serialization tests in `tests/unit/infrastructure/config/test_models.py`
- [x] T038 [US4] Add sentinel-secret/sensitive-prompt assertions for public errors, AttemptRecord, ModelResponse, config repr, and captured logs plus exact retry/Fallback Usage aggregation in `tests/unit/application/services/test_model_gateway.py`
- [x] T039 [US4] Run `tests/unit/adapters/secrets/test_environment.py`, `tests/unit/infrastructure/config/test_models.py`, and `tests/unit/application/services/test_model_gateway.py` and record expected secret/config failures before implementation

### Implementation for User Story 4

- [x] T040 [P] [US4] Implement explicit environment SecretReference resolution, empty/missing-secret errors, and non-enumerating redacted secret values in `src/zhiyi/adapters/secrets/environment.py`
- [x] T041 [P] [US4] Implement Pydantic outer configuration for curated capabilities, limits, Provider target/route validation, and safe application-object construction in `src/zhiyi/infrastructure/config/models.py`
- [x] T042 [US4] Finalize safe AttemptRecord/Usage aggregation and logging hooks without Prompt/output/credential bodies in `src/zhiyi/application/models/contracts.py` and `src/zhiyi/application/services/model_gateway.py`
- [x] T043 [US4] Run US4 green and execute the 1,000-call mixed-target concurrency isolation acceptance case in `tests/unit/application/services/test_model_gateway.py`

**Checkpoint**: All four user stories are complete under the offline release gate.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Prove extension, performance, opt-in connectivity, documentation alignment, CI, and governance.

- [x] T044 [P] Write the mandatory 10,000-call Gateway overhead acceptance measurement with stable warm-up, monotonic timing, p95 calculation, no network/provider wait, and no environment-based skip in `tests/performance/test_model_gateway_overhead.py`
- [x] T045 [P] Add default-skipped, one-request, short-timeout OpenAI and Anthropic online smoke tests in `tests/integration/models/test_openai_smoke.py` and `tests/integration/models/test_anthropic_smoke.py`
- [x] T046 [P] Add a non-deploying Python quality workflow for frozen sync, offline pytest, Ruff, mypy, governance tests, and drift checking in `.github/workflows/runtime-python.yml`
- [x] T047 Add explicit public exports and extension-seam assertions for a third test Provider without modifying existing adapters in `src/zhiyi/application/models/__init__.py`, `src/zhiyi/application/ports/__init__.py`, `src/zhiyi/application/services/__init__.py`, `src/zhiyi/adapters/models/__init__.py`, and `tests/contract/models/test_provider_contract.py`
- [x] T048 Update implemented Model Gateway/Provider status, boundaries, failure semantics, versioned dependencies, and remaining Runtime scope in `doc/功能文档.md` and `doc/技术方案.md`
- [x] T049 [P] Update repository/product-stage wording and local Python validation entry points without claiming Runtime completion in `README.md` and `doc/PROJECT.md`
- [x] T050 Create and maintain alignment evidence, exact docs impact, test evidence, dependency review, rollback, and convergence results in `specs/003-llm-provider/drift-report.md`
- [x] T051 Run `uv sync --all-groups --frozen`, all offline pytest suites, Ruff check/format, mypy, governance unit tests, and `scripts/sdd/check_design_drift.py --worktree --gate manual`; record actual outputs in `specs/003-llm-provider/drift-report.md`
- [x] T052 Execute `$speckit-converge`, append any missing tasks to `specs/003-llm-provider/tasks.md`, finish them test-first, rerun all affected checks, and only then mark the feature complete

---

## Requirement and Success-Criteria Traceability

| Requirement / criterion | Primary tasks |
|---|---|
| FR-001–FR-004, SC-001 | T004–T017 |
| FR-005–FR-006 | T018–T025 |
| FR-007–FR-008, SC-002 | T004–T006, T016, T024, T037, T041 |
| FR-009–FR-012, SC-003/SC-005 | T026–T035 |
| FR-013–FR-015, FR-019, SC-006/SC-007 | T028, T034, T036–T043 |
| FR-016–FR-017, SC-008 | T007–T012, T018–T021, T045–T046 |
| FR-018, SC-004 | T026–T043 |
| FR-020 | T001–T052 and explicit scope checks in T048–T050 |
| SC-009 | T044, T051 |
| SC-010 | T006–T008, T047 |

## Dependencies & Execution Order

### Phase dependencies

- **Setup**: starts immediately.
- **Foundational**: depends on Setup and blocks all stories.
- **US1**: depends on Foundational; establishes the executable Model Gateway MVP.
- **US2**: depends on the US1 LangChain mapping and Gateway terminal contract.
- **US3**: depends on US1; can proceed in parallel with US2 after shared mapping stabilizes.
- **US4**: depends on Foundational and integrates with the US1/US3 Gateway; secret/config tests can begin in parallel with US2/US3.
- **Polish**: depends on the selected stories; final verification requires all four.

### User story dependencies

```text
Setup -> Foundation -> US1 -> US2
                         \-> US3 -> US4 integration
Foundation ----------------> US4 secret/config slice
US1 + US2 + US3 + US4 -> Polish -> Converge
```

### Within each story

- Tests are written and observed failing for the intended missing behavior before production code.
- Platform contracts precede adapters; adapters precede Gateway orchestration.
- No retry/Fallback behavior is accepted until exact attempt and cancellation tests pass.
- Story checkpoint tests pass before later refactoring.

## Parallel Opportunities

- T002 can run while T001/T003 resolve package metadata and lock state.
- T004, T006, and T007 touch separate foundational files after package directories exist.
- T009–T011 can be authored in parallel; T014 and T015 can be implemented in parallel after T013.
- T018–T020 can be authored in parallel.
- T026 and T027, then T031 and T032, are independent policy components.
- T036/T040 and T037/T041 are independent secret/config slices.
- T044–T046 and T049 touch separate performance, smoke, CI, and documentation files.

## Parallel Examples

### User Story 1

```text
Task T010: OpenAI offline contract tests
Task T011: Anthropic offline contract tests

After shared mapping T013:
Task T014: OpenAI adapter
Task T015: Anthropic adapter
```

### User Story 3

```text
Task T026 -> T031: token-bucket tests and implementation
Task T027 -> T032: circuit-breaker tests and implementation
```

### User Story 4

```text
Task T036 -> T040: environment secret boundary
Task T037 -> T041: curated model configuration
```

## Implementation Strategy

### MVP first

1. Complete T001–T008.
2. Complete T009–T017 for provider-neutral text and streaming.
3. Stop and validate US1 without online credentials.
4. Add Tool/Structured Output, reliability, and secure configuration as independent increments.

### Completion gate

Implementation can begin only after `speckit-analyze` reports no critical findings and the
reviewer-owned `checklists/security.md` has a reviewer disposition. Completion requires T001–T052,
green validation evidence, `drift-report.md` alignment, and convergence with no unfinished appended task.

## Notes

- `[P]` means the task changes different files and has no incomplete dependency.
- The online marker never belongs to the default CI pytest command.
- Do not use live Provider output as a golden snapshot.
- Do not commit, push, deploy, or add Provider credentials as part of this feature.

## Phase 8: Convergence

- [x] T053 Add exhaustive offline OpenAI/Anthropic SDK exception classification, retry/Fallback flags, Provider request-ID preservation, and raw-body/credential redaction tests in `tests/contract/models/test_openai_contract.py` and `tests/contract/models/test_anthropic_contract.py`, then correct safe mappings in `src/zhiyi/adapters/models/openai.py` and `src/zhiyi/adapters/models/anthropic.py` per SC-001/SC-003 and FR-013 (partial)
- [x] T054 Add failing contract tests for empty/whitespace-only requests, runtime-invalid roles/content, malformed Tool/Structured Schemas, and mismatched/empty terminal payloads in `tests/unit/application/models/test_contracts.py`, then enforce pre-network invariants in `src/zhiyi/application/models/contracts.py` per Edge Cases and FR-001/FR-005 (partial)
- [x] T055 Add cancellation-observability tests for complete, stream wait, and early stream close in `tests/unit/application/services/test_model_gateway.py`, then emit exactly one safe `AttemptOutcome.CANCELLED` record through the attempt hook while preserving `CancelledError`, iterator cleanup, and no-retry behavior in `src/zhiyi/application/services/model_gateway.py` per FR-009/FR-014 (partial)
- [x] T056 Extend `tests/unit/adapters/models/test_langchain_mapping.py` with system/user/assistant/Tool and ordered text/image/document mapping plus unknown-role rejection, correcting `src/zhiyi/adapters/models/langchain_base.py` only if tests expose a mapping defect, per FR-003 and US1/AC1–AC4 (partial)
- [x] T057 Expand the 1,000-call mixed complete/stream concurrency and sensitive-failure acceptance case in `tests/unit/application/services/test_model_gateway.py` so request-specific messages, Tool fragments, Usage, credentials, Attempt summaries, and errors prove isolation and zero body leakage per SC-004/SC-006/SC-007 (partial)
