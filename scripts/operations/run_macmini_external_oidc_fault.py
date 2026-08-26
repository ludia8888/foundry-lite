"""Prove external OIDC fail-closed behavior and recovery around a Keycloak outage."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed namespace-scoped QA script and kubectl argv only.
import time

from scripts.operations import verify_macmini_external_oidc
from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    qa_command_environment,
    write_json_receipt,
)

_PYTHONPATH = ".:libs:apps/cli:apps/api:apps/worker"
_SCALE_DOWN_DEADLINE_SECONDS = 60


def run(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    if not 15 <= args.duration_seconds <= 120:
        raise ValueError("macmini_external_oidc_fault_duration_invalid")
    pre_fault = verify_macmini_external_oidc.verify(_phase_args(args, "pre"))
    process = _start_fault(args)
    try:
        scale_down = _wait_for_scale_down(args)
        outage = _verify_outage(_phase_args(args, "outage")) if scale_down else {"status": "failed", "rejected": False}
        fault = _finish_fault(process, args.duration_seconds + 420)
    except BaseException:
        _finish_fault(process, args.duration_seconds + 420)
        raise
    post_fault = verify_macmini_external_oidc.verify(_phase_args(args, "post"))
    status = _status(pre_fault, scale_down, outage, fault, post_fault)
    receipt = {
        "schemaVersion": 1,
        "status": status,
        "fault": "dependency-keycloak",
        "preFaultVerification": pre_fault,
        "scaleDownObserved": scale_down,
        "outageVerification": outage,
        "faultReceipt": fault,
        "postFaultVerification": post_fault,
        "rawTokensStored": False,
    }
    target = QA_ROOT / "evidence" / args.run_id / "external-oidc-fault.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _start_fault(args: argparse.Namespace) -> subprocess.Popen[bytes]:
    command = (
        str(QA_ROOT / "bin" / "uv"),
        "run",
        "python",
        "scripts/operations/inject_macmini_fault.py",
        "--run-id",
        args.run_id,
        "--namespace",
        args.namespace,
        "--kubeconfig",
        args.kubeconfig,
        "--fault",
        "dependency-keycloak",
        "--duration-seconds",
        str(args.duration_seconds),
    )
    return subprocess.Popen(  # nosec B603 - fixed QA script argv without shell.
        command,
        cwd=QA_ROOT / "repo",
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _phase_args(args: argparse.Namespace, phase: str) -> argparse.Namespace:
    values = vars(args).copy()
    values["run_id"] = f"{args.run_id}-oidc-{phase}"
    return argparse.Namespace(**values)


def _wait_for_scale_down(args: argparse.Namespace) -> bool:
    deadline = time.monotonic() + _SCALE_DOWN_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        result = _kubectl(args, ("get", "statefulset", "foundry-lite-keycloak", "-o", "json"))
        if result.returncode == 0 and _is_scaled_down(result.stdout):
            return True
        time.sleep(1)
    return False


def _is_scaled_down(raw: bytes) -> bool:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    spec = payload.get("spec") if isinstance(payload, dict) else None
    status = payload.get("status") if isinstance(payload, dict) else None
    replicas = spec.get("replicas") if isinstance(spec, dict) else None
    ready = status.get("readyReplicas", 0) if isinstance(status, dict) else None
    return replicas == 0 and ready == 0


def _verify_outage(args: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    try:
        verify_macmini_external_oidc.verify(args)
    except Exception as exc:  # noqa: BLE001 - any fail-closed adapter rejection is the required outage evidence.
        return {
            "status": "passed",
            "rejected": True,
            "failureType": type(exc).__name__,
            "durationMs": int((time.monotonic() - started) * 1000),
            "rawTokensStored": False,
        }
    return {
        "status": "failed",
        "rejected": False,
        "durationMs": int((time.monotonic() - started) * 1000),
        "rawTokensStored": False,
    }


def _finish_fault(process: subprocess.Popen[bytes], timeout_seconds: int) -> dict[str, object]:
    try:
        stdout, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.terminate()
        stdout, _ = process.communicate(timeout=30)
    payload = _last_json(stdout)
    if process.returncode != 0 or payload.get("status") != "passed":
        return {"status": "failed", "returnCode": process.returncode, "rawOutputStored": False}
    return {**payload, "rawOutputStored": False}


def _kubectl(args: argparse.Namespace, operation: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    command = (args.kubectl, "--kubeconfig", args.kubeconfig, "--namespace", args.namespace, *operation)
    return subprocess.run(command, check=False, capture_output=True, timeout=30)  # nosec B603 - fixed kubectl argv.


def _status(
    pre_fault: dict[str, object],
    is_scaled_down: bool,
    outage: dict[str, object],
    fault: dict[str, object],
    post_fault: dict[str, object],
) -> str:
    values = (pre_fault.get("status"), outage.get("status"), fault.get("status"), post_fault.get("status"))
    return "passed" if is_scaled_down and all(value == "passed" for value in values) else "failed"


def _last_json(raw: bytes) -> dict[str, object]:
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _environment() -> dict[str, str]:
    environment = qa_command_environment()
    environment["PYTHONPATH"] = _PYTHONPATH
    return environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--discovery-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--allowed-client-id", action="append", required=True)
    parser.add_argument("--author-token-file", required=True)
    parser.add_argument("--reviewer-token-file", required=True)
    parser.add_argument("--duration-seconds", type=int, default=45)
    try:
        receipt = run(parser.parse_args(argv))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "failed", "reason": type(exc).__name__}))
        return 1
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
