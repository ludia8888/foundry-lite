from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from foundry_lite.infrastructure.adapters.kubernetes_deployment import (
    KubernetesHttpRequest,
    KubernetesHttpResponse,
)
from foundry_lite.infrastructure.kubernetes_release_controller import (
    ArtifactVerificationError,
    BoundedCommandRunner,
    CommandResult,
    CraneCosignArtifactResolver,
    KubernetesReleaseController,
    KubernetesReleaseControllerConfig,
    ReleaseArtifactResolver,
    VerifiedImageArtifact,
)

COMMIT_ID = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE_REPOSITORY = "ghcr.io/ludia8888/foundry-lite-api"
RELEASE_NAME = "deploy-api-1234567890abcdef"


def _config(*, require_signature: bool = True) -> KubernetesReleaseControllerConfig:
    return KubernetesReleaseControllerConfig(
        namespace="foundry-qa",
        signature_issuer="https://token.actions.githubusercontent.com",
        signature_identity_regexp=r"^https://github.com/ludia8888/foundry-lite/.*$",
        require_signature=require_signature,
    )


def _resource(
    *,
    operation: str = "deploy",
    phase: str = "Pending",
    rollback_target: str | None = None,
    image_digest: str | None = None,
) -> dict[str, object]:
    spec: dict[str, object] = {
        "serviceId": "foundry-api",
        "commitId": COMMIT_ID,
        "imageRepository": IMAGE_REPOSITORY,
        "operation": operation,
        "workloadRef": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": "foundry-api",
            "containerName": "api",
        },
        "idempotencyKeyHash": "c" * 64,
    }
    if rollback_target is not None:
        spec["rollbackTargetDeployId"] = rollback_target
    status: dict[str, object] = {"phase": phase, "observedGeneration": 0}
    if image_digest is not None:
        status["imageDigest"] = image_digest
    return {
        "apiVersion": "release.foundry-lite.io/v1alpha1",
        "kind": "FoundryDeployment",
        "metadata": {"name": RELEASE_NAME, "namespace": "foundry-qa", "generation": 1},
        "spec": spec,
        "status": status,
    }


class _Resolver(ReleaseArtifactResolver):
    def __init__(self, *, is_verified: bool = True, error: str | None = None) -> None:
        self.is_verified = is_verified
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def resolve(self, repository: str, commit_id: str) -> VerifiedImageArtifact:
        self.calls.append((repository, commit_id))
        if self.error is not None:
            raise ArtifactVerificationError(self.error)
        return VerifiedImageArtifact(repository, commit_id, DIGEST, f"{repository}@{DIGEST}", True, self.is_verified)


class _ClusterTransport:
    def __init__(
        self,
        resource: dict[str, object],
        *,
        is_rollout_live: bool = True,
        rollback_target: dict[str, object] | None = None,
        rollout_conditions: list[dict[str, str]] | None = None,
    ) -> None:
        self.resource = resource
        self.is_rollout_live = is_rollout_live
        self.rollback_target = rollback_target
        self.rollout_conditions = rollout_conditions or []
        self.requests: list[KubernetesHttpRequest] = []
        self.workload_image = "ghcr.io/ludia8888/foundry-lite-api@sha256:" + "0" * 64

    def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
        self.requests.append(request)
        if request.method == "GET" and request.path.endswith("/foundrydeployments"):
            return _response({"items": [self.resource]})
        if request.method == "GET" and "/foundrydeployments/" in request.path:
            if self.rollback_target is None:
                return KubernetesHttpResponse(404, {}, b"{}")
            return _response(self.rollback_target)
        if request.method == "PATCH" and request.path.endswith("/status"):
            self.resource["status"] = _request_json(request)["status"]
            return _response(self.resource)
        if request.method == "PATCH" and "/deployments/" in request.path:
            payload = _request_json(request)
            template = cast(dict[str, object], cast(dict[str, object], payload["spec"])["template"])
            pod_spec = cast(dict[str, object], template["spec"])
            containers = cast(list[dict[str, str]], pod_spec["containers"])
            self.workload_image = containers[0]["image"]
            return _response({"metadata": {"name": "foundry-api"}})
        if request.method == "GET" and "/deployments/" in request.path:
            available = 2 if self.is_rollout_live else 1
            return _response(
                {
                    "metadata": {"name": "foundry-api", "generation": 7},
                    "spec": {
                        "replicas": 2,
                        "template": {"spec": {"containers": [{"name": "api", "image": self.workload_image}]}},
                    },
                    "status": {
                        "observedGeneration": 7,
                        "updatedReplicas": 2,
                        "availableReplicas": available,
                        "unavailableReplicas": 2 - available,
                        "conditions": self.rollout_conditions,
                    },
                }
            )
        raise AssertionError(f"unexpected request: {request.method} {request.path}")


class _Runner(BoundedCommandRunner):
    def __init__(self, outputs: Sequence[tuple[int, bytes]]) -> None:
        self.outputs = list(outputs)
        self.arguments: list[tuple[str, ...]] = []

    def run(self, arguments: Sequence[str], *, timeout_seconds: float, max_output_bytes: int) -> CommandResult:
        self.arguments.append(tuple(arguments))
        return_code, stdout = self.outputs.pop(0)
        return CommandResult(return_code, stdout)


def _response(payload: object) -> KubernetesHttpResponse:
    return KubernetesHttpResponse(200, {}, json.dumps(payload).encode())


def _request_json(request: KubernetesHttpRequest) -> dict[str, object]:
    assert request.body is not None
    return cast(dict[str, object], json.loads(request.body))


