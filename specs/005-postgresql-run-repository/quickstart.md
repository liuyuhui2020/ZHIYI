# Quickstart: PostgreSQL RunRepository Acceptance

**Feature**: `005-postgresql-run-repository`

**Purpose**: Reproduce the local migration, contract, concurrency, recovery, and
rollback/restore acceptance after Feature 005 implementation.

This quickstart uses only a disposable local PostgreSQL instance. It does not start a
Worker, acquire a lease, run LangGraph, call a model, or touch production data.

## Prerequisites

- Python 3.12 and `uv`
- Docker with Compose v2
- Free local ports required by `compose.test.yaml`
- No production database URL in `ZHIYI_TEST_DATABASE_URL`

The test Compose file uses public disposable credentials and PostgreSQL 18.6 pinned
to an immutable image digest. Never copy these credentials into a shared or
production environment.

## 1. Start the disposable database

```bash
docker compose -f compose.test.yaml up -d --wait postgresql
export ZHIYI_TEST_DATABASE_URL='postgresql+psycopg://zhiyi_test:zhiyi_test_password@127.0.0.1:55432/zhiyi_test'
```

Confirm only the expected test service is running:

```bash
docker compose -f compose.test.yaml ps
```

## 2. Install the frozen environment

```bash
uv sync --all-groups --frozen --python 3.12
```

Expected direct persistence packages are SQLAlchemy 2.0.52, Alembic 1.19.1, and
Psycopg 3.3.4. `uv.lock` remains the dependency-tree source of truth.

## 3. Establish and verify the schema explicitly

Application construction must not modify the database. Run the migration command as
an explicit release step:

```bash
uv run alembic upgrade head
uv run alembic current --check-heads
```

Then run the schema compatibility and migration checks:

```bash
uv run pytest -m postgresql tests/integration/persistence/test_migrations.py
```

Expected result:

- Alembic reports the database at all current heads.
- `zhiyi_schema_compatibility` reports RunRepository contract version 1.
- Constructing/reopening the repository performs only reads until an explicit
  repository operation is called; it never creates or upgrades tables.

## 4. Run fast and PostgreSQL test lanes

Fast offline lane:

```bash
uv run pytest -m "not online and not postgresql"
```

Real PostgreSQL lane:

```bash
uv run pytest -m postgresql
```

Every database-dependent contract, integration, migration, fault, and performance
module must declare the registered `postgresql` marker at module scope. Before running
the suites, CI performs collection-only assertions that this lane is non-empty and
that `tests/contract/persistence/test_postgresql_run_repository_contract.py`,
`tests/integration/persistence/`, and
`tests/performance/test_postgresql_run_repository.py` contribute zero nodes to the fast lane;
a silently unmarked or silently skipped real-database test fails the gate.

The PostgreSQL lane must include:

- the provider-neutral repository contract also used by the memory adapter;
- closing/rebuilding engines and lossless Run/Event/Receipt round trips;
- multiple independent connections and engines for replay and version races;
- global event-ID and cross-tenant negative tests;
- commit atomicity and the three failure windows;
- direct corruption and schema incompatibility checks;
- migration, downgrade/re-upgrade, and backup/restore exercises.

No skipped PostgreSQL contract/fault/migration test counts as acceptance.

## 5. Run static and governance gates

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
python3 -m unittest discover -s scripts/sdd/tests -v
```

Also verify that model/provider online tests remain excluded and no real Provider key
is required.

## 6. Run the concurrency and latency acceptance

```bash
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_concurrency.py
uv run pytest -m postgresql tests/performance/test_postgresql_run_repository.py
```

Expected evidence:

- at least 100 same-version state-changing race groups and 1,000 attempts, with at
  most one winner per group;
- at least 100 same-command replays over at least 20 independent connections, with
  one actual state change and identical outcomes;
- legal distinct zero-event commands persist receipts without advancing Run version;
- performance data seeded with at least 100 Runs and 100 events per Run; SQLAlchemy
  pool size 20 with zero overflow; 20 clients; at least 100 warm-up and 1,000 measured
  operations for each Run load, 100-event page, and different-Run atomic commit;
  nearest-rank p95 below 100 ms for every operation class.

Record CPU, memory, database image digest, pool settings, test counts, and measured
p50/p95 with the result; do not compare unlabelled machines as a regression gate.

## 7. Verify failure convergence

```bash
uv run pytest -m postgresql tests/integration/persistence/test_postgresql_faults.py
```

The deterministic suite must prove:

1. pre-commit connection failure and a backend terminated before commit return
   `storage_unavailable` with zero committed rows;
2. a real commit whose acknowledgement is deliberately suppressed returns
   `commit_outcome_unknown`;
3. replaying the original command and identical intent through a new connection
   returns the one committed outcome or performs the one previously rolled-back
   commit;
4. public errors, `repr`, and captured logs contain none of the planted DSN,
   password, SQL, payload, answer, or hidden-reasoning markers.

Do not replace the original command ID after `commit_outcome_unknown`, and do not add
an automatic transaction retry to make this test pass.

## 8. Exercise destructive downgrade only on a disposable copy

> **Warning**: `alembic downgrade base` deletes all Feature 005 Run, Event, and
> Receipt data. The following command is authorized only for the disposable database
> created by `compose.test.yaml`. It is not a production rollback procedure.

Before downgrade, `docker compose -f compose.test.yaml ps` and a database identity
query must both show the expected Compose service, localhost test port, database
`zhiyi_test`, and user `zhiyi_test`. Any mismatch stops the exercise.

First create the test suite's representative data and a custom-format logical dump:

```bash
uv run pytest -m postgresql tests/integration/persistence/test_migrations.py -k seed_representative_data
docker compose -f compose.test.yaml exec -T postgresql \
  pg_dump -U zhiyi_test -d zhiyi_test -Fc -f /tmp/zhiyi-005.dump
```

Exercise teardown and clean rebuild:

```bash
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic current --check-heads
```

The automated migration test restores `/tmp/zhiyi-005.dump` into a separate fresh
database and requires: all Alembic heads current, RunRepository compatibility version
1, identical table row counts, identical stable fact digests, and 100% representative
domain round trips. A controlled cutover is allowed only after these checks and the
shared read contract pass. Production rollback with data preservation must follow the
same restore-to-new-database boundary; production backup schedules and PITR are
outside 005.

## 9. Stop and remove only the disposable environment

The following operation deletes the explicitly scoped test containers and volume:

```bash
docker compose -f compose.test.yaml down -v
unset ZHIYI_TEST_DATABASE_URL
```

Verify the target with `docker compose -f compose.test.yaml ps` before running the
cleanup command. Do not point this Compose project or migration URL at production.

## Expected Final Evidence

Feature 005 is not complete until the implementation workflow records:

- exact PostgreSQL image tag and digest;
- empty upgrade, head check, destructive disposable downgrade/re-upgrade, and fresh
  restore results;
- memory and PostgreSQL shared contract results;
- concurrency, replay, atomicity, fault classification, corruption, compatibility,
  redaction, and performance counts;
- PostgreSQL/fast-lane collection partition evidence and the explicit SQL/tenant
  security review with every critical/high finding resolved and re-verified;
- Ruff, format, mypy, governance, design-drift, and converge results;
- explicit confirmation that Worker, leases, Checkpoint, API/SSE, Model Gateway
  integration, and background execution remain absent.
