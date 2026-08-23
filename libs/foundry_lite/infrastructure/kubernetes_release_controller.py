"""Bounded controller for immutable ``FoundryDeployment`` resources."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - fixed verifier executables; remove if executable selection becomes unbounded.
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from foundry_lite.infrastructure.adapters.kubernetes_deployment import (
    InClusterKubernetesHttpTransport,
    KubernetesHttpRequest,
    KubernetesHttpResponse,
    KubernetesHttpTransport,
    KubernetesTransportError,
)

_API_GROUP = "release.foundry-lite.io"
_API_VERSION = "v1alpha1"
_RESOURCE_PLURAL = "foundrydeployments"
_COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_TERMINAL_PHASES = frozenset({"Live", "Failed", "Canceled"})
_MAX_BODY_BYTES = 2 * 1024 * 1024

ControllerPhase = Literal[
    "Pending",
    "ResolvingArtifact",
    "VerifyingArtifact",
    "Applying",
    "Progressing",
    "Live",
    "Failed",
    "Canceled",
]
ControllerResultStatus = Literal["reconciled", "waiting", "terminal", "failed"]


@dataclass(frozen=True, slots=True)
class KubernetesReleaseControllerConfig:
    namespace: str
    signature_issuer: str
    signature_identity_regexp: str
    timeout_seconds: float = 20.0
    rollout_timeout_seconds: float = 600.0
    require_signature: bool = True


@dataclass(frozen=True, slots=True)
class VerifiedImageArtifact:
    repository: str
    commit_id: str
    digest: str
    image_reference: str
    is_linux_arm64: bool
    is_signature_verified: bool


@dataclass(frozen=True, slots=True)
class KubernetesReleaseControllerResult:
    resource_name: str
    status: ControllerResultStatus
    phase: ControllerPhase
    reason: str
    image_digest: str | None = None


class ArtifactVerificationError(RuntimeError):
    """Safe classification of a registry or signature verification failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReleaseArtifactResolver(Protocol):
    def resolve(self, repository: str, commit_id: str) -> VerifiedImageArtifact: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    return_code: int
    stdout: bytes


class BoundedCommandRunner(Protocol):
    def run(self, arguments: Sequence[str], *, timeout_seconds: float, max_output_bytes: int) -> CommandResult: ...


