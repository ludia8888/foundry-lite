"""Contract tests for batch-only reusable trained-model inference."""

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelField,
    TrainedModelInferencePort,
    TrainedModelInvocation,
)
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure.adapters import (
    container_trained_model_inference as container_model,
)
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import ContainerCommandResult
from foundry_lite.infrastructure.adapters.container_trained_model_inference import (
    ContainerTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.container_trained_model_runtime import (
    ContainerTrainedModelConfig,
    ContainerTrainedModelSpec,
)
from foundry_lite.infrastructure.adapters.local_trained_model_inference import (
    LocalTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.trained_model_definitions import (
    TRANSACTION_RISK_DEFINITION,
)


def test_trained_model_port_resolves_branch_and_returns_exact_api_columns() -> None:
    adapter: TrainedModelInferencePort = LocalTrainedModelInferenceAdapter()
    definition = adapter.resolve(
        "demo.transaction-risk",
        branch="feature/model-api",
        fallback_branches=("master",),
    )

    result = adapter.infer(
        TrainedModelInvocation(
            model_ref=definition.model_ref,
            branch="master",
            fallback_branches=(),
            rows=({"amount": 18_000.0, "country": "US"},),
        )
    )

    assert definition.is_preview_supported is False
    assert definition.execution_modes == ("batch",)
    assert result.rows == ({"riskScore": 0.8, "decision": "review"},)
    assert result.runtime_evidence["warmPoolEnabled"] is False


def test_container_sidecar_enforces_controls_and_returns_typed_rows(tmp_path: Path) -> None:
    captured: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], _timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        captured.append(tuple(command))
        _write_result(
            _mount_source(captured[-1], "/model-output/result.json"),
            _success_payload(),
        )
        return ContainerCommandResult(0)

    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        command_runner=runner,
        environ={"PATH": "/usr/bin", "UNSAFE_SECRET": "must-not-pass"},
    )
    result = adapter.infer(_container_invocation())

    assert result.rows == ({"riskScore": 0.8, "decision": "review"},)
    assert result.runtime_evidence["runtime"] == "isolated_container_sidecar"
    assert result.runtime_evidence["sandboxPolicy"] == adapter.config.policy.to_payload()
    assert _required_container_flags() <= set(captured[-1])
    assert not any("target=/model-output," in token for token in captured[-1])
    assert "UNSAFE_SECRET=must-not-pass" not in captured[-1]


def test_container_sidecar_prefers_requested_branch_over_earlier_fallback_spec(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    fallback = replace(TRANSACTION_RISK_DEFINITION, branch="master", revision="fallback")
    requested = replace(TRANSACTION_RISK_DEFINITION, branch="feature/model-api", revision="requested")

    def runner(command: Sequence[str], _timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        commands.append(tuple(command))
        _write_result(_mount_source(commands[-1], "/model-output/result.json"), _success_payload())
        return ContainerCommandResult(0)

    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(
            specs=(
                ContainerTrainedModelSpec(fallback, "registry.example/model:fallback"),
                ContainerTrainedModelSpec(requested, "registry.example/model:requested"),
            ),
            workspace_root=tmp_path,
        ),
        command_runner=runner,
        environ={},
    )

    resolved = adapter.resolve(
        requested.model_ref,
        branch=requested.branch,
        fallback_branches=(fallback.branch,),
    )
    result = adapter.infer(
        replace(
            _container_invocation(),
            branch=requested.branch,
            fallback_branches=(fallback.branch,),
        )
    )

    assert resolved.revision == "requested"
    assert result.definition.revision == "requested"
    assert "registry.example/model:requested" in commands[-1]
    assert "registry.example/model:fallback" not in commands[-1]


def test_container_sidecar_executes_deployment_digest_when_current_branch_image_changes(tmp_path: Path) -> None:
    deployed_image = f"registry.example/model@sha256:{'a' * 64}"
    current_image = f"registry.example/model@sha256:{'b' * 64}"
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], _timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        commands.append(tuple(command))
        _write_result(_mount_source(commands[-1], "/model-output/result.json"), _success_payload())
        return ContainerCommandResult(0)

    current_definition = replace(
        TRANSACTION_RISK_DEFINITION,
        version="2026.08.1",
        revision="container-risk-model-r2",
    )
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(
            specs=(ContainerTrainedModelSpec(current_definition, current_image),),
            workspace_root=tmp_path,
            is_image_digest_required=True,
        ),
        command_runner=runner,
        environ={},
    )
    invocation = replace(
        _container_invocation(),
        expected_model_version="2026.07.1",
        expected_revision="container-risk-model-r1",
        expected_executable_reference=deployed_image,
        pinned_definition=replace(
            TRANSACTION_RISK_DEFINITION,
            executable_reference=deployed_image,
        ),
    )

    result = adapter.infer(invocation)

    assert result.definition.executable_reference == deployed_image
    assert result.definition.version == "2026.07.1"
    assert result.definition.revision == "container-risk-model-r1"
    assert deployed_image in commands[-1]
    assert current_image not in commands[-1]


