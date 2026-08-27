# Research: Worker Lease Kernel

**Feature**: `006-worker-lease-kernel`

**Date**: 2026-08-26

**Scope**: PostgreSQL-backed coordination only. LangGraph, Checkpoint, Agent/model/
Tool/Graph execution, a Worker polling loop, Reconciler, REST/SSE, and SDK behavior
remain excluded.

## Decision 1: Keep lease coordination behind stronger, separate ports

**Decision**

- Keep the existing 004 `RunRepository` and `MemoryRunRepository` contracts
  unchanged.
- Add a framework-neutral `WorkerLeaseRepository` for claim-ID issuance, claim,
  authority read, renew, release, and inactive-running observation.
- Add `LeaseGuardedRunRepository`, a stronger port with `commit_with_lease(...)`.
  `PostgreSQLRunRepository` implements it by reusing a same-connection internal
  commit helper; future Worker-owned writes depend on this stronger port.
- Do not put an optional lease argument on `RunRepository.commit()`. An optional
  guard would make an unsafe unguarded call too easy.

**Rationale**

The application/domain boundary stays free of SQLAlchemy and PostgreSQL types, the
004 memory contract does not acquire scheduling responsibilities, and fencing is a
compile-time-visible capability of the selected port rather than caller discipline.

**Alternatives considered**

- Preflight `verify_lease()` followed by ordinary `commit()`: rejected because the
  token can expire or be replaced between transactions.
- Add all lease methods to `RunRepository`: rejected because it couples lifecycle
  persistence to high-frequency coordination.
- Require leases for every 004 command: rejected because cancellation and other
  non-Worker lifecycle commands must retain their existing authority boundary.

## Decision 2: Use two separate persistent fact sets

**Decision**

- `worker_leases` keeps one mutable current coordination row per tenant/Run.
- `worker_lease_claim_receipts` keeps the immutable successful or no-work claim
  result until the claim identifier's 24-hour replay deadline.
- Lease rows are retained after release/expiry and overwritten by the next ownership
  so `attempt_no` and `lease_version` never exhibit ABA. Claim receipt cleanup is not
  part of correctness and no cleanup Worker/Reconciler is introduced in 006.

**Rationale**

The Run table remains the lifecycle fact source, the lease row proves at most one
current coordination state, and high-frequency renewals do not create unbounded
receipts. A new Worker-produced lifecycle write is authorized under current Run/Lease
locks; an already committed 004 command is a read-only replay under the unchanged 004
receipt contract and grants no new execution authority.

Checklist review rejected a permanent command-guard table. Rolling 005 binaries and
ordinary `RunRepository.commit()` cannot enforce or preserve a second replay binding,
so adding one would make the same 004 command replay differently by caller version and
violate FR-027. The stronger port therefore strengthens only the new-write path.

**Alternatives considered**

- Lease columns on `runs`: rejected because heartbeat writes would couple and churn
  the lifecycle aggregate.
- Append-only row per lease attempt plus a partial unique index: rejected because
  current-authority locking and cleanup become more complex without a 006 audit need.
- Store a permanent guarded-command binding: rejected because it cannot be enforced by
  unchanged 004 callers or rolling 005 binaries and would split a single command's
  idempotency semantics. Tenant authorization and command-ID ownership remain the
  existing trusted application boundary.
