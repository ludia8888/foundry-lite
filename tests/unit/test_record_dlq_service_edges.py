from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports import DeadLetterRecordRow
from foundry_lite.application.services.record_dlq_service import (
    _discard_result,
    _ensure_retryable,
    _optional_status,
    _redact_sensitive,
    _retry_result,
)
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


def test_record_dlq_status_and_retry_guards_cover_closed_edges() -> None:
    assert _optional_status(None) is None
    assert _optional_status("QUARANTINED") == "QUARANTINED"
    with pytest.raises(ValidationFailed, match="invalid dead-letter record status"):
        _optional_status("BROKEN")

    with pytest.raises(ConflictDetected, match="contract-specific remediation"):
        _ensure_retryable(_row(metadata={"recordDlqKind": "data_quality_contract"}), "retry-1")
    with pytest.raises(ConflictDetected, match="different key"):
        _ensure_retryable(_row(replay_idempotency_key="retry-old", replay_status="REQUESTED"), "retry-new")
    with pytest.raises(ConflictDetected, match="already closed"):
        _ensure_retryable(_row(status="RESOLVED"), "retry-1")
    with pytest.raises(ConflictDetected, match="not retryable"):
        _ensure_retryable(_row(status="REPLAY_REQUESTED"), "retry-1")

    _ensure_retryable(_row(replay_idempotency_key="retry-old", replay_status="FAILED"), "retry-new")


def test_record_dlq_result_payloads_preserve_replay_and_discard_evidence() -> None:
    row = _row(
        replay_run_id="sync_run_1",
        metadata={
            "replayResult": {"datasetVersionId": "version-replayed", "rowCount": 1},
            "lastReplayError": {"code": "TRANSIENT_SOURCE_ERROR"},
        },
        discarded_at="2026-06-19T00:00:00Z",
    )

    retry = _retry_result(row, is_idempotent_replay=True)
    discard = _discard_result(row)

    assert retry["replayDatasetVersionId"] == "version-replayed"
    assert retry["rowCount"] == 1
    assert retry["error"] == {"code": "TRANSIENT_SOURCE_ERROR"}
    assert retry["downstreamBackfillPlan"]["affectedDatasetVersionId"] == "version_raw_1"
    assert retry["is_idempotent_replay"] is True
    assert discard == {
        "deadLetterRecordId": "dlq_1",
        "status": "QUARANTINED",
        "replayStatus": "PENDING",
        "discardedAt": "2026-06-19T00:00:00Z",
    }

    with pytest.raises(ValidationFailed, match="replay run is missing"):
        _retry_result(_row(replay_run_id=None), is_idempotent_replay=False)
    with pytest.raises(ValidationFailed, match="discard timestamp is missing"):
        _discard_result(_row(discarded_at=None))


def test_record_dlq_redacts_nested_sensitive_values() -> None:
    payload = {
        "customer": {"ssn": "123-45-6789", "name": "Ada"},
        "events": [{"secret_token": "raw-token"}, {"status": "ok"}],
    }

    assert _redact_sensitive(payload, {"ssn", "secret_token"}) == {
        "customer": {"ssn": "***MASKED***", "name": "Ada"},
        "events": [{"secret_token": "***MASKED***"}, {"status": "ok"}],
    }


def _row(**overrides: object) -> DeadLetterRecordRow:
    row = {
        "id": "dlq_1",
        "tenant_id": "tenant-demo",
        "source_event_id": "event_1",
        "source_dataset_version_id": "version_raw_1",
        "source_run_id": "sync_1",
        "payload": {"order_id": "O-1"},
        "payload_hash": "hash_1",
        "schema_version": 1,
        "transform_version": None,
        "error_kind": "VALIDATION_FAILED",
        "error_message": "bad record",
        "event_time": None,
        "ingested_at": "2026-06-19T00:00:00Z",
        "first_failed_at": "2026-06-19T00:00:00Z",
        "attempts": 1,
        "status": "QUARANTINED",
        "replay_status": "PENDING",
        "replay_run_id": "sync_run_1",
        "is_closed_partition_affected": True,
        "metadata": {},
        "replay_idempotency_key": None,
        "replay_requested_at": None,
        "discarded_at": None,
        "backfill_plan": None,
    }
    row.update(overrides)
    return cast(DeadLetterRecordRow, row)
