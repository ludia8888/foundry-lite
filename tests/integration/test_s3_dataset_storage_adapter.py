from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import foundry_lite_api.main as api_main
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DatasetFileRecord, DatasetManifestFile
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure.adapters import S3DatasetStorageAdapter, S3DatasetStorageAdapterConfig
from foundry_lite.infrastructure.adapters.s3_dataset_storage import _verify_parquet_footer
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetTransactionRepository
from testcontainers.core.container import DockerContainer

MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


class _FailingFileInsertRepository:
    def __init__(self, wrapped: SqlAlchemyDatasetTransactionRepository) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def insert_file(self, *, transaction: object, record: DatasetFileRecord) -> None:
        del transaction, record
        raise RuntimeError("dataset file insert exploded after S3 storage promotion")


class _PartialUploadClient:
    def __init__(self, wrapped: Any, *, fail_once: bool = False) -> None:
        self._wrapped = wrapped
        self._fail_once = fail_once
        self._failed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        if not self._failed or not self._fail_once:
            self._failed = True
            body = Path(filename).read_bytes()[:5]
            self._wrapped.put_object(Bucket=bucket, Key=key, Body=body)
            raise TimeoutError("network timeout during multipart upload")
        self._wrapped.upload_file(filename, bucket, key)


class _MultipartInterruptClient:
    """Starts a real multipart upload and dies before completing it, leaving orphan parts."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        upload = self._wrapped.create_multipart_upload(Bucket=bucket, Key=key)
        self._wrapped.upload_part(
            Bucket=bucket, Key=key, PartNumber=1, UploadId=upload["UploadId"], Body=Path(filename).read_bytes()
        )
        raise TimeoutError("multipart upload interrupted before completion")


class _TransientGetClient:
    """Surfaces a transient (non-not-found) read error for every get_object."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def get_object(self, **kwargs: Any) -> object:
        del kwargs
        raise TimeoutError("read timed out talking to S3")


class _CleanupFailingClient:
    """Fails the upload, then fails every cleanup call too (storage unreachable)."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self._wrapped.put_object(Bucket=bucket, Key=key, Body=Path(filename).read_bytes()[:4])
        raise TimeoutError("upload timed out")

    def list_multipart_uploads(self, **kwargs: Any) -> object:
        del kwargs
        raise RuntimeError("cleanup cannot list multipart uploads")

    def delete_object(self, **kwargs: Any) -> object:
        del kwargs
        raise RuntimeError("cleanup cannot reach storage")


class _BlindListClient:
    """Fails the upload and reports an empty listing (list-after-write lag)."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self._wrapped.put_object(Bucket=bucket, Key=key, Body=Path(filename).read_bytes()[:4])
        raise TimeoutError("upload timed out")

    def list_objects_v2(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        return {}


class _PagedListClient:
    """In-memory client that paginates list_objects_v2 across two responses."""

    def __init__(self) -> None:
        self.continuation_tokens: list[str | None] = []

    def list_objects_v2(self, **kwargs: Any) -> dict[str, object]:
        token = kwargs.get("ContinuationToken")
        self.continuation_tokens.append(token)
        if token is None:
            return {
                "Contents": [{"Key": "p/a"}, {"Key": "p/b"}],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            }
        return {"Contents": [{"Key": "p/c"}], "IsTruncated": False}


class _TimeoutDownloadClient:
    """download_file always times out — a transient error, not a missing object."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        del bucket, key, filename
        raise TimeoutError("download timed out")


class _TransientHeadClient:
    """head_object raises a transient error (not a missing-object 404)."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def head_object(self, **kwargs: Any) -> object:
        del kwargs
        raise TimeoutError("head timed out")


class _FakeClientError(Exception):
    """botocore.ClientError-shaped error carrying an S3 error code and HTTP status."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class _AccessExpiredClient:
    """Reads fail with an expired/forbidden (403) error, not a missing-object 404."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def get_object(self, **kwargs: Any) -> object:
        del kwargs
        raise _FakeClientError("ExpiredToken", 403)


@pytest.fixture(scope="session")
def minio_server() -> Iterator[MinioServer]:
    container = (
        DockerContainer("minio/minio")
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000)
    )
    container.start()
    try:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        _wait_until(lambda: _minio_ready(endpoint))
        yield MinioServer(endpoint_url=endpoint)
    finally:
        container.stop()


