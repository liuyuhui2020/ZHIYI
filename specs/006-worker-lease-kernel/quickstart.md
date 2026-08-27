# Quickstart: Worker Lease Kernel Acceptance

**Feature**: `006-worker-lease-kernel`

**Purpose**: Reproduce the local schema, claim, fencing, concurrency, failure,
redaction, observation, and performance acceptance after 006 implementation.

This quickstart uses a disposable PostgreSQL instance. It does not start a Worker
loop, run LangGraph, read a Checkpoint, call a model/Tool, recover a `running` Run, or
touch production data.

## Prerequisites

- Python 3.12 and `uv`
- Docker with Compose v2
- Local port required by `compose.test.yaml`
- No shared/test/staging/production URL in `ZHIYI_TEST_DATABASE_URL`

The Compose credentials are public disposable test values. Never reuse them or point
these destructive migration commands at a non-disposable database.

## 1. Start and identify the disposable database

```bash
docker compose -f compose.test.yaml up -d --wait postgresql
export ZHIYI_TEST_DATABASE_URL='postgresql+psycopg://zhiyi_test:zhiyi_test_password@127.0.0.1:55432/zhiyi_test'
docker compose -f compose.test.yaml ps
```

Before any downgrade, restore, direct corruption, backend termination, or volume
deletion, verify all of: Compose project/service, localhost port `55432`, database
`zhiyi_test`, and role `zhiyi_test`. Stop on any mismatch.

## 2. Install the frozen environment

```bash
uv sync --all-groups --frozen --python 3.12
```

006 adds no third-party dependency. The expected persistence stack remains SQLAlchemy
2.0.52, Alembic 1.19.1, Psycopg 3.3.4, and PostgreSQL 18.6 from the lockfile and
digest-pinned Compose configuration.

## 3. Apply and verify migration 0002 explicitly

```bash
uv run alembic upgrade head
uv run alembic current --check-heads
uv run pytest -m postgresql tests/integration/persistence/test_worker_lease_migrations.py
```

Expected evidence:

- 005 `run_repository` compatibility remains version 1;
- 006 `worker_lease_kernel` compatibility is version 1;
- both 006 tables, named constraints, and declared indexes exist;
- constructing either repository performs no DDL;
- a database at only revision 0001 remains valid for a 005 repository but fails
  closed as `schema_incompatible` for the lease repository;
- a missing compatibility row or deliberately partial/malformed 0002 also fails before
  any business-fact read and is never repaired by application startup;
- component compatibility caching cannot confuse the two checks.

Application/Worker startup must never run Alembic, `create_all()`, downgrade, repair,
or receipt cleanup.

## 4. Run fast and real-PostgreSQL lanes

```bash
uv run pytest -m "not online and not postgresql"
uv run pytest -m postgresql
```

Every real-database 006 module carries the registered module-level `postgresql`
marker. CI collection checks must prove these modules contribute zero nodes to the
fast lane and nonzero nodes to the PostgreSQL lane:

- `tests/contract/persistence/test_postgresql_worker_lease_repository_contract.py`
- `tests/integration/persistence/test_postgresql_worker_lease_claim.py`
- `tests/integration/persistence/test_postgresql_worker_lease_concurrency.py`
- `tests/integration/persistence/test_postgresql_worker_lease_guard.py`
- `tests/integration/persistence/test_postgresql_worker_lease_expiry.py`
- `tests/integration/persistence/test_postgresql_worker_lease_faults.py`
- `tests/integration/persistence/test_postgresql_worker_lease_restart.py`
- `tests/integration/persistence/test_postgresql_worker_lease_tenant_isolation.py`
- `tests/integration/persistence/test_worker_lease_migrations.py`
- `tests/performance/test_postgresql_worker_lease_kernel.py`

No skipped PostgreSQL contract/concurrency/fault/migration test counts as acceptance.
Tests use independent connections per concurrent actor and apply migrations rather
than `metadata.create_all()`.

## 5. Verify claim identity, exact replay, and fencing

```bash
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_claim.py
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_guard.py
```

Expected evidence includes:

