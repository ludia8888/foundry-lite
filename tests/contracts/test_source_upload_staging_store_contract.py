from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.source_upload_staging_store import SourceUploadStageRequest
from foundry_lite.infrastructure.adapters.local_source_upload_staging_store import LocalSourceUploadStagingStore


def test_local_source_upload_staging_round_trip_and_cleanup(tmp_path) -> None:
    store = LocalSourceUploadStagingStore(tmp_path)
    content = b"order_id,amount\nO-1,10\n"

    artifact = store.stage_uploads([SourceUploadStageRequest("orders_csv", "nested/orders.csv", BytesIO(content))])[0]

    assert artifact.file_name == "orders.csv"
    assert artifact.content_hash == f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert artifact.byte_size == len(content)
    with store.materialize_path(artifact.storage_uri) as path:
        assert path.read_bytes() == content
    with store.open_upload(artifact.storage_uri) as source:
        assert source.read() == content

    store.cleanup_uploads([artifact.storage_uri])
    assert not (tmp_path / "source-uploads" / "orders_csv").exists()


def test_local_source_upload_staging_rejects_path_escape(tmp_path) -> None:
    store = LocalSourceUploadStagingStore(tmp_path)

    with pytest.raises(AdapterError) as raised:
        store.stage_uploads([SourceUploadStageRequest("../escape", "orders.csv", BytesIO(b"id\n1\n"))])

    assert raised.value.failure.kind == "validation"
    assert not (tmp_path.parent / "escape").exists()


def test_local_source_upload_batch_failure_rolls_back_completed_files(tmp_path) -> None:
    store = LocalSourceUploadStagingStore(tmp_path)

    with pytest.raises(AdapterError) as raised:
        store.stage_uploads(
            [
                SourceUploadStageRequest("batch", "first.csv", BytesIO(b"id\n1\n")),
                SourceUploadStageRequest("batch", "second.csv", _BrokenUpload()),
            ]
        )

    assert raised.value.failure.kind == "unavailable"
    assert list((tmp_path / "source-uploads").rglob("*")) == []


def test_local_source_upload_cleanup_preserves_concurrent_sibling(tmp_path) -> None:
    store = LocalSourceUploadStagingStore(tmp_path)
    first = store.stage_uploads([SourceUploadStageRequest("shared", "first.csv", BytesIO(b"id\n1\n"))])[0]
    second = store.stage_uploads([SourceUploadStageRequest("shared", "second.csv", BytesIO(b"id\n2\n"))])[0]

    store.cleanup_uploads([first.storage_uri])

    with store.open_upload(second.storage_uri) as source:
        assert source.read() == b"id\n2\n"
    store.cleanup_uploads([second.storage_uri])


def test_local_source_upload_missing_and_outside_reads_have_stable_taxonomy(tmp_path) -> None:
    store = LocalSourceUploadStagingStore(tmp_path)
    missing = tmp_path / "source-uploads" / "source" / "missing.csv"

    with pytest.raises(AdapterError) as missing_error:
        store.open_upload(str(missing))
    with pytest.raises(AdapterError) as outside_error:
        store.open_upload(str(tmp_path / "outside.csv"))

    assert missing_error.value.failure.kind == "not_found"
    assert outside_error.value.failure.kind == "validation"
    assert {mode.operation for mode in store.failure_contract().modes} == {
        "stage_uploads",
        "read_upload",
        "cleanup_uploads",
    }


class _BrokenUpload:
    def read(self, _size: int) -> bytes:
        raise OSError("simulated upload failure")
