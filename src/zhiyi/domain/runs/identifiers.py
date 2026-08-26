"""Validated, strongly typed identifiers used by the run lifecycle domain."""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class _Identifier:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _IDENTIFIER_PATTERN.fullmatch(self.value) is None:
            raise ValueError("identifier must be a safe ASCII value of at most 128 characters")

    def __str__(self) -> str:
        return self.value


class TenantId(_Identifier):
    pass


class AgentId(_Identifier):
    pass


class AgentVersionId(_Identifier):
    pass


class TaskId(_Identifier):
    pass


class RunId(_Identifier):
    pass


class CommandId(_Identifier):
    pass


class EventId(_Identifier):
    pass


class ChargeId(_Identifier):
    pass


class CorrelationId(_Identifier):
    pass


class ReferenceId(_Identifier):
    pass


@dataclass(frozen=True, slots=True)
class AgentVersionRef:
    tenant_id: TenantId
    agent_id: AgentId
    version_id: AgentVersionId
    build_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        if not isinstance(self.agent_id, AgentId):
            raise TypeError("agent_id must be AgentId")
        if not isinstance(self.version_id, AgentVersionId):
            raise TypeError("version_id must be AgentVersionId")
        if (
            type(self.build_digest) is not str
            or _DIGEST_PATTERN.fullmatch(self.build_digest) is None
        ):
            raise ValueError("build_digest must be a lowercase SHA-256 digest")
