# Contract: PostgreSQL RunRepository Adapter

**Feature**: `005-postgresql-run-repository`

**Contract kind**: Internal application port implementation; no REST, SSE, SDK, or
database-administration API is introduced.

## Boundary

`PostgreSQLRunRepository` implements the existing framework-neutral
`RunRepository` protocol. Domain and application callers continue to exchange only
`Run`, `RunEvent`, `CommandReceipt`, `CommitOutcome`, identifier value objects, and
stable application/domain errors. SQLAlchemy, Psycopg, table rows, sessions,
connections, SQLSTATE, and migration types never cross the adapter boundary.

The implementation does not:

- claim, lease, renew, recover, or execute a Run;
- create a Worker, Reconciler, scheduler, or background loop;
- create or read LangGraph checkpoints;
- call the Model Gateway, tools, providers, REST, SSE, or SDK code;
- create, upgrade, stamp, or repair database structures at application startup.

## Repository Operations

The public method shapes remain the existing 004 protocol:

```python
async def load(tenant_id: TenantId, run_id: RunId) -> Run | None: ...

async def list_events(
    tenant_id: TenantId,
    run_id: RunId,
    *,
    after_sequence: int = 0,
    limit: int = 100,
) -> tuple[RunEvent, ...]: ...

async def find_command(
    tenant_id: TenantId,
    command_id: CommandId,
    intent_fingerprint: str,
) -> CommitOutcome | None: ...

async def commit(
    *,
    expected_version: int,
    updated_run: Run,
    new_events: tuple[RunEvent, ...],
    receipt: CommandReceipt,
) -> CommitOutcome: ...
```

### `load`

- Uses `(tenant_id, run_id)` in the query.
- Returns a fully detached immutable `Run` or `None`.
- A Run that exists in another tenant is indistinguishable from no Run.
- Invalid persisted format/projection/domain combinations fail with
  `data_corruption`; they are never silently coerced.

### `list_events`

- Retains existing input validation: `after_sequence` is a non-negative integer;
  `limit` is an integer from 1 through 1,000, default 100.
- Proves the tenant-scoped Run exists before returning an event stream. Missing and
  cross-tenant Runs use the same existing `not_found` result.
- Returns only sequences strictly greater than `after_sequence`, in numeric order,
  at most `limit` rows.
- Every event access includes tenant and Run predicates even though `event_id` is
  globally unique.

### `find_command`

- Searches only the current tenant's command space.
- Missing command returns `None`.
- Matching fingerprint returns the original receipt and zero/one original events
  with `replayed=True`.
- Different fingerprint returns existing `idempotency_conflict` without reading or
  exposing the Run, original event payload, status, or answer.
- The adapter compares the already-established 004 fingerprint. It does not receive,
  persist, or re-normalize raw command intent.

### `commit`

- Preserves 004 input type/range validation and error priority.
- Public type/basic-range validation occurs before storage access. For valid typed
  inputs, existing-command replay/conflict precedes expected-version and aggregate
  invariant evaluation.
- Atomically persists one updated Run snapshot, zero/one immutable event, and one
  immutable command receipt.
- Same command/same fingerprint replays before expected-version validation.
- Same command/different fingerprint fails before Run access.
- State-changing same-version competitors have at most one winner. Distinct legal
  zero-event commands may each persist a receipt while the Run remains unchanged.
- Failed validation, version conflict, event uniqueness conflict, timeout, or storage
  failure leaves no partial receipt/Run/event combination.
- The adapter does not retry a write internally.

## Transaction Protocol

Each `commit()` owns one connection and one explicit `READ COMMITTED` transaction.
Connections/transactions are never shared across concurrent tasks.

```text
BEGIN
SET LOCAL synchronous_commit = on
SET LOCAL lock_timeout = configured finite value
SET LOCAL statement_timeout = configured finite value

INSERT complete candidate receipt
ON CONFLICT (tenant_id, command_id) DO NOTHING
RETURNING ...

if command already exists:
    SELECT immutable receipt in a new statement snapshot
    compare fingerprint
    return conflict or original replay
else:
    create Run or SELECT tenant-scoped Run FOR UPDATE
    check expected version
    decode current facts and validate shared 004 invariants
    INSERT/UPDATE Run only when changed
    INSERT zero or one Event
    COMMIT
```

The physical receipt is inserted first to arbitrate concurrent replays. Its foreign
keys to the new Run/Event are deferred until commit, while its command primary key is
immediate. For existing Runs, the fixed lock order is command key -> Run row -> event
unique indexes.

A zero-event command linearizes while holding the current Run row lock. It succeeds
only if the supplied expected version and complete snapshot match at that point. A
state-changing command that committed first causes a version conflict; a
state-changing command that waits behind a successful zero-event receipt may continue
after the receipt commits because the Run version did not change.

## Stable Error Contract

Existing lifecycle/domain errors remain unchanged:

