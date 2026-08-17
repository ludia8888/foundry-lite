from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentCandidateQuery,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentOutcomeUnknown,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentStartRequest,
)
from foundry_lite.infrastructure.adapters import kubernetes_deployment
from foundry_lite.infrastructure.adapters.kubernetes_deployment import (
    InClusterKubernetesHttpTransport,
    KubernetesDeploymentConfig,
    KubernetesHttpRequest,
    KubernetesHttpResponse,
    KubernetesInfrastructureDeploymentAdapter,
    KubernetesTransportError,
)

COMMIT_ID = "a" * 40
PREVIOUS_COMMIT_ID = "b" * 40
SERVICE_ID = "foundry-qa/foundry-lite-api"
NAMESPACE = "foundry-qa"


@dataclass
class _SequenceTransport:
    outcomes: list[KubernetesHttpResponse | KubernetesTransportError]
    requests: list[KubernetesHttpRequest] = field(default_factory=list)

    def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, KubernetesTransportError):
            raise outcome
        return outcome


def _config() -> KubernetesDeploymentConfig:
    return KubernetesDeploymentConfig(
        namespace=NAMESPACE,
        service_id=SERVICE_ID,
        deployment_name="foundry-lite-api",
        container_name="api",
        image_repository="ghcr.io/ludia8888/foundry-lite-api",
        source_provider="github",
        source_owner="ludia8888",
        source_repository="foundry-lite",
        source_ref="qa-enterprise",
    )


def _adapter(transport: _SequenceTransport) -> KubernetesInfrastructureDeploymentAdapter:
    return KubernetesInfrastructureDeploymentAdapter(_config(), transport=transport)


def _start_request(*, commit_id: str = COMMIT_ID) -> InfrastructureDeploymentStartRequest:
    return InfrastructureDeploymentStartRequest(
        tenant_id="tenant-qa",
        service_id=SERVICE_ID,
        commit_id=commit_id,
        idempotency_key="deploy-qa-1",
        request_id="request-qa-1",
        correlation_id="correlation-qa-1",
    )


def _release_name(operation: str, commit_id: str, idempotency_key: str) -> str:
    value = f"{SERVICE_ID}:{commit_id}:{idempotency_key}"
    return f"fd-{operation}-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _release_payload(
    name: str,
    commit_id: str,
    *,
    phase: str = "Pending",
    operation: str = "deploy",
    created_at: datetime | None = None,
) -> dict[str, object]:
    created = created_at or datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC)
    status: dict[str, object] = {"phase": phase, "updatedAt": created.isoformat()}
    if phase == "Live":
        status.update(
            {
                "imageDigest": f"sha256:{'c' * 64}",
                "startedAt": created.isoformat(),
                "finishedAt": (created + timedelta(minutes=1)).isoformat(),
            }
        )
    return {
        "apiVersion": "release.foundry-lite.io/v1alpha1",
        "kind": "FoundryDeployment",
        "metadata": {"name": name, "creationTimestamp": created.isoformat()},
        "spec": {"serviceId": SERVICE_ID, "commitId": commit_id, "operation": operation},
        "status": status,
    }


def _response(status: int, payload: object, *, audit_id: str = "audit-qa-1") -> KubernetesHttpResponse:
    return KubernetesHttpResponse(
        status_code=status,
        headers={"audit-id": audit_id},
        body=json.dumps(payload).encode(),
    )


def test_kubernetes_adapter_satisfies_the_typed_port() -> None:
    adapter: InfrastructureDeploymentAdapter = _adapter(_SequenceTransport([]))
    assert adapter.profile_name == "kubernetes-infrastructure-deployment"
    assert adapter.provider_name == "kubernetes"
    assert adapter.is_live_provider is True
    operations = {mode.operation for mode in adapter.failure_contract().modes}
    assert operations == {"get_service_policy", "start", "get", "list_candidates", "rollback"}


