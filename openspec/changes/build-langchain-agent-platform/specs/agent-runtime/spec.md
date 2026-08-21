## ADDED Requirements

### Requirement: Create a bounded Agent run
The system SHALL create a tenant-scoped run from a valid session and user message, and the run MUST enforce configured step, model-call, tool-call, time, token, and cost limits.

#### Scenario: Valid run is queued
- **WHEN** an authenticated tenant submits a valid message to its session
- **THEN** the system persists one queued run and returns its stable identifier

#### Scenario: Invalid session is rejected
- **WHEN** a tenant submits a run for a missing session or a session owned by another tenant
- **THEN** the system returns a not-found response without creating a run

#### Scenario: Loop budget is exhausted
- **WHEN** an Agent reaches any configured hard limit before producing a final answer
- **THEN** the system terminates the run with a typed limit-exceeded result and emits a terminal event

### Requirement: Execute and recover graph state
The system SHALL execute the Agent Loop using durable LangGraph checkpoints and MUST allow a worker to resume from the latest committed checkpoint after a recoverable failure.

#### Scenario: Worker resumes an interrupted run
- **WHEN** a worker lease expires after at least one graph step was checkpointed
- **THEN** another worker can reclaim the run and continue from durable state without deleting prior run events

#### Scenario: Unrecoverable model failure
- **WHEN** all configured model retries and fallbacks fail
- **THEN** the run transitions once to failed with a sanitized error code and diagnostic correlation identifier

### Requirement: Interrupt, approve, and cancel runs
The system SHALL support policy-driven human interruption and cancellation with explicit state transitions.

#### Scenario: Approval is required
- **WHEN** a proposed tool call matches a policy requiring review
- **THEN** the graph checkpoints its state, the run becomes waiting-for-approval, and an approval-required event is persisted

#### Scenario: Approval is resumed
- **WHEN** an authorized tenant principal approves, edits, or rejects a pending action
- **THEN** the system records the decision exactly once and resumes or terminates execution accordingly

#### Scenario: Active run is cancelled
- **WHEN** an authorized caller cancels a queued, running, or waiting run
- **THEN** the run reaches cancelled state and no new model or tool steps are started

### Requirement: Isolate concurrent runs
The system MUST use independent graph thread identifiers for independent runs and SHALL prevent concurrent mutation of the same run state.

#### Scenario: Session has concurrent tasks
- **WHEN** two runs are created in the same product session
- **THEN** each run executes with an independent graph thread while retaining the common product session identifier

#### Scenario: Duplicate worker claim
- **WHEN** multiple workers attempt to claim the same queued or expired run
- **THEN** database locking and lease checks allow only one worker to own the active execution lease
