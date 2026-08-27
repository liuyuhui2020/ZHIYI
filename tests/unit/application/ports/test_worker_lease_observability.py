"""Positive, safe terminal telemetry contract for Worker lease operations."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from typing import NoReturn
from uuid import UUID

import pytest

from zhiyi.application.ports.worker_lease_observability import (
    LeaseOperation,
    LeaseOperationObservation,
    LeaseTransactionPhase,
    WorkerLeaseTelemetry,
    deliver_terminal_observation,
)
from zhiyi.domain.runs.identifiers import CorrelationId, RunId, TenantId
from zhiyi.domain.worker_leases.identifiers import LeaseClaimId, WorkerId


def _observation(
    operation: LeaseOperation = LeaseOperation.CLAIM,
    outcome_code: str = "claimed",
) -> LeaseOperationObservation:
    return LeaseOperationObservation(
        operation=operation,
        terminal_phase=LeaseTransactionPhase.COMPLETE,
        outcome_code=outcome_code,
        correlation_id=CorrelationId("correlation-1"),
        tenant_id=TenantId("tenant-1"),
        run_id=RunId("run-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000001")),
        duration_bucket="10-30s",
        replayed=False,
        empty=False,
        contended=True,
    )


class RecordingTelemetry(WorkerLeaseTelemetry):
    def __init__(self, *, active: list[bool], fail_channel: str | None = None) -> None:
        self.active = active
        self.fail_channel = fail_channel
        self.calls: list[tuple[str, LeaseOperationObservation]] = []

    def _record(self, channel: str, observation: LeaseOperationObservation) -> None:
        assert self.active == [False], "telemetry ran before resource cleanup"
        self.calls.append((channel, observation))
        if self.fail_channel == channel:
            raise RuntimeError(f"{channel} unavailable")

    def record_log(self, observation: LeaseOperationObservation) -> None:
        self._record("log", observation)

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        self._record("metric", observation)

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        self._record("trace", observation)


def test_terminal_observation_is_immutable_and_has_only_the_safe_allowlist() -> None:
    observation = _observation()

    assert {item.name for item in fields(observation)} == {
        "operation",
        "terminal_phase",
        "outcome_code",
        "correlation_id",
        "tenant_id",
        "run_id",
        "worker_id",
        "claim_id",
        "duration_bucket",
        "replayed",
        "empty",
        "contended",
    }
    assert "token" not in repr(observation).lower()
    assert "digest" not in repr(observation).lower()
    assert "fingerprint" not in repr(observation).lower()
    with pytest.raises(FrozenInstanceError):
        observation.empty = True  # type: ignore[misc]


def test_terminal_observation_rejects_untyped_or_unbounded_fields() -> None:
    with pytest.raises(TypeError, match="LeaseOperation"):
        LeaseOperationObservation(
            operation="claim",  # type: ignore[arg-type]
            terminal_phase=LeaseTransactionPhase.COMPLETE,
            outcome_code="claimed",
            correlation_id=None,
            tenant_id=None,
            run_id=None,
            worker_id=None,
            claim_id=None,
            duration_bucket=None,
            replayed=False,
            empty=False,
            contended=False,
        )
    with pytest.raises(ValueError, match="outcome_code"):
        LeaseOperationObservation(
            operation=LeaseOperation.CLAIM,
            terminal_phase=LeaseTransactionPhase.COMPLETE,
            outcome_code="x" * 65,
            correlation_id=None,
            tenant_id=None,
            run_id=None,
            worker_id=None,
            claim_id=None,
            duration_bucket=None,
            replayed=False,
            empty=False,
            contended=False,
        )


def test_delivery_occurs_after_cleanup_once_per_channel_in_fixed_order() -> None:
    resource_active = [True]
    telemetry = RecordingTelemetry(active=resource_active)
    observation = _observation()
    resource_active[0] = False

    deliver_terminal_observation(telemetry, observation)

    assert telemetry.calls == [
        ("log", observation),
        ("metric", observation),
        ("trace", observation),
    ]


@pytest.mark.parametrize("failed_channel", ["log", "metric", "trace"])
def test_each_channel_failure_is_isolated_and_all_channels_are_attempted(
    failed_channel: str,
) -> None:
    telemetry = RecordingTelemetry(active=[False], fail_channel=failed_channel)
    observation = _observation()

    deliver_terminal_observation(telemetry, observation)

    assert [channel for channel, _ in telemetry.calls] == ["log", "metric", "trace"]


@pytest.mark.parametrize("operation", list(LeaseOperation))
@pytest.mark.parametrize(
    "outcome_code",
    [
        "issued",
        "claimed",
        "no_work",
        "idempotency_conflict",
        "idempotency_expired",
        "lease_not_current",
        "lease_expired",
        "schema_incompatible",
        "storage_unavailable",
        "commit_outcome_unknown",
        "data_corruption",
    ],
)
def test_every_public_operation_and_terminal_outcome_uses_the_same_safe_fanout(
    operation: LeaseOperation,
    outcome_code: str,
) -> None:
    telemetry = RecordingTelemetry(active=[False])
    observation = _observation(operation, outcome_code)

    deliver_terminal_observation(telemetry, observation)

    assert telemetry.calls == [
        ("log", observation),
        ("metric", observation),
        ("trace", observation),
    ]
    printable = repr(telemetry.calls)
    assert "token=" not in printable.lower()
    assert "digest=" not in printable.lower()
    assert "fingerprint=" not in printable.lower()
    assert "postgresql://" not in printable.lower()


class MissingTelemetryMethod:
    def record_log(self, observation: LeaseOperationObservation) -> NoReturn:
        raise AssertionError(observation)


def test_delivery_requires_the_complete_three_channel_protocol() -> None:
    assert not isinstance(MissingTelemetryMethod(), WorkerLeaseTelemetry)
