from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import pytest

from zhiyi.application.models.contracts import (
    ChunkKind,
    ErrorCode,
    FinishReason,
    ImagePart,
    InputModality,
    Message,
    MessageRole,
    ModelCapabilityProfile,
    ModelChunk,
    ModelError,
    ModelLimits,
    ModelRequest,
    ModelResponse,
    ModelRoute,
    ModelTarget,
    ModelUsage,
    ProviderId,
    TextPart,
    ToolDefinition,
)
from zhiyi.application.ports.secret_provider import SecretReference, SecretValue


def capability_profile() -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        tool_calling=True,
        structured_output=True,
        input_modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
        modality_token_upper_bounds={InputModality.IMAGE: 2_048},
        max_context_tokens=16_384,
        max_output_tokens=4_096,
        usage_available=True,
    )


def target(provider: str = "fake", model_id: str = "deterministic") -> ModelTarget:
    return ModelTarget(
        provider=ProviderId(provider),
        model_id=model_id,
        credential=None,
        capabilities=capability_profile(),
        limits=ModelLimits(),
    )


def test_provider_id_is_open_but_validated() -> None:
    assert str(ProviderId("custom-provider.v2")) == "custom-provider.v2"
    with pytest.raises(ValueError, match="provider id"):
        ProviderId("../../plugin")


def test_secret_value_never_reveals_itself_in_text() -> None:
    secret = SecretValue("sentinel-secret")
    assert str(secret) == "********"
    assert repr(secret) == "SecretValue(********)"
    assert secret.reveal() == "sentinel-secret"
    assert "sentinel-secret" not in repr(secret)
    assert SecretReference("OPENAI_API_KEY").name == "OPENAI_API_KEY"


def test_message_and_request_validation_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="content"):
        Message(role=MessageRole.USER, content=())
    with pytest.raises(ValueError, match="tool_call_id"):
        Message(role=MessageRole.TOOL, content=(TextPart("ok"),))
    with pytest.raises(ValueError, match="scheme"):
        ImagePart(uri="file:///etc/passwd", media_type="image/png")
    with pytest.raises(ValueError, match="messages"):
        ModelRequest(request_id="req-1", messages=(), max_output_tokens=16)


def test_tool_schema_is_copied_and_requires_object_root() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
    }
    tool = ToolDefinition(name="weather.lookup", description="Read weather", input_schema=schema)
    schema["type"] = "string"
    assert tool.input_schema["type"] == "object"
    with pytest.raises(ValueError, match="object"):
        ToolDefinition(name="bad", description="Bad", input_schema={"type": "string"})


def test_route_rejects_duplicate_targets_and_invalid_deadline() -> None:
    primary = target()
    with pytest.raises(ValueError, match="duplicate"):
        ModelRoute(primary=primary, fallbacks=(primary,), total_timeout_seconds=3)
    with pytest.raises(ValueError, match="total_timeout"):
        ModelRoute(primary=primary, total_timeout_seconds=0)


def test_capability_profile_requires_multimodal_upper_bound() -> None:
    with pytest.raises(ValueError, match="upper bound"):
        ModelCapabilityProfile(
            input_modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
            max_context_tokens=8_192,
            max_output_tokens=1_024,
        )


def test_usage_aggregation_preserves_unknown_and_amount_semantics() -> None:
    first = ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    second = ModelUsage(input_tokens=None, output_tokens=2, total_tokens=None)
    total = ModelUsage.aggregate((first, second))
    assert total.input_tokens == 10
    assert total.output_tokens == 7
    assert total.total_tokens == 15
    assert total.incomplete is True
    with pytest.raises(ValueError, match="amount"):
        ModelUsage(amount=Decimal("0.01"))


def test_model_error_is_safe_and_can_attach_attempts() -> None:
    error = ModelError(
        code=ErrorCode.AUTHENTICATION,
        message="Provider authentication failed",
        request_id="req-1",
    )
    assert error.retryable is False
    assert "api-key" not in str(error)


def test_empty_text_runtime_invalid_role_and_content_are_rejected() -> None:
    with pytest.raises(ValueError, match="text"):
        TextPart("")
    with pytest.raises(ValueError, match="non-whitespace"):
        ModelRequest(
            request_id="req-whitespace",
            messages=(Message(MessageRole.USER, (TextPart("   "),)),),
            max_output_tokens=16,
        )
    with pytest.raises(ValueError, match="role"):
        Message("unknown", (TextPart("hello"),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="content part"):
        Message(MessageRole.USER, (object(),))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": []},
        {"type": "object", "properties": {"city": {"type": "string"}}, "required": 7},
        {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["unknown"],
        },
        {"type": "object", "additionalProperties": "yes"},
    ],
)
def test_malformed_tool_schema_is_rejected_before_adapter_mapping(
    schema: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="schema"):
        ToolDefinition(name="weather", description="Read weather", input_schema=schema)


class InvalidStructuredContract:
    @property
    def name(self) -> str:
        return ""

    @property
    def json_schema(self) -> Mapping[str, object]:
        return {"type": "array"}

    def validate(self, value: object) -> object:
        return value


def test_invalid_structured_schema_is_rejected_at_request_construction() -> None:
    with pytest.raises(ValueError, match="structured output"):
        ModelRequest(
            request_id="req-invalid-structured",
            messages=(Message(MessageRole.USER, (TextPart("hello"),)),),
            max_output_tokens=16,
            structured_output=InvalidStructuredContract(),
        )


def test_response_and_chunk_terminal_invariants_are_enforced() -> None:
    with pytest.raises(ValueError, match="attempt"):
        ModelResponse(
            request_id="req-response",
            provider=ProviderId("fake"),
            model_id="model",
            content=(TextPart("ok"),),
            finish_reason=FinishReason.STOP,
            usage=None,
            total_usage=ModelUsage(incomplete=True),
            attempts=(),
        )
    with pytest.raises(ValueError, match="kind"):
        ModelChunk(
            sequence=0,
            kind=ChunkKind.TEXT_DELTA,
            error=ModelError(
                code=ErrorCode.UNKNOWN,
                message="safe",
                request_id="req-chunk",
            ),
        )
