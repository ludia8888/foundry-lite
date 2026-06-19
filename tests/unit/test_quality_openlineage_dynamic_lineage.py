from __future__ import annotations

from pathlib import Path

from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine

from scripts.quality import check_openlineage_dynamic_lineage as gate


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'foundry-lite.db'}", future=True)
    db.create_database(engine)
    return engine


def _seed_transform_lineage(engine: Engine, *, include_lineage_edge: bool = True) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.datasets),
            [
                {
                    "id": "ds_raw_orders",
                    "tenant_id": "tenant-demo",
                    "namespace": "raw",
                    "name": "erp_orders",
                    "description": None,
                    "storage_kind": "local",
                    "storage_uri": None,
                    "owner_team": None,
                    "classification": None,
                    "status": "ACTIVE",
                    "primary_key": ["order_id"],
                    "partition_spec": [],
                    "sort_order": [],
                    "target_file_size_bytes": None,
                    "created_at": "2026-06-11T00:00:00+09:00",
                    "updated_at": "2026-06-11T00:00:00+09:00",
                },
                {
                    "id": "ds_clean_orders",
                    "tenant_id": "tenant-demo",
                    "namespace": "clean",
                    "name": "orders",
                    "description": None,
                    "storage_kind": "local",
                    "storage_uri": None,
                    "owner_team": None,
                    "classification": None,
                    "status": "ACTIVE",
                    "primary_key": ["order_id"],
                    "partition_spec": [],
                    "sort_order": [],
                    "target_file_size_bytes": None,
                    "created_at": "2026-06-11T00:00:00+09:00",
                    "updated_at": "2026-06-11T00:00:00+09:00",
                },
            ],
        )
        conn.execute(
            insert(db.dataset_versions),
            [
                {
                    "id": "dsv_input",
                    "tenant_id": "tenant-demo",
                    "dataset_id": "ds_raw_orders",
                    "branch": "main",
                    "version_number": 1,
                    "transaction_id": "tx_input",
                    "schema_version": 1,
                    "manifest_uri": "file://input",
                    "row_count": 3,
                    "byte_size": 128,
                    "status": "COMMITTED",
                    "superseded_by_version_id": None,
                    "created_at": "2026-06-11T00:00:00+09:00",
                },
                {
                    "id": "dsv_output",
                    "tenant_id": "tenant-demo",
                    "dataset_id": "ds_clean_orders",
                    "branch": "main",
                    "version_number": 1,
                    "transaction_id": "tx_output",
                    "schema_version": 1,
                    "manifest_uri": "file://output",
                    "row_count": 3,
                    "byte_size": 256,
                    "status": "COMMITTED",
                    "superseded_by_version_id": None,
                    "created_at": "2026-06-11T00:01:00+09:00",
                },
            ],
        )
        conn.execute(
            insert(db.transforms).values(
                id="transform_clean_orders",
                tenant_id="tenant-demo",
                api_name="clean_orders",
                language="sql",
                entrypoint="select * from input",
                mode="SNAPSHOT",
                inputs={"raw.erp_orders": "raw.erp_orders"},
                output_dataset_ref="clean.orders",
                checks=[],
            )
        )
        conn.execute(
            insert(db.transform_runs).values(
                id="transform_run_1",
                tenant_id="tenant-demo",
                transform_id="transform_clean_orders",
                status="SUCCESS",
                input_versions={"raw.erp_orders": "dsv_input"},
                output_version_id="dsv_output",
                transaction_id="tx_output",
                error=None,
                created_at="2026-06-11T00:00:30+09:00",
                completed_at="2026-06-11T00:01:00+09:00",
            )
        )
        if include_lineage_edge:
            conn.execute(
                insert(db.lineage_edges).values(
                    id="lineage_1",
                    tenant_id="tenant-demo",
                    from_resource_type="dataset_version",
                    from_resource_id="dsv_input",
                    to_resource_type="dataset_version",
                    to_resource_id="dsv_output",
                    relation="input_to",
                    created_by_run_id="transform_run_1",
                    created_at="2026-06-11T00:01:00+09:00",
                )
            )


def _collect_findings(engine: Engine) -> list[gate.Finding]:
    dataset_versions = gate._load_dataset_versions(engine, "tenant-demo")
    runs, edges, _ = gate._load_rows(engine, "tenant-demo")
    return gate.collect_findings(runs=runs, edges=edges, dataset_versions=dataset_versions)


