from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, RuntimeRow, TransactionContext
from foundry_lite.application.ports.materialization_repository import (
    MaterializationRecord,
    MaterializationRow,
    MaterializationRunRecord,
    MaterializationSourceRef,
    MaterializationTargetRef,
)
from foundry_lite.application.primitives import (
    CommitResult,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.materialization_protocols import (
    MaterializationDatasetIngest,
    MaterializationDatasetRegistry,
    MaterializationDatasetTransactions,
    MaterializationRuntimeBoundary,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    InvariantViolation,
    NotFound,
)


@dataclass(frozen=True)
class MaterializationRunPlan:
    api_name: str
    materialization_id: str
    materialization_type: str
    target_dataset: DatasetRow
    transaction_id: str
    run_id: str
    watermark: Mapping[str, object]


@dataclass(frozen=True)
class MaterializationSpec:
    """Built-in materialization definition used by the local MVP runtime."""

    materialization_type: str
    source: MaterializationSourceRef
    target: MaterializationTargetRef


MATERIALIZATION_SPECS: dict[str, MaterializationSpec] = {
    "action_log": MaterializationSpec(
        materialization_type="action_log",
        source={"type": "action_runs"},
        target={"dataset": "ops.action_log"},
    ),
    "order_current": MaterializationSpec(
        materialization_type="object_snapshot",
        source={"objectType": "Order"},
        target={"dataset": "ops.order_current"},
    ),
}


class MaterializationService(CoreService):
    required_dependencies = ("engine", "materialization_repository")
    required_collaborators = (
        "dataset_ingest_service",
        "dataset_registry_service",
        "dataset_transaction_service",
        "runtime_service",
    )
    dataset_ingest_service: MaterializationDatasetIngest
    dataset_registry_service: MaterializationDatasetRegistry
    dataset_transaction_service: MaterializationDatasetTransactions
    runtime_service: MaterializationRuntimeBoundary

    def materialize(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "materialization:run", "materialization", api_name)
        materialization = self._ensure_materialization(api_name, ctx=ctx)
        target_dataset = self.dataset_registry_service.get_dataset(materialization["target_ref"]["dataset"], ctx=ctx)
        run_id = _new_id("mat_run")
        with self.engine.begin() as conn:
            tx_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, target_dataset, "SNAPSHOT")
            plan = MaterializationRunPlan(
                api_name=api_name,
                materialization_id=materialization["id"],
                materialization_type=materialization["materialization_type"],
                target_dataset=target_dataset,
                transaction_id=tx_id,
                run_id=run_id,
                watermark=self._materialization_watermark(conn, materialization["materialization_type"]),
            )
            self._insert_materialization_run(conn, ctx, plan)
        try:
            return self._complete_materialization_run(ctx, plan)
        except Exception as exc:
            self._abort_materialization_run(ctx, plan, exc)
            raise

    def _insert_materialization_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
    ) -> None:
        self.materialization_repository.insert_materialization_run(
            transaction=conn,
            record=MaterializationRunRecord(
                materialization_run_id=plan.run_id,
                tenant_id=ctx.tenant_id,
                materialization_id=plan.materialization_id,
                api_name=plan.api_name,
                status="running",
                source_cursor=dict(plan.watermark),
                object_store_watermark=dict(plan.watermark),
                consistency_level="watermark",
                target_dataset_version_id=None,
                row_count=None,
                error=None,
                created_at=_now(),
                completed_at=None,
            ),
        )

    def _complete_materialization_run(
        self,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
    ) -> CommitResult:
        staged = self._write_materialization_rows(ctx, plan)
        with self.engine.begin() as conn:
            result = self.dataset_transaction_service._finalize_open_transaction(
                conn,
                ctx,
                dataset=plan.target_dataset,
                transaction_id=plan.transaction_id,
                staged_parquet=staged,
                run_id=plan.run_id,
                audit_action="materialization_commit",
                outbox_event_type="materialization.completed",
            )
            self._mark_materialization_run_succeeded(conn, ctx, plan.run_id, result)
            self._record_materialization_lineage(conn, ctx, plan, result)
            return result

    def _write_materialization_rows(
        self,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
    ) -> Path:
        rows, fieldnames = self._materialization_rows(plan.materialization_type, ctx=ctx)
        staged = self.dataset_transaction_service._staging_file(
            plan.target_dataset,
            plan.transaction_id,
            "part-00000.parquet",
        )
        self.dataset_ingest_service._rows_to_parquet(rows, staged, fieldnames)
        return staged

    def _record_materialization_lineage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
        result: CommitResult,
    ) -> None:
        self.runtime_service._lineage(
            conn,
            ctx,
            "materialization",
            plan.materialization_id,
            "dataset_version",
            result.version_id,
            "materializes_to",
            plan.run_id,
        )

    def _abort_materialization_run(
        self,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
        exc: Exception,
    ) -> None:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            plan.transaction_id,
            plan.run_id,
            exc,
            "materialization",
            adapter="compute_adapter.rows_to_parquet",
        )

    def _mark_materialization_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None:
        """Close a materialization run with the produced dataset version."""
        self.materialization_repository.update_materialization_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            materialization_run_id=run_id,
            status="succeeded",
            target_dataset_version_id=result.version_id,
            row_count=result.row_count,
            error=None,
            completed_at=_now(),
        )

    def _ensure_materialization(
        self,
        api_name: str,
        *,
        ctx: RequestContext,
    ) -> MaterializationRow:
        if api_name not in MATERIALIZATION_SPECS:
            raise NotFound("materialization not found", details={"api_name": api_name})
        spec = MATERIALIZATION_SPECS[api_name]
        with self.engine.begin() as conn:
            existing = self.materialization_repository.materialization_by_api_name(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                api_name=api_name,
            )
            if existing:
                return existing
            materialization_id = _new_id("mat")
            self.materialization_repository.insert_materialization(
                transaction=conn,
                record=MaterializationRecord(
                    materialization_id=materialization_id,
                    tenant_id=ctx.tenant_id,
                    api_name=api_name,
                    materialization_type=spec.materialization_type,
                    source_ref=spec.source,
                    target_ref=spec.target,
                    trigger_config={"type": "manual"},
                    enabled=True,
                ),
            )
            row = self.materialization_repository.materialization_by_id(
                transaction=conn,
                materialization_id=materialization_id,
            )
            if row is None:
                raise InvariantViolation("materialization insert failed")
            return row

    def _materialization_watermark(
        self,
        conn: TransactionContext,
        materialization_type: str,
    ) -> Mapping[str, object]:
        if materialization_type == "action_log":
            max_created = self.materialization_repository.latest_action_run_watermark(transaction=conn)
            return {"action_run_created_at_lte": max_created}
        max_updated = self.materialization_repository.latest_object_record_watermark(transaction=conn)
        return {"object_updated_at_lte": max_updated}

    def _materialization_rows(
        self,
        materialization_type: str,
        *,
        ctx: RequestContext,
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        with self.engine.begin() as conn:
            if materialization_type == "action_log":
                return self._action_log_materialization_rows(conn, ctx)
            return self._order_current_materialization_rows(conn, ctx)

    def _action_log_materialization_rows(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        action_rows = self.runtime_service._rows_for_tenant(conn, "action_runs", ctx)
        edit_rows = self.runtime_service._rows_for_tenant(conn, "object_edits", ctx)
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

    def _action_log_row(self, action: RuntimeRow, edit: RuntimeRow) -> Mapping[str, object]:
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
        conn: TransactionContext,
        ctx: RequestContext,
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        records = [
            row
            for row in self.runtime_service._rows_for_tenant(conn, "object_records", ctx)
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

    def _order_current_row(self, record: RuntimeRow) -> Mapping[str, object]:
        props = self._row_mapping(record, "properties")
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

    def _row_mapping(self, row: RuntimeRow, key: str) -> Mapping[str, object]:
        value = row.get(key)
        return value if isinstance(value, Mapping) else {}
