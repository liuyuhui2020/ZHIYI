"""Read-only, component-aware application-schema compatibility gates."""

from __future__ import annotations

import asyncio
import re
import weakref
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from zhiyi.adapters.persistence.postgresql_schema import SCHEMA_CONTRACT_VERSION
from zhiyi.application.ports.run_repository import RunRepositoryError, RunRepositoryErrorCode
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode


class PartialSchemaError(Exception):
    """Internal marker for an installed but incomplete physical schema."""


class SchemaIncompatibleError(Exception):
    """Internal component contract is missing, malformed, or unsupported."""


class SchemaUnavailableError(Exception):
    """Internal compatibility read could not reach or inspect storage."""


_cached_versions: weakref.WeakKeyDictionary[AsyncEngine, dict[str, int]] = (
    weakref.WeakKeyDictionary()
)
_engine_locks: weakref.WeakKeyDictionary[AsyncEngine, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)
_verified_physical_components: weakref.WeakKeyDictionary[AsyncEngine, set[str]] = (
    weakref.WeakKeyDictionary()
)
_PARTIAL_SCHEMA_SQLSTATES = frozenset({"42P01", "42703"})
_COMPONENT_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_RUN_REPOSITORY_COMPONENT = "run_repository"
_WORKER_LEASE_COMPONENT = "worker_lease_kernel"
_WORKER_LEASE_SCHEMA_CONTRACT_VERSION = 1
_WORKER_LEASE_PHYSICAL_INVENTORY = frozenset(
    {
        "worker_leases",
        "worker_lease_claim_receipts",
        "pk_worker_leases",
        "uq_worker_leases_tenant_claim",
        "fk_worker_leases_tenant_run_runs",
        "ck_worker_leases_worker_id_valid",
        "ck_worker_leases_claim_id_uuidv7",
        "ck_worker_leases_token_digest_length",
        "ck_worker_leases_attempt_no_positive",
        "ck_worker_leases_lease_version_positive",
        "ck_worker_leases_duration_seconds_supported",
        "ck_worker_leases_heartbeat_not_before_acquired",
        "ck_worker_leases_expiry_after_heartbeat",
        "ck_worker_leases_released_not_before_acquired",
        "ck_worker_leases_record_format_version_supported",
        "ix_worker_leases_tenant_inactive_running",
        "pk_worker_lease_claim_receipts",
        "fk_worker_lease_claim_receipts_tenant_run_runs",
        "ck_worker_lease_claim_receipts_claim_id_uuidv7",
        "ck_worker_lease_claim_receipts_replay_window_exact",
        "ck_worker_lease_claim_receipts_worker_id_valid",
        "ck_worker_lease_claim_receipts_duration_seconds_supported",
        "ck_worker_lease_claim_receipts_intent_format_version_supported",
        "ck_worker_lease_claim_receipts_intent_fingerprint_sha256",
        "ck_worker_lease_claim_receipts_outcome_supported",
        "ck_worker_lease_claim_receipts_attempt_no_positive",
        "ck_worker_lease_claim_receipts_initial_lease_version_positive",
        "ck_worker_lease_claim_receipts_replay_token_length",
        "ck_worker_lease_claim_receipts_complete_outcome",
        "ck_worker_lease_claim_receipts_record_format_version_supported",
        "ix_worker_lease_claim_receipts_cleanup",
    }
)


def _sqlstate(error: BaseException) -> str | None:
    candidate: object = error
    for _ in range(3):
        value = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if type(value) is str:
            return value
        nested = getattr(candidate, "orig", None)
        if not isinstance(nested, BaseException):
            break
        candidate = nested
    return None


async def _read_contract_version(
    engine: AsyncEngine,
    *,
    component: str,
) -> int | None:
    try:
        async with engine.connect() as connection:
            return cast(
                int | None,
                await connection.scalar(
                    text(
                        "SELECT contract_version FROM zhiyi_schema_compatibility "
                        "WHERE component = :component"
                    ),
                    {"component": component},
                ),
            )
    except ProgrammingError as error:
        if _sqlstate(error) in _PARTIAL_SCHEMA_SQLSTATES:
            raise PartialSchemaError from error
        raise


def _worker_lease_required_physical_inventory() -> frozenset[str]:
    return _WORKER_LEASE_PHYSICAL_INVENTORY


async def _read_worker_lease_physical_inventory(engine: AsyncEngine) -> frozenset[str]:
    required = _worker_lease_required_physical_inventory()
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT c.relname AS object_name "
                    "FROM pg_catalog.pg_class AS c "
                    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relname = ANY(:names) "
                    "UNION "
                    "SELECT constraint_record.conname AS object_name "
                    "FROM pg_catalog.pg_constraint AS constraint_record "
                    "JOIN pg_catalog.pg_namespace AS n "
                    "ON n.oid = constraint_record.connamespace "
                    "WHERE n.nspname = current_schema() "
                    "AND constraint_record.conname = ANY(:names)"
                ),
                {"names": sorted(required)},
            )
        ).scalars()
    return frozenset(value for value in rows if type(value) is str)


