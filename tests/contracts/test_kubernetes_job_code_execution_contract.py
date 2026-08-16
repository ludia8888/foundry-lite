"""Contract for Docker-free, brokered Kubernetes Job code execution."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.ports.code_execution import FunctionExecutionPlan
from foundry_lite.infrastructure import kubernetes_execution_broker as execution_broker
from foundry_lite.infrastructure.adapters import kubernetes_job_code_execution as job_adapter
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    JOB_DIR,
    OUTPUT_DIR,
    RESULT_NAME,
    ContainerCodeExecutionConfig,
    ContainerCommandResult,
    SandboxWorkspace,
    container_command,
)
from foundry_lite.infrastructure.adapters.kubernetes_job_code_execution import (
    KubernetesJobBrokerCommandRunner,
    KubernetesJobBrokerConfig,
    KubernetesJobCodeExecutionAdapter,
    kubernetes_job_broker_config_from_env,
)
from foundry_lite.infrastructure.kubernetes_execution_broker import (
    ExecutionKubernetesRequest,
    ExecutionKubernetesResponse,
    ExecutionKubernetesTransportError,
    InClusterExecutionKubernetesTransport,
    KubernetesExecutionBroker,
    KubernetesExecutionBrokerConfig,
    KubernetesExecutionBrokerResult,
)
from foundry_lite.infrastructure.kubernetes_execution_spec import (
    kubernetes_execution_job_payload,
    parse_kubernetes_execution_command,
)
from foundry_lite_worker import kubernetes_execution_broker as broker_worker
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


@pytest.mark.parametrize(
    "mutation",
    [
        "bad-prefix",
        "missing-image",
        "bad-name",
        "duplicate-security",
        "unknown-flag",
        "duplicate-resource",
        "swap-mismatch",
        "bad-user",
        "bad-cpu",
        "large-cpu",
        "bad-memory-unit",
        "large-memory",
        "bad-pids",
        "large-pids",
        "bad-tmpfs-options",
        "bad-tmpfs-unit",
        "large-tmpfs",
        "bad-file-limit",
        "unequal-file-limit",
        "zero-file-limit",
        "missing-workdir",
        "bad-environment",
        "duplicate-environment",
        "job-mount-writable",
        "output-mount-readonly",
        "duplicate-mount-option",
        "unknown-mount-option",
        "unknown-mount-target",
    ],
)
def test_kubernetes_execution_command_rejects_unsafe_contract_variants(
    mutation: str,
    tmp_path: Path,
) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)
    mutated = _mutate_execution_command(command, mutation)

    with pytest.raises(ValueError):
        parse_kubernetes_execution_command(
            mutated,
            timeout_seconds=600,
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        )


@pytest.mark.parametrize("timeout_seconds", [0, 3601])
def test_kubernetes_execution_command_rejects_timeout_outside_bounds(
    timeout_seconds: float,
    tmp_path: Path,
) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)
    with pytest.raises(ValueError, match="timeout must be between"):
        parse_kubernetes_execution_command(
            command,
            timeout_seconds=timeout_seconds,
            shared_workspace_root=workspace,
            pvc_mount_root=pvc_root,
        )


def test_kubernetes_execution_command_requires_workspace_inside_pvc(tmp_path: Path) -> None:
    pvc_root, workspace, command = _workspace_command(tmp_path)
    other_pvc = tmp_path / "other-pvc"
    other_pvc.mkdir()

    with pytest.raises(ValueError, match="workspace must live"):
        parse_kubernetes_execution_command(
            command,
            timeout_seconds=600,
            shared_workspace_root=workspace,
            pvc_mount_root=other_pvc,
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


def test_execution_broker_http_boundary_returns_success_and_cleanup_statuses() -> None:
    class _Broker:
        def execute(self, payload: Mapping[str, object]) -> KubernetesExecutionBrokerResult:
            assert payload["schemaVersion"] == 1
            return KubernetesExecutionBrokerResult(
                "foundry-lite-python-" + "a" * 32,
                "succeeded",
                0,
                {"executionMode": "kubernetes-job"},
            )

        def cleanup(self, name: str) -> bool:
            return name.endswith("present")

    client = TestClient(create_app(broker=_Broker(), bearer_token="t" * 32))  # type: ignore[arg-type]
    headers = {"authorization": f"Bearer {'t' * 32}"}

    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.post("/v1/executions", json={"schemaVersion": 1}, headers=headers)
    assert response.status_code == 200
    assert response.json()["runtimeEvidence"] == {"executionMode": "kubernetes-job"}
    assert client.delete("/v1/executions/job-present", headers=headers).status_code == 200
    assert client.delete("/v1/executions/job-missing", headers=headers).status_code == 503
    assert client.delete("/v1/executions/job-present").status_code == 401


def test_execution_broker_http_boundary_classifies_validation_and_cleanup_failures() -> None:
    class _Broker:
        def execute(self, payload: Mapping[str, object]) -> KubernetesExecutionBrokerResult:
            del payload
            raise ValueError("private-invalid-detail")

        def cleanup(self, name: str) -> bool:
            if name == "invalid":
                raise ValueError("private-name-detail")
            raise RuntimeError("private-cluster-detail")

    client = TestClient(create_app(broker=_Broker(), bearer_token="t" * 32))  # type: ignore[arg-type]
    headers = {"authorization": f"Bearer {'t' * 32}"}

    execute = client.post("/v1/executions", json={"schemaVersion": 1}, headers=headers)
    invalid = client.delete("/v1/executions/invalid", headers=headers)
    unavailable = client.delete("/v1/executions/unavailable", headers=headers)

    assert (execute.status_code, execute.json()) == (422, {"error": "invalid_execution_request"})
    assert (invalid.status_code, invalid.json()) == (422, {"error": "invalid_execution_name"})
    assert (unavailable.status_code, unavailable.json()) == (503, {"error": "execution_broker_unavailable"})
    assert "private" not in execute.text + invalid.text + unavailable.text


def test_execution_broker_http_boundary_rejects_invalid_json_and_non_object() -> None:
    class _Broker:
        def execute(self, payload: Mapping[str, object]) -> KubernetesExecutionBrokerResult:
            raise AssertionError(payload)

        def cleanup(self, name: str) -> bool:
            raise AssertionError(name)

    client = TestClient(create_app(broker=_Broker(), bearer_token="t" * 32))  # type: ignore[arg-type]
    headers = {"authorization": f"Bearer {'t' * 32}", "content-type": "application/json"}

    assert client.post("/v1/executions", content=b"not-json", headers=headers).status_code == 422
    assert client.post("/v1/executions", content=b"[]", headers=headers).status_code == 422


def test_execution_broker_worker_requires_strong_token() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        create_app(broker=object(), bearer_token="short")  # type: ignore[arg-type]


def test_execution_broker_worker_builds_only_fixed_environment_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[KubernetesExecutionBrokerConfig] = []
    marker = object()
    monkeypatch.setattr(
        broker_worker,
        "KubernetesExecutionBroker",
        lambda config: observed.append(config) or marker,
    )
    monkeypatch.setenv("FOUNDRY_LITE_KUBERNETES_EXECUTION_NAMESPACE", "foundry-qa")
    monkeypatch.setenv("FOUNDRY_LITE_KUBERNETES_EXECUTION_PVC", "foundry-runtime")
    monkeypatch.setenv("FOUNDRY_LITE_CODE_EXECUTION_WORKSPACE_ROOT", str(tmp_path))

    assert broker_worker._broker_from_env() is marker
    assert observed[0].namespace == "foundry-qa"
    assert observed[0].pvc_name == "foundry-runtime"
    assert observed[0].shared_workspace_root == tmp_path
    assert observed[0].pvc_mount_root == Path("/var/data")


def test_execution_broker_worker_main_binds_cluster_service_without_access_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object()
    observed: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(broker_worker, "create_app", lambda: app)
    monkeypatch.setattr(broker_worker.uvicorn, "run", lambda target, **kwargs: observed.append((target, kwargs)))
    monkeypatch.setenv("PORT", "9090")

    broker_worker.main()

    assert observed == [
        (
            app,
            {"host": "0.0.0.0", "port": 9090, "access_log": False, "server_header": False},
        )
    ]


class _BrokerCommandRunner(KubernetesJobBrokerCommandRunner):
    def __init__(
        self,
        responses: list[tuple[int, bytes] | BaseException],
        *,
        workspace: Path,
    ) -> None:
        super().__init__(
            KubernetesJobBrokerConfig(
                endpoint="http://foundry-lite-execution-broker:8080",
                bearer_token="t" * 32,
                shared_workspace_root=workspace,
            )
        )
        self.responses = responses
        self.requests: list[tuple[str, str, bytes | None, float]] = []

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        self.requests.append((method, path, body, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_job_adapter_runner_returns_only_validated_broker_evidence(tmp_path: Path) -> None:
    name = "foundry-lite-python-" + "a" * 32
    runner = _BrokerCommandRunner(
        [
            (
                200,
                json.dumps(
                    {"name": name, "exitCode": 7, "runtimeEvidence": {"executionMode": "kubernetes-job"}}
                ).encode(),
            )
        ],
        workspace=tmp_path,
    )

    result = runner(("kubernetes-job-client", "run", "--name", name), 30, {"SECRET": "not-forwarded"})

    assert result.return_code == 7
    assert result.runtime_evidence == {"executionMode": "kubernetes-job"}
    assert runner.requests[0][:2] == ("POST", "/v1/executions")
    assert runner.requests[0][3] == 45


@pytest.mark.parametrize(
    "response",
    [
        (503, b"{}"),
        (200, b'{"name":"different","exitCode":0,"runtimeEvidence":{}}'),
        (200, b'{"name":"foundry-lite-python-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","exitCode":"0","runtimeEvidence":{}}'),
        (200, b'{"name":"foundry-lite-python-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","exitCode":0,"runtimeEvidence":[]}'),
    ],
)
def test_job_adapter_runner_fails_closed_on_untrusted_broker_response(
    response: tuple[int, bytes],
    tmp_path: Path,
) -> None:
    name = "foundry-lite-python-" + "a" * 32
    runner = _BrokerCommandRunner([response], workspace=tmp_path)

    assert runner(("kubernetes-job-client", "run", "--name", name), 30, {}).return_code == 125


@pytest.mark.parametrize("payload", [b"not-json", b"[]"])
def test_job_adapter_runner_rejects_non_object_json(payload: bytes, tmp_path: Path) -> None:
    name = "foundry-lite-python-" + "a" * 32
    runner = _BrokerCommandRunner([(200, payload)], workspace=tmp_path)

    with pytest.raises(RuntimeError, match="broker (returned invalid JSON|response must be an object)"):
        runner(("kubernetes-job-client", "run", "--name", name), 30, {})


def test_job_adapter_runner_maps_post_timeout_to_sandbox_timeout(tmp_path: Path) -> None:
    name = "foundry-lite-python-" + "a" * 32
    runner = _BrokerCommandRunner([TimeoutError("private-timeout")], workspace=tmp_path)

    with pytest.raises(subprocess.TimeoutExpired):
        runner(("kubernetes-job-client", "run", "--name", name), 30, {})


@pytest.mark.parametrize(("response", "return_code"), [((200, b"{}"), 0), ((404, b"{}"), 0), ((503, b"{}"), 1)])
def test_job_adapter_runner_cleanup_is_bounded(
    response: tuple[int, bytes],
    return_code: int,
    tmp_path: Path,
) -> None:
    runner = _BrokerCommandRunner([response], workspace=tmp_path)
    result = runner(("kubernetes-job-client", "rm", "--force", "job-name"), 10, {})

    assert result.return_code == return_code
    assert runner.requests[0][:2] == ("DELETE", "/v1/executions/job-name")


def test_job_adapter_runner_cleanup_timeout_is_not_reported_as_deleted(tmp_path: Path) -> None:
    runner = _BrokerCommandRunner([TimeoutError()], workspace=tmp_path)

    assert runner(("kubernetes-job-client", "rm", "--force", "job-name"), 10, {}).return_code == 1


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (("other", "run", "--name", "job"), "unsupported Kubernetes execution broker command"),
        (("kubernetes-job-client", "run", "--other", "job"), "missing a name"),
        (("kubernetes-job-client", "run", "--extra", "--name"), "name is incomplete"),
    ],
)
def test_job_adapter_runner_rejects_noncanonical_commands(
    command: tuple[str, ...],
    reason: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match=reason):
        _BrokerCommandRunner([], workspace=tmp_path)(command, 10, {})


@pytest.mark.parametrize(
    "config",
    [
        KubernetesJobBrokerConfig("https://broker.example.test", "t" * 32, Path("/absolute")),
        KubernetesJobBrokerConfig("http://user:pass@broker", "t" * 32, Path("/absolute")),
        KubernetesJobBrokerConfig("http://broker/path", "t" * 32, Path("/absolute")),
        KubernetesJobBrokerConfig("http://broker", "short", Path("/absolute")),
        KubernetesJobBrokerConfig("http://broker", "t" * 32, Path("relative")),
        KubernetesJobBrokerConfig("http://broker", "t" * 32, Path("/absolute"), request_grace_seconds=0),
        KubernetesJobBrokerConfig("http://broker", "t" * 32, Path("/absolute"), request_grace_seconds=61),
    ],
)
def test_job_adapter_runner_rejects_unsafe_broker_configuration(config: KubernetesJobBrokerConfig) -> None:
    with pytest.raises(ValueError):
        KubernetesJobBrokerCommandRunner(config)


def test_job_adapter_env_config_has_internal_defaults() -> None:
    config = kubernetes_job_broker_config_from_env({"FOUNDRY_LITE_CODE_EXECUTION_BROKER_TOKEN": "t" * 32})

    assert config.endpoint == "http://foundry-lite-execution-broker:8080"
    assert config.shared_workspace_root == Path("/var/data/code-execution-workspaces")


def test_job_adapter_http_request_authenticates_bounds_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Response:
        status = 200

        def read(self, size: int) -> bytes:
            assert size == 64 * 1024 + 1
            return b'{"status":"ok"}'

    class _Connection:
        def __init__(self) -> None:
            self.requests: list[tuple[object, ...]] = []
            self.is_closed = False

        def request(self, *args: object, **kwargs: object) -> None:
            self.requests.append((*args, kwargs))

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            self.is_closed = True

    connection = _Connection()
    monkeypatch.setattr(job_adapter, "HTTPConnection", lambda *_args, **_kwargs: connection)
    runner = KubernetesJobBrokerCommandRunner(KubernetesJobBrokerConfig("http://broker:8080", "t" * 32, tmp_path))

    status, payload = runner._request("POST", "/v1/executions", b"{}", 5)

    assert (status, payload) == (200, b'{"status":"ok"}')
    headers = connection.requests[0][-1]
    assert isinstance(headers, dict)
    assert headers["headers"]["authorization"] == f"Bearer {'t' * 32}"
    assert connection.is_closed


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), OSError("private-network")])
def test_job_adapter_http_request_classifies_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: BaseException,
) -> None:
    class _Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            raise failure

        def close(self) -> None:
            pass

    monkeypatch.setattr(job_adapter, "HTTPConnection", lambda *_args, **_kwargs: _Connection())
    runner = KubernetesJobBrokerCommandRunner(KubernetesJobBrokerConfig("http://broker:8080", "t" * 32, tmp_path))

    expected = TimeoutError if isinstance(failure, TimeoutError) else RuntimeError
    with pytest.raises(expected):
        runner._request("POST", "/v1/executions", b"{}", 5)


def test_job_adapter_http_request_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Response:
        status = 200

        def read(self, _size: int) -> bytes:
            return b"x" * (64 * 1024 + 1)

    class _Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(job_adapter, "HTTPConnection", lambda *_args, **_kwargs: _Connection())
    runner = KubernetesJobBrokerCommandRunner(KubernetesJobBrokerConfig("http://broker:8080", "t" * 32, tmp_path))

    with pytest.raises(RuntimeError, match="response is too large"):
        runner._request("GET", "/v1/executions", None, 5)


class _QueuedExecutionTransport:
    def __init__(self, responses: Sequence[ExecutionKubernetesResponse | BaseException]) -> None:
        self.responses = list(responses)
        self.requests: list[ExecutionKubernetesRequest] = []

    def send(self, request: ExecutionKubernetesRequest) -> ExecutionKubernetesResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _broker_config(tmp_path: Path, **overrides: object) -> KubernetesExecutionBrokerConfig:
    values: dict[str, object] = {
        "namespace": "foundry-qa",
        "pvc_name": "foundry-runtime",
        "shared_workspace_root": tmp_path / "pvc" / "code-execution-workspaces",
        "pvc_mount_root": tmp_path / "pvc",
        "poll_interval_seconds": 0.01,
        "observation_grace_seconds": 1,
    }
    values.update(overrides)
    return KubernetesExecutionBrokerConfig(**values)  # type: ignore[arg-type]


def _pending_job(tmp_path: Path) -> tuple[tuple[str, ...], dict[str, object]]:
    pvc_root, workspace, command = _workspace_command(tmp_path)
    spec = parse_kubernetes_execution_command(
        command,
        timeout_seconds=600,
        shared_workspace_root=workspace,
        pvc_mount_root=pvc_root,
    )
    return command, kubernetes_execution_job_payload(spec, namespace="foundry-qa", pvc_name="foundry-runtime")


def test_execution_broker_reports_unknown_when_create_and_reconcile_are_unobservable(tmp_path: Path) -> None:
    command, _job = _pending_job(tmp_path)
    transport = _QueuedExecutionTransport(
        [ExecutionKubernetesTransportError("timeout"), ExecutionKubernetesTransportError("unavailable")]
    )
    broker = KubernetesExecutionBroker(_broker_config(tmp_path), transport=transport)

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.status == "outcome_unknown"
    assert result.exit_code == 125
    assert result.runtime_evidence["reason"] == "create_outcome_unknown"
    assert result.runtime_evidence["resultSha256"] is None


@pytest.mark.parametrize(
    "responses",
    [
        [ExecutionKubernetesResponse(500, b"{}")],
        [ExecutionKubernetesResponse(409, b"{}"), ExecutionKubernetesResponse(500, b"{}")],
    ],
)
def test_execution_broker_rejects_failed_create_or_reconcile(
    responses: list[ExecutionKubernetesResponse],
    tmp_path: Path,
) -> None:
    command, _job = _pending_job(tmp_path)
    broker = KubernetesExecutionBroker(_broker_config(tmp_path), transport=_QueuedExecutionTransport(responses))

    with pytest.raises(RuntimeError, match="execution_job_(create|reconcile)_failed"):
        broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})


def test_execution_broker_reconciles_missing_job_as_unknown(tmp_path: Path) -> None:
    command, _job = _pending_job(tmp_path)
    broker = KubernetesExecutionBroker(
        _broker_config(tmp_path),
        transport=_QueuedExecutionTransport(
            [ExecutionKubernetesResponse(409, b"{}"), ExecutionKubernetesResponse(404, b"{}")]
        ),
    )

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.status == "outcome_unknown"
    assert result.runtime_evidence["reason"] == "create_outcome_unknown"


@pytest.mark.parametrize(
    ("followup", "reason"),
    [
        (ExecutionKubernetesTransportError("unavailable"), "observation_unavailable"),
        (ExecutionKubernetesResponse(503, b"{}"), "observation_failed"),
    ],
)
def test_execution_broker_classifies_observation_loss(
    followup: ExecutionKubernetesResponse | BaseException,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command, job = _pending_job(tmp_path)
    transport = _QueuedExecutionTransport([_response(job, status_code=201), followup])
    monkeypatch.setattr(execution_broker.time, "sleep", lambda _seconds: None)
    broker = KubernetesExecutionBroker(_broker_config(tmp_path), transport=transport)

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.status == "outcome_unknown"
    assert result.runtime_evidence["reason"] == reason


def test_execution_broker_observes_one_pending_refresh_before_transport_loss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command, job = _pending_job(tmp_path)
    transport = _QueuedExecutionTransport(
        [
            _response(job, status_code=201),
            _response(job),
            ExecutionKubernetesTransportError("unavailable"),
        ]
    )
    monkeypatch.setattr(execution_broker.time, "sleep", lambda _seconds: None)
    broker = KubernetesExecutionBroker(_broker_config(tmp_path), transport=transport)

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.runtime_evidence["reason"] == "observation_unavailable"
    assert len(transport.requests) == 3


def test_execution_broker_bounds_observation_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command, job = _pending_job(tmp_path)
    moments = iter((0.0, 602.0, 603.0))
    monkeypatch.setattr(execution_broker.time, "monotonic", lambda: next(moments))
    broker = KubernetesExecutionBroker(
        _broker_config(tmp_path), transport=_QueuedExecutionTransport([_response(job, status_code=201)])
    )

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.status == "outcome_unknown"
    assert result.runtime_evidence["reason"] == "observation_timeout"


def _queued_response(payload: Mapping[str, object], *, status_code: int = 200) -> ExecutionKubernetesResponse:
    return ExecutionKubernetesResponse(status_code, json.dumps(payload).encode())


@pytest.mark.parametrize(
    ("job_status", "pods", "expected_exit", "expected_reason"),
    [
        ("failed", ExecutionKubernetesResponse(503, b"{}"), 125, "pod_status_unavailable"),
        ("failed", _queued_response({"items": []}), 125, "pod_status_missing"),
        ("failed", _queued_response({"items": [{"status": {}}]}), 125, "pod_exit_missing"),
        ("succeeded", _queued_response({"items": []}), 0, "job_succeeded"),
        (
            "failed",
            _queued_response(
                {"items": [{"status": {"containerStatuses": [{"state": {"terminated": {"exitCode": 9}}}]}}]}
            ),
            9,
            "container_terminated",
        ),
    ],
)
def test_execution_broker_classifies_terminal_pod_evidence(
    job_status: str,
    pods: ExecutionKubernetesResponse,
    expected_exit: int,
    expected_reason: str,
    tmp_path: Path,
) -> None:
    command, job = _pending_job(tmp_path)
    job["status"] = {job_status: 1}
    broker = KubernetesExecutionBroker(
        _broker_config(tmp_path),
        transport=_QueuedExecutionTransport([_response(job, status_code=201), pods]),
    )

    result = broker.execute({"schemaVersion": 1, "command": list(command), "timeoutSeconds": 600})

    assert result.status == job_status
    assert result.exit_code == expected_exit
    assert result.runtime_evidence["reason"] == expected_reason


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schemaVersion": 2, "command": [], "timeoutSeconds": 1},
        {"schemaVersion": 1, "command": "run", "timeoutSeconds": 1},
        {"schemaVersion": 1, "command": [1], "timeoutSeconds": 1},
        {"schemaVersion": 1, "command": ["x" * 5000], "timeoutSeconds": 1},
        {"schemaVersion": 1, "command": ["run"], "timeoutSeconds": True},
        {"schemaVersion": 1, "command": ["run"], "timeoutSeconds": float("inf")},
    ],
)
def test_execution_broker_rejects_invalid_wire_requests(payload: Mapping[str, object], tmp_path: Path) -> None:
    broker = KubernetesExecutionBroker(_broker_config(tmp_path), transport=_QueuedExecutionTransport([]))
    with pytest.raises(ValueError):
        broker.execute(payload)


@pytest.mark.parametrize(
    "config",
    [
        KubernetesExecutionBrokerConfig("", "pvc", Path("/workspace"), Path("/pvc")),
        KubernetesExecutionBrokerConfig("ns", "", Path("/workspace"), Path("/pvc")),
        KubernetesExecutionBrokerConfig("ns", "pvc", Path("relative"), Path("/pvc")),
        KubernetesExecutionBrokerConfig("ns", "pvc", Path("/workspace"), Path("relative")),
        KubernetesExecutionBrokerConfig("ns", "pvc", Path("/workspace"), Path("/pvc"), 0),
        KubernetesExecutionBrokerConfig("ns", "pvc", Path("/workspace"), Path("/pvc"), 6),
        KubernetesExecutionBrokerConfig("ns", "pvc", Path("/workspace"), Path("/pvc"), 1, 0),
        KubernetesExecutionBrokerConfig("ns", "pvc", Path("/workspace"), Path("/pvc"), 1, 61),
    ],
)
def test_execution_broker_rejects_unsafe_config(config: KubernetesExecutionBrokerConfig) -> None:
    with pytest.raises(ValueError):
        KubernetesExecutionBroker(config, transport=_QueuedExecutionTransport([]))


@pytest.mark.parametrize("status", [200, 202, 404])
def test_execution_broker_cleanup_accepts_terminal_delete_status(status: int, tmp_path: Path) -> None:
    transport = _QueuedExecutionTransport([ExecutionKubernetesResponse(status, b"{}")])
    broker = KubernetesExecutionBroker(_broker_config(tmp_path), transport=transport)
    name = "foundry-lite-python-" + "a" * 32

    assert broker.cleanup(name)
    assert transport.requests[0].method == "DELETE"


def test_incluster_execution_transport_authenticates_bounds_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("service-account-token", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")

    class _Response:
        status = 200

        def read(self, size: int) -> bytes:
            assert size == 1025
            return b"{}"

    class _Connection:
        def __init__(self) -> None:
            self.arguments: tuple[object, ...] | None = None
            self.is_closed = False

        def request(self, *args: object, **kwargs: object) -> None:
            self.arguments = (*args, kwargs)

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            self.is_closed = True

    connection = _Connection()
    monkeypatch.setattr(execution_broker.ssl, "create_default_context", lambda **_kwargs: object())
    monkeypatch.setattr(execution_broker, "HTTPSConnection", lambda *_args, **_kwargs: connection)
    transport = InClusterExecutionKubernetesTransport(host="kubernetes", port=443, token_path=token, ca_path=ca)

    response = transport.send(
        ExecutionKubernetesRequest(
            "GET",
            "/apis/batch/v1/namespaces/foundry-qa/jobs",
            max_response_bytes=1024,
        )
    )

    assert response == ExecutionKubernetesResponse(200, b"{}")
    assert connection.arguments is not None
    assert connection.arguments[-1]["headers"]["authorization"] == "Bearer service-account-token"
    assert connection.is_closed


def test_incluster_execution_transport_rejects_path_token_size_and_network_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("test-ca", encoding="utf-8")
    monkeypatch.setattr(execution_broker.ssl, "create_default_context", lambda **_kwargs: object())

    class _Response:
        status = 200

        def read(self, _size: int) -> bytes:
            return b"xx"

    class _Connection:
        def __init__(self, failure: BaseException | None = None) -> None:
            self.failure = failure

        def request(self, *_args: object, **_kwargs: object) -> None:
            if self.failure is not None:
                raise self.failure

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(execution_broker, "HTTPSConnection", lambda *_args, **_kwargs: _Connection())
    transport = InClusterExecutionKubernetesTransport(host="kubernetes", token_path=token, ca_path=ca)

    with pytest.raises(ExecutionKubernetesTransportError, match="path_not_allowed"):
        transport.send(ExecutionKubernetesRequest("GET", "/api/v1/../secrets"))
    with pytest.raises(ExecutionKubernetesTransportError, match="service_account_token_missing"):
        transport.send(ExecutionKubernetesRequest("GET", "/api/v1/namespaces/foundry-qa/pods"))

    token.write_text("token", encoding="utf-8")
    with pytest.raises(ExecutionKubernetesTransportError, match="response_too_large"):
        transport.send(ExecutionKubernetesRequest("GET", "/api/v1/namespaces/foundry-qa/pods", max_response_bytes=1))

    monkeypatch.setattr(
        execution_broker,
        "HTTPSConnection",
        lambda *_args, **_kwargs: _Connection(OSError("private-network")),
    )
    transport = InClusterExecutionKubernetesTransport(host="kubernetes", token_path=token, ca_path=ca)
    with pytest.raises(ExecutionKubernetesTransportError, match="unavailable"):
        transport.send(ExecutionKubernetesRequest("GET", "/api/v1/namespaces/foundry-qa/pods"))

    monkeypatch.setattr(
        execution_broker,
        "HTTPSConnection",
        lambda *_args, **_kwargs: _Connection(TimeoutError("private-timeout")),
    )
    transport = InClusterExecutionKubernetesTransport(host="kubernetes", token_path=token, ca_path=ca)
    with pytest.raises(ExecutionKubernetesTransportError, match="timeout"):
        transport.send(ExecutionKubernetesRequest("GET", "/api/v1/namespaces/foundry-qa/pods"))

    with pytest.raises(ValueError, match="service host is required"):
        InClusterExecutionKubernetesTransport(host="", token_path=token, ca_path=ca)


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


def _mutate_execution_command(command: Sequence[str], mutation: str) -> tuple[str, ...]:
    values = list(command)
    image_index = next(index for index, value in enumerate(values) if "@sha256:" in value)
    replacements = {
        "bad-name": ("--name", "invalid-name"),
        "swap-mismatch": ("--memory-swap=", "--memory-swap=256m"),
        "bad-user": ("--user=", "--user=65532"),
        "bad-cpu": ("--cpus=", "--cpus=not-number"),
        "large-cpu": ("--cpus=", "--cpus=5"),
        "bad-memory-unit": ("--memory=", "--memory=512"),
        "large-memory": ("--memory=", "--memory=99999m"),
        "bad-pids": ("--pids-limit=", "--pids-limit=bad"),
        "large-pids": ("--pids-limit=", "--pids-limit=999"),
        "bad-tmpfs-options": ("--tmpfs=", "--tmpfs=/tmp:rw,size=64m"),
        "bad-tmpfs-unit": ("--tmpfs=", "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64"),
        "large-tmpfs": ("--tmpfs=", "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=2048m"),
        "bad-file-limit": ("--ulimit=fsize=", "--ulimit=fsize=100"),
        "unequal-file-limit": ("--ulimit=fsize=", "--ulimit=fsize=100:101"),
        "zero-file-limit": ("--ulimit=fsize=", "--ulimit=fsize=0:0"),
    }
    if mutation in replacements:
        prefix, replacement = replacements[mutation]
        index = next(index for index, value in enumerate(values) if value.startswith(prefix))
        if mutation == "bad-name":
            index += 1
        if prefix == "--tmpfs=":
            replacement = values[index].split(":", 1)[0] + ":" + replacement.split(":", 1)[1]
        values[index] = replacement
        return tuple(values)
    if mutation == "bad-prefix":
        values[0] = "other-client"
    elif mutation == "missing-image":
        values = values[:image_index]
    elif mutation == "duplicate-security":
        values.insert(image_index, "--network=none")
    elif mutation == "unknown-flag":
        values.insert(image_index, "--privileged")
    elif mutation == "duplicate-resource":
        values.insert(image_index, next(value for value in values if value.startswith("--cpus=")))
    elif mutation == "missing-workdir":
        values.remove("--workdir=/sandbox-job")
    elif mutation in {"bad-environment", "duplicate-environment"}:
        env_index = values.index("-i") + 1
        if mutation == "bad-environment":
            values[env_index] = "INVALID"
        else:
            values.insert(env_index + 1, values[env_index])
    else:
        mount_index = next(
            index + 1
            for index, value in enumerate(values[:-1])
            if value == "--mount"
            and (
                (mutation == "job-mount-writable" and f"target={JOB_DIR}" in values[index + 1])
                or (mutation != "job-mount-writable" and f"target={OUTPUT_DIR}/{RESULT_NAME}" in values[index + 1])
            )
        )
        mount = values[mount_index]
        if mutation == "job-mount-writable":
            mount = mount.replace(",readonly", "")
        elif mutation == "output-mount-readonly":
            mount += ",readonly"
        elif mutation == "duplicate-mount-option":
            mount += ",readonly,readonly"
        elif mutation == "unknown-mount-option":
            mount += ",unknown"
        elif mutation == "unknown-mount-target":
            mount = mount.replace(f"target={OUTPUT_DIR}/{RESULT_NAME}", "target=/unknown")
        values[mount_index] = mount
    return tuple(values)


def _response(payload: Mapping[str, object], *, status_code: int = 200) -> ExecutionKubernetesResponse:
    return ExecutionKubernetesResponse(status_code, json.dumps(payload).encode())
