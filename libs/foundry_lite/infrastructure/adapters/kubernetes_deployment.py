"""Kubernetes CRD adapter for exact-revision governed application deployments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import quote, urlencode

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
    InfrastructureDeploymentSourceBinding,
    InfrastructureDeploymentStartRequest,
    InfrastructureDeploymentStatus,
)

_API_GROUP = "release.foundry-lite.io"
_API_VERSION = "v1alpha1"
_RESOURCE_PLURAL = "foundrydeployments"
_PROFILE = "kubernetes-infrastructure-deployment"
_COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_TIMEOUT_SECONDS = 30.0
_DEFAULT_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TERMINAL_PHASES = frozenset({"Live", "Failed", "Canceled"})
_STATUS_MAP: dict[str, InfrastructureDeploymentStatus] = {
    "Pending": "queued",
    "ResolvingArtifact": "building",
    "VerifyingArtifact": "preparing",
    "Applying": "deploying",
    "Progressing": "deploying",
    "Live": "live",
    "Failed": "failed",
    "Canceled": "canceled",
}

KubernetesHttpMethod = Literal["GET", "POST", "PATCH"]
KubernetesTransportFailureKind = Literal["timeout", "unavailable", "response_too_large"]


@dataclass(frozen=True, slots=True)
class KubernetesDeploymentConfig:
    """Immutable in-cluster target binding for one governed workload."""

    namespace: str
    service_id: str
    deployment_name: str
    container_name: str
    image_repository: str
    source_provider: str
    source_owner: str
    source_repository: str
    source_ref: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class KubernetesHttpRequest:
    method: KubernetesHttpMethod
    path: str
    body: bytes | None = field(default=None, repr=False)
    content_type: str = "application/json"
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = _MAX_RESPONSE_BYTES


@dataclass(frozen=True, slots=True)
class KubernetesHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(default=b"", repr=False)


class KubernetesTransportError(RuntimeError):
    def __init__(self, kind: KubernetesTransportFailureKind) -> None:
        super().__init__(kind)
        self.kind = kind


class KubernetesHttpTransport(Protocol):
    def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse: ...


class InClusterKubernetesHttpTransport:
    """Bounded Kubernetes API transport using only the mounted service account."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        token_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ca_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
    ) -> None:
        resolved_host = host if host is not None else os.environ.get("KUBERNETES_SERVICE_HOST", "")
        self._host = _api_host(resolved_host)
        self._port = (
            _api_port(str(port)) if port is not None else _api_port(os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443"))
        )
        self._token_path = token_path
        self._context = ssl.create_default_context(cafile=str(ca_path))

    def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
        if not request.path.startswith("/apis/"):
            raise KubernetesTransportError("unavailable")
        connection = HTTPSConnection(self._host, self._port, context=self._context, timeout=request.timeout_seconds)
        try:
            token = self._token_path.read_text(encoding="utf-8").strip()
            if not token:
                raise KubernetesTransportError("unavailable")
            connection.request(
                request.method,
                request.path,
                body=request.body,
                headers={
                    "accept": "application/json",
                    "authorization": f"Bearer {token}",
                    "content-type": request.content_type,
                    "user-agent": "Foundry-lite/kubernetes-infrastructure-deployment",
                },
            )
            response = connection.getresponse()
            body = response.read(request.max_response_bytes + 1)
            if len(body) > request.max_response_bytes:
                raise KubernetesTransportError("response_too_large")
            return KubernetesHttpResponse(response.status, _headers(response.headers.items()), body)
        except TimeoutError as exc:
            raise KubernetesTransportError("timeout") from exc
        except KubernetesTransportError:
            raise
        except (HTTPException, OSError, UnicodeError) as exc:
            raise KubernetesTransportError("unavailable") from exc
        finally:
            connection.close()


class KubernetesInfrastructureDeploymentAdapter:
    """Create and observe deterministic ``FoundryDeployment`` resources."""

    profile_name = _PROFILE
    provider_name = "kubernetes"
    is_live_provider = True

    def __init__(
        self,
        config: KubernetesDeploymentConfig,
        *,
        transport: KubernetesHttpTransport | None = None,
    ) -> None:
        _validate_config(config)
        self._config = config
        self._transport = transport

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=_failure_modes())

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        self._validate_read_request(request.service_id, request.tenant_id, request.request_id, request.correlation_id)
        response = self._send("get_service_policy", "GET", _deployment_path(self._config), None, None, False)
        _require_read_status("get_service_policy", response, 200)
        payload = _json_object(response.body, "get_service_policy")
        return _service_policy(self._config, payload, _audit_id(response.headers))

    def start(self, request: InfrastructureDeploymentStartRequest) -> InfrastructureDeploymentMutationResult:
        self._validate_mutation_request(request)
        commit_id = _commit_id(request.commit_id, "start", request.idempotency_key)
        name = _release_name("deploy", request.service_id, commit_id, request.idempotency_key)
        payload = _release_payload(self._config, request, name, commit_id, "deploy", None)
        return self._create_release("start", name, payload, commit_id, request.idempotency_key, None)

    def get(self, request: InfrastructureDeploymentGetRequest) -> InfrastructureDeploymentObservation:
        self._validate_read_request(request.service_id, request.tenant_id, request.request_id, request.correlation_id)
        deploy_id = _dns_label(request.deploy_id, "deploy_id", "get")
        response = self._send("get", "GET", _release_path(self._config.namespace, deploy_id), None, None, False)
        _require_read_status("get", response, 200)
        return _observation(self._config, _json_object(response.body, "get"), _audit_id(response.headers), "get")

    def list_candidates(
        self,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[InfrastructureDeploymentObservation, ...]:
        self._validate_read_request(query.service_id, query.tenant_id, query.request_id, query.correlation_id)
        _validate_candidate_query(query)
        service_hash = _short_hash(query.service_id, 16)
        params = urlencode({"labelSelector": f"foundry-lite.io/service-hash={service_hash}", "limit": query.limit})
        response = self._send(
            "list_candidates",
            "GET",
            f"{_release_collection_path(self._config.namespace)}?{params}",
            None,
            None,
            False,
        )
        _require_read_status("list_candidates", response, 200)
        items = _items(_json_object(response.body, "list_candidates"))
        observations = tuple(
            _observation(self._config, item, _audit_id(response.headers), "list_candidates") for item in items
        )
        return tuple(item for item in observations if _candidate_matches(item, query))

    def rollback(
        self,
        request: InfrastructureDeploymentRollbackRequest,
    ) -> InfrastructureDeploymentMutationResult:
        self._validate_mutation_request(request)
        target_id = _dns_label(request.target_deploy_id, "target_deploy_id", "rollback", request.idempotency_key)
        commit_id = _commit_id(request.target_commit_id, "rollback", request.idempotency_key)
        name = _release_name("rollback", request.service_id, commit_id, request.idempotency_key)
        payload = _release_payload(self._config, request, name, commit_id, "rollback", target_id)
        return self._create_release("rollback", name, payload, commit_id, request.idempotency_key, target_id)

    def _create_release(
        self,
        operation: Literal["start", "rollback"],
        name: str,
        payload: Mapping[str, object],
        commit_id: str,
        idempotency_key: str,
        rollback_target: str | None,
    ) -> InfrastructureDeploymentMutationResult:
        response = self._send(
            operation,
            "POST",
            _release_collection_path(self._config.namespace),
            payload,
            idempotency_key,
            True,
        )
        if response.status_code == 409:
            response = self._send(
                operation,
                "GET",
                _release_path(self._config.namespace, name),
                None,
                idempotency_key,
                False,
            )
        if response.status_code not in {200, 201}:
            raise _mutation_http_error(operation, response, idempotency_key)
        observation = _observation(
            self._config,
            _json_object(response.body, operation),
            _audit_id(response.headers),
            operation,
        )
        if observation.deploy_id != name or observation.commit_id != commit_id:
            raise _outcome_unknown(operation, "deterministic_release_mismatch", idempotency_key, response)
        return InfrastructureDeploymentMutationResult(
            operation=operation,
            outcome="accepted",
            provider_http_status=response.status_code,
            observation=observation,
            rollback_target_deploy_id=rollback_target,
            is_safe_to_retry=False,
            reason="kubernetes_release_resource_observed",
        )

    def _send(
        self,
        operation: InfrastructureDeploymentOperation,
        method: KubernetesHttpMethod,
        path: str,
        payload: Mapping[str, object] | None,
        idempotency_key: str | None,
        is_mutation: bool,
    ) -> KubernetesHttpResponse:
        request = KubernetesHttpRequest(
            method=method,
            path=path,
            body=_body(payload),
            timeout_seconds=self._config.timeout_seconds,
        )
        try:
            transport = self._transport or InClusterKubernetesHttpTransport()
            return transport.send(request)
        except KubernetesTransportError as exc:
            if is_mutation:
                raise _transport_outcome_unknown(operation, idempotency_key, exc, self._config.timeout_seconds) from exc
            raise _transport_read_error(operation, exc, self._config.timeout_seconds) from exc
        except Exception:  # noqa: BLE001 - injected transports may include credentials in exception text.
            failure = KubernetesTransportError("unavailable")
            if is_mutation:
                raise _transport_outcome_unknown(
                    operation, idempotency_key, failure, self._config.timeout_seconds
                ) from None
            raise _transport_read_error(operation, failure, self._config.timeout_seconds) from None

    def _validate_read_request(self, service_id: str, tenant_id: str, request_id: str, correlation_id: str) -> None:
        if service_id != self._config.service_id:
            raise _validation_error("get", "service_id_mismatch")
        for value, label in ((tenant_id, "tenant_id"), (request_id, "request_id"), (correlation_id, "correlation_id")):
            _trace(value, label, "get")

    def _validate_mutation_request(
        self,
        request: InfrastructureDeploymentStartRequest | InfrastructureDeploymentRollbackRequest,
    ) -> None:
        self._validate_read_request(request.service_id, request.tenant_id, request.request_id, request.correlation_id)
        _trace(request.idempotency_key, "idempotency_key", "start", request.idempotency_key)


def _validate_config(config: KubernetesDeploymentConfig) -> None:
    for value, label in (
        (config.namespace, "namespace"),
        (config.deployment_name, "deployment_name"),
        (config.container_name, "container_name"),
    ):
        _dns_label(value, label, "configure")
    _trace(config.service_id, "service_id", "configure")
    for value, label in (
        (config.image_repository, "image_repository"),
        (config.source_provider, "source_provider"),
        (config.source_owner, "source_owner"),
        (config.source_repository, "source_repository"),
        (config.source_ref, "source_ref"),
    ):
        if not _REPOSITORY_PATTERN.fullmatch(value):
            raise ValueError(f"Kubernetes deployment {label} is invalid")
    if config.timeout_seconds <= 0 or config.timeout_seconds > _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"Kubernetes deployment timeout must be within (0, {_MAX_TIMEOUT_SECONDS}]")


