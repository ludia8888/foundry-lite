"""Application service helpers for runtime observability workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.application.ports import (
    ObservabilityDetectorConfig,
    ObservabilityReport,
    ObservabilityStoredReport,
    RuntimeJsonObject,
    RuntimeRepository,
    StoredObservabilityIncident,
    StoredObservabilityIncidentStatus,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.observability_detectors import build_observability_report
from foundry_lite.application.services.runtime_error_payloads import redact_sensitive
from foundry_lite.application.services.runtime_run_paging import OPERATIONS_RUN_MAX_LIMIT
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
from foundry_lite.security.policy import PolicyService


class _RuntimeAudit(Protocol):
    def __call__(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


class _RuntimeOutbox(Protocol):
    def __call__(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None: ...


class _RequireOrAudit(Protocol):
    def __call__(self, ctx: RequestContext, permission: str, resource_type: str, resource_id: str) -> None: ...


class RuntimeObservabilityMixin:
    engine: TransactionManager
    policy: PolicyService
    runtime_repository: RuntimeRepository
    _audit: _RuntimeAudit
    _outbox: _RuntimeOutbox
    _require_or_audit: _RequireOrAudit

    def observability_report(
        self,
        *,
        ctx: RequestContext | None = None,
        configs: Sequence[ObservabilityDetectorConfig] = (),
        previous_incidents: Sequence[Mapping[str, object]] = (),
        observed_at: str | None = None,
    ) -> ObservabilityReport:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:summary")
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id, limit=OPERATIONS_RUN_MAX_LIMIT)
        report = build_observability_report(
            snapshot,
            configs=configs,
            previous_incidents=previous_incidents,
            observed_at=observed_at or _now(),
        )
        return cast(ObservabilityReport, redact_sensitive(report, self.policy.sensitive_column_names(ctx)))

    def record_observability_report(
        self,
        *,
        ctx: RequestContext | None = None,
        configs: Sequence[ObservabilityDetectorConfig] = (),
        observed_at: str | None = None,
    ) -> ObservabilityStoredReport:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "operations:retry", "observability", "detect-and-record")
        observed = observed_at or _now()
        previous = self.runtime_repository.list_observability_incidents(
            tenant_id=ctx.tenant_id,
            status=None,
            limit=OPERATIONS_RUN_MAX_LIMIT,
        )
        active_previous = [incident for incident in previous if incident["status"] != "resolved"]
        report = self._observability_report_payload(ctx, configs, active_previous, observed)
        stored = self._record_active_observability_incidents(ctx, report)
        result = {**report, "storedIncidents": stored, "summary": {**report["summary"], "stored": len(stored)}}
        return cast(ObservabilityStoredReport, redact_sensitive(result, self.policy.sensitive_column_names(ctx)))

    def list_observability_incidents(
        self,
        *,
        ctx: RequestContext | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[StoredObservabilityIncident]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:summary")
        parsed_status = _stored_observability_status(status)
        rows = self.runtime_repository.list_observability_incidents(
            tenant_id=ctx.tenant_id,
            status=parsed_status,
            limit=min(max(limit, 1), OPERATIONS_RUN_MAX_LIMIT),
        )
        return cast(list[StoredObservabilityIncident], redact_sensitive(rows, self.policy.sensitive_column_names(ctx)))

    def acknowledge_observability_incident(
        self,
        incident_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> StoredObservabilityIncident:
        return self._transition_observability_incident(incident_id, "acknowledged", ctx=ctx)

    def resolve_observability_incident(
        self,
        incident_id: str,
        *,
        ctx: RequestContext | None = None,
        reason: str | None = None,
    ) -> StoredObservabilityIncident:
        return self._transition_observability_incident(incident_id, "resolved", ctx=ctx, reason=reason)

    def _observability_report_payload(
        self,
        ctx: RequestContext,
        configs: Sequence[ObservabilityDetectorConfig],
        previous: Sequence[Mapping[str, object]],
        observed_at: str,
    ) -> ObservabilityReport:
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id, limit=OPERATIONS_RUN_MAX_LIMIT)
        report = build_observability_report(
            snapshot,
            configs=configs,
            previous_incidents=previous,
            observed_at=observed_at,
        )
        return cast(ObservabilityReport, redact_sensitive(report, self.policy.sensitive_column_names(ctx)))

    def _record_active_observability_incidents(
        self,
        ctx: RequestContext,
        report: ObservabilityReport,
    ) -> list[StoredObservabilityIncident]:
        stored: list[StoredObservabilityIncident] = []
        with self.engine.begin() as conn:
            for incident in report["activeIncidents"]:
                row = self.runtime_repository.upsert_observability_incident(
                    transaction=conn,
                    tenant_id=ctx.tenant_id,
                    incident=incident,
                )
                stored.append(row)
                self._write_observability_incident_event(conn, ctx, row, "observability.incident.detected")
        return stored

    def _transition_observability_incident(
        self,
        incident_id: str,
        status: StoredObservabilityIncidentStatus,
        *,
        ctx: RequestContext | None,
        reason: str | None = None,
    ) -> StoredObservabilityIncident:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "operations:retry", "observability_incident", incident_id)
        with self.engine.begin() as conn:
            before = self._observability_incident_or_raise(conn, ctx, incident_id)
            if before["status"] == status:
                return before
            _validate_observability_transition(before, status)
            updated = self.runtime_repository.update_observability_incident_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                incident_id=incident_id,
                status=status,
                actor_user_id=ctx.actor_user_id,
                updated_at=_now(),
                resolution_reason=reason,
            )
            if updated is None:
                raise ConflictDetected(
                    "observability incident changed concurrently",
                    details={"incident_id": incident_id},
                )
            self._write_observability_incident_event(conn, ctx, updated, f"observability.incident.{status}")
            return updated

    def _observability_incident_or_raise(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        incident_id: str,
    ) -> StoredObservabilityIncident:
        row = self.runtime_repository.observability_incident_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            incident_id=incident_id,
        )
        if row is None:
            raise NotFound("observability incident not found", details={"incident_id": incident_id})
        return row

    def _write_observability_incident_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        incident: StoredObservabilityIncident,
        event_type: str,
    ) -> None:
        payload = _observability_incident_event_payload(incident)
        self._audit(
            conn,
            ctx,
            event_type=event_type,
            resource_type="observability_incident",
            resource_id=incident["id"],
            action="operations:retry",
            after_ref=payload,
        )
        self._outbox(
            conn,
            ctx,
            event_type,
            "observability_incident",
            incident["id"],
            payload,
            idempotency_key=f"{event_type}:{incident['id']}:{incident['lastObservedAt']}:{incident['status']}",
            correlation_id=ctx.request_id,
        )


def _stored_observability_status(status: str | None) -> StoredObservabilityIncidentStatus | None:
    if status is None:
        return None
    if status not in {"open", "acknowledged", "resolved"}:
        raise ValidationFailed("unsupported observability incident status", details={"status": status})
    return cast(StoredObservabilityIncidentStatus, status)


def _validate_observability_transition(
    incident: StoredObservabilityIncident,
    status: StoredObservabilityIncidentStatus,
) -> None:
    if incident["status"] == "resolved" and status == "acknowledged":
        raise ConflictDetected(
            "resolved observability incident cannot be acknowledged",
            details={"incident_id": incident["id"]},
        )


def _observability_incident_event_payload(incident: StoredObservabilityIncident) -> RuntimeJsonObject:
    return {
        "incidentId": incident["id"],
        "detectorId": incident["detectorId"],
        "detectorType": incident["detectorType"],
        "configVersion": incident["configVersion"],
        "status": incident["status"],
        "severity": incident["severity"],
        "owner": incident["owner"],
        "dedupeKey": incident["dedupeKey"],
        "firstObservedAt": incident["firstObservedAt"],
        "lastObservedAt": incident["lastObservedAt"],
        "occurrenceCount": incident["occurrenceCount"],
        "evidence": incident["evidence"],
        "evidenceLinks": incident["evidenceLinks"],
        "threshold": incident["threshold"],
    }
