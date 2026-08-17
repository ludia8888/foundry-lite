"""Prepare only sean1234's dedicated Colima/Kubernetes QA environment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    encryption = run(
        ("colima", "ssh", "--profile", profile, "--", "sudo", "k3s", "secrets-encrypt", "status"),
        timeout_seconds=30,
    )
    if encryption.return_code != 0 or "Encryption Status: Enabled" not in encryption.stdout:
        raise RuntimeError("macmini_qa_k3s_secret_encryption_not_enabled")
    return {
        "schemaVersion": 1,
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
        "secretsEncryption": encryption.stdout[-2000:],
    }


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
