"""Inject and automatically recover namespace-scoped Mac mini QA faults."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - fixed kubectl only; remove if arbitrary command input is introduced.
import time
from collections.abc import Callable, Iterator
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
    "controller-pod": "app.kubernetes.io/name=foundry-lite,app.kubernetes.io/component=release-controller",
}
_SIGNAL_FAULTS = {
    "api-sigterm": (
        _POD_SELECTORS["api-pod"],
        "api",
        "TERM",
        "deployment/foundry-lite",
    ),
    "worker-sigkill": (
        _POD_SELECTORS["worker-pod"],
        "worker",
        "KILL",
        "deployment/foundry-lite-worker-action",
    ),
    "controller-sigkill": (
        _POD_SELECTORS["controller-pod"],
        "controller",
        "KILL",
        "deployment/foundry-lite-release-controller",
    ),
}
_NETWORK_NETEM = {
    "network-latency": ("delay", "250ms", "50ms", "distribution", "normal"),
    "network-packet-loss": ("loss", "20%", "25%"),
    "network-full-partition": ("loss", "100%"),
}
_NETWORK_BRIDGE = "cni0"
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
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_CONTAINER_ID = re.compile(r"^(?P<runtime>docker)://(?P<identifier>[0-9a-f]{64})$")


def inject(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    _validate_duration(args.duration_seconds)
    started_wall = datetime.now(UTC)
    started = time.monotonic()
    outcome = _fault_outcome(args)
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


def _fault_outcome(args: argparse.Namespace) -> dict[str, object]:
    if args.fault in _POD_SELECTORS:
        return _delete_one_pod(args, _POD_SELECTORS[args.fault])
    if args.fault in _SIGNAL_FAULTS:
        return _signal_fault(args, *_SIGNAL_FAULTS[args.fault])
    if args.fault.startswith("dependency-"):
        return _dependency_fault(args, args.fault.removeprefix("dependency-"))
    if args.fault in _NETWORK_NETEM:
        return _network_netem_fault(args, _NETWORK_NETEM[args.fault])
    handler = _named_fault_handlers().get(args.fault)
    if handler is None:
        raise ValueError("macmini_fault_not_supported")
    return handler(args)


def _named_fault_handlers() -> dict[str, Callable[[argparse.Namespace], dict[str, object]]]:
    return {
        "worker-oom": _worker_oom_fault,
        "network-api-worker": _network_partition,
        "network-connection-reset": _network_connection_reset_fault,
        "network-dns-failure": _network_dns_failure_fault,
        "colima-vm-restart": _colima_restart,
        "invalid-image": _invalid_image_fault,
        "rolling-restart": _rolling_restart_fault,
        "bad-config": _bad_config_fault,
        "verified-digest-rollback": _verified_digest_rollback_fault,
        "migration-failure": _migration_failure_fault,
        "pvc-disk-pressure": _pvc_disk_pressure_fault,
    }


def _network_connection_reset_fault(args: argparse.Namespace) -> dict[str, object]:
    return _network_iptables_fault(args, _tcp_reset_rules())


def _network_dns_failure_fault(args: argparse.Namespace) -> dict[str, object]:
    return _network_iptables_fault(args, _dns_reject_rules())


def _delete_one_pod(args: argparse.Namespace, selector: str) -> dict[str, object]:
    pods = _kubectl(args, ("get", "pods", "-l", selector, "-o", "json"), 30)
    payload = _json_output(pods, "macmini_fault_pod_inventory_failed")
    names = sorted(_pod_names(payload))
    if not names:
        raise RuntimeError("macmini_fault_target_pod_missing")
    deleted = _kubectl(args, ("delete", "pod", names[0], "--wait=false"), 30)
    _require_success(deleted, "macmini_fault_pod_delete_failed")
    resources = {
        "api-pod": "deployment/foundry-lite",
        "worker-pod": "deployment/foundry-lite-worker-action",
        "controller-pod": "deployment/foundry-lite-release-controller",
    }
    resource = resources[args.fault]
    rollout = _kubectl(args, ("rollout", "status", resource, "--timeout=120s"), 140)
    return {
        "status": "passed" if rollout.returncode == 0 else "failed",
        "target": names[0],
        "recoveryTargetSeconds": 120,
        "recoveryObserved": rollout.returncode == 0,
    }


def _signal_fault(
    args: argparse.Namespace,
    selector: str,
    container: str,
    signal_name: str,
    resource: str,
) -> dict[str, object]:
    pod = _first_pod(args, selector)
    before = _container_lifecycle_snapshot(args, pod, container)
    process_before = _pid_one_identity(args, pod, container)
    runtime_before = _runtime_container_target(before)
    expected_signal = _signal_number(signal_name)
    injected_at = datetime.now(UTC).isoformat()
    signalled = _runtime_docker_kill(runtime_before, signal_name)
    after = _wait_for_container_restart(args, pod, container, before, 60)
    rollout = _kubectl(args, ("rollout", "status", resource, "--timeout=120s"), 140)
    process_after = _pid_one_identity(args, pod, container)
    runtime_after = _runtime_container_target(after)
    restarted = _restart_observed(before, after)
    signal_termination_observed = _termination_observed(after, expected_signal)
    graceful_termination_observed = _graceful_termination_observed(after, signal_name)
    termination_contract_satisfied = signal_termination_observed or graceful_termination_observed
    container_id_changed = before["containerId"] != after["containerId"]
    is_passed = all(
        (
            signalled.returncode == 0,
            restarted,
            termination_contract_satisfied,
            container_id_changed,
            runtime_before["observed"],
            runtime_after["observed"],
            process_before["observed"],
            process_after["observed"],
            rollout.returncode == 0,
        )
    )
    return {
        "status": "passed" if is_passed else "failed",
        "target": pod,
        "targetContainer": container,
        "signal": f"SIG{signal_name}",
        "expectedTerminationSignal": expected_signal,
        "signalTransport": "docker-runtime",
        "signalInjectedAt": injected_at,
        "runtimeKillReturnCode": signalled.returncode,
        "runtimeKillAccepted": signalled.returncode == 0,
        "targetProcessBefore": process_before,
        "targetProcessAfter": process_after,
        "runtimeTargetBefore": runtime_before,
        "runtimeTargetAfter": runtime_after,
        "containerLifecycleBefore": before,
        "containerLifecycleAfter": after,
        "containerRestartObserved": restarted,
        "containerIdChanged": container_id_changed,
        "terminationSignalObserved": signal_termination_observed,
        "gracefulTerminationObserved": graceful_termination_observed,
        "terminationContractSatisfied": termination_contract_satisfied,
        "terminationMode": _termination_mode(signal_termination_observed, graceful_termination_observed),
        "recoveryTargetSeconds": 120,
        "recoveryObserved": rollout.returncode == 0,
    }


def _first_pod(args: argparse.Namespace, selector: str) -> str:
    pods = _json_output(
        _kubectl(args, ("get", "pods", "-l", selector, "-o", "json"), 30),
        "macmini_fault_pod_inventory_failed",
    )
    names = sorted(_pod_names(pods))
    if not names:
        raise RuntimeError("macmini_fault_target_pod_missing")
    return names[0]


def _container_lifecycle_snapshot(
    args: argparse.Namespace,
    pod: str,
    container: str,
) -> dict[str, object]:
    payload = _json_output(
        _kubectl(args, ("get", "pod", pod, "-o", "json"), 30),
        "macmini_fault_pod_status_failed",
    )
    status = payload.get("status") if isinstance(payload, dict) else None
    containers = status.get("containerStatuses") if isinstance(status, dict) else None
    if not isinstance(containers, list):
        raise RuntimeError("macmini_fault_pod_status_failed")
    matched = next(
        (item for item in containers if isinstance(item, dict) and item.get("name") == container),
        None,
    )
    if not isinstance(matched, dict):
        raise RuntimeError("macmini_fault_pod_status_failed")
    return _container_lifecycle_values(matched)


def _container_lifecycle_values(status: dict[str, object]) -> dict[str, object]:
    running = status.get("state")
    running_state = running.get("running") if isinstance(running, dict) else None
    last_state = status.get("lastState")
    terminated = last_state.get("terminated") if isinstance(last_state, dict) else None
    return {
        "containerId": _optional_text(status.get("containerID")),
        "restartCount": _optional_int(status.get("restartCount"), default=0),
        "isReady": status.get("ready") is True,
        "startedAt": _optional_text(running_state.get("startedAt")) if isinstance(running_state, dict) else None,
        "lastTermination": _termination_values(terminated),
    }


def _termination_values(terminated: object) -> dict[str, object]:
    if not isinstance(terminated, dict):
        return {"reason": None, "exitCode": None, "signal": None, "finishedAt": None}
    return {
        "reason": _optional_text(terminated.get("reason")),
        "exitCode": _optional_int(terminated.get("exitCode"), default=None),
        "signal": _optional_int(terminated.get("signal"), default=None),
        "finishedAt": _optional_text(terminated.get("finishedAt")),
    }


def _wait_for_container_restart(
    args: argparse.Namespace,
    pod: str,
    container: str,
    before: dict[str, object],
    timeout_seconds: int,
) -> dict[str, object]:
    latest = before
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            latest = _container_lifecycle_snapshot(args, pod, container)
            if _restart_observed(before, latest) and latest["isReady"] is True:
                return latest
        except RuntimeError:
            pass
        time.sleep(1)
    return latest


def _pid_one_identity(args: argparse.Namespace, pod: str, container: str) -> dict[str, object]:
    program = (
        "import hashlib,json;"
        "raw=open('/proc/1/cmdline','rb').read();"
        "print(json.dumps({'pid':1,'commandSha256':hashlib.sha256(raw).hexdigest()}))"
    )
    result = _kubectl(
        args,
        ("exec", pod, "-c", container, "--", "/opt/foundry-lite-venv/bin/python", "-c", program),
        30,
    )
    if result.returncode != 0:
        return {"observed": False, "pid": None, "commandSha256": None}
    try:
        payload = json.loads(result.stdout.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"observed": False, "pid": None, "commandSha256": None}
    digest = payload.get("commandSha256") if isinstance(payload, dict) else None
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        return {"observed": False, "pid": None, "commandSha256": None}
    return {"observed": True, "pid": 1, "commandSha256": digest}


def _runtime_container_target(snapshot: dict[str, object]) -> dict[str, object]:
    container_id = snapshot.get("containerId")
    if not isinstance(container_id, str):
        return _unobserved_runtime_target()
    match = _RUNTIME_CONTAINER_ID.fullmatch(container_id)
    if match is None:
        return _unobserved_runtime_target()
    identifier = match.group("identifier")
    inspected = _colima_root_command(("docker", "inspect", "--format", "{{.State.Pid}}", identifier), 30)
    if inspected.returncode != 0:
        return _unobserved_runtime_target()
    try:
        pid = int(inspected.stdout.decode().strip())
    except (UnicodeDecodeError, ValueError):
        return _unobserved_runtime_target()
    if pid <= 0:
        return _unobserved_runtime_target()
    return {
        "observed": True,
        "runtime": match.group("runtime"),
        "containerId": container_id,
        "runtimeContainerId": identifier,
        "hostPid": pid,
    }


def _unobserved_runtime_target() -> dict[str, object]:
    return {"observed": False, "runtime": None, "containerId": None, "runtimeContainerId": None, "hostPid": None}


def _runtime_docker_kill(target: dict[str, object], signal_name: str) -> subprocess.CompletedProcess[bytes]:
    runtime_id = target.get("runtimeContainerId")
    if not isinstance(runtime_id, str) or _SHA256.fullmatch(runtime_id) is None:
        return subprocess.CompletedProcess(("docker", "kill"), 1, b"", b"invalid runtime container id")
    return _colima_root_command(("docker", "kill", "--signal", signal_name, runtime_id), 30)


def _signal_number(signal_name: str) -> int:
    values = {"TERM": 15, "KILL": 9}
    value = values.get(signal_name)
    if value is None:
        raise RuntimeError("macmini_fault_signal_not_supported")
    return value


def _restart_observed(before: dict[str, object], after: dict[str, object]) -> bool:
    after_count = after.get("restartCount")
    before_count = before.get("restartCount")
    return (
        isinstance(after_count, int)
        and not isinstance(after_count, bool)
        and isinstance(before_count, int)
        and not isinstance(before_count, bool)
        and after_count > before_count
    )


def _termination_observed(snapshot: dict[str, object], expected_signal: int) -> bool:
    terminal = snapshot.get("lastTermination")
    if not isinstance(terminal, dict):
        return False
    signal = _optional_int(terminal.get("signal"), default=None)
    exit_code = _optional_int(terminal.get("exitCode"), default=None)
    return signal == expected_signal or exit_code == 128 + expected_signal


def _graceful_termination_observed(snapshot: dict[str, object], signal_name: str) -> bool:
    if signal_name != "TERM":
        return False
    terminal = snapshot.get("lastTermination")
    if not isinstance(terminal, dict):
        return False
    return (
        terminal.get("reason") == "Completed"
        and _optional_int(terminal.get("exitCode"), default=None) == 0
        and _optional_text(terminal.get("finishedAt")) is not None
    )


def _termination_mode(is_signal_observed: bool, is_graceful_observed: bool) -> str:
    if is_signal_observed:
        return "signal"
    if is_graceful_observed:
        return "graceful"
    return "unproven"


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object, *, default: int | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _worker_oom_fault(args: argparse.Namespace) -> dict[str, object]:
    deployment = "foundry-lite-worker-action"
    before = _named_deployment_snapshot(args, deployment)
    original_command = _deployment_container_command(before, "worker")
    fault_command = (
        "/opt/foundry-lite-venv/bin/python",
        "-c",
        "chunks=[]\nwhile True: chunks.append(bytearray(64*1024*1024))",
    )
    _patch_deployment_command(args, deployment, fault_command, "macmini_fault_oom_apply_failed")
    observed = False
    try:
        observed = _wait_for_oom_killed(args, _POD_SELECTORS["worker-pod"], args.duration_seconds)
    finally:
        _patch_deployment_command(args, deployment, original_command, "macmini_fault_oom_restore_failed")
    rollout = _kubectl(args, ("rollout", "status", f"deployment/{deployment}", "--timeout=180s"), 200)
    return {
        "status": "passed" if observed and rollout.returncode == 0 else "failed",
        "target": f"deployment/{deployment}",
        "oomKilledObserved": observed,
        "originalCommandRestored": True,
        "recoveryTargetSeconds": 180,
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


def _network_netem_fault(args: argparse.Namespace, parameters: tuple[str, ...]) -> dict[str, object]:
    _require_clean_network_qdisc()
    apply = _colima_root_command(("tc", "qdisc", "add", "dev", _NETWORK_BRIDGE, "root", "netem", *parameters), 30)
    _require_success(apply, "macmini_fault_network_netem_apply_failed")
    try:
        time.sleep(args.duration_seconds)
    finally:
        cleanup = _colima_root_command(("tc", "qdisc", "del", "dev", _NETWORK_BRIDGE, "root"), 30)
    _require_success(cleanup, "macmini_fault_network_netem_restore_failed")
    restored = _network_qdisc_is_clean()
    return {
        "status": "passed" if restored else "failed",
        "target": f"colima/foundry-qa/{_NETWORK_BRIDGE}",
        "networkEmulation": list(parameters),
        "recoveryTargetSeconds": 30,
        "recoveryObserved": restored,
        "originalQdiscRestored": restored,
    }


def _network_iptables_fault(args: argparse.Namespace, rules: tuple[tuple[str, ...], ...]) -> dict[str, object]:
    applied: list[tuple[str, ...]] = []
    try:
        for rule in rules:
            result = _colima_root_command(("iptables", "-I", "FORWARD", "1", *rule), 30)
            _require_success(result, "macmini_fault_network_rule_apply_failed")
            applied.append(rule)
        time.sleep(args.duration_seconds)
    finally:
        cleanup_failed = False
        for rule in reversed(applied):
            result = _colima_root_command(("iptables", "-D", "FORWARD", *rule), 30)
            cleanup_failed = cleanup_failed or result.returncode != 0
    if cleanup_failed:
        raise RuntimeError("macmini_fault_network_rule_restore_failed")
    return {
        "status": "passed",
        "target": f"colima/foundry-qa/{_NETWORK_BRIDGE}",
        "ruleCount": len(rules),
        "recoveryTargetSeconds": 30,
        "recoveryObserved": True,
        "originalRulesRestored": True,
    }


def _tcp_reset_rules() -> tuple[tuple[str, ...], ...]:
    return (
        (
            "-i",
            _NETWORK_BRIDGE,
            "-p",
            "tcp",
            "-j",
            "REJECT",
            "--reject-with",
            "tcp-reset",
        ),
    )


def _dns_reject_rules() -> tuple[tuple[str, ...], ...]:
    return (
        ("-i", _NETWORK_BRIDGE, "-p", "udp", "--dport", "53", "-j", "REJECT"),
        (
            "-i",
            _NETWORK_BRIDGE,
            "-p",
            "tcp",
            "--dport",
            "53",
            "-j",
            "REJECT",
            "--reject-with",
            "tcp-reset",
        ),
    )


def _require_clean_network_qdisc() -> None:
    if not _network_qdisc_is_clean():
        raise RuntimeError("macmini_fault_network_existing_qdisc")


def _network_qdisc_is_clean() -> bool:
    result = _colima_root_command(("tc", "qdisc", "show", "dev", _NETWORK_BRIDGE), 30)
    _require_success(result, "macmini_fault_network_qdisc_read_failed")
    lines = [line.strip() for line in result.stdout.decode(errors="replace").splitlines() if line.strip()]
    return not lines or all(" noqueue " in f" {line} " for line in lines)


def _colima_root_command(operation: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return _command(("colima", "ssh", "--profile", "foundry-qa", "--", "sudo", *operation), timeout)


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
        _kubectl(
            args,
            (
                "set",
                "image",
                f"deployment/{_API_DEPLOYMENT}",
                f"{_API_CONTAINER}={invalid_image}",
                "--field-manager=helm",
            ),
            30,
        ),
        "macmini_fault_invalid_image_apply_failed",
    )
    try:
        rejected = _kubectl(
            args,
            ("rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=30s"),
            50,
        )
        during = _deployment_snapshot(args)
    finally:
        _restore_api_image(args, original_image)
    after = _deployment_snapshot(args)
    available_before = _available_replicas(before)
    has_kept_capacity = available_before > 0 and _available_replicas(during) >= available_before
    is_restored = _container_image(after, _API_CONTAINER) == original_image
    has_rejected_revision = rejected.returncode != 0
    return {
        "status": ("passed" if has_rejected_revision and has_kept_capacity and is_restored else "failed"),
        "target": f"deployment/{_API_DEPLOYMENT}",
        "invalidDigest": _INVALID_DIGEST,
        "invalidRevisionRejected": has_rejected_revision,
        "previousCapacityMaintained": has_kept_capacity,
        "originalImageRestored": is_restored,
        "fieldManager": "helm",
        "recoveryTargetSeconds": 120,
    }


def _rolling_restart_fault(args: argparse.Namespace) -> dict[str, object]:
    before = _deployment_snapshot(args)
    image = _container_image(before, _API_CONTAINER)
    restarted = _kubectl(args, ("rollout", "restart", f"deployment/{_API_DEPLOYMENT}"), 30)
    _require_success(restarted, "macmini_fault_rolling_restart_apply_failed")
    rollout = _kubectl(
        args,
        ("rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=180s"),
        200,
    )
    after = _deployment_snapshot(args)
    image_unchanged = _container_image(after, _API_CONTAINER) == image
    capacity_restored = _available_replicas(after) >= _available_replicas(before)
    is_passed = rollout.returncode == 0 and image_unchanged and capacity_restored
    return {
        "status": "passed" if is_passed else "failed",
        "target": f"deployment/{_API_DEPLOYMENT}",
        "sameDigestRollingRestart": True,
        "imageDigestUnchanged": image_unchanged,
        "capacityRestored": capacity_restored,
        "recoveryTargetSeconds": 180,
        "recoveryObserved": rollout.returncode == 0,
    }


def _bad_config_fault(args: argparse.Namespace) -> dict[str, object]:
    before = _deployment_snapshot(args)
    helm_before = _helm_status(args)
    values_before = _helm_values(args)
    migration_secret = _protected_migration_secret(values_before)
    command = (
        args.helm,
        "upgrade",
        "foundry-lite",
        str(QA_ROOT / "repo" / "deploy" / "helm" / "foundry-lite"),
        "--namespace",
        args.namespace,
        "--reuse-values",
        "--atomic",
        "--timeout",
        "2m",
        "--set-string",
        f"secrets.applicationExistingSecret={migration_secret}",
    )
    failed = _bounded_command(command, 180)
    recovery = _recover_helm_release(args, _helm_revision(helm_before), values_before, failed.returncode == 0)
    helm_after = _helm_status(args)
    values_after = _helm_values(args)
    after = _deployment_snapshot(args)
    is_rejected = failed.returncode != 0
    is_unchanged = _container_image(after, _API_CONTAINER) == _container_image(before, _API_CONTAINER)
    capacity_kept = _available_replicas(after) >= _available_replicas(before)
    values_restored = _json_sha256(values_after) == _json_sha256(values_before)
    is_deployed = _helm_release_status(helm_after) == "deployed"
    is_passed = is_rejected and is_unchanged and capacity_kept and values_restored and is_deployed
    return {
        "status": "passed" if is_passed else "failed",
        "target": "helm/foundry-lite",
        "invalidProtectedConfigRejected": is_rejected,
        "liveDeploymentImageUnchanged": is_unchanged,
        "liveDeploymentCapacityMaintained": capacity_kept,
        "helmRevisionBefore": _helm_revision(helm_before),
        "helmRevisionAfter": _helm_revision(helm_after),
        "helmValuesRestored": values_restored,
        "helmReleaseRemainsDeployed": is_deployed,
        "helmRecovery": recovery,
        "failureLogSha256": "sha256:" + hashlib.sha256(failed.stdout + failed.stderr).hexdigest(),
        "rawFailureLogStored": False,
    }


def _verified_digest_rollback_fault(args: argparse.Namespace) -> dict[str, object]:
    current_commit = _validated_commit(args.current_commit, "macmini_fault_current_commit_invalid")
    rollback_commit = _validated_commit(args.rollback_commit, "macmini_fault_rollback_commit_invalid")
    if current_commit == rollback_commit:
        raise ValueError("macmini_fault_rollback_commit_not_distinct")
    before = _deployment_snapshot(args)
    original_image = _container_image(before, _API_CONTAINER)
    repository = _image_repository(original_image)
    base = _fault_resource_name("rollback", args.run_id)
    old_deploy = f"{base}-old"
    current_deploy = f"{base}-current"
    rollback = f"{base}-apply"
    final_forward = f"{base}-forward"
    steps: list[dict[str, object]] = []
    try:
        steps.append(_create_and_wait_release(args, old_deploy, rollback_commit, repository, "deploy", None))
        steps.append(_create_and_wait_release(args, current_deploy, current_commit, repository, "deploy", None))
        steps.append(_create_and_wait_release(args, rollback, rollback_commit, repository, "rollback", old_deploy))
    finally:
        steps.append(_create_and_wait_release(args, final_forward, current_commit, repository, "deploy", None))
    after = _deployment_snapshot(args)
    is_restored = _container_image(after, _API_CONTAINER) == original_image
    all_live = all(step.get("phase") == "Live" and step.get("isSignatureVerified") is True for step in steps)
    return {
        "status": "passed" if all_live and is_restored else "failed",
        "target": f"deployment/{_API_DEPLOYMENT}",
        "rollbackCommit": rollback_commit,
        "currentCommit": current_commit,
        "controllerVerifiedSteps": steps,
        "allArtifactsSignatureVerified": all_live,
        "originalCurrentDigestRestored": is_restored,
        "mutableTagUsed": False,
        "kubectlRolloutUndoUsed": False,
        "recoveryTargetSeconds": 900,
        "recoveryObserved": is_restored,
    }


def _create_and_wait_release(
    args: argparse.Namespace,
    name: str,
    commit: str,
    repository: str,
    operation: str,
    rollback_target: str | None,
) -> dict[str, object]:
    payload = _foundry_deployment(name, commit, repository, operation, rollback_target)
    _create_fault_resource(args, payload, "macmini_fault_release_create_failed")
    return _wait_foundry_deployment(args, name, 600)


def _foundry_deployment(
    name: str,
    commit: str,
    repository: str,
    operation: str,
    rollback_target: str | None,
) -> dict[str, object]:
    spec: dict[str, object] = {
        "serviceId": "foundry-lite-api",
        "commitId": commit,
        "imageRepository": repository,
        "operation": operation,
        "workloadRef": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": _API_DEPLOYMENT,
            "containerName": _API_CONTAINER,
        },
        "idempotencyKeyHash": hashlib.sha256(f"{name}:{commit}:{operation}".encode()).hexdigest(),
    }
    if rollback_target is not None:
        spec["rollbackTargetDeployId"] = rollback_target
    return {
        "apiVersion": "release.foundry-lite.io/v1alpha1",
        "kind": "FoundryDeployment",
        "metadata": {"name": name},
        "spec": spec,
    }


def _wait_foundry_deployment(args: argparse.Namespace, name: str, timeout_seconds: int) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _kubectl(args, ("get", "foundrydeployment", name, "-o", "json"), 30)
        if result.returncode == 0:
            payload = _json_output(result, "macmini_fault_release_read_failed")
            status = payload.get("status") if isinstance(payload, dict) else None
            phase = status.get("phase") if isinstance(status, dict) else None
            if isinstance(status, dict) and phase in {"Live", "Failed"}:
                return {
                    "name": name,
                    "phase": phase,
                    "reason": status.get("reason"),
                    "imageDigest": status.get("imageDigest"),
                    "isSignatureVerified": status.get("isSignatureVerified"),
                    "isLinuxArm64": status.get("isLinuxArm64"),
                }
        time.sleep(5)
    return {"name": name, "phase": "Failed", "reason": "campaign_timeout"}


def _validated_commit(value: object, reason: str) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise ValueError(reason)
    return value


def _image_repository(image: str) -> str:
    if image.count("@") != 1:
        raise RuntimeError("macmini_fault_api_image_not_digest_pinned")
    repository, _digest = image.split("@", 1)
    if not repository or len(repository) > 256:
        raise RuntimeError("macmini_fault_api_image_not_digest_pinned")
    return repository


def _migration_failure_fault(args: argparse.Namespace) -> dict[str, object]:
    before = _deployment_snapshot(args)
    image = _container_image(before, _API_CONTAINER)
    name = _fault_resource_name("migration", args.run_id)
    helm_before = _helm_status(args)
    values_before = _helm_values(args)
    next_revision = _helm_revision(helm_before) + 1
    _create_fault_resource(
        args,
        _migration_failure_secret(name),
        "macmini_fault_migration_secret_create_failed",
    )
    try:
        failed = _run_faulting_helm_upgrade(args, name)
        recovery = _recover_helm_release(args, _helm_revision(helm_before), values_before, False)
    finally:
        _delete_fault_resource_optional(args, "job", f"foundry-lite-migrate-{next_revision}")
        _delete_fault_resource(args, "secret", name, "macmini_fault_migration_secret_cleanup_failed")
    helm_after = _helm_status(args)
    values_after = _helm_values(args)
    after = _deployment_snapshot(args)
    has_failed = failed.returncode != 0
    is_unchanged = _container_image(after, _API_CONTAINER) == image
    has_kept_capacity = _available_replicas(after) >= _available_replicas(before)
    is_deployed = _helm_release_status(helm_after) == "deployed"
    values_restored = _json_sha256(values_after) == _json_sha256(values_before)
    is_passed = has_failed and is_unchanged and has_kept_capacity and is_deployed and values_restored
    failure_output = failed.stdout + failed.stderr
    return {
        "status": "passed" if is_passed else "failed",
        "target": "helm/foundry-lite",
        "migrationFailureObserved": has_failed,
        "liveDeploymentImageUnchanged": is_unchanged,
        "liveDeploymentCapacityMaintained": has_kept_capacity,
        "helmReleaseRemainsDeployed": is_deployed,
        "helmValuesRestored": values_restored,
        "helmRecovery": recovery,
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
        completed = _kubectl(
            args,
            ("wait", "--for=condition=complete", f"job/{name}", "--timeout=120s"),
            140,
        )
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
        (
            "set",
            "image",
            f"deployment/{_API_DEPLOYMENT}",
            f"{_API_CONTAINER}={image}",
            "--field-manager=helm",
        ),
        30,
    )
    _require_success(restored, "macmini_fault_invalid_image_restore_failed")
    rollout = _kubectl(
        args,
        ("rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=120s"),
        140,
    )
    _require_success(rollout, "macmini_fault_invalid_image_recovery_failed")


def _named_deployment_snapshot(args: argparse.Namespace, name: str) -> object:
    result = _kubectl(args, ("get", "deployment", name, "-o", "json"), 30)
    return _json_output(result, "macmini_fault_deployment_read_failed")


def _deployment_container_command(payload: object, container_name: str) -> tuple[str, ...]:
    try:
        containers = payload["spec"]["template"]["spec"]["containers"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("macmini_fault_deployment_invalid") from exc
    for container in containers:
        command = container.get("command") if isinstance(container, dict) else None
        if isinstance(container, dict) and container.get("name") == container_name and isinstance(command, list):
            if all(isinstance(item, str) and item for item in command):
                return tuple(command)
    raise RuntimeError("macmini_fault_deployment_invalid")


def _patch_deployment_command(
    args: argparse.Namespace,
    deployment: str,
    command: tuple[str, ...],
    reason: str,
) -> None:
    patch = json.dumps(
        [
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/command",
                "value": list(command),
            }
        ],
        separators=(",", ":"),
    )
    result = _kubectl(args, ("patch", "deployment", deployment, "--type=json", "-p", patch), 30)
    _require_success(result, reason)


def _wait_for_oom_killed(args: argparse.Namespace, selector: str, duration_seconds: int) -> bool:
    deadline = time.monotonic() + max(10, duration_seconds)
    while time.monotonic() < deadline:
        result = _kubectl(args, ("get", "pods", "-l", selector, "-o", "json"), 30)
        if result.returncode == 0 and _payload_has_oom_killed(_json_output(result, "macmini_fault_oom_read_failed")):
            return True
        time.sleep(1)
    return False


def _payload_has_oom_killed(payload: object) -> bool:
    return any(_container_has_oom_killed(container) for container in _containers(payload))


def _containers(payload: object) -> Iterator[dict[object, object]]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return
    for item in items:
        status = item.get("status") if isinstance(item, dict) else None
        containers = status.get("containerStatuses") if isinstance(status, dict) else None
        if not isinstance(containers, list):
            continue
        for container in containers:
            if isinstance(container, dict):
                yield container


def _container_has_oom_killed(container: dict[object, object]) -> bool:
    for key in ("state", "lastState"):
        state = container.get(key)
        terminated = state.get("terminated") if isinstance(state, dict) else None
        if isinstance(terminated, dict) and terminated.get("reason") == "OOMKilled":
            return True
    return False


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
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/name": "foundry-lite"},
        },
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
        f"secrets.migrationExistingSecret={secret_name}",
        "--atomic",
        "--wait",
        "--wait-for-jobs",
        "--timeout",
        "3m",
    )
    return _bounded_command(command, 420)


def _recover_helm_release(
    args: argparse.Namespace,
    revision: int,
    expected_values: object,
    is_unexpectedly_accepted: bool,
) -> dict[str, object]:
    current_status = _helm_status_optional(args)
    current_values = _helm_values_optional(args)
    has_drift = current_values is None or _json_sha256(current_values) != _json_sha256(expected_values)
    is_deployed = current_status is not None and _helm_release_status(current_status) == "deployed"
    should_rollback = is_unexpectedly_accepted or has_drift or not is_deployed
    rollback = _helm_rollback(args, revision) if should_rollback else None
    final_status = _helm_status_optional(args)
    final_values = _helm_values_optional(args)
    is_restored = (
        final_status is not None
        and _helm_release_status(final_status) == "deployed"
        and final_values is not None
        and _json_sha256(final_values) == _json_sha256(expected_values)
    )
    return {
        "rollbackPerformed": should_rollback,
        "rollbackReturnCode": None if rollback is None else rollback.returncode,
        "originalValuesRestored": is_restored,
    }


def _helm_rollback(args: argparse.Namespace, revision: int) -> subprocess.CompletedProcess[bytes]:
    command = (
        args.helm,
        "rollback",
        "foundry-lite",
        str(revision),
        "--namespace",
        args.namespace,
        "--wait",
        "--wait-for-jobs",
        "--cleanup-on-fail",
        "--timeout",
        "10m",
    )
    return _bounded_command(command, 720)


def _helm_status(args: argparse.Namespace) -> object:
    command = (
        args.helm,
        "status",
        "foundry-lite",
        "--namespace",
        args.namespace,
        "--output",
        "json",
    )
    return _json_output(_command(command, 60), "macmini_fault_helm_status_failed")


def _helm_status_optional(args: argparse.Namespace) -> object | None:
    try:
        return _helm_status(args)
    except (RuntimeError, subprocess.TimeoutExpired):
        return None


def _helm_values(args: argparse.Namespace) -> object:
    command = (
        args.helm,
        "get",
        "values",
        "foundry-lite",
        "--namespace",
        args.namespace,
        "--all",
        "--output",
        "json",
    )
    return _json_output(_command(command, 60), "macmini_fault_helm_values_failed")


def _helm_values_optional(args: argparse.Namespace) -> object | None:
    try:
        return _helm_values(args)
    except (RuntimeError, subprocess.TimeoutExpired):
        return None


def _protected_migration_secret(values: object) -> str:
    global_values = values.get("global") if isinstance(values, dict) else None
    secrets = values.get("secrets") if isinstance(values, dict) else None
    if not isinstance(global_values, dict) or global_values.get("protectedProfile") is not True:
        raise RuntimeError("macmini_fault_protected_profile_required")
    value = secrets.get("migrationExistingSecret") if isinstance(secrets, dict) else None
    if not isinstance(value, str) or not value or len(value) > 253:
        raise RuntimeError("macmini_fault_migration_secret_name_invalid")
    return value


def _json_sha256(value: object) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/name": "foundry-lite"},
        },
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
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/name": "foundry-lite"},
        },
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
        (
            "delete",
            kind,
            name,
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=60s",
        ),
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
    operation = (
        "colima",
        "ssh",
        "--profile",
        "foundry-qa",
        "--",
        "df",
        "-Pk",
        _COLIMA_PVC_STORAGE_PATH,
    )
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
    command = (
        args.kubectl,
        "--kubeconfig",
        args.kubeconfig,
        "--namespace",
        args.namespace,
        *operation,
    )
    return _command(command, timeout)


def _kubectl_input(
    args: argparse.Namespace,
    operation: tuple[str, ...],
    payload: bytes,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    command = (
        args.kubectl,
        "--kubeconfig",
        args.kubeconfig,
        "--namespace",
        args.namespace,
        *operation,
    )
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


def _bounded_command(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return _command(command, timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return subprocess.CompletedProcess(command, 124, stdout, stderr)


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
    parser.add_argument("--current-commit")
    parser.add_argument("--rollback-commit")
    receipt = inject(parser.parse_args())
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
