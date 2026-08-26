"""Collect secret-free PostgreSQL and durable-queue evidence from Mac mini QA."""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 - fixed namespace-scoped kubectl argv only.
from datetime import UTC, datetime

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
)

_OUTBOX_EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_QUERY_HEAD = """
SELECT json_build_object(
  'databaseConnections', (
    SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
  ),
  'outboxPendingCount', (
    SELECT count(*) FROM outbox_events WHERE status = 'pending' AND published_at IS NULL
  ),
  'outboxEnqueuedCount', (SELECT count(*) FROM outbox_events),
  'outboxPublishedCount', (
    SELECT count(*) FROM outbox_events WHERE status = 'published' AND published_at IS NOT NULL
  ),
  'oldestOutboxPendingSeconds', COALESCE((
    SELECT greatest(0, extract(epoch FROM (now() - min(created_at::timestamptz))))::bigint
    FROM outbox_events WHERE status = 'pending' AND published_at IS NULL
  ), 0),
  'outboxPendingTenantCount', (
    SELECT count(DISTINCT tenant_id)
    FROM outbox_events WHERE status = 'pending' AND published_at IS NULL
  ),
  'outboxPendingByTenant', COALESCE((
    SELECT json_agg(pending_tenant)
    FROM (
      SELECT
        tenant_id AS "tenantId",
        count(*)::bigint AS "pendingCount",
        greatest(0, extract(epoch FROM (now() - min(created_at::timestamptz))))::bigint
          AS "oldestPendingSeconds"
      FROM outbox_events
      WHERE status = 'pending' AND published_at IS NULL
      GROUP BY tenant_id
      ORDER BY tenant_id
      LIMIT 100
    ) AS pending_tenant
  ), '[]'::json),
  'outboxWatermark', (
    SELECT json_build_object('createdAt', created_at, 'eventId', id)
    FROM outbox_events
    ORDER BY created_at::timestamptz DESC, id DESC
    LIMIT 1
  ),
""".strip()
_QUERY_TAIL = """
  'deadLetterCount', (SELECT count(*) FROM dead_letter_events)
);
""".strip()
_WATERMARK_QUERY_FIELDS = """
  'outboxUnpublishedAtWatermarkCount', (
    SELECT count(*)
    FROM outbox_events
    WHERE status IN ('pending', 'publishing')
      AND (created_at::timestamptz, id) <= (
        :'outbox_watermark_created_at'::timestamptz,
        :'outbox_watermark_event_id'
      )
  ),
  'oldestOutboxUnpublishedAtWatermarkSeconds', COALESCE((
    SELECT greatest(0, extract(epoch FROM (now() - min(created_at::timestamptz))))::bigint
    FROM outbox_events
    WHERE status IN ('pending', 'publishing')
      AND (created_at::timestamptz, id) <= (
        :'outbox_watermark_created_at'::timestamptz,
        :'outbox_watermark_event_id'
      )
  ), 0),
""".strip()


def _query(is_watermarked: bool) -> str:
    middle = f"\n{_WATERMARK_QUERY_FIELDS}" if is_watermarked else ""
    return f"{_QUERY_HEAD}{middle}\n{_QUERY_TAIL}"


def collect(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    watermark = _validated_watermark_args(args)
    command = (
        args.kubectl,
        "--kubeconfig",
        args.kubeconfig,
        "--namespace",
        args.namespace,
        "exec",
        "-i",
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
        *_watermark_psql_args(watermark),
    )
    result = subprocess.run(  # nosec B603 - fixed kubectl/psql argv and no shell.
        command,
        input=(_query(watermark is not None) + "\n").encode(),
        check=False,
        capture_output=True,
        timeout=args.timeout_seconds,
    )
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        raise RuntimeError("macmini_operational_probe_postgresql_failed")
    payload = _validated_payload(result.stdout, is_watermarked=watermark is not None)
    return {
        "schemaVersion": 1,
        "status": "passed",
        "observedAt": datetime.now(UTC).isoformat(),
        **payload,
    }


def _validated_payload(raw: bytes, *, is_watermarked: bool = False) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_operational_probe_postgresql_invalid") from exc
    fields = (
        "databaseConnections",
        "outboxPendingCount",
        "outboxEnqueuedCount",
        "outboxPublishedCount",
        "oldestOutboxPendingSeconds",
        "outboxPendingTenantCount",
        "deadLetterCount",
    )
    if not isinstance(payload, dict) or not all(_is_nonnegative_integer(payload.get(field)) for field in fields):
        raise RuntimeError("macmini_operational_probe_postgresql_invalid")
    pending_by_tenant = payload.get("outboxPendingByTenant")
    watermark = payload.get("outboxWatermark")
    if not _is_pending_by_tenant(pending_by_tenant) or not _is_watermark(watermark):
        raise RuntimeError("macmini_operational_probe_postgresql_invalid")
    result = {
        **{field: int(payload[field]) for field in fields},
        "outboxPendingByTenant": pending_by_tenant,
        "outboxWatermark": watermark,
    }
    if is_watermarked:
        result.update(_validated_watermark_metrics(payload))
    return result


def _validated_watermark_metrics(payload: dict[str, object]) -> dict[str, int]:
    fields = ("outboxUnpublishedAtWatermarkCount", "oldestOutboxUnpublishedAtWatermarkSeconds")
    result: dict[str, int] = {}
    for field in fields:
        value = payload.get(field)
        if not _is_nonnegative_integer(value):
            raise RuntimeError("macmini_operational_probe_postgresql_invalid")
        assert isinstance(value, int) and not isinstance(value, bool)
        result[field] = value
    return result


def _validated_watermark_args(args: argparse.Namespace) -> tuple[str, str] | None:
    created_at = getattr(args, "outbox_watermark_created_at", None)
    event_id = getattr(args, "outbox_watermark_event_id", None)
    if created_at is None and event_id is None:
        return None
    if not isinstance(created_at, str) or not isinstance(event_id, str):
        raise ValueError("macmini_operational_probe_watermark_invalid")
    if not _is_created_at(created_at) or _OUTBOX_EVENT_ID.fullmatch(event_id) is None:
        raise ValueError("macmini_operational_probe_watermark_invalid")
    return created_at, event_id


def _watermark_psql_args(watermark: tuple[str, str] | None) -> tuple[str, ...]:
    if watermark is None:
        return ()
    created_at, event_id = watermark
    return ("-v", f"outbox_watermark_created_at={created_at}", "-v", f"outbox_watermark_event_id={event_id}")


def _is_watermark(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and _is_created_at(value.get("createdAt"))
        and isinstance(value.get("eventId"), str)
        and _OUTBOX_EVENT_ID.fullmatch(str(value["eventId"])) is not None
    )


def _is_created_at(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_pending_by_tenant(value: object) -> bool:
    if not isinstance(value, list) or len(value) > 100:
        return False
    return all(_is_pending_tenant(item) for item in value)


def _is_pending_tenant(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    tenant_id = value.get("tenantId")
    return (
        isinstance(tenant_id, str)
        and 0 < len(tenant_id) <= 200
        and _is_nonnegative_integer(value.get("pendingCount"))
        and _is_nonnegative_integer(value.get("oldestPendingSeconds"))
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--outbox-watermark-created-at")
    parser.add_argument("--outbox-watermark-event-id")
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
