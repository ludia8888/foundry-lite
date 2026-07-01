from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DeadLetterRecordRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.dataset.record_dlq_replay import (
    _failure_metadata,
    _int_metadata,
    _optional_str_metadata,
    _replay_lease_token,
    _replay_run_id,
    _schema_strategy,
    _str_metadata,
    _success_metadata,
)
from foundry_lite.application.services.record_dlq_service import (
    _bulk_retry_failure,
    _bulk_retry_result,
    _discard_result,
    _ensure_retryable,
    _optional_status,
    _redact_sensitive,
    _retry_result,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure import schema as db
from sqlalchemy import Connection, select


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


def test_record_dlq_bulk_retry_result_reports_partial_success() -> None:
    retry = _retry_result(_row(replay_run_id="sync_run_bulk"), is_idempotent_replay=False)
    failure = _bulk_retry_failure("dlq_missing", ValidationFailed("record is not retryable", details={"field": "id"}))

    result = _bulk_retry_result([retry], [failure])

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["succeededCount"] == 1
    assert result["failedCount"] == 1
    assert result["failedItems"] == [
        {
            "deadLetterRecordId": "dlq_missing",
            "code": "VALIDATION_FAILED",
            "message": "record is not retryable",
            "details": {"field": "id"},
        }
    ]


def test_record_dlq_bulk_retry_failure_scrubs_secret_payload() -> None:
    failure = _bulk_retry_failure(
        "dlq_secret",
        ValidationFailed("prompt: raw customer text", details={"apiKey": "plain-key", "safe": "visible"}),
    )

    assert failure == {
        "deadLetterRecordId": "dlq_secret",
        "code": "VALIDATION_FAILED",
        "message": "***MASKED***",
        "details": {"apiKey": "***MASKED***", "safe": "visible"},
    }
    assert "plain-key" not in str(failure)
    assert "raw customer text" not in str(failure)


def test_record_dlq_redacts_nested_sensitive_values() -> None:
    payload = {
        "customer": {"ssn": "123-45-6789", "name": "Ada"},
        "events": [{"secret_token": "raw-token"}, {"status": "ok"}],
    }

    assert _redact_sensitive(payload, {"ssn", "secret_token"}) == {
        "customer": {"ssn": "***MASKED***", "name": "Ada"},
        "events": [{"secret_token": "***MASKED***"}, {"status": "ok"}],
    }


def test_record_dlq_replay_failure_metadata_scrubs_secret_message() -> None:
    metadata = _failure_metadata(
        _row(metadata={"stream": "shipments"}),
        RequestContext(tenant_id="tenant-demo", actor_user_id="operator", request_id="req-secret"),
        RuntimeError("Authorization: Bearer raw-record-token"),
    )

    assert metadata["lastReplayError"] == {"type": "RuntimeError", "message": "***MASKED***"}
    assert "raw-record-token" not in str(metadata)


def test_record_dlq_replay_metadata_helpers_fail_closed_and_preserve_success_evidence() -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="operator", request_id="req-replay")
    row = _row(
        replay_run_id="replay-run-1",
        replay_lease_token="lease-1",
        metadata={"dataset_ref": "raw.orders", "schema_strategy": "envelope_json", "batchSize": 10},
    )
    result = CommitResult(
        dataset_id="dataset-orders",
        dataset_ref="raw.orders",
        transaction_id="tx-replay",
        version_id="version-replayed",
        version_number=2,
        row_count=3,
        manifest_uri="/tmp/manifest.json",
        schema_hash="schema-hash",
    )

    success = _success_metadata(row, ctx, result)

    assert _replay_lease_token(row) == "lease-1"
    assert _replay_run_id(row) == "replay-run-1"
    assert _str_metadata(row["metadata"], "dataset_ref") == "raw.orders"
    assert _optional_str_metadata(row["metadata"], "missing") is None
    assert _int_metadata(row["metadata"], "batchSize") == 10
    assert _schema_strategy(row["metadata"]) == "envelope_json"
    assert success["replayResult"] == {
        "datasetVersionId": "version-replayed",
        "transactionId": "tx-replay",
        "rowCount": 3,
    }
    with pytest.raises(InvariantViolation, match="lease is missing"):
        _replay_lease_token(_row(replay_lease_token=""))
    with pytest.raises(ValidationFailed, match="replay run is missing"):
        _replay_run_id(_row(replay_run_id=None))
    with pytest.raises(ValidationFailed, match="missing a string"):
        _str_metadata({}, "dataset_ref")
    with pytest.raises(ValidationFailed, match="missing an integer"):
        _int_metadata({"batchSize": True}, "batchSize")
    with pytest.raises(ValidationFailed, match="schema strategy is invalid"):
        _schema_strategy({"schema_strategy": "bad"})


def test_record_dlq_permission_denied_audit_records_deny_decision(foundry: FoundryLite) -> None:
    ctx = RequestContext(
        tenant_id="tenant-demo",
        actor_user_id="viewer-user",
        roles=("viewer",),
        request_id="req-record-dlq-deny",
    )

    with pytest.raises(PermissionDenied):
        foundry.operations.retry_dead_letter_record("missing-record", idempotency_key="retry-deny", ctx=ctx)

    with foundry.engine.begin() as conn:
        sql_conn = cast(Connection, conn)
        row = (
            sql_conn.execute(select(db.audit_events).where(db.audit_events.c.event_type == "permission.denied"))
            .mappings()
            .one()
        )

    assert row["action"] == "operations:retry"
    assert row["decision"] == "deny"
    assert row["after_ref"] == {"decision": "deny"}


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
