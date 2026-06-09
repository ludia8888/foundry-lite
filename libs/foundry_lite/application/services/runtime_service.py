from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.primitives import (
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    PermissionDenied,
)
from foundry_lite.infrastructure import schema as db


class RuntimeServiceMixin(CoreServiceMixin):
    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.lineage_edges).where(
                        and_(
                            db.lineage_edges.c.tenant_id == ctx.tenant_id,
                            (
                                (db.lineage_edges.c.from_resource_id == resource_id)
                                | (db.lineage_edges.c.to_resource_id == resource_id)
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    def list_runs(self, *, ctx: RequestContext | None = None) -> dict[str, list[dict[str, Any]]]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            return {
                "syncRuns": self._rows_for_tenant(conn, db.sync_runs, ctx),
                "transformRuns": self._rows_for_tenant(conn, db.transform_runs, ctx),
                "indexRuns": self._rows_for_tenant(conn, db.index_runs, ctx),
                "actionRuns": self._rows_for_tenant(conn, db.action_runs, ctx),
                "actionWritebacks": self._rows_for_tenant(conn, db.action_writebacks, ctx),
                "materializationRuns": self._rows_for_tenant(conn, db.materialization_runs, ctx),
                "outboxEvents": self._rows_for_tenant(conn, db.outbox_events, ctx),
                "auditEvents": self._rows_for_tenant(conn, db.audit_events, ctx),
            }

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

    def _select_by_id(self, conn: Connection, table: Any, row_id: str) -> dict[str, Any] | None:
        row = conn.execute(select(table).where(table.c.id == row_id)).mappings().first()
        return dict(row) if row else None

    def _rows_for_tenant(self, conn: Connection, table: Any, ctx: RequestContext) -> list[dict[str, Any]]:
        return [
            dict(row) for row in conn.execute(select(table).where(table.c.tenant_id == ctx.tenant_id)).mappings().all()
        ]

    def _audit(
        self,
        conn: Connection,
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
        conn.execute(
            insert(db.audit_events).values(
                id=_new_id("audit"),
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
            )
        )

    def _outbox(
        self,
        conn: Connection,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> None:
        try:
            conn.execute(
                insert(db.outbox_events).values(
                    id=_new_id("outbox"),
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
                )
            )
        except IntegrityError:
            return

    def _lineage(
        self,
        conn: Connection,
        ctx: RequestContext,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        relation: str,
        run_id: str,
    ) -> None:
        conn.execute(
            insert(db.lineage_edges).values(
                id=_new_id("lineage"),
                tenant_id=ctx.tenant_id,
                from_resource_type=from_type,
                from_resource_id=from_id,
                to_resource_type=to_type,
                to_resource_id=to_id,
                relation=relation,
                created_by_run_id=run_id,
                created_at=_now(),
            )
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