def test_s3_dataset_storage_adapter_contract(minio_server: MinioServer, tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, minio_server)
    staged = _staged_bytes(adapter, b"contract parquet bytes")

    stored = adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_demo",
        dataset_ref="raw.orders",
        schema_hash="schema_hash_demo",
        staged_file=staged,
        row_count=1,
        created_at="2026-06-10T00:00:00Z",
    )

    manifest = adapter.load_manifest(stored.manifest_uri)
    assert manifest["storage_profile"] == "s3-storage"
    assert manifest["files"][0]["uri"] == stored.data_file_uri
    assert _parquet_payloads(adapter.first_data_file_path(stored.manifest_uri)) == ["contract parquet bytes"]
    assert adapter.delete_committed_version(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_demo",
    )
    assert not _object_keys(_s3_client(minio_server), adapter.config.bucket, "version=dsv_demo")


def test_s3_data_file_paths_apply_partition_filter_before_download(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, minio_server)
    client = _s3_client(minio_server)
    stored = _commit(adapter, _staged_bytes(adapter, b"partitioned bytes"), version_id="dsv_partitioned")
    manifest = adapter.load_manifest(stored.manifest_uri)
    manifest["files"][0]["partition_values"] = {"bucket": "a"}
    client.put_object(
        Bucket=adapter.config.bucket,
        Key=_key_from_uri(adapter, stored.manifest_uri),
        Body=json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
    )

    matched_paths = adapter.data_file_paths(stored.manifest_uri, partition_filter={"bucket": "a"})
    missing_paths = adapter.data_file_paths(stored.manifest_uri, partition_filter={"bucket": "b"})

    assert [_parquet_payloads(path) for path in matched_paths] == [["partitioned bytes"]]
    assert missing_paths == []


def test_s3_partial_multipart_upload_never_becomes_committed_version(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_PartialUploadClient(client))
    staged = _staged_bytes(adapter, b"fake parquet bytes for failed multipart")

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, staged, version_id="dsv_partial")

    assert exc_info.value.failure.kind == "timeout"
    assert _version_keys(client, adapter, "dsv_partial") == []


def test_s3_commit_storage_success_db_failure_creates_orphan_cleanup_evidence(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    foundry = _foundry_with_storage(tmp_path, adapter, failing_file_insert=True)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    cleanup = exc_info.value.details["orphan_cleanup"]
    assert cleanup["removed"] is True
    assert _version_keys(client, adapter, str(cleanup["version_id"])) == []
    failed_run = next(run for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    assert failed_run["error"]["details"]["orphan_cleanup"]["manifest_uri"] == cleanup["manifest_uri"]


def test_s3_committed_manifest_missing_marks_storage_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    client.delete_object(Bucket=adapter.config.bucket, Key=_key_from_uri(adapter, committed.manifest_uri))

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)

    details = exc_info.value.details
    assert details["error_type"] == "committed_version_storage_missing"
    assert details["version_id"] == committed.version_id
    assert details["manifest_uri"] == committed.manifest_uri


def test_s3_abort_cleanup_never_deletes_committed_manifest(minio_server: MinioServer, tmp_path: Path) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    manifest_key = _key_from_uri(adapter, committed.manifest_uri)

    with pytest.raises(ValidationFailed, match="dataset checks failed"):
        foundry.datasets.upload_csv("raw.orders", _duplicate_csv_file(tmp_path), ctx=ctx)

    manifest = foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]
    preview = foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id)
    assert _object_exists(client, adapter.config.bucket, manifest_key)
    assert manifest["storage_profile"] == "s3-storage"
    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100)]


def test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, minio_server)
    versions = [f"dsv_{index:02d}" for index in range(1, 5)]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda version_id: _commit(adapter, _staged_bytes(adapter), version_id), versions))

    assert [result.manifest["version_id"] for result in results] == versions
    assert sorted(result.manifest_uri.rsplit("/", 1)[0] for result in results) == sorted(
        f"s3://{adapter.config.bucket}/{adapter.config.prefix}/tenant_demo/datasets/ds_orders/branch=main/version={version_id}"
        for version_id in versions
    )


