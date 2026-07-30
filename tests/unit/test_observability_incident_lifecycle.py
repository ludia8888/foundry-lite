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


def test_observability_incident_status_update_is_compare_and_set(tmp_path) -> None:
    """A stale-read status change must lose to a concurrent commit, not clobber it.

    ``update_observability_incident_status`` gates the acknowledge/resolve
    lifecycle. When it was a plain UPDATE keyed only on (tenant_id, id), a writer
    that read the incident as ``open`` before a concurrent resolve committed would
    still overwrite it — e.g. acknowledge landing on an already-resolved incident,
    leaving ``status='acknowledged'`` with ``resolved_by`` still populated. The
    UPDATE must instead guard on the allowed source statuses so the losing writer
    matches zero rows and the caller's ``ConflictDetected`` branch can fire.
    """
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    _insert_old_sync_run(dependencies.engine)

    recorded = foundry.operations.record_observability_report(
        ctx=ctx,
        configs=[_flow_config()],
        observed_at="2026-06-19T00:15:00Z",
    )
    incident_id = recorded["storedIncidents"][0]["id"]

    resolved = foundry.operations.resolve_observability_incident(incident_id, ctx=ctx, reason="recovered")
    assert resolved["status"] == "resolved"

    # Simulate the losing writer of the race: a stale acknowledge on the now-resolved
    # incident. The CAS guard must reject it (return None) rather than clobber the row.
    with dependencies.engine.begin() as conn:
        clobbered = dependencies.runtime_repository.update_observability_incident_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            incident_id=incident_id,
            status="acknowledged",
            actor_user_id="racing-operator",
            updated_at="2026-06-19T00:20:00Z",
            resolution_reason=None,
        )
    assert clobbered is None

    with dependencies.engine.connect() as conn:
        row = conn.execute(
            select(
                db.observability_incidents.c.status,
                db.observability_incidents.c.acknowledged_by,
            ).where(db.observability_incidents.c.id == incident_id)
        ).first()
    assert row is not None
    assert row[0] == "resolved"
    assert row[1] is None
