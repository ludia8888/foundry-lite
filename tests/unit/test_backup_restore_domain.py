from __future__ import annotations

import pytest
from foundry_lite.domain.backup_restore.artifact_restore import (
    artifact_restore_findings,
    latest_versions_by_dataset,
    require_artifact_tenant,
)
from foundry_lite.domain.backup_restore.restore_mode import (
    post_restore_validation_findings,
    require_restore_resume_ready,
)
from foundry_lite.domain.errors import ConflictDetected


def test_post_restore_findings_require_closed_loop_evidence() -> None:
    preflight = {
        "status": "ready",
        "datasetVersions": [{"id": "version-1"}],
        "activeIndexPointers": [{"objectType": "Order"}],
        "highWatermarks": {"actionRuns": {"count": 1}, "materializationRuns": {"count": 0}},
    }

    findings = post_restore_validation_findings(preflight)

    assert findings == [{"check": "materializationRuns", "status": "missing"}]


def test_restore_resume_ready_fails_closed_with_findings() -> None:
    with pytest.raises(ConflictDetected) as exc:
        require_restore_resume_ready("restore-1", [{"check": "actionRuns", "status": "missing"}])

    assert exc.value.details["restore_id"] == "restore-1"


def test_artifact_restore_findings_block_when_serving_head_moved() -> None:
    artifact = {
        "status": "ready",
        "datasetVersions": [
            {
                "datasetId": "ds-1",
                "branch": "main",
                "versionId": "v1",
                "versionNumber": 1,
                "manifestUri": "m1",
                "rowCount": 1,
                "byteSize": 10,
                "manifestContentHashes": ["h1"],
                "status": "valid",
            }
        ],
    }
    current = {
        "status": "ready",
        "datasetVersions": [
            {
                "datasetId": "ds-1",
                "branch": "main",
                "versionId": "v2",
                "versionNumber": 2,
                "manifestUri": "m2",
                "rowCount": 2,
                "byteSize": 20,
                "manifestContentHashes": ["h2"],
                "status": "valid",
            }
        ],
    }

    findings = artifact_restore_findings(artifact, current)

    assert findings == [
        {"check": "artifact_dataset_version_present", "status": "missing", "versionId": "v1"},
        {
            "check": "serving_head_matches_artifact",
            "status": "blocked",
            "datasetId": "ds-1",
            "artifactVersionId": "v1",
            "currentHeadVersionId": "v2",
        },
    ]


def test_latest_versions_by_dataset_uses_highest_version_number_per_branch() -> None:
    versions = [
        {"datasetId": "ds-1", "branch": "main", "versionId": "v1", "versionNumber": 1},
        {"datasetId": "ds-1", "branch": "main", "versionId": "v2", "versionNumber": 2},
        {"datasetId": "ds-1", "branch": "dev", "versionId": "dev1", "versionNumber": 1},
    ]

    latest = latest_versions_by_dataset(versions)

    assert latest[("ds-1", "main")]["versionId"] == "v2"
    assert latest[("ds-1", "dev")]["versionId"] == "dev1"


def test_artifact_tenant_rule_fails_closed_on_mismatch() -> None:
    with pytest.raises(ConflictDetected) as exc:
        require_artifact_tenant("tenant-a", "tenant-b")

    assert exc.value.details == {"artifact_tenant_id": "tenant-b", "request_tenant_id": "tenant-a"}
