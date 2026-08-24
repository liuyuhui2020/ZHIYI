from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

import pytest
from pydantic import BaseModel

from zhiyi.adapters.models.fake import FakeProvider, FakeScript
from zhiyi.adapters.models.structured_output import PydanticOutputContract
from zhiyi.adapters.models.token_estimator import ConservativeTokenEstimator
from zhiyi.adapters.secrets.environment import EnvironmentSecretProvider
from zhiyi.application.models.contracts import (
    AttemptOutcome,
    AttemptRecord,
    ChunkKind,
    ErrorCode,
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilityProfile,
    ModelChunk,
    ModelError,
    ModelLimits,
    ModelRequest,
    ModelRoute,
    ModelTarget,
    ModelUsage,
    ProviderId,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolDefinition,
)
from zhiyi.application.ports.model_provider import ProviderChunk, ProviderResponse
from zhiyi.application.ports.secret_provider import SecretReference, SecretValue
from zhiyi.application.services.model_gateway import DefaultModelGateway


class StructuredAnswer(BaseModel):
    answer: str


async def no_sleep(delay: float) -> None:
    del delay


class CredentialRecordingProvider:
    def __init__(self, provider_id: str) -> None:
        self._provider_id = ProviderId(provider_id)
        self.observed: list[tuple[str, str]] = []

    @property
    def provider_id(self) -> ProviderId:
        return self._provider_id

    async def complete(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse:
        del target
        await asyncio.sleep(0)
        assert credential is not None
        self.observed.append((model_request.request_id, credential.reveal()))
        return ProviderResponse(
            content=(TextPart("ok"),),
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    async def stream(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]:
        response = await self.complete(target, model_request, credential)
        yield ProviderChunk(text_delta=response.content[0].text)
        yield ProviderChunk(
            usage=response.usage,
            finish_reason=response.finish_reason,
        )


class CloseAwareProvider:
    def __init__(self) -> None:
        self.closed = False

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId("close-aware")

    async def complete(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse:
        del target, model_request, credential
        return ProviderResponse(content=(TextPart("ok"),), finish_reason=FinishReason.STOP)

    async def stream(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]:
        del target, model_request, credential
        try:
            yield ProviderChunk(text_delta="visible")
            await asyncio.Event().wait()
        finally:
            self.closed = True


class IsolationProvider:
    def __init__(self, provider_id: str) -> None:
        self._provider_id = ProviderId(provider_id)
        self.credentials: list[str] = []

    @property
    def provider_id(self) -> ProviderId:
        return self._provider_id

    async def complete(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse:
        del target
        await asyncio.sleep(0)
        assert credential is not None
        self.credentials.append(credential.reveal())
        text = model_request.messages[-1].content[0]
        assert isinstance(text, TextPart)
        index = int(model_request.request_id.rsplit("-", 1)[-1])
        return ProviderResponse(
            content=(TextPart(f"{model_request.request_id}:{text.text}"),),
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(
                input_tokens=index + 1,
                output_tokens=1,
                total_tokens=index + 2,
            ),
        )

    async def stream(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]:
        del target
        assert credential is not None
        self.credentials.append(credential.reveal())
        text = model_request.messages[-1].content[0]
        assert isinstance(text, TextPart)
        index = int(model_request.request_id.rsplit("-", 1)[-1])
        arguments = json.dumps({"value": text.text}, separators=(",", ":"))
        split_at = len(arguments) // 2
        yield ProviderChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                id=f"call-{index}",
                name="echo",
                arguments_fragment=arguments[:split_at],
            )
        )
        await asyncio.sleep(0)
        yield ProviderChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                arguments_fragment=arguments[split_at:],
            )
        )
        yield ProviderChunk(
            usage=ModelUsage(
                input_tokens=index + 1,
                output_tokens=1,
                total_tokens=index + 2,
            ),
            finish_reason=FinishReason.TOOL_CALLS,
        )


