"""`restore_candidates` must restore only the backup head per (dataset, branch).

The artifact inventory lists every committed version of a dataset. Iterating it
raw produced one restore candidate per historical version and committed each as a
new head in turn, so the final head became a copy of an intermediate version
rather than the backup head — and an unchanged dataset was rewritten to a stale
version with spurious ``dataset.version.restored`` events. The reduction to the
head-per-(dataset, branch) is what makes "restore to the backup" mean the backup.
"""

from __future__ import annotations

from typing import cast

from foundry_lite.application.ports.dataset_repository import DatasetRow
from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreDatasetVersion,
    BackupRestorePreflightReport,
)
from foundry_lite.application.services.backup_restore_artifact_execution_helpers import (
    restore_candidates,
)

_DATASET_ID = "ds-orders"
_BRANCH = "main"


def _version(version_id: str, version_number: int) -> BackupRestoreDatasetVersion:
    return cast(
        BackupRestoreDatasetVersion,
        {
            "datasetRef": "raw.orders",
            "datasetId": _DATASET_ID,
            "branch": _BRANCH,
            "versionId": version_id,
            "versionNumber": version_number,
            "manifestUri": f"file:///{version_id}",
            "rowCount": 1,
            "byteSize": 1,
            "storageProfile": "local",
            "manifestFileCount": 1,
        },
    )


def _preflight(versions: list[BackupRestoreDatasetVersion]) -> BackupRestorePreflightReport:
    return cast(
        BackupRestorePreflightReport,
        {
            "generatedAt": "2026-07-29T00:00:00Z",
            "tenantId": "tenant-test",
            "backupId": "backup-1",
            "status": "ready",
            "datasetVersions": versions,
            "issues": [],
        },
    )


def _datasets() -> dict[str, DatasetRow]:
    return {_DATASET_ID: cast(DatasetRow, {"id": _DATASET_ID})}


def test_unchanged_dataset_produces_no_restore_candidates() -> None:
    # Backup captured v1, v2, v3 (head v3); nothing changed since.
    artifact = _preflight([_version("v1", 1), _version("v2", 2), _version("v3", 3)])
    current = _preflight([_version("v1", 1), _version("v2", 2), _version("v3", 3)])

    candidates = restore_candidates(artifact, current, _datasets())

    # The backup head already equals the serving head, so nothing is restored —
    # not two spurious candidates for the historical versions v1 and v2.
    assert candidates == []


def test_drifted_dataset_restores_only_the_backup_head() -> None:
    # Backup head is v3; the live dataset has since drifted to head v5.
    artifact = _preflight([_version("v1", 1), _version("v2", 2), _version("v3", 3)])
    current = _preflight([_version("v3", 3), _version("v4", 4), _version("v5", 5)])

    candidates = restore_candidates(artifact, current, _datasets())

    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.artifact_version["versionId"] == "v3"
    assert candidate.current_head_version_id == "v5"
