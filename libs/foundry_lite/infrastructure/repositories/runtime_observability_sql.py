"""Observability incident row mapping and upsert value builders."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, select

import foundry_lite.infrastructure.schema as db
from foundry_lite.application.ports import (
    ObservabilityIncident,
    RuntimeJsonObject,
    StoredObservabilityIncident,
    StoredObservabilityIncidentStatus,
)


def _observability_incident_db_row(
    transaction: Any,
    *,
    tenant_id: str,
    incident_id: str,
) -> Mapping[str, Any] | None:
    row = (
        transaction.execute(
            select(db.observability_incidents).where(
                and_(
                    db.observability_incidents.c.tenant_id == tenant_id,
                    db.observability_incidents.c.id == incident_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _observability_incident_by_dedupe(
    transaction: Any,
    tenant_id: str,
    dedupe_key: str,
) -> Mapping[str, Any] | None:
    row = (
        transaction.execute(
            select(db.observability_incidents).where(
                and_(
                    db.observability_incidents.c.tenant_id == tenant_id,
                    db.observability_incidents.c.dedupe_key == dedupe_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _observability_insert_values(tenant_id: str, incident: ObservabilityIncident) -> dict[str, object]:
    observed_at = incident["lastObservedAt"]
    return {
        "id": incident["id"],
        "tenant_id": tenant_id,
        "detector_id": incident["detectorId"],
        "detector_type": incident["detectorType"],
        "config_version": incident["configVersion"],
        "status": "open",
        "severity": incident["severity"],
        "owner": incident["owner"],
        "message": incident["message"],
        "dedupe_key": incident["dedupeKey"],
        "first_observed_at": incident["firstObservedAt"],
        "last_observed_at": observed_at,
        "occurrence_count": 1,
        "evidence": dict(incident["evidence"]),
        "evidence_links": list(incident["evidenceLinks"]),
        "threshold": dict(incident["threshold"]),
        "created_at": observed_at,
        "updated_at": observed_at,
    }


def _observability_update_values(
    incident: ObservabilityIncident,
    existing: Mapping[str, Any],
) -> dict[str, object | None]:
    occurrence_increment = 0 if existing.get("last_observed_at") == incident["lastObservedAt"] else 1
    values: dict[str, object | None] = {
        "detector_id": incident["detectorId"],
        "detector_type": incident["detectorType"],
        "config_version": incident["configVersion"],
        "severity": incident["severity"],
        "owner": incident["owner"],
        "message": incident["message"],
        "last_observed_at": incident["lastObservedAt"],
        "occurrence_count": int(existing["occurrence_count"]) + occurrence_increment,
        "evidence": dict(incident["evidence"]),
        "evidence_links": list(incident["evidenceLinks"]),
        "threshold": dict(incident["threshold"]),
        "updated_at": incident["lastObservedAt"],
    }
    if existing.get("status") == "resolved":
        values.update(_observability_reopen_values())
    return values


def _observability_reopen_values() -> dict[str, object | None]:
    return {
        "status": "open",
        "acknowledged_at": None,
        "acknowledged_by": None,
        "resolved_at": None,
        "resolved_by": None,
        "resolution_reason": None,
    }


def _observability_status_values(
    status: StoredObservabilityIncidentStatus,
    actor_user_id: str,
    updated_at: str,
    resolution_reason: str | None,
) -> dict[str, object | None]:
    values: dict[str, object | None] = {"status": status, "updated_at": updated_at}
    if status == "acknowledged":
        values.update({"acknowledged_at": updated_at, "acknowledged_by": actor_user_id})
    if status == "resolved":
        values.update(
            {
                "resolved_at": updated_at,
                "resolved_by": actor_user_id,
                "resolution_reason": resolution_reason,
            }
        )
    return values


def _observability_incident_response(row: Mapping[str, Any]) -> StoredObservabilityIncident:
    return {
        "id": str(row["id"]),
        "detectorId": str(row["detector_id"]),
        "detectorType": cast(Any, row["detector_type"]),
        "configVersion": str(row["config_version"]),
        "status": cast(StoredObservabilityIncidentStatus, row["status"]),
        "severity": cast(Any, row["severity"]),
        "owner": str(row["owner"]),
        "message": str(row["message"]),
        "dedupeKey": str(row["dedupe_key"]),
        "firstObservedAt": str(row["first_observed_at"]),
        "lastObservedAt": str(row["last_observed_at"]),
        "occurrenceCount": int(row["occurrence_count"]),
        "evidence": cast(RuntimeJsonObject, row["evidence"] or {}),
        "evidenceLinks": cast(list[Any], row["evidence_links"] or []),
        "threshold": cast(RuntimeJsonObject, row["threshold"] or {}),
        "acknowledgedAt": cast(str | None, row.get("acknowledged_at")),
        "acknowledgedBy": cast(str | None, row.get("acknowledged_by")),
        "resolvedAt": cast(str | None, row.get("resolved_at")),
        "resolvedBy": cast(str | None, row.get("resolved_by")),
        "resolutionReason": cast(str | None, row.get("resolution_reason")),
    }
