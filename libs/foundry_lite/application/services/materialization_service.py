from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, RuntimeRow, TransactionContext
from foundry_lite.application.ports.materialization_repository import (
    MaterializationRecord,
    MaterializationReplayResult,
    MaterializationRow,
    MaterializationRunRecord,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.materialization_action_log import action_log_materialization_rows
from foundry_lite.application.services.materialization_late_data import object_snapshot_watermark
from foundry_lite.application.services.materialization_protocols import (
    MaterializationDatasetIngest,
    MaterializationDatasetRegistry,
    MaterializationDatasetTransactions,
    MaterializationOntologyLookup,
    MaterializationRuntimeBoundary,
    mark_materialization_run_succeeded,
)
from foundry_lite.application.services.materialization_results import (
    commit_result_for_existing_materialization,
    materialization_rows_hash,
    materialization_transaction_metadata,
    materialization_with_spec,
    run_watermark,
)
from foundry_lite.application.services.materialization_specs import (
    available_materialization_specs,
    object_snapshot_rows,
    resolve_materialization_spec,
)
from foundry_lite.application.services.materialization_types import (
    MaterializationRunPlan,
    MaterializationSpec,
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


class MaterializationService(CoreService):
    required_dependencies = ("engine", "materialization_repository", "dataset_version_repository", "policy")
    required_collaborators = (
        "dataset_ingest_service",
        "dataset_registry_service",
        "dataset_transaction_service",
        "ontology_service",
        "runtime_service",
    )
    dataset_ingest_service: MaterializationDatasetIngest
    dataset_registry_service: MaterializationDatasetRegistry
    dataset_transaction_service: MaterializationDatasetTransactions
    ontology_service: MaterializationOntologyLookup
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
        candidate = self._materialization_plan_or_existing(ctx, api_name, materialization, target_dataset)
        if isinstance(candidate, CommitResult):
            return candidate
        try:
            return self._complete_materialization_run(ctx, candidate)
        except Exception as exc:
            self._abort_materialization_run(ctx, candidate, exc)
            raise

    def _materialization_plan_or_existing(
        self,
        ctx: RequestContext,
        api_name: str,
        materialization: MaterializationRow,
        target_dataset: DatasetRow,
    ) -> MaterializationRunPlan | CommitResult:
        with self.engine.begin() as conn:
            materialization_type = supported_materialization_type(materialization["materialization_type"])
            watermark = self._materialization_watermark(conn, ctx, materialization)
            existing = self._existing_materialization_result(
                conn,
                ctx,
                materialization,
                target_dataset,
                watermark,
            )
            if existing is not None:
                return existing
            tx_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, target_dataset, "SNAPSHOT")
            rows, fieldnames = self._materialization_rows(
                conn,
                materialization,
                ctx=ctx,
                watermark=watermark,
            )
            plan = MaterializationRunPlan(
                api_name=api_name,
                materialization_id=materialization["id"],
                materialization_type=materialization_type,
                target_dataset=target_dataset,
                transaction_id=tx_id,
                run_id=_new_id("mat_run"),
                watermark=watermark,
                rows=rows,
                fieldnames=fieldnames,
            )
            self._insert_materialization_run(conn, ctx, plan)
        return plan

    def _existing_materialization_result(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        materialization: MaterializationRow,
        target_dataset: DatasetRow,
        watermark: Mapping[str, object],
    ) -> CommitResult | None:
        run = self.materialization_repository.latest_succeeded_materialization_run(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            materialization_id=materialization["id"],
        )
        if run is None or run_watermark(run) != watermark:
            return None
        latest = self.dataset_version_repository.latest_version_by_dataset_id(
            transaction=conn,
            dataset_id=str(target_dataset["id"]),
        )
        if latest is None or latest["id"] != run.get("target_dataset_version_id"):
            return None
        schema = self.dataset_version_repository.schema_for_version(
            dataset_id=latest["dataset_id"],
            schema_version=latest["schema_version"],
        )
        if schema is None:
            raise InvariantViolation("materialization result schema is missing")
        return commit_result_for_existing_materialization(target_dataset, latest, schema["schema_hash"])

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
            # Replay reads through the same resolution as runs so re-declared
            # specs replay against the declaration that runs use; a spec that
            # is no longer declared replays from its stored registration.
            resolved = resolve_materialization_spec(self.ontology_service, conn, ctx, str(run["api_name"]))
            if resolved is not None:
                materialization = materialization_with_spec(materialization, resolved)
            watermark = run_watermark(run)
            rows, _ = self._materialization_rows(
                conn,
                materialization,
                ctx=ctx,
                watermark=watermark,
            )
        return MaterializationReplayResult(
            materialization_run_id=materialization_run_id,
            api_name=str(run["api_name"]),
            watermark=watermark,
            row_count=len(rows),
            row_hash=materialization_rows_hash(rows),
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
                self._record_materialization_outbox_relation(conn, ctx, plan, result)

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
                    transaction_metadata=materialization_transaction_metadata(plan),
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

    def _record_materialization_outbox_relation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
        result: CommitResult,
    ) -> None:
        outbox_event_id = self._materialization_completed_outbox_event_id(conn, ctx, plan, result)
        self.runtime_service._run_relation(
            conn,
            ctx,
            source_run_type="materialization",
            source_run_id=plan.run_id,
            target_run_type="outbox",
            target_run_id=outbox_event_id,
            relation="emitted",
            resource_type="dataset_version",
            resource_id=result.version_id,
            metadata={
                "apiName": plan.api_name,
                "datasetRef": result.dataset_ref,
                "eventType": "materialization.completed",
            },
        )

    def _materialization_completed_outbox_event_id(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
        result: CommitResult,
    ) -> str:
        for row in self.runtime_service._rows_for_tenant(conn, "outbox_events", ctx):
            if (
                row.get("event_type") == "materialization.completed"
                and row.get("aggregate_type") == "dataset_version"
                and row.get("aggregate_id") == result.version_id
                and row.get("correlation_id") == plan.run_id
            ):
                return str(row["id"])
        raise InvariantViolation(
            "materialization completed without durable outbox event",
            details={"run_id": plan.run_id, "version_id": result.version_id},
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

    def list_materialization_specs(self, *, ctx: RequestContext | None = None) -> list[Mapping[str, object]]:
        """List runnable specs; pure permission check because reads must not write audit rows."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "materialization:run")
        with self.engine.begin() as conn:
            return list(available_materialization_specs(self.ontology_service, conn, ctx))

    def _ensure_materialization(
        self,
        api_name: str,
        *,
        ctx: RequestContext,
    ) -> MaterializationRow:
        with self.engine.begin() as conn:
            spec = resolve_materialization_spec(self.ontology_service, conn, ctx, api_name)
            if spec is None:
                raise NotFound("materialization not found", details={"api_name": api_name})
            existing = self.materialization_repository.materialization_by_api_name(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                api_name=api_name,
            )
            if existing:
                # A corrupted stored type must fail closed instead of being
                # silently repaired by the resolved declaration.
                supported_materialization_type(str(existing["materialization_type"]))
                return materialization_with_spec(existing, spec)
            return self._insert_materialization(conn, ctx, api_name, spec)

    def _insert_materialization(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        spec: MaterializationSpec,
    ) -> MaterializationRow:
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
        materialization: MaterializationRow,
        *,
        ctx: RequestContext,
        watermark: Mapping[str, object],
    ) -> tuple[Sequence[Mapping[str, object]], list[str]]:
        materialization_type = supported_materialization_type(materialization["materialization_type"])
        if materialization_type == "action_log":
            return action_log_materialization_rows(
                self.materialization_repository,
                self.policy,
                conn,
                ctx,
                watermark,
            )
        if materialization_type == "object_snapshot":
            return object_snapshot_rows(
                self.ontology_service,
                self.materialization_repository,
                conn,
                ctx,
                materialization,
                watermark,
            )
        unsupported_materialization_type(materialization_type)
