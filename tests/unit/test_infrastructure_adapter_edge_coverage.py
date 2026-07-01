from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from foundry_lite.application.ports import DatasetManifestFile, DatasetStagedFile
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactIntegrityError,
    BackupArtifactNotFoundError,
)
from foundry_lite.infrastructure.adapters import dataset_manifest_metadata as metadata
from foundry_lite.infrastructure.adapters import iceberg_dataset_storage as ice
from foundry_lite.infrastructure.adapters.iceberg_dataset_storage import (
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
)
from foundry_lite.infrastructure.adapters.local_backup_artifact_store import LocalBackupArtifactStore

from tests.contracts.test_backup_artifact_store_contract import _report


def test_dataset_manifest_metadata_helpers_preserve_safe_bounds_and_json_scalars() -> None:
    stats = {"amount": {"min": 10, "max": 20, "null_count": 0}}
    current = {"null_count": 0}

    metadata._merge_bound(current, "min", None, is_min=True)
    metadata._merge_bound(current, "min", 9, is_min=True)
    metadata._merge_bound(current, "max", 21, is_min=False)

    assert current == {"null_count": 0, "min": 9, "max": 21}
    assert metadata._sort_bounds(stats, ["missing", "amount"]) == {"amount": stats["amount"]}
    assert metadata._is_better_bound(object(), 1, is_min=True) is False
    assert metadata._json_scalar(b"abc") == "abc"
    assert metadata._json_scalar(float("inf")) is None
    assert metadata._json_scalar(Path("/tmp/value")) == "/tmp/value"


