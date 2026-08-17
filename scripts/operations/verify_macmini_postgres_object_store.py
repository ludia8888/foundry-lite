"""Collect live PostgreSQL object-store evidence from the protected API Pod."""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 - fixed kubectl argv under the Mac mini guard.
from pathlib import Path

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    utc_now,
    write_json_receipt,
)

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,100}$")


def collect(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    if _RUN_ID.fullmatch(args.run_id) is None or _TENANT_ID.fullmatch(args.tenant_id) is None:
        raise ValueError("macmini_postgres_object_store_identifier_invalid")
    kubeconfig = _qa_path(args.kubeconfig)
    result = subprocess.run(  # nosec B603 - fixed namespace-bound kubectl exec.
        _command(args, kubeconfig),
        check=False,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("macmini_postgres_object_store_runtime_check_failed")
    evidence = _validated_evidence(_last_json_object(result.stdout))
    receipt = {
        **evidence,
        "runId": args.run_id,
        "namespace": args.namespace,
        "verifiedAt": utc_now(),
        "otherNamespacesMutated": False,
    }
    target = QA_ROOT / "evidence" / args.run_id / "postgres-object-store-live.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _command(args: argparse.Namespace, kubeconfig: Path) -> tuple[str, ...]:
    return (
        args.kubectl,
        "--kubeconfig",
        str(kubeconfig),
        "--namespace",
        args.namespace,
        "exec",
        "deployment/foundry-lite",
        "-c",
        "api",
        "--",
        "/opt/foundry-lite-venv/bin/python",
        "/app/scripts/operations/verify_postgres_object_store_runtime.py",
        "--tenant-id",
        args.tenant_id,
        "--expected-role",
        "foundry_lite_app",
    )


def _validated_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("macmini_postgres_object_store_evidence_invalid")
    expected = {
        "status": "passed",
        "databaseBackend": "postgresql",
        "runtimeRole": "foundry_lite_app",
        "isSuperuser": False,
        "canBypassRls": False,
        "jsonbColumnCount": 15,
        "productionIndexCount": 10,
        "jsonbPathOpsGinIndexCount": 2,
        "forcedRlsTableCount": 9,
        "noTenantRowsVisible": True,
        "otherTenantRowsVisible": False,
        "crossTenantWriteBlocked": True,
        "rawCredentialsStored": False,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise RuntimeError("macmini_postgres_object_store_evidence_invalid")
    if not isinstance(value.get("visibleTenantRowCount"), int) or value["visibleTenantRowCount"] < 1:
        raise RuntimeError("macmini_postgres_object_store_evidence_invalid")
    return value


def _last_json_object(value: bytes) -> dict[str, object] | None:
    for raw_line in reversed(value.splitlines()):
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _qa_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_symlink():
        raise ValueError("macmini_postgres_object_store_kubeconfig_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("macmini_postgres_object_store_kubeconfig_invalid") from exc
    if resolved.parent != QA_ROOT / "state" or not resolved.is_file():
        raise ValueError("macmini_postgres_object_store_kubeconfig_invalid")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--tenant-id", default="tenant-demo")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    collect(parser.parse_args(argv))
    print('{"receiptStored":true,"status":"passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
