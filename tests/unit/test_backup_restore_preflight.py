from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import BackupArtifactIntegrityError
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, PermissionDenied
from foundry_lite.infrastructure import schema as db
from sqlalchemy import Table, func, insert, select

from tests.conftest import prepare_indexed_demo


def test_restore_preflight_validates_every_committed_manifest(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    csv_path = _csv(tmp_path, "orders.csv")
    foundry.datasets.ensure("raw.restore_orders", ctx=ctx, primary_key=["order_id"])
    committed = foundry.datasets.upload_csv("raw.restore_orders", csv_path, ctx=ctx)

    report = foundry.operations.restore_preflight_report(ctx=ctx, backup_id="backup-valid")

    version = _version(report, committed.version_id)
    assert report["status"] == "ready"
    assert version["status"] == "valid"
    assert version["datasetRef"] == "raw.restore_orders"
    assert version["manifestFileCount"] == 1
    assert version["manifestRowCount"] == committed.row_count
    assert version["manifestByteSize"] == version["byteSize"]
    assert report["restoreTrafficGate"]["writeTrafficMustBePausedBeforeRestore"] is True
    assert report["searchRebuild"]["projectionIsSourceOfTruth"] is False
    assert report["searchRebuild"]["rebuildRequiredAfterRestore"] is True

    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "backup_restore.preflight_reported" for event in audit_events)


def test_backup_restore_create_artifact_writes_receipt_and_audit(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_artifact", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_artifact", _csv(tmp_path, "artifact.csv"), ctx=ctx)

    receipt = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-ready")
    replay = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-ready")

    artifact_path = Path(receipt["artifactRef"])
    assert receipt["status"] == "created"
    assert receipt["preflightStatus"] == "ready"
    assert receipt["datasetVersionCount"] == 1
    assert artifact_path.exists()
    assert replay["status"] == "existing"
    assert replay["isIdempotentReplay"] is True
    assert replay["artifactHash"] == receipt["artifactHash"]
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    created = [
        event
        for event in audit_events
        if event["event_type"] == "backup_restore.artifact_created" and event["resource_id"] == "backup-artifact-ready"
    ]
    assert len(created) == 1


