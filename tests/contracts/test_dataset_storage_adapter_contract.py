from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from foundry_lite.application.ports import DatasetManifestFile
from foundry_lite.infrastructure.adapters import FakeDatasetStorageAdapter, LocalDatasetStorageAdapter

StorageFactory = Callable[[Path], LocalDatasetStorageAdapter]


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda root: LocalDatasetStorageAdapter(root), id="local"),
        pytest.param(lambda root: FakeDatasetStorageAdapter(root), id="fake-storage"),
    ],
)
def test_dataset_storage_adapter_contract(factory: StorageFactory, tmp_path: Path) -> None:
    adapter = factory(tmp_path / "object-storage")

    staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id="dstx_demo",
        file_name="part-00000.parquet",
    )
    staged.write_bytes(b"fake parquet bytes")
    failed_staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id="dstx_failed",
        file_name="part-00000.parquet",
    )
    failed_staged.write_bytes(b"partial parquet bytes")

    stored = adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_demo",
        dataset_ref="raw.orders",
        schema_hash="schema_hash_demo",
        staged_file=staged,
        row_count=3,
        created_at="2026-06-10T00:00:00Z",
    )

    manifest = adapter.load_manifest(stored.manifest_uri)
    assert manifest["dataset"] == "raw.orders"
    assert manifest["storage_profile"] == adapter.profile_name
    assert manifest["files"][0]["uri"] == stored.data_file_uri
    assert manifest["files"][0]["row_count"] == 3
    assert adapter.first_data_file_path(stored.manifest_uri).read_bytes() == b"fake parquet bytes"
    assert adapter.delete_staging_transaction(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id="dstx_failed",
    )
    assert not failed_staged.exists()
    assert not adapter.delete_staging_transaction(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id="dstx_failed",
    )
    assert adapter.delete_committed_version(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_demo",
    )
    assert not stored.data_file_path.exists()
    assert not adapter.delete_committed_version(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_demo",
    )


def test_fake_storage_adapter_uses_logical_non_file_uris(tmp_path: Path) -> None:
    adapter = FakeDatasetStorageAdapter(tmp_path / "object-storage")
    staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id="dstx_demo",
        file_name="part-00000.parquet",
    )
    staged.write_bytes(b"fake parquet bytes")

    stored = adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_demo",
        dataset_ref="raw.orders",
        schema_hash="schema_hash_demo",
        staged_file=staged,
        row_count=3,
        created_at="2026-06-10T00:00:00Z",
    )

    assert stored.manifest_uri.startswith("fake-storage://")
    assert stored.data_file_uri.startswith("fake-storage://")


def test_local_storage_adapter_rejects_manifest_without_files(tmp_path: Path) -> None:
    adapter = LocalDatasetStorageAdapter(tmp_path / "object-storage")
    manifest = {
        "version_id": "dsv_empty",
        "dataset": "raw.orders",
        "branch": "main",
        "schema_hash": "schema_hash_demo",
        "files": [],
        "created_at": "2026-06-10T00:00:00Z",
        "storage_profile": adapter.profile_name,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="no files"):
        adapter.first_data_file_path(str(manifest_path))


def test_local_storage_commit_verifies_manifest_file_reference(tmp_path: Path) -> None:
    adapter = LocalDatasetStorageAdapter(tmp_path / "object-storage")
    manifest_path, data_path, manifest_file = _manifest_fixture(tmp_path, file_uri=str(tmp_path / "wrong.parquet"))

    with pytest.raises(ValueError, match="does not reference"):
        adapter._verify_committed_manifest(manifest_path, data_path, manifest_file)


def test_local_storage_commit_verifies_manifest_byte_size(tmp_path: Path) -> None:
    adapter = LocalDatasetStorageAdapter(tmp_path / "object-storage")
    manifest_path, data_path, manifest_file = _manifest_fixture(tmp_path)
    manifest_file["byte_size"] = 999

    with pytest.raises(ValueError, match="byte size"):
        adapter._verify_committed_manifest(manifest_path, data_path, manifest_file)


def test_local_storage_commit_verifies_manifest_content_hash(tmp_path: Path) -> None:
    adapter = LocalDatasetStorageAdapter(tmp_path / "object-storage")
    manifest_path, data_path, manifest_file = _manifest_fixture(tmp_path)
    manifest_file["content_hash"] = "not-the-file-hash"

    with pytest.raises(ValueError, match="content hash"):
        adapter._verify_committed_manifest(manifest_path, data_path, manifest_file)


def _manifest_fixture(
    tmp_path: Path,
    *,
    file_uri: str | None = None,
) -> tuple[Path, Path, DatasetManifestFile]:
    data_path = tmp_path / "part-00000.parquet"
    data_path.write_bytes(b"fake parquet bytes")
    manifest_file = _manifest_file(data_path, file_uri=file_uri)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"files": [manifest_file]}), encoding="utf-8")
    return manifest_path, data_path, manifest_file


def _manifest_file(data_path: Path, *, file_uri: str | None = None) -> DatasetManifestFile:
    payload = data_path.read_bytes()
    return {
        "uri": file_uri or str(data_path),
        "format": "parquet",
        "row_count": 1,
        "byte_size": len(payload),
        "content_hash": hashlib.sha256(payload).hexdigest(),
    }
