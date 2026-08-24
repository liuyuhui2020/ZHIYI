"""Shared LangChain boundary for chat model providers."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from zhiyi.application.models.contracts import (
    DocumentPart,
    ErrorCode,
    FinishReason,
    ImagePart,
    Message,
    MessageRole,
    ModelError,
    ModelRequest,
    ModelTarget,
    ModelUsage,
    ProviderId,
    TextPart,
    ToolCall,
    ToolCallDelta,
    thaw_json,
)
from zhiyi.application.ports.model_provider import ProviderChunk, ProviderResponse
from zhiyi.application.ports.secret_provider import SecretValue


class ChatModelLike(Protocol):
    async def ainvoke(self, messages: object, **kwargs: object) -> object: ...

    def astream(self, messages: object, **kwargs: object) -> AsyncIterator[object]: ...

    def bind_tools(self, tools: object, **kwargs: object) -> ChatModelLike: ...

    def with_structured_output(self, schema: object, **kwargs: object) -> ChatModelLike: ...


ModelFactory = Callable[..., object]


def _content_to_langchain(message: Message) -> list[str | dict[Any, Any]]:
    content: list[str | dict[Any, Any]] = []
    for part in message.content:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append({"type": "image", "url": part.uri, "mime_type": part.media_type})
        elif isinstance(part, DocumentPart):
            item: dict[Any, Any] = {
                "type": "file",
                "url": part.uri,
                "mime_type": part.media_type,
            }
            if part.title is not None:
                item["title"] = part.title
            content.append(item)
    return content


def to_langchain_messages(messages: Sequence[Message]) -> list[BaseMessage]:
    result: list[BaseMessage] = []
    for message in messages:
        content = _content_to_langchain(message)
        if message.role is MessageRole.SYSTEM:
            result.append(SystemMessage(content=content, name=message.name))
        elif message.role is MessageRole.USER:
            result.append(HumanMessage(content=content, name=message.name))
        elif message.role is MessageRole.ASSISTANT:
            result.append(AIMessage(content=content, name=message.name))
        else:
            result.append(
                ToolMessage(
                    content=content,
                    tool_call_id=cast(str, message.tool_call_id),
                    name=message.name,
                )
            )
    return result


def _extract_text(content: object) -> tuple[TextPart, ...]:
    if isinstance(content, str):
        return (TextPart(content),) if content else ()
    if not isinstance(content, list | tuple):
        return ()
    parts: list[TextPart] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        if block_type in {"text", "text_delta"}:
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(TextPart(text))
    return tuple(parts)


def _usage_from_metadata(metadata: object) -> ModelUsage | None:
    if not isinstance(metadata, Mapping):
        return None

    def non_negative_int(name: str) -> int | None:
        value = metadata.get(name)
        return value if isinstance(value, int) and value >= 0 else None

    input_tokens = non_negative_int("input_tokens")
    output_tokens = non_negative_int("output_tokens")
    total_tokens = non_negative_int("total_tokens")
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        incomplete=any(value is None for value in (input_tokens, output_tokens, total_tokens)),
    )


def normalize_finish_reason(value: object) -> FinishReason:
    if not isinstance(value, str):
        return FinishReason.UNKNOWN
    normalized = value.lower()
    if normalized in {"stop", "end_turn", "stop_sequence"}:
        return FinishReason.STOP
    if normalized in {"tool_calls", "tool_use"}:
        return FinishReason.TOOL_CALLS
    if normalized in {"length", "max_tokens", "max_output_tokens"}:
        return FinishReason.LENGTH
    if normalized in {"content_filter", "refusal", "safety"}:
        return FinishReason.CONTENT_FILTER
    return FinishReason.UNKNOWN


def _provider_request_id(metadata: Mapping[str, object]) -> str | None:
    for name in ("request_id", "id"):
        value = metadata.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _finish_reason(metadata: Mapping[str, object]) -> FinishReason:
    return normalize_finish_reason(metadata.get("finish_reason") or metadata.get("stop_reason"))


def map_ai_message(
    message: AIMessage,
    request: ModelRequest,
    *,
    structured_output: object | None = None,
) -> ProviderResponse:
    if message.invalid_tool_calls:
        raise ModelError(
            code=ErrorCode.MALFORMED_RESPONSE,
            message="Provider returned an invalid tool call",
            request_id=request.request_id,
        )
    content = _extract_text(message.content)
    tool_calls: list[ToolCall] = []
    for raw in message.tool_calls:
        call_id = raw.get("id")
        name = raw.get("name")
        args = raw.get("args")
        if (
            not isinstance(call_id, str)
            or not isinstance(name, str)
            or not isinstance(args, Mapping)
        ):
            raise ModelError(
                code=ErrorCode.MALFORMED_RESPONSE,
                message="Provider returned an incomplete tool call",
                request_id=request.request_id,
            )
        tool_calls.append(ToolCall(id=call_id, name=name, arguments=args))

    metadata = cast(Mapping[str, object], message.response_metadata)
    if not content and not tool_calls and structured_output is None:
        raise ModelError(
            code=ErrorCode.MALFORMED_RESPONSE,
            message="Provider returned no usable content",
            request_id=request.request_id,
        )
    return ProviderResponse(
        content=content,
        finish_reason=_finish_reason(metadata),
        usage=_usage_from_metadata(message.usage_metadata),
        provider_request_id=_provider_request_id(metadata),
        tool_calls=tuple(tool_calls),
        structured_output=structured_output,
    )


def map_ai_chunk(chunk: AIMessageChunk, request: ModelRequest) -> tuple[ProviderChunk, ...]:
    result: list[ProviderChunk] = []
    for part in _extract_text(chunk.content):
        result.append(ProviderChunk(text_delta=part.text))
    for raw in chunk.tool_call_chunks:
        index = raw.get("index")
        if not isinstance(index, int):
            raise ModelError(
                code=ErrorCode.MALFORMED_RESPONSE,
                message="Provider returned an invalid tool call fragment",
                request_id=request.request_id,
            )
        args = raw.get("args")
        result.append(
            ProviderChunk(
                tool_call_delta=ToolCallDelta(
                    index=index,
                    id=raw.get("id") if isinstance(raw.get("id"), str) else None,
                    name=raw.get("name") if isinstance(raw.get("name"), str) else None,
                    arguments_fragment=args if isinstance(args, str) else "",
                )
            )
        )
    usage = _usage_from_metadata(chunk.usage_metadata)
    if usage is not None:
        result.append(ProviderChunk(usage=usage))
    metadata = cast(Mapping[str, object], chunk.response_metadata)
    finish_reason = _finish_reason(metadata)
    if finish_reason is not FinishReason.UNKNOWN:
        result.append(
            ProviderChunk(
                finish_reason=finish_reason,
                provider_request_id=_provider_request_id(metadata),
            )
        )
    return tuple(result)


class LangChainModelProvider(ABC):
    """Translate platform requests around a provider-specific LangChain model."""

    def __init__(self, *, model_factory: ModelFactory | None = None) -> None:
        self._model_factory = model_factory

    @property
    @abstractmethod
    def provider_id(self) -> ProviderId: ...

    @abstractmethod
    def _default_factory(self) -> ModelFactory: ...

    @abstractmethod
    def _model_kwargs(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue,
    ) -> dict[str, object]: ...

    @abstractmethod
    def _map_exception(self, error: Exception, request_id: str) -> ModelError: ...

    def _structured_output_kwargs(self) -> dict[str, object]:
        return {"include_raw": True}

    def _make_model(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
        *,
        structured: bool,
    ) -> ChatModelLike:
        if credential is None:
            raise ModelError(
                code=ErrorCode.AUTHENTICATION,
                message="Provider credential is unavailable",
                request_id=request.request_id,
            )
        factory = self._model_factory or self._default_factory()
        model = cast(ChatModelLike, factory(**self._model_kwargs(target, request, credential)))
        if request.tools:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": thaw_json(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
            model = model.bind_tools(tools, strict=all(tool.strict for tool in request.tools))
        if structured and request.structured_output is not None:
            model = model.with_structured_output(
                thaw_json(request.structured_output.json_schema),
                **self._structured_output_kwargs(),
            )
        return model

    def _invoke_kwargs(self, request: ModelRequest) -> dict[str, object]:
        return {"stop": list(request.stop)} if request.stop else {}

    async def complete(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse:
        try:
            structured = request.structured_output is not None
            model = self._make_model(target, request, credential, structured=structured)
            result = await model.ainvoke(
                to_langchain_messages(request.messages),
                **self._invoke_kwargs(request),
            )
            if structured:
                if not isinstance(result, Mapping):
                    raise ModelError(
                        code=ErrorCode.MALFORMED_RESPONSE,
                        message="Provider returned an invalid structured response envelope",
                        request_id=request.request_id,
                    )
                parsing_error = result.get("parsing_error")
                raw = result.get("raw")
                if parsing_error is not None or not isinstance(raw, AIMessage):
                    raise ModelError(
                        code=ErrorCode.STRUCTURED_OUTPUT_INVALID,
                        message="Provider structured output did not validate",
                        request_id=request.request_id,
                    )
                return map_ai_message(raw, request, structured_output=result.get("parsed"))
            if not isinstance(result, AIMessage):
                raise ModelError(
                    code=ErrorCode.MALFORMED_RESPONSE,
                    message="Provider returned an unexpected message type",
                    request_id=request.request_id,
                )
            return map_ai_message(result, request)
        except asyncio.CancelledError:
            raise
        except ModelError:
            raise
        except Exception as error:
            raise self._map_exception(error, request.request_id) from None

    async def stream(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]:
        if request.structured_output is not None:
            response = await self.complete(target, request, credential)
            yield ProviderChunk(
                structured_output=response.structured_output,
                usage=response.usage,
                finish_reason=response.finish_reason,
                provider_request_id=response.provider_request_id,
            )
            return
        try:
            model = self._make_model(target, request, credential, structured=False)
            async for raw in model.astream(
                to_langchain_messages(request.messages),
                **self._invoke_kwargs(request),
            ):
                if not isinstance(raw, AIMessageChunk):
                    raise ModelError(
                        code=ErrorCode.MALFORMED_RESPONSE,
                        message="Provider returned an unexpected stream message type",
                        request_id=request.request_id,
                    )
                for chunk in map_ai_chunk(raw, request):
                    yield chunk
        except asyncio.CancelledError:
            raise
        except ModelError:
            raise
        except Exception as error:
            raise self._map_exception(error, request.request_id) from None
