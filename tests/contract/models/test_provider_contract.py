from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

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
    ModelUsage,
    ProviderId,
    TextPart,
)
from zhiyi.application.ports.model_provider import ProviderChunk, ProviderResponse
from zhiyi.application.services.model_gateway import DefaultModelGateway


def request() -> ModelRequest:
    return ModelRequest(
        request_id="req-contract",
        messages=(Message(MessageRole.USER, (TextPart("hello"),)),),
        max_output_tokens=32,
    )


def target(provider_id: str = "fake") -> ModelTarget:
    return ModelTarget(
        provider=ProviderId(provider_id),
        model_id="contract-model",
        credential=None,
        capabilities=ModelCapabilityProfile(),
        limits=ModelLimits(max_retries=0),
    )


@pytest.mark.asyncio
async def test_fake_provider_replays_complete_and_stream_scripts() -> None:
    response = ProviderResponse(
        content=(TextPart("hello back"),),
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
        provider_request_id="fake-1",
    )
    chunks = (
        ProviderChunk(text_delta="hello "),
        ProviderChunk(text_delta="back"),
        ProviderChunk(
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            provider_request_id="fake-2",
        ),
    )
    provider = FakeProvider(FakeScript(completions=(response,), streams=(chunks,)))

    actual = await provider.complete(target(), request(), None)
    streamed = [chunk async for chunk in provider.stream(target(), request(), None)]

    assert provider.provider_id == ProviderId("fake")
    assert actual == response
    assert streamed == list(chunks)
    assert provider.complete_calls == 1
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_fake_provider_is_concurrency_safe() -> None:
    responses = tuple(
        ProviderResponse(content=(TextPart(str(index)),), finish_reason=FinishReason.STOP)
        for index in range(20)
    )
    provider = FakeProvider(FakeScript(completions=responses))
    actual = await asyncio.gather(
        *(provider.complete(target(), request(), None) for _ in responses),
    )
    assert sorted(part.text for response in actual for part in response.content) == sorted(
        str(index) for index in range(20)
    )


class ThirdPartyTestProvider:
    @property
    def provider_id(self) -> ProviderId:
        return ProviderId("third-party")

    async def complete(
        self,
        model_target: ModelTarget,
        model_request: ModelRequest,
        credential: object,
    ) -> ProviderResponse:
        del model_target, model_request, credential
        return ProviderResponse(
            content=(TextPart("extension works"),), finish_reason=FinishReason.STOP
        )

    async def stream(
        self,
        model_target: ModelTarget,
        model_request: ModelRequest,
        credential: object,
    ) -> AsyncIterator[ProviderChunk]:
        del model_target, model_request, credential
        yield ProviderChunk(text_delta="extension works")
        yield ProviderChunk(finish_reason=FinishReason.STOP)


@pytest.mark.asyncio
async def test_third_provider_registers_without_modifying_platform_contracts() -> None:
    provider = ThirdPartyTestProvider()
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )

    response = await gateway.complete(ModelRoute(target("third-party")), request())

    assert response.provider == ProviderId("third-party")
    assert response.content == (TextPart("extension works"),)
