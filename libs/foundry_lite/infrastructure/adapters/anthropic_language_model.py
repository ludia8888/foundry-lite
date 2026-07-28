"""Direct Anthropic Messages API adapter behind the governed Model Gateway."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.language_model import (
    ModelEvent,
    ModelMediaContentResolver,
    ModelRequest,
    ModelResponse,
)
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.infrastructure.adapters.anthropic_message_codec import (
    build_anthropic_payload,
    parse_anthropic_response,
)

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_RETRIES = 2
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})


@dataclass(frozen=True)
class AnthropicHttpResponse:
    """Transport-neutral response used by unit tests and the urllib implementation."""

    status_code: int
    headers: Mapping[str, str]
    body: Mapping[str, object]


class AnthropicTransportFailure(Exception):
    """Network failure with no provider response body or credential material."""

    def __init__(self, kind: AdapterFailureKind) -> None:
        super().__init__(kind)
        self.kind: AdapterFailureKind = kind


AnthropicTransport = Callable[[Mapping[str, str], Mapping[str, object], int], AnthropicHttpResponse]


class AnthropicLanguageModel:
    """Anthropic adapter with secretRef lookup, typed errors, and bounded response retries."""

    profile_name = "anthropic"
    supported_provider_profiles: tuple[str, ...] = ("anthropic",)

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        media_resolver: ModelMediaContentResolver | None = None,
        transport: AnthropicTransport | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._secret_provider = secret_provider
        self._media_resolver = media_resolver
        self._transport = transport or _urllib_transport
        self._max_retries = max(0, max_retries)
        self._sleeper = sleeper
        self._clock = clock

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = build_anthropic_payload(request, self._media_resolver)
        route = request.resolved_route
        if route is None:
            raise _adapter_failure("validation", False, "route_required", request.timeout_seconds)
        secret = self._secret_provider.get_secret(route.secret_ref)
        headers = _request_headers(secret.value)
        started = self._clock()
        response = self._execute(headers, payload, request.timeout_seconds, started)
        latency_ms = int((self._clock() - started) * 1000)
        return parse_anthropic_response(request, response.body, response.headers, latency_ms=latency_ms)

    def stream(self, request: ModelRequest) -> Sequence[ModelEvent]:
        response = self.complete(request)
        return (ModelEvent(delta_text=response.content, finish_reason=response.finish_reason),)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("complete", "authentication", False, "Anthropic credential was rejected."),
                AdapterFailureMode("complete", "rate_limited", True, "Anthropic rate limit was reached."),
                AdapterFailureMode("complete", "timeout", True, "Anthropic request timed out."),
                AdapterFailureMode("complete", "unavailable", True, "Anthropic service is unavailable."),
                AdapterFailureMode("complete", "validation", False, "Anthropic request or response was invalid."),
            ),
        )

    def _execute(
        self,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: int,
        started: float,
    ) -> AnthropicHttpResponse:
        for attempt in range(self._max_retries + 1):
            remaining_timeout = _remaining_timeout_seconds(self._clock(), started, timeout_seconds)
            try:
                response = self._transport_call(headers, payload, remaining_timeout)
            except AdapterError as exc:
                self._wait_before_retry(exc.failure, {}, attempt, started, timeout_seconds)
                continue
            if 200 <= response.status_code < 300:
                return response
            failure = _http_failure(response, timeout_seconds, attempt + 1)
            self._wait_before_retry(failure, response.headers, attempt, started, timeout_seconds)
        raise _adapter_failure("unknown", False, "retry_loop_exhausted", timeout_seconds)

    def _wait_before_retry(
        self,
        failure: AdapterFailure,
        headers: Mapping[str, str],
        attempt: int,
        started: float,
        timeout_seconds: int,
    ) -> None:
        if not failure.is_retryable or attempt >= self._max_retries:
            raise AdapterError(failure)
        delay = _retry_delay(headers, attempt)
        if self._clock() - started + delay >= timeout_seconds:
            raise AdapterError(failure)
        self._sleeper(delay)

    def _transport_call(
        self,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: int,
    ) -> AnthropicHttpResponse:
        try:
            return self._transport(headers, payload, timeout_seconds)
        except AnthropicTransportFailure as exc:
            is_retryable = exc.kind in {"timeout", "unavailable"}
            raise _adapter_failure(exc.kind, is_retryable, "transport_failure", timeout_seconds) from exc


def _urllib_transport(
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    timeout_seconds: int,
) -> AnthropicHttpResponse:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    request = Request(_MESSAGES_URL, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - fixed official endpoint.
            return AnthropicHttpResponse(
                status_code=int(response.status),
                headers=_normalized_headers(response.headers),
                body=_response_json(response),
            )
    except HTTPError as exc:
        return AnthropicHttpResponse(
            status_code=exc.code,
            headers=_normalized_headers(exc.headers),
            body=_response_json(exc),
        )
    except TimeoutError as exc:
        raise AnthropicTransportFailure("timeout") from exc
    except URLError as exc:
        raise AnthropicTransportFailure("unavailable") from exc


def _request_headers(api_key: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "user-agent": "Foundry-lite/anthropic-language-model",
    }


def _remaining_timeout_seconds(now: float, started: float, timeout_seconds: int) -> int:
    remaining = timeout_seconds - (now - started)
    if remaining <= 0:
        raise _adapter_failure("timeout", True, "request_budget_exhausted", timeout_seconds)
    return max(1, int(remaining))


def _json_body(raw: bytes) -> Mapping[str, object]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _response_json(response: object) -> Mapping[str, object]:
    read = getattr(response, "read", None)
    if not callable(read):
        raise AnthropicTransportFailure("validation")
    raw = read(_MAX_RESPONSE_BYTES + 1)
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise AnthropicTransportFailure("validation")
    return _json_body(raw)


def _normalized_headers(headers: object) -> dict[str, str]:
    items = getattr(headers, "items", None)
    if not callable(items):
        return {}
    pairs = cast(Iterable[tuple[object, object]], items())
    return {str(key).lower(): str(value) for key, value in pairs}


def _http_failure(
    response: AnthropicHttpResponse,
    timeout_seconds: int,
    attempt: int,
) -> AdapterFailure:
    kind = _failure_kind(response.status_code)
    error_type = _provider_error_type(response.body)
    request_id = response.headers.get("request-id") or _text(response.body.get("request_id"))
    retry_after = _retry_after_seconds(response.headers)
    details: dict[str, object] = {
        "reason": "anthropic_http_error",
        "statusCode": response.status_code,
        "errorType": error_type,
        "requestId": request_id,
        "attempt": attempt,
    }
    if retry_after is not None:
        details["retryAfterSeconds"] = retry_after
    return AdapterFailure(
        adapter_profile="anthropic",
        operation="complete",
        kind=kind,
        is_retryable=response.status_code in _RETRYABLE_STATUS_CODES,
        operator_message=f"Anthropic Messages API returned HTTP {response.status_code} ({error_type}).",
        timeout_seconds=timeout_seconds if kind == "timeout" else None,
        details=details,
    )


def _adapter_failure(
    kind: AdapterFailureKind,
    is_retryable: bool,
    reason: str,
    timeout_seconds: int,
) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="anthropic",
            operation="complete",
            kind=kind,
            is_retryable=is_retryable,
            operator_message=f"Anthropic language-model request failed ({reason}).",
            timeout_seconds=timeout_seconds if kind == "timeout" else None,
            details={"reason": reason},
        )
    )


def _failure_kind(status_code: int) -> AdapterFailureKind:
    if status_code == 400 or status_code == 413:
        return "validation"
    if status_code == 401:
        return "authentication"
    if status_code in {402, 403}:
        return "authorization"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limited"
    if status_code == 504:
        return "timeout"
    if status_code in {500, 502, 503, 529}:
        return "unavailable"
    return "unknown"


def _provider_error_type(body: Mapping[str, object]) -> str:
    error = body.get("error")
    if isinstance(error, Mapping):
        return _text(error.get("type")) or "unknown_error"
    return "unknown_error"


def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    retry_after = _retry_after_seconds(headers)
    return retry_after if retry_after is not None else min(4.0, 0.5 * (2**attempt))


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