class SensitiveFailureProvider(IsolationProvider):
    async def complete(
        self,
        target: ModelTarget,
        model_request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse:
        del target
        assert credential is not None
        text = model_request.messages[-1].content[0]
        assert isinstance(text, TextPart)
        raise RuntimeError(f"raw failure {text.text} {credential.reveal()}")


def target(
    *,
    context_tokens: int = 8_192,
    provider_id: str = "fake",
    limits: ModelLimits | None = None,
) -> ModelTarget:
    return ModelTarget(
        provider=ProviderId(provider_id),
        model_id="gateway-test",
        credential=None,
        capabilities=ModelCapabilityProfile(
            max_context_tokens=context_tokens,
            max_output_tokens=min(1_024, context_tokens),
        ),
        limits=limits or ModelLimits(max_retries=0),
    )


def request(*, text: str = "hello", max_output_tokens: int = 32) -> ModelRequest:
    return ModelRequest(
        request_id="req-gateway",
        messages=(Message(MessageRole.USER, (TextPart(text),)),),
        max_output_tokens=max_output_tokens,
    )


def tool_target() -> ModelTarget:
    base = target()
    return ModelTarget(
        provider=base.provider,
        model_id=base.model_id,
        credential=None,
        capabilities=ModelCapabilityProfile(
            tool_calling=True,
            structured_output=True,
            max_context_tokens=8_192,
            max_output_tokens=1_024,
        ),
        limits=base.limits,
    )


def tool_request() -> ModelRequest:
    return ModelRequest(
        request_id="req-tool-gateway",
        messages=(Message(MessageRole.USER, (TextPart("weather"),)),),
        max_output_tokens=64,
        tools=(
            ToolDefinition(
                name="lookup_weather",
                description="Look up weather",
                input_schema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
        ),
    )


@pytest.mark.asyncio
async def test_complete_returns_platform_response_and_attempt_usage() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(
                    content=(TextPart("answer"),),
                    finish_reason=FinishReason.STOP,
                    usage=ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                    provider_request_id="provider-1",
                ),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )
    response = await gateway.complete(ModelRoute(target()), request())

    assert response.content == (TextPart("answer"),)
    assert response.provider == ProviderId("fake")
    assert response.attempts[0].usage is not None
    assert response.total_usage.total_tokens == 3


@pytest.mark.asyncio
async def test_stream_has_ordered_deltas_usage_and_single_terminal() -> None:
    usage = ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4)
    provider = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(text_delta="hel"),
                    ProviderChunk(text_delta="lo"),
                    ProviderChunk(usage=usage),
                    ProviderChunk(finish_reason=FinishReason.STOP, provider_request_id="stream-1"),
                ),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )
    chunks = [chunk async for chunk in gateway.stream(ModelRoute(target()), request())]

    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.text_delta for chunk in chunks if chunk.kind is ChunkKind.TEXT_DELTA] == [
        "hel",
        "lo",
    ]
    assert sum(chunk.kind is ChunkKind.TERMINAL for chunk in chunks) == 1
    terminal = chunks[-1].terminal
    assert terminal is not None and terminal.response.content == (TextPart("hello"),)


@pytest.mark.asyncio
async def test_unregistered_or_over_capacity_target_fails_before_provider_call() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(content=(TextPart("unused"),), finish_reason=FinishReason.STOP),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )
    unknown = ModelTarget(
        provider=ProviderId("custom"),
        model_id="unknown",
        credential=None,
        capabilities=target().capabilities,
    )
    with pytest.raises(ModelError) as unregistered:
        await gateway.complete(ModelRoute(unknown), request())
    assert unregistered.value.code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ModelError) as capacity:
        await gateway.complete(ModelRoute(target(context_tokens=64)), request(text="x" * 100))
    assert capacity.value.code is ErrorCode.CAPABILITY_MISMATCH
    assert provider.complete_calls == 0


@pytest.mark.asyncio
async def test_complete_rejects_unknown_or_duplicate_tool_calls() -> None:
    unknown = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(
                    finish_reason=FinishReason.TOOL_CALLS,
                    tool_calls=(ToolCall("call-1", "delete_everything", {}),),
                ),
                ProviderResponse(
                    finish_reason=FinishReason.TOOL_CALLS,
                    tool_calls=(
                        ToolCall("call-2", "lookup_weather", {"city": "Shanghai"}),
                        ToolCall("call-2", "lookup_weather", {"city": "Beijing"}),
                    ),
                ),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(unknown,), token_estimator=ConservativeTokenEstimator()
    )

    with pytest.raises(ModelError) as caught:
        await gateway.complete(ModelRoute(tool_target()), tool_request())

    assert caught.value.code is ErrorCode.MALFORMED_RESPONSE

    with pytest.raises(ModelError) as duplicate:
        await gateway.complete(ModelRoute(tool_target()), tool_request())

    assert duplicate.value.code is ErrorCode.MALFORMED_RESPONSE


