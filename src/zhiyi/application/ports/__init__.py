"""Public application ports implemented by outer adapters."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zhiyi.application.ports.clock import Clock
    from zhiyi.application.ports.identifier_generator import IdentifierGenerator
    from zhiyi.application.ports.lease_token_generator import LeaseTokenGenerator
    from zhiyi.application.ports.model_provider import (
        ModelProvider,
        ProviderChunk,
        ProviderResponse,
    )
    from zhiyi.application.ports.run_repository import (
        CommandReceipt,
        CommitOutcome,
        RunRepository,
        RunRepositoryError,
        RunRepositoryErrorCode,
        safe_repository_error_message,
    )
    from zhiyi.application.ports.run_repository_validation import validate_commit_candidate
    from zhiyi.application.ports.secret_provider import (
        SecretProvider,
        SecretReference,
        SecretResolutionError,
        SecretValue,
    )
    from zhiyi.application.ports.token_estimator import TokenEstimate, TokenEstimator
    from zhiyi.application.ports.worker_lease_observability import (
        LeaseOperation,
        LeaseOperationObservation,
        LeaseTransactionPhase,
        WorkerLeaseTelemetry,
        deliver_terminal_observation,
    )
    from zhiyi.application.ports.worker_lease_repository import (
        LeaseGuardedRunRepository,
        WorkerLeaseError,
        WorkerLeaseErrorCode,
        WorkerLeaseRepository,
        safe_worker_lease_error_message,
    )

__all__ = [
    "Clock",
    "CommandReceipt",
    "CommitOutcome",
    "IdentifierGenerator",
    "LeaseGuardedRunRepository",
    "LeaseOperation",
    "LeaseOperationObservation",
    "LeaseTokenGenerator",
    "LeaseTransactionPhase",
    "ModelProvider",
    "ProviderChunk",
    "ProviderResponse",
    "RunRepository",
    "RunRepositoryError",
    "RunRepositoryErrorCode",
    "SecretProvider",
    "SecretReference",
    "SecretResolutionError",
    "SecretValue",
    "TokenEstimate",
    "TokenEstimator",
    "WorkerLeaseError",
    "WorkerLeaseErrorCode",
    "WorkerLeaseRepository",
    "WorkerLeaseTelemetry",
    "deliver_terminal_observation",
    "safe_repository_error_message",
    "safe_worker_lease_error_message",
    "validate_commit_candidate",
]


def __getattr__(name: str) -> object:
    if name == "Clock":
        from zhiyi.application.ports.clock import Clock

        return Clock
    if name == "IdentifierGenerator":
        from zhiyi.application.ports.identifier_generator import IdentifierGenerator

        return IdentifierGenerator
    if name == "LeaseTokenGenerator":
        from zhiyi.application.ports.lease_token_generator import LeaseTokenGenerator

        return LeaseTokenGenerator
    if name in {
        "CommandReceipt",
        "CommitOutcome",
        "RunRepository",
        "RunRepositoryError",
        "RunRepositoryErrorCode",
        "safe_repository_error_message",
    }:
        from zhiyi.application.ports import run_repository

        return getattr(run_repository, name)
    if name == "validate_commit_candidate":
        from zhiyi.application.ports.run_repository_validation import validate_commit_candidate

        return validate_commit_candidate
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
    if name in {
        "LeaseOperation",
        "LeaseOperationObservation",
        "LeaseTransactionPhase",
        "WorkerLeaseTelemetry",
        "deliver_terminal_observation",
    }:
        from zhiyi.application.ports import worker_lease_observability

        return getattr(worker_lease_observability, name)
    if name in {
        "LeaseGuardedRunRepository",
        "WorkerLeaseError",
        "WorkerLeaseErrorCode",
        "WorkerLeaseRepository",
        "safe_worker_lease_error_message",
    }:
        from zhiyi.application.ports import worker_lease_repository

        return getattr(worker_lease_repository, name)
    raise AttributeError(name)
