## Why

The repository is empty and needs a production-oriented foundation for building stateful, tool-using agents with the LangChain ecosystem. The first delivery must prove a complete, restartable Agent Loop while keeping product contracts independent from LangChain, LangGraph, model providers, and observability vendors.

## What Changes

- Create a Python service with explicit domain, application, runtime, adapter, API, and infrastructure boundaries.
- Add a LangGraph-based Agent Loop that calls LangChain chat models, validates tool calls, executes registered tools, and stops under deterministic budgets.
- Add PostgreSQL-backed run persistence, LangGraph checkpoints, short-term conversation state, and governed long-term memory interfaces.
- Add a typed tool registry with risk classification, approval hooks, timeout handling, output limits, audit records, and idempotency support.
- Add REST and server-sent event APIs for sessions, runs, resumable approvals, cancellation, and live Agent events.
- Add optional Langfuse tracing and evaluation integration that never blocks Agent execution and redacts sensitive fields before export.
- Add local development infrastructure, migrations, tests, static checks, structured logging, health endpoints, and operational documentation.

## Capabilities

### New Capabilities

- `agent-runtime`: Stateful Agent execution, lifecycle, bounded loops, streaming, cancellation, interruption, and recovery.
- `tool-execution`: Typed tool registration, policy enforcement, approvals, idempotent execution, MCP extension points, and auditability.
- `context-memory`: Scenario-aware context assembly, token budgeting, short-term state, and governed long-term memory.
- `platform-observability`: Stable API events, structured logs, metrics-ready instrumentation, Langfuse traces, and evaluation metadata.

### Modified Capabilities

None. This is a greenfield repository with no existing capability contracts.

## Impact

- Adds the initial application source tree, packaging, database schema, API contracts, tests, development containers, and CI configuration.
- Introduces LangChain, LangGraph, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, and optional Langfuse dependencies.
- Establishes durable identifiers and records for tenants, agents, sessions, runs, steps, tool invocations, approvals, events, and memories.
- Requires model-provider credentials only when a real provider is enabled; tests and local smoke checks use deterministic fake models.
