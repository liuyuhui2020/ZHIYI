from __future__ import annotations

import os

import pytest

from zhiyi.adapters.models.openai import OpenAIProvider
from zhiyi.adapters.models.token_estimator import ConservativeTokenEstimator
from zhiyi.adapters.secrets.environment import EnvironmentSecretProvider
from zhiyi.application.models.contracts import (
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
from zhiyi.application.ports.secret_provider import SecretReference
from zhiyi.application.services.model_gateway import DefaultModelGateway

pytestmark = [
    pytest.mark.online,
    pytest.mark.skipif(
        os.getenv("ZHIYI_RUN_ONLINE_SMOKE") != "1"
        or not os.getenv("OPENAI_API_KEY")
        or not os.getenv("ZHIYI_OPENAI_SMOKE_MODEL"),
        reason="online smoke requires explicit opt-in, key, and model id",
    ),
]


@pytest.mark.asyncio
async def test_openai_one_request_smoke() -> None:
    reference = SecretReference("OPENAI_API_KEY")
    gateway = DefaultModelGateway(
        providers=(OpenAIProvider(),),
        token_estimator=ConservativeTokenEstimator(),
        secret_provider=EnvironmentSecretProvider(allowed_references=(reference,)),
    )
    target = ModelTarget(
        provider=ProviderId("openai"),
        model_id=os.environ["ZHIYI_OPENAI_SMOKE_MODEL"],
        credential=reference,
        capabilities=ModelCapabilityProfile(),
        limits=ModelLimits(attempt_timeout_seconds=10, max_retries=0),
    )
    request = ModelRequest(
        request_id="openai-online-smoke",
        messages=(Message(MessageRole.USER, (TextPart("Reply with OK."),)),),
        max_output_tokens=8,
    )

    response = await gateway.complete(ModelRoute(target, total_timeout_seconds=12), request)

    assert response.content or response.tool_calls or response.structured_output is not None
