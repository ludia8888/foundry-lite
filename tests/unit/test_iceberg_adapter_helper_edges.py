from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
from foundry_lite.application.ports import DatasetManifestFile, DatasetStagedFile
from foundry_lite.application.ports.iceberg_maintenance import IcebergMaintenancePolicy
from foundry_lite.infrastructure.adapters import iceberg_arrow
from foundry_lite.infrastructure.adapters import iceberg_maintenance as ice_maint
from foundry_lite.infrastructure.adapters import iceberg_manifests as ice_manif


def test_iceberg_sidecar_manifest_and_pruning_helper_edges(tmp_path) -> None:
    manifest_file: DatasetManifestFile = {
        "uri": "s3://bucket/orders-a.parquet",
        "format": "parquet",
        "row_count": 2,
        "byte_size": 4,
        "content_hash": ice_manif._path_sha256(_write_bytes(tmp_path / "orders-a.parquet", b"data")),
        "partition_values": {"region": "NA"},
        "column_stats": {"amount": {"min": 10, "max": 20, "null_count": 0}},
    }
    raw_sidecar = {
        "files": [
            {
                "uri": manifest_file["uri"],
                "format": "parquet",
                "row_count": "2",
                "byte_size": "4",
                "content_hash": manifest_file["content_hash"],
                "partition_values": {"region": "NA"},
                "column_stats": {"amount": {"min": 10, "max": 20, "null_count": "0"}},
                "sort_bounds": {"amount": {"min": 10, "max": 20}},
            }
        ]
    }

    expected = ice_manif._expected_files(raw_sidecar)
    copied: DatasetManifestFile = {
        "uri": "s3://bucket/copied.parquet",
        "format": "parquet",
        "row_count": 2,
        "byte_size": 4,
        "content_hash": "hash",
    }
    ice_manif._copy_expected_manifest_metadata(copied, expected[manifest_file["uri"]])

    assert expected[manifest_file["uri"]]["column_stats"]["amount"]["null_count"] == 0
    assert copied["partition_values"] == {"region": "NA"}
    assert ice_manif._matching_expected_files(expected, None) == [expected[manifest_file["uri"]]]
    assert ice_manif._matching_expected_files(expected, {"region": "NA"}) == [expected[manifest_file["uri"]]]
    assert ice_manif._matching_expected_files(expected, {"amount": 15}) == [expected[manifest_file["uri"]]]
    assert ice_manif._matching_expected_files(expected, {"amount": 999}) == []
    assert ice_manif._file_might_match_value(
        {"uri": "x", "format": "parquet", "row_count": 1, "byte_size": 1, "content_hash": "h"}, "missing", "v"
    )
    with pytest.raises(ValueError, match="file list"):
        ice_manif._expected_files({"files": "not-a-list"})
    with pytest.raises(ValueError, match="malformed file entry"):
        ice_manif._expected_files({"files": ["bad"]})
    with pytest.raises(ValueError, match="byte size mismatch"):
        ice_manif._verify_downloaded_snapshot_file(tmp_path / "orders-a.parquet", {**manifest_file, "byte_size": 99})
    with pytest.raises(ValueError, match="content hash mismatch"):
        ice_manif._verify_downloaded_snapshot_file(
            tmp_path / "orders-a.parquet", {**manifest_file, "content_hash": "bad"}
        )


