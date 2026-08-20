"""Object-index rebuild use-case service."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    ObjectIndexRebuildResult,
    ObjectIndexRepository,
    ObjectIndexRowHashRepository,
    ObjectIndexShadowRebuildResult,
    ObjectPropertyMap,
    ObjectRecordRow,
    ObjectRecordSourceDeletion,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing_changelog import (
    ChangelogRefreshPlan,
    index_source_ref_updates,
    load_stored_row_hashes,
    persist_row_hashes,
    plan_changelog_refresh,
    source_rows_from_dataset_rows,
)
from foundry_lite.application.services.object_store.indexing_link_service import ObjectLinkIndexingService
from foundry_lite.application.services.object_store.indexing_multi_source import read_index_source_rows
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetTransactionFiles,
    IndexDatasetVersions,
    IndexObjectRecords,
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_record_factory import new_object_record_insert
from foundry_lite.application.services.object_store.indexing_record_mutations import ObjectIndexRecordMutationService
from foundry_lite.application.services.object_store.indexing_replay import (
    start_failed_index_replay_plan,
    start_index_rebuild_plan,
)
from foundry_lite.application.services.object_store.indexing_shadow_service import ObjectIndexShadowService
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
    ObjectIndexSourceRow,
    object_index_rebuild_response,
    object_index_shadow_response,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.object_store.indexing import (
    SOURCE_MISSING_DELETION_REASON,
    require_unique_source_object_ids,
    should_delete_missing_source_item,
    should_emit_source_object_change,
)


class ObjectIndexRebuildService(CoreService):
    """Runs snapshot/shadow object-index rebuild use cases."""

    required_dependencies = (
        "engine",
        "compute_adapter",
        "object_index_repository",
        "object_index_row_hash_repository",
    )
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "dataset_version_service",
        "object_index_record_mutation_service",
        "object_index_shadow_service",
        "object_link_indexing_service",
        "object_records_service",
        "ontology_service",
        "runtime_service",
    )
    dataset_registry_service: IndexDatasetRegistry
    dataset_transaction_service: IndexDatasetTransactionFiles
    dataset_version_service: IndexDatasetVersions
    object_index_record_mutation_service: ObjectIndexRecordMutationService
    object_index_shadow_service: ObjectIndexShadowService
    object_link_indexing_service: ObjectLinkIndexingService
    object_records_service: IndexObjectRecords
    object_index_repository: ObjectIndexRepository
    object_index_row_hash_repository: ObjectIndexRowHashRepository
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:index", "object_type", object_type_api_name)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_rebuild",
            resource_type="object_type",
            resource_id=object_type_api_name,
        )
        with self.engine.begin() as conn:
            plan = start_index_rebuild_plan(
                conn=conn,
                ctx=ctx,
                object_type_api_name=object_type_api_name,
                dataset_registry_service=self.dataset_registry_service,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
            )
        return self._run_rebuild_plan(ctx, plan)

    def index_shadow_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        expected_count: int | None = None,
        expected_hash: str | None = None,
    ) -> ObjectIndexShadowRebuildResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:index", "object_type", object_type_api_name)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_shadow_rebuild",
            resource_type="object_type",
            resource_id=object_type_api_name,
        )
        with self.engine.begin() as conn:
            plan = start_index_rebuild_plan(
                conn=conn,
                ctx=ctx,
                object_type_api_name=object_type_api_name,
                dataset_registry_service=self.dataset_registry_service,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
                mode="shadow",
            )
        return self._run_shadow_rebuild_plan(ctx, plan, expected_count, expected_hash)

    def index_replay_run(
        self,
        index_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "index_run", index_run_id)
        self.runtime_service._require_or_audit(ctx, "object:index", "index_run", index_run_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_replay_run",
            resource_type="index_run",
            resource_id=index_run_id,
        )
        with self.engine.begin() as conn:
            plan = start_failed_index_replay_plan(
                conn=conn,
                ctx=ctx,
                index_run_id=index_run_id,
                dataset_version_service=self.dataset_version_service,
                ontology_service=self.ontology_service,
                object_index_repository=self.object_index_repository,
            )
        return self._run_rebuild_plan(ctx, plan)

    def _run_rebuild_plan(self, ctx: RequestContext, plan: ObjectIndexRebuildPlan) -> ObjectIndexRebuildResult:
        try:
            rows = self._read_index_source_rows(plan)
            with self.engine.begin() as conn:
                counts = self._persist_index_rebuild(conn, ctx, plan, rows)
                self._finish_index_rebuild(conn, ctx, plan, counts)
            return object_index_rebuild_response(plan, counts)
        except Exception as exc:
            self._fail_index_run(ctx, plan.run_id, exc)
            raise

    def _run_shadow_rebuild_plan(
        self,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        expected_count: int | None,
        expected_hash: str | None,
    ) -> ObjectIndexShadowRebuildResult:
        try:
            rows = self._read_index_source_rows(plan)
            with self.engine.begin() as conn:
                counts = self._persist_index_rebuild(conn, ctx, plan, rows)
                validation = self.object_index_shadow_service.validate_shadow_index(
                    conn,
                    ctx,
                    plan,
                    expected_count,
                    expected_hash,
                )
                self.object_index_shadow_service.switch_shadow_index(conn, ctx, plan, counts, validation)
            return object_index_shadow_response(plan, counts, validation)
        except Exception as exc:
            self._fail_index_run(ctx, plan.run_id, exc)
            raise

    def _read_index_source_rows(self, plan: ObjectIndexRebuildPlan) -> Sequence[TabularRow]:
        return read_index_source_rows(
            plan,
            version_file_path=self.dataset_transaction_service._version_file_path,
            rows_from_parquet=self.compute_adapter.rows_from_parquet,
        )

    def _fail_index_run(
        self,
        ctx: RequestContext,
        run_id: str,
        exc: Exception,
        adapter: str = "compute_adapter.rows_from_parquet",
    ) -> None:
        with self.engine.begin() as conn:
            self.object_index_record_mutation_service.mark_index_run_failed(conn, ctx, run_id, exc, adapter)

    def _persist_index_rebuild(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> ObjectIndexRebuildCounts:
        source_rows = source_rows_from_dataset_rows(conn, self.ontology_service, plan.object_type, rows)
        require_unique_source_object_ids(
            plan.object_type_api_name,
            [source.object_id for source in source_rows],
            source_dataset_version_id=plan.source_dataset_version_id,
        )
        stored_hashes = load_stored_row_hashes(conn, self.object_index_row_hash_repository, ctx.tenant_id, plan)
        refresh = plan_changelog_refresh(plan, stored_hashes, source_rows)
        for source in refresh.rows_to_index:
            self._index_object_source_row(conn, ctx, plan, source)
        objects_deleted = self._delete_missing_records(conn, ctx, plan, refresh, source_rows)
        link_result = self.object_link_indexing_service._index_links_for_object_type(conn, ctx, plan, rows)
        persist_row_hashes(conn, self.object_index_row_hash_repository, ctx.tenant_id, plan, refresh)
        return ObjectIndexRebuildCounts(
            len(rows),
            len(refresh.rows_to_index),
            objects_deleted,
            link_result.links_upserted,
            refresh.refresh_mode,
            refresh.skipped_count,
            link_result.source_dataset_version_ids,
        )

    def _index_object_source_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        source: ObjectIndexSourceRow,
    ) -> None:
        existing = self.object_index_repository.object_record_in_index(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            object_type_api_name=plan.object_type["api_name"],
            object_id=source.object_id,
            index_version=plan.index_version,
        )
        active = self._active_object_record(conn, ctx, plan.object_type["api_name"], source.object_id)
        if existing is None:
            self._insert_new_object_record(conn, ctx, plan, source, active)
            changed = True
        else:
            changed = self._refresh_existing_object_record(conn, ctx, plan, source, existing, active)
        if should_emit_source_object_change(is_changed=changed, index_mode=plan.mode):
            self.object_index_record_mutation_service.emit_object_changed(
                conn,
                ctx,
                plan.object_type["api_name"],
                source.object_id,
                plan.source_dataset_version_id,
            )

    def _delete_missing_records(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        refresh: ChangelogRefreshPlan,
        source_rows: Sequence[ObjectIndexSourceRow],
    ) -> int:
        source_object_ids = {source.object_id for source in source_rows}
        deleted_count = 0
        for record in self._missing_record_candidates(conn, ctx, plan, refresh):
            if not should_delete_missing_source_item(
                is_deleted=bool(record["deleted"]),
                is_present_in_source=str(record["object_id"]) in source_object_ids,
                is_source_backed=record["source_dataset_version_id"] is not None,
                index_mode=plan.mode,
            ):
                continue
            self._mark_source_missing_record_deleted(conn, ctx, plan, record)
            deleted_count += 1
        return deleted_count

    def _missing_record_candidates(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        refresh: ChangelogRefreshPlan,
    ) -> list[ObjectRecordRow]:
        # Incremental refreshes already know the exact removed keys from the
        # hash diff, so they look up only those records instead of paying the
        # full-index scan a snapshot prune needs.
        if not refresh.is_incremental:
            return self.object_index_repository.object_records_for_index_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=plan.object_type["id"],
                index_version=plan.index_version,
            )
        records = (
            self.object_index_repository.object_record_in_index(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=plan.object_type["id"],
                object_type_api_name=plan.object_type["api_name"],
                object_id=object_id,
                index_version=plan.index_version,
            )
            for object_id in refresh.removed_object_ids
        )
        return [record for record in records if record is not None]

    def _mark_source_missing_record_deleted(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        record: ObjectRecordRow,
    ) -> None:
        object_id = str(record["object_id"])
        self.object_index_repository.mark_object_record_deleted_from_source(
            transaction=conn,
            record=ObjectRecordSourceDeletion(
                record_id=record["id"],
                tenant_id=ctx.tenant_id,
                source_dataset_version_id=plan.source_dataset_version_id,
                object_version=int(record["object_version"]) + 1,
                deletion_reason=SOURCE_MISSING_DELETION_REASON,
                updated_at=_now(),
            ),
        )
        self.object_index_record_mutation_service.emit_object_changed(
            conn,
            ctx,
            plan.object_type["api_name"],
            object_id,
            plan.source_dataset_version_id,
        )

    def _active_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> ObjectRecordRow | None:
        return self.object_records_service._object_record(conn, ctx, object_type_api_name, object_id)

    def _insert_new_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        source: ObjectIndexSourceRow,
        active: ObjectRecordRow | None,
    ) -> None:
        base_patch = dict(source.base_patch)
        edit_properties = dict(active["edit_properties"]) if active is not None else {}
        property_versions = dict(active["property_versions"]) if active is not None else None
        current = self.object_index_record_mutation_service.merge_properties(
            conn,
            plan.object_type["id"],
            base_patch,
            edit_properties,
        )
        self.object_index_repository.insert_object_record(
            transaction=conn,
            record=new_object_record_insert(
                ctx,
                plan,
                source,
                active,
                base_patch,
                edit_properties,
                property_versions,
                current,
            ),
        )

    def _refresh_existing_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        source: ObjectIndexSourceRow,
        existing: ObjectRecordRow,
        active: ObjectRecordRow | None,
    ) -> bool:
        base_patch = dict(source.base_patch)
        edit_source = active or existing
        current = self.object_index_record_mutation_service.merge_properties(
            conn,
            plan.object_type["id"],
            base_patch,
            edit_source["edit_properties"],
        )
        if self._reindex_is_noop(plan, existing, base_patch, current):
            return False
        self.object_index_record_mutation_service.record_conflicts_for_base_update(
            conn,
            ctx,
            plan.object_type,
            source.object_id,
            edit_source,
            base_patch,
            source_dataset_version_id=plan.source_dataset_version_id,
        )
        self.object_index_record_mutation_service.update_object_record_from_source(
            conn,
            ctx,
            existing,
            current,
            base_patch,
            plan.source_dataset_version_id,
        )
        return True

    def _reindex_is_noop(
        self,
        plan: ObjectIndexRebuildPlan,
        existing: ObjectRecordRow,
        base_patch: ObjectPropertyMap,
        current: ObjectPropertyMap,
    ) -> bool:
        # Re-indexing the identical source dataset version with unchanged base and
        # merged properties must not bump object_version or emit object.changed,
        # otherwise every replay invalidates held expectedObjectVersion values.
        return (
            plan.source_dataset_version_id == existing["source_dataset_version_id"]
            and dict(base_patch) == existing["base_properties"]
            and dict(current) == existing["properties"]
        )

    def _finish_index_rebuild(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        counts: ObjectIndexRebuildCounts,
    ) -> None:
        if plan.mode == "full":
            self.object_index_repository.deactivate_superseded_object_type_index(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=plan.object_type["id"],
                object_type_api_name=plan.object_type["api_name"],
                updated_at=_now(),
            )
        self.object_index_record_mutation_service.mark_index_run_succeeded(
            conn,
            ctx,
            plan.run_id,
            counts,
            source_ref_updates=index_source_ref_updates(counts),
        )
        self._audit_index_rebuild(conn, ctx, plan, counts)

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