def test_service_policy_requires_exact_manual_immutable_workload_binding() -> None:
    payload = {
        "metadata": {
            "name": "foundry-lite-api",
            "annotations": {
                "foundry-lite.io/service-id": SERVICE_ID,
                "foundry-lite.io/image-repository": "ghcr.io/ludia8888/foundry-lite-api",
                "foundry-lite.io/release-trigger": "manual",
            },
        },
        "spec": {"replicas": 2},
    }
    transport = _SequenceTransport([_response(200, payload)])
    observation = _adapter(transport).get_service_policy(
        InfrastructureDeploymentServicePolicyRequest(
            tenant_id="tenant-qa",
            service_id=SERVICE_ID,
            request_id="request-qa-1",
            correlation_id="correlation-qa-1",
        )
    )
    assert observation.provider == "kubernetes"
    assert observation.release_mode == "immutable_artifact"
    assert observation.trigger_mode == "manual"
    assert observation.source_binding is not None
    assert observation.source_binding.repository_owner == "ludia8888"
    assert observation.source_binding.ref == "qa-enterprise"
    assert observation.is_suspended is False
    assert transport.requests[0].path.endswith("/namespaces/foundry-qa/deployments/foundry-lite-api")


def test_start_creates_deterministic_cr_without_raw_idempotency_key() -> None:
    name = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    transport = _SequenceTransport([_response(201, _release_payload(name, COMMIT_ID))])
    result = _adapter(transport).start(_start_request())
    assert result.outcome == "accepted"
    assert result.observation is not None
    assert result.observation.deploy_id == name
    assert result.observation.status == "queued"
    request_body = transport.requests[0].body or b""
    assert b"deploy-qa-1" not in request_body
    body = json.loads(request_body)
    assert body["spec"]["idempotencyKeyHash"] == hashlib.sha256(b"deploy-qa-1").hexdigest()
    assert body["spec"]["imageRepository"] == "ghcr.io/ludia8888/foundry-lite-api"


def test_conflict_reconciles_the_same_deterministic_resource() -> None:
    name = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    transport = _SequenceTransport(
        [
            _response(409, {"kind": "Status"}),
            _response(200, _release_payload(name, COMMIT_ID, phase="Live")),
        ]
    )
    result = _adapter(transport).start(_start_request())
    assert result.observation is not None
    assert result.observation.is_successful is True
    assert result.observation.status == "live"
    assert transport.requests[1].method == "GET"
    assert transport.requests[1].path.endswith(name)


def test_mutation_timeout_is_outcome_unknown_and_never_safe_to_retry() -> None:
    adapter = _adapter(_SequenceTransport([KubernetesTransportError("timeout")]))
    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        adapter.start(_start_request())
    assert raised.value.failure.kind == "timeout"
    assert raised.value.failure.is_retryable is False
    assert raised.value.failure.details["knownNotCommitted"] is False
    assert raised.value.failure.details["safeToRetry"] is False


def test_get_maps_live_digest_and_provider_timestamps() -> None:
    name = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    adapter = _adapter(_SequenceTransport([_response(200, _release_payload(name, COMMIT_ID, phase="Live"))]))
    observation = adapter.get(
        InfrastructureDeploymentGetRequest(
            tenant_id="tenant-qa",
            service_id=SERVICE_ID,
            deploy_id=name,
            request_id="request-qa-1",
            correlation_id="correlation-qa-1",
        )
    )
    assert observation.commit_id == COMMIT_ID
    assert observation.is_terminal is True
    assert observation.is_successful is True
    assert observation.finished_at is not None


def test_candidate_scan_filters_commit_and_exact_time_window() -> None:
    created = datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC)
    matching = _release_payload("fd-deploy-match", COMMIT_ID, created_at=created)
    wrong_commit = _release_payload("fd-deploy-other", PREVIOUS_COMMIT_ID, created_at=created)
    outside = _release_payload("fd-deploy-old", COMMIT_ID, created_at=created - timedelta(days=1))
    transport = _SequenceTransport([_response(200, {"items": [matching, wrong_commit, outside]})])
    observations = _adapter(transport).list_candidates(
        InfrastructureDeploymentCandidateQuery(
            tenant_id="tenant-qa",
            service_id=SERVICE_ID,
            commit_id=COMMIT_ID,
            created_after=created - timedelta(minutes=1),
            created_before=created + timedelta(minutes=1),
            request_id="request-qa-1",
            correlation_id="correlation-qa-1",
            limit=20,
        )
    )
    assert [item.deploy_id for item in observations] == ["fd-deploy-match"]
    assert "labelSelector=" in transport.requests[0].path