def test_iceberg_row_count_maintenance_and_schema_helper_edges(tmp_path) -> None:
    policy: IcebergMaintenancePolicy = {
        "small_file_threshold_bytes": 100,
        "file_count_threshold": 1,
        "read_amplification_threshold": 1.0,
        "retention_min_snapshots": 2,
    }
    snapshots = [
        _maintenance_snapshot(
            1, is_current=False, is_protected=False, protected_by=[], reason_codes=["orphan_snapshot"]
        ),
        _maintenance_snapshot(
            2,
            is_current=True,
            is_protected=True,
            protected_by=["current_snapshot", "retention_min_snapshot"],
            reason_codes=["too_many_files", "small_files"],
        ),
    ]
    current = SimpleNamespace(snapshot_id=2)
    plan = ice_maint._maintenance_plan(
        "iceberg", "raw.orders", "dataset-1", "main", "ns.table", policy, snapshots, current
    )
    rewrite = ice_maint._rewrite_result(1, 3, 5, 1, "before-hash", "before-hash")

    assert ice_manif._part_row_count(DatasetStagedFile(path=tmp_path / "part.parquet", row_count=None), 7) == 7
    assert ice_manif._staged_files_row_count((DatasetStagedFile(path=tmp_path / "a.parquet", row_count=2),), 7) == 2
    assert ice_manif._uploaded_row_count(DatasetStagedFile(path=tmp_path / "a.parquet", row_count=2), 2) == 2
    with pytest.raises(ValueError, match="row count does not match"):
        ice_manif._uploaded_row_count(DatasetStagedFile(path=tmp_path / "a.parquet", row_count=2), 3)
    assert plan["status"] == "maintenance_needed"
    assert plan["current_snapshot_id"] == 2
    assert plan["protected_snapshot_ids"] == [2]
    assert plan["retained_snapshot_ids"] == [2]
    assert plan["deletable_snapshot_ids"] == [1]
    assert ice_maint._current_compaction_candidate(plan)["snapshot_id"] == 2
    assert ice_maint._maintenance_run_status(ice_maint._empty_rewrite_result(None), [], "no_table") == "no_table"
    assert ice_maint._maintenance_run_status(ice_maint._empty_rewrite_result(None), [], "healthy") == "skipped"
    assert ice_maint._maintenance_run_status(rewrite, [1], "maintenance_needed") == "completed"
    assert ice_maint._maintenance_reason_codes(2, 10, None, policy) == [
        "orphan_snapshot",
        "too_many_files",
        "small_files",
        "read_amplification",
    ]
    assert ice_maint._committed_snapshot_versions(
        [{"id": "dsv-1", "manifest_uri": "iceberg://ns/table@42"}, {"id": "bad", "manifest_uri": "not-iceberg"}],
        lambda uri: (
            ("ns.table", int(uri.rsplit("@", 1)[1])) if uri.startswith("iceberg://") else (_raise_value_error())
        ),
    ) == {42: "dsv-1"}


def test_iceberg_compatible_arrow_type_edges() -> None:
    table = pa.table(
        {
            "ts": pa.array([1_700_000_000_000_000_000], type=pa.timestamp("ns")),
            "items": pa.array([["a", "b"]], type=pa.large_list(pa.string())),
            "mapped": pa.array([[("k", 1)]], type=pa.map_(pa.string(), pa.int64())),
        }
    )

    converted = iceberg_arrow._iceberg_compatible_arrow_table(table)

    assert converted.schema.field("ts").type == pa.timestamp("us")
    assert pa.types.is_large_list(converted.schema.field("items").type)
    assert pa.types.is_map(converted.schema.field("mapped").type)
    assert (
        ice_manif._snapshot_token(
            [{"uri": "a", "format": "parquet", "row_count": 1, "byte_size": 1, "content_hash": "h1"}]
        )
        == "h1"
    )
    assert (
        len(
            ice_manif._snapshot_token(
                [
                    {"uri": "a", "format": "parquet", "row_count": 1, "byte_size": 1, "content_hash": "h1"},
                    {"uri": "b", "format": "parquet", "row_count": 2, "byte_size": 2, "content_hash": "h2"},
                ]
            )
        )
        == 64
    )


def _write_bytes(path, data: bytes):
    path.write_bytes(data)
    return path


def _maintenance_snapshot(
    snapshot_id: int,
    *,
    is_current: bool,
    is_protected: bool,
    protected_by: list[str],
    reason_codes: list[str],
):
    return {
        "snapshot_id": snapshot_id,
        "version_id": None,
        "manifest_uri": f"iceberg://ns/table@{snapshot_id}",
        "file_count": 2,
        "row_count": 2,
        "total_bytes": 10,
        "average_file_size_bytes": 5,
        "read_amplification": 2.0,
        "is_current": is_current,
        "is_protected": is_protected,
        "protected_by": protected_by,
        "reason_codes": reason_codes,
    }


def _raise_value_error():
    raise ValueError("bad manifest")
