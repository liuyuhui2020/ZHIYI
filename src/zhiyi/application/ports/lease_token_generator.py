"""Framework-neutral source of independently generated Worker lease tokens."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from zhiyi.domain.worker_leases.identifiers import LeaseToken


@runtime_checkable
class LeaseTokenGenerator(Protocol):
    def new_token(self) -> LeaseToken: ...
