"""Container command and configuration primitives for code execution."""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from foundry_lite.application.ports.code_execution import CodeExecutionSandboxPolicy

# subprocess is limited to the fixed-argv container CLI and must not use a shell.

JOB_DIR = "/sandbox-job"
INPUT_DIR = "/sandbox-inputs"
OUTPUT_DIR = "/sandbox-output"
RUNTIME_DIR = "/opt/foundry-lite/runner"
SDK_DIR = "/opt/foundry-lite/foundry_lite/transforms_sdk"
TMP_DIR = "/sandbox-tmp"
RESULT_NAME = "execution-result.json"
OUTPUT_NAME = "result.parquet"
DEFAULT_IMAGE = "foundry-lite-python-transform:py312-v1"
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_RESULT_BYTES = 1024 * 1024
MAX_STDERR_CAPTURE_BYTES = 64 * 1024
SANDBOX_ENVIRONMENT: Mapping[str, str] = {
    "HOME": TMP_DIR,
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPATH": "/opt/foundry-lite",
    "PYTHONUNBUFFERED": "1",
    "TMPDIR": TMP_DIR,
}
_DOCKER_CLIENT_ENV_KEYS = ("DOCKER_CONTEXT", "DOCKER_HOST", "HOME", "PATH")


@dataclass(frozen=True)
class ContainerCommandResult:
    return_code: int
    stderr: bytes = b""


ContainerCommandRunner = Callable[[Sequence[str], float, Mapping[str, str]], ContainerCommandResult]


def default_policy() -> CodeExecutionSandboxPolicy:
    return CodeExecutionSandboxPolicy(
        non_root_uid=65532,
        non_root_gid=65532,
        cpu_count=1.0,
        memory_mb=512,
        pids_limit=64,
        timeout_seconds=600,
        tmpfs_mb=64,
        is_network_disabled=True,
        is_root_filesystem_read_only=True,
        is_capability_set_dropped=True,
        has_no_new_privileges=True,
        allowed_environment_keys=tuple(SANDBOX_ENVIRONMENT),
    )


@dataclass(frozen=True)
class ContainerCodeExecutionConfig:
    image_reference: str = DEFAULT_IMAGE
    runtime_binary: str = "docker"
    policy: CodeExecutionSandboxPolicy = field(default_factory=default_policy)
    cleanup_timeout_seconds: float = 10.0
    workspace_root: Path | None = None
    is_image_digest_required: bool = False
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ContainerCodeExecutionConfig:
        source = os.environ if environ is None else environ
        return cls(
            image_reference=source.get("FOUNDRY_LITE_CODE_EXECUTION_IMAGE", DEFAULT_IMAGE),
            runtime_binary=source.get("FOUNDRY_LITE_CONTAINER_RUNTIME", "docker"),
            policy=_policy_from_env(source),
            workspace_root=_optional_path(source, "FOUNDRY_LITE_CODE_EXECUTION_WORKSPACE_ROOT"),
            is_image_digest_required=_is_protected_runtime_profile(source),
            max_output_bytes=_positive_int(
                source,
                "FOUNDRY_LITE_CODE_EXECUTION_MAX_OUTPUT_BYTES",
                DEFAULT_MAX_OUTPUT_BYTES,
            ),
            max_result_bytes=_positive_int(
                source,
                "FOUNDRY_LITE_CODE_EXECUTION_MAX_RESULT_BYTES",
                DEFAULT_MAX_RESULT_BYTES,
            ),
        )


@dataclass(frozen=True)
class SandboxWorkspace:
    job_dir: Path
    output_dir: Path
    result_path: Path
    output_path: Path
    input_mounts: tuple[tuple[Path, str], ...]


def validate_config(config: ContainerCodeExecutionConfig) -> None:
    policy = config.policy
    if not config.image_reference.strip() or not config.runtime_binary.strip():
        raise ValueError("container code execution requires a runtime binary and image reference")
    if config.max_output_bytes <= 0 or config.max_result_bytes <= 0:
        raise ValueError("container code execution output bounds must be positive")
    if config.is_image_digest_required and not _is_digest_pinned_image(config.image_reference):
        raise ValueError("protected code execution requires an image reference pinned by sha256 digest")
    required_controls = (
        policy.is_network_disabled,
        policy.is_root_filesystem_read_only,
        policy.is_capability_set_dropped,
        policy.has_no_new_privileges,
    )
    if not all(required_controls):
        raise ValueError("container code execution security controls cannot be disabled")
    if tuple(policy.allowed_environment_keys) != tuple(SANDBOX_ENVIRONMENT):
        raise ValueError("container code execution environment allowlist cannot be expanded")


