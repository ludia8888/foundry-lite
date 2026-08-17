"""Restricted in-cluster broker that owns Kubernetes Job creation for user code."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import quote, urlencode

from foundry_lite.infrastructure.kubernetes_execution_spec import (
    KubernetesExecutionSpec,
    kubernetes_execution_job_payload,
    kubernetes_execution_spec_hash,
    parse_kubernetes_execution_command,
    validate_kubernetes_image_pull_secrets,
)

_MAX_KUBERNETES_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_COMMAND_ITEMS = 128
_MAX_COMMAND_ITEM_BYTES = 4096
_HASH_CHUNK_BYTES = 1024 * 1024
_EXECUTION_NAME = re.compile(r"^foundry-lite-(?:python|function|model)-[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class KubernetesExecutionBrokerConfig:
    """Bounded namespace, PVC, polling, and observation settings for the broker."""

    namespace: str
    pvc_name: str
    shared_workspace_root: Path
    pvc_mount_root: Path
    poll_interval_seconds: float = 0.25
    observation_grace_seconds: float = 15.0
    image_pull_secrets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KubernetesExecutionBrokerResult:
    """Terminal or ambiguous Job outcome with secret-free runtime evidence."""

    name: str
    status: Literal["succeeded", "failed", "outcome_unknown"]
    exit_code: int
    runtime_evidence: Mapping[str, object]

    def to_payload(self) -> dict[str, object]:
        """Return the stable wire representation consumed by the internal adapter."""

        return {
            "name": self.name,
            "status": self.status,
            "exitCode": self.exit_code,
            "runtimeEvidence": dict(self.runtime_evidence),
        }


@dataclass(frozen=True, slots=True)
class ExecutionKubernetesRequest:
    """Allowlisted in-cluster Kubernetes API request."""

    method: Literal["GET", "POST", "DELETE"]
    path: str
    body: bytes | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0
    max_response_bytes: int = _MAX_KUBERNETES_RESPONSE_BYTES


@dataclass(frozen=True, slots=True)
class ExecutionKubernetesResponse:
    """Bounded response returned by the Kubernetes transport."""

    status_code: int
    body: bytes = field(default=b"", repr=False)


class ExecutionKubernetesTransportError(RuntimeError):
    """Classify transport failures without exposing Kubernetes response details."""

    pass


class ExecutionKubernetesTransport(Protocol):
    """Port used by the broker for real and fake Kubernetes API transports."""

    def send(self, request: ExecutionKubernetesRequest) -> ExecutionKubernetesResponse: ...


class InClusterExecutionKubernetesTransport:
    """Bounded Kubernetes transport restricted to batch Jobs and their Pods."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        token_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ca_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
    ) -> None:
        self._host = host or os.environ.get("KUBERNETES_SERVICE_HOST", "")
        self._port = port or int(os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443"))
        self._token_path = token_path
        self._context = ssl.create_default_context(cafile=str(ca_path))
        if not self._host:
            raise ValueError("Kubernetes service host is required")

    def send(self, request: ExecutionKubernetesRequest) -> ExecutionKubernetesResponse:
        _validate_kubernetes_path(request.path)
        connection = HTTPSConnection(self._host, self._port, context=self._context, timeout=request.timeout_seconds)
        try:
            token = self._token_path.read_text(encoding="utf-8").strip()
            if not token:
                raise ExecutionKubernetesTransportError("service_account_token_missing")
            connection.request(
                request.method,
                request.path,
                body=request.body,
                headers={
                    "accept": "application/json",
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                    "user-agent": "Foundry-lite/kubernetes-execution-broker",
                },
            )
            response = connection.getresponse()
            body = response.read(request.max_response_bytes + 1)
            if len(body) > request.max_response_bytes:
                raise ExecutionKubernetesTransportError("response_too_large")
            return ExecutionKubernetesResponse(response.status, body)
        except ExecutionKubernetesTransportError:
            raise
        except TimeoutError as exc:
            raise ExecutionKubernetesTransportError("timeout") from exc
        except (HTTPException, OSError, UnicodeError) as exc:
            raise ExecutionKubernetesTransportError("unavailable") from exc
        finally:
            connection.close()


class KubernetesExecutionBroker:
    """Create, reconcile, observe, and clean a deterministic execution Job."""

    def __init__(
        self,
        config: KubernetesExecutionBrokerConfig,
        *,
        transport: ExecutionKubernetesTransport | None = None,
    ) -> None:
        _validate_config(config)
        self._config = config
        self._transport = transport or InClusterExecutionKubernetesTransport()

    def execute(self, payload: Mapping[str, object]) -> KubernetesExecutionBrokerResult:
        command, timeout_seconds = _execution_request(payload)
        spec = parse_kubernetes_execution_command(
            command,
            timeout_seconds=timeout_seconds,
            shared_workspace_root=self._config.shared_workspace_root,
            pvc_mount_root=self._config.pvc_mount_root,
        )
        started = time.monotonic()
        job = kubernetes_execution_job_payload(
            spec,
            namespace=self._config.namespace,
            pvc_name=self._config.pvc_name,
            image_pull_secrets=self._config.image_pull_secrets,
        )
        resource = self._create_or_reconcile(spec, job)
        if resource is None:
            return self._result(spec, "outcome_unknown", 125, started, None, "create_outcome_unknown")
        return self._observe(spec, started, resource)

    def cleanup(self, name: str) -> bool:
        _validate_execution_name(name)
        response = self._send(
            "DELETE",
            _job_path(self._config.namespace, name),
            {"kind": "DeleteOptions", "apiVersion": "v1", "propagationPolicy": "Background"},
        )
        return response.status_code in {200, 202, 404}

    def _create_or_reconcile(
        self,
        spec: KubernetesExecutionSpec,
        job: Mapping[str, object],
    ) -> Mapping[str, object] | None:
        expected_hash = kubernetes_execution_spec_hash(
            spec,
            image_pull_secrets=self._config.image_pull_secrets,
        )
        try:
            response = self._send("POST", _jobs_path(self._config.namespace), job)
        except ExecutionKubernetesTransportError:
            return self._get_existing_job(spec.name, expected_hash)
        if response.status_code == 201:
            resource = _json_mapping(response.body, "execution_job_create_response_invalid")
            _verify_job_spec_hash(resource, expected_hash)
            return resource
        if response.status_code == 409:
            return self._get_existing_job(spec.name, expected_hash)
        raise RuntimeError("execution_job_create_failed")

    def _get_existing_job(self, name: str, expected_hash: str) -> Mapping[str, object] | None:
        try:
            response = self._send("GET", _job_path(self._config.namespace, name))
        except ExecutionKubernetesTransportError:
            return None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError("execution_job_reconcile_failed")
        resource = _json_mapping(response.body, "execution_job_reconcile_response_invalid")
        _verify_job_spec_hash(resource, expected_hash)
        return resource

    def _observe(
        self,
        spec: KubernetesExecutionSpec,
        started: float,
        resource: Mapping[str, object],
    ) -> KubernetesExecutionBrokerResult:
        deadline = started + spec.timeout_seconds + self._config.observation_grace_seconds
        current = resource
        while time.monotonic() < deadline:
            terminal = _job_terminal_status(current)
            if terminal is not None:
                exit_code, reason = self._pod_exit(spec.name, terminal)
                return self._result(spec, terminal, exit_code, started, current, reason)
            time.sleep(self._config.poll_interval_seconds)
            try:
                response = self._send("GET", _job_path(self._config.namespace, spec.name))
            except ExecutionKubernetesTransportError:
                return self._result(spec, "outcome_unknown", 125, started, current, "observation_unavailable")
            if response.status_code != 200:
                return self._result(spec, "outcome_unknown", 125, started, current, "observation_failed")
            current = _json_mapping(response.body, "execution_job_observation_invalid")
        return self._result(spec, "outcome_unknown", 125, started, current, "observation_timeout")

    def _pod_exit(self, name: str, job_status: str) -> tuple[int, str]:
        query = urlencode({"labelSelector": f"job-name={name}", "limit": 2})
        response = self._send("GET", f"{_pods_path(self._config.namespace)}?{query}")
        if response.status_code != 200:
            return (0, "job_succeeded") if job_status == "succeeded" else (125, "pod_status_unavailable")
        items = _json_mapping(response.body, "execution_pods_invalid").get("items")
        if not isinstance(items, list) or not items:
            return (0, "job_succeeded") if job_status == "succeeded" else (125, "pod_status_missing")
        for item in items:
            if isinstance(item, Mapping):
                terminated = _terminated_state(item)
                if terminated is not None:
                    return terminated
        return (0, "job_succeeded") if job_status == "succeeded" else (125, "pod_exit_missing")

    def _result(
        self,
        spec: KubernetesExecutionSpec,
        status: Literal["succeeded", "failed", "outcome_unknown"],
        exit_code: int,
        started: float,
        job: Mapping[str, object] | None,
        reason: str,
    ) -> KubernetesExecutionBrokerResult:
        metadata = job.get("metadata") if isinstance(job, Mapping) else None
        uid = metadata.get("uid") if isinstance(metadata, Mapping) else None
        evidence: dict[str, object] = {
            "executionMode": "kubernetes-job",
            "runtime": "isolated_kubernetes_job",
            "jobName": spec.name,
            "jobUid": uid if isinstance(uid, str) else None,
            "namespace": self._config.namespace,
            "imageReference": spec.image_reference,
            "imageDigest": spec.image_reference.rsplit("@", 1)[-1],
            "networkDisabled": True,
            "serviceAccountTokenMounted": False,
            "rootFilesystemReadOnly": True,
            "capabilitiesDropped": True,
            "seccompProfile": "RuntimeDefault",
            "uid": spec.uid,
            "gid": spec.gid,
            "cpuLimit": spec.cpu,
            "memoryLimit": spec.memory,
            "pidsLimitRequested": spec.pids_limit,
            "pidsLimitEnforcement": "cluster_preflight_required",
            "timeoutSeconds": spec.timeout_seconds,
            "maxFileBytes": spec.max_file_bytes,
            "durationMs": int((time.monotonic() - started) * 1000),
            "exitCode": exit_code,
            "reason": reason,
            "executionSpecSha256": kubernetes_execution_spec_hash(
                spec,
                image_pull_secrets=self._config.image_pull_secrets,
            ),
            "resultSha256": _terminal_target_hash(spec, status, "/sandbox-output/execution-result.json")
            or _terminal_target_hash(spec, status, "/model-output/result.json"),
            "outputSha256": _terminal_target_hash(spec, status, "/sandbox-output/result.parquet"),
            "stderrCollection": "hash_only_not_collected_by_broker",
        }
        return KubernetesExecutionBrokerResult(spec.name, status, exit_code, evidence)

    def _send(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> ExecutionKubernetesResponse:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
        return self._transport.send(ExecutionKubernetesRequest(method, path, body=body))


def _execution_request(payload: Mapping[str, object]) -> tuple[tuple[str, ...], float]:
    if payload.get("schemaVersion") != 1:
        raise ValueError("Kubernetes execution request schema is unsupported")
    command = _execution_command(payload.get("command"))
    timeout = _execution_timeout(payload.get("timeoutSeconds"))
    return command, timeout


def _execution_command(raw_command: object) -> tuple[str, ...]:
    if not isinstance(raw_command, list) or not raw_command or len(raw_command) > _MAX_COMMAND_ITEMS:
        raise ValueError("Kubernetes execution command is invalid")
    if not all(isinstance(item, str) and len(item.encode("utf-8")) <= _MAX_COMMAND_ITEM_BYTES for item in raw_command):
        raise ValueError("Kubernetes execution command item is invalid")
    return tuple(cast(list[str], raw_command))


def _execution_timeout(timeout: object) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not math.isfinite(timeout):
        raise ValueError("Kubernetes execution timeout is invalid")
    return float(timeout)


def _job_terminal_status(resource: Mapping[str, object]) -> Literal["succeeded", "failed"] | None:
    status = resource.get("status")
    if not isinstance(status, Mapping):
        return None
    if isinstance(status.get("succeeded"), int) and status["succeeded"] >= 1:
        return "succeeded"
    if isinstance(status.get("failed"), int) and status["failed"] >= 1:
        return "failed"
    return None


def _terminated_state(pod: Mapping[str, object]) -> tuple[int, str] | None:
    status = pod.get("status")
    statuses = status.get("containerStatuses") if isinstance(status, Mapping) else None
    if not isinstance(statuses, list):
        return None
    for container in statuses:
        state = container.get("state") if isinstance(container, Mapping) else None
        terminated = state.get("terminated") if isinstance(state, Mapping) else None
        if not isinstance(terminated, Mapping):
            continue
        exit_code = terminated.get("exitCode")
        reason = terminated.get("reason")
        if isinstance(exit_code, int):
            return exit_code, reason if isinstance(reason, str) else "container_terminated"
    return None


def _target_hash(spec: KubernetesExecutionSpec, target: str) -> str | None:
    source = next((mount.source for mount in spec.mounts if mount.target == target), None)
    if source is None:
        return None
    try:
        if not source.is_file() or source.stat().st_size == 0:
            return None
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    except OSError:
        return None


def _terminal_target_hash(
    spec: KubernetesExecutionSpec,
    status: Literal["succeeded", "failed", "outcome_unknown"],
    target: str,
) -> str | None:
    return _target_hash(spec, target) if status != "outcome_unknown" else None


def _verify_job_spec_hash(resource: Mapping[str, object], expected_hash: str) -> None:
    metadata = resource.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, Mapping) else None
    observed = annotations.get("foundry-lite.io/execution-spec-sha256") if isinstance(annotations, Mapping) else None
    if observed != expected_hash:
        raise RuntimeError("execution_job_reconcile_spec_mismatch")


def _json_mapping(payload: bytes, reason: str) -> Mapping[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(reason) from exc
    if not isinstance(value, Mapping):
        raise RuntimeError(reason)
    return cast(Mapping[str, object], value)


def _validate_config(config: KubernetesExecutionBrokerConfig) -> None:
    if not config.namespace or not config.pvc_name:
        raise ValueError("Kubernetes execution broker namespace and PVC are required")
    if not config.shared_workspace_root.is_absolute() or not config.pvc_mount_root.is_absolute():
        raise ValueError("Kubernetes execution broker paths must be absolute")
    if config.poll_interval_seconds <= 0 or config.poll_interval_seconds > 5:
        raise ValueError("Kubernetes execution broker poll interval is invalid")
    if config.observation_grace_seconds <= 0 or config.observation_grace_seconds > 60:
        raise ValueError("Kubernetes execution broker observation grace is invalid")
    validate_kubernetes_image_pull_secrets(config.image_pull_secrets)


def _validate_execution_name(name: str) -> None:
    if _EXECUTION_NAME.fullmatch(name) is None:
        raise ValueError("Kubernetes execution name is invalid")


def _validate_kubernetes_path(path: str) -> None:
    allowed = path.startswith("/apis/batch/v1/namespaces/") or path.startswith("/api/v1/namespaces/")
    if not allowed or ".." in path or "//" in path:
        raise ExecutionKubernetesTransportError("path_not_allowed")


def _jobs_path(namespace: str) -> str:
    return f"/apis/batch/v1/namespaces/{quote(namespace, safe='')}/jobs"


def _job_path(namespace: str, name: str) -> str:
    return f"{_jobs_path(namespace)}/{quote(name, safe='')}"


def _pods_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{quote(namespace, safe='')}/pods"
