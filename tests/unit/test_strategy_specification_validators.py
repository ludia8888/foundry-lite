from __future__ import annotations

import hashlib
from typing import cast

import pytest
from foundry_lite.application.ports import DatasetManifestFile, DatasetVersionRow
from foundry_lite.application.services.backup_restore_artifact_execution_helpers import validate_source_files
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.transform.scheduler import validate_transform_scheduler_max_runs


def test_validate_source_files_accepts_matching_manifest_file(tmp_path) -> None:
    path = tmp_path / "part-00000.parquet"
    data = b"row-bytes"
    path.write_bytes(data)
    source = cast(DatasetVersionRow, {"row_count": 2, "byte_size": len(data)})
    manifest_file = cast(
        DatasetManifestFile,
        {"uri": str(path), "row_count": 2, "byte_size": len(data), "content_hash": hashlib.sha256(data).hexdigest()},
    )

    validate_source_files(source, [manifest_file], [path])


def test_validate_source_files_rejects_mismatched_counts(tmp_path) -> None:
    path = tmp_path / "part-00000.parquet"
    data = b"row-bytes"
    path.write_bytes(data)
    source = cast(DatasetVersionRow, {"row_count": 3, "byte_size": len(data)})
    manifest_file = cast(
        DatasetManifestFile,
        {"uri": str(path), "row_count": 2, "byte_size": len(data), "content_hash": hashlib.sha256(data).hexdigest()},
    )

    with pytest.raises(ValueError, match="counts do not match"):
        validate_source_files(source, [manifest_file], [path])


def test_validate_transform_scheduler_max_runs_bounds() -> None:
    assert validate_transform_scheduler_max_runs(1) == 1
    assert validate_transform_scheduler_max_runs(500) == 500
    with pytest.raises(ValidationFailed, match="max_runs"):
        validate_transform_scheduler_max_runs(0)