- PostgreSQL-issued UUIDv7 round trip and RFC timestamp extraction;
- exact `+60s`, `+60s+1ms`, `24h-1ms`, `24h`, and `24h+1ms` classification using a
  deterministic captured database-time harness;
- same claim/same intent exact replay of claimed and no-work results, including the
  initial token, without selecting another Run;
- same claim/different Worker or duration conflict before queue access;
- expired claim IDs remain expired before and after receipt cleanup;
- claim/renew/release alone produce zero Run/Event/004 receipt changes;
- guarded Run writes atomically validate current token and Run version, while an exact
  prior 004 command replay follows the unchanged ordinary 004 priority after expiry
  and grants no new execution authority;
- stale/replaced/wrong/cross-tenant token success count is zero and all token/repr/log
  sentinels remain absent.

## 6. Run contention, restart, and inactive observation acceptance

```bash
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_concurrency.py
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_restart.py
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_expiry.py
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_tenant_isolation.py
```

Record exact counts for:

- 100 independent single-Run groups with 20 Worker identities each: one current
  lease per group, zero lifecycle changes;
- 100 queued Runs consumed by 20 clients: zero duplicates/omissions, and separate
  no-contention FIFO mismatch count zero;
- 100 concurrent same-claim replays and 100 same-current-version renew/release groups:
  one claim result or one conditional version advance per group;
- at least 100 complete ownership cycles: attempt and lease version never reset,
  token never repeats, stale token never regains authority;
- destroy/rebuild repository engines before expiry and after expiry: no implicit
  release/extension, and queued work becomes reclaimable only after expiry;
- 1,000 naturally expired or deliberately released `running` candidates mixed with
  still-authoritative, non-running, and other-tenant rows: on a static candidate set,
  keyset pages and single reads have zero gaps, duplicates, reverse order, noncandidate
  rows, cross-tenant leaks, or writes; a separate mutation case proves fixed-`as_of`
  exclusion and allowed removal gaps without duplicates or leakage.

The fairness drain uses platform-owned bounded transactions and finite lock/statement
timeouts. `SKIP LOCKED` remains the throughput fast path, but before a claim records
no-work it performs one ordered blocking head probe. A separate single-head test holds
that Run from an independent nonconforming connection and proves the probe returns
`storage_unavailable` on timeout, persists no no-work receipt, and succeeds or confirms
empty only after the external lock is released. PostgreSQL `SKIP LOCKED` is not
presented as an absolute starvation guarantee.

## 7. Verify deterministic failure convergence

```bash
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_worker_lease_faults.py
```

For claim, renew, release, and guarded commit, the suite repeats each required window
at least 100 times:

1. failure before commit and backend termination before commit prove rollback and
   return `storage_unavailable`;
2. a test boundary performs a real PostgreSQL COMMIT, suppresses acknowledgement, and
   returns `commit_outcome_unknown`;
3. claim converges only through original UUID/intent replay;
4. renew/release first re-read the same proof's token/attempt/version/status—unchanged
   version permits the same original condition only while still eligible, advanced
   version is confirmation only, and expiry/transition/second failure keeps the safety
   signal false;
5. guarded commit converges only with the original 004 command and lifecycle intent;
   an absent receipt still must pass the current lease guard;
6. every case ends with one lease/lifecycle fact set and zero partial combinations.

Do not make the tests pass with an internal transaction retry, a new command/claim
ID, Worker time, weakened `synchronous_commit`, or omitted tenant predicate.

