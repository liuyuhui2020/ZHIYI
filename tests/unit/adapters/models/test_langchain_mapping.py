from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from zhiyi.adapters.models.langchain_base import (
    map_ai_chunk,
    map_ai_message,
    to_langchain_messages,
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
    TextPart,
)


def request() -> ModelRequest:
    return ModelRequest(
        request_id="req-map",
        messages=(
            Message(MessageRole.SYSTEM, (TextPart("policy"),)),
            Message(MessageRole.USER, (TextPart("hello"),)),
        ),
        max_output_tokens=64,
    )


def test_platform_messages_convert_without_leaking_platform_objects() -> None:
    messages = to_langchain_messages(request().messages)
    assert [message.type for message in messages] == ["system", "human"]
    assert messages[1].content == [{"type": "text", "text": "hello"}]


def test_all_roles_and_multimodal_content_preserve_input_order() -> None:
    platform_messages = (
        Message(MessageRole.SYSTEM, (TextPart("policy"),)),
        Message(
            MessageRole.USER,
            (
                TextPart("before"),
                ImagePart("https://example.test/image.png", "image/png"),
                DocumentPart(
                    "https://example.test/report.pdf",
                    "application/pdf",
                    title="Report",
                ),
                TextPart("after"),
            ),
        ),
        Message(MessageRole.ASSISTANT, (TextPart("calling tool"),)),
        Message(
            MessageRole.TOOL,
            (TextPart("tool result"),),
            tool_call_id="call-1",
            name="weather",
        ),
    )

    mapped = to_langchain_messages(platform_messages)

    assert [message.type for message in mapped] == ["system", "human", "ai", "tool"]
    assert mapped[1].content == [
        {"type": "text", "text": "before"},
        {
            "type": "image",
            "url": "https://example.test/image.png",
            "mime_type": "image/png",
        },
        {
            "type": "file",
            "url": "https://example.test/report.pdf",
            "mime_type": "application/pdf",
            "title": "Report",
        },
        {"type": "text", "text": "after"},
    ]
    assert isinstance(mapped[3], ToolMessage)
    assert mapped[3].tool_call_id == "call-1"


def test_ai_message_mapping_keeps_text_tools_usage_and_allowlisted_metadata() -> None:
    message = AIMessage(
        content=[
            {"type": "reasoning", "reasoning": "hidden chain"},
            {"type": "text", "text": "safe answer"},
        ],
        tool_calls=[{"id": "call-1", "name": "weather", "args": {"city": "Paris"}}],
        usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        response_metadata={"finish_reason": "tool_calls", "request_id": "provider-1"},
    )
    response = map_ai_message(message, request())
    assert [part.text for part in response.content] == ["safe answer"]
    assert response.tool_calls[0].arguments["city"] == "Paris"
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.usage is not None and response.usage.total_tokens == 13
    assert response.provider_request_id == "provider-1"
    assert "hidden chain" not in repr(response)


def test_ai_message_mapping_rejects_empty_or_invalid_tool_response() -> None:
    with pytest.raises(ModelError) as empty:
        map_ai_message(AIMessage(content=[]), request())
    assert empty.value.code is ErrorCode.MALFORMED_RESPONSE

    invalid = AIMessage(
        content="",
        invalid_tool_calls=[{"id": "call-bad", "name": "weather", "args": "{", "error": "invalid"}],
    )
    with pytest.raises(ModelError) as malformed:
        map_ai_message(invalid, request())
    assert malformed.value.code is ErrorCode.MALFORMED_RESPONSE


def test_ai_chunk_mapping_preserves_text_tool_fragments_and_usage() -> None:
    chunk = AIMessageChunk(
        content="hello",
        tool_call_chunks=[{"index": 0, "id": "call-1", "name": "weather", "args": '{"city":"'}],
        usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
        response_metadata={"finish_reason": None, "request_id": "provider-stream"},
    )
    parts = map_ai_chunk(chunk, request())
    assert parts[0].text_delta == "hello"
    assert parts[1].tool_call_delta is not None
    assert parts[1].tool_call_delta.arguments_fragment == '{"city":"'
    assert parts[-1].usage is not None and parts[-1].usage.total_tokens == 6