def test_local_backup_artifact_store_rejects_missing_and_malformed_artifacts(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    receipt = store.write_backup_artifact(_report("backup-edge", version_id="version-1"))
    artifact_path = Path(receipt["artifactRef"])

    with pytest.raises(BackupArtifactNotFoundError):
        store.read_backup_artifact(str(tmp_path / "missing.json"))

    artifact_path.write_text("[]", encoding="utf-8")
    with pytest.raises(BackupArtifactIntegrityError, match="artifact root"):
        store.read_backup_artifact(str(artifact_path))

    artifact_path.write_text(
        json.dumps({"receipt": {}, "manifestFormat": "wrong", "preflightReport": {}}),
        encoding="utf-8",
    )
    with pytest.raises(BackupArtifactIntegrityError, match="unsupported manifest"):
        store.read_backup_artifact(str(artifact_path))

    artifact_path.write_text("{bad-json", encoding="utf-8")
    with pytest.raises(BackupArtifactIntegrityError, match="Expecting property name"):
        store.read_backup_artifact(str(artifact_path))

    artifact_path.write_text(json.dumps({"receipt": [], "preflightReport": {}}), encoding="utf-8")
    with pytest.raises(BackupArtifactIntegrityError, match="receipt must be an object"):
        store.read_backup_artifact(str(artifact_path))

    receipt = store.write_backup_artifact(_report("backup-ref-mismatch", version_id="version-1"))
    artifact_path = Path(receipt["artifactRef"])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["receipt"]["artifactRef"] = str(tmp_path / "other.json")
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(BackupArtifactIntegrityError, match="artifact reference mismatch"):
        store.read_backup_artifact(str(artifact_path))


def test_iceberg_manifest_filter_and_schema_helpers_cover_closed_edges() -> None:
    manifest_file: DatasetManifestFile = {
        "uri": "s3://bucket/orders.parquet",
        "format": "parquet",
        "row_count": 2,
        "byte_size": 4,
        "content_hash": "hash",
        "column_stats": {"amount": {"min": 10, "max": 20, "null_count": 0}, "nullable": {"null_count": 1}},
    }
    all_null_file: DatasetManifestFile = {
        **manifest_file,
        "column_stats": {"amount": {"null_count": 2}},
    }
    fixed = pa.list_(pa.field("item", pa.timestamp("ns")), list_size=2)
    regular = pa.list_(pa.field("item", pa.timestamp("ns")))
    struct = pa.struct([pa.field("nested", pa.timestamp("ns"))])

    assert ice._is_missing_table(FileNotFoundError("missing"))
    assert ice._is_corrupt_metadata(type("ValidationError", (Exception,), {})("bad"))
    assert ice._sanitize("tenant-demo/orders") == "tenant_demo_orders"
    assert ice._file_might_match_value(manifest_file, "nullable", None)
    assert not ice._file_might_match_value(all_null_file, "amount", 10)
    assert ice._safe_less(object(), 1) is False
    assert ice._safe_greater(object(), 1) is False
    assert ice._copy_expected_manifest_metadata(dict(manifest_file), None) is None
    assert ice._current_compaction_candidate({**_plan(), "compaction_candidates": []}) is None
    assert ice._current_snapshot_id(None) is None
    assert ice._current_snapshot_id(SimpleNamespace(snapshot_id=5)) == 5
    assert ice._retained_snapshot_ids(
        [SimpleNamespace(snapshot_id=1), SimpleNamespace(snapshot_id=2), SimpleNamespace(snapshot_id=3)],
        SimpleNamespace(snapshot_id=4),
        {9: "version-9"},
        _policy(),
    ) == {2, 3, 4, 9}
    assert pa.types.is_fixed_size_list(ice._iceberg_compatible_type(pa, fixed))
    assert pa.types.is_list(ice._iceberg_compatible_type(pa, regular))
    assert pa.types.is_struct(ice._iceberg_compatible_type(pa, struct))


def test_iceberg_adapter_error_helpers_and_fake_io_paths(tmp_path: Path) -> None:
    adapter = IcebergDatasetStorageAdapter(_iceberg_config(tmp_path), catalog=_NamespaceCatalog(ValueError("exists")))
    adapter._ensure_namespace()
    with pytest.raises(RuntimeError, match="catalog down"):
        IcebergDatasetStorageAdapter(
            _iceberg_config(tmp_path), catalog=_NamespaceCatalog(RuntimeError("catalog down"))
        )._ensure_namespace()
    with pytest.raises(ValueError, match="not an Iceberg manifest"):
        adapter._parse_manifest_uri("local://manifest")
    with pytest.raises(ValueError, match="missing snapshot id"):
        adapter._parse_manifest_uri("iceberg://ns/table@bad")

    with pytest.raises(FileNotFoundError):
        adapter._load_sidecar_manifest(_TableWithInput(_Input(exists=False)), 42)
    with pytest.raises(ValueError, match="not an object"):
        adapter._load_sidecar_manifest(_TableWithInput(_Input(payload=b"[]")), 42)
    with pytest.raises(ValueError, match="sidecar manifest is corrupt"):
        adapter._load_sidecar_manifest(_TableWithInput(_Input(payload=b"{bad-json")), 42)
    with pytest.raises(FileNotFoundError):
        adapter._download_snapshot_file(
            _TableWithInput(_Input(exists=False)),
            {
                "uri": "s3://bucket/missing.parquet",
                "format": "parquet",
                "row_count": 1,
                "byte_size": 1,
                "content_hash": "h",
            },
        )
    with pytest.raises(AdapterError):
        adapter._download_snapshot_file(
            _TableWithInput(_RaisingOpenInput()),
            {
                "uri": "s3://bucket/broken.parquet",
                "format": "parquet",
                "row_count": 1,
                "byte_size": 1,
                "content_hash": "h",
            },
        )
    assert adapter._delete_table_file(_DeleteTable(FileNotFoundError("gone")), "s3://bucket/gone.parquet") is False
    with pytest.raises(AdapterError):
        adapter._delete_table_file(_DeleteTable(RuntimeError("delete down")), "s3://bucket/down.parquet")
    with pytest.raises(AdapterError):
        adapter._hash_iceberg_file(_TableWithInput(_RaisingOpenInput()), "s3://bucket/broken.parquet")


def test_iceberg_merge_and_empty_maintenance_helpers_cover_multi_file_edges(tmp_path: Path) -> None:
    adapter = IcebergDatasetStorageAdapter(_iceberg_config(tmp_path), catalog=_NamespaceCatalog(ValueError("exists")))
    first = tmp_path / "part-a.parquet"
    second = tmp_path / "part-b.parquet"
    pq.write_table(pa.table({"id": ["a"]}), first)
    pq.write_table(pa.table({"id": ["b"]}), second)

    merged = adapter._merged_staged_file(
        "version-merge",
        (
            DatasetStagedFile(path=first, row_count=1),
            DatasetStagedFile(path=second, row_count=1),
        ),
    )
    empty = ice._empty_maintenance_run("iceberg", "raw.orders", "dataset-orders", "main", "ns.table", _policy())

    assert merged.exists()
    assert empty["status"] == "no_table"
    assert empty["before_snapshot_id"] is None


def _policy():
    return {
        "small_file_threshold_bytes": 100,
        "file_count_threshold": 2,
        "read_amplification_threshold": 1.0,
        "retention_min_snapshots": 2,
    }


def _plan():
    return {
        "dataset_ref": "raw.orders",
        "dataset_id": "dataset-orders",
        "branch": "main",
        "storage_profile": "iceberg",
        "table_identifier": "ns.table",
        "current_snapshot_id": None,
        "policy": _policy(),
        "snapshots": [],
        "compaction_candidates": [],
        "orphan_snapshots": [],
        "protected_snapshot_ids": [],
        "retained_snapshot_ids": [],
        "deletable_snapshot_ids": [],
        "status": "healthy",
    }


def _iceberg_config(tmp_path: Path) -> IcebergDatasetStorageAdapterConfig:
    return IcebergDatasetStorageAdapterConfig(
        warehouse=str(tmp_path / "warehouse"),
        catalog_uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        namespace="foundry_lite",
        cache_root=str(tmp_path / "cache"),
    )


class _NamespaceCatalog:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create_namespace(self, _namespace: str) -> None:
        raise self._exc


class _Input:
    def __init__(self, *, exists: bool = True, payload: bytes = b"{}") -> None:
        self._exists = exists
        self._payload = payload

    def exists(self) -> bool:
        return self._exists

    def open(self):
        return _BytesContext(self._payload)


class _RaisingOpenInput(_Input):
    def open(self):
        raise RuntimeError("storage down")


class _TableWithInput:
    def __init__(self, input_file: _Input) -> None:
        self.io = SimpleNamespace(new_input=lambda _uri: input_file)
        self.metadata_location = "s3://bucket/table/metadata.json"

    def location(self) -> str:
        return "s3://bucket/table"


class _BytesContext:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int | None = None) -> bytes:
        return self._payload


class _DeleteTable:
    def __init__(self, exc: Exception) -> None:
        self.io = SimpleNamespace(delete=self._delete)
        self._exc = exc

    def _delete(self, _uri: str) -> None:
        raise self._exc
