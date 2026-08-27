"""Repository terminal telemetry is delivered only after resource cleanup."""

from __future__ import annotations

from uuid import UUID

import pytest

from zhiyi.adapters.persistence.postgresql_transaction_support import (
    deliver_after_database_cleanup,
)
from zhiyi.application.ports.worker_lease_observability import (
    LeaseOperation,
    LeaseOperationObservation,
    LeaseTransactionPhase,
)
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.identifiers import LeaseClaimId, WorkerId


class _ResourceState:
    def __init__(self, *, transaction_active: bool, connection_open: bool) -> None:
        self.transaction_active = transaction_active
        self.connection_open = connection_open


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def record_log(self, observation: LeaseOperationObservation) -> None:
        self.calls.append(f"log:{observation.outcome_code}")

    def record_metric(self, observation: LeaseOperationObservation) -> None:
        self.calls.append(f"metric:{observation.outcome_code}")

    def record_trace(self, observation: LeaseOperationObservation) -> None:
        self.calls.append(f"trace:{observation.outcome_code}")


def _observation() -> LeaseOperationObservation:
    return LeaseOperationObservation(
        operation=LeaseOperation.RENEW,
        terminal_phase=LeaseTransactionPhase.COMPLETE,
        outcome_code="applied",
        correlation_id=None,
        tenant_id=TenantId("tenant-1"),
        run_id=RunId("run-1"),
        worker_id=WorkerId("worker-1"),
        claim_id=LeaseClaimId(UUID("0198f1c1-8c80-7000-8000-000000000001")),
        duration_bucket="10-30s",
        replayed=False,
        empty=False,
        contended=False,
    )


@pytest.mark.parametrize(
    ("transaction_active", "connection_open"),
    [(True, False), (False, True), (True, True)],
)
def test_delivery_refuses_to_run_before_transaction_and_connection_cleanup(
    transaction_active: bool,
    connection_open: bool,
) -> None:
    telemetry = _Telemetry()

    with pytest.raises(RuntimeError, match="cleanup"):
        deliver_after_database_cleanup(
            telemetry,
            _observation(),
            state=_ResourceState(
                transaction_active=transaction_active,
                connection_open=connection_open,
            ),
        )

    assert telemetry.calls == []


def test_delivery_fans_out_once_after_cleanup() -> None:
    telemetry = _Telemetry()

    deliver_after_database_cleanup(
        telemetry,
        _observation(),
        state=_ResourceState(transaction_active=False, connection_open=False),
    )

    assert telemetry.calls == ["log:applied", "metric:applied", "trace:applied"]
