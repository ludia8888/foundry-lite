from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine

from scripts.quality import check_audit_count_runtime as gate

NOW = "2026-06-11T00:00:00+09:00"


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'foundry-lite.db'}", future=True)
    db.create_database(engine)
    return engine


def _audit_row(
    event_id: str,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str,
    action: str,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "tenant_id": "tenant-demo",
        "actor_user_id": "user-demo",
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "action": action,
        "decision": "allow",
        "policy_decision": {},
        "before_ref": {},
        "after_ref": {},
        "correlation_id": "request-demo",
        "request_id": "request-demo",
        "metadata": {},
        "created_at": NOW,
    }


def _seed_runtime_mutations(engine: Engine, *, include_version_audit: bool = True) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.datasets).values(
                id="ds_orders",
                tenant_id="tenant-demo",
                namespace="raw",
                name="orders",
                description=None,
                storage_kind="local",
                storage_uri=None,
                owner_team=None,
                classification=None,
                status="ACTIVE",
                primary_key=["order_id"],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        conn.execute(
            insert(db.dataset_versions).values(
                id="dsv_orders_v1",
                tenant_id="tenant-demo",
                dataset_id="ds_orders",
                branch="main",
                version_number=1,
                transaction_id="tx_orders_v1",
                schema_version=1,
                manifest_uri="file://manifest",
                row_count=3,
                byte_size=100,
                status="active",
                superseded_by_version_id=None,
                created_at=NOW,
            )
        )
        conn.execute(
            insert(db.transforms).values(
                id="tf_clean_orders",
                tenant_id="tenant-demo",
                api_name="clean_orders",
                language="sql",
                entrypoint="examples/clean_orders.sql",
                mode="snapshot",
                inputs={"raw.orders": "raw.orders"},
                output_dataset_ref="clean.orders",
                checks=[],
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
                rows_read=3,
                objects_upserted=3,
                objects_deleted=0,
                links_upserted=0,
                error=None,
                started_at=NOW,
                completed_at=NOW,
                created_at=NOW,
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
                request_fingerprint="fingerprint-action-run-1",
                error=None,
                created_at=NOW,
                completed_at=NOW,
            )
        )
        audit_rows = [
            _audit_row(
                "audit_dataset",
                event_type="dataset.created",
                resource_type="dataset",
                resource_id="ds_orders",
                action="create",
            ),
            _audit_row(
                "audit_transform",
                event_type="transform.definition.created",
                resource_type="transform",
                resource_id="tf_clean_orders",
                action="register",
            ),
            _audit_row(
                "audit_ontology",
                event_type="ontology.version.activated",
                resource_type="ontology_version",
                resource_id="ont_v1",
                action="activate",
            ),
            _audit_row(
                "audit_index",
                event_type="object.index.rebuilt",
                resource_type="object_type",
                resource_id="otype_order",
                action="index_rebuild",
            ),
            _audit_row(
                "audit_action",
                event_type="action.run.committed",
                resource_type="action_run",
                resource_id="action_run_1",
                action="apply",
            ),
        ]
        if include_version_audit:
            audit_rows.append(
                _audit_row(
                    "audit_dataset_version",
                    event_type="dataset.version.committed",
                    resource_type="dataset_version",
                    resource_id="dsv_orders_v1",
                    action="csv_upload_commit",
                )
            )
        conn.execute(insert(db.audit_events), audit_rows)


def test_audit_count_runtime_gate_matches_runtime_mutations_to_audits(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_runtime_mutations(engine)

    rows = gate._load_runtime_rows(engine, "tenant-demo")
    audit_events = gate._load_audit_events(engine, "tenant-demo")
    expectations = gate.build_expectations(rows)

    assert len(expectations) == 6
    assert gate.collect_findings(expectations, audit_events) == []


def test_audit_count_runtime_gate_flags_missing_audit_row(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_runtime_mutations(engine, include_version_audit=False)

    rows = gate._load_runtime_rows(engine, "tenant-demo")
    audit_events = gate._load_audit_events(engine, "tenant-demo")
    findings = gate.collect_findings(gate.build_expectations(rows), audit_events)

    assert [finding.code for finding in findings] == [
        "audit_count_mismatch",
        "audit_expectation_mismatch",
    ]
    assert findings[1].category == "dataset.version.committed"
    assert findings[1].resource_id == "dsv_orders_v1"


def test_audit_count_runtime_gate_writes_json_report(tmp_path: Path) -> None:
    storage_root = tmp_path / "flite"
    storage_root.mkdir()
    engine = create_engine(f"sqlite:///{storage_root / 'foundry-lite.db'}", future=True)
    db.create_database(engine)
    _seed_runtime_mutations(engine)
    output = tmp_path / "quality" / "audit_count_runtime.json"

    assert gate.run_gate(storage_root=storage_root, output=output) == 0

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": true' in report
    assert '"mutation_count": 6' in report
    assert '"audit_count": 6' in report
