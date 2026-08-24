from __future__ import annotations

import time

import pytest

from zhiyi.adapters.models.fake import FakeProvider, FakeScript
from zhiyi.adapters.models.token_estimator import ConservativeTokenEstimator
from zhiyi.application.models.contracts import (
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilityProfile,
    ModelLimits,
    ModelRequest,
    ModelRoute,
    ModelTarget,
    ProviderId,
    TextPart,
)
from zhiyi.application.ports.model_provider import ProviderResponse
from zhiyi.application.services.model_gateway import DefaultModelGateway

_WARM_UP_CALLS = 200
_MEASURED_CALLS = 10_000


@pytest.mark.performance
@pytest.mark.asyncio
async def test_gateway_local_overhead_p95_is_below_ten_milliseconds() -> None:
    response = ProviderResponse(content=(TextPart("ok"),), finish_reason=FinishReason.STOP)
    provider = FakeProvider(
        FakeScript(completions=(response,) * (_WARM_UP_CALLS + _MEASURED_CALLS))
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )
    route = ModelRoute(
        ModelTarget(
            provider=ProviderId("fake"),
            model_id="performance-fixture",
            credential=None,
            capabilities=ModelCapabilityProfile(),
            limits=ModelLimits(max_retries=0),
        )
    )
    request = ModelRequest(
        request_id="performance-request",
        messages=(Message(MessageRole.USER, (TextPart("hello"),)),),
        max_output_tokens=8,
    )

    for _ in range(_WARM_UP_CALLS):
        await gateway.complete(route, request)

    samples: list[int] = []
    for _ in range(_MEASURED_CALLS):
        started = time.perf_counter_ns()
        await gateway.complete(route, request)
        samples.append(time.perf_counter_ns() - started)

    samples.sort()
    p95_seconds = samples[int(_MEASURED_CALLS * 0.95) - 1] / 1_000_000_000

    assert p95_seconds < 0.010, f"Gateway p95 was {p95_seconds * 1_000:.3f} ms"
