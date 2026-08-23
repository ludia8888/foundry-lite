from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from foundry_lite.infrastructure import kubernetes_release_controller as release_controller
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
    SubprocessCommandRunner,
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
    assert runner.arguments[2] == (
        "crane",
        "config",
        "--platform",
        "linux/arm64",
        f"{IMAGE_REPOSITORY}@{DIGEST}",
    )
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


def test_subprocess_verifier_runs_allowlisted_command_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[tuple[str, ...]] = []

    def run(arguments: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(arguments)
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(arguments, 0, stdout=b"verified", stderr=b"")

    monkeypatch.setattr(release_controller.subprocess, "run", run)

    assert SubprocessCommandRunner().run(("crane", "digest", "image"), timeout_seconds=1, max_output_bytes=32) == (
        CommandResult(0, b"verified")
    )
    assert observed == [("crane", "digest", "image")]


def test_subprocess_verifier_classifies_allowlist_timeout_tool_and_output_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = SubprocessCommandRunner()
    with pytest.raises(ArtifactVerificationError, match="verification_executable_not_allowed"):
        runner.run(("sh", "-c", "secret"), timeout_seconds=1, max_output_bytes=10)

    failures: list[BaseException | subprocess.CompletedProcess[bytes]] = [
        subprocess.TimeoutExpired(("crane",), 1),
        OSError("private-tool-detail"),
        subprocess.CompletedProcess(("crane",), 0, stdout=b"too-large", stderr=b""),
        subprocess.CompletedProcess(("crane",), 0, stdout=b"", stderr=b"too-large"),
    ]
    reasons = (
        "verification_timeout",
        "verification_tool_unavailable",
        "verification_output_too_large",
        "verification_output_too_large",
    )
    for failure, reason in zip(failures, reasons, strict=True):

        def run(
            *_args: object,
            selected: BaseException | subprocess.CompletedProcess[bytes] = failure,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            if isinstance(selected, BaseException):
                raise selected
            return selected

        monkeypatch.setattr(release_controller.subprocess, "run", run)
        with pytest.raises(ArtifactVerificationError, match=reason):
            runner.run(("crane", "digest", "image"), timeout_seconds=1, max_output_bytes=2)


def test_crane_resolver_can_record_unsigned_artifact_only_when_policy_allows_it() -> None:
    manifest = {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]}
    config = {"config": {"Labels": {"org.opencontainers.image.revision": COMMIT_ID}}}
    runner = _Runner([(0, DIGEST.encode()), (0, json.dumps(manifest).encode()), (0, json.dumps(config).encode())])

    artifact = CraneCosignArtifactResolver(_config(require_signature=False), runner=runner).resolve(
        IMAGE_REPOSITORY, COMMIT_ID
    )

    assert artifact.is_signature_verified is False
    assert all(arguments[0] != "cosign" for arguments in runner.arguments)


@pytest.mark.parametrize(
    ("outputs", "reason"),
    [
        ([(1, b"")], "image_digest_resolution_failed"),
        ([(0, b"not-a-digest")], "image_digest_invalid"),
        ([(0, DIGEST.encode()), (0, b"not-json")], "image_manifest_invalid"),
        (
            [
                (0, DIGEST.encode()),
                (0, b'{"manifests":[{"platform":{"os":"linux","architecture":"arm64"}}]}'),
                (0, json.dumps({"config": {"Labels": {"org.opencontainers.image.revision": COMMIT_ID}}}).encode()),
                (0, b"[]"),
            ],
            "image_signature_evidence_missing",
        ),
    ],
)
def test_crane_resolver_fails_closed_on_verifier_output(
    outputs: list[tuple[int, bytes]],
    reason: str,
) -> None:
    with pytest.raises(ArtifactVerificationError, match=reason):
        CraneCosignArtifactResolver(_config(), runner=_Runner(outputs)).resolve(IMAGE_REPOSITORY, COMMIT_ID)


def test_crane_resolver_rejects_invalid_commit_and_non_utf8_digest() -> None:
    resolver = CraneCosignArtifactResolver(_config(), runner=_Runner([]))
    with pytest.raises(ArtifactVerificationError, match="commit_id_invalid"):
        resolver.resolve(IMAGE_REPOSITORY, "main")
    with pytest.raises(ArtifactVerificationError, match="image_digest_resolution_failed"):
        CraneCosignArtifactResolver(_config(), runner=_Runner([(0, b"\xff")])).resolve(IMAGE_REPOSITORY, COMMIT_ID)


def test_crane_resolver_accepts_single_platform_config_manifest() -> None:
    config = {
        "os": "linux",
        "architecture": "arm64",
        "config": {"Labels": {"org.opencontainers.image.revision": COMMIT_ID}},
    }
    runner = _Runner(
        [
            (0, DIGEST.encode()),
            (0, b"{}"),
            (0, json.dumps(config).encode()),
        ]
    )

    artifact = CraneCosignArtifactResolver(_config(require_signature=False), runner=runner).resolve(
        IMAGE_REPOSITORY, COMMIT_ID
    )

    assert artifact.is_linux_arm64 is True


@pytest.mark.parametrize("config", [None, {}, {"config": []}, {"config": {"Labels": []}}])
def test_crane_resolver_rejects_missing_oci_revision_shape(config: object) -> None:
    manifest = {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]}
    runner = _Runner([(0, DIGEST.encode()), (0, json.dumps(manifest).encode()), (0, json.dumps(config).encode())])

    with pytest.raises(ArtifactVerificationError, match="oci_revision_mismatch"):
        CraneCosignArtifactResolver(_config(require_signature=False), runner=runner).resolve(
            IMAGE_REPOSITORY, COMMIT_ID
        )