- Transaction advisory locks: rejected because correctness would depend on every
  writer following an application convention rather than durable constraints.
  PostgreSQL describes advisory locks as application-defined locks, not stored
  ownership facts: [Explicit Locking](https://www.postgresql.org/docs/18/explicit-locking.html).

## Decision 3: PostgreSQL issues RFC 9562 UUIDv7 claim identifiers

**Decision**

`issue_claim_id()` executes PostgreSQL 18 `uuidv7()` before a claim request. The
caller must retain that ID before entering the write whose commit acknowledgement may
be lost. The claim transaction validates `uuid_extract_version()` and derives the
immutable issuance time with `uuid_extract_timestamp()`; Python 3.12 transports the
value as `uuid.UUID` and does not implement UUIDv7 generation.

Age classification uses a captured PostgreSQL wall-clock value and these half-open
boundaries:

```text
version is not 7                         -> invalid_input
issued_at > platform_now + 60 seconds   -> invalid_input
issued_at + 24 hours <= platform_now    -> idempotency_expired
otherwise                               -> eligible for receipt replay/new claim
```

Exactly future `+60s` is accepted; exactly age `24h` is expired. Expiration is
derived from the identifier even if its receipt has already been removed, so cleanup
cannot turn an old ID into a new command. UUID type/version/variant is locally
decidable; future/age is not. If PostgreSQL time is unavailable, classification is
`storage_unavailable`, never a Worker-clock guess.

Claim intent format 1 hashes length-prefixed Worker ID, normalized/default-expanded
duration, and the explicit format version. Tenant scopes the receipt key and claim ID
is the key within that scope. A future result-affecting field requires a new intent
format version before any claim ID can use the changed shape.

**Rationale**

UUIDv7 embeds Unix-millisecond time in a standard UUID, PostgreSQL 18 supplies native
generation/extraction, and Psycopg adapts `uuid.UUID` directly. Python 3.12 does not
provide `uuid.uuid7()`, so a hand-written generator would add clock rollback, bit
layout, randomness, and test-vector risks. UUIDv7 is an idempotency identifier, never
an authority credential.

A trusted caller can syntactically construct a valid UUIDv7 in the accepted time
window. This does not bypass queue eligibility, tenant predicates, lease mutual
exclusion, or fencing; it only creates a possible idempotency key. Authentication of a
compromised/untrusted caller is outside 006's internal port boundary.

Sources:

- [RFC 9562 UUIDv7 and security considerations](https://www.rfc-editor.org/rfc/rfc9562.html)
- [PostgreSQL 18 UUID functions](https://www.postgresql.org/docs/18/functions-uuid.html)
- [Python 3.12 uuid](https://docs.python.org/3.12/library/uuid.html)
- [Psycopg UUID adaptation](https://www.psycopg.org/psycopg3/docs/basic/adapt.html#uuid-adaptation)

**Alternatives considered**

- UUIDv4 plus request-supplied `issued_at`: rejected because the timestamp can be
  changed after receipt deletion.
- A custom timestamp/UUID structure: rejected in favor of PostgreSQL's native,
  indexed standard type.
- Persist every issued ID before claim: rejected because it adds an unnecessary
  durable write for every empty poll.

## Decision 4: Use random 256-bit fencing tokens and restricted replay storage

**Decision**

- A framework-neutral `LeaseTokenGenerator` port produces 32 random bytes; the
  infrastructure implementation uses Python `secrets.token_bytes(32)`.
- The current lease row stores only `SHA-256(token)`.
- A successful claim receipt stores the original token bytes in one restricted
  replay-only column so a lost first response can be replayed exactly. That column is
  never selected by ordinary authority/observation projections and is removed when
  the 24-hour receipt is cleaned.
- `LeaseToken` has redacted `str`/`repr`. The engine keeps `hide_parameters=True`;
  exceptions, SQL diagnostics, logging, metrics, tracing, cursor values, and test
  snapshots never include the token. Application comparison uses
  `hmac.compare_digest()` after loading the digest under the Run/Lease locks.
- The physical retention maximum is intentionally *not* claimed by 006: no cleanup
  loop exists, so raw replay bytes may remain past behavioral expiry and in backups.
  This is accepted only for disposable/local/CI and current M0 preproduction data;
  production enablement is blocked until a later Spec defines deletion SLO or
  encryption plus versioned key rotation.

**Rationale**

An irreversible digest alone cannot reproduce the original response after a
commit-acknowledgement loss. The repository has no approved KMS/envelope-encryption
facility, while a deterministic HMAC token would introduce a long-lived shared key
and rotation contract that must retain every key for at least `24h + 60s`. A random
token limits derivation risk and its execution authority lasts at most 30 seconds;
the replay copy is nevertheless treated as sensitive database material.

Source: [Python `secrets`](https://docs.python.org/3.12/library/secrets.html) and
[`hmac.compare_digest`](https://docs.python.org/3.12/library/hmac.html#hmac.compare_digest).

**Alternatives considered**

- Store only a digest: rejected because exact replay would be impossible.
- Deterministic HMAC token: viable only with a versioned secret-key ring and explicit
  key-retention/rotation operations; deferred until that infrastructure is approved.
- Add an AEAD/KMS dependency now: rejected as an unapproved external capability and
  unnecessary dependency for this internal 10–30 second credential.

## Decision 5: Use short `READ COMMITTED` transactions and a fixed lock order

**Decision**

Reuse the 005 async SQLAlchemy Core/Psycopg stack, explicit `READ COMMITTED`
transactions, `synchronous_commit=on`, finite `lock_timeout`, and finite
`statement_timeout`. Connections/transactions are never shared by concurrent tasks.
No external, model, Tool, secret-provider, or network call occurs while database
locks are held. 006 validates lock timeout in 1–5,000 ms and statement timeout in
1–10,000 ms with statement timeout no smaller than lock timeout; both default to the
existing 5,000 ms.

The cross-operation lock order is:

```text
004 command receipt arbiter, when present
-> tenant-scoped Run row
-> tenant-scoped Worker Lease row
-> Event/index writes
```

Claim has no pre-existing complete receipt to arbitrate. It first reads a possible
receipt, then locks `Run -> Lease`, builds the full outcome, and performs
`INSERT ... ON CONFLICT DO NOTHING RETURNING` for the complete receipt. Losing the
claim-ID unique race rolls back every tentative lease change and reads the winner in
a new transaction/snapshot. A committed `pending` receipt is never representable.

For every authorization decision, capture a fresh `clock_timestamp()` value after
the relevant Run/Lease locks and bind that one value into all comparisons and updates
for that decision. Do not use Worker time or transaction-start `CURRENT_TIMESTAMP`;
the latter may be stale after lock waits. PostgreSQL documents the time-function
differences here: [Current Date/Time](https://www.postgresql.org/docs/18/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT).

**Rationale**

Row locks last until transaction end, so fixed ordering makes token/status/version
validation atomic with the write. `READ COMMITTED` supplies a new statement snapshot
after a concurrent receipt arbiter commits. PostgreSQL recommends consistent lock
ordering as the primary deadlock defense:
[Explicit Locking](https://www.postgresql.org/docs/18/explicit-locking.html) and
[Read Committed](https://www.postgresql.org/docs/18/transaction-iso.html#XACT-READ-COMMITTED).

**Alternatives considered**

- `SERIALIZABLE`: rejected because it adds whole-transaction retries without
  replacing the token/version/row-lock invariants.
- Repository automatic retry: rejected because it cannot safely decide an unknown
  commit outcome.
- `Lease -> Run` order: rejected because guarded Run commits use `Run -> Lease` and
  the reverse order creates a deadlock cycle.

## Decision 6: Claim with ordered `SKIP LOCKED`, bounded fairness assumptions

**Decision**

Each new claim re-queries from the tenant queue head and selects at most one eligible
row ordered by `(updated_at, run_id)`, using `FOR UPDATE OF runs SKIP LOCKED`. The 004
state machine creates `queued` Runs once and never returns another status to `queued`,
so `updated_at` is the current enqueue time. A later requeue feature must add an
explicit `queued_at` projection before changing this assumption.

The eligibility query checks `run_status='queued'` and no unexpired, unreleased lease.
After locking the Run, it locks/rechecks the lease using a newly captured database
time. The existing `(tenant_id, run_status, updated_at, run_id)` index serves the
FIFO query; no duplicate queue index is added.

`SKIP LOCKED` provides high-throughput queue consumption and an intentionally
inconsistent view; PostgreSQL does not promise strict starvation freedom, and a skipped
row does not wait long enough to trigger `lock_timeout`. It is therefore only the fast
path. If the fast query finds no row, the same transaction performs one ordered
oldest-eligible query without `SKIP LOCKED`, under the configured lock and statement
timeouts. A persistent external lock is then `storage_unavailable`; a released row is
locked and rechecked; only a confirmed empty probe becomes a no-work receipt.

This preserves throughput while later rows exist, prevents a locked final queue head
from being mistaken for an empty queue, and makes the FR-004 external-lock boundary
testable. All platform lease transactions remain short and timeout-bounded, and every
new claim starts from the head.

Sources:

- [PostgreSQL SELECT locking clause](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE)
- [Indexes and ORDER BY](https://www.postgresql.org/docs/18/indexes-ordering.html)
- [SQLAlchemy `with_for_update`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update)

**Alternatives considered**

- Blocking exclusively on the head row for every claim: rejected because one slow
  claimant stalls otherwise independent work; the chosen probe blocks only before a
  no-work decision.
- An advancing cursor: rejected because a once-skipped older row could be forgotten.
- Strict priority, delayed work, batching, or cross-tenant scheduling: outside 006.

## Decision 7: Make renew/release conditional and monotonic without receipts

**Decision**

`attempt_no` starts at 1 and increments exactly once for every newly established
ownership. `lease_version` starts at 1 and increments exactly once for every
successful new ownership, renewal, or release; it never resets while the per-Run row
exists. Token changes on every ownership and never on renewal.

Renew locks `Run -> Lease`, captures database time, and applies only when tenant,
Run, Worker, token digest, expected `lease_version`, unreleased state, unexpired
boundary (`expires_at > platform_now`), and Run status `queued|running` all match.
It updates heartbeat/expiry from the captured time and returns a renew-by deadline no
later than one third of the selected duration, rounded down to one microsecond. Release
uses the same lock/version guard, immediately revokes authority, and increments once;
a released still-`running` row becomes an inactive recovery candidate. Matching
cleanup after a Run enters waiting/terminal is allowed without changing the Run.

After an unknown renew/release commit:

- unchanged token/attempt/version proves the original conditional mutation did not
  take effect and permits the same condition to be retried only if release, expiry,
  and current Run status still allow the operation;
- an advanced version confirms current facts only and must never be used with the old
  expected version to extend from a new time, even if it expired before confirmation;
- a changed token/attempt permanently fences the old operation;
- a second storage failure leaves `may_start_new_work=false` and never permits a blind
  extension.

The retained row, never-reset counters, and complete token + claim + attempt proof are
the no-ABA invariant that makes this confirmation safe. Tokens are independently
random and never deliberately reused; a value collision cannot reactivate an old
proof with a different claim/attempt. Renewal/release do not create operation receipts.

**Alternatives considered**

- Heartbeat command receipts: rejected because they grow without bound.
- Retry from the caller's new current time without reading state: rejected because a
  successful unknown renewal could be extended twice.
- Reset `lease_version` for each attempt: rejected because an old version could look
  current after replacement unless every check also remained perfect.

## Decision 8: Preserve 004 replay; guard only new lifecycle writes

**Decision**

`commit_with_lease()` first arbitrates the existing 004 command receipt. An existing
matching lifecycle intent returns the original replay even if the lease has since
expired. This path performs no write, grants no new execution authority, and behaves
the same through ordinary 004 callers and rolling 005 binaries.

For a new command, the transaction locks `Run -> Lease`, captures database time,
validates current authority and the expected Run version, executes the shared 004
invariant validator, and writes Run, zero/one Event, and 004 CommandReceipt atomically.

**Rationale**

Unknown outcomes are confirmable through the established 004 receipt after the short
lease expires, while an absent receipt still requires current authority at the new
write's commit boundary. A separate preflight authority read satisfies neither rule.

## Decision 9: Observe inactive running work with fixed-as-of keyset pages

**Decision**

The first page captures `as_of` from PostgreSQL. An internal immutable cursor binds
`tenant_id`, `as_of`, `last_authority_ended_at`, and `last_run_id`. Subsequent pages use
the same `as_of` and the keyset predicate:

```text
(authority_ended_at, run_id) > (last_authority_ended_at, last_run_id)
```

Queries require one tenant, `run_status='running'`, and
`authority_ended_at = COALESCE(released_at, lease_expires_at) <= as_of`, order by
`(authority_ended_at, run_id)`, and fetch at most `limit + 1` for limits 1–1,000. The
projection excludes Worker/claim/token/digest. A terminal or waiting transition may
remove a candidate between pages; this can create an allowed gap but not a duplicate,
reverse order, or cross-tenant result. New inactive rows after fixed `as_of` do not
enter later pages. Static acceptance data requires zero gaps. The internal typed cursor
has no serialization/signature/expiry contract; a future external API must add an
authenticated opaque form and TTL. No long repeatable-read transaction, reclaim,
state change, or Checkpoint inspection is performed.

Source: [PostgreSQL row comparisons](https://www.postgresql.org/docs/18/functions-comparisons.html#ROW-WISE-COMPARISON).

**Alternatives considered**

- `OFFSET`: rejected because concurrent removals cause duplicates/gaps and deep
  pages scan discarded rows.
- Recompute time per page: rejected because newly expired rows can enter behind the
  cursor.
- Hold one database snapshot across all pages: rejected because it creates a long
  transaction and connection lifetime.

## Decision 10: Add one expand-only migration and component-aware compatibility

**Decision**

Add `0002_create_worker_lease_kernel.py`; do not modify released revision `0001`.
The migration creates the two 006 tables/indexes and inserts
`worker_lease_kernel=1` while leaving `run_repository=1`. The compatibility checker
becomes component-aware and caches by `(engine, component)`, retaining the existing
RunRepository wrapper. A 005 binary can operate on the expanded schema; 006 fails
closed against a database at only 0001 before reading business facts.

Downgrade from 0002 to 0001 deletes only lease/claim facts and the 006
compatibility row; it preserves all Run/Event/004 receipt data but is still destructive
to active ownership and is allowed only in a disposable environment. A production
rollback leaves the additive 0002 schema in place and deploys the prior compatible
application; data-preserving structural rollback requires backup/restore into a fresh
database, drain of old Worker connections, verification that restored database time is
at/after every restored lease expiry, and controlled cutover. Restored rows never
authorize resuming old Worker execution.

**Rationale**

This continues 005's separation of Alembic migration position and application
contract compatibility, supports migrate-first rolling deployment, and prevents
application startup DDL.

**Alternatives considered**

- Bump `run_repository` compatibility: rejected because the 004 persistence contract
  did not change.
- Edit migration 0001: rejected because released migration history is immutable.
- Auto-migrate on startup: prohibited by the constitution and FR-024.

## Decision 11: Reuse the current dependency and PostgreSQL test stack

**Decision**

No third-party dependency is added. Reuse Python 3.12, SQLAlchemy 2.0.52 Core async,
Alembic 1.19.1, Psycopg 3.3.4, PostgreSQL 18.6, pytest/pytest-asyncio, the existing
digest-pinned Compose service, and the `postgresql` test marker. Standard-library
`uuid`, `secrets`, `hashlib`, `hmac`, and base64 utilities cover the new value types.

The real-database lane adds contract, restart, concurrency, fault-window, migration,
tenant-isolation, inactive-query, redaction, and performance coverage. Every concurrent
actor uses an independent engine connection. The existing fast/PostgreSQL collection
partition must explicitly include all new modules. Test cleanup explicitly truncates
claim receipts because no-work receipts have no Run foreign key and
will not disappear through `TRUNCATE runs ... CASCADE`.

**Rationale**

The approved 005 stack already exposes PostgreSQL SQLSTATE and transaction phases and
has deterministic real-commit/lost-ack injection. A second driver, ORM, testcontainer,
distributed lock service, or crypto dependency adds no required capability.

## Decision 12: Preserve 005 transaction failure classification

**Decision**

- Failure before commit invocation or with confirmed rollback/non-commit is
  `storage_unavailable`.
- COMMIT acknowledgement loss, unknown transaction state, SQLSTATE `08007`, or
  SQLSTATE `40003` is `commit_outcome_unknown`.
- `40P01`, `55P03`, `57014`, connection-pool exhaustion, or statement/lock timeout is
  `storage_unavailable` only when rollback is known.
- `data_corruption` covers persisted lease/receipt relationships that cannot be
  reconstructed without guessing; `schema_incompatible` covers a missing/unsupported
  006 contract.
- Do not parse localized database messages and do not retry writes inside either
  repository.

Resolution is operation-specific: claim uses the original claim ID and intent;
renew/release read current token/attempt/version before any same-condition retry;
guarded lifecycle commit uses the original 004 command identity and lifecycle intent;
an absent receipt must still pass the current lease guard.

Sources:

- [PostgreSQL error codes](https://www.postgresql.org/docs/18/errcodes-appendix.html)
- [SQLAlchemy Core exceptions](https://docs.sqlalchemy.org/en/20/core/exceptions.html)
- [SQLAlchemy pool disconnect handling](https://docs.sqlalchemy.org/en/20/core/pooling.html#disconnect-handling-pessimistic)

## Decision 13: Emit framework-neutral terminal telemetry after database cleanup

**Decision**

Add immutable `LeaseOperationObservation` and required `WorkerLeaseTelemetry`
application-port contracts. Every public 006 repository call creates one terminal
observation after its transaction and connection scope has closed, then independently
calls log, metric, and trace channels exactly once. Channel exceptions are isolated:
all remaining channels are attempted, the determined repository result is preserved,
and no database statement is retried.

The observation uses an allowlist of stable operation, phase, outcome, caller-supplied
safe identifiers, bounded duration bucket, and replay/empty/contention flags. It never
contains token/digest/fingerprint, SQL/parameters/DSN, discovered cross-tenant identity,
Run payload/result, answer, or hidden reasoning. 006 adds no OpenTelemetry, Langfuse,
background exporter, network queue, sampling, alerting, or deployment dependency.

**Rationale**

The constitution requires positive structured logs, metrics, and traces for attempts
and transitions. A negative-only redaction test can pass vacuously when nothing is
emitted. A required framework-neutral port makes positive emission and failure
isolation testable while preserving inward dependencies and keeping telemetry outside
transaction correctness.

**Alternatives considered**

- Test only that logs contain no secrets: rejected because zero emission passes.
- Add OpenTelemetry or Langfuse in 006: rejected because exporter lifecycle and
  deployment belong to later Runtime observability work and are not needed for the
  lease contract.
- Emit while holding locks to capture exact phase: rejected because an exporter stall
  would extend authority transactions and violate the no-external-call-under-lock rule.
- Provide a silent no-op default: rejected because construction could claim
  observability while disabling all required channels.
