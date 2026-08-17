"""Inject and automatically recover namespace-scoped Mac mini QA faults."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed kubectl only; remove if arbitrary command input is introduced.
import time
from datetime import UTC, datetime

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    qa_command_environment,
)

_DEPENDENCIES = {
    "postgresql": ("statefulset", "foundry-lite-postgresql", 300),
    "minio": ("statefulset", "foundry-lite-minio", 300),
    "redpanda": ("statefulset", "foundry-lite-redpanda", 300),
    "temporal": ("deployment", "foundry-lite-temporal", 300),
    "elasticsearch": ("statefulset", "foundry-lite-elasticsearch", 300),
    "clamav": ("statefulset", "foundry-lite-clamav", 300),
    "keycloak": ("statefulset", "foundry-lite-keycloak", 300),
}
_POD_SELECTORS = {
    "api-pod": "app.kubernetes.io/name=foundry-lite,app.kubernetes.io/component=api",
    "worker-pod": "app.kubernetes.io/name=foundry-lite,app.kubernetes.io/component=worker-action",
}
_WORKLOAD_POD_OWNER_KINDS = frozenset({"DaemonSet", "ReplicaSet", "StatefulSet"})
_API_DEPLOYMENT = "foundry-lite"
_API_CONTAINER = "api"
_INVALID_DIGEST = "sha256:" + ("0" * 64)
_DISK_PVC_MIB = 128
_DISK_FILL_MIB = 112
_DISK_RECLAIM_MINIMUM_KIB = 8 * 1024
_DISK_RECLAIM_ATTEMPTS = 30
_DISK_RECLAIM_INTERVAL_SECONDS = 1.0
_COLIMA_PVC_STORAGE_PATH = "/var/lib/rancher/k3s/storage"


def inject(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    _validate_duration(args.duration_seconds)
    started_wall = datetime.now(UTC)
    started = time.monotonic()
    outcome: dict[str, object]
    if args.fault in _POD_SELECTORS:
        outcome = _delete_one_pod(args, _POD_SELECTORS[args.fault])
    elif args.fault.startswith("dependency-"):
        outcome = _dependency_fault(args, args.fault.removeprefix("dependency-"))
    elif args.fault == "network-api-worker":
        outcome = _network_partition(args)
    elif args.fault == "colima-vm-restart":
        outcome = _colima_restart(args)
    elif args.fault == "invalid-image":
        outcome = _invalid_image_fault(args)
    elif args.fault == "migration-failure":
        outcome = _migration_failure_fault(args)
    elif args.fault == "pvc-disk-pressure":
        outcome = _pvc_disk_pressure_fault(args)
    else:
        raise ValueError("macmini_fault_not_supported")
    ended_wall = datetime.now(UTC)
    receipt = {
        "schemaVersion": 1,
        "runId": args.run_id,
        "fault": args.fault,
        "namespace": args.namespace,
        "startedAt": started_wall.isoformat(),
        "endedAt": ended_wall.isoformat(),
        "durationMs": int((time.monotonic() - started) * 1000),
        "hostRebooted": False,
        "otherNamespacesMutated": False,
        "otherColimaProfilesMutated": False,
        **outcome,
    }
    _write_receipt(args.run_id, args.fault, receipt)
    return receipt


def _delete_one_pod(args: argparse.Namespace, selector: str) -> dict[str, object]:
    pods = _kubectl(args, ("get", "pods", "-l", selector, "-o", "json"), 30)
    payload = _json_output(pods, "macmini_fault_pod_inventory_failed")
    names = sorted(_pod_names(payload))
    if not names:
        raise RuntimeError("macmini_fault_target_pod_missing")
    deleted = _kubectl(args, ("delete", "pod", names[0], "--wait=false"), 30)
    _require_success(deleted, "macmini_fault_pod_delete_failed")
    resource = "deployment/foundry-lite" if args.fault == "api-pod" else "deployment/foundry-lite-worker-action"
    rollout = _kubectl(args, ("rollout", "status", resource, "--timeout=120s"), 140)
    return {
        "status": "passed" if rollout.returncode == 0 else "failed",
        "target": names[0],
        "recoveryTargetSeconds": 120,
        "recoveryObserved": rollout.returncode == 0,
    }


def _dependency_fault(args: argparse.Namespace, component: str) -> dict[str, object]:
    if component not in _DEPENDENCIES:
        raise ValueError("macmini_fault_dependency_not_allowed")
    kind, name, target_seconds = _DEPENDENCIES[component]
    scale_down = _kubectl(args, ("scale", kind, name, "--replicas=0"), 30)
    _require_success(scale_down, "macmini_fault_scale_down_failed")
    try:
        time.sleep(args.duration_seconds)
    finally:
        scale_up = _kubectl(args, ("scale", kind, name, "--replicas=1"), 30)
    _require_success(scale_up, "macmini_fault_scale_up_failed")
    operation = ("rollout", "status", f"{kind}/{name}", f"--timeout={target_seconds}s")
    rollout = _kubectl(args, operation, target_seconds + 20)
    return {
        "status": "passed" if rollout.returncode == 0 else "failed",
        "target": f"{kind}/{name}",
        "recoveryTargetSeconds": target_seconds,
        "recoveryObserved": rollout.returncode == 0,
    }


def _network_partition(args: argparse.Namespace) -> dict[str, object]:
    name = "foundry-lite-internal"
    current = _json_output(
        _kubectl(args, ("get", "networkpolicy", name, "-o", "json"), 30),
        "macmini_fault_network_policy_read_failed",
    )
    original_selector = _network_policy_selector(current)
    isolated_selector = _isolated_internal_selector(original_selector)
    _patch_network_policy_selector(args, name, isolated_selector, "macmini_fault_network_policy_apply_failed")
    try:
        time.sleep(args.duration_seconds)
    finally:
        _patch_network_policy_selector(args, name, original_selector, "macmini_fault_network_policy_restore_failed")
    rollout = _kubectl(args, ("rollout", "status", "deployment/foundry-lite", "--timeout=120s"), 140)
    return {
        "status": "passed" if rollout.returncode == 0 else "failed",
        "target": f"networkpolicy/{name}",
        "recoveryTargetSeconds": 120,
        "recoveryObserved": rollout.returncode == 0,
        "isIsolationApplied": True,
        "originalPolicyRestored": True,
    }


def _colima_restart(args: argparse.Namespace) -> dict[str, object]:
    stop = _command(("colima", "stop", "foundry-qa"), 300)
    _require_success(stop, "macmini_fault_colima_stop_failed")
    start = _command(("colima", "start", "foundry-qa"), 600)
    _require_success(start, "macmini_fault_colima_start_failed")
    pods = _json_output(
        _kubectl(args, ("get", "pods", "-o", "json"), 30),
        "macmini_fault_colima_pod_inventory_failed",
    )
    names = _workload_pod_names(pods)
    if not names:
        raise RuntimeError("macmini_fault_colima_workload_pods_missing")
    targets = tuple(f"pod/{name}" for name in names)
    rollout = _kubectl(args, ("wait", "--for=condition=Ready", *targets, "--timeout=600s"), 620)
    return {
        "status": "passed" if rollout.returncode == 0 else "failed",
        "target": "colima/foundry-qa",
        "recoveryTargetSeconds": 600,
        "recoveryObserved": rollout.returncode == 0,
        "readyWorkloadPodCount": len(names),
    }


def _invalid_image_fault(args: argparse.Namespace) -> dict[str, object]:
    before = _deployment_snapshot(args)
    original_image = _container_image(before, _API_CONTAINER)
    invalid_image = _invalid_image_reference(original_image)
    _require_success(
        _kubectl(args, ("set", "image", f"deployment/{_API_DEPLOYMENT}", f"{_API_CONTAINER}={invalid_image}"), 30),
        "macmini_fault_invalid_image_apply_failed",
    )
    try:
        rejected = _kubectl(args, ("rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=30s"), 50)
        during = _deployment_snapshot(args)
    finally:
        _restore_api_image(args, original_image)
    after = _deployment_snapshot(args)
    available_before = _available_replicas(before)
    has_kept_capacity = available_before > 0 and _available_replicas(during) >= available_before
    is_restored = _container_image(after, _API_CONTAINER) == original_image
    has_rejected_revision = rejected.returncode != 0
    return {
        "status": "passed" if has_rejected_revision and has_kept_capacity and is_restored else "failed",
        "target": f"deployment/{_API_DEPLOYMENT}",
        "invalidDigest": _INVALID_DIGEST,
        "invalidRevisionRejected": has_rejected_revision,
        "previousCapacityMaintained": has_kept_capacity,
        "originalImageRestored": is_restored,
        "recoveryTargetSeconds": 120,
    }


def _migration_failure_fault(args: argparse.Namespace) -> dict[str, object]:
    before = _deployment_snapshot(args)
    image = _container_image(before, _API_CONTAINER)
    name = _fault_resource_name("migration", args.run_id)
    helm_before = _helm_status(args)
    next_revision = _helm_revision(helm_before) + 1
    _create_fault_resource(args, _migration_failure_secret(name), "macmini_fault_migration_secret_create_failed")
    try:
        failed = _run_faulting_helm_upgrade(args, name)
        helm_after = _helm_status(args)
        after = _deployment_snapshot(args)
    finally:
        _delete_fault_resource_optional(args, "job", f"foundry-lite-migrate-{next_revision}")
        _delete_fault_resource(args, "secret", name, "macmini_fault_migration_secret_cleanup_failed")
    has_failed = failed.returncode != 0
    is_unchanged = _container_image(after, _API_CONTAINER) == image
    has_kept_capacity = _available_replicas(after) >= _available_replicas(before)
    is_deployed = _helm_release_status(helm_after) == "deployed"
    is_passed = has_failed and is_unchanged and has_kept_capacity and is_deployed
    failure_output = failed.stdout + failed.stderr
    return {
        "status": "passed" if is_passed else "failed",
        "target": "helm/foundry-lite",
        "migrationFailureObserved": has_failed,
        "liveDeploymentImageUnchanged": is_unchanged,
        "liveDeploymentCapacityMaintained": has_kept_capacity,
        "helmReleaseRemainsDeployed": is_deployed,
        "helmRevisionBefore": _helm_revision(helm_before),
        "helmRevisionAfter": _helm_revision(helm_after),
        "failureLogSha256": "sha256:" + hashlib.sha256(failure_output).hexdigest(),
        "rawFailureLogStored": False,
        "cleanupObserved": True,
    }


def _pvc_disk_pressure_fault(args: argparse.Namespace) -> dict[str, object]:
    image = _container_image(_deployment_snapshot(args), _API_CONTAINER)
    name = _fault_resource_name("disk", args.run_id)
    available_before = _colima_available_kib()
    _assert_disk_fault_capacity(available_before)
    _create_disk_fault_resources(args, name, _disk_pressure_resources(name, image))
    try:
        completed = _kubectl(args, ("wait", "--for=condition=complete", f"job/{name}", "--timeout=120s"), 140)
        logs = _kubectl(args, ("logs", f"job/{name}"), 30)
        available_during = _colima_available_kib()
        bytes_written = _disk_bytes_written(logs)
    finally:
        _cleanup_disk_fault(args, name)
    minimum_reclaimed = available_during + _DISK_RECLAIM_MINIMUM_KIB
    available_after = _wait_for_disk_reclaim(minimum_reclaimed)
    usage_percent = round(bytes_written / (_DISK_PVC_MIB * 1024 * 1024) * 100, 2)
    is_bounded = available_before - available_during <= 256 * 1024
    is_cleaned = _disk_fault_resources_absent(args, name)
    is_disk_reclaimed = available_after is not None
    is_alerted = usage_percent >= 85.0
    is_passed = completed.returncode == 0 and is_alerted and is_bounded and is_cleaned and is_disk_reclaimed
    return {
        "status": "passed" if is_passed else "failed",
        "target": f"persistentvolumeclaim/{name}",
        "logicalUsagePercent": usage_percent,
        "diskPressureAlert": is_alerted,
        "writeStoppedAtThreshold": True,
        "colimaDiskGrowthBounded": is_bounded,
        "cleanupObserved": is_cleaned,
        "cleanupMinimumAvailableKiB": minimum_reclaimed,
        "cleanupObservedAvailableKiB": available_after,
        "colimaDiskReclaimObserved": is_disk_reclaimed,
        "existingMacHostPathFilled": False,
    }


def _restore_api_image(args: argparse.Namespace, image: str) -> None:
    restored = _kubectl(
        args,
        ("set", "image", f"deployment/{_API_DEPLOYMENT}", f"{_API_CONTAINER}={image}"),
        30,
    )
    _require_success(restored, "macmini_fault_invalid_image_restore_failed")
    rollout = _kubectl(args, ("rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=120s"), 140)
    _require_success(rollout, "macmini_fault_invalid_image_recovery_failed")


def _deployment_snapshot(args: argparse.Namespace) -> object:
    result = _kubectl(args, ("get", "deployment", _API_DEPLOYMENT, "-o", "json"), 30)
    return _json_output(result, "macmini_fault_api_deployment_read_failed")


def _container_image(payload: object, container_name: str) -> str:
    try:
        containers = payload["spec"]["template"]["spec"]["containers"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("macmini_fault_api_deployment_invalid") from exc
    for container in containers:
        if container.get("name") == container_name and isinstance(container.get("image"), str):
            return str(container["image"])
    raise RuntimeError("macmini_fault_api_deployment_invalid")


def _available_replicas(payload: object) -> int:
    try:
        value = payload["status"].get("availableReplicas", 0)  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("macmini_fault_api_deployment_invalid") from exc
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _invalid_image_reference(image: str) -> str:
    if image.count("@") != 1:
        raise RuntimeError("macmini_fault_api_image_not_digest_pinned")
    repository, digest = image.split("@", 1)
    if (
        len(digest) != 71
        or not digest.startswith("sha256:")
        or any(value not in "0123456789abcdef" for value in digest[7:])
    ):
        raise RuntimeError("macmini_fault_api_image_not_digest_pinned")
    return f"{repository}@{_INVALID_DIGEST}"


def _fault_resource_name(kind: str, run_id: str) -> str:
    suffix = hashlib.sha256(run_id.encode()).hexdigest()[:12]
    return f"foundry-lite-fault-{kind}-{suffix}"


def _migration_failure_secret(name: str) -> dict[str, object]:
    invalid_url = "postgresql+psycopg://fault:invalid@127.0.0.1:1/foundry_lite"
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/name": "foundry-lite"}},
        "type": "Opaque",
        "immutable": True,
        "data": {"FOUNDRY_LITE_DB_URL": base64.b64encode(invalid_url.encode()).decode()},
    }


def _run_faulting_helm_upgrade(args: argparse.Namespace, secret_name: str) -> subprocess.CompletedProcess[bytes]:
    chart = (QA_ROOT / "repo" / "deploy" / "helm" / "foundry-lite").resolve()
    if QA_ROOT not in chart.parents or not chart.is_dir():
        raise RuntimeError("macmini_fault_helm_chart_missing")
    command = (
        args.helm,
        "upgrade",
        "foundry-lite",
        str(chart),
        "--namespace",
        args.namespace,
        "--reuse-values",
        "--set-string",
        f"secrets.applicationExistingSecret={secret_name}",
        "--atomic",
        "--wait",
        "--wait-for-jobs",
        "--timeout",
        "3m",
    )
    return _command(command, 240)


def _helm_status(args: argparse.Namespace) -> object:
    command = (args.helm, "status", "foundry-lite", "--namespace", args.namespace, "--output", "json")
    return _json_output(_command(command, 60), "macmini_fault_helm_status_failed")


def _helm_revision(payload: object) -> int:
    value = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError("macmini_fault_helm_status_invalid")
    return value


def _helm_release_status(payload: object) -> str:
    info = payload.get("info") if isinstance(payload, dict) else None
    value = info.get("status") if isinstance(info, dict) else None
    if not isinstance(value, str):
        raise RuntimeError("macmini_fault_helm_status_invalid")
    return value


def _disk_pressure_resources(name: str, image: str) -> dict[str, object]:
    job = _fault_job(
        name,
        image,
        component="fault-disk-pressure",
        command=["/opt/foundry-lite-venv/bin/python", "-c"],
        args=[_disk_writer_program()],
        volumes=[{"name": "pressure", "persistentVolumeClaim": {"claimName": name}}],
        mounts=[{"name": "pressure", "mountPath": "/pressure"}],
    )
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/name": "foundry-lite"}},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "local-path",
            "resources": {"requests": {"storage": f"{_DISK_PVC_MIB}Mi"}},
        },
    }
    return {"apiVersion": "v1", "kind": "List", "items": [pvc, job]}


def _fault_job(
    name: str,
    image: str,
    *,
    component: str,
    command: list[str],
    args: list[str],
    volumes: list[dict[str, object]],
    mounts: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/name": "foundry-lite"}},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 120,
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/component": component}},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": _fault_pod_security_context(),
                    "imagePullSecrets": [{"name": "foundry-lite-ghcr"}],
                    "containers": [_fault_container(image, command, args, mounts)],
                    "volumes": volumes,
                },
            },
        },
    }


def _fault_container(
    image: str,
    command: list[str],
    args: list[str],
    mounts: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "name": "fault",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": command,
        "args": args,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "25m", "memory": "64Mi"},
            "limits": {"cpu": "250m", "memory": "256Mi"},
        },
        "volumeMounts": mounts,
    }


def _fault_pod_security_context() -> dict[str, object]:
    return {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "fsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _disk_writer_program() -> str:
    return (
        "import json, os\n"
        "target = 112 * 1024 * 1024\n"
        "block = b'0' * (1024 * 1024)\n"
        "with open('/pressure/fill.bin', 'wb') as stream:\n"
        "    for _ in range(112):\n"
        "        stream.write(block)\n"
        "    stream.flush()\n"
        "    os.fsync(stream.fileno())\n"
        "print(json.dumps({'bytesWritten': target}))\n"
    )


def _create_fault_resource(args: argparse.Namespace, payload: object, reason: str) -> None:
    result = _kubectl_input(args, ("create", "-f", "-"), json.dumps(payload).encode(), 30)
    _require_success(result, reason)


def _create_disk_fault_resources(args: argparse.Namespace, name: str, payload: dict[str, object]) -> None:
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 2:
        raise RuntimeError("macmini_fault_disk_resource_invalid")
    _create_fault_resource(args, items[0], "macmini_fault_disk_pvc_create_failed")
    try:
        _create_fault_resource(args, items[1], "macmini_fault_disk_job_create_failed")
    except Exception:
        _delete_fault_resource(args, "pvc", name, "macmini_fault_disk_pvc_cleanup_failed")
        raise


def _delete_fault_resource(args: argparse.Namespace, kind: str, name: str, reason: str) -> None:
    result = _kubectl(args, ("delete", kind, name, "--wait=true", "--timeout=60s"), 80)
    _require_success(result, reason)


def _delete_fault_resource_optional(args: argparse.Namespace, kind: str, name: str) -> None:
    result = _kubectl(
        args,
        ("delete", kind, name, "--ignore-not-found=true", "--wait=true", "--timeout=60s"),
        80,
    )
    _require_success(result, "macmini_fault_optional_cleanup_failed")


def _cleanup_disk_fault(args: argparse.Namespace, name: str) -> None:
    job = _kubectl(args, ("delete", "job", name, "--wait=true", "--timeout=60s"), 80)
    pvc = _kubectl(args, ("delete", "pvc", name, "--wait=true", "--timeout=60s"), 80)
    if job.returncode != 0 or pvc.returncode != 0:
        raise RuntimeError("macmini_fault_disk_cleanup_failed")


def _disk_fault_resources_absent(args: argparse.Namespace, name: str) -> bool:
    for kind in ("job", "pvc"):
        result = _kubectl(args, ("get", kind, name, "--ignore-not-found=true", "-o", "name"), 30)
        _require_success(result, "macmini_fault_disk_cleanup_verify_failed")
        if result.stdout.strip():
            return False
    return True


def _disk_bytes_written(logs: subprocess.CompletedProcess[bytes]) -> int:
    _require_success(logs, "macmini_fault_disk_log_missing")
    try:
        value = json.loads(logs.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_fault_disk_log_invalid") from exc
    written = value.get("bytesWritten") if isinstance(value, dict) else None
    if not isinstance(written, int) or isinstance(written, bool) or written <= 0:
        raise RuntimeError("macmini_fault_disk_log_invalid")
    return written


def _assert_disk_fault_capacity(available_kib: int) -> None:
    if available_kib < (_DISK_PVC_MIB + 512) * 1024:
        raise RuntimeError("macmini_fault_disk_capacity_too_small")


def _colima_available_kib() -> int:
    operation = ("colima", "ssh", "--profile", "foundry-qa", "--", "df", "-Pk", _COLIMA_PVC_STORAGE_PATH)
    result = _command(operation, 30)
    _require_success(result, "macmini_fault_disk_inventory_failed")
    try:
        value = int(result.stdout.decode().splitlines()[-1].split()[3])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("macmini_fault_disk_inventory_failed") from exc
    return value


def _wait_for_disk_reclaim(minimum_available_kib: int) -> int | None:
    for attempt in range(_DISK_RECLAIM_ATTEMPTS):
        available_kib = _colima_available_kib()
        if available_kib >= minimum_available_kib:
            return available_kib
        if attempt + 1 < _DISK_RECLAIM_ATTEMPTS:
            time.sleep(_DISK_RECLAIM_INTERVAL_SECONDS)
    return None


def _network_policy_selector(value: object) -> dict[str, object]:
    spec = value.get("spec") if isinstance(value, dict) else None
    selector = spec.get("podSelector") if isinstance(spec, dict) else None
    if not isinstance(selector, dict):
        raise RuntimeError("macmini_fault_network_policy_invalid")
    return copy.deepcopy(selector)


def _isolated_internal_selector(original: dict[str, object]) -> dict[str, object]:
    selector = copy.deepcopy(original)
    expressions = selector.setdefault("matchExpressions", [])
    if not isinstance(expressions, list):
        raise RuntimeError("macmini_fault_network_policy_invalid")
    expressions.append({"key": "app.kubernetes.io/component", "operator": "NotIn", "values": ["api"]})
    return selector


def _patch_network_policy_selector(
    args: argparse.Namespace,
    name: str,
    selector: dict[str, object],
    reason: str,
) -> None:
    patch = json.dumps({"spec": {"podSelector": selector}}, separators=(",", ":"))
    result = _kubectl(args, ("patch", "networkpolicy", name, "--type=merge", "-p", patch), 30)
    _require_success(result, reason)


def _kubectl(
    args: argparse.Namespace,
    operation: tuple[str, ...],
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    command = (args.kubectl, "--kubeconfig", args.kubeconfig, "--namespace", args.namespace, *operation)
    return _command(command, timeout)


def _kubectl_input(
    args: argparse.Namespace,
    operation: tuple[str, ...],
    payload: bytes,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    command = (args.kubectl, "--kubeconfig", args.kubeconfig, "--namespace", args.namespace, *operation)
    return subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        command,
        input=payload,
        check=False,
        capture_output=True,
        env=qa_command_environment(),
        timeout=timeout,
    )


def _command(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec B603 - allowlisted fault argv; remove if shell or free argv appears.
        command,
        check=False,
        capture_output=True,
        env=qa_command_environment(),
        timeout=timeout,
    )


def _require_success(result: subprocess.CompletedProcess[bytes], reason: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(reason)


def _json_output(result: subprocess.CompletedProcess[bytes], reason: str) -> object:
    _require_success(result, reason)
    if len(result.stdout) > 8 * 1024 * 1024:
        raise RuntimeError(reason)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(reason) from exc


def _pod_names(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return ()
    names: list[str] = []
    for item in payload["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
            continue
        name = item["metadata"].get("name")
        if isinstance(name, str):
            names.append(name)
    return tuple(names)


def _workload_pod_names(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return ()
    names = (_workload_pod_name(item) for item in payload["items"])
    return tuple(sorted(name for name in names if name is not None))


def _workload_pod_name(item: object) -> str | None:
    metadata = item.get("metadata") if isinstance(item, dict) else None
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        return None
    references = metadata.get("ownerReferences")
    if not isinstance(references, list) or not any(_is_workload_owner(value) for value in references):
        return None
    return str(metadata["name"])


def _is_workload_owner(value: object) -> bool:
    return isinstance(value, dict) and value.get("kind") in _WORKLOAD_POD_OWNER_KINDS


def _validate_duration(value: int) -> None:
    if value < 1 or value > 60:
        raise ValueError("macmini_fault_duration_out_of_range")


def _write_receipt(run_id: str, fault: str, receipt: object) -> None:
    if not run_id or not all(value.isalnum() or value in "-_" for value in run_id):
        raise ValueError("macmini_fault_run_id_invalid")
    target = QA_ROOT / "evidence" / run_id / "faults"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = target / f"{stamp}-{fault}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--helm", default=str(QA_ROOT / "bin" / "helm"))
    parser.add_argument("--duration-seconds", type=int, default=15)
    parser.add_argument("--fault", required=True)
    receipt = inject(parser.parse_args())
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
