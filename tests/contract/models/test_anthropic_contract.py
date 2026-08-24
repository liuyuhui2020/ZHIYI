from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel

from zhiyi.adapters.models.anthropic import AnthropicProvider
from zhiyi.adapters.models.structured_output import PydanticOutputContract
from zhiyi.application.models.contracts import (
    ErrorCode,
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilityProfile,
    ModelLimits,
    ModelRequest,
    ModelTarget,
    ProviderId,
    TextPart,
    ToolDefinition,
)
from zhiyi.application.ports.secret_provider import SecretReference, SecretValue


class StubChatModel:
    async def ainvoke(self, messages: object, **kwargs: object) -> object:
        del messages, kwargs
        return AIMessage(
            content=[
                {"type": "thinking", "thinking": "private reasoning"},
                {"type": "text", "text": "anthropic answer"},
            ],
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            response_metadata={"stop_reason": "end_turn", "request_id": "anthropic-req"},
        )

    async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[AIMessageChunk]:
        del messages, kwargs
        yield AIMessageChunk(content=[{"type": "text", "text": "anthropic "}])
        yield AIMessageChunk(
            content=[{"type": "text", "text": "answer"}],
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            response_metadata={"stop_reason": "end_turn", "request_id": "anthropic-stream"},
        )

    def bind_tools(self, tools: object, **kwargs: object) -> StubChatModel:
        del tools, kwargs
        return self

    def with_structured_output(self, schema: object, **kwargs: object) -> StubChatModel:
        del schema, kwargs
        return self


class ToolChatModel(StubChatModel):
    def __init__(self) -> None:
        self.bound_tools: object | None = None
        self.bind_kwargs: dict[str, object] = {}

    def bind_tools(self, tools: object, **kwargs: object) -> ToolChatModel:
        self.bound_tools = tools
        self.bind_kwargs = kwargs
        return self

    async def ainvoke(self, messages: object, **kwargs: object) -> AIMessage:
        del messages, kwargs
        return AIMessage(
            content=[{"type": "thinking", "thinking": "never return this"}],
            tool_calls=[
                {
                    "id": "toolu-1",
                    "name": "lookup_weather",
                    "args": {"city": "Shanghai"},
                    "type": "tool_call",
                }
            ],
            response_metadata={"stop_reason": "tool_use"},
        )

    async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[AIMessageChunk]:
        del messages, kwargs
        yield AIMessageChunk(
            content=[{"type": "thinking", "thinking": "private"}],
            tool_call_chunks=[
                {
                    "id": "toolu-1",
                    "name": "lookup_weather",
                    "args": '{"city":',
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        )
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "id": None,
                    "name": None,
                    "args": '"Shanghai"}',
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
            response_metadata={"stop_reason": "tool_use"},
        )


class StructuredAnswer(BaseModel):
    answer: str


class StructuredChatModel(StubChatModel):
    def __init__(self, *, parsing_error: object | None = None) -> None:
        self.structured_kwargs: dict[str, object] = {}
        self.parsing_error = parsing_error

    def with_structured_output(self, schema: object, **kwargs: object) -> StructuredChatModel:
        del schema
        self.structured_kwargs = kwargs
        return self

    async def ainvoke(self, messages: object, **kwargs: object) -> object:
        del messages, kwargs
        return {
            "raw": AIMessage(content="", response_metadata={"stop_reason": "end_turn"}),
            "parsed": {"answer": "verified"},
            "parsing_error": self.parsing_error,
        }


def target() -> ModelTarget:
    return ModelTarget(
        provider=ProviderId("anthropic"),
        model_id="claude-contract",
        credential=SecretReference("ANTHROPIC_API_KEY"),
        capabilities=ModelCapabilityProfile(max_context_tokens=8_192, max_output_tokens=1_024),
        limits=ModelLimits(max_retries=0),
    )


def request() -> ModelRequest:
    return ModelRequest(
        request_id="req-anthropic",
        messages=(Message(MessageRole.USER, (TextPart("hello"),)),),
        max_output_tokens=32,
    )


@pytest.mark.asyncio
async def test_anthropic_complete_and_stream_follow_platform_contract() -> None:
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> StubChatModel:
        captured.update(kwargs)
        return StubChatModel()

    provider = AnthropicProvider(model_factory=factory)
    response = await provider.complete(target(), request(), SecretValue("sentinel-anthropic"))
    chunks = [
        chunk
        async for chunk in provider.stream(target(), request(), SecretValue("sentinel-anthropic"))
    ]

    assert response.content[0].text == "anthropic answer"
    assert "private reasoning" not in repr(response)
    assert response.finish_reason is FinishReason.STOP
    assert chunks[-1].finish_reason is FinishReason.STOP
    assert captured["max_retries"] == 0
    assert captured["api_key"] == "sentinel-anthropic"
    assert provider.provider_id == ProviderId("anthropic")


@pytest.mark.asyncio
async def test_anthropic_tool_fragments_are_portable_and_reasoning_is_filtered() -> None:
    model = ToolChatModel()
    provider = AnthropicProvider(model_factory=lambda **_: model)
    tool_request = ModelRequest(
        request_id="req-anthropic-tool",
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
                    "additionalProperties": False,
                },
            ),
        ),
    )

    response = await provider.complete(target(), tool_request, SecretValue("sentinel-anthropic"))
    chunks = [
        chunk
        async for chunk in provider.stream(
            target(), tool_request, SecretValue("sentinel-anthropic")
        )
    ]

    assert response.tool_calls[0].arguments["city"] == "Shanghai"
    assert "never return this" not in repr(response)
    assert "private" not in repr(chunks)
    assert model.bind_kwargs == {"strict": True}
    assert (
        "".join(
            chunk.tool_call_delta.arguments_fragment
            for chunk in chunks
            if chunk.tool_call_delta is not None
        )
        == '{"city":"Shanghai"}'
    )


