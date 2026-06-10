from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.engine import Connection

from foundry_lite.application.primitives import (
    CommitResult,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    InvariantViolation,
    NotFound,
)
from foundry_lite.infrastructure import schema as db


class MaterializationServiceMixin(CoreServiceMixin):
    def materialize(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "materialization:run", "materialization", api_name)
        materialization = self._ensure_materialization(api_name, ctx=ctx)
        target_dataset = self.get_dataset(materialization["target_ref"]["dataset"], ctx=ctx)
        with self.engine.begin() as conn:
            tx_id = self._open_dataset_transaction(conn, ctx, target_dataset, "SNAPSHOT")
            run_id = _new_id("mat_run")
            watermark = self._materialization_watermark(conn, materialization)
            conn.execute(
                insert(db.materialization_runs).values(
                    id=run_id,
                    tenant_id=ctx.tenant_id,
                    materialization_id=materialization["id"],
                    api_name=api_name,
                    status="running",
                    source_cursor=watermark,
                    object_store_watermark=watermark,
                    consistency_level="watermark",
                    target_dataset_version_id=None,
                    row_count=None,
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                )
            )
        rows, fieldnames = self._materialization_rows(materialization, ctx=ctx)
        staged = self._staging_file(target_dataset, tx_id, "part-00000.parquet")
        self._rows_to_parquet(rows, staged, fieldnames)
        try:
            with self.engine.begin() as conn:
                result = self._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=target_dataset,
                    transaction_id=tx_id,
                    staged_parquet=staged,
                    run_id=run_id,
                    audit_action="materialization_commit",
                    outbox_event_type="materialization.completed",
                )
                conn.execute(
                    update(db.materialization_runs)
                    .where(db.materialization_runs.c.id == run_id)
                    .values(
                        status="succeeded",
                        target_dataset_version_id=result.version_id,
                        row_count=result.row_count,
                        completed_at=_now(),
                    )
                )
                self._lineage(
                    conn,
                    ctx,
                    "materialization",
                    materialization["id"],
                    "dataset_version",
                    result.version_id,
                    "materializes_to",
                    run_id,
                )
                return result
        except Exception as exc:
            self._abort_transaction_after_error(ctx, tx_id, run_id, exc, "materialization")
            raise

    def _ensure_materialization(
        self,
        api_name: str,
        *,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        target_by_name = {
            "action_log": {
                "type": "action_log",
                "source": {"type": "action_runs"},
                "target": {"dataset": "ops.action_log"},
            },
            "order_current": {
                "type": "object_snapshot",
                "source": {"objectType": "Order"},
                "target": {"dataset": "ops.order_current"},
            },
        }
        if api_name not in target_by_name:
            raise NotFound("materialization not found", details={"api_name": api_name})
        spec = target_by_name[api_name]
        with self.engine.begin() as conn:
            existing = (
                conn.execute(
                    select(db.materializations).where(
                        and_(
                            db.materializations.c.tenant_id == ctx.tenant_id,
                            db.materializations.c.api_name == api_name,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return dict(existing)
            materialization_id = _new_id("mat")
            conn.execute(
                insert(db.materializations).values(
                    id=materialization_id,
                    tenant_id=ctx.tenant_id,
                    api_name=api_name,
                    materialization_type=spec["type"],
                    source_ref=spec["source"],
                    target_ref=spec["target"],
                    trigger_config={"type": "manual"},
                    enabled=True,
                )
            )
            row = self._select_by_id(conn, db.materializations, materialization_id)
            if row is None:
                raise InvariantViolation("materialization insert failed")
            return row

    def _materialization_watermark(
        self,
        conn: Connection,
        materialization: dict[str, Any],
    ) -> dict[str, Any]:
        if materialization["materialization_type"] == "action_log":
            max_created = conn.execute(select(func.max(db.action_runs.c.created_at))).scalar()
            return {"action_run_created_at_lte": max_created}
        max_updated = conn.execute(select(func.max(db.object_records.c.updated_at))).scalar()
        return {"object_updated_at_lte": max_updated}

    def _materialization_rows(
        self,
        materialization: dict[str, Any],
        *,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        with self.engine.begin() as conn:
            if materialization["materialization_type"] == "action_log":
                return self._action_log_materialization_rows(conn, ctx)
            return self._order_current_materialization_rows(conn, ctx)

    def _action_log_materialization_rows(
        self,
        conn: Connection,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        action_rows = self._rows_for_tenant(conn, db.action_runs, ctx)
        edit_rows = self._rows_for_tenant(conn, db.object_edits, ctx)
        edit_by_action = {row["action_run_id"]: row for row in edit_rows}
        rows = [self._action_log_row(action, edit_by_action.get(action["id"], {})) for action in action_rows]
        return rows, [
            "action_run_id",
            "actor_user_id",
            "action_type",
            "target_object_type",
            "target_object_id",
            "status",
            "parameters_json",
            "edit_patch_json",
            "created_at",
        ]

    def _action_log_row(self, action: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
        return {
            "action_run_id": action["id"],
            "actor_user_id": action["actor_user_id"],
            "action_type": action["action_type_api_name"],
            "target_object_type": action["target_object_type_api_name"],
            "target_object_id": action["target_object_id"],
            "status": action["status"],
            "parameters_json": json.dumps(action["parameters"], sort_keys=True),
            "edit_patch_json": json.dumps(edit.get("patch", {}), sort_keys=True),
            "created_at": action["created_at"],
        }

    def _order_current_materialization_rows(
        self,
        conn: Connection,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records = [
            row
            for row in self._rows_for_tenant(conn, db.object_records, ctx)
            if row["object_type_api_name"] == "Order" and not row["deleted"]
        ]
        rows = [self._order_current_row(record) for record in records]
        return rows, [
            "orderId",
            "customerId",
            "status",
            "operatorNote",
            "amount",
            "margin",
            "riskScore",
            "objectVersion",
            "updatedAt",
        ]

    def _order_current_row(self, record: dict[str, Any]) -> dict[str, Any]:
        props = record["properties"]
        return {
            "orderId": props.get("orderId"),
            "customerId": props.get("customerId"),
            "status": props.get("status"),
            "operatorNote": props.get("operatorNote"),
            "amount": props.get("amount"),
            "margin": props.get("margin"),
            "riskScore": props.get("riskScore"),
            "objectVersion": record["object_version"],
            "updatedAt": record["updated_at"],
        }
