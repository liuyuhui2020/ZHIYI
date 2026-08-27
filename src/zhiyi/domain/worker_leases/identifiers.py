"""Validated, framework-neutral Worker lease capability values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import RFC_4122, UUID

_WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_SIGNED_BIGINT = 9_223_372_036_854_775_807

MIN_LEASE_DURATION_SECONDS = 10
MAX_LEASE_DURATION_SECONDS = 30
DEFAULT_LEASE_DURATION_SECONDS = 30
LEASE_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class WorkerId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _WORKER_ID_PATTERN.fullmatch(self.value) is None:
            raise ValueError("worker identifier must be safe ASCII and at most 128 characters")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class LeaseClaimId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("claim identifier must be UUIDv7")
        if self.value.version != 7 or self.value.variant != RFC_4122:
            raise ValueError("claim identifier must be UUIDv7 with the RFC variant")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class LeaseToken:
    value: bytes

    def __post_init__(self) -> None:
        if type(self.value) is not bytes:
            raise TypeError("lease token must contain exactly 32 bytes")
        if len(self.value) != LEASE_TOKEN_BYTES:
            raise ValueError("lease token must contain exactly 32 bytes")

    def __str__(self) -> str:
        return "<lease-token:redacted>"

    def __repr__(self) -> str:
        return "LeaseToken(<redacted>)"


@dataclass(frozen=True, slots=True)
class LeaseDurationSeconds:
    value: int = DEFAULT_LEASE_DURATION_SECONDS

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("lease duration must be an integer between 10 and 30 seconds")
        if not MIN_LEASE_DURATION_SECONDS <= self.value <= MAX_LEASE_DURATION_SECONDS:
            raise ValueError("lease duration must be between 10 and 30 seconds")


@dataclass(frozen=True, slots=True)
class _PositiveCounter:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("counter must be a positive integer")
        if not 1 <= self.value <= _MAX_SIGNED_BIGINT:
            raise ValueError("counter must be a positive integer within PostgreSQL bigint")

    def next(self) -> _PositiveCounter:
        if self.value == _MAX_SIGNED_BIGINT:
            raise OverflowError("counter cannot exceed PostgreSQL bigint")
        return type(self)(self.value + 1)


class LeaseAttemptNo(_PositiveCounter):
    pass


class LeaseVersion(_PositiveCounter):
    pass
