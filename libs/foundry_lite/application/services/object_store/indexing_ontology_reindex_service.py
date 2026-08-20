"""Object-index ontology reindex use-case service."""

from __future__ import annotations

from foundry_lite.application.ports import (
    ObjectIndexRepository,
    OntologyObjectReindexResult,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing_ontology_reindex import (
    OntologyReindexExecution,
    completed_ontology_reindex_result,
    ontology_reindex_execution,
    ontology_reindex_result,
    pending_ontology_reindex_operation,
)
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetVersions,
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_rebuild_service import ObjectIndexRebuildService
from foundry_lite.application.services.object_store.indexing_record_mutations import ObjectIndexRecordMutationService
from foundry_lite.application.services.object_store.indexing_runs import _start_ontology_reindex_plan
from foundry_lite.application.services.object_store.indexing_types import ObjectIndexRebuildCounts
from foundry_lite.domain.context import RequestContext


class ObjectOntologyReindexService(CoreService):
    """Runs ontology migration-triggered object reindex plans."""

    required_dependencies = ("engine", "object_index_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_version_service",
        "object_index_rebuild_service",
        "object_index_record_mutation_service",
        "ontology_service",
        "runtime_service",
    )
    dataset_registry_service: IndexDatasetRegistry
    dataset_version_service: IndexDatasetVersions
    object_index_repository: ObjectIndexRepository
    object_index_rebuild_service: ObjectIndexRebuildService
    object_index_record_mutation_service: ObjectIndexRecordMutationService
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    def index_ontology_reindex_plan(
        self,
        object_type_api_name: str,
        reindex_key: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyObjectReindexResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:index", "object_type", object_type_api_name)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_ontology_reindex_plan",
            resource_type="object_type",
            resource_id=object_type_api_name,
        )
        prepared = self._prepare_ontology_reindex_execution(ctx, object_type_api_name, reindex_key)
        if isinstance(prepared, dict):
            return prepared
        return self._run_ontology_reindex_execution(ctx, prepared)

    def _prepare_ontology_reindex_execution(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        reindex_key: str,
    ) -> OntologyObjectReindexResult | OntologyReindexExecution:
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            replay = completed_ontology_reindex_result(
                conn=conn,
                ctx=ctx,
                object_type=object_type,
                reindex_key=reindex_key,
                object_index_repository=self.object_index_repository,
            )
            if replay is not None:
                return replay
            plan = _start_ontology_reindex_plan(
                conn=conn,
                ctx=ctx,
                object_type_api_name=object_type_api_name,
                reindex_key=reindex_key,
                dataset_registry_service=self.dataset_registry_service,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
            )
            operation = pending_ontology_reindex_operation(object_type, reindex_key)
        return ontology_reindex_execution(plan, reindex_key, operation)

    def _run_ontology_reindex_execution(
        self,
        ctx: RequestContext,
        execution: OntologyReindexExecution,
    ) -> OntologyObjectReindexResult:
        plan = execution.plan
        try:
            rows = self.object_index_rebuild_service._read_index_source_rows(plan)
            with self.engine.begin() as conn:
                counts = self.object_index_rebuild_service._persist_index_rebuild(conn, ctx, plan, rows)
                self._finish_ontology_reindex(conn, ctx, execution, counts)
            return ontology_reindex_result(execution, counts)
        except Exception as exc:
            with self.engine.begin() as conn:
                self.object_index_record_mutation_service.mark_index_run_failed(conn, ctx, plan.run_id, exc)
            raise

    def _finish_ontology_reindex(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        execution: OntologyReindexExecution,
        counts: ObjectIndexRebuildCounts,
    ) -> None:
        plan = execution.plan
        completed_at = _now()
        self.object_index_repository.deactivate_superseded_object_type_index(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            object_type_api_name=plan.object_type["api_name"],
            updated_at=completed_at,
        )
        self.object_index_record_mutation_service.mark_index_run_succeeded(conn, ctx, plan.run_id, counts)
        self.ontology_service._complete_object_reindex_contract(
            conn,
            ctx,
            plan.object_type["id"],
            execution.reindex_key,
            plan.run_id,
            plan.source_dataset_version_id,
            completed_at,
        )
        self._audit_ontology_reindex(conn, ctx, execution, counts)

    def _audit_ontology_reindex(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        execution: OntologyReindexExecution,
        counts: ObjectIndexRebuildCounts,
    ) -> None:
        payload = ontology_reindex_result(execution, counts)
        plan = execution.plan
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="ontology.object_reindexed",
            resource_type="object_type",
            resource_id=plan.object_type["id"],
            action="ontology_reindex",
            after_ref=payload,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._outbox(
            conn,
            ctx,
            "ontology.object_reindexed",
            "object_type",
            plan.object_type["id"],
            payload,
            idempotency_key=execution.reindex_key,
            correlation_id=ctx.request_id,
        )
