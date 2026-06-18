from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, cast
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamArchiveConfig, StreamPublishRequest
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
    IcebergDatasetStorageAdapter,
    LocalStreamAdapter,
    SparkComputeAdapter,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from testcontainers.core.container import DockerContainer  # type: ignore[import-untyped]

from tests.conftest import DEMO_ROOT

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


def test_iceberg_s3_storage_with_spark_compute_end_to_end(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _make_bucket(minio_server)
    _set_iceberg_spark_env(monkeypatch, minio_server, bucket, tmp_path)
    deps = create_local_core_dependencies(
        adapter_profile="iceberg",
        storage_root=tmp_path / "runtime",
        db_url=f"sqlite:///{tmp_path / 'meta.db'}",
    )
    assert isinstance(deps.dataset_storage, IcebergDatasetStorageAdapter)
    assert isinstance(deps.compute_adapter, SparkComputeAdapter)

    foundry = FoundryLite(dependencies=deps)
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.demo.register_transforms(ctx)

    raw = foundry.datasets.upload_csv("raw.erp_orders", DEMO_ROOT / "data" / "orders.csv", ctx=ctx)
    clean = foundry.transforms.run("clean_orders", ctx=ctx)

    assert raw.manifest_uri.startswith("iceberg://")
    assert clean.manifest_uri.startswith("iceberg://")
    assert clean.row_count == 3
    preview = foundry.datasets.preview("clean.orders", ctx=ctx, version=clean.version_id)
    assert [row["order_id"] for row in preview] == ["O-1001", "O-1002", "O-1003"]
    assert _bucket_object_count(minio_server, bucket) > 0
    lineage = foundry.operations.lineage(clean.version_id, ctx=ctx)
    assert any(
        edge["from_resource_id"] == raw.version_id and edge["to_resource_id"] == clean.version_id for edge in lineage
    )


def test_iceberg_s3_spark_failure_aborts_without_output_version(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _make_bucket(minio_server)
    _set_iceberg_spark_env(monkeypatch, minio_server, bucket, tmp_path)
    deps = create_local_core_dependencies(
        adapter_profile="iceberg",
        storage_root=tmp_path / "runtime",
        db_url=f"sqlite:///{tmp_path / 'meta.db'}",
    )
    assert isinstance(deps.dataset_storage, IcebergDatasetStorageAdapter)
    assert isinstance(deps.compute_adapter, SparkComputeAdapter)

    foundry = FoundryLite(dependencies=deps)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("broken.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.erp_orders", DEMO_ROOT / "data" / "orders.csv", ctx=ctx)
    bad_sql = tmp_path / "broken_orders.sql"
    bad_sql.write_text(
        "select definitely_missing_column from {{ input('raw.erp_orders') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "broken_orders",
        entrypoint=bad_sql,
        inputs={"raw.erp_orders": "raw.erp_orders"},
        output_dataset_ref="broken.orders",
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed):
        foundry.transforms.run("broken_orders", ctx=ctx)

    assert foundry.datasets.list_versions("broken.orders", ctx=ctx) == []
    failed = [run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"]
    assert failed
    error = cast(dict[str, Any], failed[-1]["error"])
    adapter_failure = cast(dict[str, Any], error["adapterFailure"])
    trace = cast(dict[str, Any], error["trace"])
    assert adapter_failure["adapterProfile"] == "spark"
    assert trace["adapter"] == "compute_adapter.execute_transform"


def test_debezium_cdc_iceberg_s3_spark_archives_indexes_and_materializes_end_to_end(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _make_bucket(minio_server)
    _set_iceberg_spark_env(monkeypatch, minio_server, bucket, tmp_path)
    deps = create_local_core_dependencies(
        adapter_profile="iceberg",
        storage_root=tmp_path / "runtime",
        db_url=f"sqlite:///{tmp_path / 'meta.db'}",
    )
    assert isinstance(deps.dataset_storage, IcebergDatasetStorageAdapter)
    assert isinstance(deps.compute_adapter, SparkComputeAdapter)

    inner_stream = LocalStreamAdapter()
    cdc_stream = DebeziumPostgresStreamAdapter(
        inner_stream,
        DebeziumPostgresSourceConfig(primary_key=("order_id",)),
    )
    foundry = FoundryLite(dependencies=replace(deps, stream_adapter=cdc_stream))
    ctx = demo_admin_context()
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    clean = foundry.datasets.upload_csv("clean.orders", _orders_csv(tmp_path), ctx=ctx)
    foundry.ontology.apply(str(_order_cdc_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)

    _publish_debezium_order(inner_stream, "c", lsn=20, after={"order_id": "O-CDC-1001", "status": "PENDING"})
    _publish_debezium_order(
        inner_stream,
        "u",
        lsn=21,
        before={"order_id": "O-CDC-1001", "status": "PENDING"},
        after={"order_id": "O-CDC-1001", "status": "APPROVED"},
    )
    _publish_debezium_order(
        inner_stream,
        "d",
        lsn=22,
        before={"order_id": "O-CDC-1001", "status": "APPROVED"},
    )
    _publish_debezium_order(
        inner_stream,
        "u",
        lsn=21,
        before={"order_id": "O-CDC-1001", "status": "PENDING"},
        after={"order_id": "O-CDC-1001", "status": "STALE_REOPEN"},
    )

    archived = foundry.datasets.archive_stream_events("raw_cdc.erp_orders", stream=_cdc_stream_config(), ctx=ctx)
    assert archived is not None
    archived_rows = foundry.datasets.preview("raw_cdc.erp_orders", ctx=ctx, version=archived.version_id)
    archived_events = [_archived_cdc_event(row) for row in archived_rows]
    index_result = foundry.objects.index_cdc_events("Order", archived_events, ctx=ctx)
    deleted = foundry.objects.get("Order", "O-CDC-1001", ctx=ctx)
    active_ids = {item["objectId"] for item in foundry.objects.query("Order", ctx=ctx)["items"]}
    current = foundry.materialization.run("order_current", ctx=ctx)
    current_rows = _materialization_rows_for_version(foundry, ctx, current.version_id)

    assert clean.manifest_uri.startswith("iceberg://")
    assert archived.manifest_uri.startswith("iceberg://")
    assert current.manifest_uri.startswith("iceberg://")
    assert [row["op"] for row in archived_events] == ["c", "u", "d", "u"]
    assert archived_rows[0]["pk_json"] == '{"order_id":"O-CDC-1001"}'
    assert index_result["objects_upserted"] == 2
    assert index_result["objects_deleted"] == 1
    assert index_result["events_skipped"] == 1
    assert deleted.get("deleted") is True
    assert deleted.get("deletionReason") == "source_deleted"
    assert active_ids == {"O-BASE"}
    assert [row["orderId"] for row in current_rows] == ["O-BASE"]
    assert _bucket_object_count(minio_server, bucket) > 0
    sync_runs = foundry.operations.query_runs(ctx=ctx, run_type="sync")["syncRuns"]
    assert any(run["committed_version_id"] == archived.version_id for run in sync_runs)
    index_runs = foundry.operations.query_runs(ctx=ctx, run_type="index")["indexRuns"]
    assert any(
        run["id"] == index_result["index_run_id"] and run["trigger_type"] == "cdc_incremental" for run in index_runs
    )


def test_debezium_cdc_iceberg_s3_spark_archive_failure_aborts_without_dataset_version(
    minio_server: MinioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _make_bucket(minio_server)
    _set_iceberg_spark_env(monkeypatch, minio_server, bucket, tmp_path)
    deps = create_local_core_dependencies(
        adapter_profile="iceberg",
        storage_root=tmp_path / "runtime",
        db_url=f"sqlite:///{tmp_path / 'meta.db'}",
    )
    inner_stream = LocalStreamAdapter()
    cdc_stream = DebeziumPostgresStreamAdapter(
        inner_stream,
        DebeziumPostgresSourceConfig(primary_key=("order_id",)),
    )

    def fail_rows_to_parquet(rows: object, target_path: Path, fieldnames: list[str]) -> None:
        del rows, target_path, fieldnames
        raise RuntimeError("injected cdc archive parquet failure")

    monkeypatch.setattr(deps.compute_adapter, "rows_to_parquet", fail_rows_to_parquet)
    foundry = FoundryLite(dependencies=replace(deps, stream_adapter=cdc_stream))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    _publish_debezium_order(inner_stream, "c", lsn=30, after={"order_id": "O-CDC-2002", "status": "PENDING"})

    with pytest.raises(ValidationFailed, match="stream archive failed"):
        foundry.datasets.archive_stream_events("raw_cdc.erp_orders", stream=_cdc_stream_config(), ctx=ctx)

    assert foundry.datasets.list_versions("raw_cdc.erp_orders", ctx=ctx) == []
    failed = [
        run
        for run in foundry.operations.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
        if run["source_type"] == "stream.erp_orders_cdc"
    ]
    assert failed
    error = cast(dict[str, Any], failed[-1]["error"])
    trace = cast(dict[str, Any], error["trace"])
    assert error["message"] == "injected cdc archive parquet failure"
    assert trace["adapter"] == "stream_archive_writer"


def _set_iceberg_spark_env(
    monkeypatch: pytest.MonkeyPatch,
    minio_server: MinioServer,
    bucket: str,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_COMPUTE_PROFILE", "spark")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path / 'engine-catalog.db'}")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_WAREHOUSE", f"s3://{bucket}/warehouse")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_NAMESPACE", f"ns_{uuid4().hex[:8]}")
    monkeypatch.setenv("FOUNDRY_LITE_S3_ENDPOINT_URL", minio_server.endpoint_url)
    monkeypatch.setenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID", MINIO_ACCESS_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY", MINIO_SECRET_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_REGION", "us-east-1")


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


def _cdc_stream_config() -> StreamArchiveConfig:
    return StreamArchiveConfig(
        stream_name="erp_orders_cdc",
        topic="dbserver1.inventory.orders",
        schema_strategy="cdc_envelope_json",
    )


def _publish_debezium_order(
    stream: LocalStreamAdapter,
    op: str,
    *,
    lsn: int,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    row = after or before or {}
    stream.publish_event(
        StreamPublishRequest(
            stream_name="erp_orders_cdc",
            event_type="debezium.raw",
            tenant_id="tenant-demo",
            request_id=f"req-cdc-{lsn}-{op}",
            key=str(row["order_id"]),
            payload={
                "payload": {
                    "op": op,
                    "before": before,
                    "after": after,
                    "source": {"lsn": lsn, "ts_ms": 1700000000000 + lsn, "table": "orders"},
                }
            },
        )
    )


def _archived_cdc_event(row: Mapping[str, object]) -> dict[str, object]:
    event_type = str(row["event_type"])
    return {
        "event_id": row["event_id"],
        "op": event_type.removeprefix("cdc."),
        "pk_json": row["pk_json"],
        "before_json": row["before_json"],
        "after_json": row["after_json"],
        "ordering_json": row["ordering_json"],
    }


def _orders_csv(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    path.write_text("order_id,status,amount\nO-BASE,PENDING,100\n", encoding="utf-8")
    return path


def _order_cdc_ontology(tmp_path: Path) -> Path:
    path = tmp_path / "order-cdc.yaml"
    path.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
      cdc:
        dataset: raw_cdc.erp_orders
        primaryKeyColumns: [order_id]
        deletePolicy: tombstone
    properties:
      - apiName: orderId
        column: order_id
        type: string
        indexed: true
        nullable: false
      - apiName: status
        column: status
        type: string
        indexed: true
      - apiName: amount
        column: amount
        type: float
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _materialization_rows_for_version(
    foundry: FoundryLite,
    ctx: RequestContext,
    version_id: str,
) -> list[dict[str, object]]:
    run = next(
        row
        for row in foundry.operations.query_runs(ctx=ctx, run_type="materialization")["materializationRuns"]
        if row["target_dataset_version_id"] == version_id
    )
    return [dict(row) for row in foundry.materialization.replay_rows(str(run["id"]), ctx=ctx).rows]