class SubprocessCommandRunner:
    """Run fixed verification tools without a shell or inherited output."""

    _ALLOWED_EXECUTABLES = frozenset({"crane", "cosign"})

    def run(self, arguments: Sequence[str], *, timeout_seconds: float, max_output_bytes: int) -> CommandResult:
        if not arguments or arguments[0] not in self._ALLOWED_EXECUTABLES:
            raise ArtifactVerificationError("verification_executable_not_allowed")
        try:
            completed = subprocess.run(  # nosec B603 - allowlisted verifier argv; remove if shell or free argv appears.
                tuple(arguments),
                check=False,
                capture_output=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ArtifactVerificationError("verification_timeout") from exc
        except OSError as exc:
            raise ArtifactVerificationError("verification_tool_unavailable") from exc
        if len(completed.stdout) > max_output_bytes or len(completed.stderr) > max_output_bytes:
            raise ArtifactVerificationError("verification_output_too_large")
        return CommandResult(return_code=completed.returncode, stdout=completed.stdout)


class CraneCosignArtifactResolver:
    """Resolve an exact OCI digest and verify revision, arm64 and keyless signature."""

    def __init__(
        self,
        config: KubernetesReleaseControllerConfig,
        *,
        runner: BoundedCommandRunner | None = None,
    ) -> None:
        self._config = config
        self._runner = runner or SubprocessCommandRunner()

    def resolve(self, repository: str, commit_id: str) -> VerifiedImageArtifact:
        _validate_repository(repository)
        _validate_commit(commit_id)
        tagged_reference = f"{repository}:sha-{commit_id}"
        digest = self._text_command(("crane", "digest", tagged_reference), "image_digest_resolution_failed")
        _validate_digest(digest)
        immutable_reference = f"{repository}@{digest}"
        manifest = self._json_command(("crane", "manifest", immutable_reference), "image_manifest_invalid")
        config = self._json_command(
            ("crane", "config", "--platform", "linux/arm64", immutable_reference),
            "image_config_invalid",
        )
        if not _contains_linux_arm64(manifest, config):
            raise ArtifactVerificationError("linux_arm64_image_missing")
        if _oci_revision(config) != commit_id:
            raise ArtifactVerificationError("oci_revision_mismatch")
        is_verified = self._verify_signature(immutable_reference)
        return VerifiedImageArtifact(repository, commit_id, digest, immutable_reference, True, is_verified)

    def _verify_signature(self, immutable_reference: str) -> bool:
        if not self._config.require_signature:
            return False
        arguments = (
            "cosign",
            "verify",
            "--certificate-oidc-issuer",
            self._config.signature_issuer,
            "--certificate-identity-regexp",
            self._config.signature_identity_regexp,
            "--output",
            "json",
            immutable_reference,
        )
        payload = self._json_command(arguments, "image_signature_verification_failed")
        if not isinstance(payload, list) or not payload:
            raise ArtifactVerificationError("image_signature_evidence_missing")
        return True

    def _json_command(self, arguments: Sequence[str], reason: str) -> object:
        raw = self._command(arguments, reason)
        try:
            return json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactVerificationError(reason) from exc

    def _text_command(self, arguments: Sequence[str], reason: str) -> str:
        raw = self._command(arguments, reason)
        try:
            return raw.decode("utf-8").strip()
        except UnicodeError as exc:
            raise ArtifactVerificationError(reason) from exc

    def _command(self, arguments: Sequence[str], reason: str) -> bytes:
        result = self._runner.run(
            arguments,
            timeout_seconds=self._config.timeout_seconds,
            max_output_bytes=_MAX_BODY_BYTES,
        )
        if result.return_code != 0:
            raise ArtifactVerificationError(reason)
        return result.stdout


class KubernetesReleaseController:
    """Reconcile immutable release CRs without reading Kubernetes Secrets."""

    def __init__(
        self,
        config: KubernetesReleaseControllerConfig,
        *,
        transport: KubernetesHttpTransport | None = None,
        resolver: ReleaseArtifactResolver | None = None,
    ) -> None:
        _validate_controller_config(config)
        self._config = config
        self._transport = transport or InClusterKubernetesHttpTransport()
        self._resolver = resolver or CraneCosignArtifactResolver(config)

    def reconcile_once(self) -> tuple[KubernetesReleaseControllerResult, ...]:
        response = self._send("GET", _release_collection_path(self._config.namespace))
        if response.status_code != 200:
            raise RuntimeError("kubernetes_release_list_failed")
        payload = _json_mapping(response.body, "kubernetes_release_list_invalid")
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("kubernetes_release_items_invalid")
        return tuple(self._reconcile_resource(item) for item in items if isinstance(item, Mapping))

    def _reconcile_resource(self, resource: Mapping[str, object]) -> KubernetesReleaseControllerResult:
        try:
            release = _parse_release(resource)
            if release.phase in _TERMINAL_PHASES and release.observed_generation == release.generation:
                return KubernetesReleaseControllerResult(release.name, "terminal", release.phase, "already_terminal")
            artifact = self._resolve_artifact(release)
            self._patch_status(release, "Applying", "artifact_verified", artifact)
            self._patch_workload(release, artifact)
            return self._observe_rollout(release, artifact)
        except ArtifactVerificationError as exc:
            return self._fail_resource(resource, exc.reason)
        except (KubernetesTransportError, RuntimeError, ValueError):
            return self._fail_resource(resource, "controller_reconcile_failed")

    def _resolve_artifact(self, release: _ReleaseResource) -> VerifiedImageArtifact:
        self._patch_status(release, "ResolvingArtifact", "resolving_exact_image", None)
        if release.operation == "rollback":
            artifact = self._rollback_artifact(release)
        else:
            artifact = self._resolver.resolve(release.image_repository, release.commit_id)
        if self._config.require_signature and not artifact.is_signature_verified:
            raise ArtifactVerificationError("image_signature_verification_required")
        self._patch_status(release, "VerifyingArtifact", "artifact_verified", artifact)
        return artifact

    def _rollback_artifact(self, release: _ReleaseResource) -> VerifiedImageArtifact:
        if release.rollback_target is None:
            raise ArtifactVerificationError("rollback_target_missing")
        response = self._send("GET", _release_path(release.namespace, release.rollback_target))
        if response.status_code != 200:
            raise ArtifactVerificationError("rollback_target_not_found")
        target = _parse_release(_json_mapping(response.body, "rollback_target_invalid"))
        if target.phase != "Live" or target.image_digest is None or target.commit_id != release.commit_id:
            raise ArtifactVerificationError("rollback_target_not_verified_live")
        _validate_digest(target.image_digest)
        return VerifiedImageArtifact(
            release.image_repository,
            release.commit_id,
            target.image_digest,
            f"{release.image_repository}@{target.image_digest}",
            True,
            True,
        )

    def _patch_workload(self, release: _ReleaseResource, artifact: VerifiedImageArtifact) -> None:
        payload = {
            "metadata": {
                "annotations": {
                    "foundry-lite.io/release-id": release.name,
                    "foundry-lite.io/commit": release.commit_id,
                    "foundry-lite.io/image-digest": artifact.digest,
                }
            },
            "spec": {
                "template": {
                    "metadata": {"annotations": {"foundry-lite.io/release-id": release.name}},
                    "spec": {"containers": [{"name": release.container_name, "image": artifact.image_reference}]},
                }
            },
        }
        response = self._send(
            "PATCH",
            _deployment_path(release.namespace, release.workload_name),
            payload,
            content_type="application/strategic-merge-patch+json",
        )
        if response.status_code != 200:
            raise RuntimeError("workload_patch_failed")

    def _observe_rollout(
        self,
        release: _ReleaseResource,
        artifact: VerifiedImageArtifact,
    ) -> KubernetesReleaseControllerResult:
        response = self._send("GET", _deployment_path(release.namespace, release.workload_name))
        if response.status_code != 200:
            raise RuntimeError("workload_observation_failed")
        workload = _json_mapping(response.body, "workload_observation_invalid")
        failure_reason = _rollout_failure_reason(workload)
        if failure_reason is not None:
            self._patch_status(release, "Failed", failure_reason, artifact)
            return KubernetesReleaseControllerResult(release.name, "failed", "Failed", failure_reason, artifact.digest)
        if _rollout_timed_out(release.started_at, self._config.rollout_timeout_seconds):
            reason = "rollout_timeout"
            self._patch_status(release, "Failed", reason, artifact)
            return KubernetesReleaseControllerResult(release.name, "failed", "Failed", reason, artifact.digest)
        is_live = _is_rollout_live(workload, artifact.image_reference, release.container_name)
        phase: ControllerPhase = "Live" if is_live else "Progressing"
        reason = "exact_digest_live" if is_live else "rollout_progressing"
        self._patch_status(release, phase, reason, artifact)
        status: ControllerResultStatus = "reconciled" if is_live else "waiting"
        return KubernetesReleaseControllerResult(release.name, status, phase, reason, artifact.digest)

    def _fail_resource(
        self,
        resource: Mapping[str, object],
        reason: str,
    ) -> KubernetesReleaseControllerResult:
        identity = _failure_identity(resource, self._config.namespace)
        if identity is None:
            return KubernetesReleaseControllerResult(
                _safe_resource_name(resource), "failed", "Failed", "resource_identity_invalid"
            )
        name, namespace, generation = identity
        release = _minimal_release(name, namespace, generation)
        try:
            self._patch_status(release, "Failed", reason, None)
        except (KubernetesTransportError, RuntimeError):
            reason = "failure_status_write_failed"
        return KubernetesReleaseControllerResult(name, "failed", "Failed", reason)

    def _patch_status(
        self,
        release: _ReleaseResource,
        phase: ControllerPhase,
        reason: str,
        artifact: VerifiedImageArtifact | None,
    ) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        status: dict[str, object] = {
            "phase": phase,
            "reason": reason,
            "observedGeneration": release.generation,
            "updatedAt": now,
        }
        if phase in {"ResolvingArtifact", "Applying"} and release.started_at is None:
            status["startedAt"] = now
        if phase in {"Live", "Failed"}:
            status["finishedAt"] = now
        if artifact is not None:
            status["imageDigest"] = artifact.digest
            status["imageReference"] = artifact.image_reference
            status["isSignatureVerified"] = artifact.is_signature_verified
            status["isLinuxArm64"] = artifact.is_linux_arm64
        response = self._send(
            "PATCH",
            f"{_release_path(release.namespace, release.name)}/status",
            {"status": status},
            content_type="application/merge-patch+json",
        )
        if response.status_code != 200:
            raise RuntimeError("release_status_patch_failed")

    def _send(
        self,
        method: Literal["GET", "PATCH"],
        path: str,
        payload: Mapping[str, object] | None = None,
        *,
        content_type: str = "application/json",
    ) -> KubernetesHttpResponse:
        body = None if payload is None else json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return self._transport.send(
            KubernetesHttpRequest(
                method=method,
                path=path,
                body=body,
                content_type=content_type,
                timeout_seconds=self._config.timeout_seconds,
            )
        )


@dataclass(frozen=True, slots=True)
class _ReleaseResource:
    name: str
    namespace: str
    generation: int
    observed_generation: int
    phase: ControllerPhase
    operation: Literal["deploy", "rollback"]
    commit_id: str
    image_repository: str
    workload_name: str
    container_name: str
    rollback_target: str | None
    image_digest: str | None
    started_at: str | None


def _parse_release(resource: Mapping[str, object]) -> _ReleaseResource:
    spec = _mapping(resource.get("spec"), "release_spec_invalid")
    status = _optional_mapping(resource.get("status"))
    workload = _mapping(spec.get("workloadRef"), "workload_ref_invalid")
    name, namespace, generation = _resource_identity(resource, "")
    phase = _phase(status.get("phase", "Pending"))
    operation_value = spec.get("operation")
    if operation_value not in {"deploy", "rollback"}:
        raise ValueError("release_operation_invalid")
    operation = cast(Literal["deploy", "rollback"], operation_value)
    commit_id = _text(spec.get("commitId"), "release_commit_invalid")
    _validate_commit(commit_id)
    repository = _text(spec.get("imageRepository"), "image_repository_invalid")
    _validate_repository(repository)
    return _ReleaseResource(
        name=name,
        namespace=namespace,
        generation=generation,
        observed_generation=_nonnegative_int(status.get("observedGeneration", 0), "observed_generation_invalid"),
        phase=phase,
        operation=operation,
        commit_id=commit_id,
        image_repository=repository,
        workload_name=_dns_label(workload.get("name"), "workload_name_invalid"),
        container_name=_dns_label(workload.get("containerName"), "container_name_invalid"),
        rollback_target=_optional_text(spec.get("rollbackTargetDeployId")),
        image_digest=_optional_text(status.get("imageDigest")),
        started_at=_optional_text(status.get("startedAt")),
    )


def _resource_identity(resource: Mapping[str, object], default_namespace: str) -> tuple[str, str, int]:
    metadata = _mapping(resource.get("metadata"), "release_metadata_invalid")
    name = _dns_label(metadata.get("name"), "release_name_invalid")
    namespace = _dns_label(metadata.get("namespace", default_namespace), "release_namespace_invalid")
    generation = _positive_int(metadata.get("generation", 1), "release_generation_invalid")
    return name, namespace, generation


def _failure_identity(resource: Mapping[str, object], default_namespace: str) -> tuple[str, str, int] | None:
    try:
        return _resource_identity(resource, default_namespace)
    except ValueError:
        return None


def _safe_resource_name(resource: Mapping[str, object]) -> str:
    metadata = resource.get("metadata")
    name = metadata.get("name") if isinstance(metadata, Mapping) else None
    return name if isinstance(name, str) and _DNS_LABEL_PATTERN.fullmatch(name) else "unknown-resource"


def _minimal_release(name: str, namespace: str, generation: int) -> _ReleaseResource:
    return _ReleaseResource(
        name,
        namespace,
        generation,
        0,
        "Pending",
        "deploy",
        "0" * 40,
        "invalid/placeholder",
        "invalid-placeholder",
        "invalid-placeholder",
        None,
        None,
        None,
    )


def _is_rollout_live(workload: Mapping[str, object], image_reference: str, container_name: str) -> bool:
    metadata = _mapping(workload.get("metadata"), "workload_metadata_invalid")
    spec = _mapping(workload.get("spec"), "workload_spec_invalid")
    status = _optional_mapping(workload.get("status"))
    template = _mapping(spec.get("template"), "workload_template_invalid")
    pod_spec = _mapping(template.get("spec"), "workload_pod_spec_invalid")
    containers = pod_spec.get("containers")
    replicas = _nonnegative_int(spec.get("replicas", 1), "workload_replicas_invalid")
    generation = _positive_int(metadata.get("generation", 1), "workload_generation_invalid")
    if not isinstance(containers, list) or not _contains_container(containers, container_name, image_reference):
        return False
    return (
        _nonnegative_int(status.get("observedGeneration", 0), "workload_observed_generation_invalid") >= generation
        and _nonnegative_int(status.get("updatedReplicas", 0), "workload_updated_replicas_invalid") == replicas
        and _nonnegative_int(status.get("availableReplicas", 0), "workload_available_replicas_invalid") == replicas
        and _nonnegative_int(status.get("unavailableReplicas", 0), "workload_unavailable_replicas_invalid") == 0
    )


def _contains_container(containers: list[object], name: str, image: str) -> bool:
    return any(
        isinstance(item, Mapping) and item.get("name") == name and item.get("image") == image for item in containers
    )


def _contains_linux_arm64(manifest: object, config: object) -> bool:
    if isinstance(manifest, Mapping) and isinstance(manifest.get("manifests"), list):
        return any(
            isinstance(item, Mapping)
            and isinstance(item.get("platform"), Mapping)
            and item["platform"].get("os") == "linux"
            and item["platform"].get("architecture") == "arm64"
            for item in manifest["manifests"]
        )
    return isinstance(config, Mapping) and config.get("os") == "linux" and config.get("architecture") == "arm64"


def _oci_revision(config: object) -> str | None:
    if not isinstance(config, Mapping):
        return None
    nested = config.get("config")
    if not isinstance(nested, Mapping):
        return None
    labels = nested.get("Labels")
    if not isinstance(labels, Mapping):
        return None
    revision = labels.get("org.opencontainers.image.revision")
    return revision if isinstance(revision, str) else None


def _release_collection_path(namespace: str) -> str:
    return f"/apis/{_API_GROUP}/{_API_VERSION}/namespaces/{namespace}/{_RESOURCE_PLURAL}"


def _release_path(namespace: str, name: str) -> str:
    return f"{_release_collection_path(namespace)}/{name}"


def _deployment_path(namespace: str, name: str) -> str:
    return f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}"


