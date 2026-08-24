from __future__ import annotations

import gc
import math
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhiyi.domain.runs.aggregate import Run
from zhiyi.domain.runs.budget import RunBudget
from zhiyi.domain.runs.events import RunStatus
from zhiyi.domain.runs.identifiers import (
    AgentId,
    AgentVersionId,
    AgentVersionRef,
    EventId,
    RunId,
    TaskId,
    TenantId,
)

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
WARMUP_TRANSITIONS = 1_000
MEASURED_TRANSITIONS = 10_000
MAX_P95_NS = 1_000_000


@pytest.mark.performance
def test_local_transition_p95_is_at_most_one_millisecond() -> None:
    tenant_id = TenantId("tenant-performance")
    queued = Run.create(
        tenant_id=tenant_id,
        run_id=RunId("run-performance"),
        task_id=TaskId("task-performance"),
        agent_version=AgentVersionRef(
            tenant_id=tenant_id,
            agent_id=AgentId("agent-performance"),
            version_id=AgentVersionId("version-performance"),
            build_digest="sha256:" + "a" * 64,
        ),
        budget=RunBudget(
            deadline_at=NOW + timedelta(hours=1),
            max_steps=1,
            max_model_calls=1,
            max_tool_calls=1,
            max_input_tokens=1,
            max_output_tokens=1,
            max_total_tokens=2,
            max_cost=Decimal("1"),
            currency="USD",
        ),
        observed_at=NOW,
        event_id=EventId("event-created"),
    ).run

    for index in range(WARMUP_TRANSITIONS):
        mutation = queued.start(
            observed_at=NOW,
            event_id=EventId(f"warm-event-{index}"),
        )
        assert mutation.run.status is RunStatus.RUNNING

    durations: list[int] = []
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(MEASURED_TRANSITIONS):
            event_id = EventId(f"measured-event-{index}")
            started_at = time.perf_counter_ns()
            mutation = queued.start(observed_at=NOW, event_id=event_id)
            finished_at = time.perf_counter_ns()
            assert finished_at >= started_at
            assert mutation.run.version == 2
            durations.append(finished_at - started_at)
    finally:
        if gc_was_enabled:
            gc.enable()

    p95_index = math.ceil(MEASURED_TRANSITIONS * 0.95) - 1
    p95_ns = sorted(durations)[p95_index]
    assert p95_ns <= MAX_P95_NS, f"transition p95 was {p95_ns / 1_000_000:.3f} ms"
