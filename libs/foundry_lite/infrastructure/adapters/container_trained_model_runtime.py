"""Configuration and fixed-argv command for isolated trained-model images."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from foundry_lite.application.ports.code_execution import CodeExecutionSandboxPolicy
from foundry_lite.application.ports.trained_model_inference import TrainedModelDefinition
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    ContainerCommandRunner,
    client_environment,
    run_command,
)
from foundry_lite.infrastructure.adapters.trained_model_definitions import (
    TRANSACTION_RISK_DEFINITION,
)

MODEL_INPUT_PATH = "/model-input/request.json"
MODEL_OUTPUT_DIR = "/model-output"
MODEL_RESULT_PATH = f"{MODEL_OUTPUT_DIR}/result.json"
MODEL_TMP_DIR = "/model-tmp"
DEFAULT_MODEL_IMAGE = "foundry-lite-trained-model-transaction-risk:2026.07.1"
MODEL_ENVIRONMENT: Mapping[str, str] = {
    "HOME": MODEL_TMP_DIR,
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "TMPDIR": MODEL_TMP_DIR,
}


@dataclass(frozen=True)
class ContainerTrainedModelSpec:
    definition: TrainedModelDefinition
    image_reference: str
    runner_path: str = "/opt/foundry-lite/model/trained_model_runner.py"


def default_model_policy() -> CodeExecutionSandboxPolicy:
    return CodeExecutionSandboxPolicy(
        non_root_uid=65532,
        non_root_gid=65532,
        cpu_count=1.0,
        memory_mb=8192,
        pids_limit=64,
        timeout_seconds=600,
        tmpfs_mb=64,
        is_network_disabled=True,
        is_root_filesystem_read_only=True,
        is_capability_set_dropped=True,
        has_no_new_privileges=True,
        allowed_environment_keys=tuple(MODEL_ENVIRONMENT),
    )


@dataclass(frozen=True)
class ContainerTrainedModelConfig:
    specs: tuple[ContainerTrainedModelSpec, ...] = field(
        default_factory=lambda: (ContainerTrainedModelSpec(TRANSACTION_RISK_DEFINITION, DEFAULT_MODEL_IMAGE),)
    )
    runtime_binary: str = "docker"
    policy: CodeExecutionSandboxPolicy = field(default_factory=default_model_policy)
    workspace_root: Path | None = None
    cleanup_timeout_seconds: float = 10.0
    max_rows: int = 10_000
    max_request_bytes: int = 32 * 1024 * 1024
    max_result_bytes: int = 32 * 1024 * 1024
    is_image_digest_required: bool = False

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        is_image_digest_required: bool = False,
    ) -> ContainerTrainedModelConfig:
        source = os.environ if environ is None else environ
        image = source.get("FOUNDRY_LITE_TRAINED_MODEL_IMAGE", DEFAULT_MODEL_IMAGE)
        return cls(
            specs=(ContainerTrainedModelSpec(TRANSACTION_RISK_DEFINITION, image),),
            runtime_binary=source.get("FOUNDRY_LITE_CONTAINER_RUNTIME", "docker"),
            workspace_root=_optional_path(source, "FOUNDRY_LITE_TRAINED_MODEL_WORKSPACE_ROOT"),
            is_image_digest_required=is_image_digest_required,
        )


def validate_model_config(config: ContainerTrainedModelConfig) -> None:
    _validate_runtime_and_bounds(config)
    _validate_security_policy(config.policy)
    _validate_model_specs(config.specs)
    _validate_digest_pins(config)


def _validate_runtime_and_bounds(config: ContainerTrainedModelConfig) -> None:
    if not config.runtime_binary.strip() or not config.specs:
        raise ValueError("trained-model sidecar requires a runtime and at least one model spec")
    if config.max_rows <= 0 or config.max_request_bytes <= 0 or config.max_result_bytes <= 0:
        raise ValueError("trained-model sidecar bounds must be positive")


def _validate_security_policy(policy: CodeExecutionSandboxPolicy) -> None:
    if not all(
        (
            policy.is_network_disabled,
            policy.is_root_filesystem_read_only,
            policy.is_capability_set_dropped,
            policy.has_no_new_privileges,
        )
    ):
        raise ValueError("trained-model sidecar security controls cannot be disabled")
    if tuple(policy.allowed_environment_keys) != tuple(MODEL_ENVIRONMENT):
        raise ValueError("trained-model sidecar environment allowlist cannot be expanded")


def _validate_model_specs(specs: tuple[ContainerTrainedModelSpec, ...]) -> None:
    is_invalid = any(not spec.image_reference.strip() or not spec.runner_path.startswith("/") for spec in specs)
    if is_invalid:
        raise ValueError("trained-model sidecar image and runner path are required")


def _validate_digest_pins(config: ContainerTrainedModelConfig) -> None:
    has_unpinned_image = any(not _is_digest_pinned(spec.image_reference) for spec in config.specs)
    if config.is_image_digest_required and has_unpinned_image:
        raise ValueError("protected trained-model images must be pinned by sha256 digest")


def model_container_command(
    config: ContainerTrainedModelConfig,
    spec: ContainerTrainedModelSpec,
    request_path: Path,
    result_path: Path,
    container_name: str,
) -> tuple[str, ...]:
    policy = config.policy
    command = [
        config.runtime_binary,
        "run",
        "--rm",
        "--name",
        container_name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--user={policy.non_root_uid}:{policy.non_root_gid}",
        f"--cpus={policy.cpu_count}",
        f"--memory={policy.memory_mb}m",
        f"--memory-swap={policy.memory_mb}m",
        f"--pids-limit={policy.pids_limit}",
        f"--ulimit=fsize={config.max_result_bytes}:{config.max_result_bytes}",
        f"--tmpfs={MODEL_TMP_DIR}:rw,noexec,nosuid,nodev,size={policy.tmpfs_mb}m",
        "--init",
        "--mount",
        _bind_mount(request_path, MODEL_INPUT_PATH, is_read_only=True),
        "--mount",
        _bind_mount(result_path, MODEL_RESULT_PATH, is_read_only=False),
        spec.image_reference,
        "/usr/bin/env",
        "-i",
    ]
    command.extend(f"{key}={value}" for key, value in MODEL_ENVIRONMENT.items())
    command.extend(("python", spec.runner_path, MODEL_INPUT_PATH, MODEL_RESULT_PATH))
    return tuple(command)


def model_workspace_root(config: ContainerTrainedModelConfig) -> Path:
    if config.workspace_root is not None:
        root = config.workspace_root.expanduser().resolve()
    elif sys.platform == "darwin":
        root = Path.home() / ".foundry-lite" / "trained-model-workspaces"
    else:
        root = Path(tempfile.gettempdir()) / "foundry-lite-trained-model-workspaces"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def model_client_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return client_environment(environ)


def default_model_command_runner() -> ContainerCommandRunner:
    return run_command


def _bind_mount(source: Path, target: str, *, is_read_only: bool) -> str:
    suffix = ",readonly" if is_read_only else ""
    return f"type=bind,source={_docker_source(source)},target={target}{suffix}"


def _docker_source(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    value = str(resolved)
    if sys.platform == "darwin" and value.startswith("/private/var/"):
        return Path(value.removeprefix("/private"))
    return resolved


def _is_digest_pinned(image_reference: str) -> bool:
    marker = "@sha256:"
    if marker not in image_reference:
        return False
    digest = image_reference.rsplit(marker, 1)[1]
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _optional_path(environ: Mapping[str, str], key: str) -> Path | None:
    value = environ.get(key, "").strip()
    return Path(value) if value else None
