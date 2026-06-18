from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from importlib import import_module
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, cast
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DatasetFileRecord
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure.adapters import (
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from testcontainers.core.container import DockerContainer  # type: ignore[import-untyped]


class _FailingFileInsertRepository:
    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def insert_file(self, *, transaction: object, record: DatasetFileRecord) -> None:
        del transaction, record
        raise RuntimeError("dataset file insert exploded after Iceberg snapshot commit")


class _UnreachableCatalog:
    """Catalog whose table creation/commit times out — simulates catalog outage."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def create_table(self, *args: Any, **kwargs: Any) -> object:
        raise TimeoutError("iceberg catalog unreachable")


class _FailOnceCatalog:
    """Times out on the first table creation, then behaves normally — an ambiguous commit."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self._failed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def create_table(self, *args: Any, **kwargs: Any) -> object:
        if not self._failed:
            self._failed = True
            raise TimeoutError("ambiguous catalog commit outcome")
        return self._wrapped.create_table(*args, **kwargs)


class _FailNextLoadCatalog:
    """Times out on the next table lookup, then behaves normally."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self._failed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def load_table(self, *args: Any, **kwargs: Any) -> object:
        if not self._failed:
            self._failed = True
            raise TimeoutError("catalog lookup timed out during duplicate guard")
        return self._wrapped.load_table(*args, **kwargs)


MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


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


def test_iceberg_adapter_normal_path_commits_loads_and_isolates_snapshots(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)

    first = _commit(adapter, _staged(adapter, tmp_path, ["O-1", "O-2"], [100, 200]), version_id="dsv_1")
    manifest = adapter.load_manifest(first.manifest_uri)
    assert manifest["version_id"] == "dsv_1"
    assert manifest["branch"] == "main"
    assert manifest["created_at"] == "2026-06-16T00:00:00Z"
    assert manifest["files"][0]["row_count"] == 2
    assert pq.read_table(adapter.first_data_file_path(first.manifest_uri)).to_pydict()["id"] == ["O-1", "O-2"]

    # A later overwrite must not change what the first version's pinned snapshot reads.
    _commit(adapter, _staged(adapter, tmp_path, ["A"], [9]), version_id="dsv_2")
    assert pq.read_table(adapter.first_data_file_path(first.manifest_uri)).to_pydict()["id"] == ["O-1", "O-2"]

    # Data really lives in the MinIO warehouse bucket.
    assert _bucket_object_count(minio_server, bucket) > 0


def test_iceberg_duplicate_version_commit_is_rejected(minio_server: MinioServer, tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, minio_server, _make_bucket(minio_server))
    _commit(adapter, _staged(adapter, tmp_path, ["O-1"], [100]), version_id="dsv_dup")

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, _staged(adapter, tmp_path, ["O-2"], [200]), version_id="dsv_dup")
    assert exc_info.value.failure.kind == "conflict"


def test_iceberg_downcasts_spark_style_nanosecond_timestamps(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, minio_server, _make_bucket(minio_server))
    staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id=f"dstx_{uuid4().hex}",
        file_name="part-00000.parquet",
    )
    expected_ts = datetime(2026, 6, 9, 10, 0, 0, 123456)
    pq.write_table(
        pa.table(
            {
                "id": pa.array(["O-1"]),
                "order_ts": pa.array([expected_ts], type=pa.timestamp("ns")),
            }
        ),
        str(staged),
    )

    stored = adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_ts_ns",
        dataset_ref="raw.orders",
        schema_hash="schema_hash_ts",
        staged_file=staged,
        row_count=1,
        created_at="2026-06-16T00:00:00Z",
    )

    materialized = pq.read_table(adapter.first_data_file_path(stored.manifest_uri))
    assert materialized.schema.field("order_ts").type == pa.timestamp("us")
    assert materialized.to_pydict()["order_ts"] == [expected_ts]


def test_iceberg_duplicate_guard_transient_catalog_error_is_retryable(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    bucket = _make_bucket(minio_server)
    healthy = _adapter(tmp_path, minio_server, bucket)
    committed = _commit(healthy, _staged(healthy, tmp_path, ["O-1"], [100]), version_id="dsv_guard")
    identifier, _ = healthy._parse_manifest_uri(committed.manifest_uri)
    before = [snapshot.snapshot_id for snapshot in healthy._load_table(identifier).snapshots()]

    flaky = IcebergDatasetStorageAdapter(healthy.config, catalog=_FailNextLoadCatalog(healthy._catalog))
    with pytest.raises(AdapterError) as exc_info:
        _commit(flaky, _staged(flaky, tmp_path, ["O-2"], [200]), version_id="dsv_guard")

    assert exc_info.value.failure.kind == "timeout"
    assert exc_info.value.failure.operation == "commit_staged_file"
    assert [snapshot.snapshot_id for snapshot in healthy._load_table(identifier).snapshots()] == before


def test_iceberg_engine_and_s3_warehouse_end_to_end(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _make_bucket(minio_server)
    _set_iceberg_env(monkeypatch, minio_server, bucket, tmp_path)

    # Composition root selects the Iceberg adapter from the profile (not injected).
    deps = create_local_core_dependencies(
        adapter_profile="iceberg",
        storage_root=tmp_path / "runtime",
        db_url=f"sqlite:///{tmp_path / 'meta.db'}",
    )
    assert isinstance(deps.dataset_storage, IcebergDatasetStorageAdapter)

    foundry = FoundryLite(dependencies=deps)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    assert committed.manifest_uri.startswith("iceberg://")
    preview = foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id)
    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100), ("O-2", 200), ("O-3", 300)]
    # The whole stack ran: core engine -> Iceberg catalog snapshot -> data files on S3/MinIO.
    assert _bucket_object_count(minio_server, bucket) > 0


def test_iceberg_snapshot_committed_db_failure_cleans_up_orphan_snapshot(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #2 split-brain: Iceberg snapshot commits, then the DB metadata insert fails.
    # The DB is the source of truth, so the orphan snapshot must be cleaned up.
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    foundry = _foundry_with_storage(tmp_path, adapter, failing_file_insert=True)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    cleanup = cast(dict[str, Any], exc_info.value.details["orphan_cleanup"])
    assert cleanup["removed"] is True
    # The orphaned version's snapshot is gone — its manifest no longer resolves.
    with pytest.raises(FileNotFoundError):
        adapter.load_manifest(str(cleanup["manifest_uri"]))
    failed_run = next(run for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    error = cast(dict[str, Any], failed_run["error"])
    details = cast(dict[str, Any], error["details"])
    orphan_cleanup = cast(dict[str, Any], details["orphan_cleanup"])
    assert orphan_cleanup["manifest_uri"] == cleanup["manifest_uri"]


def test_iceberg_missing_table_on_read_marks_storage_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #3: DB version is COMMITTED but the Iceberg table/metadata vanished.
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    identifier, _ = adapter._parse_manifest_uri(committed.manifest_uri)
    adapter._catalog.drop_table(identifier)

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)
    assert exc_info.value.details["error_type"] == "committed_version_storage_missing"


def test_iceberg_corrupted_data_file_surfaces_through_engine_as_corruption(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #7: the catalog metadata is intact but its S3 data file was tampered with.
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    data_uri = adapter.load_manifest(committed.manifest_uri)["files"][0]["uri"]
    key = data_uri.removeprefix(f"s3://{bucket}/")
    _s3_client(minio_server).put_object(Bucket=bucket, Key=key, Body=b"tampered-not-a-parquet")

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id)
    assert exc_info.value.details["error_type"] == "committed_version_storage_corrupt"


def test_iceberg_manifest_hash_is_real_object_bytes_and_catches_same_size_tamper(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    manifest = adapter.load_manifest(committed.manifest_uri)
    data_uri = manifest["files"][0]["uri"]
    key = data_uri.removeprefix(f"s3://{bucket}/")
    original = _s3_client(minio_server).get_object(Bucket=bucket, Key=key)["Body"].read()
    assert manifest["files"][0]["content_hash"] == hashlib.sha256(original).hexdigest()

    _s3_client(minio_server).put_object(Bucket=bucket, Key=key, Body=b"X" * len(original))
    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)
    assert exc_info.value.details["error_type"] == "committed_version_storage_corrupt"


def test_iceberg_catalog_failure_is_visible_in_operations(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #11: a catalog outage during commit surfaces as a retryable adapter failure
    # on a FAILED Operations run, not a silent loss.
    bucket = _make_bucket(minio_server)
    healthy = _adapter(tmp_path, minio_server, bucket)
    adapter = IcebergDatasetStorageAdapter(healthy.config, catalog=_UnreachableCatalog(healthy._catalog))
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(ValidationFailed, match="csv upload failed"):
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    failed_run = next(run for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    error = cast(dict[str, Any], failed_run["error"])
    failure = cast(dict[str, Any], error["adapterFailure"])
    assert failure["adapterProfile"] == "iceberg"
    assert failure["operation"] == "commit_staged_file"
    assert failure["retryable"] is True


def test_iceberg_corrupt_table_metadata_is_corruption_not_retryable(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #6: an unparseable table metadata.json must be diagnosed as storage corruption,
    # never as a retryable transient error (the gap found while building Batch A).
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    foundry = _foundry_with_storage(tmp_path, adapter)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    identifier, _ = adapter._parse_manifest_uri(committed.manifest_uri)
    metadata_key = adapter._load_table(identifier).metadata_location.removeprefix(f"s3://{bucket}/")
    _s3_client(minio_server).put_object(Bucket=bucket, Key=metadata_key, Body=b"{ not valid iceberg metadata ")

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)
    assert exc_info.value.details["error_type"] == "committed_version_storage_corrupt"


def test_iceberg_retry_after_ambiguous_commit_does_not_duplicate_version(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #8: a first commit whose catalog outcome is unknown (timeout) leaves no
    # committed version, a retry of the same version id succeeds exactly once,
    # and a further reuse of that version id is then rejected as a conflict.
    bucket = _make_bucket(minio_server)
    base = _adapter(tmp_path, minio_server, bucket)
    adapter = IcebergDatasetStorageAdapter(base.config, catalog=_FailOnceCatalog(base._catalog))

    with pytest.raises(AdapterError):
        _commit(adapter, _staged(adapter, tmp_path, ["O-1"], [100]), version_id="dsv_retry")

    stored = _commit(adapter, _staged(adapter, tmp_path, ["O-1"], [100]), version_id="dsv_retry")
    assert pq.read_table(adapter.first_data_file_path(stored.manifest_uri)).to_pydict()["id"] == ["O-1"]

    with pytest.raises(AdapterError) as exc_info:
        _commit(adapter, _staged(adapter, tmp_path, ["O-2"], [200]), version_id="dsv_retry")
    assert exc_info.value.failure.kind == "conflict"


def test_iceberg_compatible_schema_evolution_across_versions(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #9: a later version adds a column (compatible evolution). The new version reads
    # the new column; the earlier pinned snapshot still reads its original content.
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    first = _commit(adapter, _staged(adapter, tmp_path, ["O-1"], [100]), version_id="dsv_v1")

    staged2 = adapter.staging_file(
        tenant_id="tenant_demo", dataset_id="ds_orders", transaction_id=f"dstx_{uuid4().hex}", file_name="p.parquet"
    )
    pq.write_table(pa.table({"id": ["O-2"], "amount": [200], "note": ["urgent"]}), str(staged2))
    second = adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id="dsv_v2",
        dataset_ref="raw.orders",
        schema_hash="schema_hash_v2",
        staged_file=staged2,
        row_count=1,
        created_at="2026-06-16T00:00:00Z",
    )

    new_rows = pq.read_table(adapter.first_data_file_path(second.manifest_uri)).to_pydict()
    assert new_rows["id"] == ["O-2"] and new_rows["note"] == ["urgent"]
    old_rows = pq.read_table(adapter.first_data_file_path(first.manifest_uri)).to_pydict()
    assert old_rows["id"] == ["O-1"] and old_rows["amount"] == [100]


def test_iceberg_pinned_snapshot_survives_out_of_band_pointer_change(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #10: an out-of-band change to the table's current pointer (admin rollback,
    # compaction, expiry of the head) must not change what an already-committed
    # version serves, because reads pin an exact snapshot id, not "current".
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    first = _commit(adapter, _staged(adapter, tmp_path, ["O-1"], [100]), version_id="dsv_v1")
    second = _commit(adapter, _staged(adapter, tmp_path, ["A", "B"], [1, 2]), version_id="dsv_v2")

    identifier, first_snapshot = adapter._parse_manifest_uri(first.manifest_uri)
    adapter._load_table(identifier).manage_snapshots().rollback_to_snapshot(first_snapshot).commit()

    # Even though the table's head was rolled back to v1, v2 still serves v2.
    assert sorted(pq.read_table(adapter.first_data_file_path(second.manifest_uri)).to_pydict()["id"]) == ["A", "B"]
    assert pq.read_table(adapter.first_data_file_path(first.manifest_uri)).to_pydict()["id"] == ["O-1"]


def test_iceberg_repeated_commits_keep_every_version_independently_readable(
    minio_server: MinioServer,
    tmp_path: Path,
) -> None:
    # #4: the engine's dataset version allocator (SELECT FOR UPDATE) serializes
    # commits to one dataset, so concurrent same-table commits do not occur; Iceberg
    # optimistic concurrency is the backstop. The observable guarantee is that
    # repeated commits never clobber an earlier version's pinned content.
    bucket = _make_bucket(minio_server)
    adapter = _adapter(tmp_path, minio_server, bucket)
    committed = {
        vid: _commit(adapter, _staged(adapter, tmp_path, [vid], [n]), version_id=vid)
        for n, vid in enumerate(["dsv_a", "dsv_b", "dsv_c"], start=1)
    }
    for vid, stored in committed.items():
        assert pq.read_table(adapter.first_data_file_path(stored.manifest_uri)).to_pydict()["id"] == [vid]


def _foundry_with_storage(
    tmp_path: Path,
    adapter: IcebergDatasetStorageAdapter,
    *,
    failing_file_insert: bool = False,
) -> FoundryLite:
    dependencies = create_local_core_dependencies(
        storage_root=tmp_path / f"runtime-{uuid4().hex}",
        db_url=f"sqlite:///{tmp_path / f'meta-{uuid4().hex}.db'}",
    )
    repository: Any = dependencies.dataset_transaction_repository
    if failing_file_insert:
        repository = _FailingFileInsertRepository(dependencies.dataset_transaction_repository)
    return FoundryLite(
        dependencies=replace(dependencies, dataset_storage=adapter, dataset_transaction_repository=repository)
    )


def _adapter(tmp_path: Path, minio_server: MinioServer, bucket: str) -> IcebergDatasetStorageAdapter:
    return IcebergDatasetStorageAdapter(
        IcebergDatasetStorageAdapterConfig(
            catalog_uri=f"sqlite:///{tmp_path / f'catalog-{uuid4().hex}.db'}",
            warehouse=f"s3://{bucket}/warehouse",
            namespace=f"ns_{uuid4().hex[:8]}",
            cache_root=tmp_path / "iceberg-cache",
            s3_endpoint_url=minio_server.endpoint_url,
            s3_access_key_id=MINIO_ACCESS_KEY,
            s3_secret_access_key=MINIO_SECRET_KEY,
        )
    )


def _commit(adapter: IcebergDatasetStorageAdapter, staged: Path, version_id: str) -> Any:
    return adapter.commit_staged_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        branch="main",
        version_id=version_id,
        dataset_ref="raw.orders",
        schema_hash="schema_hash_demo",
        staged_file=staged,
        row_count=2,
        created_at="2026-06-16T00:00:00Z",
    )


def _staged(adapter: IcebergDatasetStorageAdapter, tmp_path: Path, ids: list[str], amounts: list[int]) -> Path:
    staged = adapter.staging_file(
        tenant_id="tenant_demo",
        dataset_id="ds_orders",
        transaction_id=f"dstx_{uuid4().hex}",
        file_name="part-00000.parquet",
    )
    pq.write_table(pa.table({"id": ids, "amount": amounts}), str(staged))
    return staged


def _set_iceberg_env(monkeypatch: pytest.MonkeyPatch, minio_server: MinioServer, bucket: str, tmp_path: Path) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path / 'engine-catalog.db'}")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_WAREHOUSE", f"s3://{bucket}/warehouse")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_NAMESPACE", f"ns_{uuid4().hex[:8]}")
    monkeypatch.setenv("FOUNDRY_LITE_S3_ENDPOINT_URL", minio_server.endpoint_url)
    monkeypatch.setenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID", MINIO_ACCESS_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY", MINIO_SECRET_KEY)


def _make_bucket(minio_server: MinioServer) -> str:
    bucket = f"flite-{uuid4().hex}"
    _s3_client(minio_server).create_bucket(Bucket=bucket)
    return bucket


def _bucket_object_count(minio_server: MinioServer, bucket: str) -> int:
    response = _s3_client(minio_server).list_objects_v2(Bucket=bucket)
    return len(response.get("Contents") or [])


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


def _csv_file(tmp_path: Path) -> Path:
    path = tmp_path / f"orders-{uuid4().hex}.csv"
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