@pytest.mark.asyncio
async def test_stream_assembles_tool_fragments_into_verified_terminal_call() -> None:
    provider = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(
                        tool_call_delta=ToolCallDelta(
                            index=0,
                            id="call-1",
                            name="lookup_weather",
                            arguments_fragment='{"city":',
                        )
                    ),
                    ProviderChunk(
                        tool_call_delta=ToolCallDelta(index=0, arguments_fragment='"Shanghai"}')
                    ),
                    ProviderChunk(finish_reason=FinishReason.TOOL_CALLS),
                ),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )

    chunks = [chunk async for chunk in gateway.stream(ModelRoute(tool_target()), tool_request())]
    terminal = chunks[-1].terminal

    assert terminal is not None
    assert terminal.response.tool_calls == (
        ToolCall("call-1", "lookup_weather", {"city": "Shanghai"}),
    )


@pytest.mark.asyncio
async def test_stream_rejects_incomplete_tool_json_after_visible_delta() -> None:
    provider = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(
                        tool_call_delta=ToolCallDelta(
                            index=0,
                            id="call-1",
                            name="lookup_weather",
                            arguments_fragment='{"city":',
                        )
                    ),
                    ProviderChunk(finish_reason=FinishReason.TOOL_CALLS),
                ),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )

    chunks = [chunk async for chunk in gateway.stream(ModelRoute(tool_target()), tool_request())]

    assert chunks[-1].kind is ChunkKind.ERROR
    assert chunks[-1].error is not None
    assert chunks[-1].error.code is ErrorCode.MALFORMED_RESPONSE


@pytest.mark.asyncio
async def test_stream_validates_structured_output_before_terminal() -> None:
    provider = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(structured_output={"answer": "verified"}),
                    ProviderChunk(finish_reason=FinishReason.STOP),
                ),
                (
                    ProviderChunk(structured_output={"answer": 7}),
                    ProviderChunk(finish_reason=FinishReason.STOP),
                ),
            )
        )
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )
    structured_request = ModelRequest(
        request_id="req-structured-gateway",
        messages=(Message(MessageRole.USER, (TextPart("answer"),)),),
        max_output_tokens=32,
        structured_output=PydanticOutputContract(StructuredAnswer),
    )

    chunks = [
        chunk async for chunk in gateway.stream(ModelRoute(tool_target()), structured_request)
    ]
    assert chunks[-1].terminal is not None
    assert chunks[-1].terminal.response.structured_output == {"answer": "verified"}

    with pytest.raises(ModelError) as invalid:
        _ = [chunk async for chunk in gateway.stream(ModelRoute(tool_target()), structured_request)]
    assert invalid.value.code is ErrorCode.STRUCTURED_OUTPUT_INVALID


@pytest.mark.asyncio
async def test_complete_retries_transient_failure_and_aggregates_attempt_usage() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=(
                ModelError(
                    code=ErrorCode.UNAVAILABLE,
                    message="temporary",
                    request_id="req-gateway",
                    usage=ModelUsage(input_tokens=2, output_tokens=0, total_tokens=2),
                ),
                ProviderResponse(
                    content=(TextPart("recovered"),),
                    finish_reason=FinishReason.STOP,
                    usage=ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                ),
            )
        )
    )
    limits = ModelLimits(max_retries=1)
    gateway = DefaultModelGateway(
        providers=(provider,),
        token_estimator=ConservativeTokenEstimator(),
        sleeper=no_sleep,
        random_source=lambda: 0.5,
    )

    response = await gateway.complete(ModelRoute(target(limits=limits)), request())

    assert provider.complete_calls == 2
    assert len(response.attempts) == 2
    assert response.total_usage.total_tokens == 5


