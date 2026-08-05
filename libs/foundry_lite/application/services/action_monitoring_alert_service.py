"""Durable external delivery for active Action runtime monitoring alerts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.action_runtime_monitoring import (
    ACTION_MONITORING_RUN_LIMIT,
    action_monitoring_alert_payload,
    action_monitoring_bucket,
    action_monitoring_window,
    action_runtime_monitoring_payload,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.tenant_context import tenant_context


class ActionMonitoringAlertService(CoreService):
    """Evaluate alerts and enqueue one external event per policy and UTC hour."""

    required_dependencies = (
        "engine",
        "policy",
        "metadata_repository",
        "action_repository",
        "action_execution_repository",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: RuntimeEvidenceBoundary

    def publish(self, *, ctx: RequestContext, observed_at: datetime | None = None) -> dict[str, object]:
        """Persist active alerts to the durable outbox without duplicate hourly delivery."""
        self.policy.require(ctx, "action:log:read")
        started_at, ended_at = action_monitoring_window(observed_at)
        bucket = action_monitoring_bucket(observed_at)
        with self.engine.begin() as transaction:
            active = self._active_alerts(transaction, ctx, started_at, ended_at)
            event_ids = self._enqueue_active(transaction, ctx, active, started_at, ended_at, bucket)
        return {"active": len(active), "published": len(event_ids), "eventIds": event_ids, "bucket": bucket}

    def publish_all(self, *, worker_id: str) -> dict[str, int]:
        """Evaluate every known tenant for the Action control worker."""
        totals = {"tenants": 0, "active": 0, "published": 0}
        for tenant_id in self.metadata_repository.list_tenant_ids():
            with tenant_context(tenant_id):
                result = self.publish(ctx=_worker_context(worker_id, tenant_id))
            totals["tenants"] += 1
            totals["active"] += cast(int, result["active"])
            totals["published"] += cast(int, result["published"])
        return totals

    def _active_alerts(
        self, transaction: TransactionContext, ctx: RequestContext, started_at: str, ended_at: str
    ) -> list[Mapping[str, object]]:
        rows = self.action_repository.action_runs_for_monitoring(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            created_at_from=started_at,
            limit=ACTION_MONITORING_RUN_LIMIT + 1,
        )
        counts = self.action_execution_repository.effect_status_counts(transaction=transaction, tenant_id=ctx.tenant_id)
        monitoring = action_runtime_monitoring_payload(rows, counts, started_at=started_at, ended_at=ended_at)
        alert_payload = cast(Mapping[str, object], monitoring["alerts"])
        return list(cast(list[Mapping[str, object]], alert_payload["active"]))

    def _enqueue_active(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        active: list[Mapping[str, object]],
        started_at: str,
        ended_at: str,
        bucket: str,
    ) -> list[str]:
        return [
            event_id
            for alert in active
            if (event_id := self._enqueue_one(transaction, ctx, alert, started_at, ended_at, bucket)) is not None
        ]

    def _enqueue_one(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        alert: Mapping[str, object],
        started_at: str,
        ended_at: str,
        bucket: str,
    ) -> str | None:
        payload = action_monitoring_alert_payload(alert, started_at=started_at, ended_at=ended_at, bucket=bucket)
        policy_id = str(alert["policyId"])
        event_id = self.runtime_service._outbox(
            transaction,
            ctx,
            "action.monitoring.alert.triggered",
            "action_monitoring_policy",
            policy_id,
            payload,
            idempotency_key=f"action.monitoring.alert:{policy_id}:{bucket}",
            correlation_id=ctx.request_id,
        )
        if event_id is not None:
            self._audit_queued(transaction, ctx, policy_id, payload)
        return event_id

    def _audit_queued(
        self, transaction: TransactionContext, ctx: RequestContext, policy_id: str, payload: Mapping[str, object]
    ) -> None:
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="action.monitoring.alert.queued",
            resource_type="action_monitoring_policy",
            resource_id=policy_id,
            action="monitor",
            after_ref=payload,
        )


def _worker_context(worker_id: str, tenant_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        actor_user_id=worker_id,
        request_id=f"action-monitoring:{worker_id}:{tenant_id}",
        roles=("admin",),
    )