def _release_payload(
    config: KubernetesDeploymentConfig,
    request: InfrastructureDeploymentStartRequest | InfrastructureDeploymentRollbackRequest,
    name: str,
    commit_id: str,
    operation: Literal["deploy", "rollback"],
    rollback_target: str | None,
) -> Mapping[str, object]:
    spec: dict[str, object] = {
        "serviceId": config.service_id,
        "commitId": commit_id,
        "imageRepository": config.image_repository,
        "operation": operation,
        "workloadRef": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": config.deployment_name,
            "containerName": config.container_name,
        },
        "idempotencyKeyHash": hashlib.sha256(request.idempotency_key.encode()).hexdigest(),
    }
    if rollback_target is not None:
        spec["rollbackTargetDeployId"] = rollback_target
    return {
        "apiVersion": f"{_API_GROUP}/{_API_VERSION}",
        "kind": "FoundryDeployment",
        "metadata": {
            "name": name,
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "foundry-lite",
                "foundry-lite.io/service-hash": _short_hash(config.service_id, 16),
                "foundry-lite.io/commit": commit_id[:16],
            },
            "annotations": {
                "foundry-lite.io/request-id": request.request_id,
                "foundry-lite.io/correlation-id": request.correlation_id,
            },
        },
        "spec": spec,
    }