@pytest.mark.asyncio
async def test_complete_falls_back_after_retry_budget_and_records_reason() -> None:
    primary = FakeProvider(
        FakeScript(
            completions=(
                ModelError(
                    code=ErrorCode.RATE_LIMITED,
                    message="busy",
                    request_id="req-gateway",
                    usage=ModelUsage(input_tokens=2, output_tokens=0, total_tokens=2),
                ),
            )
        ),
        provider_id=ProviderId("primary"),
    )
    fallback = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(
                    content=(TextPart("fallback"),),
                    finish_reason=FinishReason.STOP,
                    usage=ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                ),
            )
        ),
        provider_id=ProviderId("secondary"),
    )
    gateway = DefaultModelGateway(
        providers=(primary, fallback),
        token_estimator=ConservativeTokenEstimator(),
        sleeper=no_sleep,
    )
    route = ModelRoute(
        target(provider_id="primary"),
        fallbacks=(target(provider_id="secondary"),),
    )

    response = await gateway.complete(route, request())

    assert response.provider == ProviderId("secondary")
    assert [attempt.provider for attempt in response.attempts] == [
        ProviderId("primary"),
        ProviderId("secondary"),
    ]
    assert response.attempts[-1].fallback_reason is ErrorCode.RATE_LIMITED
    assert response.total_usage.total_tokens == 5


@pytest.mark.asyncio
async def test_authentication_failure_never_retries_or_falls_back() -> None:
    primary = FakeProvider(
        FakeScript(
            completions=(
                ModelError(
                    code=ErrorCode.AUTHENTICATION,
                    message="denied",
                    request_id="req-gateway",
                ),
            )
        ),
        provider_id=ProviderId("primary"),
    )
    fallback = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(
                    content=(TextPart("must not run"),), finish_reason=FinishReason.STOP
                ),
            )
        ),
        provider_id=ProviderId("secondary"),
    )
    gateway = DefaultModelGateway(
        providers=(primary, fallback), token_estimator=ConservativeTokenEstimator()
    )
    route = ModelRoute(
        target(provider_id="primary", limits=ModelLimits(max_retries=3)),
        fallbacks=(target(provider_id="secondary"),),
    )

    with pytest.raises(ModelError) as caught:
        await gateway.complete(route, request())

    assert caught.value.code is ErrorCode.AUTHENTICATION
    assert primary.complete_calls == 1
    assert fallback.complete_calls == 0


@pytest.mark.asyncio
async def test_stream_retries_only_before_first_visible_delta() -> None:
    provider = FakeProvider(
        FakeScript(
            streams=(
                (
                    ModelError(
                        code=ErrorCode.UNAVAILABLE,
                        message="before first delta",
                        request_id="req-gateway",
                    ),
                ),
                (
                    ProviderChunk(text_delta="recovered"),
                    ProviderChunk(finish_reason=FinishReason.STOP),
                ),
                (
                    ProviderChunk(text_delta="visible"),
                    ModelError(
                        code=ErrorCode.UNAVAILABLE,
                        message="after first delta",
                        request_id="req-gateway",
                    ),
                ),
            )
        )
    )
    limits = ModelLimits(max_retries=1)
    gateway = DefaultModelGateway(
        providers=(provider,),
        token_estimator=ConservativeTokenEstimator(),
        sleeper=no_sleep,
    )

    recovered = [
        chunk async for chunk in gateway.stream(ModelRoute(target(limits=limits)), request())
    ]
    interrupted = [
        chunk async for chunk in gateway.stream(ModelRoute(target(limits=limits)), request())
    ]

    assert recovered[-1].kind is ChunkKind.TERMINAL
    assert interrupted[-1].kind is ChunkKind.ERROR
    assert provider.stream_calls == 3


