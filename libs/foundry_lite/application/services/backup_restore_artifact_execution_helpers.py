"""Application service helpers for backup restore artifact execution helpers workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import (
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoredVersion,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreArtifactRestoreStatus,
    BackupRestoreDatasetVersion,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    DatasetManifest,
    DatasetManifestFile,
    DatasetRow,
    DatasetStagedFile,
    DatasetTransactionRepository,
    DatasetVersionRecord,
    DatasetVersionRepository,
    DatasetVersionRow,
    RuntimeJsonObject,
    RuntimeRepository,
    TransactionContext,
)
from foundry_lite.application.primitives import _file_hash, _now
from foundry_lite.application.services.backup_restore_artifact_restore import _latest_versions_by_dataset
from foundry_lite.application.services.dataset.transaction_models import DatasetCommitArtifacts
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

ARTIFACT_RESTORE_EXECUTED_EVENT = "backup_restore.artifact_restore_executed"
ARTIFACT_RESTORE_EXECUTE_FAILED_EVENT = "backup_restore.artifact_restore_execute_failed"


@dataclass(frozen=True)
class RestoreCandidate:
    artifact_version: BackupRestoreDatasetVersion
    dataset: DatasetRow
    current_head_version_id: str


@dataclass(frozen=True)
class RestoreSourceFiles:
    manifest: DatasetManifest
    data_paths: tuple[Path, ...]
    files: tuple[DatasetManifestFile, ...]


@dataclass(frozen=True)
class CleanupSpec:
    dataset_id: str
    branch: str
    version_id: str


def restore_candidates(
    artifact_preflight: BackupRestorePreflightReport,
    current_preflight: BackupRestorePreflightReport,
    datasets: Mapping[str, DatasetRow],
) -> list[RestoreCandidate]:
    latest = _latest_versions_by_dataset(current_preflight["datasetVersions"])
    # The artifact inventory lists every committed version of each dataset, so
    # iterating it raw produced one restore candidate per historical version and
    # committed each as a new head in turn — the final head ended up being a copy
    # of some intermediate version rather than the backup's head, and even an
    # unchanged dataset was rewritten. Restore only the backup head per
    # (dataset, branch), which is what "restore to the backup" means.
    artifact_head_ids = {
        str(head["versionId"]) for head in _latest_versions_by_dataset(artifact_preflight["datasetVersions"]).values()
    }
    candidates: list[RestoreCandidate] = []
    for artifact_version in artifact_preflight["datasetVersions"]:
        if str(artifact_version["versionId"]) not in artifact_head_ids:
            continue
        current = latest.get((artifact_version["datasetId"], artifact_version["branch"]))
        if current is None or current["versionId"] == artifact_version["versionId"]:
            continue
        dataset = datasets[str(artifact_version["datasetId"])]
        candidates.append(RestoreCandidate(artifact_version, dataset, str(current["versionId"])))
    return candidates


def required_source_version(
    repository: DatasetVersionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    candidate: RestoreCandidate,
) -> DatasetVersionRow:
    source = repository.version_by_id(transaction=conn, version_id=candidate.artifact_version["versionId"])
    if source is None or source["tenant_id"] != ctx.tenant_id or source["dataset_id"] != candidate.dataset["id"]:
        raise ConflictDetected(
            "backup artifact source dataset version is not restorable",
            details=dict(candidate.artifact_version),
        )
    return source


def validate_source_files(
    source: DatasetVersionRow,
    files: Sequence[DatasetManifestFile],
    data_paths: Sequence[Path],
) -> None:
    if not files or len(files) != len(data_paths):
        raise ValueError("manifest file list and resolved data paths disagree")
    row_count = sum(file["row_count"] for file in files)
    byte_size = sum(file["byte_size"] for file in files)
    if row_count != source["row_count"] or byte_size != source["byte_size"]:
        raise ValueError("source version counts do not match committed manifest")
    for manifest_file, path in zip(files, data_paths, strict=True):
        if path.stat().st_size != manifest_file["byte_size"] or _file_hash(path) != manifest_file["content_hash"]:
            raise ValueError(f"source file integrity check failed: {manifest_file['uri']}")


def staged_files(source_files: RestoreSourceFiles) -> tuple[DatasetStagedFile, ...]:
    return tuple(
        DatasetStagedFile(
            path=path,
            row_count=manifest_file["row_count"],
            partition_values=manifest_file.get("partition_values") or {},
        )
        for manifest_file, path in zip(source_files.files, source_files.data_paths, strict=True)
    )


def dataset_version_record(
    ctx: RequestContext,
    dataset_id: str,
    source: DatasetVersionRow,
    tx_id: str,
    commit: DatasetCommitArtifacts,
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        version_id=commit.version_id,
        tenant_id=ctx.tenant_id,
        dataset_id=dataset_id,
        branch=source["branch"],
        version_number=commit.version_number,
        transaction_id=tx_id,
        schema_version=source["schema_version"],
        manifest_uri=commit.stored_commit.manifest_uri,
        row_count=source["row_count"],
        byte_size=commit.stored_commit.byte_size,
        status="active",
        superseded_by_version_id=None,
        created_at=_now(),
    )


def commit_restore_transaction(
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    tx_id: str,
    source: DatasetVersionRow,
    current: DatasetVersionRow,
    artifact: BackupRestoreArtifact,
    restored_version_id: str,
) -> None:
    committed = repository.commit_transaction(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        transaction_id=tx_id,
        committed_version_id=restored_version_id,
        schema_version=source["schema_version"],
        committed_at=_now(),
        metadata=restore_transaction_metadata(source, current, artifact),
    )
    if not committed:
        raise ConflictDetected("restore transaction lost its OPEN state", details={"transaction_id": tx_id})


def restore_transaction_metadata(
    source: DatasetVersionRow,
    current: DatasetVersionRow,
    artifact: BackupRestoreArtifact,
) -> RuntimeJsonObject:
    receipt = artifact["receipt"]
    return {
        "backupRestore": {
            "artifactRef": receipt["artifactRef"],
            "artifactHash": receipt["artifactHash"],
            "backupId": receipt["backupId"],
            "sourceVersionId": source["id"],
            "currentHeadBeforeRestoreVersionId": current["id"],
        }
    }


def restored_result(
    candidate: RestoreCandidate,
    source: DatasetVersionRow,
    current: DatasetVersionRow,
    tx_id: str,
    commit: DatasetCommitArtifacts,
) -> BackupRestoreArtifactRestoredVersion:
    return {
        "datasetId": candidate.dataset["id"],
        "datasetRef": f"{candidate.dataset['namespace']}.{candidate.dataset['name']}",
        "branch": source["branch"],
        "status": "restored",
        "sourceVersionId": source["id"],
        "currentHeadBeforeRestoreVersionId": current["id"],
        "restoredVersionId": commit.version_id,
        "restoreTransactionId": tx_id,
        "restoredVersionNumber": commit.version_number,
        "rowCount": source["row_count"],
        "manifestUri": commit.stored_commit.manifest_uri,
    }


def already_current_result(
    candidate: RestoreCandidate,
    source: DatasetVersionRow,
) -> BackupRestoreArtifactRestoredVersion:
    return {
        "datasetId": candidate.dataset["id"],
        "datasetRef": f"{candidate.dataset['namespace']}.{candidate.dataset['name']}",
        "branch": source["branch"],
        "status": "already_current",
        "sourceVersionId": source["id"],
        "currentHeadBeforeRestoreVersionId": source["id"],
        "restoredVersionId": source["id"],
        "restoreTransactionId": "",
        "restoredVersionNumber": source["version_number"],
        "rowCount": source["row_count"],
        "manifestUri": source["manifest_uri"],
    }


def restored_event_payload(
    artifact: BackupRestoreArtifact,
    candidate: RestoreCandidate,
    source: DatasetVersionRow,
    current: DatasetVersionRow,
    tx_id: str,
    commit: DatasetCommitArtifacts,
) -> RuntimeJsonObject:
    result = dict(restored_result(candidate, source, current, tx_id, commit))
    result["artifactRef"] = artifact["receipt"]["artifactRef"]
    result["artifactHash"] = artifact["receipt"]["artifactHash"]
    result["backupId"] = artifact["receipt"]["backupId"]
    return result


def execution_report(
    ctx: RequestContext,
    artifact: BackupRestoreArtifact,
    restore_id: str,
    findings: list[RuntimeJsonObject],
    mode: BackupRestoreModeReport | None,
    validation: BackupRestorePostRestoreValidationReport | None,
    restored: list[BackupRestoreArtifactRestoredVersion],
) -> BackupRestoreArtifactRestoreReport:
    receipt = artifact["receipt"]
    preflight = artifact["preflightReport"]
    status = execution_status(findings, mode, validation)
    return {
        "generatedAt": _now(),
        "tenantId": ctx.tenant_id,
        "backupId": preflight["backupId"],
        "restoreId": restore_id,
        "status": status,
        "artifactRef": receipt["artifactRef"],
        "artifactHash": receipt["artifactHash"],
        "manifestFormat": artifact["manifestFormat"],
        "executionMode": "historical_artifact_dataset_head_restore",
        "isArtifactVerified": True,
        "isHistoricalRepointExecuted": has_restored_versions(restored),
        "preflightStatus": preflight["status"],
        "restoreModeStatus": mode["status"] if mode is not None else "not_started",
        "postRestoreValidationStatus": validation["status"] if validation is not None else "not_run",
        "datasetVersionCount": len(preflight["datasetVersions"]),
        "blockingIssueCount": blocking_issue_count(findings, validation),
        "findings": findings,
        "restoreMode": mode,
        "postRestoreValidation": validation,
        "restoredDatasetVersions": restored,
        "restoreTransactionCount": len([item for item in restored if item["status"] == "restored"]),
        "summary": execution_summary(status, findings, mode, validation, restored),
    }


def execution_status(
    findings: Sequence[RuntimeJsonObject],
    mode: BackupRestoreModeReport | None,
    validation: BackupRestorePostRestoreValidationReport | None,
) -> BackupRestoreArtifactRestoreStatus:
    if findings or mode is None or mode["status"] == "blocked":
        return "blocked"
    if validation is not None and validation["status"] == "blocked":
        return "blocked"
    return "validated"


def execution_summary(
    status: BackupRestoreArtifactRestoreStatus,
    findings: list[RuntimeJsonObject],
    mode: BackupRestoreModeReport | None,
    validation: BackupRestorePostRestoreValidationReport | None,
    restored: list[BackupRestoreArtifactRestoredVersion],
) -> RuntimeJsonObject:
    has_restored = has_restored_versions(restored)
    return {
        "artifactMatchesCurrentServingHead": status == "validated" and not has_restored,
        "restoreModeStarted": mode is not None,
        "postRestoreValidationRan": validation is not None,
        "pointInTimeRepointExecuted": has_restored,
        "restoredDatasetVersionCount": len([item for item in restored if item["status"] == "restored"]),
        "nextStep": execution_next_step(status, findings, validation),
    }


def execution_next_step(
    status: BackupRestoreArtifactRestoreStatus,
    findings: Sequence[RuntimeJsonObject],
    validation: BackupRestorePostRestoreValidationReport | None,
) -> str:
    if findings:
        return "resolve_restore_findings_before_retry"
    if status == "validated" and validation is not None and validation["status"] == "passed":
        return "approve_restore_resume"
    if status == "validated":
        return "run_post_restore_validation"
    return "resolve_post_restore_validation_findings_before_resume"


def blocking_restore_findings(findings: Sequence[RuntimeJsonObject]) -> list[RuntimeJsonObject]:
    return [finding for finding in findings if finding.get("check") != "serving_head_matches_artifact"]


def blocking_issue_count(
    findings: Sequence[RuntimeJsonObject],
    validation: BackupRestorePostRestoreValidationReport | None,
) -> int:
    if findings:
        return len(findings)
    if validation is not None and validation["status"] == "blocked":
        return len(validation["findings"])
    return 0


def has_restored_versions(restored: Sequence[BackupRestoreArtifactRestoredVersion]) -> bool:
    return any(item["status"] == "restored" for item in restored)


def execution_event_type(report: BackupRestoreArtifactRestoreReport) -> str:
    if report["status"] == "validated":
        return ARTIFACT_RESTORE_EXECUTED_EVENT
    return ARTIFACT_RESTORE_EXECUTE_FAILED_EVENT


def existing_execution_report(
    repository: RuntimeRepository,
    ctx: RequestContext,
    restore_id: str,
) -> BackupRestoreArtifactRestoreReport | None:
    # Idempotency must key on whether the restore already mutated dataset heads,
    # not only on a validated outcome. `_restore_dataset_heads` commits heads
    # before post-restore validation runs, and a blocked validation records an
    # EXECUTE_FAILED event; matching only EXECUTED let a retry re-run the restore
    # and commit duplicate head versions. Short-circuit on any prior restore event
    # for this id whose report shows the historical repoint was executed.
    rows = [
        row
        for row in repository.list_runs(tenant_id=ctx.tenant_id)["auditEvents"]
        if row.get("resource_id") == restore_id and _restore_event_is_idempotency_marker(row)
    ]
    if not rows:
        return None
    latest = max(rows, key=lambda row: str(row.get("created_at") or ""))
    return cast(BackupRestoreArtifactRestoreReport, latest["after_ref"])


def _restore_event_is_idempotency_marker(row: Mapping[str, object]) -> bool:
    event_type = row.get("event_type")
    if event_type == ARTIFACT_RESTORE_EXECUTED_EVENT:
        return True
    if event_type != ARTIFACT_RESTORE_EXECUTE_FAILED_EVENT:
        return False
    after_ref = row.get("after_ref")
    return isinstance(after_ref, Mapping) and bool(after_ref.get("isHistoricalRepointExecuted"))


def require_same_artifact(
    existing: BackupRestoreArtifactRestoreReport,
    receipt: BackupRestoreArtifactReceipt,
) -> BackupRestoreArtifactRestoreReport:
    if existing["artifactHash"] == receipt["artifactHash"]:
        return existing
    raise ConflictDetected(
        "restore id already executed for a different backup artifact",
        details={
            "restore_id": existing["restoreId"],
            "existing_artifact_hash": existing["artifactHash"],
            "requested_artifact_hash": receipt["artifactHash"],
        },
    )
