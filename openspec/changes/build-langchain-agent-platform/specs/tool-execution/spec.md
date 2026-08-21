## ADDED Requirements

### Requirement: Register typed and versioned tools
The system SHALL expose only tools registered with a unique name, version, validated input schema, risk classification, timeout, result limit, permissions, and side-effect metadata.

#### Scenario: Valid tool is available
- **WHEN** an Agent is built for a principal with the required permission
- **THEN** the model receives the registered tool name, description, and JSON-compatible input schema

#### Scenario: Unknown or unauthorized tool is requested
- **WHEN** a model requests an unregistered tool or a tool unavailable to the tenant principal
- **THEN** the system denies execution, records the decision, and returns a typed tool error to the graph

#### Scenario: Tool arguments are invalid
- **WHEN** model-generated arguments fail the registered schema
- **THEN** the tool is not called and a bounded validation result is returned to the Agent Loop

### Requirement: Execute tools safely
The system MUST enforce timeout, concurrency, output-size, approval, and cancellation policies before returning a tool result to the model.

#### Scenario: Safe read tools execute in parallel
- **WHEN** one model message proposes multiple tools marked read-only and parallel-safe
- **THEN** the system may execute them concurrently within the configured concurrency limit

#### Scenario: Side-effect tool requires serialized execution
- **WHEN** a side-effecting tool is approved
- **THEN** the system executes it serially under its idempotency key and persists the outcome before continuing the graph

#### Scenario: Tool times out or returns excessive output
- **WHEN** execution exceeds the timeout or result-size limit
- **THEN** the system stops consuming output, persists a sanitized typed error, and continues or terminates according to policy

### Requirement: Make tool invocation idempotent and auditable
The system SHALL persist each invocation using a deterministic idempotency key and MUST not intentionally repeat a completed side effect during retry or graph recovery.

#### Scenario: Completed invocation is replayed
- **WHEN** recovery encounters the same tenant, run, tool-call, and tool-version idempotency key
- **THEN** the system returns the persisted result without calling the tool again

#### Scenario: Invocation outcome is ambiguous
- **WHEN** a worker loses contact after starting a non-queryable external side effect
- **THEN** the invocation is marked outcome-unknown and requires policy or operator resolution instead of automatic retry

### Requirement: Support MCP through the registry boundary
The system SHALL allow MCP-discovered tools to be adapted into the same registry and policy model without exposing MCP transport objects to the Agent runtime.

#### Scenario: MCP tool lacks risk metadata
- **WHEN** a remote MCP tool is discovered without trusted policy metadata
- **THEN** the system assigns a conservative risk classification and requires explicit enablement before use