def test_backup_restore_create_artifact_fails_closed_when_preflight_is_blocked(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_artifact_blocked", ctx=ctx, primary_key=["order_id"])
    committed = foundry.datasets.upload_csv(
        "raw.restore_artifact_blocked",
        _csv(tmp_path, "artifact-blocked.csv"),
        ctx=ctx,
    )
    Path(committed.manifest_uri).unlink()

    with pytest.raises(ConflictDetected, match="backup artifact requires a ready restore preflight"):
        foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-blocked")

    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(
        event["event_type"] == "backup_restore.artifact_failed" and event["resource_id"] == "backup-artifact-blocked"
        for event in audit_events
    )


def test_backup_restore_create_artifact_reports_storage_integrity_failure(
    foundry: FoundryLite,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = demo_admin_context()
    backup_id = "backup-artifact-too-large"
    foundry.datasets.ensure("raw.restore_artifact_large", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_artifact_large", _csv(tmp_path, "artifact-large.csv"), ctx=ctx)
    service = foundry.operations._backup_restore.backup_restore_artifact_service

    def fail_write(_store: object, _report: object) -> None:
        raise BackupArtifactIntegrityError("s3-backup-artifact://redacted/large.json", "size limit")

    monkeypatch.setattr(type(service.backup_artifact_store), "write_backup_artifact", fail_write)
    with pytest.raises(InvariantViolation, match="integrity contract failed") as raised:
        foundry.operations.create_backup_artifact(ctx=ctx, backup_id=backup_id)

    assert raised.value.details == {
        "backup_id": backup_id,
        "artifact_ref": "s3-backup-artifact://redacted/large.json",
        "reason": "size limit",
    }
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    failure = next(event for event in audit_events if event["resource_id"] == backup_id)
    assert failure["event_type"] == "backup_restore.artifact_failed"
    assert failure["after_ref"]["reason"] == "artifact_integrity_failed"


def test_backup_restore_artifact_restore_validates_and_runs_closed_loop(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    _apply_action_and_materialize(foundry, ctx)
    receipt = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-restore")

    report = foundry.operations.restore_from_backup_artifact(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-artifact",
        validation_id="validation-artifact",
    )

    assert report["status"] == "validated"
    assert report["restoreModeStatus"] == "paused"
    assert report["postRestoreValidationStatus"] == "passed"
    assert report["summary"]["artifactMatchesCurrentServingHead"] is True
    assert report["summary"]["pointInTimeRepointExecuted"] is False
    assert report["summary"]["nextStep"] == "approve_restore_resume"
    assert report["postRestoreValidation"] is not None
    assert report["postRestoreValidation"]["validationId"] == "validation-artifact"
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "backup_restore.artifact_restore_requested" for event in audit_events)
    assert any(event["event_type"] == "backup_restore.artifact_restore_validated" for event in audit_events)


def test_backup_restore_artifact_restore_blocks_when_serving_head_moved(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_artifact_drift", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_artifact_drift", _csv(tmp_path, "artifact-drift-a.csv"), ctx=ctx)
    receipt = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-drift")
    foundry.datasets.upload_csv("raw.restore_artifact_drift", _csv(tmp_path, "artifact-drift-b.csv"), ctx=ctx)

    report = foundry.operations.restore_from_backup_artifact(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-artifact-drift",
    )

    assert report["status"] == "blocked"
    assert report["restoreModeStatus"] == "not_started"
    assert report["summary"]["artifactMatchesCurrentServingHead"] is False
    assert {finding["check"] for finding in report["findings"]} == {"serving_head_matches_artifact"}
    status = foundry.operations.restore_mode_status("restore-artifact-drift", ctx=ctx)
    assert status["status"] == "inactive"


def test_backup_restore_artifact_execute_restores_historical_dataset_head(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_artifact_execute", ctx=ctx, primary_key=["order_id"])
    original = foundry.datasets.upload_csv(
        "raw.restore_artifact_execute",
        _csv_with_status(tmp_path, "artifact-execute-a.csv", "PENDING"),
        ctx=ctx,
    )
    receipt = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-execute")
    moved = foundry.datasets.upload_csv(
        "raw.restore_artifact_execute",
        _csv_with_status(tmp_path, "artifact-execute-b.csv", "SHIPPED"),
        ctx=ctx,
    )

    report = foundry.operations.execute_backup_artifact_restore(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-artifact-execute",
        should_run_post_restore_validation=False,
    )
    replay = foundry.operations.execute_backup_artifact_restore(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-artifact-execute",
        should_run_post_restore_validation=False,
    )

    restored = report["restoredDatasetVersions"][0]
    preview = foundry.datasets.preview("raw.restore_artifact_execute", ctx=ctx)
    inspection = foundry.datasets.inspect("raw.restore_artifact_execute", ctx=ctx)
    assert report["status"] == "validated"
    assert report["isHistoricalRepointExecuted"] is True
    assert report["restoreTransactionCount"] == 1
    assert report["summary"]["artifactMatchesCurrentServingHead"] is False
    assert report["summary"]["pointInTimeRepointExecuted"] is True
    assert report["summary"]["restoredDatasetVersionCount"] == 1
    assert restored["sourceVersionId"] == original.version_id
    assert restored["currentHeadBeforeRestoreVersionId"] == moved.version_id
    assert restored["restoredVersionId"] not in {original.version_id, moved.version_id}
    assert replay["restoredDatasetVersions"][0]["restoredVersionId"] == restored["restoredVersionId"]
    assert preview[0]["order_id"] == "O-1"
    assert preview[0]["status"] == "PENDING"
    assert inspection["version"]["id"] == restored["restoredVersionId"]
    with foundry.engine.begin() as conn:
        transaction = cast(Any, conn)
        tx_row = (
            transaction.execute(
                select(db.dataset_transactions).where(db.dataset_transactions.c.id == restored["restoreTransactionId"])
            )
            .mappings()
            .one()
        )
    assert tx_row["committed_version_id"] == restored["restoredVersionId"]
    assert tx_row["metadata"]["backupRestore"]["sourceVersionId"] == original.version_id
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "backup_restore.artifact_restore_executed" for event in audit_events)
    assert any(event["event_type"] == "backup_restore.dataset_version_restored" for event in audit_events)


def test_artifact_restore_retry_after_blocked_validation_does_not_duplicate_heads(
    foundry: FoundryLite,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restore that committed heads but then blocked validation must be idempotent.

    Heads are committed before post-restore validation runs, and a blocked
    validation records an EXECUTE_FAILED event. When idempotency matched only the
    EXECUTED event, a retry with the same restore id re-ran the restore and
    committed a second set of head versions. The retry must instead short-circuit
    on the prior head-commit.
    """
    import foundry_lite.application.services.backup_restore_artifact_restore as restore_mod

    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_blocked_retry", ctx=ctx, primary_key=["order_id"])
    original = foundry.datasets.upload_csv(
        "raw.restore_blocked_retry",
        _csv_with_status(tmp_path, "blocked-retry-a.csv", "PENDING"),
        ctx=ctx,
    )
    receipt = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-blocked-retry")
    foundry.datasets.upload_csv(
        "raw.restore_blocked_retry",
        _csv_with_status(tmp_path, "blocked-retry-b.csv", "SHIPPED"),
        ctx=ctx,
    )

    real_report = restore_mod.post_restore_validation_report

    def blocked_report(*args: Any, **kwargs: Any) -> dict[str, object]:
        report = dict(real_report(*args, **kwargs))
        report["status"] = "blocked"
        return report

    monkeypatch.setattr(restore_mod, "post_restore_validation_report", blocked_report)

    def dataset_version_count() -> int:
        with foundry.engine.begin() as conn:
            return int(cast(Any, conn).execute(select(func.count()).select_from(db.dataset_versions)).scalar_one())

    first = foundry.operations.execute_backup_artifact_restore(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-blocked-retry",
        should_run_post_restore_validation=True,
    )
    assert first["status"] == "blocked"
    assert first["isHistoricalRepointExecuted"] is True
    assert first["restoredDatasetVersions"]  # heads were committed before validation blocked
    versions_after_first = dataset_version_count()

    replay = foundry.operations.execute_backup_artifact_restore(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-blocked-retry",
        should_run_post_restore_validation=True,
    )

    # The retry short-circuits on the prior head-commit: no new dataset version is
    # committed, and the dataset still serves the restored (PENDING) head.
    assert dataset_version_count() == versions_after_first
    assert replay["restoreId"] == first["restoreId"]
    preview = foundry.datasets.preview("raw.restore_blocked_retry", ctx=ctx)
    assert preview[0]["status"] == "PENDING"
    assert original.version_id  # original captured for context


def test_backup_restore_artifact_execute_fails_closed_when_source_storage_is_missing(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_artifact_execute_missing", ctx=ctx, primary_key=["order_id"])
    original = foundry.datasets.upload_csv(
        "raw.restore_artifact_execute_missing",
        _csv_with_status(tmp_path, "artifact-execute-missing-a.csv", "PENDING"),
        ctx=ctx,
    )
    receipt = foundry.operations.create_backup_artifact(ctx=ctx, backup_id="backup-artifact-execute-missing")
    foundry.datasets.upload_csv(
        "raw.restore_artifact_execute_missing",
        _csv_with_status(tmp_path, "artifact-execute-missing-b.csv", "SHIPPED"),
        ctx=ctx,
    )
    Path(original.manifest_uri).unlink()

    report = foundry.operations.execute_backup_artifact_restore(
        ctx=ctx,
        artifact_ref=receipt["artifactRef"],
        artifact_hash=receipt["artifactHash"],
        restore_id="restore-artifact-execute-missing",
        should_run_post_restore_validation=False,
    )

    assert report["status"] == "blocked"
    assert report["isHistoricalRepointExecuted"] is False
    assert report["restoreTransactionCount"] == 0
    assert {finding["check"] for finding in report["findings"]} == {
        "artifact_dataset_version_integrity",
        "current_preflight",
    }


def test_restore_preflight_rejects_db_storage_point_mismatch(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    csv_path = _csv(tmp_path, "orders.csv")
    foundry.datasets.ensure("raw.restore_missing", ctx=ctx, primary_key=["order_id"])
    committed = foundry.datasets.upload_csv("raw.restore_missing", csv_path, ctx=ctx)
    Path(committed.manifest_uri).unlink()

    report = foundry.operations.restore_preflight_report(ctx=ctx, backup_id="backup-missing")

    version = _version(report, committed.version_id)
    assert report["status"] == "blocked"
    assert report["summary"]["dbStoragePointMismatchCount"] == 1
    assert version["status"] == "missing"
    issue = version["issue"]
    assert isinstance(issue, dict)
    details = issue["details"]
    assert isinstance(details, dict)
    assert issue["code"] == "committed_manifest_missing"
    assert details["servingTruth"] == "metadata_db_committed_dataset_version"


def test_restore_preflight_captures_index_pointers_and_runtime_high_watermarks(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)

    report = foundry.operations.restore_preflight_report(ctx=ctx, backup_id="backup-index")

    pointer_names = {pointer["objectTypeApiName"] for pointer in report["activeIndexPointers"]}
    high_watermarks = report["highWatermarks"]
    outbox_watermark = cast(dict[str, object], high_watermarks["outboxEvents"])
    audit_watermark = cast(dict[str, object], high_watermarks["auditEvents"])
    assert {"Order", "Customer"}.issubset(pointer_names)
    assert isinstance(outbox_watermark["count"], int) and outbox_watermark["count"] > 0
    assert isinstance(audit_watermark["count"], int) and audit_watermark["count"] > 0
    assert report["temporalStrategy"]["workflowProfile"] == "local-workflow"
    assert report["searchRebuild"]["marker"] == "search-rebuild:tenant-demo:backup-index"


def test_restore_pauses_outbox_until_reconciliation(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_pause", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_pause", _csv(tmp_path, "pause.csv"), ctx=ctx)
    _seed_dead_letter_event(foundry, outbox_id="outbox_restore_pause", dlq_id="dlq_restore_pause")

    mode = foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-pause", restore_id="restore-pause")

    assert mode["status"] == "paused"
    assert mode["is_outbox_publisher_paused"] is True
    assert mode["is_serving_traffic_open"] is False
    with pytest.raises(ConflictDetected, match="restore mode keeps outbox publisher paused"):
        foundry.operations.retry_dead_letter_event("dlq_restore_pause", ctx=ctx)
    runs = foundry.operations.list_runs(ctx=ctx)
    assert _runtime_row(runs["outboxEvents"], "outbox_restore_pause")["status"] == "failed"
    assert _runtime_row(runs["deadLetterEvents"], "dlq_restore_pause")["id"] == "dlq_restore_pause"


def test_restore_mode_blocks_platform_write_traffic(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    mode = foundry.operations.start_restore_mode(
        ctx=ctx,
        backup_id="backup-write-gate",
        restore_id="restore-write-gate",
    )

    assert mode["status"] == "paused"
    assert foundry.datasets.preview("clean.orders", ctx=ctx)
    _assert_restore_blocks("create", lambda: foundry.datasets.ensure("raw.restore_new", ctx=ctx))
    _assert_restore_blocks(
        "upload_csv",
        lambda: foundry.datasets.upload_csv(
            "raw.erp_orders",
            _csv(tmp_path, "g.csv"),
            ctx=ctx,
        ),
    )
    _assert_restore_blocks(
        "apply",
        lambda: foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "restore gate"},
            idempotency_key="restore-write-gate-action",
            ctx=ctx,
        ),
    )
    _assert_restore_blocks("index_rebuild", lambda: foundry.objects.reindex("Order", ctx=ctx))
    _assert_restore_blocks("materialize", lambda: foundry.materialization.run("action_log", ctx=ctx))
    _assert_restore_blocks(
        "register_transform",
        lambda: foundry.transforms.register(
            "restore_gate_transform",
            entrypoint=tmp_path / "missing.sql",
            inputs={},
            output_dataset_ref="clean.orders",
            ctx=ctx,
        ),
    )
    _assert_restore_blocks(
        "start_connector_sync_workflow",
        lambda: foundry.operations.start_connector_sync_workflow(
            "clean.orders",
            connector_name="rest",
            resource_name="orders",
            idempotency_key="restore-write-gate-workflow",
            ctx=ctx,
        ),
    )


def test_backup_restore_recovery_overview_summarizes_active_restore_and_preflight(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_overview", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_overview", _csv(tmp_path, "overview.csv"), ctx=ctx)

    mode = foundry.operations.start_restore_mode(
        ctx=ctx,
        backup_id="backup-overview",
        restore_id="restore-overview",
    )
    overview = foundry.operations.recovery_overview(ctx=ctx)

    assert overview["activeRestoreMode"] == mode
    assert overview["latestRestoreMode"] == mode
    assert overview["latestPreflight"] is not None
    assert overview["latestPostRestoreValidation"] is None
    assert overview["latestPreflight"]["backupId"] == "backup-overview"
    assert overview["latestPreflight"]["status"] == "ready"
    assert overview["restoreTrafficGate"]["activeRestoreId"] == "restore-overview"
    assert overview["restoreTrafficGate"]["isWriteTrafficPaused"] is True
    assert overview["restoreTrafficGate"]["isOutboxPublisherPaused"] is True
    assert overview["restoreTrafficGate"]["isServingTrafficOpen"] is False
    assert overview["summary"]["activeRestoreMode"] is True
    assert overview["summary"]["preflightStatus"] == "ready"
    assert overview["requiredOperatorActions"] == [
        "run_post_restore_closed_loop_validation",
        "approve_restore_resume",
    ]


def test_operations_retry_dead_letter_event_checks_permission_before_materialization(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    _seed_dead_letter_event(foundry, outbox_id="outbox_retry_denied", dlq_id="dlq_retry_denied")
    unauthorized = RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id="data-engineer-user",
        roles=("data_engineer",),
        request_id="req-retry-denied",
    )
    before_versions = _table_count(foundry, db.dataset_versions)
    before_runs = _table_count(foundry, db.materialization_runs)

    with pytest.raises(PermissionDenied):
        foundry.operations.retry_dead_letter_event("dlq_retry_denied", ctx=unauthorized)

    assert _table_count(foundry, db.dataset_versions) == before_versions
    assert _table_count(foundry, db.materialization_runs) == before_runs
    runs = foundry.operations.list_runs(ctx=ctx)
    assert _runtime_row(runs["deadLetterEvents"], "dlq_retry_denied")["id"] == "dlq_retry_denied"


def test_restore_resume_requires_closed_loop_validation(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_validation", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_validation", _csv(tmp_path, "validation.csv"), ctx=ctx)

    foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-validation", restore_id="restore-validation")

    validation = foundry.operations.run_post_restore_validation(
        "restore-validation",
        ctx=ctx,
        validation_id="closed-loop-missing",
    )

    assert validation["status"] == "blocked"
    assert validation["validationId"] == "closed-loop-missing"
    assert {finding["check"] for finding in validation["findings"]} == {
        "object_index_pointer",
        "actionRuns",
        "materializationRuns",
    }
    with pytest.raises(ConflictDetected, match="post-restore validation must pass"):
        foundry.operations.approve_restore_resume("restore-validation", ctx=ctx, validation_id="closed-loop-missing")


def test_post_restore_closed_loop_smoke(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    _apply_action_and_materialize(foundry, ctx)
    _seed_dead_letter_event(foundry, outbox_id="outbox_restore_resume", dlq_id="dlq_restore_resume")
    foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-resume", restore_id="restore-resume")
    validation = foundry.operations.run_post_restore_validation(
        "restore-resume",
        ctx=ctx,
        validation_id="closed-loop-resume",
    )
    overview = foundry.operations.recovery_overview(ctx=ctx)

    approved = foundry.operations.approve_restore_resume(
        "restore-resume",
        ctx=ctx,
        validation_id="closed-loop-resume",
    )
    retry = foundry.operations.retry_dead_letter_event("dlq_restore_resume", ctx=ctx)

    assert validation["status"] == "passed"
    assert validation["summary"]["outboxResumeEligible"] is True
    assert overview["latestPostRestoreValidation"] is not None
    assert overview["latestPostRestoreValidation"]["validationId"] == "closed-loop-resume"
    assert overview["requiredOperatorActions"] == ["approve_restore_resume"]
    assert approved["status"] == "resume_approved"
    assert approved["is_outbox_publisher_paused"] is False
    assert approved["is_serving_traffic_open"] is True
    assert approved["summary"]["validationId"] == "closed-loop-resume"
    assert retry["status"] == "pending"
    assert foundry.operations.restore_mode_status("restore-resume", ctx=ctx) == approved


def test_outbox_retry_stays_blocked_when_any_restore_mode_remains_active(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    _apply_action_and_materialize(foundry, ctx)
    _seed_dead_letter_event(foundry, outbox_id="outbox_restore_overlap", dlq_id="dlq_restore_overlap")
    restore_a = foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-a", restore_id="restore-a")
    foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-b", restore_id="restore-b")
    foundry.operations.run_post_restore_validation(
        "restore-b",
        ctx=ctx,
        validation_id="closed-loop-restore-b",
    )
    approved_b = foundry.operations.approve_restore_resume(
        "restore-b",
        ctx=ctx,
        validation_id="closed-loop-restore-b",
    )

    with pytest.raises(ConflictDetected) as exc:
        foundry.operations.retry_dead_letter_event("dlq_restore_overlap", ctx=ctx)

    assert restore_a["status"] == "paused"
    assert approved_b["status"] == "resume_approved"
    assert exc.value.details["restore_id"] == "restore-a"


def test_restore_retry_is_idempotent(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_retry", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.restore_retry", _csv(tmp_path, "retry.csv"), ctx=ctx)

    first = foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-retry", restore_id="restore-retry")
    second = foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-retry", restore_id="restore-retry")

    assert second == first
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    restore_events = [
        event
        for event in audit_events
        if event["event_type"] == "backup_restore.restore_mode_started" and event["resource_id"] == "restore-retry"
    ]
    assert len(restore_events) == 1


def test_restore_failure_never_opens_serving_traffic(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.restore_blocked", ctx=ctx, primary_key=["order_id"])
    committed = foundry.datasets.upload_csv("raw.restore_blocked", _csv(tmp_path, "blocked.csv"), ctx=ctx)
    Path(committed.manifest_uri).unlink()

    mode = foundry.operations.start_restore_mode(ctx=ctx, backup_id="backup-blocked", restore_id="restore-blocked")
    status = foundry.operations.restore_mode_status("restore-blocked", ctx=ctx)

    assert mode["status"] == "blocked"
    assert status == mode
    assert mode["blockingIssueCount"] == 1
    assert mode["is_serving_traffic_open"] is False
    assert mode["is_outbox_publisher_paused"] is True
    assert mode["summary"]["restoreFailureKeepsServingTrafficClosed"] is True


def _csv(tmp_path: Path, name: str) -> str:
    return _csv_with_status(tmp_path, name, "PENDING")


def _csv_with_status(tmp_path: Path, name: str, status: str) -> str:
    path = tmp_path / name
    path.write_text(f"order_id,status\nO-1,{status}\n", encoding="utf-8")
    return str(path)


def _apply_action_and_materialize(foundry: FoundryLite, ctx: RequestContext) -> None:
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Restore validation"},
        idempotency_key="restore-closed-loop-validation",
        ctx=ctx,
    )
    foundry.materialization.run("action_log", ctx=ctx)


def _assert_restore_blocks(operation: str, callback: Callable[[], object]) -> None:
    with pytest.raises(ConflictDetected, match="restore mode blocks write traffic") as exc:
        callback()
    assert exc.value.details["operation"] == operation
    assert exc.value.details["restore_id"] == "restore-write-gate"


def _version(report: object, version_id: str) -> dict[str, object]:
    assert isinstance(report, dict)
    for version in report["datasetVersions"]:
        if version["versionId"] == version_id:
            return version
    raise AssertionError(f"missing version inventory for {version_id}")


def _seed_dead_letter_event(foundry: FoundryLite, *, outbox_id: str, dlq_id: str) -> None:
    with foundry.engine.begin() as conn:
        transaction = cast(Any, conn)
        transaction.execute(
            insert(db.outbox_events).values(
                id=outbox_id,
                tenant_id="tenant-demo",
                event_type="materialization.requested",
                aggregate_type="materialization",
                aggregate_id="action_log",
                payload={"materialization": "action_log"},
                status="failed",
                attempts=3,
                idempotency_key=outbox_id,
                correlation_id=outbox_id,
                created_at="2026-06-10T00:00:00Z",
                published_at="2026-06-10T00:00:01Z",
            )
        )
        transaction.execute(
            insert(db.dead_letter_events).values(
                id=dlq_id,
                tenant_id="tenant-demo",
                source_event_id=outbox_id,
                event_type="materialization.requested",
                payload={"materialization": "action_log"},
                error={"message": "publisher failed"},
                failed_at="2026-06-10T00:00:02Z",
                retry_after=None,
            )
        )


def _runtime_row(rows: object, row_id: str) -> dict[str, object]:
    assert isinstance(rows, list)
    for row in rows:
        if isinstance(row, dict) and row.get("id") == row_id:
            return row
    raise AssertionError(f"missing runtime row {row_id}")


def _table_count(foundry: FoundryLite, table: Table) -> int:
    with foundry.engine.begin() as conn:
        transaction = cast(Any, conn)
        return cast(int, transaction.execute(select(func.count()).select_from(table)).scalar_one())