def _observation(
    config: KubernetesDeploymentConfig,
    payload: Mapping[str, object],
    provider_request_id: str | None,
    operation: str,
) -> InfrastructureDeploymentObservation:
    metadata = _mapping(payload.get("metadata"), f"{operation}_metadata_invalid")
    spec = _mapping(payload.get("spec"), f"{operation}_spec_invalid")
    status = _optional_mapping(payload.get("status"))
    name = _required_text(metadata.get("name"), 253, f"{operation}_name_invalid")
    commit_id = _commit_id(_required_text(spec.get("commitId"), 64, f"{operation}_commit_invalid"), operation)
    service_id = _required_text(spec.get("serviceId"), 256, f"{operation}_service_invalid")
    if service_id != config.service_id:
        raise _response_error(operation, "service_id_mismatch", None)
    phase = _required_text(status.get("phase", "Pending"), 64, f"{operation}_phase_invalid")
    image_digest = status.get("imageDigest")
    if image_digest is not None and (not isinstance(image_digest, str) or not _DIGEST_PATTERN.fullmatch(image_digest)):
        raise _response_error(operation, "image_digest_invalid", None)
    created_at = _timestamp(metadata.get("creationTimestamp"), is_optional=False, reason="creation_timestamp_invalid")
    return InfrastructureDeploymentObservation(
        provider="kubernetes",
        service_id=config.service_id,
        deploy_id=name,
        status=_STATUS_MAP.get(phase, "unknown"),
        provider_status=phase,
        commit_id=commit_id,
        trigger=_required_text(spec.get("operation", "deploy"), 32, f"{operation}_trigger_invalid"),
        created_at=created_at,
        started_at=_timestamp(status.get("startedAt"), is_optional=True, reason="started_at_invalid"),
        updated_at=_timestamp(status.get("updatedAt"), is_optional=True, reason="updated_at_invalid"),
        finished_at=_timestamp(status.get("finishedAt"), is_optional=True, reason="finished_at_invalid"),
        is_terminal=phase in _TERMINAL_PHASES,
        is_successful=phase == "Live",
        provider_request_id=provider_request_id,
    )


