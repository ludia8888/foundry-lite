from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import (
    AdapterError,
    BackupRestoreDatasetVersion,
    BackupRestoreIndexPointer,
    BackupRestoreManifestIssue,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    BackupRestoreRecoveryOverview,
    DatasetManifest,
    DatasetManifestFile,
    DatasetRow,
    DatasetVersionRow,
    RuntimeJsonObject,
    RuntimeRow,
    RuntimeRunSnapshot,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.backup_restore_mode import (
    active_restore_mode_report,
    inactive_restore_mode_report,
    latest_restore_mode_report,
)
from foundry_lite.application.services.backup_restore_overview import recovery_overview_from_audit
from foundry_lite.application.services.backup_restore_validation import (
    BackupRestoreValidationMixin,
    post_restore_validation_findings,
    post_restore_validation_report,
    restore_mode_report,
    resume_approved_report,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class BackupRestoreService(BackupRestoreValidationMixin, CoreService):
    """Build backup/restore commit-point reports and restore-mode gates."""

    required_dependencies = (
        "engine",
        "dataset_repository",
        "dataset_version_repository",
        "dataset_storage",
        "object_index_repository",
        "runtime_repository",
        "search_adapter",
        "workflow_adapter",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: DatasetRuntimeBoundary

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport:
        """Compare metadata DB commit points with storage before restore."""
        ctx = ctx or RequestContext()
        report_id = backup_id or f"preflight-{ctx.request_id}"
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "backup_restore", report_id)
        dataset_versions = self._dataset_version_inventory(ctx)
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        issues = [item["issue"] for item in dataset_versions if item["issue"] is not None]
        report = self._report(ctx, report_id, dataset_versions, issues, snapshot)
        with self.engine.begin() as conn:
            self._audit_report(conn, ctx, report)
        return report

    def start_restore_mode(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
        restore_id: str | None = None,
    ) -> BackupRestoreModeReport:
        """Start a durable restore-mode lockout after running preflight."""
        ctx = ctx or RequestContext()
        resolved_restore_id = restore_id or backup_id or f"restore-{ctx.request_id}"
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", resolved_restore_id)
        existing = self._existing_restore_mode_report(ctx, resolved_restore_id)
        if existing is not None:
            return existing
        preflight = self.restore_preflight_report(ctx=ctx, backup_id=backup_id or resolved_restore_id)
        report = restore_mode_report(ctx, resolved_restore_id, preflight)
        with self.engine.begin() as conn:
            self._audit_restore_mode(conn, ctx, report)
        return report

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport:
        """Return durable restore-mode status for one restore id."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "backup_restore", restore_id)
        existing = self._existing_restore_mode_report(ctx, restore_id)
        return existing or inactive_restore_mode_report(ctx, restore_id)

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport:
        """Approve publisher resume after post-restore closed-loop validation."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", restore_id)
        current = self._approval_candidate(ctx, restore_id)
        if current["status"] == "resume_approved":
            return current
        validation = self._required_passed_post_restore_validation(ctx, restore_id, validation_id)
        preflight = self.restore_preflight_report(ctx=ctx, backup_id=current["backupId"] or restore_id)
        findings = post_restore_validation_findings(preflight)
        if findings:
            raise ConflictDetected(
                "post-restore validation must pass before publisher resume",
                details={"restore_id": restore_id, "findings": findings},
            )
        report = resume_approved_report(ctx, current, preflight, validation["validationId"])
        with self.engine.begin() as conn:
            self._audit_restore_mode(conn, ctx, report)
        return report

    def run_post_restore_validation(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestorePostRestoreValidationReport:
        """Run and persist post-restore validation evidence before approval."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", restore_id)
        current = self._approval_candidate(ctx, restore_id)
        preflight = self.restore_preflight_report(ctx=ctx, backup_id=current["backupId"] or restore_id)
        report = post_restore_validation_report(ctx, current, preflight, validation_id)
        with self.engine.begin() as conn:
            self._audit_post_restore_validation(conn, ctx, report)
        return report

    def recovery_overview(self, *, ctx: RequestContext | None = None) -> BackupRestoreRecoveryOverview:
        """Return the recovery console read model from durable evidence."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:summary", "backup_restore", "recovery")
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        audit_events = snapshot["auditEvents"]
        active_mode = active_restore_mode_report(audit_events)
        latest_mode = latest_restore_mode_report(audit_events)
        return recovery_overview_from_audit(
            ctx,
            active_mode=active_mode,
            latest_mode=latest_mode,
            audit_events=audit_events,
        )

    def _dataset_version_inventory(self, ctx: RequestContext) -> list[BackupRestoreDatasetVersion]:
        entries: list[BackupRestoreDatasetVersion] = []
        for dataset in self.dataset_repository.list_active_datasets(tenant_id=ctx.tenant_id):
            dataset_ref = f"{dataset['namespace']}.{dataset['name']}"
            versions = self.dataset_version_repository.list_versions(dataset_id=dataset["id"])
            for version in versions:
                entries.append(self._version_inventory(dataset_ref, dataset, version))
        return entries

    def _version_inventory(
        self,
        dataset_ref: str,
        dataset: DatasetRow,
        version: DatasetVersionRow,
    ) -> BackupRestoreDatasetVersion:
        try:
            manifest = self.dataset_storage.load_manifest(version["manifest_uri"])
            summary = self._manifest_summary(version, manifest)
        except FileNotFoundError as exc:
            return self._blocked_version(dataset_ref, dataset, version, "committed_manifest_missing", str(exc))
        except AdapterError as exc:
            return self._blocked_version(dataset_ref, dataset, version, _adapter_issue_code(exc), exc.message)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return self._blocked_version(dataset_ref, dataset, version, "committed_manifest_corrupt", str(exc))
        return _version_payload(dataset_ref, dataset, version, summary, issue=None)

    def _manifest_summary(
        self,
        version: DatasetVersionRow,
        manifest: DatasetManifest,
    ) -> RuntimeJsonObject:
        files = manifest.get("files") or []
        paths = self.dataset_storage.data_file_paths(version["manifest_uri"])
        if len(paths) != len(files):
            raise ValueError("manifest file list and resolved data paths disagree")
        self._validate_manifest_files(files, paths)
        row_count = sum(int(file["row_count"]) for file in files)
        byte_size = sum(int(file["byte_size"]) for file in files)
        if row_count != int(version["row_count"]) or byte_size != int(version["byte_size"]):
            raise ValueError("metadata DB version counts do not match committed manifest")
        return {
            "fileCount": len(files),
            "rowCount": row_count,
            "byteSize": byte_size,
            "contentHashes": [str(file["content_hash"]) for file in files],
            "storageProfile": str(manifest.get("storage_profile") or self.dataset_storage.profile_name),
        }

    def _validate_manifest_files(self, files: Sequence[DatasetManifestFile], paths: Sequence[Path]) -> None:
        for manifest_file, path in zip(files, paths, strict=True):
            if path.stat().st_size != int(manifest_file["byte_size"]):
                raise ValueError(f"data file byte size mismatch: {manifest_file['uri']}")
            if _file_hash(path) != str(manifest_file["content_hash"]):
                raise ValueError(f"data file content hash mismatch: {manifest_file['uri']}")

    def _blocked_version(
        self,
        dataset_ref: str,
        dataset: DatasetRow,
        version: DatasetVersionRow,
        code: str,
        message: str,
    ) -> BackupRestoreDatasetVersion:
        issue = _manifest_issue(dataset_ref, dataset, version, code, message)
        summary: RuntimeJsonObject = {"fileCount": 0, "rowCount": 0, "byteSize": 0, "contentHashes": []}
        return _version_payload(dataset_ref, dataset, version, summary, issue=issue)

    def _report(
        self,
        ctx: RequestContext,
        backup_id: str,
        versions: list[BackupRestoreDatasetVersion],
        issues: list[BackupRestoreManifestIssue],
        snapshot: RuntimeRunSnapshot,
    ) -> BackupRestorePreflightReport:
        return {
            "generatedAt": _now(),
            "tenantId": ctx.tenant_id,
            "backupId": backup_id,
            "status": "blocked" if issues else "ready",
            "datasetVersions": versions,
            "issues": issues,
            "activeIndexPointers": self._active_index_pointers(snapshot, ctx),
            "highWatermarks": _runtime_high_watermarks(snapshot),
            "temporalStrategy": self._temporal_strategy(),
            "searchRebuild": self._search_rebuild_marker(ctx, backup_id),
            "restoreTrafficGate": _restore_traffic_gate(),
            "summary": _summary(versions, issues),
        }

    def _active_index_pointers(
        self,
        snapshot: RuntimeRunSnapshot,
        ctx: RequestContext,
    ) -> list[BackupRestoreIndexPointer]:
        candidates = _index_pointer_candidates(snapshot["indexRuns"])
        with self.engine.begin() as conn:
            return [
                {
                    "objectTypeId": object_type_id,
                    "objectTypeApiName": object_type_api_name,
                    "activeIndexVersion": self.object_index_repository.active_index_version(
                        transaction=conn,
                        tenant_id=ctx.tenant_id,
                        object_type_id=object_type_id,
                    ),
                }
                for object_type_id, object_type_api_name in candidates
            ]

    def _temporal_strategy(self) -> RuntimeJsonObject:
        return {
            "workflowProfile": self.workflow_adapter.profile_name,
            "historyStrategy": "preserve_or_reconcile_before_resume",
            "namespaceStrategy": "restore_profile_namespace_before_workflow_resume",
            "productWorkflowExecution": "future_restore_executor_scope",
        }

    def _search_rebuild_marker(self, ctx: RequestContext, backup_id: str) -> RuntimeJsonObject:
        return {
            "searchProfile": self.search_adapter.profile_name,
            "projectionIsSourceOfTruth": False,
            "rebuildRequiredAfterRestore": True,
            "marker": f"search-rebuild:{ctx.tenant_id}:{backup_id}",
        }

    def _audit_report(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        report: BackupRestorePreflightReport,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="backup_restore.preflight_reported",
            resource_type="backup_restore",
            resource_id=report["backupId"],
            action="operations:read:detail",
            after_ref={
                "status": report["status"],
                "datasetVersionCount": len(report["datasetVersions"]),
                "issueCount": len(report["issues"]),
                "activeIndexPointerCount": len(report["activeIndexPointers"]),
            },
            correlation_id=ctx.request_id,
        )

    def _existing_restore_mode_report(self, ctx: RequestContext, restore_id: str) -> BackupRestoreModeReport | None:
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        return latest_restore_mode_report(snapshot["auditEvents"], restore_id=restore_id)


def _version_payload(
    dataset_ref: str,
    dataset: DatasetRow,
    version: DatasetVersionRow,
    summary: Mapping[str, object],
    *,
    issue: BackupRestoreManifestIssue | None,
) -> BackupRestoreDatasetVersion:
    return {
        "datasetRef": dataset_ref,
        "datasetId": dataset["id"],
        "branch": version["branch"],
        "versionId": version["id"],
        "versionNumber": version["version_number"],
        "manifestUri": version["manifest_uri"],
        "rowCount": version["row_count"],
        "byteSize": version["byte_size"],
        "storageProfile": str(summary.get("storageProfile") or "unknown"),
        "manifestFileCount": _summary_int(summary, "fileCount"),
        "manifestRowCount": _summary_int(summary, "rowCount"),
        "manifestByteSize": _summary_int(summary, "byteSize"),
        "manifestContentHashes": _summary_string_list(summary, "contentHashes"),
        "status": "valid" if issue is None else ("missing" if issue["code"].endswith("missing") else "corrupt"),
        "issue": issue,
    }


def _manifest_issue(
    dataset_ref: str,
    dataset: DatasetRow,
    version: DatasetVersionRow,
    code: str,
    message: str,
) -> BackupRestoreManifestIssue:
    return {
        "code": code,
        "datasetRef": dataset_ref,
        "datasetId": dataset["id"],
        "versionId": version["id"],
        "manifestUri": version["manifest_uri"],
        "message": message,
        "details": {
            "branch": version["branch"],
            "versionNumber": version["version_number"],
            "servingTruth": "metadata_db_committed_dataset_version",
            "restoreAction": "block_serving_until_manifest_inventory_matches_db",
        },
    }


def _adapter_issue_code(exc: AdapterError) -> str:
    if exc.failure.kind == "not_found":
        return "committed_manifest_missing"
    return "committed_manifest_corrupt"


def _runtime_high_watermarks(snapshot: RuntimeRunSnapshot) -> RuntimeJsonObject:
    return {
        "auditEvents": _table_watermark(snapshot["auditEvents"], ("created_at",)),
        "outboxEvents": _table_watermark(snapshot["outboxEvents"], ("created_at", "published_at")),
        "actionRuns": _table_watermark(snapshot["actionRuns"], ("created_at", "completed_at")),
        "actionWritebacks": _table_watermark(snapshot["actionWritebacks"], ("created_at", "updated_at")),
        "materializationRuns": _table_watermark(snapshot["materializationRuns"], ("created_at", "completed_at")),
    }


def _table_watermark(rows: Sequence[RuntimeRow], time_fields: Sequence[str]) -> RuntimeJsonObject:
    timestamps: list[str] = []
    for row in rows:
        timestamps.extend(_row_timestamps(row, time_fields))
    timestamps.sort()
    return {
        "count": len(rows),
        "maxTimestamp": timestamps[-1] if timestamps else None,
        "statusCounts": _status_counts(rows),
    }


def _status_counts(rows: Sequence[RuntimeRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = _string(row.get("status"))
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


def _index_pointer_candidates(index_runs: Sequence[RuntimeRow]) -> list[tuple[str, str]]:
    by_type: dict[str, str] = {}
    for row in index_runs:
        object_type_id = _string(row.get("object_type_id"))
        object_type_api_name = _string(row.get("object_type_api_name"))
        if object_type_id and object_type_api_name:
            by_type[object_type_id] = object_type_api_name
    return sorted(by_type.items(), key=lambda item: item[1])


def _restore_traffic_gate() -> RuntimeJsonObject:
    return {
        "writeTrafficMustBePausedBeforeRestore": True,
        "outboxPublisherMustRemainPausedUntilReconciled": True,
        "servingTrafficCanResumeAfter": "manifest_validation_and_operator_approval",
        "currentReportDoesNotOpenServingTraffic": True,
    }


def _summary(
    versions: Sequence[BackupRestoreDatasetVersion],
    issues: Sequence[BackupRestoreManifestIssue],
) -> RuntimeJsonObject:
    return {
        "datasetVersionCount": len(versions),
        "validDatasetVersionCount": len([version for version in versions if version["status"] == "valid"]),
        "issueCount": len(issues),
        "dbStoragePointMismatchCount": len(issues),
    }


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _row_timestamps(row: RuntimeRow, time_fields: Sequence[str]) -> list[str]:
    values: list[str] = []
    for field in time_fields:
        value = _string(row.get(field))
        if value is not None:
            values.append(value)
    return values


def _summary_int(summary: Mapping[str, object], key: str) -> int:
    value = summary[key]
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"summary field must be int-compatible: {key}")


def _summary_string_list(summary: Mapping[str, object], key: str) -> list[str]:
    value = summary[key]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return cast(list[str], value)
    raise TypeError(f"summary field must be list[str]: {key}")


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
