"""Render HTTP adapter for exact-revision infrastructure deploys and rollback."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from typing import Literal, Protocol, cast
from urllib.parse import urlencode, urlsplit

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentCandidateQuery,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentMutationResult,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentOperation,
    InfrastructureDeploymentOutcomeUnknown,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentServicePolicyObservation,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentStartRequest,
    InfrastructureDeploymentStatus,
)
from foundry_lite.application.ports.secret_provider import SecretProvider

_RENDER_ORIGIN = "https://api.render.com/v1"
_RENDER_HOST = "api.render.com"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_MAX_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_CONFIGURED_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_CANDIDATE_PAGES = 5
_SERVICE_ID_PATTERN = re.compile(r"^srv-[a-z0-9-]{3,64}$")
_DEPLOY_ID_PATTERN = re.compile(r"^dep-[a-z0-9-]{3,64}$")
_COMMIT_ID_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_TRACE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_REPOSITORY_COORDINATE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

_STATUS_MAP: dict[str, InfrastructureDeploymentStatus] = {
    "created": "queued",
    "queued": "queued",
    "build_in_progress": "building",
    "pre_deploy_in_progress": "preparing",
    "update_in_progress": "deploying",
    "live": "live",
    "deactivated": "deactivated",
    "build_failed": "failed",
    "pre_deploy_failed": "failed",
    "update_failed": "failed",
    "canceled": "canceled",
}
_TERMINAL_PROVIDER_STATUSES = frozenset(
    {"live", "deactivated", "build_failed", "pre_deploy_failed", "update_failed", "canceled"}
)
_KNOWN_REJECTION_CODES = frozenset({400, 401, 402, 403, 404, 406, 409, 410, 429})

RenderHttpMethod = Literal["GET", "POST"]
RenderTransportFailureKind = Literal["timeout", "unavailable", "response_too_large"]


@dataclass(frozen=True, slots=True)
class RenderHttpRequest:
    """A bounded request for an injectable Render HTTP transport."""

    method: RenderHttpMethod
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES


@dataclass(frozen=True, slots=True)
class RenderHttpResponse:
    """Raw bounded response kept transport-neutral for contract tests."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class RenderTransportError(RuntimeError):
    """Transport failure without a raw URL, header, body, or credential message."""

    def __init__(self, kind: RenderTransportFailureKind) -> None:
        super().__init__(kind)
        self.kind = kind


class RenderHttpTransport(Protocol):
    """Send one request without retries or redirects."""

    def send(self, request: RenderHttpRequest) -> RenderHttpResponse: ...


class UrllibRenderHttpTransport:
    """Standard-library HTTPS transport pinned to Render's official API host."""

    def send(self, request: RenderHttpRequest) -> RenderHttpResponse:
        target = _fixed_render_target(request.url)
        connection = HTTPSConnection(_RENDER_HOST, 443, timeout=request.timeout_seconds)
        try:
            connection.request(request.method, target, body=request.body, headers=dict(request.headers))
            response = connection.getresponse()
            body = response.read(request.max_response_bytes + 1)
            if len(body) > request.max_response_bytes:
                raise RenderTransportError("response_too_large")
            return RenderHttpResponse(
                status_code=response.status,
                headers=_normalized_headers(response.headers.items()),
                body=body,
            )
        except TimeoutError as exc:
            raise RenderTransportError("timeout") from exc
        except (HTTPException, OSError) as exc:
            raise RenderTransportError("unavailable") from exc
        finally:
            connection.close()


