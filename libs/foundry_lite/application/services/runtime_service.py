from __future__ import annotations

from typing import Any, cast

from foundry_lite.application.ports import (
    AuditEventRecord,
    LineageEdgeRecord,
    OutboxEventRecord,
    RuntimeLookupTable,
    RuntimeRowsTable,
)
from foundry_lite.application.primitives import (
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    PermissionDenied,
)


class RuntimeService(CoreService):
    required_dependencies = ("engine", "policy", "runtime_repository")

    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        ctx = ctx or RequestContext()
        return self.runtime_repository.lineage_for_resource(tenant_id=ctx.tenant_id, resource_id=resource_id)

    def list_runs(self, *, ctx: RequestContext | None = None) -> dict[str, list[dict[str, Any]]]:
        ctx = ctx or RequestContext()
        return self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action=permission,
                    decision="deny",
                )
            raise

    def _select_by_id(self, conn: Any, table: RuntimeLookupTable, row_id: str) -> dict[str, Any] | None:
        return self.runtime_repository.row_by_id(transaction=conn, table=table, row_id=row_id)

    def _rows_for_tenant(self, conn: Any, table: RuntimeRowsTable, ctx: RequestContext) -> list[dict[str, Any]]:
        return self.runtime_repository.rows_for_tenant(transaction=conn, table=table, tenant_id=ctx.tenant_id)

    def _audit(
        self,
        conn: Any,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: dict[str, Any] | None = None,
        before_ref: dict[str, Any] | None = None,
        after_ref: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.runtime_repository.insert_audit_event(
            transaction=conn,
            record=AuditEventRecord(
                event_id=_new_id("audit"),
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.actor_user_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                policy_decision=policy_decision or {},
                before_ref=before_ref or {},
                after_ref=after_ref or {},
                correlation_id=correlation_id or ctx.request_id,
                request_id=ctx.request_id,
                metadata={},
                created_at=_now(),
            ),
        )

    def _outbox(
        self,
        conn: Any,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> None:
        self.runtime_repository.insert_outbox_event(
            transaction=conn,
            record=OutboxEventRecord(
                event_id=_new_id("outbox"),
                tenant_id=ctx.tenant_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                status="pending",
                attempts=0,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                created_at=_now(),
                published_at=None,
            ),
        )

    def _lineage(
        self,
        conn: Any,
        ctx: RequestContext,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        relation: str,
        run_id: str,
    ) -> None:
        self.runtime_repository.insert_lineage_edge(
            transaction=conn,
            record=LineageEdgeRecord(
                edge_id=_new_id("lineage"),
                tenant_id=ctx.tenant_id,
                from_resource_type=from_type,
                from_resource_id=from_id,
                to_resource_type=to_type,
                to_resource_id=to_id,
                relation=relation,
                created_by_run_id=run_id,
                created_at=_now(),
            ),
        )

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        if hasattr(exc, "code"):
            typed_exc = cast(Any, exc)
            return {
                "type": typed_exc.code,
                "message": str(exc),
                "details": getattr(typed_exc, "details", {}),
            }
        return {"type": exc.__class__.__name__, "message": str(exc), "details": {}}
