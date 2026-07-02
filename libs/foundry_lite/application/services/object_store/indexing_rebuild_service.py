"""Object-index rebuild use-case service."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    ObjectIndexRebuildResult,
    ObjectIndexRepository,
    ObjectIndexShadowRebuildResult,
    ObjectPropertyMap,
    ObjectRecordRow,
    ObjectRecordSourceDeletion,
    ObjectTypeRow,
    PropertyTypeRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing_link_service import ObjectLinkIndexingService
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
from foundry_lite.application.services.object_store.indexing_runs import (
    _start_failed_index_replay_plan,
    _start_index_rebuild_plan,
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
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.object_store.indexing import (
    SOURCE_MISSING_DELETION_REASON,
    require_unique_source_object_ids,
    should_delete_missing_source_item,
    should_emit_source_object_change,
)


class ObjectIndexRebuildService(CoreService):
    """Runs snapshot/shadow object-index rebuild use cases."""

    required_dependencies = ("engine", "compute_adapter", "object_index_repository")
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
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        ctx = ctx or RequestContext()
        self._require_object_index(ctx, "object_type", object_type_api_name)
        self._require_write_traffic(ctx, "index_rebuild", "object_type", object_type_api_name)
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
        self._require_object_index(ctx, "object_type", object_type_api_name)
        self._require_write_traffic(ctx, "index_shadow_rebuild", "object_type", object_type_api_name)
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
        return self._run_shadow_rebuild_plan(ctx, plan, expected_count, expected_hash)

    def index_replay_run(
        self,
        index_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        ctx = ctx or RequestContext()
        self._require_object_index(ctx, "index_run", index_run_id)
        self.runtime_service._require_or_audit(ctx, "operations:retry", "index_run", index_run_id)
        self._require_write_traffic(ctx, "index_replay_run", "index_run", index_run_id)
        with self.engine.begin() as conn:
            plan = _start_failed_index_replay_plan(
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

    def _require_object_index(self, ctx: RequestContext, resource_type: str, resource_id: str) -> None:
        self.runtime_service._require_or_audit(ctx, "object:index", resource_type, resource_id)

    def _require_write_traffic(
        self,
        ctx: RequestContext,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def _read_index_source_rows(self, plan: ObjectIndexRebuildPlan) -> Sequence[TabularRow]:
        parquet_path = self.dataset_transaction_service._version_file_path(plan.dataset_version)
        return self.compute_adapter.rows_from_parquet(parquet_path)

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
        objects_upserted = 0
        source_object_ids: set[str] = set()
        source_rows = self._source_rows_from_dataset_rows(conn, plan, rows)
        require_unique_source_object_ids(
            plan.object_type_api_name,
            [source.object_id for source in source_rows],
            source_dataset_version_id=plan.source_dataset_version_id,
        )
        for source in source_rows:
            source_object_ids.add(source.object_id)
            self._index_object_source_row(conn, ctx, plan, source)
            objects_upserted += 1
        objects_deleted = self._delete_missing_source_records(conn, ctx, plan, source_object_ids)
        links_upserted = self.object_link_indexing_service._index_links_for_object_type(conn, ctx, plan, rows)
        return ObjectIndexRebuildCounts(len(rows), objects_upserted, objects_deleted, links_upserted)

    def _source_rows_from_dataset_rows(
        self,
        conn: TransactionContext,
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> list[ObjectIndexSourceRow]:
        return [self._source_row_from_dataset_row(conn, plan.object_type, row) for row in rows]

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

    def _delete_missing_source_records(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        source_object_ids: set[str],
    ) -> int:
        records = self.object_index_repository.object_records_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            index_version=plan.index_version,
        )
        deleted_count = 0
        for record in records:
            if not should_delete_missing_source_item(
                is_deleted=bool(record["deleted"]),
                is_present_in_source=str(record["object_id"]) in source_object_ids,
                index_mode=plan.mode,
            ):
                continue
            self._mark_source_missing_record_deleted(conn, ctx, plan, record)
            deleted_count += 1
        return deleted_count

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

    def _source_row_from_dataset_row(
        self,
        conn: TransactionContext,
        object_type: ObjectTypeRow,
        row: TabularRow,
    ) -> ObjectIndexSourceRow:
        properties = self.ontology_service._properties_for_object_type(conn, object_type["id"])
        pk_prop = next(prop for prop in properties if prop["api_name"] == object_type["primary_key_property"])
        pk_column = pk_prop["column_name"]
        if pk_column is None:
            raise ValidationFailed("object primary key column missing")
        object_id = row.get(pk_column)
        if object_id in {None, ""}:
            raise ValidationFailed("object primary key cannot be null")
        base_patch = self._base_patch_from_dataset_row(row, properties)
        return ObjectIndexSourceRow(object_id=str(object_id), base_patch=base_patch)

    def _active_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> ObjectRecordRow | None:
        return self.object_records_service._object_record(conn, ctx, object_type_api_name, object_id)

    def _base_patch_from_dataset_row(
        self,
        row: TabularRow,
        properties: Sequence[PropertyTypeRow],
    ) -> ObjectPropertyMap:
        base_patch: dict[str, object] = {}
        for prop in properties:
            if prop["source"] != "dataset":
                continue
            column_name = prop["column_name"]
            if column_name is None:
                raise ValidationFailed("dataset-backed property column missing", details={"property": prop["api_name"]})
            base_patch[prop["api_name"]] = row.get(column_name)
        return base_patch

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
        self.object_index_record_mutation_service.mark_index_run_succeeded(conn, ctx, plan.run_id, counts)
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
