from __future__ import annotations

import json
import subprocess
from argparse import Namespace

import pytest

from scripts.operations import run_macmini_operational_probe as subject


def test_operational_probe_returns_only_bounded_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "databaseConnections": 6,
        "outboxPendingCount": 0,
        "outboxEnqueuedCount": 12,
        "outboxPublishedCount": 12,
        "oldestOutboxPendingSeconds": 0,
        "outboxPendingTenantCount": 0,
        "outboxPendingByTenant": [],
        "outboxWatermark": {"createdAt": "2026-08-26T00:00:00+00:00", "eventId": "evt-12"},
        "deadLetterCount": 4,
    }
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda namespace: None)
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
    )

    receipt = subject.collect(
        Namespace(
            namespace="foundry-qa",
            kubeconfig="/private/kubeconfig",
            kubectl="kubectl",
            timeout_seconds=30,
        )
    )

    assert receipt["status"] == "passed"
    assert receipt["outboxPendingCount"] == 0
    assert receipt["outboxPendingByTenant"] == []
    assert receipt["outboxWatermark"]["eventId"] == "evt-12"
    assert "databaseUrl" not in receipt


def test_operational_probe_adds_exact_watermark_query_without_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "databaseConnections": 6,
        "outboxPendingCount": 2,
        "outboxEnqueuedCount": 14,
        "outboxPublishedCount": 12,
        "oldestOutboxPendingSeconds": 1,
        "outboxPendingTenantCount": 1,
        "outboxPendingByTenant": [{"tenantId": "tenant-a", "pendingCount": 2, "oldestPendingSeconds": 1}],
        "outboxWatermark": {"createdAt": "2026-08-26T00:00:01+00:00", "eventId": "evt-14"},
        "outboxUnpublishedAtWatermarkCount": 0,
        "oldestOutboxUnpublishedAtWatermarkSeconds": 0,
        "deadLetterCount": 4,
    }
    observed: list[tuple[str, ...]] = []
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda command, **_kwargs: (
            observed.append(command) or subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")
        ),
    )

    receipt = subject.collect(
        Namespace(
            namespace="foundry-qa",
            kubeconfig="/private/kubeconfig",
            kubectl="kubectl",
            timeout_seconds=30,
            outbox_watermark_created_at="2026-08-26T00:00:00+00:00",
            outbox_watermark_event_id="evt-12",
        )
    )

    assert receipt["outboxPendingCount"] == 2
    assert receipt["outboxUnpublishedAtWatermarkCount"] == 0
    assert "outbox_watermark_created_at=2026-08-26T00:00:00+00:00" in observed[0]
    assert "outbox_watermark_event_id=evt-12" in observed[0]
    assert "-i" in observed[0]
    assert "outboxUnpublishedAtWatermarkCount" not in " ".join(observed[0])


def test_operational_probe_rejects_negative_or_non_integer_metrics() -> None:
    with pytest.raises(RuntimeError, match="postgresql_invalid"):
        subject._validated_payload(b'{"databaseConnections":-1}')


def test_operational_probe_rejects_unbounded_or_invalid_tenant_breakdown() -> None:
    assert subject._is_pending_by_tenant([]) is True
    assert (
        subject._is_pending_by_tenant([{"tenantId": "tenant-a", "pendingCount": 1, "oldestPendingSeconds": 5}]) is True
    )
    assert subject._is_pending_by_tenant([{"tenantId": "", "pendingCount": 1, "oldestPendingSeconds": 5}]) is False
    assert subject._is_pending_by_tenant([{}] * 101) is False


def test_operational_probe_rejects_partial_or_malformed_watermark() -> None:
    with pytest.raises(ValueError, match="watermark_invalid"):
        subject._validated_watermark_args(
            Namespace(outbox_watermark_created_at="2026-08-26T00:00:00+00:00", outbox_watermark_event_id=None)
        )
    with pytest.raises(ValueError, match="watermark_invalid"):
        subject._validated_watermark_args(
            Namespace(outbox_watermark_created_at="not-a-time", outbox_watermark_event_id="evt-1")
        )
