from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.operations import inject_macmini_fault as subject
from scripts.operations.inject_macmini_fault import (
    _isolated_internal_selector,
    _validate_duration,
)


def test_fault_network_partition_removes_api_from_existing_internal_allow_policy() -> None:
    original = {"matchExpressions": [{"key": "foundry-lite.io/execution-sandbox", "operator": "DoesNotExist"}]}

    isolated = _isolated_internal_selector(original)

    assert isolated["matchExpressions"][-1] == {
        "key": "app.kubernetes.io/component",
        "operator": "NotIn",
        "values": ["api"],
    }
    assert original == {"matchExpressions": [{"key": "foundry-lite.io/execution-sandbox", "operator": "DoesNotExist"}]}


def test_fault_duration_is_bounded() -> None:
    _validate_duration(60)
    with pytest.raises(ValueError, match="duration_out_of_range"):
        _validate_duration(61)


def test_network_partition_restores_original_allow_selector_when_fault_wait_fails(
    monkeypatch,
) -> None:
    original = {"matchExpressions": [{"key": "sandbox", "operator": "DoesNotExist"}]}
    calls: list[tuple[str, ...]] = []

    def kubectl(_args: Namespace, operation: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(operation)
        if operation[:3] == ("get", "networkpolicy", "foundry-lite-internal"):
            return subprocess.CompletedProcess(
                operation,
                0,
                json.dumps({"spec": {"podSelector": original}}).encode(),
                b"",
            )
        return subprocess.CompletedProcess(operation, 0, b"", b"")

    monkeypatch.setattr(subject, "_kubectl", kubectl)
    monkeypatch.setattr(
        subject.time,
        "sleep",
        lambda _duration: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(RuntimeError, match="injected"):
        subject._network_partition(Namespace(namespace="foundry-qa", duration_seconds=1))

    patches = [operation for operation in calls if operation[:2] == ("patch", "networkpolicy")]
    assert len(patches) == 2
    assert '"values":["api"]' in patches[0][-1]
    assert '"values":["api"]' not in patches[1][-1]


def test_dependency_fault_scales_back_up_when_fault_wait_fails(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda _args, operation, _timeout: (
            calls.append(operation) or subprocess.CompletedProcess(operation, 0, b"", b"")
        ),
    )
    monkeypatch.setattr(
        subject.time,
        "sleep",
        lambda _duration: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(RuntimeError, match="injected"):
        subject._dependency_fault(Namespace(duration_seconds=1), "postgresql")

    assert ("scale", "statefulset", "foundry-lite-postgresql", "--replicas=0") in calls
    assert ("scale", "statefulset", "foundry-lite-postgresql", "--replicas=1") in calls


def test_signal_fault_requires_observed_container_restart(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_first_pod", lambda _args, _selector: "worker-1")
    snapshots = iter(
        (
            {
                "containerId": "docker://" + ("a" * 64),
                "restartCount": 3,
                "isReady": True,
                "startedAt": "2026-08-20T00:00:00Z",
                "lastTermination": {"reason": None, "exitCode": None, "signal": None, "finishedAt": None},
            },
            {
                "containerId": "docker://" + ("b" * 64),
                "restartCount": 4,
                "isReady": True,
                "startedAt": "2026-08-20T00:00:01Z",
                "lastTermination": {
                    "reason": "Error",
                    "exitCode": 137,
                    "signal": 9,
                    "finishedAt": "2026-08-20T00:00:01Z",
                },
            },
        )
    )
    monkeypatch.setattr(subject, "_container_lifecycle_snapshot", lambda _args, _pod, _container: next(snapshots))
    monkeypatch.setattr(
        subject,
        "_pid_one_identity",
        lambda _args, _pod, _container: {"observed": True, "pid": 1, "commandSha256": "a" * 64},
    )
    runtime_targets = iter(
        (
            {
                "observed": True,
                "runtime": "docker",
                "containerId": "docker://" + ("a" * 64),
                "runtimeContainerId": "a" * 64,
                "hostPid": 101,
            },
            {
                "observed": True,
                "runtime": "docker",
                "containerId": "docker://" + ("b" * 64),
                "runtimeContainerId": "b" * 64,
                "hostPid": 102,
            },
        )
    )
    monkeypatch.setattr(subject, "_runtime_container_target", lambda _snapshot: next(runtime_targets))
    kill_calls: list[tuple[dict[str, object], str]] = []
    monkeypatch.setattr(
        subject,
        "_runtime_docker_kill",
        lambda target, signal: (
            kill_calls.append((target, signal)) or subprocess.CompletedProcess(("docker", "kill"), 0, b"", b"")
        ),
    )
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda _args, operation, _timeout: subprocess.CompletedProcess(operation, 0, b"", b""),
    )

    receipt = subject._signal_fault(
        Namespace(),
        subject._POD_SELECTORS["worker-pod"],
        "worker",
        "KILL",
        "deployment/foundry-lite-worker-action",
    )

    assert receipt["status"] == "passed"
    assert receipt["signal"] == "SIGKILL"
    assert receipt["signalTransport"] == "docker-runtime"
    assert receipt["runtimeKillAccepted"] is True
    assert receipt["containerRestartObserved"] is True
    assert receipt["terminationSignalObserved"] is True
    assert receipt["gracefulTerminationObserved"] is False
    assert receipt["terminationContractSatisfied"] is True
    assert receipt["terminationMode"] == "signal"
    assert receipt["containerIdChanged"] is True
    assert receipt["targetProcessBefore"] == {"observed": True, "pid": 1, "commandSha256": "a" * 64}
    assert kill_calls == [
        (
            {
                "observed": True,
                "runtime": "docker",
                "containerId": "docker://" + ("a" * 64),
                "runtimeContainerId": "a" * 64,
                "hostPid": 101,
            },
            "KILL",
        )
    ]


def test_sigterm_accepts_graceful_completed_container_restart(monkeypatch) -> None:
    _stub_signal_fault(
        monkeypatch,
        signal_name="TERM",
        last_termination={
            "reason": "Completed",
            "exitCode": 0,
            "signal": None,
            "finishedAt": "2026-08-21T10:38:35Z",
        },
    )

    receipt = subject._signal_fault(
        Namespace(),
        subject._POD_SELECTORS["api-pod"],
        "api",
        "TERM",
        "deployment/foundry-lite",
    )

    assert receipt["status"] == "passed"
    assert receipt["terminationSignalObserved"] is False
    assert receipt["gracefulTerminationObserved"] is True
    assert receipt["terminationContractSatisfied"] is True
    assert receipt["terminationMode"] == "graceful"


def test_sigkill_does_not_accept_graceful_completed_termination(monkeypatch) -> None:
    _stub_signal_fault(
        monkeypatch,
        signal_name="KILL",
        last_termination={
            "reason": "Completed",
            "exitCode": 0,
            "signal": None,
            "finishedAt": "2026-08-21T10:38:35Z",
        },
    )

    receipt = subject._signal_fault(
        Namespace(),
        subject._POD_SELECTORS["worker-pod"],
        "worker",
        "KILL",
        "deployment/foundry-lite-worker-action",
    )

    assert receipt["status"] == "failed"
    assert receipt["terminationContractSatisfied"] is False
    assert receipt["terminationMode"] == "unproven"


def _stub_signal_fault(
    monkeypatch,
    *,
    signal_name: str,
    last_termination: dict[str, object],
) -> None:
    before = {
        "containerId": "docker://" + ("a" * 64),
        "restartCount": 0,
        "isReady": True,
        "startedAt": "2026-08-21T10:00:00Z",
        "lastTermination": {"reason": None, "exitCode": None, "signal": None, "finishedAt": None},
    }
    after = {
        "containerId": "docker://" + ("b" * 64),
        "restartCount": 1,
        "isReady": True,
        "startedAt": "2026-08-21T10:38:36Z",
        "lastTermination": last_termination,
    }
    monkeypatch.setattr(subject, "_first_pod", lambda _args, _selector: "target-1")
    monkeypatch.setattr(subject, "_container_lifecycle_snapshot", lambda *_args: before)
    monkeypatch.setattr(subject, "_wait_for_container_restart", lambda *_args: after)
    monkeypatch.setattr(
        subject,
        "_pid_one_identity",
        lambda *_args: {"observed": True, "pid": 1, "commandSha256": "a" * 64},
    )
    monkeypatch.setattr(
        subject,
        "_runtime_container_target",
        lambda snapshot: {
            "observed": True,
            "runtime": "docker",
            "containerId": snapshot["containerId"],
            "runtimeContainerId": str(snapshot["containerId"]).removeprefix("docker://"),
            "hostPid": 101 if snapshot is before else 102,
        },
    )
    monkeypatch.setattr(
        subject,
        "_runtime_docker_kill",
        lambda _target, observed_signal: subprocess.CompletedProcess(
            ("docker", "kill", "--signal", observed_signal),
            0 if observed_signal == signal_name else 1,
            b"",
            b"",
        ),
    )
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda _args, operation, _timeout: subprocess.CompletedProcess(operation, 0, b"", b""),
    )


def test_runtime_signal_target_fails_closed_when_kubernetes_does_not_report_docker_id() -> None:
    target = subject._runtime_container_target({"containerId": "containerd://" + ("a" * 64)})

    assert target["observed"] is False
    assert subject._runtime_docker_kill(target, "KILL").returncode == 1


def test_worker_oom_always_restores_original_command(monkeypatch) -> None:
    original = ("python", "-m", "worker")
    patches: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        subject,
        "_named_deployment_snapshot",
        lambda _args, _deployment: {
            "spec": {"template": {"spec": {"containers": [{"name": "worker", "command": list(original)}]}}}
        },
    )
    monkeypatch.setattr(
        subject,
        "_patch_deployment_command",
        lambda _args, _deployment, command, _reason: patches.append(command),
    )
    monkeypatch.setattr(subject, "_wait_for_oom_killed", lambda _args, _selector, _duration: True)
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda _args, operation, _timeout: subprocess.CompletedProcess(operation, 0, b"", b""),
    )

    receipt = subject._worker_oom_fault(Namespace(duration_seconds=10))

    assert receipt["status"] == "passed"
    assert patches[-1] == original
    assert receipt["oomKilledObserved"] is True


