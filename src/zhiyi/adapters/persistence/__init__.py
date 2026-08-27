"""Persistence adapters for the run lifecycle boundary."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zhiyi.adapters.persistence.memory_run_repository import MemoryRunRepository
    from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository
    from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
        PostgreSQLWorkerLeaseRepository,
    )

__all__ = [
    "MemoryRunRepository",
    "PostgreSQLRunRepository",
    "PostgreSQLWorkerLeaseRepository",
]


def __getattr__(name: str) -> object:
    if name == "MemoryRunRepository":
        from zhiyi.adapters.persistence.memory_run_repository import MemoryRunRepository

        return MemoryRunRepository
    if name == "PostgreSQLRunRepository":
        from zhiyi.adapters.persistence.postgresql_run_repository import PostgreSQLRunRepository

        return PostgreSQLRunRepository
    if name == "PostgreSQLWorkerLeaseRepository":
        from zhiyi.adapters.persistence.postgresql_worker_lease_repository import (
            PostgreSQLWorkerLeaseRepository,
        )

        return PostgreSQLWorkerLeaseRepository
    raise AttributeError(name)