def _service_policy(
    config: KubernetesDeploymentConfig,
    payload: Mapping[str, object],
    provider_request_id: str | None,
) -> InfrastructureDeploymentServicePolicyObservation:
    metadata = _mapping(payload.get("metadata"), "deployment_metadata_invalid")
    spec = _mapping(payload.get("spec"), "deployment_spec_invalid")
    annotations = _mapping(metadata.get("annotations"), "deployment_annotations_invalid")
    expected = {
        "foundry-lite.io/service-id": config.service_id,
        "foundry-lite.io/image-repository": config.image_repository,
        "foundry-lite.io/release-trigger": "manual",
    }
    if any(annotations.get(key) != value for key, value in expected.items()):
        raise _response_error("get_service_policy", "deployment_policy_annotation_mismatch", None)
    name = _required_text(metadata.get("name"), 253, "deployment_name_invalid")
    if name != config.deployment_name:
        raise _response_error("get_service_policy", "deployment_name_mismatch", None)
    return InfrastructureDeploymentServicePolicyObservation(
        provider="kubernetes",
        service_id=config.service_id,
        release_mode="immutable_artifact",
        trigger_mode="manual",
        source_binding=InfrastructureDeploymentSourceBinding(
            provider=config.source_provider,
            repository_owner=config.source_owner,
            repository_name=config.source_repository,
            ref=config.source_ref,
        ),
        workload_kind="deployment",
        is_suspended=spec.get("paused") is True or spec.get("replicas") == 0,
        provider_request_id=provider_request_id,
    )


def _candidate_matches(
    item: InfrastructureDeploymentObservation, query: InfrastructureDeploymentCandidateQuery
) -> bool:
    created_at = item.created_at
    return (
        item.commit_id == query.commit_id.lower()
        and created_at is not None
        and query.created_after <= created_at <= query.created_before
    )


def _validate_candidate_query(query: InfrastructureDeploymentCandidateQuery) -> None:
    if query.created_after.tzinfo is None or query.created_before.tzinfo is None:
        raise _validation_error("list_candidates", "candidate_window_must_be_timezone_aware")
    if query.created_after >= query.created_before:
        raise _validation_error("list_candidates", "candidate_window_is_not_increasing")
    if query.limit < 1 or query.limit > 100:
        raise _validation_error("list_candidates", "candidate_limit_out_of_range")


def _deployment_path(config: KubernetesDeploymentConfig) -> str:
    namespace = quote(config.namespace, safe="")
    name = quote(config.deployment_name, safe="")
    return f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}"


def _release_collection_path(namespace: str) -> str:
    return f"/apis/{_API_GROUP}/{_API_VERSION}/namespaces/{quote(namespace, safe='')}/{_RESOURCE_PLURAL}"


def _release_path(namespace: str, name: str) -> str:
    return f"{_release_collection_path(namespace)}/{quote(name, safe='')}"


def _release_name(operation: str, service_id: str, commit_id: str, idempotency_key: str) -> str:
    return f"fd-{operation}-{_short_hash(f'{service_id}:{commit_id}:{idempotency_key}', 24)}"


def _short_hash(value: str, length: int) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def _body(payload: Mapping[str, object] | None) -> bytes | None:
    if payload is None:
        return None
    return json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode()