def test_network_netem_restores_qdisc_when_wait_fails(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subject, "_require_clean_network_qdisc", lambda: None)
    monkeypatch.setattr(
        subject,
        "_colima_root_command",
        lambda operation, _timeout: calls.append(operation) or subprocess.CompletedProcess(operation, 0, b"", b""),
    )
    monkeypatch.setattr(
        subject.time,
        "sleep",
        lambda _duration: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(RuntimeError, match="injected"):
        subject._network_netem_fault(Namespace(duration_seconds=1), ("loss", "20%"))

    assert calls[-1] == ("tc", "qdisc", "del", "dev", "cni0", "root")


def test_dns_fault_rules_cover_udp_and_tcp() -> None:
    rules = subject._dns_reject_rules()

    assert {rule[3] for rule in rules} == {"udp", "tcp"}
    assert all("53" in rule for rule in rules)


def test_invalid_image_fault_restores_exact_digest_after_rejected_rollout(
    monkeypatch,
) -> None:
    original = "ghcr.io/ludia8888/foundry-lite-api@sha256:" + ("a" * 64)
    snapshots = iter(
        (
            _deployment(original, 2),
            _deployment(subject._invalid_image_reference(original), 2),
            _deployment(original, 2),
        )
    )
    calls: list[tuple[str, ...]] = []

    def kubectl(_args: Namespace, operation: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(operation)
        if operation[:3] == ("get", "deployment", "foundry-lite"):
            return subprocess.CompletedProcess(operation, 0, json.dumps(next(snapshots)).encode(), b"")
        if operation[:2] == ("rollout", "status") and operation[-1] == "--timeout=30s":
            return subprocess.CompletedProcess(operation, 1, b"", b"timed out")
        return subprocess.CompletedProcess(operation, 0, b"", b"")

    monkeypatch.setattr(subject, "_kubectl", kubectl)

    receipt = subject._invalid_image_fault(Namespace())

    assert receipt["status"] == "passed"
    assert receipt["invalidRevisionRejected"] is True
    assert calls[-3] == (
        "set",
        "image",
        "deployment/foundry-lite",
        f"api={original}",
        "--field-manager=helm",
    )
    assert receipt["fieldManager"] == "helm"


def test_rolling_restart_preserves_exact_image_digest(monkeypatch) -> None:
    original = "ghcr.io/ludia8888/foundry-lite-api@sha256:" + ("e" * 64)
    snapshots = iter((_deployment(original, 2), _deployment(original, 2)))
    monkeypatch.setattr(subject, "_deployment_snapshot", lambda _args: next(snapshots))
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda _args, operation, _timeout: subprocess.CompletedProcess(operation, 0, b"", b""),
    )

    receipt = subject._rolling_restart_fault(Namespace())

    assert receipt["status"] == "passed"
    assert receipt["imageDigestUnchanged"] is True
    assert receipt["sameDigestRollingRestart"] is True


def test_bad_config_is_rejected_without_helm_revision_or_image_change(
    monkeypatch,
) -> None:
    original = "ghcr.io/ludia8888/foundry-lite-api@sha256:" + ("f" * 64)
    statuses = iter(
        (
            {"version": 8, "info": {"status": "deployed"}},
            {"version": 8, "info": {"status": "deployed"}},
        )
    )
    protected_values = {
        "global": {"protectedProfile": True},
        "secrets": {
            "applicationExistingSecret": "foundry-lite-application",
            "migrationExistingSecret": "foundry-lite-migration",
        },
    }
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(subject, "_deployment_snapshot", lambda _args: _deployment(original, 2))
    monkeypatch.setattr(subject, "_helm_status", lambda _args: next(statuses))
    monkeypatch.setattr(subject, "_helm_values", lambda _args: protected_values)
    monkeypatch.setattr(
        subject,
        "_recover_helm_release",
        lambda *_args: {"rollbackPerformed": False, "rollbackReturnCode": None, "originalValuesRestored": True},
    )
    monkeypatch.setattr(
        subject,
        "_command",
        lambda command, _timeout: (
            commands.append(command) or subprocess.CompletedProcess(command, 1, b"", b"protected config rejected")
        ),
    )

    receipt = subject._bad_config_fault(Namespace(helm="helm", namespace="foundry-qa"))

    assert receipt["status"] == "passed"
    assert receipt["invalidProtectedConfigRejected"] is True
    assert receipt["helmValuesRestored"] is True
    assert "secrets.applicationExistingSecret=foundry-lite-migration" in commands[0]


def test_migration_failure_uses_atomic_helm_and_keeps_live_image(monkeypatch) -> None:
    original = "ghcr.io/ludia8888/foundry-lite-api@sha256:" + ("b" * 64)
    created: list[dict[str, object]] = []
    deleted: list[tuple[str, str]] = []
    helm_statuses = iter(
        (
            {"version": 2, "info": {"status": "deployed"}},
            {"version": 2, "info": {"status": "deployed"}},
        )
    )
    protected_values = {
        "global": {"protectedProfile": True},
        "secrets": {
            "applicationExistingSecret": "foundry-lite-application",
            "migrationExistingSecret": "foundry-lite-migration",
        },
    }
    monkeypatch.setattr(subject, "_deployment_snapshot", lambda _args: _deployment(original, 2))
    monkeypatch.setattr(subject, "_helm_status", lambda _args: next(helm_statuses))
    monkeypatch.setattr(subject, "_helm_values", lambda _args: protected_values)
    monkeypatch.setattr(
        subject,
        "_recover_helm_release",
        lambda *_args: {"rollbackPerformed": False, "rollbackReturnCode": None, "originalValuesRestored": True},
    )
    monkeypatch.setattr(
        subject,
        "_create_fault_resource",
        lambda _args, value, _reason: created.append(value),
    )
    monkeypatch.setattr(
        subject,
        "_delete_fault_resource",
        lambda _args, kind, name, _reason: deleted.append((kind, name)),
    )
    monkeypatch.setattr(
        subject,
        "_delete_fault_resource_optional",
        lambda _args, kind, name: deleted.append((kind, name)),
    )
    monkeypatch.setattr(
        subject,
        "_run_faulting_helm_upgrade",
        lambda _args, _name: subprocess.CompletedProcess((), 1, b"", b"redacted migration failure"),
    )

    receipt = subject._migration_failure_fault(Namespace(run_id="enterprise-qa"))

    assert receipt["status"] == "passed"
    assert receipt["rawFailureLogStored"] is False
    assert created[0]["kind"] == "Secret"
    assert created[0]["immutable"] is True
    name = subject._fault_resource_name("migration", "enterprise-qa")
    assert deleted == [("job", "foundry-lite-migrate-3"), ("secret", name)]


def test_verified_rollback_resource_is_controller_owned_and_immutable() -> None:
    payload = subject._foundry_deployment(
        "qa-rollback",
        "a" * 40,
        "ghcr.io/ludia8888/foundry-lite-api",
        "rollback",
        "qa-old-live",
    )

    spec = payload["spec"]
    assert isinstance(spec, dict)
    assert spec["operation"] == "rollback"
    assert spec["rollbackTargetDeployId"] == "qa-old-live"
    assert spec["commitId"] == "a" * 40
    assert len(str(spec["idempotencyKeyHash"])) == 64
    assert "imageDigest" not in spec


def test_faulting_helm_upgrade_reuses_values_and_is_atomic(tmp_path: Path, monkeypatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    (qa_root / "repo" / "deploy" / "helm" / "foundry-lite").mkdir(parents=True)
    captured: list[tuple[str, ...]] = []
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(
        subject,
        "_command",
        lambda command, _timeout: (
            captured.append(command) or subprocess.CompletedProcess(command, 1, b"", b"migration failed")
        ),
    )

    result = subject._run_faulting_helm_upgrade(
        Namespace(helm=str(qa_root / "bin" / "helm"), namespace="foundry-qa"),
        "foundry-lite-fault-migration",
    )

    assert result.returncode == 1
    assert "--reuse-values" in captured[0]
    assert "--atomic" in captured[0]
    assert "secrets.migrationExistingSecret=foundry-lite-fault-migration" in captured[0]


def test_bounded_command_classifies_timeout_without_losing_receipt_path(monkeypatch) -> None:
    monkeypatch.setattr(
        subject,
        "_command",
        lambda command, timeout: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, timeout, output=b"bounded", stderr=b"redacted")
        ),
    )

    result = subject._bounded_command(("helm", "upgrade"), 420)

    assert result.returncode == 124
    assert result.stdout == b"bounded"


