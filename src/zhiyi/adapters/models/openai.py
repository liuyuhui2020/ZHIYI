"""Official OpenAI LangChain provider adapter."""

from __future__ import annotations

from collections.abc import Mapping

import openai
from langchain_openai import ChatOpenAI

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
        nested.get("code") if isinstance(nested, Mapping) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, str):
            return candidate
    return None


def _safe_request_id(error: Exception) -> str | None:
    request_id = getattr(error, "request_id", None)
    return request_id if isinstance(request_id, str) and request_id else None


class OpenAIProvider(LangChainModelProvider):
    @property
    def provider_id(self) -> ProviderId:
        return ProviderId("openai")

    def _default_factory(self) -> ModelFactory:
        return ChatOpenAI

    def _structured_output_kwargs(self) -> dict[str, object]:
        return {"include_raw": True, "method": "json_schema", "strict": True}

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
            "max_completion_tokens": request.max_output_tokens,
            "stream_usage": True,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        return kwargs

    def _map_exception(self, error: Exception, request_id: str) -> ModelError:
        if isinstance(error, openai.ContentFilterFinishReasonError) or (
            _safe_error_code(error) in _CONTENT_POLICY_CODES
        ):
            code = ErrorCode.CONTENT_POLICY
            message = "OpenAI content policy rejected the request"
        elif isinstance(error, openai.AuthenticationError):
            code = ErrorCode.AUTHENTICATION
            message = "OpenAI authentication failed"
        elif isinstance(error, openai.PermissionDeniedError):
            code = ErrorCode.PERMISSION
            message = "OpenAI permission was denied"
        elif isinstance(error, openai.RateLimitError):
            code = ErrorCode.RATE_LIMITED
            message = "OpenAI rate limit was reached"
        elif isinstance(error, openai.APITimeoutError):
            code = ErrorCode.TIMEOUT
            message = "OpenAI request timed out"
        elif isinstance(error, openai.APIConnectionError | openai.InternalServerError):
            code = ErrorCode.UNAVAILABLE
            message = "OpenAI is temporarily unavailable"
        elif isinstance(error, openai.BadRequestError):
            code = ErrorCode.INVALID_REQUEST
            message = "OpenAI rejected the request"
        else:
            code = ErrorCode.UNKNOWN
            message = "OpenAI request failed"
        return ModelError(
            code=code,
            message=message,
            request_id=request_id,
            provider_request_id=_safe_request_id(error),
        )
