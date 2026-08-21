## ADDED Requirements

### Requirement: Assemble context deterministically
The system SHALL build model context from typed context items with fixed precedence, provenance, trust, sensitivity, and token estimates.

#### Scenario: Context fits the model budget
- **WHEN** all eligible context items fit after reserving output capacity
- **THEN** the assembler emits them in deterministic precedence order and records a context manifest

#### Scenario: Context exceeds the model budget
- **WHEN** eligible context exceeds the available input budget
- **THEN** the assembler drops or summarizes lower-priority items according to policy without removing mandatory platform or safety instructions

#### Scenario: Untrusted retrieved instruction conflicts with policy
- **WHEN** retrieved or tool-provided content contains instructions conflicting with trusted policy
- **THEN** it remains marked as untrusted data and cannot replace platform or Agent policy

### Requirement: Persist short-term conversation state
The system SHALL scope working conversation state to a graph thread and MUST support trimming or summarization before the provider context limit is exceeded.

#### Scenario: Follow-up run uses session history
- **WHEN** a new run explicitly continues an existing conversation thread
- **THEN** eligible prior messages are included subject to the context budget and policy

#### Scenario: Independent run avoids state collision
- **WHEN** two independent tasks share a product session
- **THEN** their working graph state remains isolated unless context is explicitly copied through the application layer

### Requirement: Govern long-term memory
The system SHALL store long-term memory with tenant, subject, agent, namespace, provenance, sensitivity, creation time, and optional expiration.

#### Scenario: Relevant memory is recalled
- **WHEN** the memory policy selects records relevant to a new task
- **THEN** only tenant-authorized, unexpired records are returned as attributed context items

#### Scenario: Memory write is not permitted
- **WHEN** an Agent proposes long-term storage without a matching write policy
- **THEN** no memory is persisted and the denial is observable

#### Scenario: Subject requests deletion
- **WHEN** an authorized deletion request targets a subject or namespace
- **THEN** matching memories become unavailable to future retrieval and deletion is audited

### Requirement: Enforce tenant isolation
The system MUST include tenant scope in every context, memory, session, and retrieval operation.

#### Scenario: Cross-tenant memory identifier is supplied
- **WHEN** a principal attempts to retrieve another tenant's memory by identifier
- **THEN** the system returns no record and logs a security-relevant denial without exposing record existence