@pytest.mark.asyncio
async def test_cancellation_stops_retry_chain() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(content=(TextPart("late"),), finish_reason=FinishReason.STOP),
            ),
            delay_seconds=1,
        )
    )
    attempts: list[AttemptRecord] = []
    gateway = DefaultModelGateway(
        providers=(provider,),
        token_estimator=ConservativeTokenEstimator(),
        attempt_sink=attempts.append,
    )
    task = asyncio.create_task(
        gateway.complete(ModelRoute(target(limits=ModelLimits(max_retries=3))), request())
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert provider.complete_calls == 1
    assert len(attempts) == 1
    assert attempts[0].outcome is AttemptOutcome.CANCELLED
    assert attempts[0].error_code is ErrorCode.CANCELLED


@pytest.mark.asyncio
async def test_stream_cancellation_and_early_close_emit_one_cancelled_attempt() -> None:
    waiting = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(text_delta="late"),
                    ProviderChunk(finish_reason=FinishReason.STOP),
                ),
            ),
            delay_seconds=1,
        )
    )
    waiting_attempts: list[AttemptRecord] = []
    waiting_gateway = DefaultModelGateway(
        providers=(waiting,),
        token_estimator=ConservativeTokenEstimator(),
        attempt_sink=waiting_attempts.append,
    )
    waiting_stream = waiting_gateway.stream(ModelRoute(target()), request())
    waiting_task = asyncio.ensure_future(anext(waiting_stream))
    await asyncio.sleep(0)
    waiting_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_task

    assert len(waiting_attempts) == 1
    assert waiting_attempts[0].outcome is AttemptOutcome.CANCELLED

    close_aware = CloseAwareProvider()
    close_attempts: list[AttemptRecord] = []
    close_gateway = DefaultModelGateway(
        providers=(close_aware,),
        token_estimator=ConservativeTokenEstimator(),
        attempt_sink=close_attempts.append,
    )
    close_target = ModelTarget(
        provider=ProviderId("close-aware"),
        model_id="close-model",
        credential=None,
        capabilities=target().capabilities,
        limits=ModelLimits(max_retries=2),
    )
    close_stream = close_gateway.stream(ModelRoute(close_target), request())

    first = await anext(close_stream)
    await cast(AsyncGenerator[ModelChunk, None], close_stream).aclose()

    assert first.kind is ChunkKind.TEXT_DELTA
    assert close_aware.closed is True
    assert len(close_attempts) == 1
    assert close_attempts[0].outcome is AttemptOutcome.CANCELLED


@pytest.mark.asyncio
async def test_circuit_opens_and_short_circuits_subsequent_call() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=(
                ModelError(
                    code=ErrorCode.UNAVAILABLE,
                    message="down",
                    request_id="req-gateway",
                ),
                ProviderResponse(
                    content=(TextPart("must wait for recovery"),),
                    finish_reason=FinishReason.STOP,
                ),
            )
        )
    )
    limits = ModelLimits(max_retries=0, circuit_failure_threshold=1)
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )

    with pytest.raises(ModelError) as first:
        await gateway.complete(ModelRoute(target(limits=limits)), request())
    with pytest.raises(ModelError) as second:
        await gateway.complete(ModelRoute(target(limits=limits)), request())

    assert first.value.code is ErrorCode.UNAVAILABLE
    assert second.value.code is ErrorCode.CIRCUIT_OPEN
    assert provider.complete_calls == 1


@pytest.mark.asyncio
async def test_attempt_timeout_retries_only_to_configured_hard_limit() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=(
                ProviderResponse(content=(TextPart("late-1"),), finish_reason=FinishReason.STOP),
                ProviderResponse(content=(TextPart("late-2"),), finish_reason=FinishReason.STOP),
            ),
            delay_seconds=0.05,
        )
    )
    limits = ModelLimits(
        attempt_timeout_seconds=0.005,
        max_retries=1,
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.001,
        circuit_failure_threshold=10,
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )

    with pytest.raises(ModelError) as caught:
        await gateway.complete(
            ModelRoute(target(limits=limits), total_timeout_seconds=0.1), request()
        )

    assert caught.value.code is ErrorCode.TIMEOUT
    assert len(caught.value.attempts) == 2
    assert provider.complete_calls == 2