def _json_object(body: bytes, operation: str) -> Mapping[str, object]:
    try:
        parsed = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _response_error(operation, "response_not_json", None) from exc
    if not isinstance(parsed, Mapping):
        raise _response_error(operation, "response_not_object", None)
    return cast(Mapping[str, object], parsed)


def _items(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise _response_error("list_candidates", "response_items_invalid", None)
    return tuple(cast(Mapping[str, object], item) for item in items)


def _mapping(value: object, reason: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _response_error("get", reason, None)
    return cast(Mapping[str, object], value)


def _optional_mapping(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    return _mapping(value, "status_invalid")


def _required_text(value: object, max_length: int, reason: str) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _response_error("get", reason, None)
    return value


def _timestamp(value: object, *, is_optional: bool, reason: str) -> datetime | None:
    if value is None and is_optional:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise _response_error("get", reason, None)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _response_error("get", reason, None) from exc
    if parsed.tzinfo is None:
        raise _response_error("get", reason, None)
    return parsed.astimezone(UTC)


def _trace(value: str, label: str, operation: str, idempotency_key: str | None = None) -> str:
    if not _TRACE_PATTERN.fullmatch(value):
        raise _validation_error(operation, f"invalid_{label}", idempotency_key)
    return value


def _dns_label(
    value: str,
    label: str,
    operation: str,
    idempotency_key: str | None = None,
) -> str:
    if not _DNS_LABEL_PATTERN.fullmatch(value):
        raise _validation_error(operation, f"invalid_{label}", idempotency_key)
    return value


def _commit_id(value: str, operation: str, idempotency_key: str | None = None) -> str:
    normalized = value.lower()
    if not _COMMIT_PATTERN.fullmatch(normalized):
        raise _validation_error(operation, "exact_commit_sha_required", idempotency_key)
    return normalized


def _api_host(value: str) -> str:
    if not value or re.fullmatch(r"[A-Za-z0-9.-]{1,253}", value) is None:
        raise ValueError("Kubernetes service host is invalid")
    return value


def _api_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("Kubernetes service port is invalid") from exc
    if port < 1 or port > 65535:
        raise ValueError("Kubernetes service port is invalid")
    return port


def _headers(items: Iterable[tuple[object, object]]) -> Mapping[str, str]:
    return {str(key).lower(): str(value) for key, value in items}


def _audit_id(headers: Mapping[str, str]) -> str | None:
    value = headers.get("audit-id") or headers.get("x-request-id")
    return value if value and len(value) <= 256 else None


def _require_read_status(
    operation: InfrastructureDeploymentOperation,
    response: KubernetesHttpResponse,
    expected: int,
) -> None:
    if response.status_code != expected:
        raise _http_error(operation, response, None, False)


def _mutation_http_error(
    operation: InfrastructureDeploymentOperation,
    response: KubernetesHttpResponse,
    idempotency_key: str,
) -> AdapterError | InfrastructureDeploymentOutcomeUnknown:
    if response.status_code in {400, 401, 403, 404, 422, 429}:
        return _http_error(operation, response, idempotency_key, True)
    return _outcome_unknown(operation, "unexpected_mutation_response", idempotency_key, response)


def _http_error(
    operation: InfrastructureDeploymentOperation,
    response: KubernetesHttpResponse,
    idempotency_key: str | None,
    is_mutation: bool,
) -> AdapterError:
    kind = cast(
        AdapterFailureKind,
        {
            400: "validation",
            401: "authentication",
            403: "authorization",
            404: "not_found",
            409: "conflict",
            422: "validation",
            429: "rate_limited",
        }.get(response.status_code, "unavailable" if response.status_code >= 500 else "unknown"),
    )
    retryable = kind in {"rate_limited", "unavailable"} and not is_mutation
    return AdapterError(
        AdapterFailure(
            adapter_profile=_PROFILE,
            operation=operation,
            kind=kind,
            is_retryable=retryable,
            operator_message=f"Kubernetes deployment {operation} request failed.",
            idempotency_key=idempotency_key,
            details={
                "statusCode": response.status_code,
                "providerRequestId": _audit_id(response.headers),
                "knownNotCommitted": is_mutation and response.status_code < 500,
                "safeToRetry": is_mutation and response.status_code < 500,
            },
        )
    )


def _validation_error(
    operation: str,
    reason: str,
    idempotency_key: str | None = None,
) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile=_PROFILE,
            operation=operation,
            kind="validation",
            is_retryable=False,
            operator_message=f"Kubernetes deployment {operation} input was invalid.",
            idempotency_key=idempotency_key,
            details={"reason": reason, "knownNotCommitted": True, "safeToRetry": True},
        )
    )


def _response_error(operation: str, reason: str, response: KubernetesHttpResponse | None) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile=_PROFILE,
            operation=operation,
            kind="validation",
            is_retryable=False,
            operator_message=f"Kubernetes deployment {operation} response was invalid.",
            details={
                "reason": reason,
                "statusCode": response.status_code if response else None,
                "providerRequestId": _audit_id(response.headers) if response else None,
            },
        )
    )