def test_rollback_creates_a_new_release_pinned_to_verified_target() -> None:
    target = "fd-deploy-prior"
    name = _release_name("rollback", PREVIOUS_COMMIT_ID, "rollback-qa-1")
    transport = _SequenceTransport([_response(201, _release_payload(name, PREVIOUS_COMMIT_ID, operation="rollback"))])
    result = _adapter(transport).rollback(
        InfrastructureDeploymentRollbackRequest(
            tenant_id="tenant-qa",
            service_id=SERVICE_ID,
            target_deploy_id=target,
            target_commit_id=PREVIOUS_COMMIT_ID,
            idempotency_key="rollback-qa-1",
            request_id="request-qa-1",
            correlation_id="correlation-qa-1",
        )
    )
    assert result.rollback_target_deploy_id == target
    body = json.loads(transport.requests[0].body or b"{}")
    assert body["spec"]["rollbackTargetDeployId"] == target
    assert body["spec"]["operation"] == "rollback"


def test_invalid_commit_and_policy_drift_fail_closed() -> None:
    adapter = _adapter(_SequenceTransport([]))
    with pytest.raises(AdapterError, match="invalid") as invalid:
        adapter.start(_start_request(commit_id="main"))
    assert invalid.value.failure.details["knownNotCommitted"] is True

    payload = {
        "metadata": {
            "name": "foundry-lite-api",
            "annotations": {
                "foundry-lite.io/service-id": SERVICE_ID,
                "foundry-lite.io/image-repository": "ghcr.io/ludia8888/foundry-lite-api",
                "foundry-lite.io/release-trigger": "automatic",
            },
        },
        "spec": {"replicas": 2},
    }
    adapter = _adapter(_SequenceTransport([_response(200, payload)]))
    with pytest.raises(AdapterError, match="response was invalid"):
        adapter.get_service_policy(
            InfrastructureDeploymentServicePolicyRequest(
                tenant_id="tenant-qa",
                service_id=SERVICE_ID,
                request_id="request-qa-1",
                correlation_id="correlation-qa-1",
            )
        )


@pytest.mark.parametrize(
    ("status", "expected_exception", "kind"),
    [
        (422, AdapterError, "validation"),
        (429, AdapterError, "rate_limited"),
        (500, InfrastructureDeploymentOutcomeUnknown, "unknown"),
    ],
)
def test_mutation_http_failures_preserve_commit_certainty(
    status: int,
    expected_exception: type[Exception],
    kind: str,
) -> None:
    adapter = _adapter(_SequenceTransport([_response(status, {"kind": "Status"})]))

    with pytest.raises(expected_exception) as raised:
        adapter.start(_start_request())

    failure = raised.value.failure  # type: ignore[attr-defined]
    assert failure.kind == kind
    if status < 500:
        assert failure.details["knownNotCommitted"] is True
        assert failure.details["safeToRetry"] is True
    else:
        assert failure.details["knownNotCommitted"] is False
        assert failure.details["safeToRetry"] is False


def test_mutation_rejects_deterministic_resource_mismatch_as_unknown() -> None:
    expected = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    mismatched = _release_payload("fd-deploy-different", COMMIT_ID)
    adapter = _adapter(_SequenceTransport([_response(201, mismatched)]))

    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        adapter.start(_start_request())

    assert expected != "fd-deploy-different"
    assert raised.value.failure.details["reason"] == "deterministic_release_mismatch"