## 8. Run static, security, and governance gates

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
python3 -m unittest discover -s scripts/sdd/tests -v
```

The implementation security review must explicitly inspect:

- every SQL/lock path for tenant predicates and the global lock order;
- token generation, restricted receipt projection, digest comparison, redacted value
  types, `hide_parameters`, and safe exception chaining;
- the explicit M0-only risk that expired raw replay tokens and test dumps have no 006
  physical retention maximum; no production-enable claim is allowed;
- no network/model/Tool/secret-resolution/log-export call under row locks;
- exactly one safe terminal observation per public 006 operation in each recording
  log/metric/trace channel, emitted only after transaction/connection cleanup;
- each telemetry channel failure leaves the repository result unchanged, still
  attempts the other channels, and triggers no database retry;
- UUID age validation before receipt/queue lookup;
- no application DDL and no Worker/Reconciler/Checkpoint/execution imports.

## 9. Run latency acceptance

```bash
uv run pytest -m postgresql tests/performance/test_postgresql_worker_lease_kernel.py
```

Reference workload:

- PostgreSQL 18.6 at the exact Compose image digest;
- one tenant with at least 10,000 queued Runs;
- pool size 20, zero overflow, 20 clients;
- at least 100 warmups and 1,000 measured samples for each full
  `issue_claim_id + claim`, renew, authority read, and release path;
- nearest-rank p50/p95 reported per operation; every p95 below 200 ms;
- guarded-write latency may be reported as additional evidence but cannot replace any
  of the four required operation measurements;
- receipt cleanup load measured separately and never used to hide claim latency;
- constraints, `READ COMMITTED`, `synchronous_commit=on`, tenant filters, fencing,
  and redaction remain enabled.

The fixture executes PostgreSQL `CHECKPOINT` before setup and again after seeding, both
outside the measured interval, so writes from earlier acceptance modules cannot land in
the latency sample. This is allowed only for the disposable Compose acceptance role,
which has checkpoint privilege. Never grant checkpoint or migration privilege to the
production application/Worker role.

Record CPU, memory, database version/image digest, pool/timeouts, row counts, query
plans, lock-wait counts, and measured percentiles. Results from unlabelled machines are
not a regression comparison.

## 10. Exercise destructive downgrade only on a disposable copy

> **Warning**: `alembic downgrade 0001` deletes active leases and claim replay tokens.
> It does not delete 005 Run/Event/004
> receipt rows, but it is not a production rollback operation.

After the identity preflight from section 1, seed representative 005 plus 006 facts
and create a custom-format dump through the automated migration test. Then exercise:

```bash
uv run alembic downgrade 0001
uv run alembic current
uv run alembic upgrade head
uv run alembic current --check-heads
uv run pytest -m postgresql tests/integration/persistence/test_worker_lease_migrations.py
```

The test must verify:

- downgrade preserved all 005 Run/Event/004 receipt facts and removed only 006 facts;
- re-upgrade recreated an empty compatible lease kernel;
- the representative revision-0002 custom dump restores into a separate fresh database;
- restored migration heads, compatibility components, row counts, immutable receipt
  results, token redaction, and domain round trips match;
- restored dump files and database access are treated as credential-sensitive; the
  restored database cannot receive coordination traffic until old Worker connections
  are drained and `clock_timestamp()` is verified at/after every restored
  `lease_expires_at`, so no restored lease resumes execution authority;
- normal application rollback can leave additive 0002 tables in place for old 005
  binaries.

Production data preservation requires a verified backup/restore to a fresh database
and controlled cutover. Production backup scheduling, PITR, migration execution, and
deployment remain separately authorized and outside 006. The automated test must keep
its custom-format dump in a task-specific temporary directory and remove that dump
after restore assertions; an unrestricted backup reader is inside the accepted
sensitive-data threat boundary, not evidence of token encryption.

## 11. Stop and remove only the disposable environment

After re-verifying the Compose identity:

```bash
docker compose -f compose.test.yaml down -v
unset ZHIYI_TEST_DATABASE_URL
```

This deletes only the named disposable containers and volume.

## Expected final evidence

006 cannot be claimed complete until the implementation workflow records:

- the exact PostgreSQL tag/digest, migration head, two compatibility versions, and
  destructive-disposable warning;
- all claim UUID boundaries, replay/conflict, concurrency, monotonicity, guarded
  atomicity, restart, tenant, expired-observation, bounded-head-probe, failure-window,
  positive telemetry, channel-isolation, restore, and latency counts required above;
- zero sensitive sentinels and a completed tenant/SQL/lock/token security review;
- fast/PostgreSQL collection partition evidence, Ruff, format, mypy, SDD unit tests,
  design-drift, `$speckit-analyze`, and `$speckit-converge` results;
- explicit confirmation that no Worker background loop, LangGraph, Checkpoint,
  Agent/model/Tool/Graph execution, Reconciler, automatic recovery, REST/SSE, or SDK
  production behavior exists.
