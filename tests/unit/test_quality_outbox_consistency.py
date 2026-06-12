from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine

from scripts.quality import check_outbox_consistency as gate

NOW = "2026-06-11T00:00:00+09:00"


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'foundry-lite.db'}", future=True)
    db.create_database(engine)
    return engine


def _outbox_row(
    event_id: str,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    idempotency_key: str,
    correlation_id: str | None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "tenant_id": "tenant-demo",
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "payload": {},
        "status": "pending",
        "attempts": 0,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "created_at": NOW,
        "published_at": None,
    }


def _seed_outbox_state(
    engine: Engine,
    *,
    include_dataset_outbox: bool = True,
    wrong_action_correlation: bool = False,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.dataset_versions),
            [
                {
                    "id": "dsv_orders_v1",
                    "tenant_id": "tenant-demo",
                    "dataset_id": "ds_orders",
                    "branch": "main",
                    "version_number": 1,
                    "transaction_id": "tx_orders_v1",
                    "schema_version": 1,
                    "manifest_uri": "file://orders-manifest",
                    "row_count": 3,
                    "byte_size": 100,
                    "status": "active",
                    "superseded_by_version_id": None,
                    "created_at": NOW,
                },
                {
                    "id": "dsv_action_log_v1",
                    "tenant_id": "tenant-demo",
                    "dataset_id": "ds_action_log",
                    "branch": "main",
                    "version_number": 1,
                    "transaction_id": "tx_action_log_v1",
                    "schema_version": 1,
                    "manifest_uri": "file://action-log-manifest",
                    "row_count": 1,
                    "byte_size": 100,
                    "status": "active",
                    "superseded_by_version_id": None,
                    "created_at": NOW,
                },
            ],
        )
        conn.execute(
            insert(db.sync_runs).values(
                id="sync_orders",
                tenant_id="tenant-demo",
                sync_name="orders",
                source_type="csv",
                output_dataset_id="ds_orders",
                transaction_id="tx_orders_v1",
                committed_version_id="dsv_orders_v1",
                status="succeeded",
                error=None,
                created_at=NOW,
                completed_at=NOW,
            )
        )
        conn.execute(
            insert(db.materialization_runs).values(
                id="mat_action_log",
                tenant_id="tenant-demo",
                materialization_id="mat_action_log_def",
                api_name="action_log",
                status="succeeded",
                source_cursor={},
                object_store_watermark={},
                consistency_level="watermark",
                target_dataset_version_id="dsv_action_log_v1",
                row_count=1,
                error=None,
                created_at=NOW,
                completed_at=NOW,
            )
        )
        conn.execute(
            insert(db.ontology_versions).values(
                id="ont_v1",
                tenant_id="tenant-demo",
                version_number=1,
                status="active",
                created_by="user-demo",
                created_at=NOW,
                activated_at=NOW,
            )
        )
        conn.execute(
            insert(db.index_runs).values(
                id="index_order",
                tenant_id="tenant-demo",
                object_type_id="otype_order",
                object_type_api_name="Order",
                trigger_type="reindex",
                source_ref={"dataset_version_id": "dsv_orders_v1"},
                status="succeeded",
                cursor={},
                rows_read=1,
                objects_upserted=1,
                objects_deleted=0,
                links_upserted=0,
                error=None,
                started_at=NOW,
                completed_at=NOW,
                created_at=NOW,
            )
        )
        conn.execute(
            insert(db.object_records).values(
                id="obj_order_1001",
                tenant_id="tenant-demo",
                object_type_id="otype_order",
                object_type_api_name="Order",
                object_id="O-1001",
                properties={"status": "PENDING"},
                base_properties={"status": "PENDING"},
                edit_properties={},
                property_versions={"status": 1},
                source_dataset_version_id="dsv_orders_v1",
                source_hash="hash",
                object_version=1,
                deleted=False,
                deletion_reason=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        conn.execute(
            insert(db.action_runs).values(
                id="action_run_1",
                tenant_id="tenant-demo",
                action_type_id="atype_approve",
                action_type_api_name="ApproveOrder",
                actor_user_id="user-demo",
                target_object_type_id="otype_order",
                target_object_type_api_name="Order",
                target_object_id="O-1001",
                expected_object_version=1,
                parameters={"reason": "ok"},
                status="succeeded",
                idempotency_key="approve-O-1001",
                error=None,
                created_at=NOW,
                completed_at=NOW,
            )
        )
        conn.execute(
            insert(db.object_edits).values(
                id="edit_1",
                tenant_id="tenant-demo",
                action_run_id="action_run_1",
                object_type_id="otype_order",
                object_type_api_name="Order",
                object_id="O-1001",
                edit_type="set_property",
                patch={"status": "APPROVED"},
                previous_values={"status": "PENDING"},
                actor_user_id="user-demo",
                idempotency_key="approve-O-1001",
                created_at=NOW,
            )
        )
        outbox_rows = [
            _outbox_row(
                "outbox_materialization",
                event_type="materialization.completed",
                aggregate_type="dataset_version",
                aggregate_id="dsv_action_log_v1",
                idempotency_key="dsv_action_log_v1",
                correlation_id="mat_action_log",
            ),
            _outbox_row(
                "outbox_ontology",
                event_type="ontology.version.activated",
                aggregate_type="ontology_version",
                aggregate_id="ont_v1",
                idempotency_key="ont_v1",
                correlation_id="request-demo",
            ),
            _outbox_row(
                "outbox_object_index",
                event_type="object.changed",
                aggregate_type="object",
                aggregate_id="Order/O-1001",
                idempotency_key="dsv_orders_v1:Order:O-1001",
                correlation_id="dsv_orders_v1",
            ),
            _outbox_row(
                "outbox_action",
                event_type="action.run.committed",
                aggregate_type="action_run",
                aggregate_id="action_run_1",
                idempotency_key="action.run.committed:action_run_1",
                correlation_id="action_run_1",
            ),
            _outbox_row(
                "outbox_object_edit",
                event_type="object.edit.committed",
                aggregate_type="object",
                aggregate_id="O-1001",
                idempotency_key="object.edit.committed:action_run_1",
                correlation_id="wrong" if wrong_action_correlation else "action_run_1",
            ),
            _outbox_row(
                "outbox_object_action",
                event_type="object.changed",
                aggregate_type="object",
                aggregate_id="O-1001",
                idempotency_key="object.changed:action_run_1",
                correlation_id="action_run_1",
            ),
        ]
        if include_dataset_outbox:
            outbox_rows.append(
                _outbox_row(
                    "outbox_dataset_version",
                    event_type="dataset.version.committed",
                    aggregate_type="dataset_version",
                    aggregate_id="dsv_orders_v1",
                    idempotency_key="dsv_orders_v1",
                    correlation_id="sync_orders",
                )
            )
        conn.execute(insert(db.outbox_events), outbox_rows)


def test_outbox_consistency_gate_matches_state_changes_to_outbox(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_outbox_state(engine)

    rows = gate._load_runtime_rows(engine, "tenant-demo")
    expectations = gate.build_expectations(rows)
    count_expectations = gate.build_count_expectations(rows)
    outbox_events = gate._covered_outbox_events(rows)

    assert len(expectations) + sum(expectation.expected_count for expectation in count_expectations) == 7
    assert gate.collect_findings(expectations, outbox_events, count_expectations) == []


def test_outbox_consistency_gate_flags_missing_outbox_row(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_outbox_state(engine, include_dataset_outbox=False)

    rows = gate._load_runtime_rows(engine, "tenant-demo")
    findings = gate.collect_findings(
        gate.build_expectations(rows),
        gate._covered_outbox_events(rows),
        gate.build_count_expectations(rows),
    )

    assert [finding.code for finding in findings] == [
        "outbox_count_mismatch",
        "outbox_expectation_mismatch",
    ]
    assert findings[1].category == "dataset.version.committed"
    assert findings[1].aggregate_id == "dsv_orders_v1"


def test_outbox_consistency_gate_flags_correlation_gap(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_outbox_state(engine, wrong_action_correlation=True)

    rows = gate._load_runtime_rows(engine, "tenant-demo")
    findings = gate.collect_findings(
        gate.build_expectations(rows),
        gate._covered_outbox_events(rows),
        gate.build_count_expectations(rows),
    )

    assert [finding.code for finding in findings] == ["outbox_correlation_mismatch"]
    assert findings[0].category == "object.edit.committed"


def test_outbox_consistency_gate_writes_json_report(tmp_path: Path) -> None:
    storage_root = tmp_path / "flite"
    storage_root.mkdir()
    engine = create_engine(f"sqlite:///{storage_root / 'foundry-lite.db'}", future=True)
    db.create_database(engine)
    _seed_outbox_state(engine)
    output = tmp_path / "quality" / "outbox_consistency.json"

    assert gate.run_gate(storage_root=storage_root, output=output) == 0

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": true' in report
    assert '"state_change_count": 7' in report
    assert '"outbox_count": 7' in report
