"""Backup/restore preflight use-case service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from foundry_lite.application.ports import (
    AdapterError,
    BackupRestoreDatasetVersion,
    BackupRestoreIndexPointer,
    BackupRestoreManifestIssue,
    BackupRestorePreflightReport,
    DatasetManifest,
    DatasetRow,
    DatasetVersionRow,
    RuntimeJsonObject,
    RuntimeRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext


class BackupRestorePreflightService(CoreService):
    """Compare metadata DB commit points with storage before restore."""

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
        ctx = ctx or RequestContext()
        report_id = backup_id or f"preflight-{ctx.request_id}"
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "backup_restore", report_id)
        dataset_versions = self._dataset_version_inventory(ctx)
        issues = [item["issue"] for item in dataset_versions if item["issue"] is not None]
        high_watermarks = self.runtime_repository.backup_restore_high_watermarks(tenant_id=ctx.tenant_id)
        index_candidates = self.runtime_repository.backup_restore_index_candidates(tenant_id=ctx.tenant_id)
        report = self._report(ctx, report_id, dataset_versions, issues, high_watermarks, index_candidates)
        with self.engine.begin() as conn:
            self._audit_report(conn, ctx, report)
        return report

    def _dataset_version_inventory(self, ctx: RequestContext) -> list[BackupRestoreDatasetVersion]:
        coordinates: list[tuple[str, DatasetRow, DatasetVersionRow]] = []
        for dataset in self.dataset_repository.list_active_datasets(tenant_id=ctx.tenant_id):
            dataset_ref = f"{dataset['namespace']}.{dataset['name']}"
            versions = self.dataset_version_repository.list_versions(dataset_id=dataset["id"])
            for version in versions:
                coordinates.append((dataset_ref, dataset, version))
        if len(coordinates) < 2:
            return [self._version_inventory(*coordinate) for coordinate in coordinates]
        with ThreadPoolExecutor(max_workers=min(8, len(coordinates)), thread_name_prefix="backup-preflight") as pool:
            return list(pool.map(self._version_inventory_coordinate, coordinates))

    def _version_inventory_coordinate(
        self,
        coordinate: tuple[str, DatasetRow, DatasetVersionRow],
    ) -> BackupRestoreDatasetVersion:
        return self._version_inventory(*coordinate)

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
            return self._blocked_version(
                dataset_ref, dataset, version, "committed_manifest_missing", scrub_error_text(str(exc))
            )
        except AdapterError as exc:
            return self._blocked_version(
                dataset_ref, dataset, version, _adapter_issue_code(exc), scrub_error_text(exc.message)
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return self._blocked_version(
                dataset_ref, dataset, version, "committed_manifest_corrupt", scrub_error_text(str(exc))
            )
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
        high_watermarks: RuntimeJsonObject,
        index_candidates: Sequence[RuntimeRow],
    ) -> BackupRestorePreflightReport:
        return {
            "generatedAt": _now(),
            "tenantId": ctx.tenant_id,
            "backupId": backup_id,
            "status": "blocked" if issues else "ready",
            "datasetVersions": versions,
            "issues": issues,
            "activeIndexPointers": self._active_index_pointers(index_candidates, ctx),
            "highWatermarks": high_watermarks,
            "temporalStrategy": self._temporal_strategy(),
            "searchRebuild": self._search_rebuild_marker(ctx, backup_id),
            "restoreTrafficGate": _restore_traffic_gate(),
            "summary": _summary(versions, issues),
        }

    def _active_index_pointers(
        self,
        index_candidates: Sequence[RuntimeRow],
        ctx: RequestContext,
    ) -> list[BackupRestoreIndexPointer]:
        candidates = _index_pointer_candidates(index_candidates)
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
