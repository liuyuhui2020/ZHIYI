"""Official Anthropic LangChain provider adapter."""

from __future__ import annotations

from collections.abc import Mapping

import anthropic
from langchain_anthropic import ChatAnthropic

from zhiyi.adapters.models.langchain_base import LangChainModelProvider, ModelFactory
from zhiyi.application.models.contracts import (
    ErrorCode,
    ModelError,
    ModelRequest,
    ModelTarget,
    ProviderId,
)
from zhiyi.application.ports.secret_provider import SecretValue

_CONTENT_POLICY_CODES = frozenset(
    {"content_filter", "content_policy_violation", "safety_violation"}
)


def _safe_error_code(error: Exception) -> str | None:
    direct = getattr(error, "code", None)
    if isinstance(direct, str):
        return direct
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return None
    nested = body.get("error")
    candidates = (
        body.get("code"),
        body.get("type"),
        nested.get("code") if isinstance(nested, Mapping) else None,
        nested.get("type") if isinstance(nested, Mapping) else None,
    )
    return next((value for value in candidates if isinstance(value, str)), None)


def _safe_request_id(error: Exception) -> str | None:
    request_id = getattr(error, "request_id", None)
    return request_id if isinstance(request_id, str) and request_id else None


class AnthropicProvider(LangChainModelProvider):
    @property
    def provider_id(self) -> ProviderId:
        return ProviderId("anthropic")

    def _default_factory(self) -> ModelFactory:
        return ChatAnthropic

    def _structured_output_kwargs(self) -> dict[str, object]:
        return {"include_raw": True, "method": "json_schema"}

    def _model_kwargs(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "model": target.model_id,
            "api_key": credential.reveal(),
            "max_retries": 0,
            "timeout": target.limits.attempt_timeout_seconds,
            "max_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        return kwargs

    def _map_exception(self, error: Exception, request_id: str) -> ModelError:
        if _safe_error_code(error) in _CONTENT_POLICY_CODES:
            code = ErrorCode.CONTENT_POLICY
            message = "Anthropic content policy rejected the request"
        elif isinstance(error, anthropic.AuthenticationError):
            code = ErrorCode.AUTHENTICATION
            message = "Anthropic authentication failed"
        elif isinstance(error, anthropic.PermissionDeniedError):
            code = ErrorCode.PERMISSION
            message = "Anthropic permission was denied"
        elif isinstance(error, anthropic.RateLimitError):
            code = ErrorCode.RATE_LIMITED
            message = "Anthropic rate limit was reached"
        elif isinstance(error, anthropic.APITimeoutError):
            code = ErrorCode.TIMEOUT
            message = "Anthropic request timed out"
        elif isinstance(
            error,
            anthropic.APIConnectionError
            | anthropic.InternalServerError
            | anthropic.OverloadedError,
        ):
            code = ErrorCode.UNAVAILABLE
            message = "Anthropic is temporarily unavailable"
        elif isinstance(error, anthropic.BadRequestError):
            code = ErrorCode.INVALID_REQUEST
            message = "Anthropic rejected the request"
        else:
            code = ErrorCode.UNKNOWN
            message = "Anthropic request failed"
        return ModelError(
            code=code,
            message=message,
            request_id=request_id,
            provider_request_id=_safe_request_id(error),
        )
