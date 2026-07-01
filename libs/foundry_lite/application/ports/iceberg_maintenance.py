"""Application port contract for iceberg maintenance."""

from __future__ import annotations

from typing import Protocol, TypedDict

from foundry_lite.application.ports.dataset_version_repository import DatasetVersionRow


class IcebergMaintenancePolicy(TypedDict):
    small_file_threshold_bytes: int
    file_count_threshold: int
    read_amplification_threshold: float
    retention_min_snapshots: int


class IcebergMaintenanceSnapshot(TypedDict):
    snapshot_id: int
    version_id: str | None
    manifest_uri: str
    file_count: int
    row_count: int
    total_bytes: int
    average_file_size_bytes: int
    read_amplification: float
    is_current: bool
    is_protected: bool
    protected_by: list[str]
    reason_codes: list[str]


class IcebergMaintenancePlan(TypedDict):
    dataset_ref: str
    dataset_id: str
    branch: str
    storage_profile: str
    table_identifier: str
    current_snapshot_id: int | None
    policy: IcebergMaintenancePolicy
    snapshots: list[IcebergMaintenanceSnapshot]
    compaction_candidates: list[IcebergMaintenanceSnapshot]
    orphan_snapshots: list[IcebergMaintenanceSnapshot]
    protected_snapshot_ids: list[int]
    retained_snapshot_ids: list[int]
    deletable_snapshot_ids: list[int]
    status: str


class IcebergMaintenanceRun(TypedDict):
    """Evidence returned after a bounded Iceberg maintenance execution."""

    dataset_ref: str
    dataset_id: str
    branch: str
    storage_profile: str
    table_identifier: str
    status: str
    policy: IcebergMaintenancePolicy
    before_snapshot_id: int | None
    after_snapshot_id: int | None
    compacted_snapshot_id: int | None
    rewritten_data_file_count: int
    output_data_file_count: int
    before_row_hash: str | None
    after_row_hash: str | None
    is_row_hash_preserved: bool | None
    expired_snapshot_ids: list[int]
    protected_snapshot_ids: list[int]
    retained_snapshot_ids: list[int]
    skipped_expiration_snapshot_ids: list[int]
    orphan_cleanup_file_uris: list[str]
    before_plan_status: str
    after_plan_status: str


class IcebergMaintenanceAdapter(Protocol):
    """Optional storage capability for Iceberg-specific table maintenance."""

    @property
    def profile_name(self) -> str: ...

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
        """Return a dry-run maintenance plan without mutating the serving table."""
        ...

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
        """Execute table maintenance while preserving committed-version snapshots."""
        ...