class RenderInfrastructureDeploymentAdapter:
    """Render deploy adapter with exact commits and fail-closed mutation outcomes."""

    profile_name = "render-infrastructure-deployment"

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        token_secret_ref: str,
        transport: RenderHttpTransport | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        _validate_adapter_config(token_secret_ref, timeout_seconds, max_response_bytes)
        self._secret_provider = secret_provider
        self._token_secret_ref = token_secret_ref
        self._transport = transport or UrllibRenderHttpTransport()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=_failure_modes())

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        service_id = _service_id(request.service_id, "get_service_policy")
        _validate_read_trace(
            request.tenant_id,
            request.request_id,
            request.correlation_id,
            "get_service_policy",
        )
        response = self._request(
            "get_service_policy",
            "GET",
            f"{_RENDER_ORIGIN}/services/{service_id}",
            None,
            None,
            is_mutation=False,
        )
        self._require_status(response, "get_service_policy", 200)
        return self._service_policy_observation(response, service_id)

    def start(
        self,
        request: InfrastructureDeploymentStartRequest,
    ) -> InfrastructureDeploymentMutationResult:
        service_id = _service_id(request.service_id, "start", request.idempotency_key)
        commit_id = _commit_id(request.commit_id, "start", request.idempotency_key)
        _validate_mutation_trace(request, "start")
        response = self._request(
            "start",
            "POST",
            f"{_RENDER_ORIGIN}/services/{service_id}/deploys",
            {"commitId": commit_id},
            request.idempotency_key,
            is_mutation=True,
        )
        return self._start_result(response, service_id, commit_id, request.idempotency_key)

    def get(
        self,
        request: InfrastructureDeploymentGetRequest,
    ) -> InfrastructureDeploymentObservation:
        service_id = _service_id(request.service_id, "get")
        deploy_id = _deploy_id(request.deploy_id, "get")
        _validate_read_trace(request.tenant_id, request.request_id, request.correlation_id, "get")
        response = self._request(
            "get",
            "GET",
            f"{_RENDER_ORIGIN}/services/{service_id}/deploys/{deploy_id}",
            None,
            None,
            is_mutation=False,
        )
        self._require_status(response, "get", 200)
        return self._read_observation(response, service_id, "get")

    def list_candidates(
        self,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[InfrastructureDeploymentObservation, ...]:
        service_id = _service_id(query.service_id, "list_candidates")
        commit_id = _commit_id(query.commit_id, "list_candidates")
        _validate_candidate_query(query)
        cursor: str | None = None
        matches: list[InfrastructureDeploymentObservation] = []
        for _ in range(_MAX_CANDIDATE_PAGES):
            response = self._candidate_page_response(service_id, query, cursor)
            page_matches, cursor = self._candidate_observations(response, service_id, commit_id, query)
            matches.extend(page_matches)
            if cursor is None:
                return tuple(matches)
        raise _response_error("list_candidates", "candidate_scan_exceeded_bound", response)

    def _candidate_page_response(
        self,
        service_id: str,
        query: InfrastructureDeploymentCandidateQuery,
        cursor: str | None,
    ) -> RenderHttpResponse:
        response = self._request(
            "list_candidates",
            "GET",
            _candidate_url(service_id, query, cursor),
            None,
            None,
            is_mutation=False,
        )
        self._require_status(response, "list_candidates", 200)
        return response

    def rollback(
        self,
        request: InfrastructureDeploymentRollbackRequest,
    ) -> InfrastructureDeploymentMutationResult:
        service_id = _service_id(request.service_id, "rollback", request.idempotency_key)
        target_id = _deploy_id(request.target_deploy_id, "rollback", request.idempotency_key)
        target_commit_id = _commit_id(request.target_commit_id, "rollback", request.idempotency_key)
        _validate_mutation_trace(request, "rollback")
        response = self._request(
            "rollback",
            "POST",
            f"{_RENDER_ORIGIN}/services/{service_id}/rollback",
            {"deployId": target_id},
            request.idempotency_key,
            is_mutation=True,
        )
        return self._rollback_result(
            response,
            service_id,
            target_id,
            target_commit_id,
            request.idempotency_key,
        )

    def _start_result(
        self,
        response: RenderHttpResponse,
        service_id: str,
        commit_id: str,
        idempotency_key: str,
    ) -> InfrastructureDeploymentMutationResult:
        if response.status_code == 202:
            return _unknown_mutation_result("start", response.status_code, None, "queued_without_provider_receipt")
        self._require_mutation_created(response, "start", idempotency_key)
        observation = self._mutation_observation(response, service_id, "start", idempotency_key)
        if observation.commit_id != commit_id:
            raise _outcome_unknown("start", "provider_commit_mismatch", idempotency_key, response)
        return _accepted_mutation_result("start", response.status_code, observation, None)

    def _rollback_result(
        self,
        response: RenderHttpResponse,
        service_id: str,
        target_id: str,
        target_commit_id: str,
        idempotency_key: str,
    ) -> InfrastructureDeploymentMutationResult:
        if response.status_code == 202:
            return _unknown_mutation_result(
                "rollback",
                response.status_code,
                target_id,
                "queued_without_provider_receipt",
            )
        self._require_mutation_created(response, "rollback", idempotency_key)
        observation = self._mutation_observation(response, service_id, "rollback", idempotency_key)
        if observation.deploy_id == target_id:
            raise _outcome_unknown("rollback", "rollback_did_not_return_new_deploy", idempotency_key, response)
        if observation.commit_id != target_commit_id:
            raise _outcome_unknown("rollback", "provider_commit_mismatch", idempotency_key, response)
        if observation.trigger != "rollback":
            raise _outcome_unknown("rollback", "provider_rollback_trigger_mismatch", idempotency_key, response)
        return _accepted_mutation_result("rollback", response.status_code, observation, target_id)

    def _mutation_observation(
        self,
        response: RenderHttpResponse,
        service_id: str,
        operation: Literal["start", "rollback"],
        idempotency_key: str,
    ) -> InfrastructureDeploymentObservation:
        try:
            return _observation(_json_object(response.body), service_id, _provider_request_id(response.headers))
        except _RenderResponseInvalid as exc:
            raise _outcome_unknown(operation, exc.reason, idempotency_key, response) from exc

    def _read_observation(
        self,
        response: RenderHttpResponse,
        service_id: str,
        operation: Literal["get", "list_candidates"],
    ) -> InfrastructureDeploymentObservation:
        try:
            return _observation(_json_object(response.body), service_id, _provider_request_id(response.headers))
        except _RenderResponseInvalid as exc:
            raise _response_error(operation, exc.reason, response) from exc

    def _service_policy_observation(
        self,
        response: RenderHttpResponse,
        service_id: str,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        try:
            return _service_policy_observation(
                _json_object(response.body),
                service_id,
                _provider_request_id(response.headers),
            )
        except _RenderResponseInvalid as exc:
            raise _response_error("get_service_policy", exc.reason, response) from exc

    def _candidate_observations(
        self,
        response: RenderHttpResponse,
        service_id: str,
        commit_id: str,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[tuple[InfrastructureDeploymentObservation, ...], str | None]:
        try:
            rows = _json_list(response.body)
            observations = tuple(
                _observation(_deploy_from_list_row(row), service_id, _provider_request_id(response.headers))
                for row in rows
            )
            cursor = _next_candidate_cursor(rows, query.limit)
        except _RenderResponseInvalid as exc:
            raise _response_error("list_candidates", exc.reason, response) from exc
        matches = tuple(
            item for item in observations if item.commit_id == commit_id and _is_candidate_in_window(item, query)
        )
        return matches, cursor

    def _request(
        self,
        operation: InfrastructureDeploymentOperation,
        method: RenderHttpMethod,
        url: str,
        payload: Mapping[str, object] | None,
        idempotency_key: str | None,
        *,
        is_mutation: bool,
    ) -> RenderHttpResponse:
        headers = self._headers(operation, idempotency_key)
        request = RenderHttpRequest(
            method=method,
            url=url,
            headers=headers,
            body=_request_body(payload),
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        try:
            response = self._transport.send(request)
        except RenderTransportError as exc:
            if is_mutation:
                raise _transport_outcome_unknown(operation, idempotency_key, exc, self._timeout_seconds) from exc
            raise _transport_read_error(operation, exc, self._timeout_seconds) from exc
        except Exception:  # noqa: BLE001 - never leak an injected transport's raw credential-bearing error.
            failure = RenderTransportError("unavailable")
            if is_mutation:
                raise _transport_outcome_unknown(operation, idempotency_key, failure, self._timeout_seconds) from None
            raise _transport_read_error(operation, failure, self._timeout_seconds) from None
        if len(response.body) > self._max_response_bytes:
            if is_mutation:
                raise _outcome_unknown(operation, "response_too_large", idempotency_key, response)
            raise _response_error(operation, "response_too_large", response)
        return response

    def _headers(
        self,
        operation: InfrastructureDeploymentOperation,
        idempotency_key: str | None,
    ) -> dict[str, str]:
        try:
            token = self._secret_provider.get_secret(self._token_secret_ref)
        except Exception:  # noqa: BLE001 - secret-provider messages are untrusted and must not escape.
            raise _secret_error(operation, idempotency_key) from None
        return {
            "accept": "application/json",
            "authorization": f"Bearer {token.value}",
            "content-type": "application/json",
            "user-agent": "Foundry-lite/render-infrastructure-deployment",
        }

    def _require_status(
        self,
        response: RenderHttpResponse,
        operation: Literal["get_service_policy", "get", "list_candidates"],
        expected: int,
    ) -> None:
        if response.status_code != expected:
            raise _http_error(operation, response, None, is_mutation=False)

    def _require_mutation_created(
        self,
        response: RenderHttpResponse,
        operation: Literal["start", "rollback"],
        idempotency_key: str,
    ) -> None:
        if response.status_code == 201:
            return
        if response.status_code in _KNOWN_REJECTION_CODES:
            raise _http_error(operation, response, idempotency_key, is_mutation=True)
        raise _outcome_unknown(operation, "unexpected_mutation_response", idempotency_key, response)


class _RenderResponseInvalid(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _failure_modes() -> tuple[AdapterFailureMode, ...]:
    modes: list[AdapterFailureMode] = []
    for operation in ("start", "rollback"):
        modes.extend(
            (
                AdapterFailureMode(
                    operation,
                    "validation",
                    False,
                    "Render deployment input was invalid.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "authentication",
                    False,
                    "Render API authentication failed.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "rate_limited",
                    True,
                    "Render rejected the request before acceptance.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "timeout",
                    False,
                    "Render mutation outcome requires reconciliation.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "unavailable",
                    False,
                    "Render mutation outcome requires reconciliation.",
                    has_required_idempotency_key=True,
                ),
            )
        )
    for operation in ("get_service_policy", "get", "list_candidates"):
        modes.extend(
            (
                AdapterFailureMode(operation, "validation", False, "Render deployment response was invalid."),
                AdapterFailureMode(operation, "authentication", False, "Render API authentication failed."),
                AdapterFailureMode(operation, "rate_limited", True, "Render API rate limit was reached."),
                AdapterFailureMode(operation, "timeout", True, "Render read timed out."),
                AdapterFailureMode(operation, "unavailable", True, "Render read is temporarily unavailable."),
            )
        )
    return tuple(modes)


def _validate_adapter_config(token_secret_ref: str, timeout_seconds: float, max_response_bytes: int) -> None:
    if not token_secret_ref.strip() or len(token_secret_ref) > 256:
        raise ValueError("token_secret_ref must be a non-empty bounded secret reference")
    if timeout_seconds <= 0 or timeout_seconds > _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be within (0, {_MAX_TIMEOUT_SECONDS}]")
    if max_response_bytes <= 0 or max_response_bytes > _MAX_CONFIGURED_RESPONSE_BYTES:
        raise ValueError(f"max_response_bytes must be within (0, {_MAX_CONFIGURED_RESPONSE_BYTES}]")


def _validate_mutation_trace(
    request: InfrastructureDeploymentStartRequest | InfrastructureDeploymentRollbackRequest,
    operation: Literal["start", "rollback"],
) -> None:
    _validate_read_trace(request.tenant_id, request.request_id, request.correlation_id, operation)
    _trace_value(request.idempotency_key, "idempotency_key", operation, request.idempotency_key)


def _validate_read_trace(tenant_id: str, request_id: str, correlation_id: str, operation: str) -> None:
    _trace_value(tenant_id, "tenant_id", operation)
    _trace_value(request_id, "request_id", operation)
    _trace_value(correlation_id, "correlation_id", operation)


def _validate_candidate_query(query: InfrastructureDeploymentCandidateQuery) -> None:
    _validate_read_trace(query.tenant_id, query.request_id, query.correlation_id, "list_candidates")
    if query.created_after.tzinfo is None or query.created_before.tzinfo is None:
        raise _validation_error("list_candidates", "candidate_window_must_be_timezone_aware")
    if query.created_after >= query.created_before:
        raise _validation_error("list_candidates", "candidate_window_is_not_increasing")
    if query.limit < 1 or query.limit > 100:
        raise _validation_error("list_candidates", "candidate_limit_out_of_range")


def _trace_value(value: str, field_name: str, operation: str, idempotency_key: str | None = None) -> str:
    if not _TRACE_VALUE_PATTERN.fullmatch(value):
        raise _validation_error(operation, f"invalid_{field_name}", idempotency_key)
    return value


def _service_id(value: str, operation: str, idempotency_key: str | None = None) -> str:
    if not _SERVICE_ID_PATTERN.fullmatch(value):
        raise _validation_error(operation, "invalid_service_id", idempotency_key)
    return value


def _deploy_id(value: str, operation: str, idempotency_key: str | None = None) -> str:
    if not _DEPLOY_ID_PATTERN.fullmatch(value):
        raise _validation_error(operation, "invalid_deploy_id", idempotency_key)
    return value


def _commit_id(value: str, operation: str, idempotency_key: str | None = None) -> str:
    normalized = value.lower()
    if not _COMMIT_ID_PATTERN.fullmatch(normalized):
        raise _validation_error(operation, "exact_commit_sha_required", idempotency_key)
    return normalized


def _candidate_url(
    service_id: str,
    query: InfrastructureDeploymentCandidateQuery,
    cursor: str | None = None,
) -> str:
    values: dict[str, object] = {
        "createdAfter": _render_timestamp(query.created_after),
        "createdBefore": _render_timestamp(query.created_before),
        "limit": query.limit,
    }
    if cursor is not None:
        values["cursor"] = cursor
    params = urlencode(values)
    return f"{_RENDER_ORIGIN}/services/{service_id}/deploys?{params}"


def _next_candidate_cursor(
    rows: tuple[Mapping[str, object], ...],
    page_limit: int,
) -> str | None:
    """Continue a full page; malformed pagination evidence fails closed."""

    if len(rows) < page_limit:
        return None
    if len(rows) > page_limit:
        raise _RenderResponseInvalid("candidate_page_exceeded_requested_limit")
    cursor = rows[-1].get("cursor")
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise _RenderResponseInvalid("candidate_page_cursor_is_invalid")
    return cursor


def _is_candidate_in_window(
    observation: InfrastructureDeploymentObservation,
    query: InfrastructureDeploymentCandidateQuery,
) -> bool:
    """Recheck provider rows locally instead of trusting server-side filtering."""

    created_at = observation.created_at
    return (
        created_at is not None
        and observation.service_id == query.service_id
        and query.created_after <= created_at <= query.created_before
    )


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _fixed_render_target(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != _RENDER_HOST or not parts.path.startswith("/v1/"):
        raise RenderTransportError("unavailable")
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _request_body(payload: Mapping[str, object] | None) -> bytes | None:
    if payload is None:
        return None
    return json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8")


def _normalized_headers(items: Iterable[tuple[object, object]]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in items}


def _json_object(body: bytes) -> Mapping[str, object]:
    parsed = _json_value(body)
    if not isinstance(parsed, Mapping):
        raise _RenderResponseInvalid("response_is_not_an_object")
    return cast(Mapping[str, object], parsed)


def _json_list(body: bytes) -> tuple[Mapping[str, object], ...]:
    parsed = _json_value(body)
    if not isinstance(parsed, list) or not all(isinstance(item, Mapping) for item in parsed):
        raise _RenderResponseInvalid("response_is_not_a_deploy_list")
    return tuple(cast(Mapping[str, object], item) for item in parsed)


def _json_value(body: bytes) -> object:
    try:
        return cast(object, json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _RenderResponseInvalid("response_is_not_valid_json") from exc


def _deploy_from_list_row(row: Mapping[str, object]) -> Mapping[str, object]:
    deploy = row.get("deploy")
    if not isinstance(deploy, Mapping):
        raise _RenderResponseInvalid("deploy_list_row_is_invalid")
    return cast(Mapping[str, object], deploy)


def _observation(
    payload: Mapping[str, object],
    service_id: str,
    provider_request_id: str | None,
) -> InfrastructureDeploymentObservation:
    deploy_id = _provider_deploy_id(payload.get("id"))
    provider_status = _bounded_optional_text(payload.get("status"), 64) or "unknown"
    canonical_status = _STATUS_MAP.get(provider_status, "unknown")
    commit_id = _provider_commit_id(payload.get("commit"))
    return InfrastructureDeploymentObservation(
        provider="render",
        service_id=service_id,
        deploy_id=deploy_id,
        status=canonical_status,
        provider_status=provider_status,
        commit_id=commit_id,
        trigger=_bounded_optional_text(payload.get("trigger"), 64),
        created_at=_optional_datetime(payload.get("createdAt")),
        started_at=_optional_datetime(payload.get("startedAt")),
        updated_at=_optional_datetime(payload.get("updatedAt")),
        finished_at=_optional_datetime(payload.get("finishedAt")),
        is_terminal=provider_status in _TERMINAL_PROVIDER_STATUSES,
        is_successful=provider_status == "live",
        provider_request_id=provider_request_id,
    )


def _service_policy_observation(
    payload: Mapping[str, object],
    service_id: str,
    provider_request_id: str | None,
) -> InfrastructureDeploymentServicePolicyObservation:
    provider_service_id = payload.get("id")
    if provider_service_id != service_id:
        raise _RenderResponseInvalid("provider_service_id_mismatch")
    is_auto_deploy_enabled = payload.get("autoDeploy")
    if not isinstance(is_auto_deploy_enabled, bool):
        raise _RenderResponseInvalid("provider_auto_deploy_policy_is_invalid")
    source_owner, source_name = _github_repository_coordinates(payload.get("repo"))
    source_branch = _provider_source_branch(payload.get("branch"))
    service_type = _provider_service_type(payload.get("type"))
    is_suspended = _provider_suspension(payload.get("suspended"))
    return InfrastructureDeploymentServicePolicyObservation(
        provider="render",
        service_id=service_id,
        is_auto_deploy_enabled=is_auto_deploy_enabled,
        source_repository_owner=source_owner,
        source_repository_name=source_name,
        source_branch=source_branch,
        service_type=service_type,
        is_suspended=is_suspended,
        provider_request_id=provider_request_id,
    )


def _github_repository_coordinates(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 512:
        raise _RenderResponseInvalid("provider_source_repository_is_invalid")
    parsed = urlsplit(value.rstrip("/"))
    parts = parsed.path.removesuffix(".git").strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "github.com"
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or not all(_REPOSITORY_COORDINATE_PATTERN.fullmatch(part) for part in parts)
    ):
        raise _RenderResponseInvalid("provider_source_repository_is_invalid")
    return parts[0], parts[1]


def _provider_source_branch(value: object) -> str:
    if not isinstance(value, str) or not _TRACE_VALUE_PATTERN.fullmatch(value):
        raise _RenderResponseInvalid("provider_source_branch_is_invalid")
    return value


def _provider_service_type(value: object) -> str:
    if value != "web_service":
        raise _RenderResponseInvalid("provider_service_type_is_not_web_service")
    return "web_service"


def _provider_suspension(value: object) -> bool:
    if value not in {"suspended", "not_suspended"}:
        raise _RenderResponseInvalid("provider_service_suspension_is_invalid")
    return value == "suspended"


def _provider_deploy_id(value: object) -> str:
    if not isinstance(value, str) or not _DEPLOY_ID_PATTERN.fullmatch(value):
        raise _RenderResponseInvalid("provider_deploy_id_is_invalid")
    return value


def _provider_commit_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _RenderResponseInvalid("provider_commit_is_invalid")
    raw_id = value.get("id")
    if raw_id is None:
        return None
    if not isinstance(raw_id, str) or not _COMMIT_ID_PATTERN.fullmatch(raw_id.lower()):
        raise _RenderResponseInvalid("provider_commit_id_is_invalid")
    return raw_id.lower()


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise _RenderResponseInvalid("provider_timestamp_is_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _RenderResponseInvalid("provider_timestamp_is_invalid") from exc
    if parsed.tzinfo is None:
        raise _RenderResponseInvalid("provider_timestamp_is_not_timezone_aware")
    return parsed


def _bounded_optional_text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _RenderResponseInvalid("provider_text_field_is_invalid")
    return value


def _provider_request_id(headers: Mapping[str, str]) -> str | None:
    for key in ("x-render-request-id", "x-request-id", "request-id"):
        value = headers.get(key)
        if value and len(value) <= 256:
            return value
    return None


def _accepted_mutation_result(
    operation: Literal["start", "rollback"],
    status_code: int,
    observation: InfrastructureDeploymentObservation,
    rollback_target_deploy_id: str | None,
) -> InfrastructureDeploymentMutationResult:
    return InfrastructureDeploymentMutationResult(
        operation=operation,
        outcome="accepted",
        provider_http_status=status_code,
        observation=observation,
        rollback_target_deploy_id=rollback_target_deploy_id,
        is_safe_to_retry=False,
        reason="provider_deploy_receipt_created",
    )


def _unknown_mutation_result(
    operation: Literal["start", "rollback"],
    status_code: int,
    rollback_target_deploy_id: str | None,
    reason: str,
) -> InfrastructureDeploymentMutationResult:
    return InfrastructureDeploymentMutationResult(
        operation=operation,
        outcome="outcome_unknown",
        provider_http_status=status_code,
        observation=None,
        rollback_target_deploy_id=rollback_target_deploy_id,
        is_safe_to_retry=False,
        reason=reason,
    )


def _validation_error(operation: str, reason: str, idempotency_key: str | None = None) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind="validation",
            is_retryable=False,
            operator_message=f"Render deployment {operation} input is invalid.",
            idempotency_key=idempotency_key,
            details={"reason": reason, "knownNotCommitted": True, "safeToRetry": False},
        )
    )


def _secret_error(operation: str, idempotency_key: str | None) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind="authentication",
            is_retryable=False,
            operator_message="Render API credential could not be resolved.",
            idempotency_key=idempotency_key,
            details={"reason": "secret_resolution_failed", "knownNotCommitted": True, "safeToRetry": False},
        )
    )


def _http_error(
    operation: InfrastructureDeploymentOperation,
    response: RenderHttpResponse,
    idempotency_key: str | None,
    *,
    is_mutation: bool,
) -> AdapterError:
    if is_mutation and response.status_code >= 500:
        return _outcome_unknown(operation, "provider_server_error", idempotency_key, response)
    kind = _http_failure_kind(response.status_code)
    is_retryable = response.status_code == 429 or (not is_mutation and response.status_code >= 500)
    details: dict[str, object] = {
        "reason": "render_http_error",
        "statusCode": response.status_code,
        "providerRequestId": _provider_request_id(response.headers),
        "knownNotCommitted": response.status_code in _KNOWN_REJECTION_CODES,
        "safeToRetry": is_retryable,
    }
    retry_after = _retry_after_seconds(response.headers)
    if retry_after is not None:
        details["retryAfterSeconds"] = retry_after
    return AdapterError(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind=kind,
            is_retryable=is_retryable,
            operator_message=f"Render API rejected deployment {operation} with HTTP {response.status_code}.",
            idempotency_key=idempotency_key,
            details=details,
        )
    )


def _http_failure_kind(status_code: int) -> AdapterFailureKind:
    if status_code in {400, 406}:
        return "validation"
    if status_code == 401:
        return "authentication"
    if status_code in {402, 403}:
        return "authorization"
    if status_code in {404, 410}:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limited"
    if status_code == 504:
        return "timeout"
    if status_code >= 500:
        return "unavailable"
    return "unknown"


def _response_error(
    operation: InfrastructureDeploymentOperation,
    reason: str,
    response: RenderHttpResponse,
) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind="validation",
            is_retryable=False,
            operator_message=f"Render deployment {operation} response was invalid.",
            details={
                "reason": reason,
                "statusCode": response.status_code,
                "providerRequestId": _provider_request_id(response.headers),
            },
        )
    )


def _outcome_unknown(
    operation: InfrastructureDeploymentOperation,
    reason: str,
    idempotency_key: str | None,
    response: RenderHttpResponse,
) -> InfrastructureDeploymentOutcomeUnknown:
    return InfrastructureDeploymentOutcomeUnknown(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind="unknown",
            is_retryable=False,
            operator_message="Render mutation outcome is unknown; reconcile provider deploys before any retry.",
            idempotency_key=idempotency_key,
            details={
                "reason": reason,
                "statusCode": response.status_code,
                "providerRequestId": _provider_request_id(response.headers),
                "knownNotCommitted": False,
                "safeToRetry": False,
            },
        )
    )