def test_s3_retry_after_storage_timeout_does_not_duplicate_version(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_PartialUploadClient(client, fail_once=True))
    staged = _staged_bytes(adapter, b"retryable parquet bytes")

    with pytest.raises(AdapterError):
        _commit(adapter, staged, version_id="dsv_retry")
    stored = _commit(adapter, staged, version_id="dsv_retry")

    assert stored.manifest["version_id"] == "dsv_retry"
    assert sorted(key.rsplit("/", 1)[-1] for key in _version_keys(client, adapter, "dsv_retry")) == [
        "manifest.json",
        "part-00000.parquet",
    ]


def test_s3_storage_failure_is_visible_in_operations(minio_server: MinioServer, tmp_path: Path) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_PartialUploadClient(client))
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(ValidationFailed, match="csv upload failed"):
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    failed_run = next(run for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    failure = failed_run["error"]["adapterFailure"]
    assert failed_run["error"]["type"] == "ADAPTER_FAILURE"
    assert failure["adapterProfile"] == "s3-storage"
    assert failure["operation"] == "commit_staged_file"
    assert failure["retryable"] is True
    assert failed_run["error"]["trace"]["run_id"] == failed_run["id"]


def test_s3_real_multipart_upload_failure_aborts_orphaned_parts(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_MultipartInterruptClient(client))
    staged = _staged_bytes(adapter, b"interrupted multipart bytes")

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, staged, version_id="dsv_mpu")

    assert exc_info.value.failure.kind == "timeout"
    prefix = f"{adapter.config.prefix}/tenant_demo/datasets/ds_orders/branch=main/version=dsv_mpu"
    pending = client.list_multipart_uploads(Bucket=adapter.config.bucket, Prefix=prefix).get("Uploads") or []
    assert pending == []
    assert _version_keys(client, adapter, "dsv_mpu") == []


def test_s3_read_path_detects_corrupted_data_object(minio_server: MinioServer, tmp_path: Path) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    stored = _commit(adapter, _staged_bytes(adapter, b"healthy parquet bytes"), version_id="dsv_corrupt")

    data_key = _key_from_uri(adapter, stored.data_file_uri)
    original = client.get_object(Bucket=adapter.config.bucket, Key=data_key)["Body"].read()
    client.put_object(Bucket=adapter.config.bucket, Key=data_key, Body=b"X" * len(original))

    with pytest.raises(ValueError, match="content hash mismatch"):
        adapter.first_data_file_path(stored.manifest_uri)


def test_s3_commit_rejects_unreadable_parquet_footer_and_cleans_up(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id=f"dstx_{uuid4().hex}",
        file_name="part-00000.parquet",
    )
    staged.write_bytes(b"not a parquet file")

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, staged, version_id="dsv_bad_footer")

    failure = exc_info.value.failure
    assert failure.kind == "validation"
    assert failure.is_retryable is False
    assert _version_keys(client, adapter, "dsv_bad_footer") == []


def test_s3_commit_rejects_parquet_row_count_mismatch_and_cleans_up(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, _staged_bytes(adapter, b"one parquet row"), version_id="dsv_bad_row_count", row_count=2)

    failure = exc_info.value.failure
    assert failure.kind == "validation"
    assert failure.is_retryable is False
    assert _version_keys(client, adapter, "dsv_bad_row_count") == []


def test_s3_parquet_footer_validation_skips_non_parquet_format(tmp_path: Path) -> None:
    payload = tmp_path / "plain.csv"
    payload.write_text("not,parquet\n", encoding="utf-8")
    manifest_file: DatasetManifestFile = {
        "uri": "s3://bucket/plain.csv",
        "format": "csv",
        "row_count": 99,
        "byte_size": payload.stat().st_size,
        "content_hash": "sha256:unused",
    }

    _verify_parquet_footer(payload, manifest_file)


