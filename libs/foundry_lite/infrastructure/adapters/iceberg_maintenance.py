"""Maintenance plan/run construction and snapshot classification helpers for the Iceberg adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from foundry_lite.application.ports import DatasetManifestFile, DatasetVersionRow
from foundry_lite.application.ports.iceberg_maintenance import (
    IcebergMaintenancePlan,
    IcebergMaintenancePolicy,
    IcebergMaintenanceRun,
    IcebergMaintenanceSnapshot,
)
from foundry_lite.infrastructure.adapters.iceberg_manifests import (
    _BRANCH_PROP,
    _CREATED_AT_PROP,
    _DATASET_PROP,
    _SCHEMA_PROP,
    _VERSION_PROP,
)

_MAINTENANCE_KIND_PROP = "foundry.maintenance.kind"


_MAINTENANCE_BEFORE_SNAPSHOT_PROP = "foundry.maintenance.before_snapshot_id"


@dataclass(frozen=True)
class _MaintenanceRewriteResult:
    """Compaction evidence for the current Iceberg snapshot."""

    before_snapshot_id: int | None
    after_snapshot_id: int | None
    compacted_snapshot_id: int | None
    rewritten_data_file_count: int
    output_data_file_count: int
    before_row_hash: str | None
    after_row_hash: str | None
    is_row_hash_preserved: bool | None


def _empty_maintenance_run(
    profile_name: str,
    dataset_ref: str,
    dataset_id: str,
    branch: str,
    identifier: str,
    policy: IcebergMaintenancePolicy,
) -> IcebergMaintenanceRun:
    rewrite = _empty_rewrite_result(None)
    empty_plan = _empty_maintenance_plan(profile_name, dataset_ref, dataset_id, branch, identifier, policy)
    return _maintenance_run(
        profile_name,
        empty_plan,
        empty_plan,
        rewrite,
        expired_snapshot_ids=[],
        skipped_expiration_snapshot_ids=[],
        orphan_cleanup_file_uris=[],
    )


def _empty_maintenance_plan(
    profile_name: str,
    dataset_ref: str,
    dataset_id: str,
    branch: str,
    identifier: str,
    policy: IcebergMaintenancePolicy,
) -> IcebergMaintenancePlan:
    return {
        "dataset_ref": dataset_ref,
        "dataset_id": dataset_id,
        "branch": branch,
        "storage_profile": profile_name,
        "table_identifier": identifier,
        "current_snapshot_id": None,
        "policy": policy,
        "snapshots": [],
        "compaction_candidates": [],
        "orphan_snapshots": [],
        "protected_snapshot_ids": [],
        "retained_snapshot_ids": [],
        "deletable_snapshot_ids": [],
        "status": "no_table",
    }


def _maintenance_run(
    profile_name: str,
    before_plan: IcebergMaintenancePlan,
    after_plan: IcebergMaintenancePlan,
    rewrite: _MaintenanceRewriteResult,
    *,
    expired_snapshot_ids: list[int],
    skipped_expiration_snapshot_ids: list[int],
    orphan_cleanup_file_uris: list[str],
) -> IcebergMaintenanceRun:
    status = _maintenance_run_status(rewrite, expired_snapshot_ids, before_plan["status"])
    return {
        "dataset_ref": before_plan["dataset_ref"],
        "dataset_id": before_plan["dataset_id"],
        "branch": before_plan["branch"],
        "storage_profile": profile_name,
        "table_identifier": before_plan["table_identifier"],
        "status": status,
        "policy": before_plan["policy"],
        "before_snapshot_id": rewrite.before_snapshot_id,
        "after_snapshot_id": rewrite.after_snapshot_id,
        "compacted_snapshot_id": rewrite.compacted_snapshot_id,
        "rewritten_data_file_count": rewrite.rewritten_data_file_count,
        "output_data_file_count": rewrite.output_data_file_count,
        "before_row_hash": rewrite.before_row_hash,
        "after_row_hash": rewrite.after_row_hash,
        "is_row_hash_preserved": rewrite.is_row_hash_preserved,
        "expired_snapshot_ids": sorted(expired_snapshot_ids),
        "protected_snapshot_ids": after_plan["protected_snapshot_ids"],
        "retained_snapshot_ids": after_plan["retained_snapshot_ids"],
        "skipped_expiration_snapshot_ids": sorted(skipped_expiration_snapshot_ids),
        "orphan_cleanup_file_uris": sorted(orphan_cleanup_file_uris),
        "before_plan_status": before_plan["status"],
        "after_plan_status": after_plan["status"],
    }


def _maintenance_run_status(
    rewrite: _MaintenanceRewriteResult,
    expired_snapshot_ids: list[int],
    before_plan_status: str,
) -> str:
    if before_plan_status == "no_table":
        return "no_table"
    if rewrite.compacted_snapshot_id is None and not expired_snapshot_ids:
        return "skipped"
    return "completed"


def _maintenance_plan(
    profile_name: str,
    dataset_ref: str,
    dataset_id: str,
    branch: str,
    identifier: str,
    policy: IcebergMaintenancePolicy,
    snapshots: list[IcebergMaintenanceSnapshot],
    current: Any | None,
) -> IcebergMaintenancePlan:
    candidates = _snapshots_with_reasons(snapshots)
    orphans = _orphan_snapshots(snapshots)
    return {
        "dataset_ref": dataset_ref,
        "dataset_id": dataset_id,
        "branch": branch,
        "storage_profile": profile_name,
        "table_identifier": identifier,
        "current_snapshot_id": _current_snapshot_id(current),
        "policy": policy,
        "snapshots": snapshots,
        "compaction_candidates": candidates,
        "orphan_snapshots": orphans,
        "protected_snapshot_ids": _protected_snapshot_ids(snapshots),
        "retained_snapshot_ids": _plan_retained_snapshot_ids(snapshots),
        "deletable_snapshot_ids": _deletable_snapshot_ids(orphans),
        "status": _maintenance_plan_status(candidates, orphans),
    }


def _snapshots_with_reasons(snapshots: list[IcebergMaintenanceSnapshot]) -> list[IcebergMaintenanceSnapshot]:
    return [snapshot for snapshot in snapshots if snapshot["reason_codes"]]


def _orphan_snapshots(snapshots: list[IcebergMaintenanceSnapshot]) -> list[IcebergMaintenanceSnapshot]:
    return [snapshot for snapshot in snapshots if "orphan_snapshot" in snapshot["reason_codes"]]


def _protected_snapshot_ids(snapshots: list[IcebergMaintenanceSnapshot]) -> list[int]:
    return sorted(snapshot["snapshot_id"] for snapshot in snapshots if snapshot["is_protected"])


def _plan_retained_snapshot_ids(snapshots: list[IcebergMaintenanceSnapshot]) -> list[int]:
    retained = (
        snapshot["snapshot_id"] for snapshot in snapshots if "retention_min_snapshot" in snapshot["protected_by"]
    )
    return sorted(set(retained))


def _deletable_snapshot_ids(orphans: list[IcebergMaintenanceSnapshot]) -> list[int]:
    return sorted(snapshot["snapshot_id"] for snapshot in orphans if not snapshot["is_protected"])


def _current_compaction_candidate(plan: IcebergMaintenancePlan) -> IcebergMaintenanceSnapshot | None:
    compaction_reasons = {"too_many_files", "small_files", "read_amplification"}
    for snapshot in plan["compaction_candidates"]:
        if snapshot["is_current"] and compaction_reasons & set(snapshot["reason_codes"]):
            return snapshot
    return None


def _empty_rewrite_result(snapshot_id: int | None) -> _MaintenanceRewriteResult:
    return _MaintenanceRewriteResult(
        before_snapshot_id=snapshot_id,
        after_snapshot_id=snapshot_id,
        compacted_snapshot_id=None,
        rewritten_data_file_count=0,
        output_data_file_count=0,
        before_row_hash=None,
        after_row_hash=None,
        is_row_hash_preserved=None,
    )


def _rewrite_result(
    before_snapshot_id: int,
    after_snapshot_id: int,
    before_file_count: int,
    output_file_count: int,
    before_row_hash: str,
    after_row_hash: str,
) -> _MaintenanceRewriteResult:
    return _MaintenanceRewriteResult(
        before_snapshot_id=before_snapshot_id,
        after_snapshot_id=after_snapshot_id,
        compacted_snapshot_id=after_snapshot_id,
        rewritten_data_file_count=before_file_count,
        output_data_file_count=output_file_count,
        before_row_hash=before_row_hash,
        after_row_hash=after_row_hash,
        is_row_hash_preserved=before_row_hash == after_row_hash,
    )


def _current_snapshot_id(current: Any | None) -> int | None:
    if current is None:
        return None
    return int(current.snapshot_id)


def _maintenance_plan_status(
    candidates: list[IcebergMaintenanceSnapshot],
    orphans: list[IcebergMaintenanceSnapshot],
) -> str:
    if candidates or orphans:
        return "maintenance_needed"
    return "healthy"


def _maintenance_snapshot(
    snapshot: Any,
    manifest_uri: str,
    files: list[DatasetManifestFile],
    *,
    current_snapshot_id: int | None,
    committed_version_id: str | None,
    retained_snapshot_ids: set[int],
    policy: IcebergMaintenancePolicy,
) -> IcebergMaintenanceSnapshot:
    snapshot_id = int(snapshot.snapshot_id)
    file_count = len(files)
    total_bytes = sum(file["byte_size"] for file in files)
    row_count = sum(file["row_count"] for file in files)
    average = int(total_bytes / file_count) if file_count else 0
    protected_by = _protected_reasons(snapshot_id, current_snapshot_id, committed_version_id, retained_snapshot_ids)
    return {
        "snapshot_id": snapshot_id,
        "version_id": _snapshot_version_id(snapshot, committed_version_id),
        "manifest_uri": manifest_uri,
        "file_count": file_count,
        "row_count": row_count,
        "total_bytes": total_bytes,
        "average_file_size_bytes": average,
        "read_amplification": float(file_count),
        "is_current": snapshot_id == current_snapshot_id,
        "is_protected": bool(protected_by),
        "protected_by": protected_by,
        "reason_codes": _maintenance_reason_codes(file_count, average, committed_version_id, policy),
    }


def _protected_reasons(
    snapshot_id: int,
    current_snapshot_id: int | None,
    committed_version_id: str | None,
    retained_snapshot_ids: set[int],
) -> list[str]:
    reasons: list[str] = []
    if snapshot_id == current_snapshot_id:
        reasons.append("current_snapshot")
    if committed_version_id is not None:
        reasons.append(f"committed_db_version:{committed_version_id}")
    if snapshot_id in retained_snapshot_ids:
        reasons.append("retention_min_snapshot")
    return reasons


def _maintenance_reason_codes(
    file_count: int,
    average_file_size_bytes: int,
    committed_version_id: str | None,
    policy: IcebergMaintenancePolicy,
) -> list[str]:
    reasons: list[str] = []
    if committed_version_id is None:
        reasons.append("orphan_snapshot")
    if file_count > policy["file_count_threshold"]:
        reasons.append("too_many_files")
    if file_count and average_file_size_bytes < policy["small_file_threshold_bytes"]:
        reasons.append("small_files")
    if float(file_count) > policy["read_amplification_threshold"]:
        reasons.append("read_amplification")
    return reasons


def _snapshot_version_id(snapshot: Any, committed_version_id: str | None) -> str | None:
    value = snapshot.summary.additional_properties.get(_VERSION_PROP)
    return value if isinstance(value, str) and value else committed_version_id


def _maintenance_snapshot_properties(
    snapshot: Any,
    dataset_ref: str,
    branch: str,
    before_snapshot_id: int,
) -> dict[str, str]:
    created_at = _utc_now_iso()
    schema_hash = str(snapshot.summary.additional_properties.get(_SCHEMA_PROP) or "")
    return {
        _VERSION_PROP: f"maintenance:{before_snapshot_id}:{created_at}",
        _DATASET_PROP: dataset_ref,
        _BRANCH_PROP: branch,
        _SCHEMA_PROP: schema_hash,
        _CREATED_AT_PROP: created_at,
        _MAINTENANCE_KIND_PROP: "compaction",
        _MAINTENANCE_BEFORE_SNAPSHOT_PROP: str(before_snapshot_id),
    }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _committed_snapshot_versions(
    committed_versions: list[DatasetVersionRow],
    parse_manifest_uri: object,
) -> dict[int, str]:
    parser = cast(Callable[[str], tuple[str, int]], parse_manifest_uri)
    committed: dict[int, str] = {}
    for version in committed_versions:
        try:
            _, snapshot_id = parser(version["manifest_uri"])
        except ValueError:
            continue
        committed[snapshot_id] = version["id"]
    return committed


def _retained_snapshot_ids(
    snapshots: Sequence[Any],
    current: Any | None,
    committed: Mapping[int, str],
    policy: IcebergMaintenancePolicy,
) -> set[int]:
    retained = set(committed)
    if current is not None:
        retained.add(int(current.snapshot_id))
    minimum = policy["retention_min_snapshots"]
    retained.update(int(snapshot.snapshot_id) for snapshot in snapshots[-minimum:])
    return retained
