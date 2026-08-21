## Context

This is a greenfield repository. The first release must demonstrate a complete Agent execution path without turning framework objects into permanent product contracts. The service must run locally without paid services, use a deterministic fake model in tests, and enable real model providers and Langfuse through configuration.

The primary stakeholders are application developers defining agents and tools, operators running the service, and API clients creating sessions and consuming live events. The initial deployment target is a containerized modular monolith with separate API and worker processes sharing PostgreSQL.

## Goals / Non-Goals

**Goals:**

- Deliver a restartable, bounded LangGraph Agent Loop with LangChain model and tool integration.
- Keep domain records, API schemas, event schemas, and policy rules independent from vendor SDK types.
- Make tool side effects explicit, authorized, auditable, timeout-bound, and idempotent.
- Assemble context predictably under a token budget and isolate all data by tenant.
- Provide durable run events, SSE streaming, structured logs, health checks, and optional Langfuse traces.
- Provide deterministic tests for success, invalid input, limits, model failures, tool failures, duplicate execution, interruption, and recovery.

**Non-Goals:**

- Compatibility with any previous repository or schema.
- A visual graph editor, tool marketplace, autonomous multi-agent society, or model fine-tuning.
- Arbitrary shell, browser, SQL, or filesystem tools in the first release.
- Exactly-once external side effects; the design provides at-least-once execution with idempotency controls.
- Depending on LangSmith Deployment or Langfuse availability for runtime correctness.

## Decisions

### 1. Use a modular monolith with hexagonal boundaries

The source tree is split into domain, application, runtime, adapters, API, and infrastructure modules. API and worker are separate process entry points but use the same package and database. This keeps the initial operational footprint small while preserving boundaries that can later be extracted.

Alternative considered: microservices from the start. Rejected because distributed transactions, versioned contracts, and operations would slow the first reliable vertical slice without a demonstrated scaling need.

### 2. Use explicit LangGraph `StateGraph` as the execution runtime

The main graph contains context assembly, model invocation, model-output validation, tool policy, tool execution, and finalization nodes. Conditional edges implement the Agent Loop. LangChain `create_agent` remains available for small subagents but is not the top-level product runtime because run budgets, approval states, events, and durable tool semantics must be explicit.

Graph state contains compact execution data and references to durable artifacts, not unbounded payloads. Every run has hard limits for steps, model calls, tool calls, wall time, input tokens, output tokens, and estimated cost.

Alternative considered: a custom `while` loop. Rejected for the first release because durable checkpoints, interruption, streaming, and recovery are core requirements already provided by LangGraph.

### 3. Separate product persistence from LangGraph checkpoints

PostgreSQL is the system of record for tenants, agents, sessions, runs, steps, tool invocations, approvals, events, and memories. LangGraph PostgreSQL checkpoints are opaque continuation state. Checkpoint deletion cannot erase audit history, and replaying audit events never re-executes a graph.

The API inserts a queued run transactionally. Workers claim queued runs using `FOR UPDATE SKIP LOCKED`, set a lease, and periodically renew it. Expired leases can be reclaimed. State transitions append monotonically sequenced run events in the same database transaction.

Alternative considered: an in-process FastAPI background task. Rejected because process termination would lose work.

### 4. Keep LangChain behind model and tool adapters

The application defines provider-neutral requests, responses, usage, finish reasons, tool calls, and errors. Adapters use LangChain chat-model packages and translate messages at the boundary. Provider-specific features are declared in a capability profile rather than assumed to be portable.

Timeout, retry, fallback, rate limit, circuit-breaker, and budget decisions are application policies. Structured output uses Pydantic validation and retains raw provider metadata for diagnostics without exposing it in public API contracts.

### 5. Treat tools as governed commands

Each tool declares a version, Pydantic input schema, risk level, side-effect class, timeout, maximum result size, required permissions, and whether parallel execution is safe. Read-only tools may execute concurrently; side-effecting tools execute serially after policy evaluation and optional approval.

`tenant_id + run_id + tool_call_id + tool_version` is the invocation idempotency key. Invocation and result are persisted before the graph consumes the result. Unknown tools, invalid arguments, oversized output, timeout, and denied approval become typed tool results rather than uncaught graph errors.

