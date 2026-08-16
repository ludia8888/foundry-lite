"""Container-backed isolation adapter for untrusted Python transforms."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast
from uuid import uuid4

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.code_execution import (
    CodeExecutionFailureEvidence,
    CodeExecutionFailureType,
    FunctionExecutionPlan,
    FunctionExecutionResult,
    FunctionQueryExecutor,
)
from foundry_lite.application.ports.compute_adapter import (
    InputFilePaths,
    PythonTransformPlan,
    TransformDeadLetterRecord,
    TransformExecutionResult,
)
from foundry_lite.infrastructure.adapters.container_cleanup import (
    ContainerCleanupEvidence,
    force_remove_container,
)
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    FUNCTION_IPC_DIR,
    INPUT_DIR,
    JOB_DIR,
    OUTPUT_DIR,
    OUTPUT_NAME,
    RESULT_NAME,
    ContainerCodeExecutionConfig,
    ContainerCommandResult,
    ContainerCommandRunner,
    SandboxWorkspace,
    client_environment,
    container_command,
    run_command,
    validate_config,
)
from foundry_lite.infrastructure.adapters.function_query_bridge import FunctionQueryBridge

# subprocess is limited to typed timeout handling for the fixed-argv runtime boundary.


class ContainerCodeExecutionAdapter:
    """Runs Python transforms in a fail-closed, non-root container."""

    profile_name = "container-code-execution"

    def __init__(
        self,
        config: ContainerCodeExecutionConfig | None = None,
        *,
        command_runner: ContainerCommandRunner | None = None,
        environ: Mapping[str, str] | None = None,
        is_image_digest_required: bool | None = None,
    ) -> None:
        resolved_config = config or ContainerCodeExecutionConfig.from_env(environ)
        if is_image_digest_required is not None:
            resolved_config = replace(
                resolved_config,
                is_image_digest_required=is_image_digest_required,
            )
        self.config = resolved_config
        validate_config(self.config)
        self._command_runner = command_runner or run_command
        self._client_environment = client_environment(os.environ if environ is None else environ)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "execute_python_transform",
                    "unavailable",
                    False,
                    "The isolated code-execution runtime or pinned runner image is unavailable.",
                ),
                AdapterFailureMode(
                    "execute_python_transform",
                    "timeout",
                    True,
                    "The isolated Python transform exceeded its execution timeout.",
                    timeout_seconds=self.config.policy.timeout_seconds,
                ),
                AdapterFailureMode(
                    "execute_python_transform",
                    "validation",
                    False,
                    "The isolated Python transform or its output did not satisfy the runner contract.",
                ),
                AdapterFailureMode(
                    "execute_python_transform",
                    "unknown",
                    False,
                    "The isolated runner failed without a valid typed result.",
                ),
            ),
        )

    def execute_function(
        self,
        plan: FunctionExecutionPlan,
        *,
        query_executor: FunctionQueryExecutor | None = None,
    ) -> FunctionExecutionResult:
        """Run one ontology function under the same sandbox policy as a transform.

        The policy is shared deliberately. A function is user code from the same authors under
        the same threat model, so it gets the same non-root, no-network, read-only-root,
        capability-dropped container; only the payload shape differs, JSON rather than parquet.
        """
        started = time.monotonic()
        with tempfile.TemporaryDirectory(
            prefix="foundry-lite-function-sandbox-",
            dir=_workspace_root(self.config),
        ) as temporary:
            workspace = _prepare_function_workspace(plan, Path(temporary), self)
            container_name = f"foundry-lite-function-{uuid4().hex}"
            runner = _FUNCTION_RUNNERS.get(plan.runtime)
            if runner is None:
                raise self._error("runner_contract_error", "validation", False)
            command = container_command(
                self.config,
                workspace,
                container_name,
                runner.script,
                interpreter=runner.interpreter,
                image_reference=runner.image(self.config),
            )
            bridge = FunctionQueryBridge(workspace.writable_mounts[0][0], workspace.query_nonce, query_executor)
            with bridge:
                result = self._execute_container(command, container_name, plan.timeout_seconds)
            bridge.raise_if_failed()
            payload = _read_runner_result(workspace.result_path, result, self)
            output = _require_function_success(payload, result, self)
            return FunctionExecutionResult(
                output=output,
                stderr_byte_count=len(result.stderr),
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_evidence=result.runtime_evidence,
            )

    def execute_python_transform(self, plan: PythonTransformPlan) -> TransformExecutionResult:
        workspace_root = _workspace_root(self.config)
        with tempfile.TemporaryDirectory(
            prefix="foundry-lite-python-sandbox-",
            dir=workspace_root,
        ) as temporary:
            workspace = _prepare_workspace(plan, Path(temporary))
            container_name = f"foundry-lite-python-{uuid4().hex}"
            command = container_command(self.config, workspace, container_name)
            result = self._execute_container(command, container_name)
            runner_result = _read_runner_result(
                workspace.result_path,
                result,
                self,
            )
            dead_letters = _require_success_result(runner_result, result, self)
            _copy_validated_output(
                workspace.output_path,
                plan.target_path,
                self,
            )
            return TransformExecutionResult(
                dead_letters=dead_letters,
                runtime_evidence=result.runtime_evidence,
            )

    def _execute_container(
        self,
        command: Sequence[str],
        container_name: str,
        timeout_seconds: float | None = None,
    ) -> ContainerCommandResult:
        # A per-call budget cannot exceed the sandbox policy: the policy is the operator's bound
        # on how long any one container may hold a slot, and a function definition is authored
        # content that must not be able to raise it.
        budget = (
            min(timeout_seconds, self.config.policy.timeout_seconds)
            if timeout_seconds
            else (self.config.policy.timeout_seconds)
        )
        try:
            result = self._command_runner(command, budget, self._client_environment)
        except subprocess.TimeoutExpired as exc:
            cleanup = self._force_remove(container_name)
            raise self._error(
                "sandbox_timeout",
                "timeout",
                cleanup.is_confirmed,
                stderr=_timeout_stderr(exc),
                cleanup=cleanup,
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise self._error("runtime_unavailable", "unavailable", False) from exc
        cleanup = self._force_remove(container_name)
        if not cleanup.is_confirmed:
            raise self._error(
                "runtime_unavailable",
                "unavailable",
                False,
                result=result,
                cleanup=cleanup,
            )
        return result

    def _force_remove(self, container_name: str) -> ContainerCleanupEvidence:
        return force_remove_container(
            self._command_runner,
            runtime_binary=self.config.runtime_binary,
            container_name=container_name,
            timeout_seconds=self.config.cleanup_timeout_seconds,
            environment=self._client_environment,
        )

    def _error(
        self,
        failure_type: CodeExecutionFailureType,
        kind: AdapterFailureKind,
        is_retryable: bool,
        *,
        result: ContainerCommandResult | None = None,
        stderr: bytes = b"",
        runner_failure: Mapping[str, object] | None = None,
        cleanup: ContainerCleanupEvidence | None = None,
    ) -> AdapterError:
        captured = result.stderr if result is not None else stderr
        evidence = _failure_evidence(self.config, failure_type, result, captured, runner_failure, cleanup)
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="execute_python_transform",
                kind=kind,
                is_retryable=is_retryable,
                operator_message=_operator_message(failure_type),
                timeout_seconds=self.config.policy.timeout_seconds if kind == "timeout" else None,
                details={"codeExecution": evidence.to_payload()},
            )
        )


def _prepare_workspace(plan: PythonTransformPlan, root: Path) -> SandboxWorkspace:
    job_dir = root / "job"
    input_dir = root / "inputs"
    output_dir = root / "output"
    job_dir.mkdir(mode=0o700)
    output_dir.mkdir(mode=0o700)
    result_path = output_dir / RESULT_NAME
    output_path = output_dir / OUTPUT_NAME
    result_path.touch()
    output_path.touch()
    result_path.chmod(0o666)
    output_path.chmod(0o666)
    source_path = job_dir / "transform.py"
    source_path.write_text(plan.source_code, encoding="utf-8")
    input_paths, input_mounts = _container_input_paths(plan.input_paths_by_ref, input_dir)
    manifest = _manifest(plan, input_paths)
    (job_dir / "request.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    source_path.chmod(0o444)
    (job_dir / "request.json").chmod(0o444)
    job_dir.chmod(0o555)
    return SandboxWorkspace(
        job_dir=job_dir,
        output_dir=output_dir,
        result_path=result_path,
        output_path=output_path,
        input_mounts=input_mounts,
    )


def _workspace_root(config: ContainerCodeExecutionConfig) -> Path:
    configured = config.workspace_root
    if configured is not None:
        root = configured.expanduser().resolve()
    elif sys.platform == "darwin":
        root = Path.home() / ".foundry-lite" / "code-execution-workspaces"
    else:
        root = Path(tempfile.gettempdir()) / "foundry-lite-code-execution-workspaces"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _container_input_paths(
    paths_by_ref: Mapping[str, InputFilePaths],
    input_dir: Path,
) -> tuple[dict[str, list[str]], tuple[tuple[Path, str], ...]]:
    input_dir.mkdir(mode=0o700)
    mounted: dict[Path, tuple[Path, str]] = {}
    container_paths: dict[str, list[str]] = {}
    for dataset_ref, raw_paths in sorted(paths_by_ref.items()):
        resolved_paths = tuple(path.expanduser().resolve(strict=True) for path in _path_tuple(raw_paths))
        for path in resolved_paths:
            if path not in mounted:
                staged_path = input_dir / f"input-{len(mounted):04d}.parquet"
                shutil.copyfile(path, staged_path)
                staged_path.chmod(0o444)
                mounted[path] = (staged_path, f"{INPUT_DIR}/{staged_path.name}")
        container_paths[dataset_ref] = [mounted[path][1] for path in resolved_paths]
    input_dir.chmod(0o555)
    mounts = tuple(staged for staged in mounted.values())
    return container_paths, mounts


@dataclass(frozen=True)
class _FunctionRunner:
    """Which image and entry command serve one function runtime."""

    script: str
    interpreter: str
    source_name: str
    image: Callable[[ContainerCodeExecutionConfig], str]


# Two images, one policy. They differ in what is installed, not in how they are confined: the
# run-time flags -- non-root, no network, read-only root, capabilities dropped -- come from the
# same `container_command` for both.
_FUNCTION_RUNNERS: Mapping[str, _FunctionRunner] = {
    "python": _FunctionRunner(
        "python_function_runner.py", "python", "function.py", lambda config: config.image_reference
    ),
    "typescript": _FunctionRunner(
        "typescript_function_runner.mjs", "node", "function.ts", lambda config: config.node_image_reference
    ),
}


def _prepare_function_workspace(
    plan: FunctionExecutionPlan,
    root: Path,
    adapter: ContainerCodeExecutionAdapter,
) -> SandboxWorkspace:
    """Lay out the same job/output pair a transform gets, with a JSON request instead of mounts.

    A function reads nothing from disk, so there are no input mounts: everything it was given is
    inside request.json, which is why the byte ceiling is checked here rather than in the
    sandbox. Refusing oversized input before the container starts keeps a large object set from
    being paid for twice.
    """
    job_dir = root / "job"
    output_dir = root / "output"
    ipc_dir = root / "ipc"
    job_dir.mkdir(mode=0o700)
    output_dir.mkdir(mode=0o700)
    query_nonce = uuid4().hex
    result_path = output_dir / RESULT_NAME
    result_path.touch()
    result_path.chmod(0o666)

    runner = _FUNCTION_RUNNERS[plan.runtime]
    source_path = job_dir / runner.source_name
    source_path.write_text(plan.source, encoding="utf-8")
    source_path.chmod(0o444)
    manifest = json.dumps(
        {
            "schemaVersion": 1,
            "entrypoint": plan.entrypoint,
            "sourcePath": f"{JOB_DIR}/{runner.source_name}",
            "source": plan.source,
            "sourceSha256": hashlib.sha256(plan.source.encode()).hexdigest(),
            "inputs": dict(plan.inputs_json),
            "argumentOrder": list(plan.argument_order),
            "outputType": plan.output_type,
            "queryBridge": {
                "directory": FUNCTION_IPC_DIR,
                "nonce": query_nonce,
                "timeoutSeconds": min(plan.timeout_seconds, 30),
            },
        },
        sort_keys=True,
    )
    if len(manifest.encode("utf-8")) > plan.input_byte_limit:
        raise adapter._error("resource_limit", "validation", False)
    request_path = job_dir / "request.json"
    request_path.write_text(manifest, encoding="utf-8")
    request_path.chmod(0o444)
    job_dir.chmod(0o555)
    return SandboxWorkspace(
        job_dir=job_dir,
        output_dir=output_dir,
        result_path=result_path,
        output_path=result_path,
        input_mounts=(),
        writable_mounts=((ipc_dir, FUNCTION_IPC_DIR),),
        query_nonce=query_nonce,
    )


def _require_function_success(
    payload: Mapping[str, object],
    command_result: ContainerCommandResult,
    adapter: ContainerCodeExecutionAdapter,
) -> object:
    if payload.get("status") == "succeeded":
        return payload.get("output")
    raise adapter._error(
        _function_failure_type(payload.get("failureType")),
        "validation",
        False,
        result=command_result,
    )


def _function_failure_type(reported: object) -> CodeExecutionFailureType:
    """Only the runner's own vocabulary is trusted; anything else is a contract breach.

    The runner is trusted code, but the file it writes lives in a directory the sandbox can
    write to, so an unrecognised value means the contract was violated rather than that a new
    failure mode exists.
    """
    if reported in {"user_code_error", "output_validation_error", "runner_contract_error"}:
        return cast(CodeExecutionFailureType, reported)
    return "runner_contract_error"


def _manifest(plan: PythonTransformPlan, input_paths: Mapping[str, Sequence[str]]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "entrypointName": Path(plan.entrypoint).name,
        "sourceSha256": hashlib.sha256(plan.source_code.encode()).hexdigest(),
        "sourcePath": f"{JOB_DIR}/transform.py",
        "functionName": plan.function_name,
        "inputRefsByAlias": dict(plan.input_refs_by_alias),
        "inputPathsByRef": {key: list(value) for key, value in input_paths.items()},
        "outputDatasetRef": plan.output_dataset_ref,
        "outputPath": f"{OUTPUT_DIR}/{OUTPUT_NAME}",
    }


def _read_runner_result(
    result_path: Path,
    command_result: ContainerCommandResult,
    adapter: ContainerCodeExecutionAdapter,
) -> Mapping[str, object]:
    if command_result.return_code in {125, 126, 127, 137}:
        _raise_for_missing_result(command_result, adapter)
    if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size == 0:
        _raise_for_missing_result(command_result, adapter)
    if result_path.stat().st_size > adapter.config.max_result_bytes:
        raise adapter._error("runner_contract_error", "validation", False, result=command_result)
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise adapter._error("runner_contract_error", "unknown", False, result=command_result) from exc
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != 1:
        raise adapter._error("runner_contract_error", "unknown", False, result=command_result)
    return payload


def _raise_for_missing_result(
    result: ContainerCommandResult,
    adapter: ContainerCodeExecutionAdapter,
) -> None:
    if result.return_code == 137:
        raise adapter._error("resource_limit", "unavailable", False, result=result)
    if result.return_code in {125, 126, 127}:
        raise adapter._error("runtime_unavailable", "unavailable", False, result=result)
    raise adapter._error("runner_contract_error", "unknown", False, result=result)


def _require_success_result(
    payload: Mapping[str, object],
    command_result: ContainerCommandResult,
    adapter: ContainerCodeExecutionAdapter,
) -> tuple[TransformDeadLetterRecord, ...]:
    if payload.get("status") == "failed":
        failure = payload.get("failure")
        runner_failure = failure if isinstance(failure, Mapping) else {}
        failure_type, kind = _classify_runner_failure(runner_failure)
        raise adapter._error(failure_type, kind, False, result=command_result, runner_failure=runner_failure)
    if payload.get("status") != "succeeded" or command_result.return_code != 0:
        raise adapter._error("runner_contract_error", "unknown", False, result=command_result)
    raw_dead_letters = payload.get("deadLetters", [])
    if not isinstance(raw_dead_letters, Sequence) or isinstance(raw_dead_letters, str | bytes):
        raise adapter._error("runner_contract_error", "unknown", False, result=command_result)
    return tuple(_dead_letter(item, adapter, command_result) for item in raw_dead_letters)


def _dead_letter(
    value: object,
    adapter: ContainerCodeExecutionAdapter,
    result: ContainerCommandResult,
) -> TransformDeadLetterRecord:
    if not isinstance(value, Mapping) or not isinstance(value.get("payload"), Mapping):
        raise adapter._error("runner_contract_error", "unknown", False, result=result)
    try:
        return TransformDeadLetterRecord(
            input_dataset_ref=str(value["inputDatasetRef"]),
            row_index=int(cast(int, value["rowIndex"])),
            payload=cast(Mapping[str, object], value["payload"]),
            error_kind=str(value["errorKind"]),
            error_message=str(value["errorMessage"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise adapter._error("runner_contract_error", "unknown", False, result=result) from exc


def _copy_validated_output(
    source: Path,
    target: Path,
    adapter: ContainerCodeExecutionAdapter,
) -> None:
    if source.is_symlink() or not source.is_file():
        raise adapter._error("output_validation_error", "validation", False)
    if source.stat().st_size > adapter.config.max_output_bytes:
        raise adapter._error("output_validation_error", "validation", False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise adapter._error("output_validation_error", "validation", False) from exc


def _failure_evidence(
    config: ContainerCodeExecutionConfig,
    failure_type: CodeExecutionFailureType,
    result: ContainerCommandResult | None,
    stderr: bytes,
    runner_failure: Mapping[str, object] | None,
    cleanup: ContainerCleanupEvidence | None,
) -> CodeExecutionFailureEvidence:
    return CodeExecutionFailureEvidence(
        failure_type=failure_type,
        sandbox_policy=config.policy,
        runtime_profile=config.runtime_binary,
        image_reference=config.image_reference,
        exit_code=result.return_code if result is not None else None,
        signal_name=_signal_name(result.return_code if result is not None else None),
        stderr_sha256=hashlib.sha256(stderr).hexdigest() if stderr else None,
        stderr_byte_count=len(stderr),
        runner_failure_type=_mapping_string(runner_failure, "type"),
        exception_type=_mapping_string(runner_failure, "exceptionType"),
        exception_message_sha256=_mapping_string(runner_failure, "messageSha256"),
        cleanup=cleanup.to_payload() if cleanup is not None else None,
    )


def _classify_runner_failure(
    failure: Mapping[str, object],
) -> tuple[CodeExecutionFailureType, AdapterFailureKind]:
    failure_type = _mapping_string(failure, "type")
    if failure_type in {"user_code_error", "entrypoint_load_failed"}:
        return "user_code_error", "validation"
    output_failures = {
        "function_not_found",
        "callable_not_found",
        "invalid_return_type",
        "invalid_return_rows",
        "output_missing",
    }
    if failure_type in output_failures:
        return "output_validation_error", "validation"
    return "runner_contract_error", "unknown"


def _operator_message(failure_type: CodeExecutionFailureType) -> str:
    messages = {
        "runtime_unavailable": "The isolated code-execution runtime or pinned runner image is unavailable.",
        "sandbox_timeout": "The isolated Python transform exceeded its execution timeout.",
        "resource_limit": "The isolated Python transform was terminated by a sandbox resource limit.",
        "runner_contract_error": "The isolated runner failed without a valid typed result.",
        "user_code_error": "The isolated Python transform raised an error.",
        "output_validation_error": "The isolated Python transform did not produce a valid output.",
    }
    return messages[failure_type]


def _mapping_string(value: Mapping[str, object] | None, key: str) -> str | None:
    if value is None:
        return None
    candidate = value.get(key)
    return candidate if isinstance(candidate, str) else None


def _signal_name(return_code: int | None) -> str | None:
    if return_code == 137:
        return "SIGKILL"
    if return_code == 143:
        return "SIGTERM"
    return None


def _path_tuple(paths: InputFilePaths) -> tuple[Path, ...]:
    return (paths,) if isinstance(paths, Path) else paths


def _timeout_stderr(exc: subprocess.TimeoutExpired) -> bytes:
    stderr = exc.stderr
    if isinstance(stderr, bytes):
        return stderr
    if isinstance(stderr, str):
        return stderr.encode("utf-8", errors="replace")
    return b""