def _json_mapping(body: bytes, reason: str) -> Mapping[str, object]:
    if len(body) > _MAX_BODY_BYTES:
        raise RuntimeError(reason)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(reason) from exc
    return _mapping(payload, reason)


def _mapping(value: object, reason: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(reason)
    return value


def _optional_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(reason)
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value, "optional_text_invalid")


def _dns_label(value: object, reason: str) -> str:
    text = _text(value, reason)
    if not _DNS_LABEL_PATTERN.fullmatch(text):
        raise ValueError(reason)
    return text


def _positive_int(value: object, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(reason)
    return value


def _nonnegative_int(value: object, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(reason)
    return value


def _phase(value: object) -> ControllerPhase:
    allowed: tuple[ControllerPhase, ...] = (
        "Pending",
        "ResolvingArtifact",
        "VerifyingArtifact",
        "Applying",
        "Progressing",
        "Live",
        "Failed",
        "Canceled",
    )
    if value not in allowed:
        raise ValueError("release_phase_invalid")
    return value


def _validate_controller_config(config: KubernetesReleaseControllerConfig) -> None:
    _dns_label(config.namespace, "controller_namespace_invalid")
    if not config.signature_issuer.startswith("https://") or len(config.signature_issuer) > 512:
        raise ValueError("signature_issuer_invalid")
    if not config.signature_identity_regexp or len(config.signature_identity_regexp) > 512:
        raise ValueError("signature_identity_invalid")
    if config.timeout_seconds <= 0 or config.timeout_seconds > 60:
        raise ValueError("controller_timeout_invalid")
    if config.rollout_timeout_seconds <= 0 or config.rollout_timeout_seconds > 3600:
        raise ValueError("controller_rollout_timeout_invalid")


def _rollout_failure_reason(workload: Mapping[str, object]) -> str | None:
    status = _optional_mapping(workload.get("status"))
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return None
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if condition.get("type") == "Progressing" and condition.get("status") == "False":
            reason = condition.get("reason")
            if reason == "ProgressDeadlineExceeded":
                return "progress_deadline_exceeded"
            return "rollout_progressing_failed"
        if condition.get("type") == "ReplicaFailure" and condition.get("status") == "True":
            return "replica_failure"
    return None


def _rollout_timed_out(started_at: str | None, timeout_seconds: float) -> bool:
    if started_at is None:
        return False
    try:
        parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        return True
    return (datetime.now(UTC) - parsed).total_seconds() > timeout_seconds


def _validate_repository(repository: str) -> None:
    if not _REPOSITORY_PATTERN.fullmatch(repository) or ":" in repository or "@" in repository:
        raise ArtifactVerificationError("image_repository_invalid")


def _validate_commit(commit_id: str) -> None:
    if not _COMMIT_PATTERN.fullmatch(commit_id):
        raise ArtifactVerificationError("commit_id_invalid")


def _validate_digest(digest: str) -> None:
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ArtifactVerificationError("image_digest_invalid")