| Condition | Error |
|---|---|
| Existing command, different fingerprint | `idempotency_conflict` |
| Missing/current version differs from expected | `version_conflict` |
| Duplicate global event ID, wrong ownership/sequence, receipt mismatch, or other invalid commit | `invariant_violation` |
| Missing/cross-tenant event stream | `not_found` |

The application port adds a separate repository failure family:

| Stable code | Contract |
|---|---|
| `storage_unavailable` | Read storage was unavailable, or a failed write is known not to have committed. The caller may apply finite retry/backoff outside the repository; every command retry retains the original command identity and intent. |
| `commit_outcome_unknown` | COMMIT may have completed but acknowledgement was lost. The only safe resolution is a new connection plus the original `command_id` and identical normalized intent. A new command ID is forbidden. |
| `data_corruption` | Connected rows could not be reconstructed into legal 004 facts, or authoritative and projected fields disagreed. Do not synthesize a legal Run. |
| `schema_incompatible` | The database is reachable but the RunRepository compatibility contract is missing or unsupported. Do not migrate or guess. |

### Failure classification

- Before commit invocation or after an explicit confirmed abort/rollback:
  `storage_unavailable`.
- Connection acquisition, lock/statement timeout, deadlock, serialization abort, or
  capacity exhaustion is `storage_unavailable` only when rollback/non-commit is known;
  none is retried inside the repository.
- During commit acknowledgement, SQLSTATE `08007`/`40003`, or unknown transaction
  status: `commit_outcome_unknown`.
- A generic SQLAlchemy/DBAPI exception type or `connection_invalidated=True` is not,
  by itself, proof of either outcome.
- Named constraint mappings may produce only the safe errors above. Raw constraint,
  SQL, parameter, host, and driver messages are not rethrown.
- Error precedence is: unreachable storage -> `storage_unavailable`; reachable but
  unsupported contract -> `schema_incompatible`; compatible structure plus invalid
  record/projection/reference -> `data_corruption`; entered unknown commit phase ->
  `commit_outcome_unknown` without later inference.
- Replaying an unknown outcome on a new connection returns the persisted receipt if
  the first transaction committed. If no receipt exists because it rolled back, the
  same replay may acquire the command key and perform the one complete commit.

## Schema Compatibility Contract

- Alembic owns migration revisions; `zhiyi_schema_compatibility` owns the application
  read/write contract version.
- Initial accepted compatibility set is `{1}`.
- Repository assembly/first use performs a read-only compatibility check and caches
  it only for that repository/engine lifecycle.
- Empty, older, newer, or partially migrated structures fail closed as
  `schema_incompatible`.
- A database connection failure while checking compatibility is
  `storage_unavailable`.
- Application startup never calls Alembic or SQLAlchemy `create_all()`.

## Assembly and Configuration

Infrastructure assembly receives a secret-resolved database URL and non-secret
options. It constructs an async engine with:

- `postgresql+psycopg`;
- finite connect/operation/lock timeouts;
- SQLAlchemy pool pre-ping for stale checkout detection;
- one SQLAlchemy pool only;
- `hide_parameters=True`;
- SQL echo disabled;
- bounded pool size and overflow values supplied by configuration.

The URL/credential object uses a secret-safe representation. Configuration and error
validation must prove that DSNs, passwords, raw SQL, SQL parameters, complete event
payloads, final answers, and hidden reasoning never appear in `str`, `repr`, or logs.

## Observability Contract

Repository diagnostics use structured, bounded fields only:

```text
operation
transaction_phase
stable_error_code
correlation_id
tenant_id (when safe and available)
run_id (when safe and available)
duration_ms
replayed
```

Public exceptions and printable representations expose only stable code, constant
safe message, and optional correlation ID; they never contain tenant/run identifiers.
Internal logs may contain tenant/run identifiers already provided by the caller, but
never an owner identifier learned from a conflicting/cross-tenant record. No log
contains command input/fingerprint, event payload, result answer, SQL text, parameters,
host, port, username, password, or DSN. Expected domain conflicts are not logged as
stack traces. Storage/compatibility/corruption failures preserve exception chaining
internally for debugging while exposing only the stable safe error.

## Acceptance Contract

The PostgreSQL adapter must pass:

1. The same provider-neutral contract suite as `MemoryRunRepository`.
2. Restart round trips for all statuses, results, events, receipts, usage, UTC values,
   extreme valid Decimal examples, a 5,000-digit non-negative counter, and positive
   and negative 5,000-digit nested JSON integers while preserving the process-wide
   integer-string conversion limit.
3. Real multi-engine/multi-connection command replay and version races.
4. Global event-ID and tenant/Run negative tests.
5. Receipt/Run/Event atomicity at every injected failure point.
6. Known rollback versus unknown commit-outcome classification and original-command
   convergence.
7. Data-corruption, schema-compatibility, migration, downgrade/re-upgrade, and backup
   restore exercises.
8. Sensitive marker scanning over public exceptions, printable objects, and captured
   logs.
9. Twenty-client latency acceptance without disabling constraints or tenant filters.
