# Contract: Worker Lease Kernel

**Feature**: `006-worker-lease-kernel`

**Contract kind**: Internal domain/application ports with PostgreSQL adapters. No
REST, SSE, SDK, Worker process, scheduler, Reconciler, Agent executor, LangGraph, or
Checkpoint contract is introduced.

## Boundary

Application callers exchange immutable ZHIYI values only. SQLAlchemy rows,
connections, transactions, PostgreSQL UUID/time functions, SQLSTATE, and Alembic
types stay behind adapters.

The existing `RunRepository.commit()` remains unchanged and unguarded for existing
004 callers. A Worker-owned write means a new lifecycle fact produced by Worker
execution whose authority depends on that execution still owning the lease; those
writes must depend on the stronger `LeaseGuardedRunRepository`. Independently
authorized control-plane creation, cancellation, or deadline enforcement keeps the
ordinary 004 boundary. An independent authority read never substitutes for the
same-transaction guard.

The kernel does not:

- poll continuously, start a Worker process, execute an Agent/model/Tool/Graph node,
  or perform any external side effect;
- create/read a LangGraph Checkpoint or choose a recovery point;
- reclaim a `running` Run, change its status, or implement a Reconciler;
- expose a public API, SDK, deployment, backup service, or production cleanup job;
- change Run lifecycle semantics, create a RunEvent, or create a 004 CommandReceipt
  except when a caller explicitly invokes `commit_with_lease()` with an existing 004
  lifecycle candidate.

## Framework-neutral method shapes

Names may be split across application modules during implementation, but their
behavior and inputs/outputs must remain equivalent to this contract.

```python
@dataclass(frozen=True, slots=True)
class LeaseOperationObservation:
    operation: LeaseOperation
    terminal_phase: LeaseTransactionPhase
    outcome_code: str
    correlation_id: CorrelationId | None
    tenant_id: TenantId | None
    run_id: RunId | None
    worker_id: WorkerId | None
    claim_id: LeaseClaimId | None
    duration_bucket: str | None
    replayed: bool
    empty: bool
    contended: bool


class WorkerLeaseTelemetry(Protocol):
    def record_log(self, observation: LeaseOperationObservation) -> None: ...

    def record_metric(self, observation: LeaseOperationObservation) -> None: ...

    def record_trace(self, observation: LeaseOperationObservation) -> None: ...


class WorkerLeaseRepository(Protocol):
    async def issue_claim_id(self) -> LeaseClaimId: ...

    async def claim(self, command: ClaimLeaseCommand) -> LeaseClaimOutcome: ...

    async def get_authority(
        self,
        proof: LeaseAuthorityProof,
    ) -> LeaseAuthority: ...

    async def renew(
        self,
        command: RenewLeaseCommand,
    ) -> ConditionalLeaseOutcome: ...

    async def release(
        self,
        command: ReleaseLeaseCommand,
    ) -> ConditionalLeaseOutcome: ...

    async def get_inactive_running(
        self,
        tenant_id: TenantId,
        run_id: RunId,
    ) -> InactiveRunningLease | None: ...

    async def list_inactive_running(
        self,
        tenant_id: TenantId,
        *,
        cursor: InactiveRunningCursor | None = None,
        limit: int = 100,
    ) -> InactiveRunningPage: ...


class LeaseGuardedRunRepository(RunRepository, Protocol):
    async def commit_with_lease(
        self,
        *,
        proof: LeaseAuthorityProof,
        expected_version: int,
        updated_run: Run,
        new_events: tuple[RunEvent, ...],
        receipt: CommandReceipt,
    ) -> CommitOutcome: ...
```

`LeaseClaimId`, `WorkerId`, `LeaseToken`, claim/mutation commands, outcomes, authority
proofs, and cursor/page types are framework-neutral immutable values. `LeaseToken`
prints only a redacted constant; the raw value is available solely to the authorized
coordination caller for pass-back to the repository.

Both PostgreSQL repositories require a `WorkerLeaseTelemetry` dependency for 006
operations. A repository invocation constructs one terminal observation after its
transaction and connection scope is closed, then independently attempts the three
methods. No no-op default may silently disable all three channels. Recording adapters
may bridge these calls to standard structured logging, metrics, or distributed tracing;
006 does not define their exporter SDK or network lifecycle.

## `issue_claim_id`