def test_s3_transient_load_error_is_retryable_not_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    flaky = _adapter(tmp_path, minio_server, client=_TransientGetClient(_s3_client(minio_server)))

    with pytest.raises(AdapterError) as exc_info:
        flaky.load_manifest(f"s3://{flaky.config.bucket}/anything/manifest.json")

    assert exc_info.value.failure.kind == "timeout"
    assert exc_info.value.failure.is_retryable is True

    healthy = _adapter(tmp_path, minio_server)
    with pytest.raises(FileNotFoundError):
        healthy.load_manifest(f"s3://{healthy.config.bucket}/missing/manifest.json")


def test_s3_failed_commit_cleanup_failure_preserves_original_error_with_evidence(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_CleanupFailingClient(client))
    staged = _staged_bytes(adapter, b"orphan bytes after cleanup failure")

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, staged, version_id="dsv_cleanupfail")

    failure = exc_info.value.failure
    assert failure.kind == "timeout"
    assert failure.operation == "commit_staged_file"
    assert failure.details["orphan_cleanup"] == "incomplete"
    assert failure.details["cleanup_errors"]
    # The orphan survives because cleanup genuinely failed — it stays visible for operators.
    assert _version_keys(client, adapter, "dsv_cleanupfail") != []


def test_s3_failed_commit_cleanup_uses_known_keys_not_listing(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_BlindListClient(client))
    staged = _staged_bytes(adapter, b"orphan invisible to listing")

    with pytest.raises(AdapterError):
        _commit(adapter, staged, version_id="dsv_blindlist")

    # Even though list_objects_v2 returns nothing, the orphan is deleted by known key.
    assert _version_keys(client, adapter, "dsv_blindlist") == []


def test_s3_duplicate_version_commit_is_rejected_without_destroying_existing(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    stored = _commit(adapter, _staged_bytes(adapter, b"first writer bytes"), version_id="dsv_dup")

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, _staged_bytes(adapter, b"second writer different bytes"), version_id="dsv_dup")

    assert exc_info.value.failure.kind == "conflict"
    assert exc_info.value.failure.is_retryable is False
    # The first commit must be untouched: same manifest, same bytes.
    assert adapter.load_manifest(stored.manifest_uri)["version_id"] == "dsv_dup"
    assert _parquet_payloads(adapter.first_data_file_path(stored.manifest_uri)) == ["first writer bytes"]


def test_s3_read_path_detects_truncated_data_object(minio_server: MinioServer, tmp_path: Path) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    stored = _commit(adapter, _staged_bytes(adapter, b"healthy parquet bytes"), version_id="dsv_trunc")

    # Replace the committed object with a shorter body so the size check fires before the hash.
    client.put_object(Bucket=adapter.config.bucket, Key=_key_from_uri(adapter, stored.data_file_uri), Body=b"short")

    with pytest.raises(ValueError, match="byte size mismatch"):
        adapter.first_data_file_path(stored.manifest_uri)


def test_s3_transient_download_error_is_retryable_not_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    stored = _commit(adapter, _staged_bytes(adapter), version_id="dsv_dltransient")

    flaky = S3DatasetStorageAdapter(adapter.config, client=_TimeoutDownloadClient(client))
    with pytest.raises(AdapterError) as exc_info:
        flaky.first_data_file_path(stored.manifest_uri)

    assert exc_info.value.failure.kind == "timeout"
    assert exc_info.value.failure.is_retryable is True


def test_s3_missing_data_object_on_read_is_reported_missing(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    stored = _commit(adapter, _staged_bytes(adapter), version_id="dsv_nodata")

    client.delete_object(Bucket=adapter.config.bucket, Key=_key_from_uri(adapter, stored.data_file_uri))

    with pytest.raises(FileNotFoundError):
        adapter.first_data_file_path(stored.manifest_uri)


def test_s3_transient_head_during_commit_guard_is_retryable(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, minio_server, client=_TransientHeadClient(_s3_client(minio_server)))

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, _staged_bytes(adapter), version_id="dsv_headtransient")

    assert exc_info.value.failure.kind == "timeout"
    assert exc_info.value.failure.operation == "commit_staged_file"