def test_controller_applies_only_verified_digest_and_records_live_status() -> None:
    transport = _ClusterTransport(_resource())
    resolver = _Resolver()

    result = KubernetesReleaseController(_config(), transport=transport, resolver=resolver).reconcile_once()

    assert result[0].status == "reconciled"
    assert result[0].phase == "Live"
    assert result[0].image_digest == DIGEST
    assert transport.workload_image == f"{IMAGE_REPOSITORY}@{DIGEST}"
    status = cast(dict[str, object], transport.resource["status"])
    assert status["reason"] == "exact_digest_live"
    assert status["isSignatureVerified"] is True
    assert status["isLinuxArm64"] is True
    assert all("sha-" not in request.body.decode() for request in transport.requests if request.body)


def test_controller_leaves_progressing_rollout_nonterminal() -> None:
    transport = _ClusterTransport(_resource(), is_rollout_live=False)

    result = KubernetesReleaseController(_config(), transport=transport, resolver=_Resolver()).reconcile_once()[0]

    assert result.status == "waiting"
    assert result.phase == "Progressing"
    assert result.reason == "rollout_progressing"


def test_controller_marks_progress_deadline_failure_terminal() -> None:
    conditions = [{"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}]
    transport = _ClusterTransport(_resource(), is_rollout_live=False, rollout_conditions=conditions)

    result = KubernetesReleaseController(_config(), transport=transport, resolver=_Resolver()).reconcile_once()[0]

    assert result.status == "failed"
    assert result.reason == "progress_deadline_exceeded"
    assert result.image_digest == DIGEST


def test_controller_marks_bounded_rollout_timeout_terminal() -> None:
    resource = _resource(phase="Progressing")
    status = cast(dict[str, object], resource["status"])
    status["startedAt"] = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    transport = _ClusterTransport(resource, is_rollout_live=False)

    result = KubernetesReleaseController(_config(), transport=transport, resolver=_Resolver()).reconcile_once()[0]

    assert result.status == "failed"
    assert result.reason == "rollout_timeout"


def test_controller_fails_closed_before_workload_patch_when_signature_is_missing() -> None:
    transport = _ClusterTransport(_resource())

    result = KubernetesReleaseController(
        _config(), transport=transport, resolver=_Resolver(is_verified=False)
    ).reconcile_once()[0]

    assert result.status == "failed"
    assert result.reason == "image_signature_verification_required"
    assert not any("/deployments/" in request.path for request in transport.requests)


def test_controller_reuses_only_a_verified_live_digest_for_rollback() -> None:
    target = _resource(phase="Live", image_digest=DIGEST)
    cast(dict[str, object], target["metadata"])["name"] = "deploy-api-previous"
    cast(dict[str, object], target["status"])["observedGeneration"] = 1
    resource = _resource(operation="rollback", rollback_target="deploy-api-previous")
    transport = _ClusterTransport(resource, rollback_target=target)
    resolver = _Resolver(error="resolver_must_not_be_called")

    result = KubernetesReleaseController(_config(), transport=transport, resolver=resolver).reconcile_once()[0]

    assert result.status == "reconciled"
    assert result.image_digest == DIGEST
    assert resolver.calls == []
    assert transport.workload_image == f"{IMAGE_REPOSITORY}@{DIGEST}"


def test_controller_does_not_reprocess_observed_terminal_resource() -> None:
    resource = _resource(phase="Live", image_digest=DIGEST)
    cast(dict[str, object], resource["status"])["observedGeneration"] = 1
    transport = _ClusterTransport(resource)

    result = KubernetesReleaseController(_config(), transport=transport, resolver=_Resolver()).reconcile_once()[0]

    assert result.status == "terminal"
    assert len(transport.requests) == 1


def test_crane_cosign_resolver_verifies_arm64_revision_and_immutable_signature() -> None:
    manifest = {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]}
    config = {"config": {"Labels": {"org.opencontainers.image.revision": COMMIT_ID}}}
    runner = _Runner(
        [
            (0, DIGEST.encode()),
            (0, json.dumps(manifest).encode()),
            (0, json.dumps(config).encode()),
            (0, b'[{"critical":{"identity":{"docker-reference":"verified"}}}]'),
        ]
    )

    artifact = CraneCosignArtifactResolver(_config(), runner=runner).resolve(IMAGE_REPOSITORY, COMMIT_ID)

    assert artifact.image_reference == f"{IMAGE_REPOSITORY}@{DIGEST}"
    assert artifact.is_signature_verified
    assert runner.arguments[0] == ("crane", "digest", f"{IMAGE_REPOSITORY}:sha-{COMMIT_ID}")
    assert runner.arguments[-1][-1] == f"{IMAGE_REPOSITORY}@{DIGEST}"


@pytest.mark.parametrize(
    ("manifest", "config", "reason"),
    [
        ({"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]}, {}, "linux_arm64_image_missing"),
        (
            {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]},
            {"config": {"Labels": {"org.opencontainers.image.revision": "d" * 40}}},
            "oci_revision_mismatch",
        ),
    ],
)
def test_crane_cosign_resolver_rejects_wrong_platform_or_revision(
    manifest: object,
    config: object,
    reason: str,
) -> None:
    runner = _Runner(
        [
            (0, DIGEST.encode()),
            (0, json.dumps(manifest).encode()),
            (0, json.dumps(config).encode()),
        ]
    )

    with pytest.raises(ArtifactVerificationError, match=reason):
        CraneCosignArtifactResolver(_config(), runner=runner).resolve(IMAGE_REPOSITORY, COMMIT_ID)


def test_crane_cosign_resolver_never_accepts_mutable_or_malformed_repository() -> None:
    resolver = CraneCosignArtifactResolver(_config(), runner=_Runner([]))

    with pytest.raises(ArtifactVerificationError, match="image_repository_invalid"):
        resolver.resolve(f"{IMAGE_REPOSITORY}:latest", COMMIT_ID)