def test_container_sidecar_executes_deployment_pin_after_current_registry_removal(tmp_path: Path) -> None:
    deployed_image = f"registry.example/model@sha256:{'a' * 64}"
    deployed_runner_path = "/srv/deployed-model/custom_runner.py"
    rotated_image = f"registry.example/other@sha256:{'c' * 64}"
    rotated_definition = replace(
        TRANSACTION_RISK_DEFINITION,
        model_ref="demo.other-model",
        display_name="Other model",
    )
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], _timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        commands.append(tuple(command))
        _write_result(_mount_source(commands[-1], "/model-output/result.json"), _success_payload())
        return ContainerCommandResult(0)

    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(
            specs=(ContainerTrainedModelSpec(rotated_definition, rotated_image),),
            workspace_root=tmp_path,
            is_image_digest_required=True,
        ),
        command_runner=runner,
        environ={},
    )
    invocation = replace(
        _container_invocation(),
        expected_model_version="2026.07.1",
        expected_revision="container-risk-model-r1",
        expected_executable_reference=deployed_image,
        pinned_definition=replace(
            TRANSACTION_RISK_DEFINITION,
            executable_reference=deployed_image,
            executable_entrypoint=deployed_runner_path,
        ),
    )

    result = adapter.infer(invocation)

    assert result.definition.model_ref == "demo.transaction-risk"
    assert deployed_image in commands[-1]
    assert deployed_runner_path in commands[-1]
    assert "/opt/foundry-lite/model/trained_model_runner.py" not in commands[-1]
    assert rotated_image not in commands[-1]


def test_container_sidecar_rejects_non_digest_deployment_pin_in_protected_runtime(tmp_path: Path) -> None:
    current_image = f"registry.example/model@sha256:{'b' * 64}"
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(
            specs=(ContainerTrainedModelSpec(TRANSACTION_RISK_DEFINITION, current_image),),
            workspace_root=tmp_path,
            is_image_digest_required=True,
        ),
        command_runner=lambda *_args: pytest.fail("unpinned model must not start a container"),
        environ={},
    )

    with pytest.raises(ValidationFailed, match="sha256 image digest"):
        adapter.infer(
            replace(
                _container_invocation(),
                expected_model_version="2026.07.1",
                expected_revision="container-risk-model-r1",
                expected_executable_reference="registry.example/model:mutable",
            )
        )


def test_container_sidecar_serializes_every_advertised_scalar_input_type(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    definition = replace(
        TRANSACTION_RISK_DEFINITION,
        input_fields=(
            TrainedModelField("decimalValue", "decimal"),
            TrainedModelField("binaryValue", "binary"),
            TrainedModelField("dateValue", "date"),
            TrainedModelField("timestampValue", "timestamp"),
        ),
    )

    def runner(command: Sequence[str], _timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        request_path = _mount_source(tuple(command), "/model-input/request.json")
        captured.update(json.loads(request_path.read_text(encoding="utf-8")))
        _write_result(_mount_source(tuple(command), "/model-output/result.json"), _success_payload())
        return ContainerCommandResult(0)

    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(
            specs=(ContainerTrainedModelSpec(definition, "registry.example/model:typed"),),
            workspace_root=tmp_path,
        ),
        command_runner=runner,
        environ={},
    )
    adapter.infer(
        TrainedModelInvocation(
            model_ref=definition.model_ref,
            branch=definition.branch,
            fallback_branches=(),
            rows=(
                {
                    "decimalValue": Decimal("12.340"),
                    "binaryValue": b"\x00\xff",
                    "dateValue": date(2026, 7, 28),
                    "timestampValue": datetime(2026, 7, 28, 12, 30, tzinfo=UTC),
                },
            ),
        )
    )

    assert captured["rows"] == [
        {
            "binaryValue": "AP8=",
            "dateValue": "2026-07-28",
            "decimalValue": "12.340",
            "timestampValue": "2026-07-28T12:30:00+00:00",
        }
    ]


def test_container_sidecar_normalizes_unsupported_input_to_adapter_failure(tmp_path: Path) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        command_runner=lambda *_args: pytest.fail("container must not start for invalid input"),
        environ={},
    )
    invocation = replace(_container_invocation(), rows=({"amount": object(), "country": "US"},))

    with pytest.raises(AdapterError) as captured:
        adapter.infer(invocation)

    evidence = captured.value.failure.details["trainedModelSidecar"]
    assert captured.value.failure.kind == "validation"
    assert isinstance(evidence, dict)
    assert evidence["failureType"] == "input_encoding_error"


