from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.compute_adapter import PythonTransformPlan
from foundry_lite.infrastructure.adapters import container_code_execution as code_execution
from foundry_lite.infrastructure.adapters.container_code_execution import ContainerCodeExecutionAdapter
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    MAX_STDERR_CAPTURE_BYTES,
    NODE_SANDBOX_ENVIRONMENT,
    RUNTIME_DIR,
    SANDBOX_ENVIRONMENT,
    SDK_DIR,
    ContainerCodeExecutionConfig,
    ContainerCommandResult,
    SandboxWorkspace,
    _docker_bind_source,
    container_command,
    default_policy,
    run_command,
)


class _SuccessRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], float, dict[str, str]]] = []

    def __call__(
        self,
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        call = (tuple(command), timeout_seconds, dict(environment))
        self.calls.append(call)
        output_path = _mounted_source(command, "/sandbox-output/result.parquet")
        result_path = _mounted_source(command, "/sandbox-output/execution-result.json")
        pq.write_table(pa.Table.from_pylist([{"ok": True}]), output_path)
        _write_result(result_path, {"schemaVersion": 1, "status": "succeeded", "deadLetters": []})
        return ContainerCommandResult(return_code=0)


def test_code_execution_contract_enforces_sandbox_controls_and_env_allowlist(tmp_path: Path) -> None:
    runner = _SuccessRunner()
    adapter = ContainerCodeExecutionAdapter(
        ContainerCodeExecutionConfig(),
        command_runner=runner,
        environ={
            "DOCKER_HOST": "unix:///safe/docker.sock",
            "HOME": "/safe/home",
            "PATH": "/safe/bin",
            "HOST_SECRET": "must-not-cross-boundary",
        },
    )

    result = adapter.execute_python_transform(_plan(tmp_path))

    command, timeout_seconds, client_env = runner.calls[0]
    assert result.dead_letters == ()
    assert pq.read_table(tmp_path / "target.parquet").to_pylist() == [{"ok": True}]
    assert timeout_seconds == 600
    assert client_env == {
        "DOCKER_HOST": "unix:///safe/docker.sock",
        "HOME": "/safe/home",
        "PATH": "/safe/bin",
    }
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges:true" in command
    assert "--user=65532:65532" in command
    assert "--cpus=1.0" in command
    assert "--memory=512m" in command
    assert "--memory-swap=512m" in command
    assert "--pids-limit=64" in command
    assert "--ulimit=fsize=268435456:268435456" in command
    assert "--tmpfs=/sandbox-tmp:rw,noexec,nosuid,nodev,size=64m" in command
    assert "--pull=never" in command
    assert _sandbox_environment(command) == dict(SANDBOX_ENVIRONMENT)
    assert "must-not-cross-boundary" not in " ".join(command)
    _assert_mount_permissions(command)
    mounts = _mount_values(command)
    assert not any(f"target={RUNTIME_DIR}" in mount or f"target={SDK_DIR}" in mount for mount in mounts)


def test_code_execution_contract_limits_node_path_to_the_node_runtime(tmp_path: Path) -> None:
    workspace = SandboxWorkspace(
        job_dir=tmp_path / "job",
        output_dir=tmp_path / "output",
        result_path=tmp_path / "result.json",
        output_path=tmp_path / "result.parquet",
        input_mounts=(),
    )

    python_command = container_command(ContainerCodeExecutionConfig(), workspace, "python-sandbox")
    node_command = container_command(
        ContainerCodeExecutionConfig(),
        workspace,
        "node-sandbox",
        interpreter="node",
    )

    assert _sandbox_environment(python_command) == dict(SANDBOX_ENVIRONMENT)
    assert "NODE_PATH" not in _sandbox_environment(python_command)
    assert _sandbox_environment(node_command, interpreter="node") == dict(NODE_SANDBOX_ENVIRONMENT)


def test_code_execution_contract_timeout_is_typed_and_force_cleans_container(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def timeout_runner(
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        del environment
        calls.append(tuple(command))
        if len(command) > 1 and command[1] == "rm":
            return ContainerCommandResult(return_code=0)
        raise subprocess.TimeoutExpired(command, timeout_seconds, stderr=b"sensitive-user-stderr")

    adapter = ContainerCodeExecutionAdapter(command_runner=timeout_runner, environ={})

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(_plan(tmp_path))

    evidence = _code_execution_evidence(captured.value)
    assert captured.value.failure.kind == "timeout"
    assert captured.value.failure.is_retryable is True
    assert evidence["failureType"] == "sandbox_timeout"
    assert evidence["stderrSha256"] == hashlib.sha256(b"sensitive-user-stderr").hexdigest()
    assert evidence["stderrByteCount"] == len(b"sensitive-user-stderr")
    assert evidence["cleanup"] == {
        "status": "CONFIRMED",
        "exitCode": 0,
        "stderrSha256": None,
        "stderrByteCount": 0,
        "exceptionType": None,
        "exceptionMessageSha256": None,
    }
    assert "sensitive-user-stderr" not in str(captured.value.details)
    assert any(len(command) > 2 and command[1:3] == ("rm", "--force") for command in calls)


def test_code_execution_contract_timeout_cleanup_failure_is_not_retryable(
    tmp_path: Path,
) -> None:
    private_cleanup_error = "private cleanup runtime detail"

    def timeout_and_cleanup_failure(
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        del environment
        if len(command) > 1 and command[1] == "rm":
            raise RuntimeError(private_cleanup_error)
        raise subprocess.TimeoutExpired(command, timeout_seconds)

    adapter = ContainerCodeExecutionAdapter(command_runner=timeout_and_cleanup_failure, environ={})

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(_plan(tmp_path))

    evidence = _code_execution_evidence(captured.value)
    cleanup = evidence["cleanup"]
    assert captured.value.failure.kind == "timeout"
    assert captured.value.failure.is_retryable is False
    assert isinstance(cleanup, dict)
    assert cleanup["status"] == "FAILED"
    assert cleanup["exceptionType"] == "RuntimeError"
    assert cleanup["exceptionMessageSha256"] == hashlib.sha256(private_cleanup_error.encode()).hexdigest()
    assert private_cleanup_error not in str(captured.value.details)


def test_code_execution_contract_resource_exit_is_typed_without_raw_stderr(tmp_path: Path) -> None:
    def resource_runner(
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        del command, timeout_seconds, environment
        return ContainerCommandResult(return_code=137, stderr=b"private row value")

    adapter = ContainerCodeExecutionAdapter(command_runner=resource_runner, environ={})

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(_plan(tmp_path))

    evidence = _code_execution_evidence(captured.value)
    assert captured.value.failure.kind == "unavailable"
    assert captured.value.failure.is_retryable is False
    assert evidence["failureType"] == "resource_limit"
    assert evidence["signalName"] == "SIGKILL"
    assert evidence["stderrSha256"] == hashlib.sha256(b"private row value").hexdigest()
    assert "private row value" not in str(captured.value.details)


def test_code_execution_contract_rejects_output_above_host_bound(tmp_path: Path) -> None:
    def oversized_runner(
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        del timeout_seconds, environment
        output_path = _mounted_source(command, "/sandbox-output/result.parquet")
        result_path = _mounted_source(command, "/sandbox-output/execution-result.json")
        output_path.write_bytes(b"x" * 17)
        _write_result(result_path, {"schemaVersion": 1, "status": "succeeded", "deadLetters": []})
        return ContainerCommandResult(return_code=0)

    adapter = ContainerCodeExecutionAdapter(
        ContainerCodeExecutionConfig(max_output_bytes=16),
        command_runner=oversized_runner,
        environ={},
    )

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(_plan(tmp_path))

    evidence = _code_execution_evidence(captured.value)
    assert captured.value.failure.kind == "validation"
    assert evidence["failureType"] == "output_validation_error"
    assert not (tmp_path / "target.parquet").exists()


def test_code_execution_runtime_discards_stderr_beyond_capture_bound() -> None:
    result = run_command(
        (
            sys.executable,
            "-c",
            f"import sys; sys.stderr.buffer.write(b'x' * {MAX_STDERR_CAPTURE_BYTES + 1024})",
        ),
        5,
        os.environ,
    )

    assert result.return_code == 0
    assert result.stderr == b"x" * MAX_STDERR_CAPTURE_BYTES


def test_code_execution_contract_runner_failure_preserves_safe_typed_evidence(tmp_path: Path) -> None:
    def failure_runner(
        command: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        del timeout_seconds, environment
        result_path = _mounted_source(command, "/sandbox-output/execution-result.json")
        _write_result(
            result_path,
            {
                "schemaVersion": 1,
                "status": "failed",
                "failure": {
                    "type": "entrypoint_load_failed",
                    "exceptionType": "SyntaxError",
                    "messageSha256": "a" * 64,
                },
            },
        )
        return ContainerCommandResult(return_code=2)

    adapter = ContainerCodeExecutionAdapter(command_runner=failure_runner, environ={})

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(_plan(tmp_path))

    evidence = _code_execution_evidence(captured.value)
    assert captured.value.failure.kind == "validation"
    assert evidence["failureType"] == "user_code_error"
    assert evidence["runnerFailureType"] == "entrypoint_load_failed"
    assert evidence["exceptionType"] == "SyntaxError"
    assert evidence["exceptionMessageSha256"] == "a" * 64


def test_code_execution_contract_rejects_weakened_policy() -> None:
    disabled_network = replace(default_policy(), is_network_disabled=False)
    expanded_env = replace(
        default_policy(),
        allowed_environment_keys=(*default_policy().allowed_environment_keys, "HOST_SECRET"),
    )

    with pytest.raises(ValueError, match="security controls cannot be disabled"):
        ContainerCodeExecutionAdapter(ContainerCodeExecutionConfig(policy=disabled_network))
    with pytest.raises(ValueError, match="allowlist cannot be expanded"):
        ContainerCodeExecutionAdapter(ContainerCodeExecutionConfig(policy=expanded_env))


def test_code_execution_contract_requires_digest_pinned_image_for_protected_runtime() -> None:
    with pytest.raises(ValueError, match="pinned by sha256 digest"):
        ContainerCodeExecutionAdapter(
            ContainerCodeExecutionConfig(
                image_reference="registry.example/foundry-python:latest",
                is_image_digest_required=True,
            )
        )

    digest = "a" * 64
    adapter = ContainerCodeExecutionAdapter(
        ContainerCodeExecutionConfig(
            image_reference=f"registry.example/foundry-python@sha256:{digest}",
            is_image_digest_required=True,
        )
    )

    assert adapter.config.image_reference.endswith(digest)


def test_code_execution_contract_protected_env_enables_digest_guard() -> None:
    with pytest.raises(ValueError, match="pinned by sha256 digest"):
        ContainerCodeExecutionAdapter(
            environ={
                "FOUNDRY_LITE_RUNTIME_PROFILE": "production",
                "FOUNDRY_LITE_CODE_EXECUTION_IMAGE": "registry.example/foundry-python:mutable",
            }
        )


def test_code_execution_contract_failure_taxonomy_is_operator_safe() -> None:
    contract = ContainerCodeExecutionAdapter(environ={}).failure_contract()

    assert contract.adapter_profile == "container-code-execution"
    assert {mode.kind for mode in contract.modes} == {"unavailable", "timeout", "validation", "unknown"}
    assert all(mode.operator_message and "stderr" not in mode.operator_message.lower() for mode in contract.modes)


def test_code_execution_contract_normalizes_colima_macos_private_var_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "foundry_lite.infrastructure.adapters.container_code_execution_runtime.sys.platform",
        "darwin",
    )

    normalized = _docker_bind_source(Path("/private/var/folders/sandbox/job"))

    assert normalized == Path("/var/folders/sandbox/job")


def test_code_execution_contract_reads_explicit_workspace_root() -> None:
    config = ContainerCodeExecutionConfig.from_env(
        {"FOUNDRY_LITE_CODE_EXECUTION_WORKSPACE_ROOT": "/shared/code-execution"}
    )

    assert config.workspace_root == Path("/shared/code-execution")


def test_code_execution_contract_maps_missing_runtime_binary_to_unavailable(
    tmp_path: Path,
) -> None:
    def missing_runner(
        _command: Sequence[str],
        _timeout_seconds: float,
        _environment: Mapping[str, str],
    ) -> ContainerCommandResult:
        raise FileNotFoundError("docker")

    adapter = ContainerCodeExecutionAdapter(command_runner=missing_runner, environ={})

    with pytest.raises(AdapterError) as raised:
        adapter.execute_python_transform(_plan(tmp_path))

    assert raised.value.failure.kind == "unavailable"
    assert _code_execution_evidence(raised.value)["failureType"] == "runtime_unavailable"


def test_code_execution_workspace_and_input_staging_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_execution.sys, "platform", "linux")
    monkeypatch.setattr(code_execution.tempfile, "gettempdir", lambda: str(tmp_path))
    assert code_execution._workspace_root(ContainerCodeExecutionConfig()) == (
        tmp_path / "foundry-lite-code-execution-workspaces"
    )

    input_path = tmp_path / "source.parquet"
    input_path.write_bytes(b"parquet")
    paths, mounts = code_execution._container_input_paths(
        {"raw.a": input_path, "raw.b": (input_path,)},
        tmp_path / "inputs",
    )
    assert paths["raw.a"] == paths["raw.b"]
    assert len(mounts) == 1


@pytest.mark.parametrize("return_code", [125, 126, 127])
def test_code_execution_runner_missing_result_maps_runtime_exit_codes(
    tmp_path: Path,
    return_code: int,
) -> None:
    adapter = ContainerCodeExecutionAdapter(environ={})
    result_path = tmp_path / "result.json"
    result_path.touch()

    with pytest.raises(AdapterError) as raised:
        code_execution._read_runner_result(
            result_path,
            ContainerCommandResult(return_code=return_code),
            adapter,
        )

    assert raised.value.failure.kind == "unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"[]",
        b'{"schemaVersion":2}',
    ],
)
def test_code_execution_runner_result_parser_rejects_missing_or_malformed_evidence(
    tmp_path: Path,
    payload: bytes,
) -> None:
    adapter = ContainerCodeExecutionAdapter(environ={})
    result_path = tmp_path / "result.json"
    result_path.write_bytes(payload)

    with pytest.raises(AdapterError):
        code_execution._read_runner_result(
            result_path,
            ContainerCommandResult(return_code=0),
            adapter,
        )


def test_code_execution_runner_result_parser_enforces_size_bound(tmp_path: Path) -> None:
    adapter = ContainerCodeExecutionAdapter(
        ContainerCodeExecutionConfig(max_result_bytes=2),
        environ={},
    )
    result_path = tmp_path / "result.json"
    result_path.write_bytes(b"{}x")

    with pytest.raises(AdapterError) as raised:
        code_execution._read_runner_result(
            result_path,
            ContainerCommandResult(return_code=0),
            adapter,
        )
    assert raised.value.failure.kind == "validation"


@pytest.mark.parametrize(
    "payload",
    [
        {"schemaVersion": 1, "status": "unknown"},
        {"schemaVersion": 1, "status": "succeeded", "deadLetters": "bad"},
        {"schemaVersion": 1, "status": "succeeded", "deadLetters": ["bad"]},
        {
            "schemaVersion": 1,
            "status": "succeeded",
            "deadLetters": [{"payload": {}, "rowIndex": "bad"}],
        },
    ],
)
def test_code_execution_success_contract_rejects_invalid_status_and_dead_letters(
    payload: dict[str, object],
) -> None:
    adapter = ContainerCodeExecutionAdapter(environ={})
    with pytest.raises(AdapterError):
        code_execution._require_success_result(
            payload,
            ContainerCommandResult(return_code=0),
            adapter,
        )


def test_code_execution_output_copy_rejects_missing_and_host_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ContainerCodeExecutionAdapter(environ={})
    with pytest.raises(AdapterError):
        code_execution._copy_validated_output(
            tmp_path / "missing.parquet",
            tmp_path / "target.parquet",
            adapter,
        )

    source = tmp_path / "source.parquet"
    source.write_bytes(b"valid")
    monkeypatch.setattr(
        code_execution.shutil,
        "copyfile",
        lambda *_args: (_ for _ in ()).throw(OSError("disk")),
    )
    with pytest.raises(AdapterError):
        code_execution._copy_validated_output(source, tmp_path / "target.parquet", adapter)
    assert not (tmp_path / "target.parquet").exists()


def test_code_execution_failure_classification_and_signal_helpers_are_complete() -> None:
    assert code_execution._classify_runner_failure({"type": "invalid_return_rows"}) == (
        "output_validation_error",
        "validation",
    )
    assert code_execution._classify_runner_failure({"type": "unexpected"}) == (
        "runner_contract_error",
        "unknown",
    )
    assert code_execution._signal_name(143) == "SIGTERM"
    assert code_execution._signal_name(0) is None
    assert code_execution._timeout_stderr(subprocess.TimeoutExpired(["docker"], 1, stderr="text")) == b"text"
    assert code_execution._timeout_stderr(subprocess.TimeoutExpired(["docker"], 1)) == b""


def _plan(tmp_path: Path) -> PythonTransformPlan:
    return PythonTransformPlan(
        entrypoint=str(tmp_path / "transform.py"),
        source_code="def compute():\n    return [{'ok': True}]\n",
        function_name="compute",
        input_refs_by_alias={},
        input_paths_by_ref={},
        output_dataset_ref="clean.output",
        target_path=tmp_path / "target.parquet",
    )


def _mounted_source(command: Sequence[str], target: str) -> Path:
    for index, value in enumerate(command):
        if value != "--mount" or index + 1 >= len(command):
            continue
        fields = dict(part.split("=", 1) for part in command[index + 1].split(",") if "=" in part)
        if fields.get("target") == target:
            return Path(fields["source"])
    raise AssertionError(f"mount target not found: {target}")


def _sandbox_environment(command: Sequence[str], *, interpreter: str = "python") -> dict[str, str]:
    start = command.index("-i") + 1
    end = command.index(interpreter, start)
    return dict(value.split("=", 1) for value in command[start:end])


def _assert_mount_permissions(command: Sequence[str]) -> None:
    mounts = _mount_values(command)
    writable_targets = {
        "/sandbox-output/result.parquet",
        "/sandbox-output/execution-result.json",
    }
    output_mounts = [mount for mount in mounts if any(f"target={target}" in mount for target in writable_targets)]
    read_only_mounts = [mount for mount in mounts if mount not in output_mounts]
    assert len(output_mounts) == 2
    assert all("readonly" not in mount for mount in output_mounts)
    assert not any("target=/sandbox-output," in mount for mount in mounts)
    assert read_only_mounts
    assert all(mount.endswith(",readonly") for mount in read_only_mounts)


def _mount_values(command: Sequence[str]) -> list[str]:
    return [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--mount"]


def _write_result(result_path: Path, payload: Mapping[str, object]) -> None:
    result_path.write_text(json.dumps(payload), encoding="utf-8")


def _code_execution_evidence(exc: AdapterError) -> Mapping[str, object]:
    value = exc.failure.details["codeExecution"]
    assert isinstance(value, Mapping)
    return value
