"""Container command and configuration primitives for code execution."""

from __future__ import annotations

import math
import os
import signal
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from foundry_lite.application.ports.code_execution import CodeExecutionSandboxPolicy

# subprocess is limited to the fixed-argv container CLI and must not use a shell.

JOB_DIR = "/sandbox-job"
INPUT_DIR = "/sandbox-inputs"
OUTPUT_DIR = "/sandbox-output"
FUNCTION_IPC_DIR = "/sandbox-function-ipc"
RUNTIME_DIR = "/opt/foundry-lite/runner"
SDK_DIR = "/opt/foundry-lite/foundry_lite/transforms_sdk"
TMP_DIR = "/sandbox-tmp"
RESULT_NAME = "execution-result.json"
OUTPUT_NAME = "result.parquet"
DEFAULT_IMAGE = "foundry-lite-python-transform:py312-v1"
DEFAULT_NODE_IMAGE = "foundry-lite-node-function:node22-v1"
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
NODE_SANDBOX_ENVIRONMENT: Mapping[str, str] = {
    **SANDBOX_ENVIRONMENT,
    # Resolves `typescript` from the global install in the Node image. Keep this runtime-only so
    # the Python sandbox does not receive environment capabilities it cannot use.
    "NODE_PATH": "/usr/local/lib/node_modules",
}
_DOCKER_CLIENT_ENV_KEYS = ("DOCKER_CONTEXT", "DOCKER_HOST", "HOME", "PATH")
_COMMAND_POLL_SECONDS = 0.1
_CONTAINER_PROBE_INTERVAL_SECONDS = 0.5
_CONTAINER_PROBE_TIMEOUT_SECONDS = 2.0
_EXIT_EVENT_GRACE_SECONDS = 2.0
_STDERR_READER_GRACE_SECONDS = 1.0
_RESULT_MOUNT_TARGETS = {
    "/model-output/result.json",
    "/sandbox-output/execution-result.json",
}


