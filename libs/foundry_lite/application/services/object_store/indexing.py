from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    IndexRunCursor,
    ObjectIndexRebuildResult,
    ObjectIndexShadowRebuildResult,
    ObjectIndexValidationResult,
    ObjectRecordRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing_cdc_service import ObjectIndexingCdcMixin
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetTransactionFiles,
    IndexDatasetVersions,
    IndexObjectRecords,
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_rebuild_service import ObjectIndexingRebuildMixin
from foundry_lite.application.services.object_store.indexing_runs import (
    _start_failed_index_replay_plan,
    _start_index_rebuild_plan,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexCdcCounts,
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
    ObjectIndexStats,
    object_index_rebuild_response,
    object_index_shadow_response,
    object_index_stats,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


class ObjectIndexingService(ObjectIndexingRebuildMixin, ObjectIndexingCdcMixin, CoreService):
    required_dependencies = ("engine", "compute_adapter", "object_index_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "dataset_version_service",
        "object_records_service",
        "ontology_service",
        "runtime_service",
    )
    dataset_registry_service: IndexDatasetRegistry
    dataset_transaction_service: IndexDatasetTransactionFiles
    dataset_version_service: IndexDatasetVersions
    object_records_service: IndexObjectRecords
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_rebuild",
            resource_type="object_type",
            resource_id=object_type_api_name,
        )
        with self.engine.begin() as conn:
            plan = _start_index_rebuild_plan(
                conn=conn,
                ctx=ctx,
                object_type_api_name=object_type_api_name,
                dataset_registry_service=self.dataset_registry_service,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
            )
        try:
            rows = self._read_index_source_rows(plan)
            with self.engine.begin() as conn:
                counts = self._persist_index_rebuild(conn, ctx, plan, rows)
                self._finish_index_rebuild(conn, ctx, plan, counts)
            return object_index_rebuild_response(plan, counts)
        except Exception as exc:
            with self.engine.begin() as conn:
                self._mark_index_run_failed(conn, ctx, plan.run_id, exc)
            raise

    def index_shadow_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        expected_count: int | None = None,
        expected_hash: str | None = None,
    ) -> ObjectIndexShadowRebuildResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_shadow_rebuild",
            resource_type="object_type",
            resource_id=object_type_api_name,
        )
        with self.engine.begin() as conn:
            plan = _start_index_rebuild_plan(
                conn=conn,
                ctx=ctx,
                object_type_api_name=object_type_api_name,
                dataset_registry_service=self.dataset_registry_service,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
                mode="shadow",
            )
        try:
            rows = self._read_index_source_rows(plan)
            with self.engine.begin() as conn:
                counts = self._persist_index_rebuild(conn, ctx, plan, rows)
                validation = self._validate_shadow_index(conn, ctx, plan, expected_count, expected_hash)
                self._switch_shadow_index(conn, ctx, plan, counts, validation)
            return object_index_shadow_response(plan, counts, validation)
        except Exception as exc:
            with self.engine.begin() as conn:
                self._mark_index_run_failed(conn, ctx, plan.run_id, exc)
            raise

    def index_replay_run(
        self,
        index_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "index_run", index_run_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_replay_run",
            resource_type="index_run",
            resource_id=index_run_id,
        )
        with self.engine.begin() as conn:
            plan = _start_failed_index_replay_plan(
                conn=conn,
                ctx=ctx,
                index_run_id=index_run_id,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
            )
        try:
            rows = self._read_index_source_rows(plan)
            with self.engine.begin() as conn:
                counts = self._persist_index_rebuild(conn, ctx, plan, rows)
                self._finish_index_rebuild(conn, ctx, plan, counts)
            return object_index_rebuild_response(plan, counts)
        except Exception as exc:
            with self.engine.begin() as conn:
                self._mark_index_run_failed(conn, ctx, plan.run_id, exc)
            raise

    def _read_index_source_rows(self, plan: ObjectIndexRebuildPlan) -> Sequence[TabularRow]:
        parquet_path = self.dataset_transaction_service._version_file_path(plan.dataset_version)
        return self.compute_adapter.rows_from_parquet(parquet_path)

    def _active_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> ObjectRecordRow | None:
        return self.object_records_service._object_record(conn, ctx, object_type_api_name, object_id)

    def _finish_index_rebuild(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        counts: ObjectIndexRebuildCounts,
    ) -> None:
        self._mark_index_run_succeeded(conn, ctx, plan.run_id, counts)
        self._audit_index_rebuild(conn, ctx, plan, counts)

    def _validate_shadow_index(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        expected_count: int | None,
        expected_hash: str | None,
    ) -> ObjectIndexValidationResult:
        baseline = self._expected_shadow_stats(conn, ctx, plan, expected_count, expected_hash)
        records = self.object_index_repository.object_records_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            index_version=plan.index_version,
        )
        actual = object_index_stats(records)
        validation = _validation_result(
            baseline.object_count,
            actual.object_count,
            baseline.object_hash,
            actual.object_hash,
        )
        if _validation_failed(validation):
            raise ValidationFailed("shadow index validation failed", details=dict(validation))
        return validation

    def _expected_shadow_stats(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        expected_count: int | None,
        expected_hash: str | None,
    ) -> ObjectIndexStats:
        records = self.object_index_repository.object_records_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            index_version=plan.previous_index_version,
        )
        baseline = object_index_stats(records)
        return ObjectIndexStats(
            object_count=expected_count if expected_count is not None else baseline.object_count,
            object_hash=expected_hash if expected_hash is not None else baseline.object_hash,
        )

    def _switch_shadow_index(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        counts: ObjectIndexRebuildCounts,
        validation: ObjectIndexValidationResult,
    ) -> None:
        now = _now()
        switched = self.object_index_repository.switch_active_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            index_version=plan.index_version,
            updated_at=now,
            expected_previous_index_version=plan.previous_index_version,
        )
        if not switched:
            raise ValidationFailed(
                "shadow index active version changed during switch",
                details={
                    "objectType": plan.object_type["api_name"],
                    "expectedPreviousIndexVersion": plan.previous_index_version,
                    "attemptedIndexVersion": plan.index_version,
                },
            )
        self._mark_index_run_succeeded(conn, ctx, plan.run_id, counts)
        self._audit_shadow_index_switch(conn, ctx, plan, validation)

    def _audit_index_rebuild(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        counts: ObjectIndexRebuildCounts,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="object.index.rebuilt",
            resource_type="object_type",
            resource_id=plan.object_type["id"],
            action="index_rebuild",
            after_ref={"objects_upserted": counts.objects_upserted},
        )

    def _audit_shadow_index_switch(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        validation: ObjectIndexValidationResult,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="object.index.shadow_switched",
            resource_type="object_type",
            resource_id=plan.object_type["id"],
            action="index_shadow_rebuild",
            before_ref={"indexVersion": plan.previous_index_version},
            after_ref={"indexVersion": plan.index_version, "validation": validation},
        )

    def _mark_index_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        counts: ObjectIndexRebuildCounts | ObjectIndexCdcCounts,
        cursor: IndexRunCursor | None = None,
    ) -> None:
        """Persist successful index run counters within the current tenant."""
        updated = self.object_index_repository.mark_index_run_succeeded(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            rows_read=counts.rows_read,
            objects_upserted=counts.objects_upserted,
            objects_deleted=counts.objects_deleted,
            links_upserted=counts.links_upserted,
            cursor=cursor or {"last_row": counts.rows_read},
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("index run terminal state changed concurrently", details={"run_id": run_id})

    def _mark_index_run_failed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        exc: Exception,
        adapter: str = "compute_adapter.rows_from_parquet",
    ) -> None:
        """Persist a failed index run with the normalized error payload."""
        self.object_index_repository.mark_index_run_failed(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            error=self.runtime_service._error_payload(
                exc,
                ctx,
                run_id=run_id,
                correlation_id=run_id,
                adapter=adapter,
            ),
            completed_at=_now(),
        )


def _validation_result(
    expected_count: int,
    actual_count: int,
    expected_hash: str,
    actual_hash: str,
) -> ObjectIndexValidationResult:
    return {
        "expectedCount": expected_count,
        "actualCount": actual_count,
        "expectedHash": expected_hash,
        "actualHash": actual_hash,
    }


def _validation_failed(validation: ObjectIndexValidationResult) -> bool:
    return (
        validation["expectedCount"] != validation["actualCount"]
        or validation["expectedHash"] != validation["actualHash"]
    )