@pytest.mark.parametrize("transport_failure", [KubernetesTransportError("timeout"), RuntimeError("private")])
def test_read_transport_failure_is_retryable_and_redacted(transport_failure: BaseException) -> None:
    class _Transport:
        def send(self, _request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            raise transport_failure

    adapter = KubernetesInfrastructureDeploymentAdapter(_config(), transport=_Transport())

    with pytest.raises(AdapterError) as raised:
        adapter.get(
            InfrastructureDeploymentGetRequest(
                tenant_id="tenant-qa",
                service_id=SERVICE_ID,
                deploy_id="fd-deploy-example",
                request_id="request-qa-1",
                correlation_id="correlation-qa-1",
            )
        )

    assert raised.value.failure.is_retryable is True
    assert "private" not in str(raised.value)


def test_mutation_unknown_transport_exception_is_ambiguous_and_redacted() -> None:
    class _Transport:
        def send(self, _request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            raise RuntimeError("private-cluster-detail")

    adapter = KubernetesInfrastructureDeploymentAdapter(_config(), transport=_Transport())
    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        adapter.start(_start_request())

    assert raised.value.failure.kind == "unavailable"
    assert raised.value.failure.details["safeToRetry"] is False
    assert "private-cluster-detail" not in str(raised.value)


def test_read_request_requires_exact_service_binding() -> None:
    with pytest.raises(AdapterError) as raised:
        _adapter(_SequenceTransport([])).get(
            InfrastructureDeploymentGetRequest(
                tenant_id="tenant-qa",
                service_id="other-service",
                deploy_id="fd-deploy-example",
                request_id="request-qa-1",
                correlation_id="correlation-qa-1",
            )
        )
    assert raised.value.failure.details["reason"] == "service_id_mismatch"


@pytest.mark.parametrize(
    "query",
    [
        InfrastructureDeploymentCandidateQuery(
            "tenant-qa",
            SERVICE_ID,
            COMMIT_ID,
            datetime(2026, 1, 1),
            datetime(2026, 1, 2),
            "request-qa-1",
            "correlation-qa-1",
            20,
        ),
        InfrastructureDeploymentCandidateQuery(
            "tenant-qa",
            SERVICE_ID,
            COMMIT_ID,
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            "request-qa-1",
            "correlation-qa-1",
            20,
        ),
        InfrastructureDeploymentCandidateQuery(
            "tenant-qa",
            SERVICE_ID,
            COMMIT_ID,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            "request-qa-1",
            "correlation-qa-1",
            101,
        ),
    ],
)
def test_candidate_scan_rejects_unsafe_windows_and_limits(query: InfrastructureDeploymentCandidateQuery) -> None:
    with pytest.raises(AdapterError):
        _adapter(_SequenceTransport([])).list_candidates(query)


def test_candidate_scan_rejects_non_object_items() -> None:
    adapter = _adapter(_SequenceTransport([_response(200, {"items": ["not-an-object"]})]))
    query = InfrastructureDeploymentCandidateQuery(
        tenant_id="tenant-qa",
        service_id=SERVICE_ID,
        commit_id=COMMIT_ID,
        created_after=datetime(2026, 1, 1, tzinfo=UTC),
        created_before=datetime(2026, 1, 2, tzinfo=UTC),
        request_id="request-qa-1",
        correlation_id="correlation-qa-1",
        limit=20,
    )
    with pytest.raises(AdapterError, match="response was invalid"):
        adapter.list_candidates(query)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda payload: payload.update({"metadata": {}}), "name_invalid"),
        (lambda payload: payload["spec"].update({"serviceId": "other"}), "service_id_mismatch"),
        (lambda payload: payload["status"].update({"imageDigest": "sha256:bad"}), "image_digest_invalid"),
        (lambda payload: payload["metadata"].update({"creationTimestamp": "naive"}), "creation_timestamp_invalid"),
    ],
)
def test_get_rejects_malformed_provider_observation(mutation: object, reason: str) -> None:
    name = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    payload = _release_payload(name, COMMIT_ID)
    mutation(payload)  # type: ignore[operator]
    adapter = _adapter(_SequenceTransport([_response(200, payload)]))

    with pytest.raises(AdapterError) as raised:
        adapter.get(
            InfrastructureDeploymentGetRequest(
                tenant_id="tenant-qa",
                service_id=SERVICE_ID,
                deploy_id=name,
                request_id="request-qa-1",
                correlation_id="correlation-qa-1",
            )
        )
    assert raised.value.failure.details["reason"].endswith(reason)