- Performs the component compatibility check, then asks PostgreSQL 18 for `uuidv7()`.
- Returns a UUIDv7 value whose embedded millisecond timestamp is the immutable issue
  time used by `claim`.
- Does not read/select a Run, create a receipt, create a lease, or grant authority.
- A lost read response has no uncertain write result; the caller may request another
  ID. Once `claim` is attempted, only the saved original ID may resolve that attempt.
- Storage and compatibility failures use the stable repository errors below.

## `claim`

### Input

`ClaimLeaseCommand` contains exactly:

- one authorized `tenant_id`;
- one non-empty bounded `worker_id`;
- one previously issued `LeaseClaimId`;
- normalized `duration_seconds`, default 30 and otherwise integer 10–30.

The intent fingerprint version 1 is deterministic from the complete normalized
Worker/duration intent plus `intent_format_version=1`, using length-prefixed canonical
bytes. `tenant_id` scopes the receipt key and `claim_id` is that scope's command key;
neither is duplicated as mutable intent. Any future field that changes selection or
result semantics requires a new intent format version before a claim ID can use it.
The request does not supply a separate timestamp, token, Run ID, priority, batch size,
or Worker clock.

Local format/type/range failures, including a non-UUIDv7 version/variant, occur before
business access. Future/age classification requires a captured PostgreSQL time and is
classified in this order:

```text
issued more than 60 seconds in future    -> invalid_input
issued 24 hours ago or earlier           -> idempotency_expired
```

The exact valid age interval is `(platform_now - 24h, platform_now + 60s]`.
If the database clock cannot be read, the result is `storage_unavailable`; local time
must not guess `invalid_input` or `idempotency_expired`.

### Result

`LeaseClaimOutcome` is one of:

- `no_work`: normal immutable receipt result, no token/Run/attempt and no error;
- `claimed`: immutable initial `LeaseGrant` containing tenant, Run, Worker, claim ID,
  token, attempt number, initial lease version, acquired/heartbeat/expiry timestamps,
  selected duration, renew-by deadline, and `currently_authoritative`.

The renew-by deadline is captured database time plus duration divided by three,
rounded down to one microsecond. It is guidance, not authority; only the database
expiry check grants authority.

### Selection and atomicity

- At most one Run is selected within the request tenant.
- Only `queued` Runs without a valid current lease are eligible.
- With no contention, order is `(updated_at, run_id)`; current 004 semantics make
  `updated_at` the initial queued time.
- Concurrent consumers use `FOR UPDATE ... SKIP LOCKED` as the fast path, start every
  new claim from the queue head, and recheck eligibility under `Run -> Lease` locks.
- Before a fast-path empty result can become a no-work receipt, the same transaction
  executes one ordered oldest-eligible query without `SKIP LOCKED`, using the configured
  finite lock/statement timeouts. A timeout returns `storage_unavailable`; an acquired
  row is rechecked and claimed when eligible; only a confirmed empty probe is no-work.
- Claim creates/replaces only the lease and one complete claim receipt. It does not
  update Run status/version/budget/usage/result, create events, or touch 004 receipts.

### Idempotency

- Same tenant + claim ID + same normalized intent returns the first immutable
  `claimed` or `no_work` result, including the exact original token for a successful
  result. It never chooses a different Run.
- `currently_authoritative` is recomputed from current Run/Lease facts; exact result
  replay does not falsely claim that an expired/released/replaced grant is current.
- Same tenant + claim ID + different Worker or duration is
  `idempotency_conflict` before queue access and never exposes the original outcome.
- Different tenants may use the same UUID independently.
- At/after the 24-hour boundary, `idempotency_expired` precedes receipt lookup. The ID
  can never become a new claim even if its row has been cleaned.
- Concurrent same-ID attempts may tentatively select different rows, but only the
  complete-receipt winner commits. Every loser rolls back all lease changes and reads
  the winner from a new transaction.

## `get_authority`

`LeaseAuthorityProof` contains tenant, Run, Worker, claim ID, attempt number, and raw
token. It never accepts Worker time. Claim ID, Worker ID, attempt number, or lease
version alone is not a capability; only the complete proof against current facts can
authorize.

The repository locks nothing and uses a captured PostgreSQL time to return:

- `authoritative=True` only when all proof fields/digest match the current row,
  `released_at` is null, expiry is strictly in the future, and Run is
  `queued|running`;
