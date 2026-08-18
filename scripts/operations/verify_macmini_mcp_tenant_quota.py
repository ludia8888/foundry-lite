"""Collect live tenant quota proof from the exact deployed API image."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed namespace-bound kubectl exec only.

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    utc_now,
    write_json_receipt,
)


def collect(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    command = (
        args.kubectl,
        "--kubeconfig",
        args.kubeconfig,
        "--namespace",
        args.namespace,
        "exec",
        "deployment/foundry-lite",
        "-c",
        "api",
        "--",
        "/opt/foundry-lite-venv/bin/python",
        "/app/scripts/operations/verify_mcp_tenant_quota_runtime.py",
        "--run-id",
        args.run_id,
    )
    result = subprocess.run(command, check=False, capture_output=True, timeout=300)  # nosec B603
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("macmini_mcp_quota_runtime_check_failed")
    payload = _validated(_last_json(result.stdout))
    receipt = {
        **payload,
        "runId": args.run_id,
        "namespace": args.namespace,
        "verifiedAt": utc_now(),
    }
    target = QA_ROOT / "evidence" / args.run_id / "mcp-tenant-quota-live.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _validated(payload: object) -> dict[str, object]:
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "passed"
        or payload.get("tenantAQuotaDenied") is not True
        or payload.get("tenantBStillAllowed") is not True
        or payload.get("durableDenialAuditOutboxRequested") is not True
        or payload.get("rawIdentityStored") is not False
    ):
        raise RuntimeError("macmini_mcp_quota_evidence_invalid")
    return payload


def _last_json(raw: bytes) -> dict[str, object] | None:
    for line in reversed(raw.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    receipt = collect(parser.parse_args())
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