@pytest.mark.asyncio
async def test_route_deadline_includes_retry_backoff() -> None:
    provider = FakeProvider(
        FakeScript(
            completions=tuple(
                ModelError(
                    code=ErrorCode.UNAVAILABLE,
                    message="still down",
                    request_id="req-gateway",
                )
                for _ in range(5)
            )
        )
    )
    limits = ModelLimits(
        max_retries=4,
        retry_base_delay_seconds=0.05,
        retry_max_delay_seconds=0.05,
        circuit_failure_threshold=10,
    )
    gateway = DefaultModelGateway(
        providers=(provider,), token_estimator=ConservativeTokenEstimator()
    )

    with pytest.raises(ModelError) as caught:
        await gateway.complete(
            ModelRoute(target(limits=limits), total_timeout_seconds=0.01), request()
        )

    assert caught.value.code is ErrorCode.TIMEOUT
    assert provider.complete_calls == 1


@pytest.mark.asyncio
async def test_stream_first_block_and_idle_timeouts_are_independent() -> None:
    first_block = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(text_delta="late"),
                    ProviderChunk(finish_reason=FinishReason.STOP),
                ),
            ),
            delay_seconds=0.02,
        )
    )
    first_limits = ModelLimits(
        max_retries=0,
        stream_first_byte_timeout_seconds=0.005,
        stream_idle_timeout_seconds=1,
    )
    first_gateway = DefaultModelGateway(
        providers=(first_block,), token_estimator=ConservativeTokenEstimator()
    )

    with pytest.raises(ModelError) as first_error:
        _ = [
            chunk
            async for chunk in first_gateway.stream(
                ModelRoute(target(limits=first_limits)), request()
            )
        ]
    assert first_error.value.code is ErrorCode.TIMEOUT

    idle = FakeProvider(
        FakeScript(
            streams=(
                (
                    ProviderChunk(text_delta="visible"),
                    ProviderChunk(text_delta="late"),
                ),
            ),
            delay_seconds=0.02,
        )
    )
    idle_limits = ModelLimits(
        max_retries=0,
        stream_first_byte_timeout_seconds=0.1,
        stream_idle_timeout_seconds=0.005,
    )
    idle_gateway = DefaultModelGateway(
        providers=(idle,), token_estimator=ConservativeTokenEstimator()
    )
    chunks = [
        chunk
        async for chunk in idle_gateway.stream(ModelRoute(target(limits=idle_limits)), request())
    ]

    assert chunks[0].kind is ChunkKind.TEXT_DELTA
    assert chunks[-1].kind is ChunkKind.ERROR
    assert chunks[-1].error is not None
    assert chunks[-1].error.code is ErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_incompatible_fallback_is_rejected_before_primary_call() -> None:
    primary = FakeProvider(provider_id=ProviderId("primary"))
    secondary = FakeProvider(provider_id=ProviderId("secondary"))
    gateway = DefaultModelGateway(
        providers=(primary, secondary), token_estimator=ConservativeTokenEstimator()
    )
    incompatible = target(provider_id="secondary")
    compatible_primary = ModelTarget(
        provider=ProviderId("primary"),
        model_id="gateway-test",
        credential=None,
        capabilities=tool_target().capabilities,
        limits=ModelLimits(max_retries=0),
    )

    with pytest.raises(ModelError) as caught:
        await gateway.complete(
            ModelRoute(compatible_primary, fallbacks=(incompatible,)), tool_request()
        )

    assert caught.value.code is ErrorCode.CAPABILITY_MISMATCH
    assert primary.complete_calls == 0
    assert secondary.complete_calls == 0


@pytest.mark.asyncio
async def test_attempt_hook_contains_only_safe_summary_fields() -> None:
    sensitive_prompt = "prompt-sentinel-never-log"
    sensitive_secret = "secret-sentinel-never-log"
    provider = CredentialRecordingProvider("secure")
    secret_provider = EnvironmentSecretProvider(
        allowed_references=(SecretReference("SECURE_KEY"),),
        environment={"SECURE_KEY": sensitive_secret},
    )
    attempts: list[AttemptRecord] = []
    gateway = DefaultModelGateway(
        providers=(provider,),
        token_estimator=ConservativeTokenEstimator(),
        secret_provider=secret_provider,
        attempt_sink=attempts.append,
    )
    secure_target = ModelTarget(
        provider=ProviderId("secure"),
        model_id="safe-model",
        credential=SecretReference("SECURE_KEY"),
        capabilities=target().capabilities,
        limits=ModelLimits(max_retries=0),
    )

    response = await gateway.complete(ModelRoute(secure_target), request(text=sensitive_prompt))
    public_text = repr((response, attempts))

    assert sensitive_prompt not in public_text
    assert sensitive_secret not in public_text
    assert attempts[0].provider == ProviderId("secure")
    assert attempts[0].usage is not None