- `authoritative=False` with stable reason `lease_expired` when the matching current
  proof is at/after expiry;
- `authoritative=False` with stable reason `lease_not_current` for missing,
  cross-tenant, released, replaced, wrong Worker/token/claim/attempt, or an ineligible
  Run status.

Every result contains `may_start_new_work`, true exactly when `authoritative=True`.
When the proof matches the stored ownership, the result may return its safe current
lease version and timestamps so an unknown renew/release can converge. If the proof
does not match, no actual owner, token, digest, claim, version, or expiry is disclosed;
only caller-supplied IDs may be echoed. This read is advisory only; callers must not
use it to authorize a later write.

## `renew`

`RenewLeaseCommand` contains an authority proof, positive expected `lease_version`,
and optional duration defaulting to 30 and otherwise 10–30.

The operation locks tenant-scoped `Run -> Lease`, captures one PostgreSQL time, and
advances exactly once only when:

- all authority proof fields/token digest match;
- expected lease version equals the stored current value;
- the lease is unreleased and strictly unexpired;
- Run status remains `queued|running`.

Success keeps token/claim/attempt/acquired time, sets heartbeat to the captured time,
sets expiry to captured time plus duration, increments lease version once, and returns
`applied=True` plus safe current authority and microsecond-floor renew-by guidance.
It never changes the Run. A concurrent successful renewal advances lease version but
does not invalidate the same token for `commit_with_lease()`, whose proof intentionally
does not carry an expected lease version.

An expired lease returns `lease_expired` and is never revived. Missing, cross-tenant,
released/replaced/wrong proof returns `lease_not_current` without owner disclosure.
If the proof still matches but the expected version is stale, the operation returns
`applied=False` plus the safe current authority/version/timestamps and performs no
write. This is the required confirmation path after a lost acknowledgement.

Concurrent calls with one current expected version have at most one version advance.
No renew operation receipt is stored.

## `release`

`ReleaseLeaseCommand` contains an authority proof and positive expected
`lease_version`. It uses the same `Run -> Lease` lock/version/token checks as renewal,
sets `released_at` from captured PostgreSQL time, and increments lease version once.
It never changes Run lifecycle facts.

- Successful release returns `applied=True`, `authoritative=False`.
- Same-token stale-version or repeated release returns `applied=False` and current
  safe confirmation without another version change.
- Matching proof may clean an unreleased lease after the Run enters waiting/terminal,
  even when its time boundary has passed; this narrow cleanup exception grants no
  authority, starts no work, and does not change that Run.
- For a still-`queued|running` Run, an expired proof cannot release or mutate the row.
  Missing, cross-tenant, replaced, or wrong proof also returns the stable safe
  non-authority result.
- No release receipt is stored.

Every non-applied or released outcome has `may_start_new_work=False`. Releasing a
still-`running` lease immediately makes that Run an inactive-running recovery
candidate; release does not itself recover or change the Run.

## Unknown renew/release convergence

After `commit_outcome_unknown`, the caller sets `may_start_new_work=False`, opens a new
connection, and calls `get_authority()` with the same proof:

1. Same token/attempt, unchanged lease version, still-unreleased/unexpired authority,
   and a Run status allowed for that mutation: the original conditional mutation did
   not apply; retrying the exact original condition with the original duration and
   expected version is permitted.
2. Same token/attempt and lease version advanced: return current facts only; do not
   run the old mutation again or compute a new expiry from retry time. This remains
   confirmation-only even if the lease expired before the read.
3. Token/claim/attempt changed or proof no longer matches: old operation is fenced
   permanently.
4. Unchanged version but expiry/release/Run transition now disallows the operation:
   do not retry and keep the safety signal false.
5. A second storage failure remains `storage_unavailable` or
   `commit_outcome_unknown` by phase; it never authorizes work or a blind extension.

This relies on the durable no-ABA invariant: the row is retained, attempt and lease
versions never reset, and authority matches token + claim + attempt together. Tokens
are generated independently and are never deliberately reused; even a random-value
collision cannot reactivate an old proof whose claim/attempt no longer match.

## `get_inactive_running` and `list_inactive_running`

- Both require one tenant; missing and cross-tenant facts both return empty/not found.
- They return only `running` Runs whose
  `authority_ended_at = COALESCE(released_at, lease_expires_at)` is at/before a
  captured database `as_of`.