def test_controller_rejects_invalid_list_response() -> None:
    class _Transport:
        def __init__(self, response: KubernetesHttpResponse) -> None:
            self.response = response

        def send(self, _request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            return self.response

    for response, reason in (
        (KubernetesHttpResponse(503, {}, b"{}"), "kubernetes_release_list_failed"),
        (KubernetesHttpResponse(200, {}, b"not-json"), "kubernetes_release_list_invalid"),
        (_response({"items": {}}), "kubernetes_release_items_invalid"),
    ):
        with pytest.raises(RuntimeError, match=reason):
            KubernetesReleaseController(
                _config(), transport=_Transport(response), resolver=_Resolver()
            ).reconcile_once()


@pytest.mark.parametrize(
    ("resource", "target", "reason"),
    [
        (_resource(operation="rollback"), None, "rollback_target_missing"),
        (_resource(operation="rollback", rollback_target="missing"), None, "rollback_target_not_found"),
        (
            _resource(operation="rollback", rollback_target="previous"),
            _resource(phase="Pending", image_digest=DIGEST),
            "rollback_target_not_verified_live",
        ),
    ],
)
def test_controller_rejects_unverified_rollback_target(
    resource: dict[str, object],
    target: dict[str, object] | None,
    reason: str,
) -> None:
    if target is not None:
        cast(dict[str, object], target["metadata"])["name"] = "previous"
    transport = _ClusterTransport(resource, rollback_target=target)

    result = KubernetesReleaseController(_config(), transport=transport, resolver=_Resolver()).reconcile_once()[0]

    assert result.status == "failed"
    assert result.reason == reason


@pytest.mark.parametrize(
    ("failed_path", "expected_reason"),
    [("/deployments/", "controller_reconcile_failed"), ("/status", "failure_status_write_failed")],
)
def test_controller_classifies_cluster_patch_failures(failed_path: str, expected_reason: str) -> None:
    class _FailingTransport(_ClusterTransport):
        def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            if request.method == "PATCH" and failed_path in request.path:
                self.requests.append(request)
                return KubernetesHttpResponse(503, {}, b"{}")
            return super().send(request)

    resource = _resource()
    if failed_path == "/status":
        cast(dict[str, object], resource["spec"])["operation"] = "invalid"
    result = KubernetesReleaseController(
        _config(), transport=_FailingTransport(resource), resolver=_Resolver()
    ).reconcile_once()[0]

    assert result.status == "failed"
    assert result.reason == expected_reason


def test_controller_classifies_replica_failure_and_invalid_started_timestamp() -> None:
    replica_failure = _ClusterTransport(
        _resource(),
        is_rollout_live=False,
        rollout_conditions=[{"type": "ReplicaFailure", "status": "True", "reason": "private"}],
    )
    failed = KubernetesReleaseController(_config(), transport=replica_failure, resolver=_Resolver()).reconcile_once()[0]
    assert failed.reason == "replica_failure"

    resource = _resource(phase="Progressing")
    cast(dict[str, object], resource["status"])["startedAt"] = "not-a-timestamp"
    timed_out = KubernetesReleaseController(
        _config(), transport=_ClusterTransport(resource, is_rollout_live=False), resolver=_Resolver()
    ).reconcile_once()[0]
    assert timed_out.reason == "rollout_timeout"


def test_controller_classifies_generic_progress_failure_and_naive_started_timestamp() -> None:
    progressing_failure = _ClusterTransport(
        _resource(),
        is_rollout_live=False,
        rollout_conditions=[{"type": "Progressing", "status": "False", "reason": "Other"}],
    )
    result = KubernetesReleaseController(
        _config(), transport=progressing_failure, resolver=_Resolver()
    ).reconcile_once()[0]
    assert result.reason == "rollout_progressing_failed"

    resource = _resource(phase="Progressing")
    cast(dict[str, object], resource["status"])["startedAt"] = "2026-08-16T01:02:03"
    result = KubernetesReleaseController(
        _config(), transport=_ClusterTransport(resource, is_rollout_live=False), resolver=_Resolver()
    ).reconcile_once()[0]
    assert result.reason == "rollout_timeout"


def test_controller_ignores_unrelated_or_malformed_rollout_conditions() -> None:
    conditions = cast(
        list[dict[str, str]],
        ["not-an-object", {"type": "ReplicaFailure", "status": "False", "reason": "none"}],
    )
    result = KubernetesReleaseController(
        _config(),
        transport=_ClusterTransport(_resource(), is_rollout_live=False, rollout_conditions=conditions),
        resolver=_Resolver(),
    ).reconcile_once()[0]

    assert result.reason == "rollout_progressing"


def test_controller_classifies_workload_observation_and_container_identity_failure() -> None:
    class _ObservationFailure(_ClusterTransport):
        def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            if request.method == "GET" and "/deployments/" in request.path:
                self.requests.append(request)
                return KubernetesHttpResponse(503, {}, b"{}")
            return super().send(request)

    failed = KubernetesReleaseController(
        _config(), transport=_ObservationFailure(_resource()), resolver=_Resolver()
    ).reconcile_once()[0]
    assert failed.reason == "controller_reconcile_failed"

    class _WrongContainer(_ClusterTransport):
        def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            response = super().send(request)
            if request.method == "GET" and "/deployments/" in request.path:
                payload = json.loads(response.body)
                payload["spec"]["template"]["spec"]["containers"] = [{"name": "other", "image": self.workload_image}]
                return _response(payload)
            return response

    waiting = KubernetesReleaseController(
        _config(), transport=_WrongContainer(_resource()), resolver=_Resolver()
    ).reconcile_once()[0]
    assert waiting.reason == "rollout_progressing"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda resource: resource.update({"spec": []}),
        lambda resource: cast(dict[str, object], resource["spec"]).update({"workloadRef": []}),
        lambda resource: cast(dict[str, object], resource["status"]).update({"phase": "Unknown"}),
        lambda resource: cast(dict[str, object], resource["metadata"]).update({"generation": 0}),
        lambda resource: cast(dict[str, object], resource["status"]).update({"observedGeneration": -1}),
    ],
)
def test_controller_fails_closed_on_malformed_release_resource(mutation: object) -> None:
    resource = _resource()
    mutation(resource)  # type: ignore[operator]

    result = KubernetesReleaseController(
        _config(), transport=_ClusterTransport(resource), resolver=_Resolver()
    ).reconcile_once()[0]

    assert result.status == "failed"
    if cast(dict[str, object], resource["metadata"]).get("generation") == 0:
        assert result.reason == "resource_identity_invalid"