def test_container_sidecar_redacts_model_failure(tmp_path: Path) -> None:
    private_message = "private-model-customer-value"

    def runner(command: Sequence[str], _timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        _write_result(
            _mount_source(tuple(command), "/model-output/result.json"),
            {
                "schemaVersion": 1,
                "status": "failed",
                "failure": {
                    "type": "model_execution_error",
                    "exceptionType": "RuntimeError",
                    "messageSha256": hashlib.sha256(private_message.encode()).hexdigest(),
                },
            },
        )
        return ContainerCommandResult(2, stderr=private_message.encode())

    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        command_runner=runner,
        environ={},
    )
    with pytest.raises(AdapterError) as captured:
        adapter.infer(_container_invocation())

    evidence = captured.value.failure.details["trainedModelSidecar"]
    assert isinstance(evidence, dict)
    assert evidence["exceptionType"] == "RuntimeError"
    assert evidence["exceptionMessageSha256"] == hashlib.sha256(private_message.encode()).hexdigest()
    assert private_message not in str(captured.value.failure.details)


def test_container_sidecar_timeout_force_cleans_container(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], timeout: float, _environment: Mapping[str, str]) -> ContainerCommandResult:
        argv = tuple(command)
        commands.append(argv)
        if argv[1:3] == ("rm", "--force"):
            return ContainerCommandResult(0)
        raise subprocess.TimeoutExpired(argv, timeout)

    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        command_runner=runner,
        environ={},
    )
    with pytest.raises(AdapterError) as captured:
        adapter.infer(_container_invocation())

    assert captured.value.failure.kind == "timeout"
    assert any(command[1:3] == ("rm", "--force") for command in commands)


def test_container_sidecar_requires_digest_in_protected_runtime() -> None:
    with pytest.raises(ValueError, match="sha256 digest"):
        ContainerTrainedModelInferenceAdapter(
            ContainerTrainedModelConfig(is_image_digest_required=True),
            environ={},
        )

    digest = "a" * 64
    config = ContainerTrainedModelConfig(
        specs=(
            ContainerTrainedModelSpec(
                TRANSACTION_RISK_DEFINITION,
                f"registry.example/model@sha256:{digest}",
            ),
        ),
        is_image_digest_required=True,
    )
    assert ContainerTrainedModelInferenceAdapter(config, environ={}).list_models()


@pytest.mark.parametrize(
    ("config", "failure_type"),
    (
        (
            ContainerTrainedModelConfig(max_rows=1),
            "input_row_limit",
        ),
        (
            ContainerTrainedModelConfig(max_request_bytes=16),
            "input_byte_limit",
        ),
    ),
)
def test_container_sidecar_rejects_oversized_input_with_typed_redacted_failure(
    tmp_path: Path,
    config: ContainerTrainedModelConfig,
    failure_type: str,
) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(
            specs=config.specs,
            policy=config.policy,
            workspace_root=tmp_path,
            max_rows=config.max_rows,
            max_request_bytes=config.max_request_bytes,
        ),
        command_runner=lambda *_args: pytest.fail("container must not start for invalid input"),
        environ={},
    )
    invocation = _container_invocation()
    if failure_type == "input_row_limit":
        invocation = TrainedModelInvocation(
            model_ref=invocation.model_ref,
            branch=invocation.branch,
            fallback_branches=invocation.fallback_branches,
            rows=(*invocation.rows, *invocation.rows),
        )

    with pytest.raises(AdapterError) as captured:
        adapter.infer(invocation)

    assert captured.value.failure.kind == "validation"
    assert captured.value.failure.is_retryable is False
    evidence = captured.value.failure.details["trainedModelSidecar"]
    assert isinstance(evidence, dict)
    assert evidence["failureType"] == failure_type
    assert "18_000" not in str(captured.value.failure.details)


