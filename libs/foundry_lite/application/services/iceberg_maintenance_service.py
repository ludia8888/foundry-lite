"""Application service helpers for iceberg maintenance service workflows."""

from __future__ import annotations

from typing import cast

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetVersionRow,
    RuntimeJsonObject,
    TransactionContext,
)
from foundry_lite.application.ports.iceberg_maintenance import (
    IcebergMaintenanceAdapter,
    IcebergMaintenancePlan,
    IcebergMaintenancePolicy,
    IcebergMaintenanceRun,
)
from foundry_lite.application.primitives import _dataset_ref_parts
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

DEFAULT_SMALL_FILE_THRESHOLD_BYTES = 32 * 1024 * 1024
DEFAULT_FILE_COUNT_THRESHOLD = 32
DEFAULT_READ_AMPLIFICATION_THRESHOLD = 2.0
DEFAULT_RETENTION_MIN_SNAPSHOTS = 1


class IcebergMaintenanceService(CoreService):
    required_dependencies = (
        "engine",
        "dataset_repository",
        "dataset_version_repository",
        "dataset_storage",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: DatasetRuntimeBoundary

    def plan_iceberg_maintenance(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        branch: str = "main",
        small_file_threshold_bytes: int = DEFAULT_SMALL_FILE_THRESHOLD_BYTES,
        file_count_threshold: int = DEFAULT_FILE_COUNT_THRESHOLD,
        read_amplification_threshold: float = DEFAULT_READ_AMPLIFICATION_THRESHOLD,
        retention_min_snapshots: int = DEFAULT_RETENTION_MIN_SNAPSHOTS,
    ) -> IcebergMaintenancePlan:
        """Return a dry-run Iceberg maintenance plan with operator audit evidence."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "iceberg_maintenance", dataset_ref)
        adapter = self._iceberg_maintenance_adapter()
        policy = self._maintenance_policy(
            small_file_threshold_bytes=small_file_threshold_bytes,
            file_count_threshold=file_count_threshold,
            read_amplification_threshold=read_amplification_threshold,
            retention_min_snapshots=retention_min_snapshots,
        )
        dataset = self._dataset(ctx, dataset_ref)
        versions = self._committed_versions(dataset["id"], branch)
        plan = adapter.plan_iceberg_maintenance(
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
            dataset_ref=dataset_ref,
            branch=branch,
            committed_versions=versions,
            policy=policy,
        )
        with self.engine.begin() as conn:
            self._audit_plan(conn, ctx, dataset_ref, plan)
        return plan

    def run_iceberg_maintenance(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        branch: str = "main",
        small_file_threshold_bytes: int = DEFAULT_SMALL_FILE_THRESHOLD_BYTES,
        file_count_threshold: int = DEFAULT_FILE_COUNT_THRESHOLD,
        read_amplification_threshold: float = DEFAULT_READ_AMPLIFICATION_THRESHOLD,
        retention_min_snapshots: int = DEFAULT_RETENTION_MIN_SNAPSHOTS,
    ) -> IcebergMaintenanceRun:
        """Run bounded Iceberg maintenance and record operator audit evidence."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "iceberg_maintenance", dataset_ref)
        adapter = self._iceberg_maintenance_adapter()
        policy = self._maintenance_policy(
            small_file_threshold_bytes=small_file_threshold_bytes,
            file_count_threshold=file_count_threshold,
            read_amplification_threshold=read_amplification_threshold,
            retention_min_snapshots=retention_min_snapshots,
        )
        dataset = self._dataset(ctx, dataset_ref)
        run = adapter.run_iceberg_maintenance(
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
            dataset_ref=dataset_ref,
            branch=branch,
            committed_versions=self._committed_versions(dataset["id"], branch),
            policy=policy,
        )
        with self.engine.begin() as conn:
            self._audit_run(conn, ctx, dataset_ref, run)
        return run

    def _dataset(self, ctx: RequestContext, dataset_ref: str) -> DatasetRow:
        namespace, name = _dataset_ref_parts(dataset_ref)
        dataset = self.dataset_repository.find_active_dataset(
            tenant_id=ctx.tenant_id,
            namespace=namespace,
            name=name,
        )
        if dataset is None:
            raise NotFound("dataset not found", details={"dataset_ref": dataset_ref})
        return dataset

    def _committed_versions(self, dataset_id: str, branch: str) -> list[DatasetVersionRow]:
        return [
            row
            for row in self.dataset_version_repository.list_versions(dataset_id=dataset_id)
            if row["branch"] == branch
        ]

    def _iceberg_maintenance_adapter(self) -> IcebergMaintenanceAdapter:
        if not hasattr(self.dataset_storage, "plan_iceberg_maintenance"):
            raise ValidationFailed(
                "active dataset storage profile does not support Iceberg maintenance",
                details={"storage_profile": self.dataset_storage.profile_name},
            )
        return cast(IcebergMaintenanceAdapter, self.dataset_storage)

    def _maintenance_policy(
        self,
        *,
        small_file_threshold_bytes: int,
        file_count_threshold: int,
        read_amplification_threshold: float,
        retention_min_snapshots: int,
    ) -> IcebergMaintenancePolicy:
        if small_file_threshold_bytes <= 0:
            raise ValidationFailed("small file threshold must be positive")
        if file_count_threshold <= 0:
            raise ValidationFailed("file count threshold must be positive")
        if read_amplification_threshold <= 0:
            raise ValidationFailed("read amplification threshold must be positive")
        if retention_min_snapshots <= 0:
            raise ValidationFailed("retention minimum snapshots must be positive")
        return {
            "small_file_threshold_bytes": small_file_threshold_bytes,
            "file_count_threshold": file_count_threshold,
            "read_amplification_threshold": read_amplification_threshold,
            "retention_min_snapshots": retention_min_snapshots,
        }

    def _audit_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_ref: str,
        plan: IcebergMaintenancePlan,
    ) -> None:
        after_ref: RuntimeJsonObject = {
            "datasetRef": dataset_ref,
            "candidateCount": len(plan["compaction_candidates"]),
            "orphanSnapshotCount": len(plan["orphan_snapshots"]),
            "protectedSnapshotIds": list(plan["protected_snapshot_ids"]),
            "deletableSnapshotIds": list(plan["deletable_snapshot_ids"]),
            "status": plan["status"],
        }
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="iceberg_maintenance.plan_created",
            resource_type="dataset",
            resource_id=plan["dataset_id"],
            action="operations:read:detail",
            after_ref=after_ref,
            correlation_id=ctx.request_id,
        )

    def _audit_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_ref: str,
        run: IcebergMaintenanceRun,
    ) -> None:
        after_ref: RuntimeJsonObject = {
            "datasetRef": dataset_ref,
            "status": run["status"],
            "beforeSnapshotId": run["before_snapshot_id"],
            "afterSnapshotId": run["after_snapshot_id"],
            "compactedSnapshotId": run["compacted_snapshot_id"],
            "isRowHashPreserved": run["is_row_hash_preserved"],
            "expiredSnapshotIds": list(run["expired_snapshot_ids"]),
            "orphanCleanupFileCount": len(run["orphan_cleanup_file_uris"]),
            "protectedSnapshotIds": list(run["protected_snapshot_ids"]),
        }
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="iceberg_maintenance.run_completed",
            resource_type="dataset",
            resource_id=run["dataset_id"],
            action="operations:retry",
            after_ref=after_ref,
            correlation_id=ctx.request_id,
        )