def _outcome_unknown(
    operation: InfrastructureDeploymentOperation,
    reason: str,
    idempotency_key: str | None,
    response: KubernetesHttpResponse,
) -> InfrastructureDeploymentOutcomeUnknown:
    return InfrastructureDeploymentOutcomeUnknown(
        AdapterFailure(
            adapter_profile=_PROFILE,
            operation=operation,
            kind="unknown",
            is_retryable=False,
            operator_message=(
                "Kubernetes mutation outcome is unknown; reconcile the deterministic release before retrying."
            ),
            idempotency_key=idempotency_key,
            details={
                "reason": reason,
                "statusCode": response.status_code,
                "providerRequestId": _audit_id(response.headers),
                "knownNotCommitted": False,
                "safeToRetry": False,
            },
        )
    )


def _transport_outcome_unknown(
    operation: InfrastructureDeploymentOperation,
    idempotency_key: str | None,
    exc: KubernetesTransportError,
    timeout_seconds: float,
) -> InfrastructureDeploymentOutcomeUnknown:
    kind: AdapterFailureKind = "timeout" if exc.kind == "timeout" else "unavailable"
    return InfrastructureDeploymentOutcomeUnknown(
        AdapterFailure(
            adapter_profile=_PROFILE,
            operation=operation,
            kind=kind,
            is_retryable=False,
            operator_message="Kubernetes mutation transport outcome is unknown; reconcile before retrying.",
            timeout_seconds=int(timeout_seconds) if kind == "timeout" else None,
            idempotency_key=idempotency_key,
            details={"reason": exc.kind, "knownNotCommitted": False, "safeToRetry": False},
        )
    )


def _transport_read_error(
    operation: InfrastructureDeploymentOperation,
    exc: KubernetesTransportError,
    timeout_seconds: float,
) -> AdapterError:
    kind: AdapterFailureKind = "timeout" if exc.kind == "timeout" else "unavailable"
    return AdapterError(
        AdapterFailure(
            adapter_profile=_PROFILE,
            operation=operation,
            kind=kind,
            is_retryable=True,
            operator_message=f"Kubernetes deployment {operation} transport failed.",
            timeout_seconds=int(timeout_seconds) if kind == "timeout" else None,
            details={"reason": exc.kind},
        )
    )


def _failure_modes() -> tuple[AdapterFailureMode, ...]:
    modes: list[AdapterFailureMode] = []
    for operation in ("start", "rollback"):
        modes.extend(
            (
                AdapterFailureMode(
                    operation,
                    "validation",
                    False,
                    "Kubernetes deployment input was invalid.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "authorization",
                    False,
                    "Kubernetes deployment was not authorized.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "timeout",
                    False,
                    "Kubernetes mutation requires reconciliation.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    operation,
                    "unavailable",
                    False,
                    "Kubernetes mutation requires reconciliation.",
                    has_required_idempotency_key=True,
                ),
            )
        )
    for operation in ("get_service_policy", "get", "list_candidates"):
        modes.extend(
            (
                AdapterFailureMode(operation, "validation", False, "Kubernetes response was invalid."),
                AdapterFailureMode(operation, "authorization", False, "Kubernetes read was not authorized."),
                AdapterFailureMode(operation, "timeout", True, "Kubernetes read timed out."),
                AdapterFailureMode(operation, "unavailable", True, "Kubernetes API is temporarily unavailable."),
            )
        )
    return tuple(modes)


__all__ = [
    "InClusterKubernetesHttpTransport",
    "KubernetesDeploymentConfig",
    "KubernetesHttpRequest",
    "KubernetesHttpResponse",
    "KubernetesInfrastructureDeploymentAdapter",
    "KubernetesTransportError",
]