def test_controller_keeps_reconciling_after_resource_identity_failure() -> None:
    valid_resource = _resource()
    invalid_resource = _resource()
    cast(dict[str, object], invalid_resource["metadata"])["generation"] = 0

    class _MultiResourceTransport(_ClusterTransport):
        def send(self, request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            if request.method == "GET" and request.path.endswith("/foundrydeployments"):
                self.requests.append(request)
                return _response({"items": [invalid_resource, valid_resource]})
            return super().send(request)

    results = KubernetesReleaseController(
        _config(), transport=_MultiResourceTransport(valid_resource), resolver=_Resolver()
    ).reconcile_once()

    assert [(result.status, result.reason) for result in results] == [
        ("failed", "resource_identity_invalid"),
        ("reconciled", "exact_digest_live"),
    ]


def test_controller_rejects_oversized_list_body() -> None:
    class _Transport:
        def send(self, _request: KubernetesHttpRequest) -> KubernetesHttpResponse:
            return KubernetesHttpResponse(200, {}, b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(RuntimeError, match="kubernetes_release_list_invalid"):
        KubernetesReleaseController(_config(), transport=_Transport(), resolver=_Resolver()).reconcile_once()


@pytest.mark.parametrize(
    "config",
    [
        KubernetesReleaseControllerConfig("Invalid_Namespace", "https://issuer", "identity"),
        KubernetesReleaseControllerConfig("foundry-qa", "http://issuer", "identity"),
        KubernetesReleaseControllerConfig("foundry-qa", "https://issuer", ""),
        KubernetesReleaseControllerConfig("foundry-qa", "https://issuer", "identity", timeout_seconds=0),
        KubernetesReleaseControllerConfig("foundry-qa", "https://issuer", "identity", timeout_seconds=61),
        KubernetesReleaseControllerConfig("foundry-qa", "https://issuer", "identity", rollout_timeout_seconds=0),
        KubernetesReleaseControllerConfig("foundry-qa", "https://issuer", "identity", rollout_timeout_seconds=3601),
    ],
)
def test_controller_rejects_unsafe_configuration(config: KubernetesReleaseControllerConfig) -> None:
    with pytest.raises(ValueError):
        KubernetesReleaseController(config, transport=_ClusterTransport(_resource()), resolver=_Resolver())