def _transport_outcome_unknown(
    operation: InfrastructureDeploymentOperation,
    idempotency_key: str | None,
    exc: RenderTransportError,
    timeout_seconds: float,
) -> InfrastructureDeploymentOutcomeUnknown:
    kind: AdapterFailureKind
    if exc.kind == "timeout":
        kind = "timeout"
    elif exc.kind == "unavailable":
        kind = "unavailable"
    else:
        kind = "unknown"
    return InfrastructureDeploymentOutcomeUnknown(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind=kind,
            is_retryable=False,
            operator_message="Render mutation transport outcome is unknown; reconcile before retrying.",
            timeout_seconds=int(timeout_seconds) if kind == "timeout" else None,
            idempotency_key=idempotency_key,
            details={"reason": exc.kind, "knownNotCommitted": False, "safeToRetry": False},
        )
    )


def _transport_read_error(
    operation: InfrastructureDeploymentOperation,
    exc: RenderTransportError,
    timeout_seconds: float,
) -> AdapterError:
    kind: AdapterFailureKind
    is_retryable: bool
    if exc.kind == "timeout":
        kind, is_retryable = "timeout", True
    elif exc.kind == "unavailable":
        kind, is_retryable = "unavailable", True
    else:
        kind, is_retryable = "validation", False
    return AdapterError(
        AdapterFailure(
            adapter_profile="render-infrastructure-deployment",
            operation=operation,
            kind=kind,
            is_retryable=is_retryable,
            operator_message=f"Render deployment {operation} transport failed.",
            timeout_seconds=int(timeout_seconds) if kind == "timeout" else None,
            details={"reason": exc.kind},
        )
    )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


__all__ = [
    "RenderHttpRequest",
    "RenderHttpResponse",
    "RenderHttpTransport",
    "RenderInfrastructureDeploymentAdapter",
    "RenderTransportError",
    "UrllibRenderHttpTransport",
]
