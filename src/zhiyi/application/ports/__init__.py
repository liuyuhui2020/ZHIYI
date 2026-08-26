"""Public application ports implemented by outer adapters."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zhiyi.application.ports.clock import Clock
    from zhiyi.application.ports.identifier_generator import IdentifierGenerator
    from zhiyi.application.ports.model_provider import (
        ModelProvider,
        ProviderChunk,
        ProviderResponse,
    )
    from zhiyi.application.ports.run_repository import (
        CommandReceipt,
        CommitOutcome,
        RunRepository,
    )
    from zhiyi.application.ports.secret_provider import (
        SecretProvider,
        SecretReference,
        SecretResolutionError,
        SecretValue,
    )
    from zhiyi.application.ports.token_estimator import TokenEstimate, TokenEstimator

__all__ = [
    "Clock",
    "CommandReceipt",
    "CommitOutcome",
    "IdentifierGenerator",
    "ModelProvider",
    "ProviderChunk",
    "ProviderResponse",
    "RunRepository",
    "SecretProvider",
    "SecretReference",
    "SecretResolutionError",
    "SecretValue",
    "TokenEstimate",
    "TokenEstimator",
]


def __getattr__(name: str) -> object:
    if name == "Clock":
        from zhiyi.application.ports.clock import Clock

        return Clock
    if name == "IdentifierGenerator":
        from zhiyi.application.ports.identifier_generator import IdentifierGenerator

        return IdentifierGenerator
    if name in {"CommandReceipt", "CommitOutcome", "RunRepository"}:
        from zhiyi.application.ports import run_repository

        return getattr(run_repository, name)
    if name in {"ModelProvider", "ProviderChunk", "ProviderResponse"}:
        from zhiyi.application.ports import model_provider

        return getattr(model_provider, name)
    if name in {
        "SecretProvider",
        "SecretReference",
        "SecretResolutionError",
        "SecretValue",
    }:
        from zhiyi.application.ports import secret_provider

        return getattr(secret_provider, name)
    if name in {"TokenEstimate", "TokenEstimator"}:
        from zhiyi.application.ports import token_estimator

        return getattr(token_estimator, name)
    raise AttributeError(name)
