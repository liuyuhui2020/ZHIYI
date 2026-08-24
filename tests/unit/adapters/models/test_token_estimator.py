from __future__ import annotations

from zhiyi.adapters.models.token_estimator import ConservativeTokenEstimator
from zhiyi.application.models.contracts import (
    ImagePart,
    InputModality,
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


def target() -> ModelTarget:
    return ModelTarget(
        provider=ProviderId("fake"),
        model_id="token-test",
        credential=None,
        capabilities=ModelCapabilityProfile(
            tool_calling=True,
            input_modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
            modality_token_upper_bounds={InputModality.IMAGE: 1_024},
            max_context_tokens=32_000,
            max_output_tokens=4_096,
        ),
        limits=ModelLimits(),
    )


def test_estimate_is_at_least_utf8_bytes_plus_protocol_overhead() -> None:
    text = "你好, model"
    request = ModelRequest(
        request_id="req-token",
        messages=(Message(MessageRole.USER, (TextPart(text),)),),
        max_output_tokens=128,
    )
    estimate = ConservativeTokenEstimator().estimate(target(), request)
    assert estimate.input_upper_bound >= len(text.encode())
    assert estimate.method == "utf8-bytes-v1"


def test_estimate_includes_tools_schemas_and_multimodal_bounds() -> None:
    base = ModelRequest(
        request_id="req-base",
        messages=(Message(MessageRole.USER, (TextPart("hello"),)),),
        max_output_tokens=128,
    )
    expanded = ModelRequest(
        request_id="req-expanded",
        messages=(
            Message(
                MessageRole.USER,
                (
                    TextPart("hello"),
                    ImagePart("https://example.test/image.png", "image/png"),
                ),
            ),
        ),
        tools=(
            ToolDefinition(
                name="weather",
                description="Look up weather",
                input_schema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
        ),
        max_output_tokens=128,
    )
    estimator = ConservativeTokenEstimator()
    assert estimator.estimate(target(), expanded).input_upper_bound > (
        estimator.estimate(target(), base).input_upper_bound + 1_024
    )
