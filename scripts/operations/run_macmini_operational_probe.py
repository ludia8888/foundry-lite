"""Collect secret-free PostgreSQL and durable-queue evidence from Mac mini QA."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed namespace-scoped kubectl argv only.
from datetime import UTC, datetime

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
)

_QUERY = """
SELECT json_build_object(
  'databaseConnections', (
    SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
  ),
  'outboxPendingCount', (
    SELECT count(*) FROM outbox_events WHERE status = 'pending' AND published_at IS NULL
  ),
  'oldestOutboxPendingSeconds', COALESCE((
    SELECT greatest(0, extract(epoch FROM (now() - min(created_at::timestamptz))))::bigint
    FROM outbox_events WHERE status = 'pending' AND published_at IS NULL
  ), 0),
  'deadLetterCount', (SELECT count(*) FROM dead_letter_events)
);
""".strip()


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
        "statefulset/foundry-lite-postgresql",
        "--",
        "psql",
        "-U",
        "postgres",
        "-d",
        "foundry_lite",
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        _QUERY,
    )
    result = subprocess.run(  # nosec B603 - fixed kubectl/psql argv and no shell.
        command,
        check=False,
        capture_output=True,
        timeout=args.timeout_seconds,
    )
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        raise RuntimeError("macmini_operational_probe_postgresql_failed")
    payload = _validated_payload(result.stdout)
    return {
        "schemaVersion": 1,
        "status": "passed",
        "observedAt": datetime.now(UTC).isoformat(),
        **payload,
    }


def _validated_payload(raw: bytes) -> dict[str, int]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_operational_probe_postgresql_invalid") from exc
    fields = (
        "databaseConnections",
        "outboxPendingCount",
        "oldestOutboxPendingSeconds",
        "deadLetterCount",
    )
    if not isinstance(payload, dict) or not all(_is_nonnegative_integer(payload.get(field)) for field in fields):
        raise RuntimeError("macmini_operational_probe_postgresql_invalid")
    return {field: int(payload[field]) for field in fields}


def _is_nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    try:
        receipt = collect(parser.parse_args())
    except (RuntimeError, ValueError, subprocess.TimeoutExpired, OSError) as exc:
        print(
            json.dumps(
                {"schemaVersion": 1, "status": "failed", "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
