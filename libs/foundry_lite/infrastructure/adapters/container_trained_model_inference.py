"""Digest-pinnable, fail-closed container sidecar for trained-model batches."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess  # nosec B404
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
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
from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelInferenceResult,
    TrainedModelInvocation,
)
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    require_trained_model_invocation_pin,
)
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    ContainerCommandResult,
    ContainerCommandRunner,
)
from foundry_lite.infrastructure.adapters.container_trained_model_runtime import (
    ContainerTrainedModelConfig,
    ContainerTrainedModelSpec,
    default_model_command_runner,
    is_digest_pinned_model_image,
    model_client_environment,
    model_container_command,
    model_workspace_root,
    validate_model_config,
)


class ContainerTrainedModelInferenceAdapter:
    """Execute each trained-model batch in its own isolated model image."""

    profile_name = "container-trained-model-sidecar"

    def __init__(
        self,
        config: ContainerTrainedModelConfig | None = None,
        *,
        command_runner: ContainerCommandRunner | None = None,
        environ: Mapping[str, str] | None = None,
        is_image_digest_required: bool = False,
    ) -> None:
        self.config = config or ContainerTrainedModelConfig.from_env(
            environ,
            is_image_digest_required=is_image_digest_required,
        )
        validate_model_config(self.config)
        self._command_runner = command_runner or default_model_command_runner()
        self._client_environment = model_client_environment(os.environ if environ is None else environ)

    def list_models(self) -> Sequence[TrainedModelDefinition]:
        return tuple(spec.definition for spec in self.config.specs)

    def resolve(
        self,
        model_ref: str,
        *,
        branch: str,
        fallback_branches: Sequence[str] = (),
    ) -> TrainedModelDefinition:
        return _resolved_definition(self._resolve_spec(model_ref, branch, fallback_branches))

    def infer(self, invocation: TrainedModelInvocation) -> TrainedModelInferenceResult:
        resolved = _pinned_spec(invocation) or self._resolve_spec(
            invocation.model_ref,
            invocation.branch,
            invocation.fallback_branches,
        )
        spec = _execution_spec(self.config, resolved, invocation.expected_executable_reference)
        definition = invocation.pinned_definition or _resolved_definition(spec)
        require_trained_model_invocation_pin(invocation, definition)
        payload = _request_payload(invocation)
        request_bytes = _bounded_request_bytes(payload, self)
        with tempfile.TemporaryDirectory(
            prefix="foundry-lite-trained-model-",
            dir=model_workspace_root(self.config),
        ) as temporary:
            root = Path(temporary)
            request_path, result_path = _prepare_workspace(root, request_bytes)
            container_name = f"foundry-lite-model-{uuid4().hex}"
            command = model_container_command(
                self.config,
                spec,
                request_path,
                result_path,
                container_name,
            )
            result = self._execute_container(command, container_name)
            response = _read_result(result_path, result, self)
        rows, runtime = _successful_response(response, result, self)
        return TrainedModelInferenceResult(
            definition=definition,
            rows=rows,
            runtime_evidence=_runtime_evidence(runtime, spec, self.config),
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("infer", "unavailable", False, "The pinned model image is unavailable."),
                AdapterFailureMode(
                    "infer",
                    "timeout",
                    True,
                    "The trained-model sidecar exceeded its execution timeout.",
                    timeout_seconds=self.config.policy.timeout_seconds,
                ),
                AdapterFailureMode("infer", "validation", False, "The model rejected input or output contract."),
                AdapterFailureMode("infer", "unknown", False, "The model sidecar returned no valid result."),
            ),
        )

    def _resolve_spec(
        self,
        model_ref: str,
        branch: str,
        fallback_branches: Sequence[str],
    ) -> ContainerTrainedModelSpec:
        branches = (branch, *fallback_branches)
        for candidate_branch in branches:
            for spec in self.config.specs:
                if spec.definition.model_ref == model_ref and spec.definition.branch == candidate_branch:
                    return spec
        raise NotFound(
            "trained model was not found on the selected branch or fallbacks",
            details={"modelRef": model_ref, "branches": list(branches)},
        )

    def _execute_container(self, command: Sequence[str], container_name: str) -> ContainerCommandResult:
        try:
            return self._command_runner(command, self.config.policy.timeout_seconds, self._client_environment)
        except subprocess.TimeoutExpired as exc:
            self._force_remove(container_name)
            raise self._error("sandbox_timeout", "timeout", True) from exc
        except (FileNotFoundError, OSError) as exc:
            raise self._error("runtime_unavailable", "unavailable", False) from exc

    def _force_remove(self, container_name: str) -> None:
        command = (self.config.runtime_binary, "rm", "--force", container_name)
        with suppress(Exception):
            self._command_runner(command, self.config.cleanup_timeout_seconds, self._client_environment)

    def _error(
        self,
        failure_type: str,
        kind: AdapterFailureKind,
        is_retryable: bool,
        *,
        result: ContainerCommandResult | None = None,
        runner_failure: Mapping[str, object] | None = None,
    ) -> AdapterError:
        stderr = result.stderr if result is not None else b""
        details = {
            "failureType": failure_type,
            "imageReference": _image_reference(self.config),
            "exitCode": result.return_code if result is not None else None,
            "stderrSha256": hashlib.sha256(stderr).hexdigest() if stderr else None,
            "stderrByteCount": len(stderr),
            "runnerFailureType": _text(runner_failure, "type"),
            "exceptionType": _text(runner_failure, "exceptionType"),
            "exceptionMessageSha256": _text(runner_failure, "messageSha256"),
            "sandboxPolicy": self.config.policy.to_payload(),
        }
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="infer",
                kind=kind,
                is_retryable=is_retryable,
                operator_message=_operator_message(failure_type),
                timeout_seconds=self.config.policy.timeout_seconds if kind == "timeout" else None,
                details={"trainedModelSidecar": details},
            )
        )


def _request_payload(invocation: TrainedModelInvocation) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "modelRef": invocation.model_ref,
        "branch": invocation.branch,
        "fallbackBranches": list(invocation.fallback_branches),
        "rows": [dict(row) for row in invocation.rows],
    }


def _resolved_definition(spec: ContainerTrainedModelSpec) -> TrainedModelDefinition:
    return replace(spec.definition, executable_reference=spec.image_reference)


def _pinned_spec(invocation: TrainedModelInvocation) -> ContainerTrainedModelSpec | None:
    definition = invocation.pinned_definition
    if definition is None:
        return None
    image_reference = invocation.expected_executable_reference or definition.executable_reference
    return ContainerTrainedModelSpec(definition=definition, image_reference=image_reference)


def _execution_spec(
    config: ContainerTrainedModelConfig,
    resolved: ContainerTrainedModelSpec,
    expected_executable_reference: str | None,
) -> ContainerTrainedModelSpec:
    executable_reference = (expected_executable_reference or resolved.image_reference).strip()
    if config.is_image_digest_required and not is_digest_pinned_model_image(executable_reference):
        raise ValidationFailed("protected trained-model execution pin must be a sha256 image digest")
    return replace(resolved, image_reference=executable_reference)


def _bounded_request_bytes(
    payload: Mapping[str, object],
    adapter: ContainerTrainedModelInferenceAdapter,
) -> bytes:
    rows = cast(list[object], payload["rows"])
    if len(rows) > adapter.config.max_rows:
        raise adapter._error("input_row_limit", "validation", False)
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_trained_model_json_value,
        ).encode()
    except (OverflowError, TypeError, ValueError) as exc:
        raise adapter._error("input_encoding_error", "validation", False) from exc
    if len(encoded) > adapter.config.max_request_bytes:
        raise adapter._error("input_byte_limit", "validation", False)
    return encoded


def _trained_model_json_value(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("non-finite decimal is not a trained-model JSON value")
        return format(value, "f")
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported trained-model JSON value: {type(value).__name__}")


def _prepare_workspace(root: Path, request_bytes: bytes) -> tuple[Path, Path]:
    request_path = root / "request.json"
    output_dir = root / "output"
    result_path = output_dir / "result.json"
    request_path.write_bytes(request_bytes)
    request_path.chmod(0o444)
    output_dir.mkdir(mode=0o700)
    result_path.touch()
    result_path.chmod(0o666)
    return request_path, result_path


def _read_result(
    path: Path,
    command_result: ContainerCommandResult,
    adapter: ContainerTrainedModelInferenceAdapter,
) -> Mapping[str, object]:
    if command_result.return_code in {125, 126, 127, 137}:
        _raise_missing_result(command_result, adapter)
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        _raise_missing_result(command_result, adapter)
    if path.stat().st_size > adapter.config.max_result_bytes:
        raise adapter._error("output_limit", "validation", False, result=command_result)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise adapter._error("runner_contract_error", "unknown", False, result=command_result) from exc
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != 1:
        raise adapter._error("runner_contract_error", "unknown", False, result=command_result)
    return payload


def _raise_missing_result(
    result: ContainerCommandResult,
    adapter: ContainerTrainedModelInferenceAdapter,
) -> None:
    if result.return_code == 137:
        raise adapter._error("resource_limit", "unavailable", False, result=result)
    if result.return_code in {125, 126, 127}:
        raise adapter._error("runtime_unavailable", "unavailable", False, result=result)
    raise adapter._error("runner_contract_error", "unknown", False, result=result)


def _successful_response(
    payload: Mapping[str, object],
    result: ContainerCommandResult,
    adapter: ContainerTrainedModelInferenceAdapter,
) -> tuple[tuple[Mapping[str, object], ...], Mapping[str, object]]:
    if payload.get("status") == "failed":
        failure = payload.get("failure")
        evidence = failure if isinstance(failure, Mapping) else {}
        raise adapter._error("model_execution_error", "validation", False, result=result, runner_failure=evidence)
    rows = payload.get("rows")
    runtime = payload.get("runtimeEvidence")
    if payload.get("status") != "succeeded" or result.return_code != 0:
        raise adapter._error("runner_contract_error", "unknown", False, result=result)
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise adapter._error("output_contract_error", "validation", False, result=result)
    if not isinstance(runtime, Mapping):
        raise adapter._error("runner_contract_error", "unknown", False, result=result)
    return tuple(cast(Mapping[str, object], row) for row in rows), runtime


def _runtime_evidence(
    runtime: Mapping[str, object],
    spec: ContainerTrainedModelSpec,
    config: ContainerTrainedModelConfig,
) -> dict[str, object]:
    return {
        **dict(runtime),
        "executionMode": "batch",
        "runtime": "isolated_container_sidecar",
        "imageReference": spec.image_reference,
        "imageDigestPinned": "@sha256:" in spec.image_reference,
        "sandboxPolicy": config.policy.to_payload(),
    }


def _image_reference(config: ContainerTrainedModelConfig) -> str:
    return config.specs[0].image_reference if config.specs else "unavailable"


def _text(value: Mapping[str, object] | None, key: str) -> str | None:
    candidate = value.get(key) if value is not None else None
    return candidate if isinstance(candidate, str) else None


def _operator_message(failure_type: str) -> str:
    messages = {
        "input_row_limit": "The trained-model batch exceeded its configured row limit.",
        "input_byte_limit": "The trained-model batch exceeded its configured byte limit.",
        "input_encoding_error": "The trained-model batch contains an unsupported input value.",
        "sandbox_timeout": "The trained-model sidecar exceeded its execution timeout.",
        "runtime_unavailable": "The pinned trained-model image or container runtime is unavailable.",
        "resource_limit": "The trained-model sidecar was terminated by a resource limit.",
        "output_limit": "The trained-model sidecar result exceeded its byte limit.",
        "output_contract_error": "The trained-model sidecar returned rows outside its typed contract.",
        "model_execution_error": "The trained model rejected the batch.",
        "runner_contract_error": "The trained-model sidecar returned no valid typed result.",
    }
    return messages.get(failure_type, "The trained-model sidecar failed.")