@dataclass(frozen=True)
class ContainerCommandResult:
    return_code: int
    stderr: bytes = b""
    is_exit_event_recovered: bool = False
    runtime_evidence: Mapping[str, object] | None = None


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
        allowed_environment_keys=tuple(NODE_SANDBOX_ENVIRONMENT),
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
    node_image_reference: str = DEFAULT_NODE_IMAGE

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ContainerCodeExecutionConfig:
        source = os.environ if environ is None else environ
        return cls(
            image_reference=source.get("FOUNDRY_LITE_CODE_EXECUTION_IMAGE", DEFAULT_IMAGE),
            node_image_reference=source.get("FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE", DEFAULT_NODE_IMAGE),
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
    writable_mounts: tuple[tuple[Path, str], ...] = ()
    query_nonce: str = ""


def validate_config(config: ContainerCodeExecutionConfig) -> None:
    _validate_runtime_coordinates(config)
    _validate_resource_limits(config)
    _validate_protected_image(config)
    _validate_security_controls(config.policy)


def _validate_runtime_coordinates(config: ContainerCodeExecutionConfig) -> None:
    if not config.image_reference.strip() or not config.runtime_binary.strip():
        raise ValueError("container code execution requires a runtime binary and image reference")
    if config.max_output_bytes <= 0 or config.max_result_bytes <= 0:
        raise ValueError("container code execution output bounds must be positive")


def _validate_resource_limits(config: ContainerCodeExecutionConfig) -> None:
    if not math.isfinite(config.cleanup_timeout_seconds) or config.cleanup_timeout_seconds <= 0:
        raise ValueError("container code execution cleanup timeout must be finite and positive")
    if not math.isfinite(config.policy.cpu_count) or config.policy.cpu_count <= 0:
        raise ValueError("container code execution CPU count must be finite and positive")


def _validate_protected_image(config: ContainerCodeExecutionConfig) -> None:
    if not config.is_image_digest_required:
        return
    if not _is_digest_pinned_image(config.image_reference):
        raise ValueError("protected code execution requires a Python image reference pinned by sha256 digest")
    if not _is_digest_pinned_image(config.node_image_reference):
        raise ValueError("protected code execution requires a Node image reference pinned by sha256 digest")


def _validate_security_controls(policy: CodeExecutionSandboxPolicy) -> None:
    required_controls = (
        policy.is_network_disabled,
        policy.is_root_filesystem_read_only,
        policy.is_capability_set_dropped,
        policy.has_no_new_privileges,
    )
    if not all(required_controls):
        raise ValueError("container code execution security controls cannot be disabled")
    if tuple(policy.allowed_environment_keys) != tuple(NODE_SANDBOX_ENVIRONMENT):
        raise ValueError("container code execution environment allowlist cannot be expanded")


def container_command(
    config: ContainerCodeExecutionConfig,
    workspace: SandboxWorkspace,
    container_name: str,
    runner_script: str = "python_transform_runner.py",
    *,
    interpreter: str = "python",
    image_reference: str | None = None,
) -> tuple[str, ...]:
    policy = config.policy
    command = [
        config.runtime_binary,
        "run",
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
    command.extend((image_reference or config.image_reference, "/usr/bin/env", "-i"))
    environment = NODE_SANDBOX_ENVIRONMENT if interpreter == "node" else SANDBOX_ENVIRONMENT
    command.extend(f"{key}={value}" for key, value in environment.items())
    command.extend(
        (
            interpreter,
            f"{RUNTIME_DIR}/{runner_script}",
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
        start_new_session=os.name == "posix",
    )
    captured = bytearray()
    reader = threading.Thread(
        target=_drain_stderr,
        args=(process, captured),
        daemon=True,
    )
    reader.start()
    try:
        return_code, was_recovered = _wait_for_command(process, command, timeout_seconds, environment)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        # The child is reaped and its pipe is closed, so this bounded reader now
        # has a deterministic EOF. Waiting for it avoids losing the final stderr
        # bytes merely because a heavily loaded host scheduled the reader late.
        _finish_stderr_reader(process, reader)
        raise subprocess.TimeoutExpired(
            command,
            timeout_seconds,
            stderr=bytes(captured),
        ) from exc
    if was_recovered:
        _kill_process_tree(process)
        _finish_stderr_reader(process, reader)
    else:
        _join_reader_or_kill_descendants(process, reader)
    return ContainerCommandResult(
        return_code=return_code,
        stderr=bytes(captured),
        is_exit_event_recovered=was_recovered,
    )


def _wait_for_command(
    process: subprocess.Popen[bytes],
    command: Sequence[str],
    timeout_seconds: float,
    environment: Mapping[str, str],
) -> tuple[int, bool]:
    deadline = time.monotonic() + timeout_seconds
    result_path = _result_mount_source(command)
    next_probe_at = 0.0
    result_completed_at: float | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        try:
            return process.wait(timeout=min(_COMMAND_POLL_SECONDS, remaining)), False
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if result_path is None or not _has_completed_result(result_path):
                result_completed_at = None
                continue
            if result_completed_at is None:
                result_completed_at = now
            if now - result_completed_at < _EXIT_EVENT_GRACE_SECONDS or now < next_probe_at:
                continue
            next_probe_at = now + _CONTAINER_PROBE_INTERVAL_SECONDS
            recovered_exit_code = _container_exit_code_if_gone(command, environment)
            if recovered_exit_code is not None:
                return recovered_exit_code, True


def _result_mount_source(command: Sequence[str]) -> Path | None:
    for index, argument in enumerate(command[:-1]):
        if argument != "--mount":
            continue
        parts = command[index + 1].split(",")
        fields = dict(part.split("=", 1) for part in parts if "=" in part)
        if fields.get("target") in _RESULT_MOUNT_TARGETS and "readonly" not in parts:
            source = fields.get("source")
            return Path(source) if source else None
    return None


def _has_completed_result(result_path: Path) -> bool:
    try:
        return result_path.stat().st_size > 0
    except OSError:
        return False


def _container_exit_code_if_gone(command: Sequence[str], environment: Mapping[str, str]) -> int | None:
    identity = _container_identity(command)
    if identity is None:
        return None
    runtime_binary, container_name = identity
    try:
        probe = subprocess.run(  # nosec B603 - fixed runtime argv, no shell.
            [runtime_binary, "exec", container_name, "/usr/bin/env", "true"],
            env=dict(environment),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_CONTAINER_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0 or not _is_explicit_stopped_error(probe.stderr):
        return None
    return _inspect_container_exit_code(runtime_binary, container_name, environment)


def _is_explicit_stopped_error(stderr: bytes) -> bool:
    normalized = stderr.lower()
    return b"is not running" in normalized or b"is stopped" in normalized


def _inspect_container_exit_code(
    runtime_binary: str,
    container_name: str,
    environment: Mapping[str, str],
) -> int | None:
    try:
        result = subprocess.run(  # nosec B603 - fixed runtime argv, no shell.
            [runtime_binary, "inspect", "--format={{.State.ExitCode}}", container_name],
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_CONTAINER_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    try:
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except ValueError:
        return None


def _container_identity(command: Sequence[str]) -> tuple[str, str] | None:
    if len(command) < 4 or command[1] != "run":
        return None
    try:
        name_index = command.index("--name")
    except ValueError:
        return None
    if name_index + 1 >= len(command):
        return None
    return command[0], command[name_index + 1]


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the fixed-argv client and descendants that may retain stderr."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if process.poll() is None:
                process.kill()
    elif process.poll() is None:
        process.kill()
    process.wait()


def _join_reader_or_kill_descendants(
    process: subprocess.Popen[bytes],
    reader: threading.Thread,
) -> None:
    reader.join(timeout=_STDERR_READER_GRACE_SECONDS)
    if not reader.is_alive():
        return
    _kill_process_tree(process)
    _finish_stderr_reader(process, reader)


def _finish_stderr_reader(
    process: subprocess.Popen[bytes],
    reader: threading.Thread,
) -> None:
    reader.join(timeout=_STDERR_READER_GRACE_SECONDS)
    if not reader.is_alive():
        return
    stream = process.stderr
    if stream is not None:
        stream.close()
    reader.join(timeout=_STDERR_READER_GRACE_SECONDS)


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
        allowed_environment_keys=tuple(NODE_SANDBOX_ENVIRONMENT),
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
    mounts.extend(_bind_mount(path, target, is_read_only=False) for path, target in workspace.writable_mounts)
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
    try:
        while chunk := stream.read(64 * 1024):
            remaining = MAX_STDERR_CAPTURE_BYTES - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
    except (OSError, ValueError):
        pass
    finally:
        stream.close()


def _positive_int(environ: Mapping[str, str], key: str, default: int) -> int:
    value = int(environ.get(key, str(default)))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _positive_float(environ: Mapping[str, str], key: str, default: float) -> float:
    value = float(environ.get(key, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and positive")
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