- Single-Run lookup returns one safe `InactiveRunningLease` or `None`.
- First list page captures `as_of`; subsequent pages use an immutable cursor bound to
  the same tenant, `as_of`, last authority-end time, and last Run ID.
- Sort is `(authority_ended_at, run_id)` ascending. Limit default is 100 and allowed
  range is 1–1,000. Invalid/mismatched cursors fail before a broad query.
- The page fetches at most `limit + 1`, returns a next cursor only when required, and
  never falls back to OFFSET or a cross-tenant scan.
- Results contain only tenant/Run, attempt, lease version, acquired/heartbeat,
  authority-ended time, and `expired|released` reason. They exclude Worker/claim,
  token/digest, and all Run business payload/result/event fields.
- The cursor is an internal immutable value, not a serialized bearer token: it is
  type/range checked and tenant-bound but has no 006 expiry or signing contract.
  Repeating a page with unchanged facts is deterministic. New inactive rows after the
  fixed `as_of` are excluded; a candidate removed by a waiting/terminal transition may
  create a later-page gap but never a duplicate, reverse order, tenant leak, or stale
  row. Any future external API must separately specify authenticated opaque encoding,
  versioning, and TTL before exposing cursors.
- Observation performs no locks that change rows, no lease transfer, no Run update,
  no Checkpoint read, and no recovery decision.

## `commit_with_lease`

### Input relationship

- `updated_run`, every event, the 004 receipt, and authority proof must name the same
  tenant and Run.
- Existing 004 type/range validation and error precedence remain unchanged.
- The lifecycle `intent_fingerprint` retains its 004 meaning. Lease credentials are
  neither added to that fingerprint nor stored in a permanent replay binding.
- The proof does not require an expected lease version, because a concurrent valid
  heartbeat may advance that coordination version without invalidating the token.

### Replay path

Existing-command arbitration is exactly the existing 004 contract and occurs before
Run version/current-lease checks:

- lifecycle fingerprint differs -> existing 004 `idempotency_conflict`;
- lifecycle fingerprint matches -> return the original 004 replay with
  `replayed=True`, even if the lease later expired. No row changes and no execution
  authority is granted.

An ordinary 004 caller or rolling 005 binary may replay the same receipt with the same
result. This is required compatibility: 006 strengthens authorization of a *new*
Worker-produced write, not tenant-scoped read-only replay. Tenant authorization and
command-ID ownership remain the existing trusted application boundary.

### New-write path

In one `READ COMMITTED` transaction:

```text
arbitrate 004 command receipt
lock tenant-scoped Run
lock tenant-scoped Worker Lease
capture PostgreSQL platform time
validate full authority proof, expiry, release, and Run status
validate expected Run version and shared 004 invariants
write Run when changed, zero/one Event, and 004 receipt
COMMIT
```

Any failure leaves Run, Event, 004 receipt, and lease unchanged. The guard is read-only
against the lease; a Worker lifecycle command does not renew or release ownership.
Legal 004 zero-event semantics remain unchanged: a zero-event commit does not consume
a Run version, while its 004 receipt remains replayable with ordinary 004 priority.

This method establishes only the atomic persistence boundary. 006 does not invoke it
from a Worker loop or invent any lifecycle command, Agent step, or external effect.

## Tenant-indistinguishable results

- `claim`: other-tenant Runs are absent from selection and can only contribute to the
  ordinary `no_work` result; same claim UUID in different tenants is independent.
- `get_authority`, `renew`, and `release`: missing and cross-tenant lease/Run/proof
  facts return the same `lease_not_current` shape with no discovered owner, claim,
  version, expiry, or token metadata.
- inactive single/list observation: missing/cross-tenant facts return `None`/empty;
  a cursor whose embedded tenant differs from the explicit tenant is local
  `invalid_input` and causes no fallback query.
- `commit_with_lease`: Run/receipt arbitration remains tenant-keyed and uses the
  existing 004 not-found/conflict/replay shapes; a fact in another tenant is never
  reported as the reason.

Public results may echo validated identifiers supplied by the caller when useful for
correlation. They never echo an identifier, owner, version, timestamp, or existence
fact discovered only from another tenant or a mismatching current lease.

## Stable error contract

Lease semantic results use safe constant codes:

| Code | Meaning |
|---|---|
| `invalid_input` | Public type/range/identifier/token/cursor/UUID format or future-time rule failed; no business access occurs where the rule is locally decidable. |
| `idempotency_conflict` | Tenant-scoped claim or lifecycle command identity was reused with a different normalized intent. |
| `idempotency_expired` | Claim UUID issue time is at least 24 hours old; no receipt or queue access follows. |
| `lease_not_current` | Proof is missing, cross-tenant, released, replaced, wrong, ineligible by Run status, or otherwise does not grant current authority. |
| `lease_expired` | The otherwise matching current proof reached its authoritative database expiry boundary. |

`no_work` and a stale-version conditional confirmation are ordinary typed outcomes,
not storage errors.

The existing repository failure family is reused:

| Code | Meaning |
|---|---|
| `storage_unavailable` | Read unavailable or write is known not to have committed. Caller may use finite external retry/backoff while retaining the original identity. |
| `commit_outcome_unknown` | COMMIT may have completed but acknowledgement was lost. Resolve only with the operation-specific protocol above. |
| `data_corruption` | Compatible tables contain lease/receipt projections or relationships that cannot be reconstructed safely. |
| `schema_incompatible` | Database is reachable but the `worker_lease_kernel` contract is missing/unsupported/partial. No automatic migration occurs. |

Failure classification remains phase plus SQLSTATE based. A UUID's local syntax and
version may be rejected before database access, but future/age classification requires
database time; inability to obtain that time is `storage_unavailable`. SQLSTATE `08007`/`40003`
or a connection loss during COMMIT acknowledgement is unknown. Confirmed rollback,
pre-commit disconnect, finite lock/statement timeout, deadlock, or capacity failure is
unavailable only when non-commit is known. The repository does not auto-retry.

## Transaction and lock contract

- One operation owns one connection and explicit short transaction.
- Isolation is explicitly `READ COMMITTED`; `synchronous_commit=on`.
- 006 lock timeout is construction-validated in 1–5,000 ms; statement timeout is
  1–10,000 ms and no smaller than lock timeout. Both default to 5,000 ms.
- Standard resource order is 004 receipt arbiter (ordinary/guarded lifecycle commit,
  if any) -> tenant-scoped Run -> tenant-scoped Lease -> Event/index writes. Ordinary
  004 commit stops after Run/Event. Claim deliberately inserts its complete claim
  receipt after Run/Lease work because no pre-existing receipt can be reserved without
  a forbidden pending state; a unique loser rolls back the whole transaction before
  replay. Renew/release use Run -> Lease. No path reverses Run/Lease order.
- Claim builds and inserts only a complete receipt; a same-ID unique loser rolls back
  its entire Run/Lease transaction before replaying the winner.
- `SKIP LOCKED` is only the claim fast path. Before recording no-work, one ordered
  blocking head probe runs under the same finite timeouts; a persistent row lock is
  `storage_unavailable`, not an empty queue.
- Every mutating or guarded authority/expiry decision captures PostgreSQL wall-clock
  time after acquiring the target Run/Lease locks and binds that value through the
  remaining statements. Read-only authority/observation captures one database time
  without row locks.
- No model, Tool, network, secret resolution, telemetry channel, logging export, or
  other external call occurs while a transaction, connection scope, or lock is active.
- No transaction is automatically retried by the adapter.

## Schema compatibility and migration contract

- Alembic migration `0002` owns the two 006 tables/indexes and compatibility row.
- Application/Worker construction performs only component-aware read-only
  compatibility checks; it never invokes Alembic or `metadata.create_all()`.
- 0002 is expand-compatible with 005 binaries and leaves `run_repository=1`.
- Downgrade to 0001 destroys only lease/claim facts and is disposable-only.
  Production rollback retains the additive schema or restores a verified backup to a
  fresh database before controlled cutover. A partially applied/malformed 0002 fails
  `schema_incompatible` before business reads. A restored database must be quarantined
  until its database clock is verified at/after every restored lease expiry and old
  Worker connections are drained; restored rows never authorize resuming old work.

## Observability and redaction contract

Each public 006 repository invocation emits positive terminal telemetry. This includes
`issue_claim_id`, claim, authority read, renew, release, inactive single/list reads,
and `commit_with_lease`; ordinary 005 `commit()` is unchanged. Once the repository
result or stable error has been determined and database cleanup has completed, it
creates exactly one immutable `LeaseOperationObservation`, then:

