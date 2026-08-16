"""Code-execution adapter that delegates fixed sandbox commands to an internal Job broker."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - only the typed timeout exception is reused.
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from typing import cast
from urllib.parse import SplitResult, quote, urlsplit

from foundry_lite.infrastructure.adapters.container_code_execution import ContainerCodeExecutionAdapter
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    ContainerCodeExecutionConfig,
    ContainerCommandResult,
)

_MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class KubernetesJobBrokerConfig:
    endpoint: str
    bearer_token: str
    shared_workspace_root: Path
    request_grace_seconds: float = 15.0


class KubernetesJobBrokerCommandRunner:
    """Translate the adapter's fixed sandbox argv into one authenticated broker request."""

    def __init__(self, config: KubernetesJobBrokerConfig) -> None:
        self._config = config
        self._endpoint = _broker_endpoint(config.endpoint)
        self._hostname = self._endpoint.hostname or ""
        _validate_broker_config(config)

    def __call__(
        self,
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        del environment
        if _is_cleanup_command(command):
            return self._cleanup(command, timeout_seconds)
        name = _execution_name(command)
        payload = json.dumps(
            {
                "schemaVersion": 1,
                "command": list(command),
                "timeoutSeconds": timeout_seconds,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            status, response = self._request(
                "POST",
                "/v1/executions",
                payload,
                timeout_seconds + self._config.request_grace_seconds,
            )
        except TimeoutError as exc:
            raise subprocess.TimeoutExpired(command, timeout_seconds) from exc
        if status != 200:
            return ContainerCommandResult(return_code=125)
        body = _json_mapping(response)
        if body.get("name") != name:
            return ContainerCommandResult(return_code=125)
        exit_code = body.get("exitCode")
        evidence = body.get("runtimeEvidence")
        if not isinstance(exit_code, int) or not isinstance(evidence, Mapping):
            return ContainerCommandResult(return_code=125)
        return ContainerCommandResult(
            return_code=exit_code,
            runtime_evidence=cast(Mapping[str, object], evidence),
        )

    def _cleanup(self, command: Sequence[str], timeout_seconds: float) -> ContainerCommandResult:
        name = command[3]
        try:
            status, _ = self._request(
                "DELETE",
                f"/v1/executions/{quote(name, safe='')}",
                None,
                timeout_seconds,
            )
        except TimeoutError:
            return ContainerCommandResult(return_code=1)
        return ContainerCommandResult(return_code=0 if status in {200, 404} else 1)

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        connection = HTTPConnection(self._hostname, self._endpoint.port, timeout=timeout_seconds)
        try:
            connection.request(
                method,
                f"{self._endpoint.path.rstrip('/')}{path}",
                body=body,
                headers={
                    "accept": "application/json",
                    "authorization": f"Bearer {self._config.bearer_token}",
                    "content-type": "application/json",
                    "user-agent": "Foundry-lite/kubernetes-job-code-execution",
                },
            )
            response = connection.getresponse()
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeError("kubernetes execution broker response is too large")
            return response.status, payload
        except TimeoutError:
            raise
        except (HTTPException, OSError) as exc:
            raise RuntimeError("kubernetes execution broker is unavailable") from exc
        finally:
            connection.close()


class KubernetesJobCodeExecutionAdapter(ContainerCodeExecutionAdapter):
    """Reuse the proven workspace/runner contract while replacing Docker with Kubernetes Jobs."""

    profile_name = "kubernetes-job-code-execution"

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        command_runner: KubernetesJobBrokerCommandRunner | None = None,
    ) -> None:
        source = os.environ if environ is None else environ
        broker_config = kubernetes_job_broker_config_from_env(source)
        runner = command_runner or KubernetesJobBrokerCommandRunner(broker_config)
        container_config = ContainerCodeExecutionConfig.from_env(source)
        container_config = replace(
            container_config,
            runtime_binary="kubernetes-job-client",
            workspace_root=broker_config.shared_workspace_root,
            is_image_digest_required=True,
        )
        super().__init__(
            container_config,
            command_runner=runner,
            environ={},
            is_image_digest_required=True,
        )


def kubernetes_job_broker_config_from_env(source: Mapping[str, str]) -> KubernetesJobBrokerConfig:
    return KubernetesJobBrokerConfig(
        endpoint=source.get(
            "FOUNDRY_LITE_CODE_EXECUTION_BROKER_URL",
            "http://foundry-lite-execution-broker:8080",
        ),
        bearer_token=source.get("FOUNDRY_LITE_CODE_EXECUTION_BROKER_TOKEN", ""),
        shared_workspace_root=Path(
            source.get(
                "FOUNDRY_LITE_CODE_EXECUTION_WORKSPACE_ROOT",
                "/var/data/code-execution-workspaces",
            )
        ),
    )


def _broker_endpoint(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Kubernetes execution broker URL must be a clean internal HTTP URL")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Kubernetes execution broker URL cannot contain a path, query, or fragment")
    return parsed


def _validate_broker_config(config: KubernetesJobBrokerConfig) -> None:
    if not config.bearer_token or len(config.bearer_token) < 32:
        raise ValueError("Kubernetes execution broker requires a bearer token of at least 32 characters")
    if not config.shared_workspace_root.is_absolute():
        raise ValueError("Kubernetes execution shared workspace root must be absolute")
    if config.request_grace_seconds <= 0 or config.request_grace_seconds > 60:
        raise ValueError("Kubernetes execution request grace must be between 0 and 60 seconds")


def _is_cleanup_command(command: Sequence[str]) -> bool:
    return len(command) == 4 and tuple(command[:3]) == ("kubernetes-job-client", "rm", "--force")


def _execution_name(command: Sequence[str]) -> str:
    if len(command) < 4 or tuple(command[:2]) != ("kubernetes-job-client", "run"):
        raise RuntimeError("unsupported Kubernetes execution broker command")
    try:
        index = command.index("--name")
    except ValueError as exc:
        raise RuntimeError("Kubernetes execution command is missing a name") from exc
    if index + 1 >= len(command):
        raise RuntimeError("Kubernetes execution command name is incomplete")
    return command[index + 1]


def _json_mapping(payload: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Kubernetes execution broker returned invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise RuntimeError("Kubernetes execution broker response must be an object")
    return cast(Mapping[str, object], value)