def test_s3_adapter_error_during_verify_still_runs_cleanup(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_TimeoutDownloadClient(client))

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, _staged_bytes(adapter), version_id="dsv_verifyfail")

    # An AdapterError raised mid-commit (here: the verify download) is not a conflict,
    # so the uploaded objects must still be cleaned up rather than left as an orphan.
    assert exc_info.value.failure.kind == "timeout"
    assert _version_keys(client, adapter, "dsv_verifyfail") == []


def test_s3_list_keys_paginates_beyond_single_response(tmp_path: Path) -> None:
    fake = _PagedListClient()
    adapter = S3DatasetStorageAdapter(
        S3DatasetStorageAdapterConfig(
            bucket="b",
            cache_root=tmp_path / "cache",
            should_create_bucket_if_missing=False,
        ),
        client=fake,
    )

    assert adapter._list_keys("p") == ["p/a", "p/b", "p/c"]
    assert fake.continuation_tokens == [None, "page-2"]


def test_s3_composition_root_selects_adapter_from_profile_and_runs_full_cycle(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = f"flite-{uuid4().hex}"
    _set_s3_env(monkeypatch, minio_server, bucket)

    # Build dependencies through the real composition root selected by profile,
    # NOT by injecting an adapter. This exercises local_runtime's s3-storage
    # branch and _s3_storage_config (env -> S3DatasetStorageAdapterConfig).
    deps = create_local_core_dependencies(
        adapter_profile="s3-storage",
        storage_root=tmp_path / "runtime",
        db_url=f"sqlite:///{tmp_path / 'composition-root.db'}",
    )
    assert isinstance(deps.dataset_storage, S3DatasetStorageAdapter)
    assert deps.dataset_storage.config.bucket == bucket
    assert deps.dataset_storage.config.endpoint_url == minio_server.endpoint_url

    foundry = FoundryLite(dependencies=deps)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    preview = foundry.datasets.preview("raw.orders", ctx=ctx)
    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100)]
    # The bytes really live in the configured MinIO bucket, not on local disk.
    assert _object_keys(_s3_client(minio_server), bucket, "manifest.json")


def test_s3_api_end_to_end_preview_reads_through_s3_and_surfaces_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = f"flite-{uuid4().hex}"
    _set_s3_env(monkeypatch, minio_server, bucket)
    deps = create_local_core_dependencies(
        adapter_profile="s3-storage",
        storage_root=tmp_path / "api-runtime",
        db_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    assert isinstance(deps.dataset_storage, S3DatasetStorageAdapter)
    foundry = FoundryLite(dependencies=deps)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    # Drive the real FastAPI entrypoint against the S3-backed composition root.
    monkeypatch.setattr(api_main, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)}

    versions = client.get("/api/datasets/raw/orders/versions", headers=headers)
    assert versions.status_code == 200
    assert any(row["id"] == committed.version_id for row in versions.json())

    preview = client.get("/api/datasets/raw/orders/preview", headers=headers)
    assert preview.status_code == 200
    assert [(row["id"], row["amount"]) for row in preview.json()] == [("O-1", 100)]

    # Tricky: delete the committed manifest directly in MinIO. The HTTP read path
    # must surface storage corruption, not silently serve a broken version.
    manifest_key = _key_from_uri(deps.dataset_storage, committed.manifest_uri)
    _s3_client(minio_server).delete_object(Bucket=bucket, Key=manifest_key)

    corrupted = client.get("/api/datasets/raw/orders/preview", headers=headers)
    assert corrupted.status_code == 400
    assert corrupted.json()["detail"]["details"]["error_type"] == "committed_version_storage_missing"


