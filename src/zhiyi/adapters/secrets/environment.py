"""Explicit, non-enumerating environment secret resolution."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from zhiyi.application.ports.secret_provider import (
    SecretReference,
    SecretResolutionError,
    SecretValue,
)


class EnvironmentSecretProvider:
    """Resolve only configured references through exact-key lookups."""

    def __init__(
        self,
        *,
        allowed_references: Iterable[SecretReference],
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._allowed = frozenset(reference.name for reference in allowed_references)
        if not self._allowed:
            raise ValueError("at least one secret reference must be allowlisted")
        self._environment = os.environ if environment is None else environment

    async def resolve(self, reference: SecretReference) -> SecretValue:
        if reference.name not in self._allowed:
            raise SecretResolutionError
        value = self._environment.get(reference.name)
        if value is None or not value.strip():
            raise SecretResolutionError
        return SecretValue(value)
