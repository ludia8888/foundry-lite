from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
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
