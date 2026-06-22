from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, RuntimeRow, TransactionContext
from foundry_lite.application.ports.materialization_repository import (
    MaterializationRecord,
    MaterializationReplayResult,
    MaterializationRow,
    MaterializationRunRecord,
    MaterializationSourceRef,
    MaterializationTargetRef,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.materialization_late_data import object_snapshot_watermark
from foundry_lite.application.services.materialization_protocols import (
    MaterializationDatasetIngest,
    MaterializationDatasetRegistry,
    MaterializationDatasetTransactions,
    MaterializationRuntimeBoundary,
    mark_materialization_run_succeeded,
)
from foundry_lite.application.services.materialization_types import (
    supported_materialization_type,
    unsupported_materialization_type,
)
from foundry_lite.application.services.write_traffic_gate import require_write_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    DatasetCommitBlocked,
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
    rows: Sequence[Mapping[str, object]]
    fieldnames: list[str]


@dataclass(frozen=True)
class MaterializationSpec:
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
        require_write_open(self.runtime_service, ctx, "materialize", "materialization", api_name)
        materialization = self._ensure_materialization(api_name, ctx=ctx)
        target_dataset = self.dataset_registry_service.get_dataset(materialization["target_ref"]["dataset"], ctx=ctx)
        run_id = _new_id("mat_run")
        with self.engine.begin() as conn:
            tx_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, target_dataset, "SNAPSHOT")
            materialization_type = supported_materialization_type(materialization["materialization_type"])
            watermark = self._materialization_watermark(conn, ctx, materialization)
            rows, fieldnames = self._materialization_rows(
                conn,
                materialization_type,
                ctx=ctx,
                watermark=watermark,
            )
            plan = MaterializationRunPlan(
                api_name=api_name,
                materialization_id=materialization["id"],
                materialization_type=materialization_type,
                target_dataset=target_dataset,
                transaction_id=tx_id,
                run_id=run_id,
                watermark=watermark,
                rows=rows,
                fieldnames=fieldnames,
            )
            self._insert_materialization_run(conn, ctx, plan)
        try:
            return self._complete_materialization_run(ctx, plan)
        except Exception as exc:
            self._abort_materialization_run(ctx, plan, exc)
            raise

    def replay_rows_for_run(
        self,
        materialization_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> MaterializationReplayResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "materialization:run", "materialization", materialization_run_id)
        with self.engine.begin() as conn:
            run = self._materialization_run_by_id(conn, ctx, materialization_run_id)
            materialization = self.materialization_repository.materialization_by_id(
                transaction=conn,
                materialization_id=str(run["materialization_id"]),
            )
            if materialization is None:
                raise NotFound("materialization not found", details={"materialization_run_id": materialization_run_id})
            watermark = _run_watermark(run)
            materialization_type = supported_materialization_type(materialization["materialization_type"])
            rows, _ = self._materialization_rows(
                conn,
                materialization_type,
                ctx=ctx,
                watermark=watermark,
            )
        return MaterializationReplayResult(
            materialization_run_id=materialization_run_id,
            api_name=str(run["api_name"]),
            watermark=watermark,
            row_count=len(rows),
            row_hash=_materialization_rows_hash(rows),
            rows=rows,
        )

    def _materialization_run_by_id(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        materialization_run_id: str,
    ) -> RuntimeRow:
        for row in self.runtime_service._rows_for_tenant(conn, "materialization_runs", ctx):
            if row["id"] == materialization_run_id:
                return row
        raise NotFound("materialization run not found", details={"materialization_run_id": materialization_run_id})

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
        blocked: DatasetCommitBlocked | None = None
        with self.engine.begin() as conn:

            def after_persist(conn: TransactionContext, result: CommitResult) -> None:
                mark_materialization_run_succeeded(self.materialization_repository, conn, ctx, plan.run_id, result)
                self._record_materialization_lineage(conn, ctx, plan, result)

            try:
                return self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=plan.target_dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="materialization_commit",
                    outbox_event_type="materialization.completed",
                    extra_checks=({"type": "allow_empty"},),
                    transaction_metadata=_materialization_transaction_metadata(plan),
                    after_persist=after_persist,
                )
            except DatasetCommitBlocked as exc:
                blocked = exc
        if blocked is not None:
            raise blocked
        raise InvariantViolation("materialization finalization did not return a commit result")

    def _write_materialization_rows(
        self,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
    ) -> Path:
        del ctx
        staged = self.dataset_transaction_service._staging_file(
            plan.target_dataset,
            plan.transaction_id,
            "part-00000.parquet",
        )
        self.dataset_ingest_service._rows_to_parquet(plan.rows, staged, plan.fieldnames)
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
        ctx: RequestContext,
        materialization: MaterializationRow,
    ) -> Mapping[str, object]:
        materialization_type = materialization["materialization_type"]
        if materialization_type == "action_log":
            watermark = self.materialization_repository.latest_action_run_watermark(
                transaction=conn,
                tenant_id=ctx.tenant_id,
            )
            return {
                "action_run_completed_at_lte": watermark["completed_at"],
                "action_run_id_lte": watermark["action_run_id"],
            }
        if materialization_type == "object_snapshot":
            return object_snapshot_watermark(
                self.materialization_repository, self.runtime_service, conn, ctx, materialization
            )
        unsupported_materialization_type(materialization_type)

    def _materialization_rows(
        self,
        conn: TransactionContext,
        materialization_type: str,
        *,
        ctx: RequestContext,
        watermark: Mapping[str, object],
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        if materialization_type == "action_log":
            return self._action_log_materialization_rows(conn, ctx, watermark)
        if materialization_type == "object_snapshot":
            return self._order_current_materialization_rows(conn, ctx, watermark)
        unsupported_materialization_type(materialization_type)

    def _action_log_materialization_rows(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        watermark: Mapping[str, object],
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        action_rows = self.runtime_service._rows_for_tenant(conn, "action_runs", ctx)
        edit_rows = self.runtime_service._rows_for_tenant(conn, "object_edits", ctx)
        edit_by_action = {row["action_run_id"]: row for row in edit_rows}
        rows = [
            self._action_log_row(action, edit_by_action.get(action["id"], {}))
            for action in action_rows
            if action["status"] == "succeeded" and _action_within_watermark(action, watermark)
        ]
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
        watermark: Mapping[str, object],
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        records = self._order_records_at_watermark(conn, ctx, watermark)
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

    def _order_current_row(self, record: Mapping[str, object]) -> Mapping[str, object]:
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

    def _row_mapping(self, row: Mapping[str, object], key: str) -> Mapping[str, object]:
        value = row.get(key)
        return value if isinstance(value, Mapping) else {}

    def _order_records_at_watermark(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        watermark: Mapping[str, object],
    ) -> Sequence[Mapping[str, object]]:
        max_sequence = watermark.get("object_change_sequence_lte")
        index_version = watermark.get("active_index_version")
        if not isinstance(max_sequence, int) or not isinstance(index_version, str):
            return []
        return self.materialization_repository.object_records_at_watermark(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name="Order",
            index_version=index_version,
            max_object_change_sequence=max_sequence,
        )


def _materialization_transaction_metadata(plan: MaterializationRunPlan) -> dict[str, object]:
    reopen = plan.watermark.get("lateDataReopen")
    return {
        "materializationDetail": {
            "apiName": plan.api_name,
            "materializationType": plan.materialization_type,
            "watermark": dict(plan.watermark),
            "reopen": reopen if isinstance(reopen, Mapping) else {"isReopened": False},
        }
    }


def _action_within_watermark(row: RuntimeRow, watermark: Mapping[str, object]) -> bool:
    completed_at = row.get("completed_at")
    max_completed = watermark.get("action_run_completed_at_lte")
    max_action_id = watermark.get("action_run_id_lte")
    if not isinstance(completed_at, str) or not isinstance(max_completed, str):
        return False
    if completed_at < max_completed:
        return True
    return completed_at == max_completed and isinstance(max_action_id, str) and str(row["id"]) <= max_action_id


def _run_watermark(run: RuntimeRow) -> Mapping[str, object]:
    watermark = run.get("object_store_watermark") or run.get("source_cursor")
    return watermark if isinstance(watermark, Mapping) else {}


def _materialization_rows_hash(rows: Sequence[Mapping[str, object]]) -> str:
    payload = json.dumps([dict(row) for row in rows], sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()
