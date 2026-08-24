from __future__ import annotations

import pytest
from pydantic import ValidationError

from zhiyi.application.models.contracts import InputModality, ProviderId
from zhiyi.infrastructure.config.models import (
    ModelCapabilityConfig,
    ModelLimitsConfig,
    ModelRouteConfig,
    ModelTargetConfig,
)


def capability(**overrides: object) -> ModelCapabilityConfig:
    values: dict[str, object] = {
        "tool_calling": True,
        "structured_output": True,
        "input_modalities": frozenset({InputModality.TEXT}),
        "max_context_tokens": 8_192,
        "max_output_tokens": 1_024,
    }
    values.update(overrides)
    return ModelCapabilityConfig.model_validate(values)


def target(provider: str = "openai", model_id: str = "model-a") -> ModelTargetConfig:
    return ModelTargetConfig(
        provider=provider,
        model_id=model_id,
        capabilities=capability(),
    )


def test_curated_target_builds_safe_application_objects_and_default_credentials() -> None:
    openai = target("openai")
    anthropic = target("anthropic", "model-b")
    fake = target("fake", "fixture")
    openai_target = openai.to_application()
    anthropic_target = anthropic.to_application()

    assert openai_target.credential is not None
    assert openai_target.credential.name == "OPENAI_API_KEY"
    assert anthropic_target.credential is not None
    assert anthropic_target.credential.name == "ANTHROPIC_API_KEY"
    assert fake.to_application().credential is None
    assert openai.to_application().provider == ProviderId("openai")
    assert "sentinel" not in repr(openai)
    assert "api_key" not in repr(openai).lower()


@pytest.mark.parametrize(
    "values",
    [
        {"input_modalities": frozenset({InputModality.IMAGE})},
        {
            "input_modalities": frozenset({InputModality.TEXT, InputModality.IMAGE}),
            "modality_token_upper_bounds": {},
        },
        {"max_context_tokens": 100, "max_output_tokens": 101},
    ],
)
def test_invalid_capability_profiles_fail_during_config_load(values: object) -> None:
    with pytest.raises(ValidationError):
        capability(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "values",
    [
        {"attempt_timeout_seconds": 0},
        {"stream_first_byte_timeout_seconds": -1},
        {"stream_idle_timeout_seconds": 0},
        {"max_retries": -1},
        {"rate_limit_per_second": 0},
        {"rate_limit_burst": 0},
        {"circuit_failure_threshold": 0},
        {"circuit_recovery_seconds": 0},
        {"retry_base_delay_seconds": 2, "retry_max_delay_seconds": 1},
    ],
)
def test_invalid_operational_limits_fail_during_config_load(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ModelLimitsConfig.model_validate(values)


def test_route_rejects_duplicate_or_cyclic_target_and_invalid_total_timeout() -> None:
    primary = target()

    with pytest.raises(ValidationError):
        ModelRouteConfig(primary=primary, fallbacks=(primary,))
    with pytest.raises(ValidationError):
        ModelRouteConfig(primary=primary, total_timeout_seconds=0)


def test_route_serialization_contains_references_but_never_secret_values() -> None:
    route = ModelRouteConfig(
        primary=target("openai"),
        fallbacks=(target("anthropic", "model-b"),),
        total_timeout_seconds=30,
    )

    dumped = route.model_dump(mode="json")
    application_route = route.to_application()

    assert dumped["primary"]["provider"] == "openai"
    assert application_route.targets[1].provider == ProviderId("anthropic")
    assert "SecretValue" not in repr(dumped)
