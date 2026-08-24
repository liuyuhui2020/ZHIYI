from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import openai
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel

from zhiyi.adapters.models.openai import OpenAIProvider
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
    def __init__(self) -> None:
        self.invocations = 0

    async def ainvoke(self, messages: object, **kwargs: object) -> object:
        del messages, kwargs
        self.invocations += 1
        return AIMessage(
            content="openai answer",
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            response_metadata={"finish_reason": "stop", "request_id": "openai-req"},
        )

    async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[AIMessageChunk]:
        del messages, kwargs
        yield AIMessageChunk(content="openai ")
        yield AIMessageChunk(
            content="answer",
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            response_metadata={"finish_reason": "stop", "request_id": "openai-stream"},
        )

    def bind_tools(self, tools: object, **kwargs: object) -> StubChatModel:
        del tools, kwargs
        return self

    def with_structured_output(self, schema: object, **kwargs: object) -> StubChatModel:
        del schema, kwargs
        return self


class ToolChatModel(StubChatModel):
    def __init__(self) -> None:
        super().__init__()
        self.bound_tools: object | None = None
        self.bind_kwargs: dict[str, object] = {}

    def bind_tools(self, tools: object, **kwargs: object) -> ToolChatModel:
        self.bound_tools = tools
        self.bind_kwargs = kwargs
        return self

    async def ainvoke(self, messages: object, **kwargs: object) -> AIMessage:
        del messages, kwargs
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "lookup_weather",
                    "args": {"city": "Shanghai"},
                    "type": "tool_call",
                }
            ],
            response_metadata={"finish_reason": "tool_calls"},
        )

    async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[AIMessageChunk]:
        del messages, kwargs
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "id": "call-1",
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
            response_metadata={"finish_reason": "tool_calls"},
        )


class StructuredAnswer(BaseModel):
    answer: str


class StructuredChatModel(StubChatModel):
    def __init__(self, *, parsing_error: object | None = None) -> None:
        super().__init__()
        self.schema: object | None = None
        self.structured_kwargs: dict[str, object] = {}
        self.parsing_error = parsing_error

    def with_structured_output(self, schema: object, **kwargs: object) -> StructuredChatModel:
        self.schema = schema
        self.structured_kwargs = kwargs
        return self

    async def ainvoke(self, messages: object, **kwargs: object) -> object:
        del messages, kwargs
        return {
            "raw": AIMessage(content="", response_metadata={"finish_reason": "stop"}),
            "parsed": {"answer": "verified"},
            "parsing_error": self.parsing_error,
        }


def target() -> ModelTarget:
    return ModelTarget(
        provider=ProviderId("openai"),
        model_id="gpt-contract",
        credential=SecretReference("OPENAI_API_KEY"),
        capabilities=ModelCapabilityProfile(max_context_tokens=8_192, max_output_tokens=1_024),
        limits=ModelLimits(max_retries=0),
    )


def request() -> ModelRequest:
    return ModelRequest(
        request_id="req-openai",
        messages=(Message(MessageRole.USER, (TextPart("hello"),)),),
        max_output_tokens=32,
    )


@pytest.mark.asyncio
async def test_openai_complete_and_stream_follow_platform_contract() -> None:
    model = StubChatModel()
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> StubChatModel:
        captured.update(kwargs)
        return model

    provider = OpenAIProvider(model_factory=factory)
    response = await provider.complete(target(), request(), SecretValue("sentinel-openai"))
    chunks = [
        chunk
        async for chunk in provider.stream(target(), request(), SecretValue("sentinel-openai"))
    ]

    assert response.content[0].text == "openai answer"
    assert response.finish_reason is FinishReason.STOP
    assert chunks[-1].finish_reason is FinishReason.STOP
    assert captured["max_retries"] == 0
    assert captured["api_key"] == "sentinel-openai"
    assert provider.provider_id == ProviderId("openai")