@pytest.mark.asyncio
async def test_anthropic_structured_output_uses_native_json_schema_and_safe_failure() -> None:
    model = StructuredChatModel()
    provider = AnthropicProvider(model_factory=lambda **_: model)
    structured_request = ModelRequest(
        request_id="req-anthropic-structured",
        messages=(Message(MessageRole.USER, (TextPart("answer"),)),),
        max_output_tokens=32,
        structured_output=PydanticOutputContract(StructuredAnswer),
    )

    response = await provider.complete(
        target(), structured_request, SecretValue("sentinel-anthropic")
    )
    assert response.structured_output == {"answer": "verified"}
    assert model.structured_kwargs == {"include_raw": True, "method": "json_schema"}

    invalid = AnthropicProvider(
        model_factory=lambda **_: StructuredChatModel(
            parsing_error=ValueError("never echo raw output")
        )
    )
    with pytest.raises(Exception, match="structured_output_invalid") as caught:
        await invalid.complete(target(), structured_request, SecretValue("sentinel-anthropic"))
    assert "never echo raw output" not in str(caught.value)


def anthropic_response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", "https://api.anthropic.test/v1/messages"),
        headers={"request-id": "anthropic-safe-request-id"},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            anthropic.AuthenticationError(
                "raw-auth-sentinel", response=anthropic_response(401), body={}
            ),
            ErrorCode.AUTHENTICATION,
        ),
        (
            anthropic.PermissionDeniedError(
                "raw-permission-sentinel", response=anthropic_response(403), body={}
            ),
            ErrorCode.PERMISSION,
        ),
        (
            anthropic.RateLimitError(
                "raw-rate-sentinel", response=anthropic_response(429), body={}
            ),
            ErrorCode.RATE_LIMITED,
        ),
        (
            anthropic.APITimeoutError(httpx.Request("POST", "https://api.anthropic.test")),
            ErrorCode.TIMEOUT,
        ),
        (
            anthropic.APIConnectionError(
                message="raw-connection-sentinel",
                request=httpx.Request("POST", "https://api.anthropic.test"),
            ),
            ErrorCode.UNAVAILABLE,
        ),
        (
            anthropic.InternalServerError(
                "raw-server-sentinel", response=anthropic_response(500), body={}
            ),
            ErrorCode.UNAVAILABLE,
        ),
        (
            anthropic.OverloadedError(
                "raw-overload-sentinel", response=anthropic_response(529), body={}
            ),
            ErrorCode.UNAVAILABLE,
        ),
        (
            anthropic.BadRequestError(
                "raw-input-sentinel", response=anthropic_response(400), body={}
            ),
            ErrorCode.INVALID_REQUEST,
        ),
        (
            anthropic.BadRequestError(
                "raw-policy-sentinel",
                response=anthropic_response(400),
                body={"error": {"type": "content_policy_violation"}},
            ),
            ErrorCode.CONTENT_POLICY,
        ),
        (RuntimeError("raw-unknown-sentinel"), ErrorCode.UNKNOWN),
    ],
)
def test_anthropic_exception_matrix_is_stable_and_redacted(
    error: Exception, expected: ErrorCode
) -> None:
    mapped = AnthropicProvider()._map_exception(error, "platform-request")

    assert mapped.code is expected
    assert mapped.retryable is (
        expected in {ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE}
    )
    assert mapped.fallback_allowed is mapped.retryable
    assert "sentinel" not in str(mapped)
    assert "sentinel" not in repr(mapped)
    if isinstance(error, anthropic.APIStatusError):
        assert mapped.provider_request_id == "anthropic-safe-request-id"
