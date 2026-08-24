# Design Drift Report

**Feature**: 004-run-lifecycle-kernel
**Status**: ALIGNED
**Docs-Impact**: UPDATED
**Docs-Updated**: README.md, doc/PROJECT.md, doc/功能文档.md, doc/技术方案.md
**Docs-Impact-Reason**: The feature intentionally adds implemented Run lifecycle, budget, command-idempotency, event, result, and persistence-port semantics and will update product status and the complete cancellation/timeout transition matrix without claiming PostgreSQL, Worker, Graph, or API completion.
**Reviewed-By**: AI

## Alignment Evidence

- Requirements and success criteria checked: all 25 functional requirements and 12 success
  criteria remain represented by the approved plan/tasks. Tests cover the complete state-pair
  matrix, four terminal results, every budget dimension below/equal/over, deadline, command and
  charge idempotency, tenant isolation, event/result safety, concurrency, and performance.
- Plan and contract checked: implementation preserves immutable domain values, application-owned
  orchestration, tenant-scoped pre-transition receipt lookup, receipt-before-version commit order,
  exact Decimal arithmetic, `1..1000` event pagination, and the approved no-network/no-database
  slice boundary.
- Tasks and implementation paths checked: T001-T052 are complete, including four appended
  convergence tasks; every product implementation file appears explicitly in `tasks.md`.
- Test-first evidence: foundation initially failed collection for missing budget/event/command
  modules; US1 failed for the absent aggregate; US2 failed for the absent memory repository and
  lifecycle service. Implementations were then added and the focused quickstart commands pass:
  domain 127, commands 6, services 10, repository contract 6, memory adapter 4, performance 1.
- Full verification evidence on Python 3.12.13: frozen sync checked 53 packages; offline pytest
  collected 279 tests and passed 277 with 2 explicitly deselected online tests; Ruff check passed;
  Ruff format reports 64 formatted files; strict mypy passed 64 source files; SDD governance passed
  29 tests; the manual design-drift gate passed while checking 26 implementation files.
- Architecture and safety evidence: AST dependency tests scan every `src/zhiyi/domain/**/*.py`
  file for forbidden outer/framework imports. Sentinel tests reject unstructured Prompt,
  authorization, Provider body, raw output, and reasoning fields; approved final answer text exists
  only in the explicit result property and is redacted from repr, fingerprints, receipts, events,
  and service outcomes.
- Concurrency and performance evidence: deterministic 1,000-way duplicate-command replay and
  different-command/cancel-vs-charge races have exactly one write winner where required; the
  mandatory warmed 10,000-transition test passes the monotonic p95 <= 1 ms gate without skips.
- Dependency evidence: `pyproject.toml` and `uv.lock` are unchanged; no package, migration,
  network call, Provider credential, deployment, or external state was added.
- Documentation evidence: `README.md`, `doc/PROJECT.md`, `doc/功能文档.md`, and
  `doc/技术方案.md` now describe two completed M0 foundation slices, the full cancellation/limit
  matrix, safe lifecycle contracts, and PostgreSQL/lease/Worker as the next scope. They explicitly
  retain the incomplete Runtime/API/recovery warning. `doc/需求文档.md` is unchanged because this
  feature implements its existing product requirements rather than changing the product baseline.
- Documentation build evidence: Astro checked 20 files with zero diagnostics, built all 9 pages,
  and validated 8 required routes, internal links, anchors, canonical URLs, sitemap, and Pagefind.
- Convergence evidence: the first audit appended T049-T052 for SC-002/003/004 acceptance depth and
  the single-event commit invariant; all four were completed. The second audit checked 25 FR,
  12 SC, 18 acceptance scenarios, 7 plan decisions, and 8 constitution principles with zero
  remaining missing, partial, contradicting, or unrequested findings.

## Intentional Differences

None.

## Rollback

- No database, migration, deployment, network, Tool, or external state is in scope.
- Rollback removes the 004 `domain/runs`, lifecycle command/service/port, in-memory persistence
  adapter, and associated tests, then restores the four synchronized status/design documents. No
  data rollback is required; the completed 003 Model Gateway remains unchanged and unintegrated.

## Blocking Findings

None. Any later finding reopens the report and tasks before completion is claimed.
