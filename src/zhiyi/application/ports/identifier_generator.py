"""Identifier generation boundary for lifecycle services."""

from __future__ import annotations

from typing import Protocol


class IdentifierGenerator(Protocol):
    def new_id(self, namespace: str) -> str:
        """Return a new identifier string for the requested namespace."""

        ...