def tool_request() -> ModelRequest:
    return ModelRequest(
        request_id="req-openai-tool",
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


@pytest.mark.asyncio
async def test_openai_binds_strict_tools_and_preserves_streamed_json_fragments() -> None:
    model = ToolChatModel()
    provider = OpenAIProvider(model_factory=lambda **_: model)

    response = await provider.complete(target(), tool_request(), SecretValue("sentinel-openai"))
    chunks = [
        chunk
        async for chunk in provider.stream(target(), tool_request(), SecretValue("sentinel-openai"))
    ]

    assert response.tool_calls[0].arguments["city"] == "Shanghai"
    assert model.bind_kwargs == {"strict": True}
    assert "additionalProperties" in repr(model.bound_tools)
    assert (
        "".join(
            chunk.tool_call_delta.arguments_fragment
            for chunk in chunks
            if chunk.tool_call_delta is not None
        )
        == '{"city":"Shanghai"}'
    )


@pytest.mark.asyncio
async def test_openai_structured_output_uses_strict_envelope_and_rejects_parse_error() -> None:
    contract = PydanticOutputContract(StructuredAnswer)
    structured_request = ModelRequest(
        request_id="req-openai-structured",
        messages=(Message(MessageRole.USER, (TextPart("answer"),)),),
        max_output_tokens=32,
        structured_output=contract,
    )
    model = StructuredChatModel()
    provider = OpenAIProvider(model_factory=lambda **_: model)

    response = await provider.complete(target(), structured_request, SecretValue("sentinel-openai"))
    assert response.structured_output == {"answer": "verified"}
    assert model.structured_kwargs == {
        "include_raw": True,
        "method": "json_schema",
        "strict": True,
    }

    invalid = OpenAIProvider(
        model_factory=lambda **_: StructuredChatModel(parsing_error=ValueError("raw secret"))
    )
    with pytest.raises(Exception, match="structured_output_invalid"):
        await invalid.complete(target(), structured_request, SecretValue("sentinel-openai"))


def openai_response(status_code: int) -> httpx2.Response:
    return httpx2.Response(
        status_code,
        request=httpx2.Request("POST", "https://api.openai.test/v1/responses"),
        headers={"x-request-id": "openai-safe-request-id"},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.AuthenticationError("raw-auth-sentinel", response=openai_response(401), body={}),
            ErrorCode.AUTHENTICATION,
        ),
        (
            openai.PermissionDeniedError(
                "raw-permission-sentinel", response=openai_response(403), body={}
            ),
            ErrorCode.PERMISSION,
        ),
        (
            openai.RateLimitError("raw-rate-sentinel", response=openai_response(429), body={}),
            ErrorCode.RATE_LIMITED,
        ),
        (
            openai.APITimeoutError(httpx2.Request("POST", "https://api.openai.test")),
            ErrorCode.TIMEOUT,
        ),
        (
            openai.APIConnectionError(
                message="raw-connection-sentinel",
                request=httpx2.Request("POST", "https://api.openai.test"),
            ),
            ErrorCode.UNAVAILABLE,
        ),
        (
            openai.InternalServerError(
                "raw-server-sentinel", response=openai_response(500), body={}
            ),
            ErrorCode.UNAVAILABLE,
        ),
        (
            openai.BadRequestError("raw-input-sentinel", response=openai_response(400), body={}),
            ErrorCode.INVALID_REQUEST,
        ),
        (
            openai.BadRequestError(
                "raw-policy-sentinel",
                response=openai_response(400),
                body={"error": {"code": "content_policy_violation"}},
            ),
            ErrorCode.CONTENT_POLICY,
        ),
        (openai.ContentFilterFinishReasonError(), ErrorCode.CONTENT_POLICY),
        (RuntimeError("raw-unknown-sentinel"), ErrorCode.UNKNOWN),
    ],
)
def test_openai_exception_matrix_is_stable_and_redacted(
    error: Exception, expected: ErrorCode
) -> None:
    mapped = OpenAIProvider()._map_exception(error, "platform-request")

    assert mapped.code is expected
    assert mapped.retryable is (
        expected in {ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE}
    )
    assert mapped.fallback_allowed is mapped.retryable
    assert "sentinel" not in str(mapped)
    assert "sentinel" not in repr(mapped)
    if isinstance(error, openai.APIStatusError):
        assert mapped.provider_request_id == "openai-safe-request-id"