def test_s3_real_multipart_interrupt_during_upload_is_visible_in_operations(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=_MultipartInterruptClient(client))
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(ValidationFailed, match="csv upload failed"):
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    failed_run = next(run for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    failure = failed_run["error"]["adapterFailure"]
    assert failure["adapterProfile"] == "s3-storage"
    assert failure["operation"] == "commit_staged_file"
    assert failure["retryable"] is True
    # The interrupted multipart upload leaves no orphaned parts anywhere in the bucket.
    pending = client.list_multipart_uploads(Bucket=adapter.config.bucket, Prefix=adapter.config.prefix).get("Uploads")
    assert (pending or []) == []


def test_s3_corrupted_data_object_surfaces_through_engine_as_storage_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    # Silently corrupt the committed data object (same length to hit the hash check).
    data_key = _key_from_uri(adapter, adapter.load_manifest(committed.manifest_uri)["files"][0]["uri"])
    original = client.get_object(Bucket=adapter.config.bucket, Key=data_key)["Body"].read()
    client.put_object(Bucket=adapter.config.bucket, Key=data_key, Body=b"X" * len(original))

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id)

    assert exc_info.value.details["error_type"] == "committed_version_storage_corrupt"


def test_s3_transient_read_during_inspect_surfaces_retryable_not_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    client = _s3_client(minio_server)
    adapter = _adapter(tmp_path, minio_server, client=client)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    # After a healthy commit, reads start timing out. This must surface as a retryable
    # adapter failure through the engine, not be mislabelled as storage corruption.
    adapter._client = _TransientGetClient(client)
    with pytest.raises(AdapterError) as exc_info:
        foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)

    assert exc_info.value.failure.kind == "timeout"
    assert exc_info.value.failure.is_retryable is True


def test_s3_committed_manifest_row_count_matches_actual_parquet_rows(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # A2: lock the invariant that the manifest row_count is the real parquet row
    # count, not a writer-reported number the storage layer trusts blindly.
    adapter = _adapter(tmp_path, minio_server, client=_s3_client(minio_server))
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _multi_row_csv_file(tmp_path), ctx=ctx)

    manifest_row_count = adapter.load_manifest(committed.manifest_uri)["files"][0]["row_count"]
    actual_rows = foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id, limit=100)
    assert manifest_row_count == len(actual_rows) == 3


def test_s3_staging_cleanup_is_isolated_per_transaction(minio_server: MinioServer, tmp_path: Path) -> None:
    # A6: aborting/cleaning one transaction's staging must not delete another
    # in-flight transaction's staged attempt file.
    adapter = _adapter(tmp_path, minio_server)
    keep = adapter.staging_file(
        tenant_id="tenant_demo", dataset_id="ds_orders", transaction_id="dstx_keep", file_name="part-00000.parquet"
    )
    keep.write_bytes(b"in-flight retry attempt")
    drop = adapter.staging_file(
        tenant_id="tenant_demo", dataset_id="ds_orders", transaction_id="dstx_drop", file_name="part-00000.parquet"
    )
    drop.write_bytes(b"failed attempt")

    assert adapter.delete_staging_transaction(
        tenant_id="tenant_demo", dataset_id="ds_orders", transaction_id="dstx_drop"
    )
    assert not drop.exists()
    assert keep.exists()


def test_s3_access_expiry_on_read_is_retryable_not_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # A7: a 403/expired-credential read must surface as a retryable adapter failure,
    # never be mislabelled as missing-object storage corruption. (We use boto3
    # credentials, not presigned URLs, but the 403-vs-404 boundary is the same.)
    adapter = _adapter(tmp_path, minio_server, client=_AccessExpiredClient(_s3_client(minio_server)))

    with pytest.raises(AdapterError) as exc_info:
        adapter.load_manifest(f"s3://{adapter.config.bucket}/any/manifest.json")

    assert exc_info.value.failure.is_retryable is True
    assert exc_info.value.failure.kind != "not_found"


def _set_s3_env(monkeypatch: pytest.MonkeyPatch, minio_server: MinioServer, bucket: str) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_S3_BUCKET", bucket)
    monkeypatch.setenv("FOUNDRY_LITE_S3_ENDPOINT_URL", minio_server.endpoint_url)
    monkeypatch.setenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID", MINIO_ACCESS_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY", MINIO_SECRET_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_REGION", "us-east-1")
    monkeypatch.setenv("FOUNDRY_LITE_S3_PREFIX", f"tests/{uuid4().hex}")