@pytest.mark.asyncio
async def test_one_thousand_mixed_target_calls_keep_credentials_isolated() -> None:
    alpha = IsolationProvider("alpha")
    beta = IsolationProvider("beta")
    secret_provider = EnvironmentSecretProvider(
        allowed_references=(
            SecretReference("ALPHA_KEY"),
            SecretReference("BETA_KEY"),
        ),
        environment={"ALPHA_KEY": "alpha-sentinel", "BETA_KEY": "beta-sentinel"},
    )
    gateway = DefaultModelGateway(
        providers=(alpha, beta),
        token_estimator=ConservativeTokenEstimator(),
        secret_provider=secret_provider,
    )

    def secure_target(provider_id: str, reference: str) -> ModelTarget:
        return ModelTarget(
            provider=ProviderId(provider_id),
            model_id=f"{provider_id}-model",
            credential=SecretReference(reference),
            capabilities=tool_target().capabilities,
            limits=ModelLimits(max_retries=0),
        )

    alpha_route = ModelRoute(secure_target("alpha", "ALPHA_KEY"))
    beta_route = ModelRoute(secure_target("beta", "BETA_KEY"))

    async def invoke(index: int) -> None:
        route = alpha_route if index % 2 == 0 else beta_route
        payload = f"message-{index}"
        base_request = ModelRequest(
            request_id=f"concurrent-{index}",
            messages=(Message(MessageRole.USER, (TextPart(payload),)),),
            max_output_tokens=8,
        )
        if index % 4 < 2:
            response = await gateway.complete(route, base_request)
            assert response.content == (TextPart(f"concurrent-{index}:{payload}"),)
            assert response.total_usage.total_tokens == index + 2
            return

        stream_request = ModelRequest(
            request_id=base_request.request_id,
            messages=base_request.messages,
            max_output_tokens=base_request.max_output_tokens,
            tools=(
                ToolDefinition(
                    name="echo",
                    description="Echo one value",
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                ),
            ),
        )
        chunks = [chunk async for chunk in gateway.stream(route, stream_request)]
        terminal = chunks[-1].terminal
        assert terminal is not None
        assert terminal.response.tool_calls == (
            ToolCall(f"call-{index}", "echo", {"value": payload}),
        )
        assert terminal.response.total_usage.total_tokens == index + 2

    results = await asyncio.gather(*(invoke(index) for index in range(1_000)))

    assert results == [None] * 1_000
    assert len(alpha.credentials) == 500
    assert len(beta.credentials) == 500
    assert set(alpha.credentials) == {"alpha-sentinel"}
    assert set(beta.credentials) == {"beta-sentinel"}


@pytest.mark.asyncio
async def test_sensitive_provider_failure_never_reaches_error_or_attempt_hook() -> None:
    prompt = "prompt-never-leak"
    secret = "secret-never-leak"
    provider = SensitiveFailureProvider("sensitive")
    attempts: list[AttemptRecord] = []
    reference = SecretReference("SENSITIVE_KEY")
    gateway = DefaultModelGateway(
        providers=(provider,),
        token_estimator=ConservativeTokenEstimator(),
        secret_provider=EnvironmentSecretProvider(
            allowed_references=(reference,), environment={reference.name: secret}
        ),
        attempt_sink=attempts.append,
    )
    sensitive_target = ModelTarget(
        provider=provider.provider_id,
        model_id="sensitive-model",
        credential=reference,
        capabilities=target().capabilities,
        limits=ModelLimits(max_retries=0),
    )

    with pytest.raises(ModelError) as caught:
        await gateway.complete(ModelRoute(sensitive_target), request(text=prompt))

    public = repr((caught.value, attempts)) + str(caught.value)
    assert caught.value.code is ErrorCode.UNKNOWN
    assert prompt not in public
    assert secret not in public
    assert len(attempts) == 1
