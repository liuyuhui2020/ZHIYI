"""Secret resolution port with non-printable secret values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_SECRET_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


@dataclass(frozen=True, slots=True)
class SecretReference:
    """A stable reference to secret material, never the material itself."""

    name: str

    def __post_init__(self) -> None:
        if not _SECRET_REFERENCE_PATTERN.fullmatch(self.name):
            raise ValueError("secret reference must be an uppercase configuration key")


class SecretValue:
    """A deliberately non-printable wrapper used only at an adapter boundary."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("secret value must not be empty")
        self.__value = value

    def reveal(self) -> str:
        """Return secret material to a provider adapter."""
        return self.__value

    def __str__(self) -> str:
        return "********"

    def __repr__(self) -> str:
        return "SecretValue(********)"


class SecretResolutionError(Exception):
    """Safe failure that never distinguishes missing from unauthorized secrets."""

    def __init__(self) -> None:
        super().__init__("Secret is unavailable")


class SecretProvider(Protocol):
    """Resolve a secret reference without exposing storage details."""

    async def resolve(self, reference: SecretReference) -> SecretValue: ...