def _adapter(
    tmp_path: Path,
    minio_server: MinioServer,
    *,
    client: Any | None = None,
) -> S3DatasetStorageAdapter:
    bucket = f"flite-{uuid4().hex}"
    return S3DatasetStorageAdapter(
        S3DatasetStorageAdapterConfig(
            bucket=bucket,
            endpoint_url=minio_server.endpoint_url,
            access_key_id=MINIO_ACCESS_KEY,
            secret_access_key=MINIO_SECRET_KEY,
            prefix=f"tests/{uuid4().hex}",
            cache_root=tmp_path / "s3-cache",
        ),
        client=client or _s3_client(minio_server),
    )


def _s3_client(minio_server: MinioServer) -> Any:
    boto3 = import_module("boto3")
    botocore_config = import_module("botocore.config")
    return boto3.client(
        "s3",
        endpoint_url=minio_server.endpoint_url,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=botocore_config.Config(s3={"addressing_style": "path"}),
    )


def _foundry_with_storage(
    tmp_path: Path,
    adapter: S3DatasetStorageAdapter,
    *,
    failing_file_insert: bool = False,
) -> FoundryLite:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / f"runtime-{uuid4().hex}")
    repository = dependencies.dataset_transaction_repository
    if failing_file_insert:
        repository = _FailingFileInsertRepository(dependencies.dataset_transaction_repository)
    return FoundryLite(
        dependencies=replace(dependencies, dataset_storage=adapter, dataset_transaction_repository=repository)
    )


def _commit(adapter: S3DatasetStorageAdapter, staged: Path, version_id: str, *, row_count: int = 1) -> Any:
    return adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id=version_id,
        dataset_ref="raw.orders",
        schema_hash="schema_hash_demo",
        staged_file=staged,
        row_count=row_count,
        created_at="2026-06-10T00:00:00Z",
    )


def _staged_bytes(adapter: S3DatasetStorageAdapter, payload: bytes = b"fake parquet bytes") -> Path:
    staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id=f"dstx_{uuid4().hex}",
        file_name="part-00000.parquet",
    )
    pq.write_table(pa.table({"payload": [payload.decode("utf-8")]}), str(staged))
    return staged


def _parquet_payloads(path: Path) -> list[str]:
    return list(pq.read_table(path).to_pydict()["payload"])


def _version_keys(client: Any, adapter: S3DatasetStorageAdapter, version_id: str) -> list[str]:
    prefix = f"{adapter.config.prefix}/tenant_demo/datasets/ds_orders/branch=main/version={version_id}"
    return _object_keys(client, adapter.config.bucket, prefix)


def _object_keys(client: Any, bucket: str, pattern: str) -> list[str]:
    response = client.list_objects_v2(Bucket=bucket)
    objects = response.get("Contents") or []
    return sorted(str(item["Key"]) for item in objects if pattern in str(item.get("Key", "")))


def _object_exists(client: Any, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return False
    return True


def _key_from_uri(adapter: S3DatasetStorageAdapter, uri: str) -> str:
    return uri.removeprefix(f"s3://{adapter.config.bucket}/")


def _csv_file(tmp_path: Path) -> Path:
    path = tmp_path / f"orders-{uuid4().hex}.csv"
    path.write_text("id,amount\nO-1,100\n", encoding="utf-8")
    return path


def _duplicate_csv_file(tmp_path: Path) -> Path:
    path = tmp_path / f"duplicate-orders-{uuid4().hex}.csv"
    path.write_text("id,amount\nO-2,200\nO-2,201\n", encoding="utf-8")
    return path


def _multi_row_csv_file(tmp_path: Path) -> Path:
    path = tmp_path / f"multi-orders-{uuid4().hex}.csv"
    path.write_text("id,amount\nO-1,100\nO-2,200\nO-3,300\n", encoding="utf-8")
    return path


def _wait_until(condition: Callable[[], bool], *, timeout_seconds: float = 60, interval_seconds: float = 1) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if condition():
            return
        Event().wait(interval_seconds)
    raise TimeoutError("MinIO did not become ready before timeout")


def _minio_ready(endpoint_url: str) -> bool:
    try:
        with urlopen(f"{endpoint_url}/minio/health/ready", timeout=2) as response:
            response.read()
            return response.status == 200
    except (ConnectionError, TimeoutError, URLError, json.JSONDecodeError):
        return False