def test_container_sidecar_resolve_and_runtime_unavailable_fail_closed(
    tmp_path: Path,
) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        command_runner=lambda *_args: (_ for _ in ()).throw(FileNotFoundError("docker")),
        environ={},
    )
    with pytest.raises(NotFound):
        adapter.resolve("missing", branch="master")
    with pytest.raises(AdapterError) as raised:
        adapter.infer(_container_invocation())
    assert raised.value.failure.kind == "unavailable"


@pytest.mark.parametrize("return_code", [125, 126, 127, 137])
def test_container_sidecar_missing_result_maps_runtime_exit_codes(
    tmp_path: Path,
    return_code: int,
) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        environ={},
    )
    path = tmp_path / "result.json"
    path.touch()
    with pytest.raises(AdapterError) as raised:
        container_model._read_result(
            path,
            ContainerCommandResult(return_code),
            adapter,
        )
    assert raised.value.failure.kind == "unavailable"


@pytest.mark.parametrize("payload", [b"", b"{", b"[]", b'{"schemaVersion":2}'])
def test_container_sidecar_result_parser_rejects_malformed_evidence(
    tmp_path: Path,
    payload: bytes,
) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path),
        environ={},
    )
    path = tmp_path / "result.json"
    path.write_bytes(payload)
    with pytest.raises(AdapterError):
        container_model._read_result(path, ContainerCommandResult(0), adapter)


def test_container_sidecar_result_parser_enforces_output_size(tmp_path: Path) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(
        ContainerTrainedModelConfig(workspace_root=tmp_path, max_result_bytes=2),
        environ={},
    )
    path = tmp_path / "result.json"
    path.write_bytes(b"{}x")
    with pytest.raises(AdapterError) as raised:
        container_model._read_result(path, ContainerCommandResult(0), adapter)
    assert raised.value.failure.kind == "validation"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "unknown"},
        {"status": "succeeded", "rows": "bad", "runtimeEvidence": {}},
        {"status": "succeeded", "rows": [{}], "runtimeEvidence": []},
    ],
)
def test_container_sidecar_success_parser_rejects_invalid_output_contract(
    payload: dict[str, object],
) -> None:
    adapter = ContainerTrainedModelInferenceAdapter(environ={})
    with pytest.raises(AdapterError):
        container_model._successful_response(payload, ContainerCommandResult(0), adapter)


def test_container_sidecar_failure_contract_and_unknown_operator_message() -> None:
    adapter = ContainerTrainedModelInferenceAdapter(environ={})
    assert {mode.kind for mode in adapter.failure_contract().modes} == {
        "unavailable",
        "timeout",
        "validation",
        "unknown",
    }
    assert container_model._image_reference(ContainerTrainedModelConfig(specs=())) == "unavailable"
    assert container_model._text(None, "type") is None
    assert container_model._operator_message("unknown-failure") == ("The trained-model sidecar failed.")


def _container_invocation() -> TrainedModelInvocation:
    return TrainedModelInvocation(
        model_ref="demo.transaction-risk",
        branch="master",
        fallback_branches=(),
        rows=({"amount": 18_000.0, "country": "US"},),
    )


def _mount_source(command: tuple[str, ...], target: str) -> Path:
    for index, token in enumerate(command):
        if token != "--mount" or index + 1 >= len(command):
            continue
        fields = dict(part.split("=", 1) for part in command[index + 1].split(",") if "=" in part)
        if fields.get("target") == target:
            return Path(fields["source"])
    raise AssertionError(f"mount target not found: {target}")


def _write_result(result_path: Path, payload: dict[str, object]) -> None:
    result_path.write_text(json.dumps(payload), encoding="utf-8")


def _success_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "succeeded",
        "rows": [{"riskScore": 0.8, "decision": "review"}],
        "runtimeEvidence": {
            "uid": 65532,
            "gid": 65532,
            "networkBlocked": True,
            "rootWriteBlocked": True,
            "effectiveCapabilities": "0000000000000000",
            "noNewPrivileges": "1",
        },
    }


def _required_container_flags() -> set[str]:
    return {
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user=65532:65532",
        "--cpus=1.0",
        "--memory=8192m",
        "--memory-swap=8192m",
        "--pids-limit=64",
        "--ulimit=fsize=33554432:33554432",
    }
