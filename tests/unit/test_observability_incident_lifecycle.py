from __future__ import annotations

from collections import Counter

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ObservabilityDetectorConfig
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import insert, select


def test_observability_recorded_incident_lifecycle_reopens_after_resolution(tmp_path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    config = _flow_config()
    _insert_old_sync_run(dependencies.engine)

    recorded = foundry.operations.record_observability_report(
        ctx=ctx,
        configs=[config],
        observed_at="2026-06-19T00:15:00Z",
    )
    incident_id = recorded["storedIncidents"][0]["id"]
    duplicate = foundry.operations.record_observability_report(
        ctx=ctx,
        configs=[config],
        observed_at="2026-06-19T00:15:00Z",
    )
    acknowledged = foundry.operations.acknowledge_observability_incident(incident_id, ctx=ctx)
    resolved = foundry.operations.resolve_observability_incident(incident_id, ctx=ctx, reason="source recovered")
    reopened = foundry.operations.record_observability_report(
        ctx=ctx,
        configs=[config],
        observed_at="2026-06-19T00:30:00Z",
    )

    assert recorded["summary"]["stored"] == 1
    assert duplicate["storedIncidents"] == []
    assert acknowledged["status"] == "acknowledged"
    assert resolved["status"] == "resolved"
    assert reopened["storedIncidents"][0]["status"] == "open"
    assert reopened["storedIncidents"][0]["occurrenceCount"] == 2
    assert foundry.operations.list_observability_incidents(ctx=ctx, status="open")[0]["id"] == incident_id
    assert Counter(_runtime_event_types(dependencies.engine)) == Counter(
        {
            "observability.incident.detected": 2,
            "observability.incident.acknowledged": 1,
            "observability.incident.resolved": 1,
        }
    )


def _flow_config() -> ObservabilityDetectorConfig:
    return {
        "detectorId": "raw-orders-flow",
        "detectorType": "flow_interruption",
        "configVersion": "s56-v2",
        "owner": "data-platform",
        "severity": "warning",
        "runType": "sync",
        "resourceType": "dataset",
        "resourceId": "raw.orders",
        "expectedCadenceSeconds": 300,
        "cooldownSeconds": 600,
    }


def _insert_old_sync_run(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.sync_runs).values(
                id="sync_raw_orders_old",
                tenant_id="tenant-demo",
                sync_name="raw.orders",
                source_type="rest",
                output_dataset_id="raw.orders",
                transaction_id="tx_raw_orders",
                committed_version_id="raw.orders:v1",
                status="SUCCEEDED",
                error=None,
                created_at="2026-06-19T00:00:00Z",
                completed_at="2026-06-19T00:00:00Z",
            )
        )


def _runtime_event_types(engine) -> list[str]:
    with engine.begin() as conn:
        rows = (
            conn.execute(
                select(db.audit_events.c.event_type)
                .where(db.audit_events.c.resource_type == "observability_incident")
                .order_by(db.audit_events.c.created_at, db.audit_events.c.id)
            )
            .scalars()
            .all()
        )
    return list(rows)
