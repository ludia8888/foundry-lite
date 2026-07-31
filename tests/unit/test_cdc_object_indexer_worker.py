from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker import cdc_object_indexer
from foundry_lite_worker.cdc_object_indexer import (
    CdcObjectIndexerWorkerConfig,
    ContinuousCdcObjectIndexerResult,
    run_cdc_object_indexer_continuously,
    run_cdc_object_indexer_once,
)
from sqlalchemy import and_, select


def test_cdc_object_indexer_worker_once_resumes_after_last_indexed_version(tmp_path: Path) -> None:
    storage_root = tmp_path / "runtime"
    foundry = _seed_order_cdc_runtime(storage_root, tmp_path)
    _commit_cdc_version(foundry, tmp_path, "topic:0:1", 1, "APPROVED")
    config = CdcObjectIndexerWorkerConfig(
        object_type_api_name="Order",
        source_dataset_ref="raw_cdc.erp_orders",
        storage_root=storage_root,
        worker_id="cdc-worker",
    )

    first = run_cdc_object_indexer_once(config)
    _commit_cdc_version(foundry, tmp_path, "topic:0:2", 2, "SHIPPED")
    second = run_cdc_object_indexer_once(config)
    third = run_cdc_object_indexer_once(config)

    reloaded = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    order = reloaded.objects.get("Order", "O-1001", ctx=demo_admin_context())
    workflow = _workflow_row(storage_root)
    cursor = cast(dict[str, object], cast(dict[str, object], workflow["output"])["cdcObjectIndexer"])["cursor"]
    assert first is not None
    assert second is not None
    assert third is None
    assert first.source_dataset_version_number == 1
    assert second.source_dataset_version_number == 2
    assert order["properties"]["status"] == "SHIPPED"
    assert order["objectVersion"] == 2
    assert cast(dict[str, object], cursor)["lastIndexedVersionId"] == second.source_dataset_version_id
    assert _index_run_count(storage_root) == 2


def test_cdc_object_indexer_continuous_loop_indexes_until_empty_and_releases_lease(tmp_path: Path) -> None:
    storage_root = tmp_path / "continuous"
    foundry = _seed_order_cdc_runtime(storage_root, tmp_path)
    _commit_cdc_version(foundry, tmp_path, "topic:0:10", 10, "PACKED")
    _commit_cdc_version(foundry, tmp_path, "topic:0:11", 11, "DELIVERED")

    result = run_cdc_object_indexer_continuously(
        CdcObjectIndexerWorkerConfig(
            object_type_api_name="Order",
            source_dataset_ref="raw_cdc.erp_orders",
            storage_root=storage_root,
            is_continuous=True,
            continuous_max_empty_polls=1,
            worker_id="cdc-continuous",
        )
    )

    reloaded = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    workflow = _workflow_row(storage_root)
    output = cast(dict[str, object], workflow["output"])
    order = reloaded.objects.get("Order", "O-1001", ctx=demo_admin_context())
    assert result.stop_reason == "empty_polls"
    assert result.iterations == 3
    assert result.indexed_batches == 2
    assert result.events_indexed == 2
    assert result.workflow_run_id == workflow["id"]
    assert workflow["status"] == "succeeded"
    assert output["stopReason"] == "empty_polls"
    assert output["indexedBatches"] == 2
    assert order["properties"]["status"] == "DELIVERED"


def test_cdc_object_indexer_continuous_loop_stops_cleanly_on_oversized_version(tmp_path: Path) -> None:
    """An oversized committed version must stop the loop cleanly, not crash it.

    The row-limit guard raises for an oversized version; when that propagated out
    of the continuous loop the process crashed, and because the cursor never moved
    past the poison version it crash-looped on every restart. The loop now returns
    a durable ``version_oversized`` terminal result (workflow failed) instead.
    """
    storage_root = tmp_path / "continuous-oversized"
    foundry = _seed_order_cdc_runtime(storage_root, tmp_path)
    _commit_multi_row_cdc_version(foundry, tmp_path)

    result = run_cdc_object_indexer_continuously(
        CdcObjectIndexerWorkerConfig(
            object_type_api_name="Order",
            source_dataset_ref="raw_cdc.erp_orders",
            storage_root=storage_root,
            is_continuous=True,
            continuous_max_empty_polls=1,
            max_rows_per_version=1,
            worker_id="cdc-continuous-oversized",
        )
    )

    workflow = _workflow_row(storage_root)
    assert result.stop_reason == "version_oversized"
    assert workflow["status"] == "failed"