1. calls `record_log` once;
2. calls `record_metric` once;
3. calls `record_trace` once.

The three calls are isolated. If any call raises, the remaining calls are still
attempted, the determined business result/error is returned unchanged, and no database
operation is retried. Recording test doubles must observe no invocation while the
transaction/connection is active. 006 provides the framework-neutral port and
operation facts only; it does not implement OpenTelemetry/Langfuse exporters, network
batching, sampling, alerts, or deployment wiring.

Raw token bytes are returned only in an authorized successful claim or exact
same-tenant/same-intent replay and accepted only as an incoming proof. The ordinary
lease/observation projections never select the receipt's replay column. Token digest
and claim fingerprint are internal secret-adjacent comparison material: neither is a
public/log/metric/cursor field. Safe coordination metadata is limited to the bounded
fields below.

006 does not create a separate column-privileged database role, encrypted column, KMS,
or cleanup job. A database administrator, diagnostic tool with unrestricted SQL, or
backup reader can recover raw replay bytes, and dumps inherit that sensitivity. This
is an explicit M0 preproduction/disposable-data acceptance only; it blocks production
enablement until a later approved retention/encryption design. Behavioral replay ends
at 24 hours regardless of physical copies, and any replay after lease expiry returns
`currently_authoritative=False`.

Allowed bounded fields are operation, transaction phase, stable outcome/error code,
correlation ID, caller-supplied tenant/Run/Worker/claim IDs where policy permits,
duration, contention/empty/replay flags, attempt number, lease version, and timing
bucket. Never include:

- raw token or token digest;
- claim intent fingerprint;
- SQL, bound parameters, DSN, username, password, host, or database driver message;
- an owner identity discovered only from a cross-tenant/conflicting row;
- Run input/output, result, Event payload, final answer, prompt/context, or hidden
  reasoning.

Value/error `str` and `repr` are part of the redaction tests. Expected empty/conflict/
stale outcomes are not logged as stack traces.

## Acceptance contract

Implementation must prove, using PostgreSQL 18.6 and independent connections:

1. 100 one-Run groups x 20 Workers produce exactly one current lease and no Run
   lifecycle change.
2. 100 queued Runs drain through 20 clients with no duplicate/omission; a separate
   uncontended run is strict FIFO, and bounded conforming contention leaves no eligible
   older row behind. A single externally locked final head returns
   `storage_unavailable` through the bounded blocking probe and never persists no-work.
3. Claim UUID boundary, 100-way same-ID replay/conflict, token uniqueness/redaction,
   and no-work immutability.
4. 100 cycles and 100 same-version races for renew/release, including lost-ack
   convergence and no ABA.
5. Guarded commit versus expiry, replacement, renew, release, cancellation, and
   terminal transition with no partial fact combination.
6. Engine/process recreation before and after expiry without implicit release or
   extension.
7. Cross-tenant negative matrices for every operation and cursor.
8. 1,000 mixed naturally expired/released inactive-running candidates through single
   and keyset reads: static data has no gap/duplicate/reverse order/noncandidate/leak;
   concurrent transition data obeys fixed-`as_of` and allowed-removal-gap semantics.
9. Deterministic pre-commit, confirmed-rollback, and real-commit/lost-ack windows for
   claim, renew, release, and guarded commit, each repeated as required by SC-005.
10. Expand/partial compatibility, disposable downgrade/re-upgrade, and sensitive
    fresh backup restore while preserving 005 facts and proving restored leases cannot
    resume authority.
11. At 10,000 queued Runs, 20 clients, 100 warmups and 1,000 measured samples per
    full issue-ID-plus-claim/renew/authority/release operation, nearest-rank p95 below
    200 ms without weakening durability, constraints, tenant predicates, or fencing.
12. Zero sensitive sentinel occurrences in public errors, logs, metrics/traces test
    doubles, SQL diagnostics, and printable values.
13. Every public 006 operation and required outcome emits exactly one terminal record
    to each recording log/metric/trace channel after cleanup; channel exceptions cause
    zero business-result changes, database retries, partial writes, or skipped remaining
    channels.
14. Zero production Worker loop, Agent/LangGraph/Tool execution, Checkpoint,
    Reconciler, automatic recovery, public API/SDK, cleanup, deployment, or capacity
    behavior; raw-token retention and NTP remain explicit production-enable blockers.
