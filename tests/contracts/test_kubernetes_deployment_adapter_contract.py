from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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
from foundry_lite.infrastructure.adapters.kubernetes_deployment import (
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
