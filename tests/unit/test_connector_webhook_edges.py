from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from foundry_lite.application.services.dataset.connector_snapshot_ingest import (
    _connector_fieldnames,
    _resumable_sync_plan,
)
from foundry_lite.application.services.dataset.webhook_ingest import (
    _webhook_event_id,
    _webhook_payload_event_id,
    _webhook_signature_valid,
)
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed


def test_resumable_connector_sync_plan_requires_extracting_transaction() -> None:
    plan = _resumable_sync_plan({"status": "EXTRACTING", "transaction_id": "tx-1"}, "sync-1")

    assert plan.transaction_id == "tx-1"
    assert plan.run_id == "sync-1"
    with pytest.raises(ConflictDetected, match="not resumable"):
        _resumable_sync_plan({"status": "COMMITTED", "transaction_id": "tx-1"}, "sync-1")
    with pytest.raises(ConflictDetected, match="not resumable"):
        _resumable_sync_plan({"status": "EXTRACTING", "transaction_id": ""}, "sync-1")


def test_connector_snapshot_fieldnames_fall_back_to_row_shape() -> None:
    assert _connector_fieldnames({"columns": ["id", "name"]}, []) == ["id", "name"]
    assert _connector_fieldnames({"columns": ["id", 7]}, [{"fallback": 1, "value": 2}]) == [
        "fallback",
        "value",
    ]
    with pytest.raises(ValidationFailed, match="no schema columns"):
        _connector_fieldnames({}, [])


def test_webhook_signature_requires_secret_and_valid_fresh_timestamp() -> None:
    body = b'{"id":"evt-1"}'

    with pytest.raises(ValidationFailed, match="secret is not configured"):
        _webhook_signature_valid(body, "sha256=unused", _fresh_timestamp(), "")
    with pytest.raises(PermissionDenied, match="invalid webhook timestamp"):
        _webhook_signature_valid(body, "sha256=unused", "not-a-date", "secret")
    with pytest.raises(PermissionDenied, match="invalid webhook timestamp"):
        _webhook_signature_valid(body, "sha256=unused", datetime.now().replace(microsecond=0).isoformat(), "secret")
    with pytest.raises(PermissionDenied, match="outside replay window"):
        _webhook_signature_valid(body, "sha256=unused", _stale_timestamp(), "secret")


def test_webhook_event_id_uses_stable_payload_identity() -> None:
    assert _webhook_payload_event_id({"eventId": "evt-1"}) == "evt-1"
    assert _webhook_payload_event_id({"eventId": ""}) is None
    explicit = _webhook_event_id("mock_saas", "orders", {"eventId": "evt-1", "timestamp": "a"}, None)
    replay = _webhook_event_id("mock_saas", "orders", {"eventId": "evt-1", "timestamp": "b"}, None)
    stable_a = _webhook_event_id("mock_saas", "orders", {"value": 1, "timestamp": "a"}, None)
    stable_b = _webhook_event_id("mock_saas", "orders", {"value": 1, "timestamp": "b"}, None)

    assert explicit == replay
    assert stable_a == stable_b
    assert _webhook_event_id("mock_saas", "orders", {"value": 1}, "provided-event") == "provided-event"


def _fresh_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _stale_timestamp() -> str:
    return (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
