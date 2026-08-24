"""Validated outer configuration for curated model targets and routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zhiyi.application.models.contracts import (
    InputModality,
    ModelCapabilityProfile,
    ModelLimits,
    ModelRoute,
    ModelTarget,
    ProviderId,
)
from zhiyi.application.ports.secret_provider import SecretReference

_DEFAULT_CREDENTIALS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelCapabilityConfig(_ConfigModel):
    tool_calling: bool = False
    structured_output: bool = False
    input_modalities: frozenset[InputModality] = frozenset({InputModality.TEXT})
    modality_token_upper_bounds: dict[InputModality, int] = Field(default_factory=dict)
    max_context_tokens: int = 8_192
    max_output_tokens: int = 1_024
    usage_available: bool = True

    def to_application(self) -> ModelCapabilityProfile:
        return ModelCapabilityProfile(
            tool_calling=self.tool_calling,
            structured_output=self.structured_output,
            input_modalities=self.input_modalities,
            modality_token_upper_bounds=self.modality_token_upper_bounds,
            max_context_tokens=self.max_context_tokens,
            max_output_tokens=self.max_output_tokens,
            usage_available=self.usage_available,
        )

    @model_validator(mode="after")
    def validate_application_contract(self) -> ModelCapabilityConfig:
        self.to_application()
        return self


class ModelLimitsConfig(_ConfigModel):
    attempt_timeout_seconds: float = 30.0
    stream_first_byte_timeout_seconds: float = 15.0
    stream_idle_timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 0.25
    retry_max_delay_seconds: float = 2.0
    rate_limit_per_second: float | None = None
    rate_limit_burst: int = 1
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 30.0

    def to_application(self) -> ModelLimits:
        return ModelLimits(**self.model_dump())

    @model_validator(mode="after")
    def validate_application_contract(self) -> ModelLimitsConfig:
        self.to_application()
        return self


class ModelTargetConfig(_ConfigModel):
    provider: str
    model_id: str
    credential_reference: str | None = None
    capabilities: ModelCapabilityConfig
    limits: ModelLimitsConfig = Field(default_factory=ModelLimitsConfig)

    def to_application(self) -> ModelTarget:
        credential_name = self.credential_reference or _DEFAULT_CREDENTIALS.get(self.provider)
        credential = SecretReference(credential_name) if credential_name is not None else None
        return ModelTarget(
            provider=ProviderId(self.provider),
            model_id=self.model_id,
            credential=credential,
            capabilities=self.capabilities.to_application(),
            limits=self.limits.to_application(),
        )

    @model_validator(mode="after")
    def validate_application_contract(self) -> ModelTargetConfig:
        self.to_application()
        return self


class ModelRouteConfig(_ConfigModel):
    primary: ModelTargetConfig
    fallbacks: tuple[ModelTargetConfig, ...] = ()
    total_timeout_seconds: float = 60.0

    def to_application(self) -> ModelRoute:
        return ModelRoute(
            primary=self.primary.to_application(),
            fallbacks=tuple(target.to_application() for target in self.fallbacks),
            total_timeout_seconds=self.total_timeout_seconds,
        )

    @model_validator(mode="after")
    def validate_application_contract(self) -> ModelRouteConfig:
        self.to_application()
        return self