async def _ensure_worker_lease_physical_inventory(engine: AsyncEngine) -> None:
    verified = _verified_physical_components.get(engine)
    if verified is not None and _WORKER_LEASE_COMPONENT in verified:
        return
    async with _component_lock(engine, f"{_WORKER_LEASE_COMPONENT}_physical"):
        verified = _verified_physical_components.get(engine)
        if verified is not None and _WORKER_LEASE_COMPONENT in verified:
            return
        actual = await _read_worker_lease_physical_inventory(engine)
        if actual != _worker_lease_required_physical_inventory():
            raise SchemaIncompatibleError
        if verified is None:
            verified = set()
            _verified_physical_components[engine] = verified
        verified.add(_WORKER_LEASE_COMPONENT)


def _validate_contract_request(component: str, accepted_versions: frozenset[int]) -> None:
    if type(component) is not str or _COMPONENT_PATTERN.fullmatch(component) is None:
        raise ValueError("component must be a safe bounded identifier")
    if (
        type(accepted_versions) is not frozenset
        or not accepted_versions
        or any(type(version) is not int or version < 1 for version in accepted_versions)
    ):
        raise ValueError("accepted_versions must be a non-empty frozenset of positive integers")


def _component_lock(engine: AsyncEngine, component: str) -> asyncio.Lock:
    locks = _engine_locks.get(engine)
    if locks is None:
        locks = {}
        _engine_locks[engine] = locks
    lock = locks.get(component)
    if lock is None:
        lock = asyncio.Lock()
        locks[component] = lock
    return lock


def _cached_contract_version(engine: AsyncEngine, component: str) -> int | None:
    versions = _cached_versions.get(engine)
    return versions.get(component) if versions is not None else None


def _cache_contract_version(engine: AsyncEngine, component: str, version: int) -> None:
    versions = _cached_versions.get(engine)
    if versions is None:
        versions = {}
        _cached_versions[engine] = versions
    versions[component] = version


async def ensure_component_schema_compatible(
    engine: AsyncEngine,
    *,
    component: str,
    accepted_versions: frozenset[int],
) -> None:
    """Verify one component using SELECT-only access and an engine-scoped cache."""

    _validate_contract_request(component, accepted_versions)
    cached = _cached_contract_version(engine, component)
    if cached is not None:
        if cached not in accepted_versions:
            raise SchemaIncompatibleError
        return

    async with _component_lock(engine, component):
        cached = _cached_contract_version(engine, component)
        if cached is None:
            try:
                version = await _read_contract_version(engine, component=component)
            except PartialSchemaError as error:
                raise SchemaIncompatibleError from error
            except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
                raise SchemaUnavailableError from error
            if type(version) is not int or version < 1:
                raise SchemaIncompatibleError
            _cache_contract_version(engine, component, version)
            cached = version
        if cached not in accepted_versions:
            raise SchemaIncompatibleError


async def ensure_schema_compatible(engine: AsyncEngine) -> None:
    """Retain the Feature 005 run-repository compatibility boundary."""

    try:
        await ensure_component_schema_compatible(
            engine,
            component=_RUN_REPOSITORY_COMPONENT,
            accepted_versions=frozenset({SCHEMA_CONTRACT_VERSION}),
        )
    except SchemaIncompatibleError as error:
        raise RunRepositoryError(RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE) from error
    except SchemaUnavailableError as error:
        raise RunRepositoryError(RunRepositoryErrorCode.STORAGE_UNAVAILABLE) from error


async def ensure_worker_lease_schema_compatible(engine: AsyncEngine) -> None:
    """Verify the Worker Lease Kernel component without changing any schema."""

    try:
        await ensure_component_schema_compatible(
            engine,
            component=_WORKER_LEASE_COMPONENT,
            accepted_versions=frozenset({_WORKER_LEASE_SCHEMA_CONTRACT_VERSION}),
        )
        await _ensure_worker_lease_physical_inventory(engine)
    except SchemaIncompatibleError as error:
        raise WorkerLeaseError(WorkerLeaseErrorCode.SCHEMA_INCOMPATIBLE) from error
    except (SchemaUnavailableError, DBAPIError, SQLAlchemyError, ConnectionError) as error:
        raise WorkerLeaseError(WorkerLeaseErrorCode.STORAGE_UNAVAILABLE) from error