@pytest.mark.parametrize("body", [b"not-json", b"[]"])
def test_get_rejects_non_object_json_response(body: bytes) -> None:
    adapter = _adapter(_SequenceTransport([KubernetesHttpResponse(200, {}, body)]))
    with pytest.raises(AdapterError, match="response was invalid"):
        adapter.get(
            InfrastructureDeploymentGetRequest(
                tenant_id="tenant-qa",
                service_id=SERVICE_ID,
                deploy_id="fd-deploy-example",
                request_id="request-qa-1",
                correlation_id="correlation-qa-1",
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"spec": []}),
        lambda payload: payload.update({"status": []}),
        lambda payload: payload["status"].update({"updatedAt": 123}),
        lambda payload: payload["status"].update({"updatedAt": "2026-08-16T01:02:03"}),
    ],
)
def test_get_rejects_invalid_nested_shapes_and_timestamps(mutation: object) -> None:
    name = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    payload = _release_payload(name, COMMIT_ID)
    mutation(payload)  # type: ignore[operator]
    adapter = _adapter(_SequenceTransport([_response(200, payload)]))
    with pytest.raises(AdapterError, match="response was invalid"):
        adapter.get(
            InfrastructureDeploymentGetRequest(
                tenant_id="tenant-qa",
                service_id=SERVICE_ID,
                deploy_id=name,
                request_id="request-qa-1",
                correlation_id="correlation-qa-1",
            )
        )


def test_get_defaults_missing_status_to_pending() -> None:
    name = _release_name("deploy", COMMIT_ID, "deploy-qa-1")
    payload = _release_payload(name, COMMIT_ID)
    payload.pop("status")
    observation = _adapter(_SequenceTransport([_response(200, payload)])).get(
        InfrastructureDeploymentGetRequest(
            tenant_id="tenant-qa",
            service_id=SERVICE_ID,
            deploy_id=name,
            request_id="request-qa-1",
            correlation_id="correlation-qa-1",
        )
    )
    assert observation.status == "queued"


def test_service_policy_rejects_wrong_workload_and_reports_suspended_workload() -> None:
    annotations = {
        "foundry-lite.io/service-id": SERVICE_ID,
        "foundry-lite.io/image-repository": "ghcr.io/ludia8888/foundry-lite-api",
        "foundry-lite.io/release-trigger": "manual",
    }
    wrong = {"metadata": {"name": "other", "annotations": annotations}, "spec": {"replicas": 2}}
    with pytest.raises(AdapterError, match="response was invalid"):
        _adapter(_SequenceTransport([_response(200, wrong)])).get_service_policy(
            InfrastructureDeploymentServicePolicyRequest("tenant-qa", SERVICE_ID, "request-qa-1", "correlation-qa-1")
        )

    paused = {
        "metadata": {"name": "foundry-lite-api", "annotations": annotations},
        "spec": {"replicas": 0, "paused": True},
    }
    policy = _adapter(_SequenceTransport([_response(200, paused)])).get_service_policy(
        InfrastructureDeploymentServicePolicyRequest("tenant-qa", SERVICE_ID, "request-qa-1", "correlation-qa-1")
    )
    assert policy.is_suspended is True


def test_read_http_error_is_classified_retryable() -> None:
    adapter = _adapter(_SequenceTransport([_response(503, {"kind": "Status"})]))
    with pytest.raises(AdapterError) as raised:
        adapter.get(
            InfrastructureDeploymentGetRequest(
                "tenant-qa", SERVICE_ID, "fd-deploy-example", "request-qa-1", "correlation-qa-1"
            )
        )
    assert raised.value.failure.kind == "unavailable"
    assert raised.value.failure.is_retryable is True


@pytest.mark.parametrize(
    "config",
    [
        replace(_config(), namespace="Invalid_Namespace"),
        replace(_config(), service_id="invalid service"),
        replace(_config(), image_repository="bad repository"),
        replace(_config(), timeout_seconds=0),
        replace(_config(), timeout_seconds=31),
    ],
)
def test_kubernetes_deployment_adapter_rejects_unsafe_config(config: KubernetesDeploymentConfig) -> None:
    with pytest.raises((ValueError, AdapterError)):
        KubernetesInfrastructureDeploymentAdapter(config, transport=_SequenceTransport([]))