def test_helm_recovery_rolls_pending_release_back_to_exact_pre_fault_values(monkeypatch) -> None:
    expected = {"global": {"protectedProfile": True}, "auth": {"profile": "header-trust"}}
    statuses = iter(
        (
            {"version": 25, "info": {"status": "pending-rollback"}},
            {"version": 26, "info": {"status": "deployed"}},
        )
    )
    values = iter(({"auth": {"profile": "local"}}, expected))
    rollbacks: list[int] = []
    monkeypatch.setattr(subject, "_helm_status_optional", lambda _args: next(statuses))
    monkeypatch.setattr(subject, "_helm_values_optional", lambda _args: next(values))
    monkeypatch.setattr(
        subject,
        "_helm_rollback",
        lambda _args, revision: (
            rollbacks.append(revision) or subprocess.CompletedProcess(("helm", "rollback"), 0, b"", b"")
        ),
    )

    receipt = subject._recover_helm_release(Namespace(), 22, expected, False)

    assert rollbacks == [22]
    assert receipt["rollbackPerformed"] is True
    assert receipt["rollbackReturnCode"] == 0
    assert receipt["originalValuesRestored"] is True


def test_pvc_disk_pressure_stops_at_threshold_and_cleans_resources(monkeypatch) -> None:
    original = "ghcr.io/ludia8888/foundry-lite-api@sha256:" + ("c" * 64)
    created: list[dict[str, object]] = []
    cleaned: list[str] = []
    disk_samples = iter((10_000_000, 9_900_000, 9_900_001, 9_999_000))
    sleeps: list[float] = []
    monkeypatch.setattr(subject, "_deployment_snapshot", lambda _args: _deployment(original, 2))
    monkeypatch.setattr(
        subject,
        "_create_fault_resource",
        lambda _args, value, _reason: created.append(value),
    )
    monkeypatch.setattr(subject, "_cleanup_disk_fault", lambda _args, name: cleaned.append(name))
    monkeypatch.setattr(subject, "_colima_available_kib", lambda: next(disk_samples))
    monkeypatch.setattr(subject.time, "sleep", sleeps.append)

    def kubectl(_args: Namespace, operation: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        output = json.dumps({"bytesWritten": 112 * 1024 * 1024}).encode() if operation[0] == "logs" else b""
        return subprocess.CompletedProcess(operation, 0, output, b"")

    monkeypatch.setattr(subject, "_kubectl", kubectl)

    receipt = subject._pvc_disk_pressure_fault(Namespace(run_id="enterprise-qa"))

    assert receipt["status"] == "passed"
    assert receipt["logicalUsagePercent"] == 87.5
    assert receipt["diskPressureAlert"] is True
    assert receipt["cleanupObserved"] is True
    assert receipt["cleanupObservedAvailableKiB"] == 9_999_000
    assert receipt["colimaDiskReclaimObserved"] is True
    assert receipt["existingMacHostPathFilled"] is False
    assert created[0]["spec"]["resources"]["requests"]["storage"] == "128Mi"  # type: ignore[index]
    assert created[1]["kind"] == "Job"
    assert cleaned == [subject._fault_resource_name("disk", "enterprise-qa")]
    assert sleeps == [subject._DISK_RECLAIM_INTERVAL_SECONDS]


def test_colima_disk_inventory_reads_the_pvc_data_filesystem(monkeypatch) -> None:
    captured: list[tuple[str, ...]] = []
    output = b"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vdb1 100 20 80 20% /var/lib/rancher\n"

    def command(operation: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        captured.append(operation)
        return subprocess.CompletedProcess(operation, 0, output, b"")

    monkeypatch.setattr(subject, "_command", command)

    assert subject._colima_available_kib() == 80
    assert captured[0][-1] == "/var/lib/rancher/k3s/storage"


def test_fault_command_prepends_private_tools_for_colima_restart(tmp_path: Path, monkeypatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    observed: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.update({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("scripts.operations.macmini_qa_guard.QA_ROOT", qa_root)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(subject.subprocess, "run", fake_run)

    result = subject._command(("colima", "start", "foundry-qa"), 600)

    assert result.returncode == 0
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["PATH"].split(":") == [
        str(qa_root / "bin"),
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
    ]


def test_workload_pod_inventory_excludes_completed_and_failed_jobs() -> None:
    payload = {
        "items": [
            {
                "metadata": {
                    "name": "api-1",
                    "ownerReferences": [{"kind": "ReplicaSet"}],
                }
            },
            {
                "metadata": {
                    "name": "postgres-0",
                    "ownerReferences": [{"kind": "StatefulSet"}],
                }
            },
            {"metadata": {"name": "migration-1", "ownerReferences": [{"kind": "Job"}]}},
            {
                "metadata": {
                    "name": "oauth-failed",
                    "ownerReferences": [{"kind": "Job"}],
                }
            },
        ]
    }

    assert subject._workload_pod_names(payload) == ("api-1", "postgres-0")


def test_disk_fault_cleans_pvc_when_job_creation_fails(monkeypatch) -> None:
    resources = subject._disk_pressure_resources("fault-disk", "registry/image@sha256:" + ("d" * 64))
    created: list[str] = []
    deleted: list[tuple[str, str]] = []

    def create(_args: Namespace, payload: dict[str, object], _reason: str) -> None:
        created.append(str(payload["kind"]))
        if payload["kind"] == "Job":
            raise RuntimeError("job create failed")

    monkeypatch.setattr(subject, "_create_fault_resource", create)
    monkeypatch.setattr(
        subject,
        "_delete_fault_resource",
        lambda _args, kind, name, _reason: deleted.append((kind, name)),
    )

    with pytest.raises(RuntimeError, match="job create failed"):
        subject._create_disk_fault_resources(Namespace(), "fault-disk", resources)

    assert created == ["PersistentVolumeClaim", "Job"]
    assert deleted == [("pvc", "fault-disk")]


def _deployment(image: str, available_replicas: int) -> dict[str, object]:
    return {
        "spec": {"template": {"spec": {"containers": [{"name": "api", "image": image}]}}},
        "status": {"availableReplicas": available_replicas},
    }
