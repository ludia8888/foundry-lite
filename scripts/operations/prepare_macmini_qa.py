"""Prepare only sean1234's dedicated Colima/Kubernetes QA environment."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from scripts.operations.macmini_qa_guard import (
    COLIMA_PROFILE,
    QA_ROOT,
    CommandResult,
    assert_host_boundary,
    assert_profile,
    ensure_qa_directories,
    run,
    utc_now,
    write_json_receipt,
)


def prepare(*, profile: str, should_restart: bool) -> dict[str, object]:
    assert_profile(profile)
    assert_host_boundary()
    ensure_qa_directories()
    before = run(("colima", "list", "--json"), timeout_seconds=30)
    if before.return_code != 0:
        raise RuntimeError("macmini_qa_colima_inventory_failed")
    commands: list[dict[str, object]] = []
    if should_restart:
        commands.extend(_restart_profile(profile))
    status = run(("colima", "status", profile, "--json"), timeout_seconds=30)
    if status.return_code != 0:
        raise RuntimeError("macmini_qa_colima_status_failed")
    readiness = _wait_for_k3s(profile)
    encryption = _secrets_encryption_status(profile)
    kubeconfig = _install_kubeconfig(profile)
    return {
        "schemaVersion": 2,
        "recordedAt": utc_now(),
        "principal": "sean1234",
        "qaRoot": str(QA_ROOT),
        "colimaProfile": profile,
        "requestedResources": {"cpu": 6, "memoryGiB": 16, "diskGiB": 120},
        "hostRebooted": False,
        "otherProfilesMutated": False,
        "commands": commands,
        "inventoryBefore": _json_or_text(before.stdout),
        "statusAfter": _json_or_text(status.stdout),
        "k3sReadiness": readiness.stdout.strip(),
        "secretsEncryption": encryption,
        "kubeconfig": str(kubeconfig),
    }


def _wait_for_k3s(profile: str, *, attempts: int = 90) -> CommandResult:
    command = ("colima", "ssh", "--profile", profile, "--", "sudo", "k3s", "kubectl", "get", "--raw=/readyz")
    for attempt in range(attempts):
        result = run(command, timeout_seconds=10)
        if result.return_code == 0 and result.stdout.strip() == "ok":
            return result
        if attempt + 1 < attempts:
            time.sleep(2)
    raise RuntimeError("macmini_qa_k3s_not_ready")


def _secrets_encryption_status(profile: str) -> dict[str, object]:
    server = _k3s_server_url(profile)
    command = (
        "colima",
        "ssh",
        "--profile",
        profile,
        "--",
        "sudo",
        "k3s",
        "secrets-encrypt",
        "status",
        "--server",
        server,
        "--output",
        "json",
    )
    result = run(command, timeout_seconds=30)
    payload = _json_or_text(result.stdout)
    if result.return_code != 0 or not isinstance(payload, dict):
        raise RuntimeError("macmini_qa_k3s_secret_encryption_status_failed")
    if payload.get("enable") is not True or payload.get("hashmatch") is not True:
        raise RuntimeError("macmini_qa_k3s_secret_encryption_not_enabled")
    return payload


def _k3s_server_url(profile: str) -> str:
    command = (
        "colima",
        "ssh",
        "--profile",
        profile,
        "--",
        "sudo",
        "awk",
        "/^[[:space:]]*server:/ {print $2; exit}",
        "/etc/rancher/k3s/k3s.yaml",
    )
    result = run(command, timeout_seconds=30)
    server = result.stdout.strip()
    try:
        parsed = urlparse(server)
        port = parsed.port
    except ValueError as error:
        raise RuntimeError("macmini_qa_k3s_server_url_invalid") from error
    if result.return_code != 0 or parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("macmini_qa_k3s_server_url_invalid")
    if port is None or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("macmini_qa_k3s_server_url_invalid")
    return server


def _install_kubeconfig(profile: str) -> Path:
    context = f"colima-{profile}"
    exported = run(("kubectl", "config", "view", "--raw", "--minify", "--context", context), timeout_seconds=30)
    if exported.return_code != 0 or not exported.stdout.strip():
        raise RuntimeError("macmini_qa_kubeconfig_export_failed")
    target = QA_ROOT / "state" / "kubeconfig"
    pending = target.with_name(f".{target.name}-{os.getpid()}.pending")
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(exported.stdout)
        validation = run(("kubectl", "--kubeconfig", str(pending), "get", "--raw=/readyz"), timeout_seconds=30)
        if validation.return_code != 0 or validation.stdout.strip() != "ok":
            raise RuntimeError("macmini_qa_kubeconfig_validation_failed")
        os.replace(pending, target)
        os.chmod(target, 0o600)
    finally:
        pending.unlink(missing_ok=True)
    return target


def _restart_profile(profile: str) -> list[dict[str, object]]:
    stop = run(("colima", "stop", profile), timeout_seconds=180)
    if stop.return_code not in {0, 1}:
        raise RuntimeError("macmini_qa_colima_stop_failed")
    start = run(
        (
            "colima",
            "start",
            profile,
            "--arch",
            "aarch64",
            "--cpu",
            "6",
            "--memory",
            "16",
            "--disk",
            "120",
            "--runtime",
            "docker",
            "--kubernetes",
            "--k3s-arg=--secrets-encryption",
        ),
        timeout_seconds=900,
    )
    if start.return_code != 0:
        raise RuntimeError("macmini_qa_colima_start_failed")
    return [_command_receipt(stop), _command_receipt(start)]


def _command_receipt(result: CommandResult) -> dict[str, object]:
    return {
        "argv": list(result.argv),
        "returnCode": result.return_code,
        "stdoutTail": result.stdout[-2000:],
        "stderrTail": result.stderr[-2000:],
    }


def _json_or_text(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value[-4000:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=COLIMA_PROFILE)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    receipt = prepare(profile=args.profile, should_restart=args.restart)
    target = Path(QA_ROOT, "evidence", args.run_id, "host-preflight.json")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