def test_cdc_object_indexer_continuous_loop_stops_when_lease_is_lost(tmp_path: Path) -> None:
    storage_root = tmp_path / "lease-lost"
    foundry = _seed_order_cdc_runtime(storage_root, tmp_path)
    _commit_cdc_version(foundry, tmp_path, "topic:0:20", 20, "PICKED")
    _commit_cdc_version(foundry, tmp_path, "topic:0:21", 21, "INVOICED")
    old_worker = CdcObjectIndexerWorkerConfig(
        object_type_api_name="Order",
        source_dataset_ref="raw_cdc.erp_orders",
        storage_root=storage_root,
        is_continuous=True,
        continuous_max_empty_polls=1,
        worker_id="old-cdc-worker",
        lease_ttl_seconds=0,
    )
    checks = 0
    takeover_done = False

    def takeover_after_first_batch() -> bool:
        nonlocal checks, takeover_done
        checks += 1
        if checks == 2 and not takeover_done:
            takeover_done = True
            run_cdc_object_indexer_continuously(
                replace(old_worker, worker_id="new-cdc-worker", request_id="req-new-cdc")
            )
        return False

    result = run_cdc_object_indexer_continuously(old_worker, should_stop=takeover_after_first_batch)

    reloaded = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    order = reloaded.objects.get("Order", "O-1001", ctx=demo_admin_context())
    workflow = _workflow_row(storage_root)
    assert result.stop_reason == "lease_lost"
    assert result.indexed_batches == 1
    assert workflow["status"] == "succeeded"
    assert cast(dict[str, object], workflow["output"])["indexedBatches"] == 1
    assert order["properties"]["status"] == "INVOICED"
    assert _index_run_count(storage_root) == 2


def test_cdc_object_indexer_worker_fails_closed_when_version_exceeds_row_limit(tmp_path: Path) -> None:
    storage_root = tmp_path / "row-limit"
    foundry = _seed_order_cdc_runtime(storage_root, tmp_path)
    _commit_multi_row_cdc_version(foundry, tmp_path)
    config = CdcObjectIndexerWorkerConfig(
        object_type_api_name="Order",
        source_dataset_ref="raw_cdc.erp_orders",
        storage_root=storage_root,
        max_rows_per_version=1,
        worker_id="cdc-row-limit",
    )

    with pytest.raises(ValidationFailed, match="max_rows_per_version"):
        run_cdc_object_indexer_once(config)

    workflow = _workflow_row(storage_root)
    assert workflow["status"] == "failed"
    assert cast(dict[str, object], workflow["output"]) == {
        "status": "NO_VERSIONS",
        "cdcObjectIndexer": {"cursor": {}},
    }
    assert cast(dict[str, object], workflow["error"])["type"] == "VALIDATION_FAILED"
    assert _index_run_count(storage_root) == 0


