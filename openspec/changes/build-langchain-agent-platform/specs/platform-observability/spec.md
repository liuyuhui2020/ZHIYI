## ADDED Requirements

### Requirement: Publish durable Agent events
The system SHALL persist a stable, tenant-scoped event contract with a monotonically increasing sequence for every run.

#### Scenario: Client streams a live run
- **WHEN** an authenticated client opens the run event stream
- **THEN** the system emits persisted events in sequence and sends heartbeats without blocking Agent execution

#### Scenario: Client reconnects
- **WHEN** a client reconnects with its last received event sequence
- **THEN** the system resumes from the next available event without intentionally duplicating earlier events

#### Scenario: Client requests another tenant's stream
- **WHEN** a principal requests events for a run outside its tenant
- **THEN** the system returns not found and emits no event data

### Requirement: Record safe operational telemetry
The system SHALL produce structured logs and trace metadata with correlation identifiers while preventing configured sensitive values from being exported.

#### Scenario: Model and tool calls are traced
- **WHEN** tracing is enabled for a run
- **THEN** one run trace contains observations for graph nodes, model calls, retrieval, memory, and tools with latency and usage metadata

#### Scenario: Telemetry exporter fails
- **WHEN** Langfuse or another trace exporter is unavailable
- **THEN** Agent execution continues, the exporter failure is rate-limited in logs, and the run result is unaffected

#### Scenario: Sensitive tool payload is observed
- **WHEN** a tool input or output is classified as sensitive
- **THEN** raw values are redacted or omitted before logging or trace enqueueing

### Requirement: Expose health and readiness
The system SHALL expose liveness and readiness endpoints that distinguish process health from dependency readiness.

#### Scenario: Database is unavailable
- **WHEN** the process is running but PostgreSQL cannot be reached
- **THEN** liveness remains healthy while readiness reports unavailable with no credentials or internal connection details

### Requirement: Support repeatable evaluation
The system SHALL attach stable Agent, prompt, model, tool, and dataset version metadata to runs used in evaluation.

#### Scenario: Evaluation run is compared
- **WHEN** the same dataset is executed against two Agent versions
- **THEN** correctness, tool-use, latency, token, and cost scores can be attributed to each version without changing production records
