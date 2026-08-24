"""Provider-neutral model invocation gateway."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from zhiyi.application.models.contracts import (
    AttemptOutcome,
    AttemptRecord,
    ChunkKind,
    ErrorCode,
    FinishReason,
    ModelChunk,
    ModelError,
    ModelRequest,
    ModelResponse,
    ModelRoute,
    ModelTarget,
    ModelUsage,
    ProviderId,
    StreamTerminal,
    TextPart,
    ToolCall,
    ToolCallDelta,
)
from zhiyi.application.ports.model_provider import ModelProvider, ProviderResponse
from zhiyi.application.ports.secret_provider import (
    SecretProvider,
    SecretResolutionError,
    SecretValue,
)
from zhiyi.application.ports.token_estimator import TokenEstimator
from zhiyi.application.services.circuit_breaker import CircuitBreaker
from zhiyi.application.services.rate_limiter import AsyncTokenBucket

MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
RandomSource = Callable[[], float]
AttemptSink = Callable[[AttemptRecord], None]


@dataclass(slots=True)
class _ToolCallBuffer:
    index: int
    call_id: str | None = None
    name: str | None = None
    fragments: list[str] = field(default_factory=list)

    def append(self, delta: ToolCallDelta, request_id: str) -> None:
        if delta.id is not None:
            if self.call_id is not None and self.call_id != delta.id:
                raise self._malformed(request_id)
            self.call_id = delta.id
        if delta.name is not None:
            if self.name is not None and self.name != delta.name:
                raise self._malformed(request_id)
            self.name = delta.name
        self.fragments.append(delta.arguments_fragment)

    def build(self, request_id: str) -> ToolCall:
        if self.call_id is None or self.name is None:
            raise self._malformed(request_id)
        try:
            arguments = json.loads("".join(self.fragments))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise self._malformed(request_id) from None
        if not isinstance(arguments, Mapping):
            raise self._malformed(request_id)
        try:
            return ToolCall(id=self.call_id, name=self.name, arguments=arguments)
        except (TypeError, ValueError):
            raise self._malformed(request_id) from None

    @staticmethod
    def _malformed(request_id: str) -> ModelError:
        return ModelError(
            code=ErrorCode.MALFORMED_RESPONSE,
            message="Provider returned malformed tool call fragments",
            request_id=request_id,
        )


class DefaultModelGateway:
    """Validate, route, and normalize model calls without exposing provider types."""

    def __init__(
        self,
        *,
        providers: Iterable[ModelProvider],
        token_estimator: TokenEstimator,
        secret_provider: SecretProvider | None = None,
        clock: MonotonicClock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
        random_source: RandomSource = random.random,
        attempt_sink: AttemptSink | None = None,
    ) -> None:
        provider_list = tuple(providers)
        provider_map = {provider.provider_id: provider for provider in provider_list}
        if not provider_map:
            raise ValueError("at least one model provider must be registered")
        if len(provider_map) != len(provider_list):
            raise ValueError("provider ids must be unique")
        self._providers = provider_map
        self._token_estimator = token_estimator
        self._secret_provider = secret_provider
        self._clock = clock
        self._sleeper = sleeper
        self._random_source = random_source
        self._attempt_sink = attempt_sink
        self._rate_limiters: dict[tuple[ProviderId, str], AsyncTokenBucket] = {}
        self._circuit_breakers: dict[tuple[ProviderId, str], CircuitBreaker] = {}

    def _provider(self, target: ModelTarget, request_id: str) -> ModelProvider:
        provider = self._providers.get(target.provider)
        if provider is None:
            raise ModelError(
                code=ErrorCode.INVALID_REQUEST,
                message="Model provider is not registered",
                request_id=request_id,
            )
        return provider

    def _preflight_target(self, target: ModelTarget, request: ModelRequest) -> None:
        self._provider(target, request.request_id)
        missing = [
            capability.value
            for capability in request.required_capabilities
            if not target.capabilities.supports(capability)
        ]
        unsupported_modalities = request.input_modalities - target.capabilities.input_modalities
        if missing or unsupported_modalities:
            raise ModelError(
                code=ErrorCode.CAPABILITY_MISMATCH,
                message="Model capabilities do not satisfy the request",
                request_id=request.request_id,
            )
        if request.max_output_tokens > target.capabilities.max_output_tokens:
            raise ModelError(
                code=ErrorCode.CAPABILITY_MISMATCH,
                message="Requested output exceeds the model limit",
                request_id=request.request_id,
            )
        estimate = self._token_estimator.estimate(target, request)
        if estimate.input_upper_bound + request.max_output_tokens > (
            target.capabilities.max_context_tokens
        ):
            raise ModelError(
                code=ErrorCode.CAPABILITY_MISMATCH,
                message="Estimated input and output exceed the model context limit",
                request_id=request.request_id,
            )

    def _preflight_route(self, route: ModelRoute, request: ModelRequest) -> None:
        for target in route.targets:
            self._preflight_target(target, request)

    def _remaining(self, deadline: float, request_id: str) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ModelError(
                code=ErrorCode.TIMEOUT,
                message="Model call exceeded its total deadline",
                request_id=request_id,
            )
        return remaining

    def _rate_limiter(self, target: ModelTarget) -> AsyncTokenBucket | None:
        rate = target.limits.rate_limit_per_second
        if rate is None:
            return None
        limiter = self._rate_limiters.get(target.key)
        if limiter is None:
            limiter = AsyncTokenBucket(
                rate_per_second=rate,
                burst=target.limits.rate_limit_burst,
                clock=self._clock,
                sleeper=self._sleeper,
            )
            self._rate_limiters[target.key] = limiter
        return limiter

    def _circuit_breaker(self, target: ModelTarget) -> CircuitBreaker:
        breaker = self._circuit_breakers.get(target.key)
        if breaker is None:
            breaker = CircuitBreaker(
                threshold=target.limits.circuit_failure_threshold,
                recovery_seconds=target.limits.circuit_recovery_seconds,
                clock=self._clock,
            )
            self._circuit_breakers[target.key] = breaker
        return breaker

    async def _admit(
        self,
        target: ModelTarget,
        request_id: str,
        deadline: float,
    ) -> CircuitBreaker:
        limiter = self._rate_limiter(target)
        if limiter is not None:
            try:
                async with asyncio.timeout(self._remaining(deadline, request_id)):
                    await limiter.acquire()
            except TimeoutError:
                raise ModelError(
                    code=ErrorCode.TIMEOUT,
                    message="Rate-limit wait exceeded the total deadline",
                    request_id=request_id,
                ) from None
        breaker = self._circuit_breaker(target)
        await breaker.before_call(request_id)
        return breaker

    async def _backoff(
        self,
        target: ModelTarget,
        retry_index: int,
        request_id: str,
        deadline: float,
    ) -> None:
        exponent = min(retry_index, 30)
        base_delay = min(
            target.limits.retry_max_delay_seconds,
            target.limits.retry_base_delay_seconds * (2**exponent),
        )
        sample = self._random_source()
        jitter = 0.5 + min(1.0, max(0.0, sample))
        try:
            async with asyncio.timeout(self._remaining(deadline, request_id)):
                await self._sleeper(base_delay * jitter)
        except TimeoutError:
            raise ModelError(
                code=ErrorCode.TIMEOUT,
                message="Retry backoff exceeded the total deadline",
                request_id=request_id,
            ) from None

    @staticmethod
    def _unknown_error(request_id: str) -> ModelError:
        return ModelError(
            code=ErrorCode.UNKNOWN,
            message="Model provider failed unexpectedly",
            request_id=request_id,
        )

    async def _credential(
        self,
        target: ModelTarget,
        request_id: str,
        deadline: float,
    ) -> SecretValue | None:
        if target.credential is None:
            return None
        if self._secret_provider is None:
            raise ModelError(
                code=ErrorCode.AUTHENTICATION,
                message="Secret provider is not configured",
                request_id=request_id,
            )
        try:
            async with asyncio.timeout(self._remaining(deadline, request_id)):
                return await self._secret_provider.resolve(target.credential)
        except TimeoutError:
            raise ModelError(
                code=ErrorCode.TIMEOUT,
                message="Secret resolution exceeded the total deadline",
                request_id=request_id,
            ) from None
        except SecretResolutionError:
            raise ModelError(
                code=ErrorCode.AUTHENTICATION,
                message="Model credential is unavailable",
                request_id=request_id,
            ) from None

    def _attempt(
        self,
        *,
        attempt_no: int,
        target: ModelTarget,
        started_at: datetime,
        started_monotonic: float,
        outcome: AttemptOutcome,
        error_code: ErrorCode | None = None,
        provider_request_id: str | None = None,
        usage: ModelUsage | None = None,
        fallback_reason: ErrorCode | None = None,
    ) -> AttemptRecord:
        return AttemptRecord(
            attempt_no=attempt_no,
            provider=target.provider,
            model_id=target.model_id,
            started_at=started_at,
            duration_ms=max(0.0, (self._clock() - started_monotonic) * 1_000),
            outcome=outcome,
            error_code=error_code,
            provider_request_id=provider_request_id,
            usage=usage,
            fallback_reason=fallback_reason,
        )

    def _emit_attempt(self, attempt: AttemptRecord) -> None:
        if self._attempt_sink is None:
            return
        try:
            self._attempt_sink(attempt)
        except Exception:
            # Observability integrations must not change call behavior.
            return

    def _response(
        self,
        *,
        request: ModelRequest,
        target: ModelTarget,
        provider_response: ProviderResponse,
        attempts: tuple[AttemptRecord, ...],
    ) -> ModelResponse:
        structured_output = provider_response.structured_output
        if request.structured_output is not None:
            if structured_output is None:
                raise ModelError(
                    code=ErrorCode.STRUCTURED_OUTPUT_INVALID,
                    message="Provider returned no structured output",
                    request_id=request.request_id,
                )
            try:
                structured_output = request.structured_output.validate(structured_output)
            except Exception:
                raise ModelError(
                    code=ErrorCode.STRUCTURED_OUTPUT_INVALID,
                    message="Structured output did not satisfy the platform contract",
                    request_id=request.request_id,
                ) from None
        elif structured_output is not None:
            raise ModelError(
                code=ErrorCode.MALFORMED_RESPONSE,
                message="Provider returned unexpected structured output",
                request_id=request.request_id,
            )
        if structured_output is not None and provider_response.tool_calls:
            raise ModelError(
                code=ErrorCode.MALFORMED_RESPONSE,
                message="Provider mixed tool calls and structured output",
                request_id=request.request_id,
            )
        self._validate_tool_calls(provider_response, request)
        usages = tuple(attempt.usage for attempt in attempts if attempt.usage is not None)
        try:
            return ModelResponse(
                request_id=request.request_id,
                provider=target.provider,
                model_id=target.model_id,
                provider_request_id=provider_response.provider_request_id,
                content=provider_response.content,
                tool_calls=provider_response.tool_calls,
                structured_output=structured_output,
                finish_reason=provider_response.finish_reason,
                usage=provider_response.usage,
                total_usage=ModelUsage.aggregate(usages),
                attempts=attempts,
            )
        except (TypeError, ValueError):
            raise ModelError(
                code=ErrorCode.MALFORMED_RESPONSE,
                message="Provider response violated the platform contract",
                request_id=request.request_id,
            ) from None

    def _validate_tool_calls(self, response: ProviderResponse, request: ModelRequest) -> None:
        allowed = {tool.name for tool in request.tools}
        ids: set[str] = set()
        for call in response.tool_calls:
            if call.name not in allowed or call.id in ids:
                raise ModelError(
                    code=ErrorCode.MALFORMED_RESPONSE,
                    message="Provider returned an unknown or duplicate tool call",
                    request_id=request.request_id,
                )
            ids.add(call.id)

    async def complete(self, route: ModelRoute, request: ModelRequest) -> ModelResponse:
        deadline = self._clock() + route.total_timeout_seconds
        self._preflight_route(route, request)
        attempts: list[AttemptRecord] = []
        fallback_reason: ErrorCode | None = None
        last_error: ModelError | None = None

        for target in route.targets:
            provider = self._provider(target, request.request_id)
            exhausted_error: ModelError | None = None
            for retry_index in range(target.limits.max_retries + 1):
                started_at = datetime.now(UTC)
                started_monotonic = self._clock()
                breaker: CircuitBreaker | None = None
                admitted = False
                provider_response: ProviderResponse | None = None
                try:
                    breaker = await self._admit(target, request.request_id, deadline)
                    admitted = True
                    credential = await self._credential(target, request.request_id, deadline)
                    timeout = min(
                        target.limits.attempt_timeout_seconds,
                        self._remaining(deadline, request.request_id),
                    )
                    async with asyncio.timeout(timeout):
                        provider_response = await provider.complete(target, request, credential)
                    succeeded = self._attempt(
                        attempt_no=len(attempts) + 1,
                        target=target,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        outcome=AttemptOutcome.SUCCEEDED,
                        provider_request_id=provider_response.provider_request_id,
                        usage=provider_response.usage,
                        fallback_reason=fallback_reason,
                    )
                    response = self._response(
                        request=request,
                        target=target,
                        provider_response=provider_response,
                        attempts=(*attempts, succeeded),
                    )
                except asyncio.CancelledError:
                    if admitted and breaker is not None:
                        await breaker.record_cancelled()
                    cancelled = self._attempt(
                        attempt_no=len(attempts) + 1,
                        target=target,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        outcome=AttemptOutcome.CANCELLED,
                        error_code=ErrorCode.CANCELLED,
                        provider_request_id=(
                            provider_response.provider_request_id
                            if provider_response is not None
                            else None
                        ),
                        usage=(provider_response.usage if provider_response is not None else None),
                        fallback_reason=fallback_reason,
                    )
                    self._emit_attempt(cancelled)
                    raise
                except TimeoutError:
                    error = ModelError(
                        code=ErrorCode.TIMEOUT,
                        message="Model provider attempt timed out",
                        request_id=request.request_id,
                    )
                except ModelError as caught:
                    error = caught
                except Exception:
                    error = self._unknown_error(request.request_id)
                else:
                    if breaker is not None:
                        await breaker.record_success()
                    self._emit_attempt(succeeded)
                    return response

                if admitted and breaker is not None:
                    await breaker.record_failure(error.code)
                usage = error.usage
                if usage is None and provider_response is not None:
                    usage = provider_response.usage
                failed = self._attempt(
                    attempt_no=len(attempts) + 1,
                    target=target,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    outcome=AttemptOutcome.FAILED,
                    error_code=error.code,
                    provider_request_id=error.provider_request_id,
                    usage=usage,
                    fallback_reason=fallback_reason,
                )
                attempts.append(failed)
                self._emit_attempt(failed)
                exhausted_error = error
                last_error = error
                if error.retryable and retry_index < target.limits.max_retries:
                    try:
                        await self._backoff(
                            target,
                            retry_index,
                            request.request_id,
                            deadline,
                        )
                    except ModelError as deadline_error:
                        raise deadline_error.with_attempts(tuple(attempts)) from None
                    continue
                break

            if exhausted_error is None:
                raise RuntimeError("model attempt loop did not produce an outcome")
            if not exhausted_error.fallback_allowed:
                raise exhausted_error.with_attempts(tuple(attempts)) from None
            fallback_reason = exhausted_error.code

        if last_error is None:
            raise RuntimeError("model route did not contain a target")
        raise last_error.with_attempts(tuple(attempts)) from None

    async def stream(
        self,
        route: ModelRoute,
        request: ModelRequest,
    ) -> AsyncIterator[ModelChunk]:
        deadline = self._clock() + route.total_timeout_seconds
        self._preflight_route(route, request)
        attempts: list[AttemptRecord] = []
        sequence = 0
        visible = False
        fallback_reason: ErrorCode | None = None
        last_error: ModelError | None = None

        for target in route.targets:
            provider = self._provider(target, request.request_id)
            exhausted_error: ModelError | None = None
            for retry_index in range(target.limits.max_retries + 1):
                started_at = datetime.now(UTC)
                started_monotonic = self._clock()
                breaker: CircuitBreaker | None = None
                admitted = False
                usage: ModelUsage | None = None
                provider_request_id: str | None = None
                text_fragments: list[str] = []
                finish_reason = FinishReason.UNKNOWN
                structured_output: object | None = None
                tool_buffers: dict[int, _ToolCallBuffer] = {}
                try:
                    breaker = await self._admit(target, request.request_id, deadline)
                    admitted = True
                    credential = await self._credential(target, request.request_id, deadline)
                    iterator = provider.stream(target, request, credential)
                    try:
                        first = True
                        while True:
                            timeout = min(
                                target.limits.stream_first_byte_timeout_seconds
                                if first
                                else target.limits.stream_idle_timeout_seconds,
                                self._remaining(deadline, request.request_id),
                            )
                            try:
                                async with asyncio.timeout(timeout):
                                    provider_chunk = await anext(iterator)
                            except StopAsyncIteration:
                                break
                            first = False
                            if provider_chunk.text_delta:
                                visible = True
                                text_fragments.append(provider_chunk.text_delta)
                                yield ModelChunk(
                                    sequence=sequence,
                                    kind=ChunkKind.TEXT_DELTA,
                                    text_delta=provider_chunk.text_delta,
                                )
                                sequence += 1
                            if provider_chunk.tool_call_delta is not None:
                                visible = True
                                delta = provider_chunk.tool_call_delta
                                buffer = tool_buffers.setdefault(
                                    delta.index, _ToolCallBuffer(index=delta.index)
                                )
                                buffer.append(delta, request.request_id)
                                yield ModelChunk(
                                    sequence=sequence,
                                    kind=ChunkKind.TOOL_CALL_DELTA,
                                    tool_call_delta=delta,
                                )
                                sequence += 1
                            if provider_chunk.usage is not None:
                                usage = provider_chunk.usage
                                yield ModelChunk(
                                    sequence=sequence,
                                    kind=ChunkKind.USAGE,
                                    usage=usage,
                                )
                                sequence += 1
                            if provider_chunk.finish_reason is not None:
                                finish_reason = provider_chunk.finish_reason
                            if provider_chunk.provider_request_id is not None:
                                provider_request_id = provider_chunk.provider_request_id
                            if provider_chunk.structured_output is not None:
                                if structured_output is not None:
                                    raise ModelError(
                                        code=ErrorCode.MALFORMED_RESPONSE,
                                        message=("Provider returned duplicate structured output"),
                                        request_id=request.request_id,
                                    )
                                structured_output = provider_chunk.structured_output
                    finally:
                        close = getattr(iterator, "aclose", None)
                        if close is not None:
                            await close()

                    tool_calls = tuple(
                        tool_buffers[index].build(request.request_id)
                        for index in sorted(tool_buffers)
                    )
                    provider_response = ProviderResponse(
                        content=((TextPart("".join(text_fragments)),) if text_fragments else ()),
                        finish_reason=finish_reason,
                        usage=usage,
                        provider_request_id=provider_request_id,
                        tool_calls=tool_calls,
                        structured_output=structured_output,
                    )
                    succeeded = self._attempt(
                        attempt_no=len(attempts) + 1,
                        target=target,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        outcome=AttemptOutcome.SUCCEEDED,
                        provider_request_id=provider_request_id,
                        usage=usage,
                        fallback_reason=fallback_reason,
                    )
                    response = self._response(
                        request=request,
                        target=target,
                        provider_response=provider_response,
                        attempts=(*attempts, succeeded),
                    )
                except (asyncio.CancelledError, GeneratorExit):
                    if admitted and breaker is not None:
                        await breaker.record_cancelled()
                    cancelled = self._attempt(
                        attempt_no=len(attempts) + 1,
                        target=target,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        outcome=AttemptOutcome.CANCELLED,
                        error_code=ErrorCode.CANCELLED,
                        provider_request_id=provider_request_id,
                        usage=usage,
                        fallback_reason=fallback_reason,
                    )
                    self._emit_attempt(cancelled)
                    raise
                except TimeoutError:
                    error = ModelError(
                        code=ErrorCode.TIMEOUT,
                        message="Model stream timed out",
                        request_id=request.request_id,
                    )
                except ModelError as caught:
                    error = caught
                except Exception:
                    error = self._unknown_error(request.request_id)
                else:
                    if breaker is not None:
                        await breaker.record_success()
                    self._emit_attempt(succeeded)
                    yield ModelChunk(
                        sequence=sequence,
                        kind=ChunkKind.TERMINAL,
                        terminal=StreamTerminal(response),
                    )
                    return

                if admitted and breaker is not None:
                    await breaker.record_failure(error.code)
                failed = self._attempt(
                    attempt_no=len(attempts) + 1,
                    target=target,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    outcome=AttemptOutcome.FAILED,
                    error_code=error.code,
                    provider_request_id=error.provider_request_id,
                    usage=error.usage or usage,
                    fallback_reason=fallback_reason,
                )
                attempts.append(failed)
                self._emit_attempt(failed)
                final_error = error.with_attempts(tuple(attempts))
                if visible:
                    yield ModelChunk(
                        sequence=sequence,
                        kind=ChunkKind.ERROR,
                        error=final_error,
                    )
                    return
                exhausted_error = error
                last_error = error
                if error.retryable and retry_index < target.limits.max_retries:
                    try:
                        await self._backoff(
                            target,
                            retry_index,
                            request.request_id,
                            deadline,
                        )
                    except ModelError as deadline_error:
                        raise deadline_error.with_attempts(tuple(attempts)) from None
                    continue
                break

            if exhausted_error is None:
                raise RuntimeError("model stream loop did not produce an outcome")
            if not exhausted_error.fallback_allowed:
                raise exhausted_error.with_attempts(tuple(attempts)) from None
            fallback_reason = exhausted_error.code

        if last_error is None:
            raise RuntimeError("model route did not contain a target")
        raise last_error.with_attempts(tuple(attempts)) from None