def test_incluster_deployment_transport_authenticates_bounds_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("service-account-token", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")

    class _Headers:
        def items(self) -> list[tuple[str, str]]:
            return [("Audit-Id", "audit-1")]

    class _Response:
        status = 200
        headers = _Headers()

        def read(self, size: int) -> bytes:
            assert size == 1025
            return b"{}"

    class _Connection:
        def __init__(self) -> None:
            self.arguments: tuple[object, ...] | None = None
            self.is_closed = False

        def request(self, *args: object, **kwargs: object) -> None:
            self.arguments = (*args, kwargs)

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            self.is_closed = True

    connection = _Connection()
    monkeypatch.setattr(kubernetes_deployment.ssl, "create_default_context", lambda **_kwargs: object())
    monkeypatch.setattr(kubernetes_deployment, "HTTPSConnection", lambda *_args, **_kwargs: connection)
    transport = InClusterKubernetesHttpTransport(host="kubernetes", port=443, token_path=token, ca_path=ca)

    response = transport.send(
        KubernetesHttpRequest(
            "GET",
            "/apis/apps/v1/namespaces/foundry-qa/deployments/foundry-lite-api",
            max_response_bytes=1024,
        )
    )

    assert response == KubernetesHttpResponse(200, {"audit-id": "audit-1"}, b"{}")
    assert connection.arguments is not None
    assert connection.arguments[-1]["headers"]["authorization"] == "Bearer service-account-token"
    assert connection.is_closed


def test_incluster_deployment_transport_rejects_path_token_size_and_network_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")
    monkeypatch.setattr(kubernetes_deployment.ssl, "create_default_context", lambda **_kwargs: object())

    class _Headers:
        def items(self) -> list[tuple[str, str]]:
            return []

    class _Response:
        status = 200
        headers = _Headers()

        def read(self, _size: int) -> bytes:
            return b"xx"

    class _Connection:
        def __init__(self, failure: BaseException | None = None) -> None:
            self.failure = failure

        def request(self, *_args: object, **_kwargs: object) -> None:
            if self.failure is not None:
                raise self.failure

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(kubernetes_deployment, "HTTPSConnection", lambda *_args, **_kwargs: _Connection())
    transport = InClusterKubernetesHttpTransport(host="kubernetes", token_path=token, ca_path=ca)
    with pytest.raises(KubernetesTransportError, match="unavailable"):
        transport.send(KubernetesHttpRequest("GET", "/api/v1/secrets"))
    with pytest.raises(KubernetesTransportError, match="unavailable"):
        transport.send(KubernetesHttpRequest("GET", "/apis/apps/v1/deployments"))

    token.write_text("token", encoding="utf-8")
    with pytest.raises(KubernetesTransportError, match="response_too_large"):
        transport.send(KubernetesHttpRequest("GET", "/apis/apps/v1/deployments", max_response_bytes=1))

    monkeypatch.setattr(
        kubernetes_deployment,
        "HTTPSConnection",
        lambda *_args, **_kwargs: _Connection(OSError("private-network")),
    )
    transport = InClusterKubernetesHttpTransport(host="kubernetes", token_path=token, ca_path=ca)
    with pytest.raises(KubernetesTransportError, match="unavailable"):
        transport.send(KubernetesHttpRequest("GET", "/apis/apps/v1/deployments"))


@pytest.mark.parametrize(("host", "port"), [("", None), ("bad host", None), ("kubernetes", 70000)])
def test_incluster_deployment_transport_rejects_invalid_api_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    host: str,
    port: int | None,
) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(kubernetes_deployment.ssl, "create_default_context", lambda **_kwargs: object())
    if port is not None:
        monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", str(port))
    with pytest.raises(ValueError):
        InClusterKubernetesHttpTransport(host=host, port=port, ca_path=ca)


def test_incluster_deployment_transport_rejects_non_numeric_environment_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "not-a-port")
    monkeypatch.setattr(kubernetes_deployment.ssl, "create_default_context", lambda **_kwargs: object())

    with pytest.raises(ValueError, match="service port is invalid"):
        InClusterKubernetesHttpTransport(host="kubernetes", ca_path=ca)