def test_cdc_object_indexer_worker_config_cli_and_failure_payload_edges(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    env_config = cdc_object_indexer.config_from_env(
        {
            "FOUNDRY_LITE_HOME": str(tmp_path / "home"),
            "FOUNDRY_LITE_CDC_OBJECT_TYPE": "Shipment",
            "FOUNDRY_LITE_CDC_SOURCE_DATASET": "raw_cdc.shipments",
            "FOUNDRY_LITE_CDC_INDEXER_MAX_ROWS_PER_VERSION": "42",
            "FOUNDRY_LITE_CDC_INDEXER_CONTINUOUS": "yes",
            "FOUNDRY_LITE_CDC_INDEXER_MAX_BATCHES": "3",
            "FOUNDRY_LITE_CDC_INDEXER_MAX_EMPTY_POLLS": "2",
            "FOUNDRY_LITE_CDC_INDEXER_WORKER_ID": "worker-env",
            "FOUNDRY_LITE_TENANT_ID": "tenant-env",
            "FOUNDRY_LITE_WORKER_ACTOR_USER_ID": "actor-env",
            "FOUNDRY_LITE_WORKER_REQUEST_ID": "req-env",
        }
    )
    assert env_config.object_type_api_name == "Shipment"
    assert env_config.is_continuous is True
    assert env_config.continuous_max_batches == 3

    def fake_continuous(_config: CdcObjectIndexerWorkerConfig) -> ContinuousCdcObjectIndexerResult:
        return ContinuousCdcObjectIndexerResult(1, 1, 0, 2, "dsv_cli", "idx_cli", "max_batches")

    monkeypatch.setattr(cdc_object_indexer, "run_cdc_object_indexer_continuously", fake_continuous)
    exit_code = cdc_object_indexer.main(
        [
            "--storage-root",
            str(tmp_path),
            "--object-type",
            "Order",
            "--source-dataset",
            "raw_cdc.orders",
            "--continuous",
            "--max-batches",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "STOPPED"
    assert payload["lastVersionId"] == "dsv_cli"

    def fail_once(_config: CdcObjectIndexerWorkerConfig) -> None:
        raise ValueError("bad cdc worker config")

    monkeypatch.setattr(cdc_object_indexer, "run_cdc_object_indexer_once", fail_once)
    assert cdc_object_indexer.main(["--storage-root", str(tmp_path)]) == 1
    value_payload = json.loads(capsys.readouterr().out)
    assert value_payload["type"] == "CONFIGURATION_ERROR"
    assert value_payload["details"]["adapter"] == "cdc_object_indexer_worker"
    assert cdc_object_indexer._failure_payload(ValidationFailed("boom"), None)["type"] == "VALIDATION_FAILED"


def _seed_order_cdc_runtime(storage_root: Path, tmp_path: Path) -> FoundryLite:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    ctx = demo_admin_context()
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    snapshot = tmp_path / "orders.csv"
    snapshot.write_text("order_id,status,amount\nO-1001,PENDING,700\n", encoding="utf-8")
    foundry.datasets.upload_csv("clean.orders", snapshot, ctx=ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    return foundry


def _commit_cdc_version(foundry: FoundryLite, tmp_path: Path, event_id: str, lsn: int, status: str) -> None:
    path = tmp_path / f"{event_id.replace(':', '_')}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_id", "op", "pk_json", "after_json", "ordering_json"])
        writer.writeheader()
        writer.writerow(
            {
                "event_id": event_id,
                "op": "u",
                "pk_json": json.dumps({"order_id": "O-1001"}, sort_keys=True),
                "after_json": json.dumps(
                    {"order_id": "O-1001", "status": status, "amount": 700 + lsn},
                    sort_keys=True,
                ),
                "ordering_json": json.dumps(
                    {"lsn": lsn, "offset": lsn, "source_ts_ms": 1700000000000 + lsn, "table": "orders"},
                    sort_keys=True,
                ),
            }
        )
    foundry.datasets.upload_csv("raw_cdc.erp_orders", path, ctx=demo_admin_context())


def _commit_multi_row_cdc_version(foundry: FoundryLite, tmp_path: Path) -> None:
    path = tmp_path / "cdc-multi-row.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_id", "op", "pk_json", "after_json", "ordering_json"])
        writer.writeheader()
        for lsn, status in ((30, "READY"), (31, "PACKED")):
            writer.writerow(
                {
                    "event_id": f"topic:0:{lsn}",
                    "op": "u",
                    "pk_json": json.dumps({"order_id": "O-1001"}, sort_keys=True),
                    "after_json": json.dumps(
                        {"order_id": "O-1001", "status": status, "amount": 700 + lsn},
                        sort_keys=True,
                    ),
                    "ordering_json": json.dumps(
                        {"lsn": lsn, "offset": lsn, "source_ts_ms": 1700000000000 + lsn, "table": "orders"},
                        sort_keys=True,
                    ),
                }
            )
    foundry.datasets.upload_csv("raw_cdc.erp_orders", path, ctx=demo_admin_context())


def _order_ontology(tmp_path: Path) -> Path:
    path = tmp_path / "order-cdc-worker.yaml"
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


def _workflow_row(storage_root: Path) -> dict[str, object]:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    with foundry.engine.begin() as conn:
        row = (
            cast(Any, conn)
            .execute(
                select(db.workflow_runs)
                .where(
                    and_(
                        db.workflow_runs.c.tenant_id == demo_admin_context().tenant_id,
                        db.workflow_runs.c.workflow_name == "ContinuousCdcObjectIndexerWorker",
                    )
                )
                .order_by(db.workflow_runs.c.created_at.desc())
            )
            .mappings()
            .first()
        )
    assert row is not None
    return dict(row)


def _index_run_count(storage_root: Path) -> int:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    with foundry.engine.begin() as conn:
        rows = (
            cast(Any, conn)
            .execute(
                select(db.index_runs.c.id).where(
                    and_(
                        db.index_runs.c.tenant_id == demo_admin_context().tenant_id,
                        db.index_runs.c.trigger_type == "cdc_incremental",
                    )
                )
            )
            .all()
        )
    return len(rows)
