# ZHIYI Constitution

## Core Principles

### I. Specification Before Implementation (NON-NEGOTIABLE)

Every product change MUST belong to exactly one active Spec Kit feature under
`specs/NNN-feature-name/`. Before production code is written, `spec.md` MUST
describe user-visible behavior and measurable acceptance criteria, `plan.md`
MUST describe the design and constitution checks, and `tasks.md` MUST map work
to concrete file paths and tests. Clarification markers and template
placeholders are blocking at implementation time.

An intentional behavior, API, data, permission, state-machine, security, or
architecture change MUST update the affected Spec Kit artifacts first. Code is
not allowed to redefine an approved design by accident.

### II. Product Semantics Own the Framework

ZHIYI owns the stable concepts Tenant, Agent, AgentVersion, Session, Task, Run,
Tool, Approval, Memory, Artifact, and Event. LangGraph provides graph execution,
checkpointing, interrupts, and recovery; LangChain provides model, message,
tool-schema, and structured-output adapters; Langfuse provides observation and
evaluation only. Framework or provider objects MUST NOT become public API or
domain contracts.

Dependencies point inward. The domain layer MUST NOT import FastAPI, Pydantic,
SQLAlchemy, LangChain, LangGraph, Langfuse, provider SDKs, or outer project
layers. Application code depends on domain and ports. Runtime orchestration
depends on application contracts. Adapters implement ports. API routes call
application use cases and MUST NOT operate on ORM models directly.

### III. Test-First and End-to-End Traceability (NON-NEGOTIABLE)

Executable behavior follows Red-Green-Refactor: add a test that fails for the
expected reason, implement the smallest correct change, then refactor while the
suite remains green. Every implementation file changed for a feature MUST be
named by a task in `tasks.md`; each requirement and success criterion MUST be
covered by a task, test, or explicit verification step.

Tests MUST cover normal behavior, invalid and boundary input, authorization and
tenant isolation, timeout and partial failure, retries, concurrency,
idempotency, recovery, and sensitive-data redaction where applicable.

### IV. Recoverable, Idempotent Agent Execution

Every Run MUST bind an immutable AgentVersion and reach a terminal, resumable,
cancellable, timed-out, or human-resolution state. Tool execution semantics are
at-least-once plus idempotency; the project MUST NOT claim exactly-once external
effects. A side effect with unknown outcome MUST enter explicit resolution and
MUST NOT be retried automatically. Interrupts occur before non-idempotent side
effects, and recovery tests MUST prove completed tools are not repeated.

### V. Tools and Context Are Untrusted by Default

Every tool requires a versioned descriptor with input/output schema,
permissions, risk, side-effect class, timeout, output limit, and idempotency
behavior. Write or high-risk operations pass deterministic policy checks and,
when required, explicit approval. Unregistered MCP tools, arbitrary shell, eval,
dynamic Python, and arbitrary SQL are disabled by default.

User, retrieval, memory, and tool content are untrusted data and MUST NOT be
promoted to platform instructions. Context assembly preserves a fixed trust
order and produces a Context Manifest. Long-term memory is accepted by policy,
not written directly at model discretion.

### VI. Tenant Isolation, Privacy, and Least Privilege

Tenant identity is resolved before resource access; tenant-owned records and
high-frequency indexes include `tenant_id`. Cross-tenant negative tests are
mandatory. Secrets MUST come from a secret provider and MUST NOT enter source,
AgentSpec, graph state, events, artifacts, or traces. Data is minimized and
redacted before observability export. High-risk tool payloads are not recorded
by default.

### VII. Observable Without Exposing Hidden Reasoning

Runs, attempts, steps, model calls, tool invocations, approvals, and state
transitions MUST expose structured logs, metrics, and traces with stable IDs.
Langfuse failure MUST NOT break execution correctness. The system MUST NOT
persist, display, or expose raw chain-of-thought as a stable contract; it stores
decisions, structured summaries, citations, evidence, and tool results instead.

### VIII. Simple, Versioned, and Reversible Change

Prefer mature, replaceable components and the smallest design that satisfies an
approved spec. Public APIs, event schemas, prompts, tools, agents, and data
contracts are versioned. Migrations are rolling-upgrade compatible and include
rollback or recovery instructions. New dependencies require a stated purpose,
maintenance and license review, and lockfile update.

## Design Drift Policy

Design drift is classified before a change can be accepted:

1. **Constitution or architecture violation**: hard block. Editing a report or
   product document cannot waive it. Amend the constitution or add an approved
   ADR with migration and rollback consequences before implementation.
2. **Intentional semantic change**: update `spec.md`, `plan.md`, `tasks.md`, the
   drift report, and all affected product/technical documents in the same
   feature. Run `$speckit-analyze` again before implementation continues.
3. **Implementation detail within the approved design**: product documents need
   not churn, but the concrete file MUST remain traceable from `tasks.md`, tests
   MUST prove alignment, and the drift report MUST explain why document impact
   is `NONE`.

The repository drift checker is a deterministic minimum gate. Passing it does
not replace human review, `$speckit-analyze`, `$speckit-converge`, tests, security
review, or architecture review.

## Mandatory SDD Workflow and Quality Gates

The required lifecycle is:

1. `$speckit-constitution` when project principles change.
2. `$speckit-specify` for user scenarios, requirements, and success criteria.
3. `$speckit-clarify` when material ambiguity remains.
4. `$speckit-plan` for technical design, interfaces, risks, and rollback.
5. `$speckit-checklist` for high-risk or cross-cutting work.
6. `$speckit-tasks` with exact file paths and test-first ordering.
7. `$speckit-analyze`; critical findings block implementation.
8. `$speckit-implement` in task order.
9. Run tests, static checks, and `scripts/sdd/check_design_drift.py`.
10. `$speckit-converge`; append and finish missing tasks before completion.

Git pre-commit, pre-push, AI completion hooks, and CI MAY enforce different
subsets for latency, but CI is the final non-bypassable gate. Skipping local
hooks never waives CI or review requirements. A hook failure is resolved only by
bringing implementation and design back into alignment or reverting the change.

## Governance

This constitution supersedes local habits, generated plans, agent suggestions,
and framework defaults. In case of conflict, precedence is:

1. This constitution and explicitly approved ADRs.
2. The active feature `spec.md` and acceptance criteria.
3. The active feature `plan.md`, contracts, and data model.
4. The active feature `tasks.md`.
5. Repository product and technical documentation.
6. Existing implementation.

Amendments require an explicit rationale, affected-artifact list, migration and
rollback assessment, version bump, and human approval. Constitution compliance
MUST be reviewed during planning and again after design. Architectural
exceptions expire unless an ADR states a review date and owner.

**Version**: 1.0.0 | **Ratified**: 2026-08-21 | **Last Amended**: 2026-08-21
