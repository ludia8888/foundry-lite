"""Contract for Docker-free, brokered Kubernetes Job code execution."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.ports.code_execution import FunctionExecutionPlan
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    ContainerCodeExecutionConfig,
    ContainerCommandResult,
    SandboxWorkspace,
    container_command,
)
from foundry_lite.infrastructure.adapters.kubernetes_job_code_execution import (
    KubernetesJobCodeExecutionAdapter,
)
from foundry_lite.infrastructure.kubernetes_execution_broker import (
    ExecutionKubernetesRequest,
    ExecutionKubernetesResponse,
    ExecutionKubernetesTransportError,
    KubernetesExecutionBroker,
    KubernetesExecutionBrokerConfig,
)
from foundry_lite.infrastructure.kubernetes_execution_spec import (
    kubernetes_execution_job_payload,
    parse_kubernetes_execution_command,
)
from foundry_lite_worker.kubernetes_execution_broker import create_app

_DIGEST = "sha256:" + "a" * 64


class _KubernetesTransport:
    def __init__(self, *, is_create_outcome_unknown: bool = False, is_spec_mismatch: bool = False) -> None:
        self.requests: list[ExecutionKubernetesRequest] = []
        self.is_create_outcome_unknown = is_create_outcome_unknown
        self.is_spec_mismatch = is_spec_mismatch
        self.job: dict[str, object] | None = None

    def send(self, request: ExecutionKubernetesRequest) -> ExecutionKubernetesResponse:
        self.requests.append(request)
        if request.method == "POST" and request.path.endswith("/jobs"):
            assert request.body is not None
            self.job = json.loads(request.body)
            self.job["status"] = {"succeeded": 1}
            if self.is_create_outcome_unknown:
                self.is_create_outcome_unknown = False
                raise ExecutionKubernetesTransportError("timeout")
            return _response(self.job, status_code=201)
        if request.method == "GET" and "/jobs/" in request.path:
            assert self.job is not None
            if self.is_spec_mismatch:
                metadata = self.job["metadata"]
                assert isinstance(metadata, dict)
                metadata["annotations"] = {"foundry-lite.io/execution-spec-sha256": "0" * 64}
            return _response(self.job)
        if request.method == "GET" and "/pods?" in request.path:
            return _response(
                {
                    "items": [
                        {
                            "status": {
                                "containerStatuses": [{"state": {"terminated": {"exitCode": 0, "reason": "Completed"}}}]
                            }
                        }
                    ]
                }
            )
        if request.method == "DELETE":
            return ExecutionKubernetesResponse(200)
        raise AssertionError((request.method, request.path))


def test_kubernetes_job_payload_enforces_digest_identity_resources_and_no_network(tmp_path: Path) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)

    spec = parse_kubernetes_execution_command(
        command,
        timeout_seconds=600,
        shared_workspace_root=workspace,
        pvc_mount_root=pvc_root,
    )
    payload = kubernetes_execution_job_payload(spec, namespace="foundry-qa", pvc_name="foundry-runtime")
    pod = payload["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert container["image"] == f"ghcr.io/example/runner@{_DIGEST}"
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["resources"]["limits"] == {"cpu": "1.0", "memory": "512Mi"}
    assert container["command"][:3] == ["/bin/sh", "-c", 'ulimit -f "$1"; shift; exec "$@"']
    assert pod["automountServiceAccountToken"] is False
    assert pod["hostNetwork"] is False
    assert payload["spec"]["backoffLimit"] == 0
    assert payload["spec"]["activeDeadlineSeconds"] == 600
    assert payload["metadata"]["labels"]["foundry-lite.io/execution-sandbox"] == "true"
    assert len(payload["metadata"]["annotations"]["foundry-lite.io/execution-spec-sha256"]) == 64


def test_kubernetes_execution_command_rejects_mutable_image_network_or_workspace_escape(tmp_path: Path) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)

    with pytest.raises(ValueError, match="sha256 digest"):
        parse_kubernetes_execution_command(
            _replace(command, f"ghcr.io/example/runner@{_DIGEST}", "ghcr.io/example/runner:latest"),
            timeout_seconds=600,
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        )
    with pytest.raises(ValueError, match="CPU limit is out of range"):
        parse_kubernetes_execution_command(
            _replace(command, "--cpus=1.0", "--cpus=999"),
            timeout_seconds=600,
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        )
    with pytest.raises(ValueError, match="security flags"):
        parse_kubernetes_execution_command(
            tuple(item for item in command if item != "--network=none"),
            timeout_seconds=600,
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = list(command)
    first_mount = escaped.index("--mount") + 1
    mount_parts = escaped[first_mount].split(",")
    escaped[first_mount] = ",".join(f"source={outside}" if part.startswith("source=") else part for part in mount_parts)
    with pytest.raises(ValueError, match="escapes the shared workspace"):
        parse_kubernetes_execution_command(
            tuple(escaped),
            timeout_seconds=600,
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        )


@pytest.mark.parametrize("is_create_outcome_unknown", [False, True])
def test_execution_broker_reconciles_create_and_returns_hash_only_runtime_evidence(
    tmp_path: Path,
    is_create_outcome_unknown: bool,
) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)
    result_path = _mount_source(command, "/sandbox-output/execution-result.json")
    output_path = _mount_source(command, "/sandbox-output/result.parquet")
    result_path.write_text('{"schemaVersion":1,"status":"succeeded","deadLetters":[]}', encoding="utf-8")
    output_path.write_bytes(b"parquet-result")
    transport = _KubernetesTransport(is_create_outcome_unknown=is_create_outcome_unknown)
    broker = KubernetesExecutionBroker(
        KubernetesExecutionBrokerConfig(
            namespace="foundry-qa",
            pvc_name="foundry-runtime",
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        ),
        transport=transport,
    )

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.runtime_evidence["executionMode"] == "kubernetes-job"
    assert result.runtime_evidence["imageDigest"] == _DIGEST
    assert result.runtime_evidence["networkDisabled"] is True
    assert str(result.runtime_evidence["resultSha256"]).startswith("sha256:")
    assert str(result.runtime_evidence["outputSha256"]).startswith("sha256:")
    assert "parquet-result" not in json.dumps(result.to_payload())
    if is_create_outcome_unknown:
        assert any(request.method == "GET" and "/jobs/" in request.path for request in transport.requests)


def test_execution_broker_rejects_reconciled_job_with_different_spec(tmp_path: Path) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)
    transport = _KubernetesTransport(is_create_outcome_unknown=True, is_spec_mismatch=True)
    broker = KubernetesExecutionBroker(
        KubernetesExecutionBrokerConfig(
            namespace="foundry-qa",
            pvc_name="foundry-runtime",
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        ),
        transport=transport,
    )

    with pytest.raises(RuntimeError, match="reconcile_spec_mismatch"):
        broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})


def test_execution_broker_cleanup_requires_exact_deterministic_name(tmp_path: Path) -> None:
    pvc_root, workspace, _ = _workspace_command(tmp_path)
    broker = KubernetesExecutionBroker(
        KubernetesExecutionBrokerConfig(
            namespace="foundry-qa",
            pvc_name="foundry-runtime",
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        ),
        transport=_KubernetesTransport(),
    )

    with pytest.raises(ValueError, match="name is invalid"):
        broker.cleanup("foundry-lite-python-not-a-deterministic-id")


def test_kubernetes_job_adapter_never_invokes_a_local_container_runtime(tmp_path: Path) -> None:
    class _Runner:
        def __init__(self) -> None:
            self.commands: list[tuple[str, ...]] = []

        def __call__(
            self,
            command: Sequence[str],
            timeout_seconds: float,
            environment: Mapping[str, str],
        ) -> ContainerCommandResult:
            del timeout_seconds, environment
            self.commands.append(tuple(command))
            if command[1:3] == ("rm", "--force"):
                return ContainerCommandResult(0)
            _mount_source(command, "/sandbox-output/execution-result.json").write_text(
                '{"schemaVersion":1,"status":"succeeded","output":42}',
                encoding="utf-8",
            )
            return ContainerCommandResult(
                0,
                runtime_evidence={"executionMode": "kubernetes-job", "networkDisabled": True},
            )

    runner = _Runner()
    adapter = KubernetesJobCodeExecutionAdapter(
        environ={
            "FOUNDRY_LITE_CODE_EXECUTION_IMAGE": f"ghcr.io/example/runner@{_DIGEST}",
            "FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE": f"ghcr.io/example/node-runner@{_DIGEST}",
            "FOUNDRY_LITE_CODE_EXECUTION_BROKER_TOKEN": "x" * 32,
            "FOUNDRY_LITE_CODE_EXECUTION_WORKSPACE_ROOT": str(tmp_path),
        },
        command_runner=runner,  # type: ignore[arg-type]
    )

    result = adapter.execute_function(
        FunctionExecutionPlan(
            function_api_name="calculate",
            function_version="1",
            runtime="python",
            entrypoint="calculate",
            source="def calculate():\n    return 42\n",
            inputs_json={},
            argument_order=(),
            output_type="integer",
            timeout_seconds=30,
            input_byte_limit=10_000,
        )
    )

    assert result.output == 42
    assert result.runtime_evidence == {"executionMode": "kubernetes-job", "networkDisabled": True}
    assert adapter.profile_name == "kubernetes-job-code-execution"
    assert adapter.config.runtime_binary == "kubernetes-job-client"
    assert all(command[0] == "kubernetes-job-client" for command in runner.commands)


def test_execution_broker_http_boundary_requires_token_and_hides_internal_failures() -> None:
    class _Broker:
        def execute(self, payload: Mapping[str, object]) -> object:
            del payload
            raise RuntimeError("private Kubernetes response")

        def cleanup(self, name: str) -> bool:
            return bool(name)

    client = TestClient(create_app(broker=_Broker(), bearer_token="t" * 32))  # type: ignore[arg-type]

    assert client.post("/v1/executions", json={}).status_code == 401
    response = client.post(
        "/v1/executions",
        json={"schemaVersion": 1},
        headers={"authorization": f"Bearer {'t' * 32}"},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "execution_broker_unavailable"}
    assert "private Kubernetes response" not in response.text
    oversized = client.post(
        "/v1/executions",
        content=b"x" * (128 * 1024 + 1),
        headers={"authorization": f"Bearer {'t' * 32}", "content-type": "application/json"},
    )
    assert oversized.status_code == 422


def _workspace_command(tmp_path: Path) -> tuple[Path, Path, tuple[str, ...]]:
    pvc_root = tmp_path / "pvc"
    workspace = pvc_root / "code-execution-workspaces"
    session = workspace / "session"
    job = session / "job"
    output = session / "output"
    inputs = session / "inputs"
    for path in (job, output, inputs):
        path.mkdir(parents=True, exist_ok=True)
    result_path = output / "execution-result.json"
    output_path = output / "result.parquet"
    input_path = inputs / "input-0000.parquet"
    for path in (result_path, output_path, input_path):
        path.touch()
    sandbox = SandboxWorkspace(
        job_dir=job,
        output_dir=output,
        result_path=result_path,
        output_path=output_path,
        input_mounts=((input_path, "/sandbox-inputs/input-0000.parquet"),),
    )
    config = ContainerCodeExecutionConfig(
        image_reference=f"ghcr.io/example/runner@{_DIGEST}",
        node_image_reference=f"ghcr.io/example/node-runner@{_DIGEST}",
        runtime_binary="kubernetes-job-client",
        workspace_root=workspace,
        is_image_digest_required=True,
    )
    return pvc_root, workspace, container_command(config, sandbox, "foundry-lite-python-" + "a" * 32)


def _mount_source(command: Sequence[str], target: str) -> Path:
    for index, item in enumerate(command[:-1]):
        if item != "--mount":
            continue
        fields = dict(part.split("=", 1) for part in command[index + 1].split(",") if "=" in part)
        if fields.get("target") == target:
            return Path(fields["source"])
    raise AssertionError(target)


def _replace(command: Sequence[str], old: str, new: str) -> tuple[str, ...]:
    return tuple(new if item == old else item for item in command)


def _response(payload: Mapping[str, object], *, status_code: int = 200) -> ExecutionKubernetesResponse:
    return ExecutionKubernetesResponse(status_code, json.dumps(payload).encode())