def test_openlineage_dynamic_lineage_gate_passes_for_version_bound_transform(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_transform_lineage(engine)

    dataset_versions = gate._load_dataset_versions(engine, "tenant-demo")
    runs, edges, transform_names = gate._load_rows(engine, "tenant-demo")
    events = gate.build_openlineage_events(
        runs=runs,
        dataset_versions=dataset_versions,
        transform_names=transform_names,
        tenant_id="tenant-demo",
    )

    assert gate.collect_findings(runs=runs, edges=edges, dataset_versions=dataset_versions) == []
    assert gate.collect_event_findings(events) == []
    assert events[0]["eventType"] == "COMPLETE"
    assert events[0]["inputs"][0]["name"] == "raw.erp_orders@dsv_input"
    assert events[0]["outputs"][0]["name"] == "clean.orders@dsv_output"


def test_openlineage_dynamic_lineage_gate_flags_missing_edge(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_transform_lineage(engine, include_lineage_edge=False)

    findings = _collect_findings(engine)

    assert [finding.code for finding in findings] == ["missing_lineage_edge"]
    assert findings[0].run_id == "transform_run_1"
    assert findings[0].dataset_version_id == "dsv_input"


def test_openlineage_dynamic_lineage_gate_flags_duplicate_edge(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_transform_lineage(engine)

    with engine.begin() as conn:
        conn.execute(
            insert(db.lineage_edges).values(
                id="lineage_duplicate",
                tenant_id="tenant-demo",
                from_resource_type="dataset_version",
                from_resource_id="dsv_input",
                to_resource_type="dataset_version",
                to_resource_id="dsv_output",
                relation="input_to",
                created_by_run_id="transform_run_1",
                created_at="2026-06-11T00:01:01+09:00",
            )
        )

    findings = _collect_findings(engine)

    assert [finding.code for finding in findings] == ["duplicate_lineage_edge"]
    assert findings[0].run_id == "transform_run_1"
    assert findings[0].dataset_version_id == "dsv_input"


def test_openlineage_dynamic_lineage_gate_flags_non_version_edge(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_transform_lineage(engine)

    with engine.begin() as conn:
        conn.execute(
            insert(db.lineage_edges).values(
                id="lineage_dataset_edge",
                tenant_id="tenant-demo",
                from_resource_type="dataset",
                from_resource_id="ds_raw_orders",
                to_resource_type="dataset_version",
                to_resource_id="dsv_output",
                relation="input_to",
                created_by_run_id="transform_run_1",
                created_at="2026-06-11T00:01:01+09:00",
            )
        )

    findings = _collect_findings(engine)

    assert [finding.code for finding in findings] == ["input_to_edge_not_version_bound"]
    assert findings[0].edge_id == "lineage_dataset_edge"
    assert findings[0].run_id == "transform_run_1"


def test_openlineage_dynamic_lineage_gate_flags_failed_run_edge(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_transform_lineage(engine)

    with engine.begin() as conn:
        conn.execute(
            insert(db.transform_runs).values(
                id="transform_run_failed",
                tenant_id="tenant-demo",
                transform_id="transform_clean_orders",
                status="FAILED",
                input_versions={"raw.erp_orders": "dsv_input"},
                output_version_id=None,
                transaction_id="tx_failed",
                error={"message": "boom"},
                created_at="2026-06-11T00:02:00+09:00",
                completed_at="2026-06-11T00:02:10+09:00",
            )
        )
        conn.execute(
            insert(db.lineage_edges).values(
                id="lineage_failed",
                tenant_id="tenant-demo",
                from_resource_type="dataset_version",
                from_resource_id="dsv_input",
                to_resource_type="dataset_version",
                to_resource_id="dsv_output",
                relation="input_to",
                created_by_run_id="transform_run_failed",
                created_at="2026-06-11T00:02:10+09:00",
            )
        )

    findings = _collect_findings(engine)

    assert [finding.code for finding in findings] == [
        "lineage_run_not_successful",
        "lineage_edge_not_declared_by_transform_run",
    ]
    assert findings[0].run_id == "transform_run_failed"


def test_openlineage_dynamic_lineage_gate_writes_json_report(tmp_path: Path) -> None:
    storage_root = tmp_path / "flite"
    storage_root.mkdir()
    engine = create_engine(f"sqlite:///{storage_root / 'foundry-lite.db'}", future=True)
    db.create_database(engine)
    _seed_transform_lineage(engine)

    report = tmp_path / "quality" / "openlineage_dynamic_lineage.json"
    events = tmp_path / "quality" / "openlineage_events.json"

    assert gate.run_gate(storage_root=storage_root, output=report, events_output=events) == 0
    assert '"gate_pass": true' in report.read_text(encoding="utf-8")
    assert '"events"' in events.read_text(encoding="utf-8")
