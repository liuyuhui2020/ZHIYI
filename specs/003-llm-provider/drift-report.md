# Design Drift Report

**Feature**: 003-llm-provider
**Status**: ALIGNED
**Docs-Impact**: UPDATED
**Docs-Updated**: README.md, doc/PROJECT.md, doc/功能文档.md, doc/技术方案.md
**Docs-Impact-Reason**: Model Gateway is now an implemented and offline-tested M0 slice, so repository status, Provider behavior, pinned dependency versions, failure boundaries, validation commands, and the still-unimplemented Agent/Run Runtime scope were synchronized without claiming full Runtime completion.
**Reviewed-By**: AI+HUMAN

## Alignment Evidence

- Requirements and success criteria checked: FR-001–FR-020 and SC-001–SC-010
  are traced to T001–T052. The implementation covers provider-neutral complete
  and stream contracts, OpenAI/Anthropic adapters, Tool Calling, Structured
  Output, conservative Token preflight, stable errors, total/attempt/stream
  deadlines, bounded retry/Fallback, rate limiting, circuit breaking, Usage,
  secret boundaries, concurrency, extension, and offline-first verification.
- Plan sections checked: the implementation follows the approved hexagonal
  dependency direction. Application contracts and services do not import
  LangChain, OpenAI, Anthropic, Pydantic, environment access, or infrastructure
  configuration; all such dependencies remain in outer adapters/configuration.
- Tasks and implementation paths checked: all product, test, configuration,
  workflow, dependency, documentation, and feature-artifact paths are named by
  T001–T052. The Python 3.12 aliases use syntax that the repository's Python
  3.9 governance parser can also statically inspect; no architecture rule was
  weakened or bypassed.
- Test evidence: `uv run pytest -m "not online" -q` passes 107 tests with two
  paid smoke tests deselected. This includes offline OpenAI/Anthropic contract
  doubles and complete SDK exception matrices, 1,000 mixed complete/stream calls
  with request/Tool/Usage/credential isolation, cancellation observability, and
  the mandatory 10,000-call local Gateway p95 threshold below 10 ms.
- Quality evidence: `uv run ruff check src tests`, `uv run ruff format --check
  src tests`, and strict `uv run mypy` all pass; mypy reports no issues across
  40 source/test files.
- Reproducibility evidence: `uv sync --all-groups --frozen --python 3.12`
  checks 53 locked packages. Runtime dependencies are exactly pinned to
  `langchain-core==1.6.0`, `langchain-openai==1.6.0`,
  `langchain-anthropic==1.6.1`, and `pydantic==2.13.4`; test/quality tools are
  pinned to `pytest==9.1.1`, `pytest-asyncio==1.4.0`, `ruff==0.16.4`, and
  `mypy==2.3.1`.
- Dependency review: the direct dependency purpose, official maintenance
  source, license, Python compatibility, and lock coverage are recorded in
  `research.md`. No `langchain` meta-package or `langchain-community` dependency
  was introduced, and both Provider SDK retry layers are explicitly disabled.
- Security evidence: the approved 30-item security checklist is complete.
  Environment secrets require allowlisted exact-key lookup, cannot be enumerated
  by the adapter, use redacted values, and never enter public responses,
  AttemptRecords, configuration dumps, or event hooks. Reasoning/thinking and
  unverified structured output are filtered at the adapter/Gateway boundaries.
- CI evidence: `.github/workflows/runtime-python.yml` is non-deploying and runs
  frozen sync, offline pytest, Ruff, mypy, governance tests, and design drift.
  Online tests require an explicit opt-in flag, Provider key, and model ID, and
  issue at most one short request per Provider.
- Documentation evidence: the website content pipeline passes all 17 unit tests;
  Astro reports zero errors, warnings, or hints; 9 static pages build; and the
  validator passes all required routes, links, anchors, canonical URLs, sitemap,
  and Pagefind output.
- Governance evidence: all 29 SDD checker unit tests pass. The final worktree
  drift gate passes with 43 implementation files inspected.

## Intentional Differences

- Python remains targeted to 3.12, but PEP 695 `type` alias statements are not
  used because the authoritative worktree drift command runs under the local
  system Python 3.9 parser. Traditional module-level aliases preserve identical
  runtime and mypy semantics while keeping governance fail-closed inspection
  operational.
- OpenAI and Anthropic adapters were delivered with the independent Model
  Gateway slice instead of waiting for the wider M1 integration milestone.
  Long-term documents now distinguish this completed adapter slice from the
  still-unimplemented Agent/Run Runtime, persistence, API, Worker, Trace,
  billing, and evaluation integration.

## Rollback

- No database, migration, deployment, external message, or persistent state is
  involved. Rollback consists of removing the Provider registrations and Python
  package/workflow introduced by this feature, restoring the prior dependency
  files, and reverting the four synchronized documents.
- Fake Provider and provider-neutral contract removal must not occur after a
  later Runtime feature adopts them without first synchronizing that feature's
  Spec/Plan/Tasks and migration/compatibility plan.
- No online Provider calls or deployment actions were performed during this
  implementation.

## Convergence Result

- The first `$speckit-converge` audit checked 20 FRs, 10 SCs, 17 acceptance
  scenarios, 9 edge cases, 6 plan decisions, and 8 constitution principles. It
  appended T053–T057 for incomplete error-matrix, contract-boundary,
  cancellation-observability, message-order, and concurrency/leakage evidence.
- T053–T057 were completed test-first. The follow-up convergence audit found
  zero missing, partial, contradicting, or unrequested gaps at every severity
  and left `tasks.md` unchanged. All 57 tasks are complete.
- The pre-landing review then found that `SecretResolutionError` handling was
  attached to retry backoff instead of credential resolution. A failing
  regression test proved that missing or unauthorized credentials surfaced as
  `unknown`; the handler was moved to the credential boundary so the public
  error is the required non-retryable `authentication` result before any
  Provider call. A final convergence pass found no remaining gap under
  FR-013/FR-015 or T038/T040/T042 and left `tasks.md` unchanged.

## Blocking Findings

None.

> `ALIGNED` records current Spec/Plan/Tasks/code/document agreement; it cannot
> waive constitution, architecture, security, test, or convergence failures.
