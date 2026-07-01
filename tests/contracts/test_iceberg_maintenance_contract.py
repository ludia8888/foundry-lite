from __future__ import annotations

from foundry_lite.application.ports import (
    DatasetVersionRow,
)
from foundry_lite.application.ports.iceberg_maintenance import (
    IcebergMaintenanceAdapter,
    IcebergMaintenancePlan,
    IcebergMaintenancePolicy,
    IcebergMaintenanceRun,
    IcebergMaintenanceSnapshot,
)


class FakeIcebergMaintenanceAdapter:
    profile_name = "iceberg"

    def plan_iceberg_maintenance(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_ref: str,
        branch: str,
        committed_versions: list[DatasetVersionRow],
        policy: IcebergMaintenancePolicy,
    ) -> IcebergMaintenancePlan:
        del tenant_id
        protected: IcebergMaintenanceSnapshot = {
            "snapshot_id": 101,
            "version_id": committed_versions[0]["id"],
            "manifest_uri": committed_versions[0]["manifest_uri"],
            "file_count": 1,
            "row_count": 10,
            "total_bytes": 10,
            "average_file_size_bytes": 10,
            "read_amplification": 1.0,
            "is_current": True,
            "is_protected": True,
            "protected_by": [f"committed_db_version:{committed_versions[0]['id']}"],
            "reason_codes": ["small_files"],
        }
        orphan: IcebergMaintenanceSnapshot = {
            **protected,
            "snapshot_id": 99,
            "version_id": None,
            "manifest_uri": "iceberg://warehouse/table@99",
            "is_current": False,
            "is_protected": False,
            "protected_by": [],
            "reason_codes": ["orphan_snapshot"],
        }
        return {
            "dataset_ref": dataset_ref,
            "dataset_id": dataset_id,
            "branch": branch,
            "storage_profile": self.profile_name,
            "table_identifier": "warehouse.table",
            "current_snapshot_id": 101,
            "policy": policy,
            "snapshots": [protected, orphan],
            "compaction_candidates": [protected],
            "orphan_snapshots": [orphan],
            "protected_snapshot_ids": [101],
            "retained_snapshot_ids": [101],
            "deletable_snapshot_ids": [99],
            "status": "maintenance_needed",
        }

    def run_iceberg_maintenance(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_ref: str,
        branch: str,
        committed_versions: list[DatasetVersionRow],
        policy: IcebergMaintenancePolicy,
    ) -> IcebergMaintenanceRun:
        plan = self.plan_iceberg_maintenance(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_ref=dataset_ref,
            branch=branch,
            committed_versions=committed_versions,
            policy=policy,
        )
        return {
            "dataset_ref": dataset_ref,
            "dataset_id": dataset_id,
            "branch": branch,
            "storage_profile": self.profile_name,
            "table_identifier": plan["table_identifier"],
            "status": "completed",
            "policy": policy,
            "before_snapshot_id": 101,
            "after_snapshot_id": 202,
            "compacted_snapshot_id": 202,
            "rewritten_data_file_count": 8,
            "output_data_file_count": 1,
            "before_row_hash": "row-hash",
            "after_row_hash": "row-hash",
            "is_row_hash_preserved": True,
            "expired_snapshot_ids": [99],
            "protected_snapshot_ids": [101],
            "retained_snapshot_ids": [101],
            "skipped_expiration_snapshot_ids": [],
            "orphan_cleanup_file_uris": ["s3://bucket/table/data/orphan.parquet"],
            "before_plan_status": plan["status"],
            "after_plan_status": "healthy",
        }


def test_iceberg_maintenance_adapter_contract_reports_protection_and_candidates() -> None:
    adapter: IcebergMaintenanceAdapter = FakeIcebergMaintenanceAdapter()
    version = _version_row("dsv_committed", "iceberg://warehouse/table@101")

    plan = adapter.plan_iceberg_maintenance(
        tenant_id="tenant-demo",
        dataset_id="ds_orders",
        dataset_ref="raw.orders",
        branch="main",
        committed_versions=[version],
        policy={
            "small_file_threshold_bytes": 32,
            "file_count_threshold": 4,
            "read_amplification_threshold": 2.0,
            "retention_min_snapshots": 1,
        },
    )

    assert plan["storage_profile"] == "iceberg"
    assert plan["protected_snapshot_ids"] == [101]
    assert plan["deletable_snapshot_ids"] == [99]
    assert plan["compaction_candidates"][0]["protected_by"] == ["committed_db_version:dsv_committed"]
    assert plan["orphan_snapshots"][0]["reason_codes"] == ["orphan_snapshot"]


def test_iceberg_maintenance_adapter_contract_reports_run_evidence() -> None:
    adapter: IcebergMaintenanceAdapter = FakeIcebergMaintenanceAdapter()
    version = _version_row("dsv_committed", "iceberg://warehouse/table@101")

    run = adapter.run_iceberg_maintenance(
        tenant_id="tenant-demo",
        dataset_id="ds_orders",
        dataset_ref="raw.orders",
        branch="main",
        committed_versions=[version],
        policy={
            "small_file_threshold_bytes": 32,
            "file_count_threshold": 4,
            "read_amplification_threshold": 2.0,
            "retention_min_snapshots": 1,
        },
    )

    assert run["status"] == "completed"
    assert run["is_row_hash_preserved"] is True
    assert run["compacted_snapshot_id"] == 202
    assert run["expired_snapshot_ids"] == [99]
    assert run["orphan_cleanup_file_uris"] == ["s3://bucket/table/data/orphan.parquet"]


def _version_row(version_id: str, manifest_uri: str) -> DatasetVersionRow:
    return {
        "id": version_id,
        "tenant_id": "tenant-demo",
        "dataset_id": "ds_orders",
        "branch": "main",
        "version_number": 1,
        "transaction_id": "dstx_committed",
        "schema_version": 1,
        "manifest_uri": manifest_uri,
        "row_count": 10,
        "byte_size": 10,
        "status": "COMMITTED",
        "superseded_by_version_id": None,
        "created_at": "2026-06-18T00:00:00Z",
    }