def container_command(
    config: ContainerCodeExecutionConfig,
    workspace: SandboxWorkspace,
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
        f"--ulimit=fsize={max(config.max_output_bytes, config.max_result_bytes)}:"
        f"{max(config.max_output_bytes, config.max_result_bytes)}",
        f"--tmpfs={TMP_DIR}:rw,noexec,nosuid,nodev,size={policy.tmpfs_mb}m",
        "--workdir=/sandbox-job",
        "--init",
    ]
    command.extend(_workspace_mount_arguments(workspace))
    command.extend((config.image_reference, "/usr/bin/env", "-i"))
    command.extend(f"{key}={value}" for key, value in SANDBOX_ENVIRONMENT.items())
    command.extend(
        (
            "python",
            f"{RUNTIME_DIR}/python_transform_runner.py",
            f"{JOB_DIR}/request.json",
            f"{OUTPUT_DIR}/{RESULT_NAME}",
        )
    )
    return tuple(command)


def client_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {key: environ[key] for key in _DOCKER_CLIENT_ENV_KEYS if key in environ}


def run_command(
    command: Sequence[str],
    timeout_seconds: float,
    environment: Mapping[str, str],
) -> ContainerCommandResult:
    # Fixed argv, no shell; stderr is drained but only a bounded prefix is retained.
    process = subprocess.Popen(  # nosec B603
        list(command),
        env=dict(environment),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    captured = bytearray()
    reader = threading.Thread(
        target=_drain_stderr,
        args=(process, captured),
        daemon=True,
    )
    reader.start()
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        reader.join(timeout=1)
        raise subprocess.TimeoutExpired(
            command,
            timeout_seconds,
            stderr=bytes(captured),
        ) from exc
    reader.join(timeout=1)
    return ContainerCommandResult(return_code=return_code, stderr=bytes(captured))


def _policy_from_env(environ: Mapping[str, str]) -> CodeExecutionSandboxPolicy:
    return CodeExecutionSandboxPolicy(
        non_root_uid=_positive_int(environ, "FOUNDRY_LITE_CODE_EXECUTION_UID", 65532),
        non_root_gid=_positive_int(environ, "FOUNDRY_LITE_CODE_EXECUTION_GID", 65532),
        cpu_count=_positive_float(environ, "FOUNDRY_LITE_CODE_EXECUTION_CPUS", 1.0),
        memory_mb=_positive_int(environ, "FOUNDRY_LITE_CODE_EXECUTION_MEMORY_MB", 512),
        pids_limit=_positive_int(environ, "FOUNDRY_LITE_CODE_EXECUTION_PIDS", 64),
        timeout_seconds=_positive_int(environ, "FOUNDRY_LITE_CODE_EXECUTION_TIMEOUT_SECONDS", 600),
        tmpfs_mb=_positive_int(environ, "FOUNDRY_LITE_CODE_EXECUTION_TMPFS_MB", 64),
        is_network_disabled=True,
        is_root_filesystem_read_only=True,
        is_capability_set_dropped=True,
        has_no_new_privileges=True,
        allowed_environment_keys=tuple(SANDBOX_ENVIRONMENT),
    )


def _workspace_mount_arguments(workspace: SandboxWorkspace) -> list[str]:
    mounts = [
        _bind_mount(workspace.job_dir, JOB_DIR, is_read_only=True),
        _bind_mount(
            workspace.output_path,
            f"{OUTPUT_DIR}/{OUTPUT_NAME}",
            is_read_only=False,
        ),
        _bind_mount(
            workspace.result_path,
            f"{OUTPUT_DIR}/{RESULT_NAME}",
            is_read_only=False,
        ),
    ]
    mounts.extend(_bind_mount(path, target, is_read_only=True) for path, target in workspace.input_mounts)
    return [item for mount in mounts for item in ("--mount", mount)]


def _bind_mount(source: Path, target: str, *, is_read_only: bool) -> str:
    suffix = ",readonly" if is_read_only else ""
    return f"type=bind,source={_docker_bind_source(source)},target={target}{suffix}"


def _docker_bind_source(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    value = str(resolved)
    if sys.platform == "darwin" and value.startswith("/private/var/"):
        return Path(value.removeprefix("/private"))
    return resolved


def _drain_stderr(
    process: subprocess.Popen[bytes],
    captured: bytearray,
) -> None:
    stream = process.stderr
    if stream is None:
        return
    while chunk := stream.read(64 * 1024):
        remaining = MAX_STDERR_CAPTURE_BYTES - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])


def _positive_int(environ: Mapping[str, str], key: str, default: int) -> int:
    value = int(environ.get(key, str(default)))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _positive_float(environ: Mapping[str, str], key: str, default: float) -> float:
    value = float(environ.get(key, str(default)))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _optional_path(environ: Mapping[str, str], key: str) -> Path | None:
    value = environ.get(key)
    return Path(value).expanduser() if value else None


def _is_protected_runtime_profile(environ: Mapping[str, str]) -> bool:
    profile = environ.get("FOUNDRY_LITE_RUNTIME_PROFILE", "local").strip().lower()
    return profile in {"production", "prod", "staging", "stage"}


def _is_digest_pinned_image(image_reference: str) -> bool:
    name, separator, digest = image_reference.strip().rpartition("@sha256:")
    is_digest_length_valid = len(digest) == 64
    is_digest_hex = all(character in "0123456789abcdef" for character in digest)
    return bool(separator and name and is_digest_length_valid and is_digest_hex)