MCP is added through the same registry boundary after local tools work; remote MCP tools receive conservative risk defaults.

### 6. Separate working context from governed memory

Short-term conversation state is thread-scoped and checkpointed with the graph. Long-term memory is stored under tenant, subject, agent, and namespace. A context assembler applies fixed precedence: platform policy, agent policy, scenario instructions, current task, selected memories, retrieval results, recent messages, and bounded tool results.

Context items record source, timestamp, trust level, sensitivity, and token estimate. The assembler reserves output capacity, drops lowest-priority items first, and summarizes only through an explicit summarization policy. Long-term writes require a memory policy and retain provenance, expiration, and deletion support.

### 7. Publish a stable event contract over SSE

LangGraph stream chunks are translated into internal events such as `run.started`, `message.delta`, `tool.requested`, `tool.completed`, `approval.required`, `run.completed`, and `run.failed`. Events have a run-local sequence and can be resumed with `Last-Event-ID`. Provider and LangGraph event shapes never appear on the public API.

Slow or disconnected clients do not block execution because clients read persisted events. The API applies pagination, heartbeat, maximum connection duration, and tenant-scoped authorization.

### 8. Make observability optional and failure-isolated

Structured logs include request, tenant, session, run, step, and tool identifiers without raw secrets or full prompts. A trace sink creates one Langfuse trace per run attempt and observations for graph nodes, model calls, retrieval, memory, and tools. Sensitive fields are redacted before enqueueing, and trace-export failure is logged but never fails a run.

Langfuse may manage prompt versions used by experiments, but runtime has a versioned local fallback. Security policy and authorization rules are never loaded from Langfuse.

### 9. Use explicit tenant authentication boundaries

Every non-health API request resolves a tenant principal through an authentication port. The development adapter accepts configured API keys; production deployments can replace it with OIDC or gateway identity. Repository methods require tenant scope, and database uniqueness and lookup constraints include `tenant_id` where applicable.

### 10. Pin a simple, replaceable infrastructure stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL, LangChain, LangGraph, and optional Langfuse form the initial stack. `uv.lock` pins exact transitive versions. Redis, Temporal, a vector database, and Kubernetes are deferred until load or workflow requirements justify them; PostgreSQL plus optional pgvector serves the first release.

## Risks / Trade-offs

- [LangChain ecosystem changes quickly] → Pin versions, isolate imports, add provider contract tests, and upgrade intentionally.
- [Graph checkpoint and product records can diverge] → Assign separate semantics, persist product transitions independently, reconcile stuck runs, and never present checkpoints as audit records.
- [A worker can die during a tool side effect] → Require idempotency keys for side-effecting tools, persist invocation state, use leases, and surface ambiguous outcomes for operator review.
- [Context or traces can leak sensitive data] → Classify context, redact before logging/export, default high-risk tool payload capture off, and test tenant isolation.
- [PostgreSQL event polling can become expensive] → Index by run and sequence, use bounded polling initially, and introduce LISTEN/NOTIFY or a broker only after measurements justify it.
- [Provider behavior differs despite a common interface] → Maintain capability profiles and contract tests; do not silently downgrade required capabilities.
- [The first release is broad] → Deliver one vertical slice first and defer MCP networking, semantic retrieval, and production OIDC until the core loop is verified.

## Migration Plan

1. Create the package, configuration, migrations, and local PostgreSQL environment.
2. Ship domain contracts and a fake-model graph behind tests.
3. Add the durable run worker, event persistence, REST/SSE APIs, and one read-only reference tool.
4. Add real provider and Langfuse adapters behind disabled-by-default configuration.
5. Run migration, unit, integration, type, lint, recovery, and soak checks before enabling external traffic.
6. Roll back by stopping API and worker containers and reverting the schema migration; no legacy data migration is required for this greenfield release.

## Open Questions

- The first release will use a general assistant scenario and a safe calculator/reference tool until a business-specific scenario is selected.
- Langfuse defaults to disabled locally and supports either Cloud or a self-hosted endpoint through configuration.
- Initial performance targets are 100 concurrent active runs per worker pool and a 250 ms p95 event-read overhead, to be revised after baseline measurements.
