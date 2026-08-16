"""Inject and automatically recover namespace-scoped Mac mini QA faults."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess  # nosec B404 - fixed kubectl only; remove if arbitrary command input is introduced.
import time
from datetime import UTC, datetime

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace

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
    rollout = _kubectl(args, ("wait", "--for=condition=Ready", "pods", "--all", "--timeout=600s"), 620)
    return {
        "status": "passed" if rollout.returncode == 0 else "failed",
        "target": "colima/foundry-qa",
        "recoveryTargetSeconds": 600,
        "recoveryObserved": rollout.returncode == 0,
    }


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


def _command(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec B603 - allowlisted fault argv; remove if shell or free argv appears.
        command, check=False, capture_output=True, timeout=timeout
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
    parser.add_argument("--duration-seconds", type=int, default=15)
    parser.add_argument("--fault", required=True)
    receipt = inject(parser.parse_args())
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
