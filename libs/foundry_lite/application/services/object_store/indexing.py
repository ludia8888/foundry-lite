from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    LinkTypeRow,
    ObjectConflictRecord,
    ObjectIndexRebuildResult,
    ObjectPropertyMap,
    ObjectRecordInsert,
    ObjectRecordRow,
    ObjectRecordSourceUpdate,
    ObjectTypeRow,
    PropertyTypeRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import (
    _json_hash,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.index_records import build_object_link_insert
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetTransactionFiles,
    IndexDatasetVersions,
    IndexObjectRecords,
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_runs import (
    _start_failed_index_replay_plan,
    _start_index_rebuild_plan,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
    ObjectIndexSourceRow,
    object_index_rebuild_response,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class ObjectIndexingService(CoreService):
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
            return object_index_rebuild_response(plan, counts)
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
            return object_index_rebuild_response(plan, counts)
        except Exception as exc:
            with self.engine.begin() as conn:
                self._mark_index_run_failed(conn, ctx, plan.run_id, exc)
            raise

    def _read_index_source_rows(self, plan: ObjectIndexRebuildPlan) -> Sequence[TabularRow]:
        parquet_path = self.dataset_transaction_service._version_file_path(plan.dataset_version)
        return self.compute_adapter.rows_from_parquet(parquet_path)

    def _persist_index_rebuild(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> ObjectIndexRebuildCounts:
        objects_upserted = 0
        for row in rows:
            self._index_object_row(conn, ctx, plan.object_type, row, plan.source_dataset_version_id)
            objects_upserted += 1
        links_upserted = self._index_links_for_object_type(
            conn,
            ctx,
            plan.object_type,
            rows,
            plan.source_dataset_version_id,
        )
        counts = ObjectIndexRebuildCounts(len(rows), objects_upserted, links_upserted)
        self._mark_index_run_succeeded(
            conn, ctx, plan.run_id, counts.rows_read, counts.objects_upserted, counts.links_upserted
        )
        self._audit_index_rebuild(conn, ctx, plan, counts)
        return counts

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

    def _index_object_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        row: TabularRow,
        source_dataset_version_id: str,
    ) -> None:
        source = self._source_row_from_dataset_row(conn, object_type, row)
        existing = self.object_records_service._object_record(conn, ctx, object_type["api_name"], source.object_id)
        if existing is None:
            self._insert_new_object_record(conn, ctx, object_type, source, source_dataset_version_id)
        else:
            self._refresh_existing_object_record(conn, ctx, object_type, source, existing, source_dataset_version_id)
        self._emit_object_changed(conn, ctx, object_type["api_name"], source.object_id, source_dataset_version_id)

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
                raise ValidationFailed(
                    "dataset-backed property column missing",
                    details={"property": prop["api_name"]},
                )
            base_patch[prop["api_name"]] = row.get(column_name)
        return base_patch

    def _insert_new_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        source: ObjectIndexSourceRow,
        source_dataset_version_id: str,
    ) -> None:
        base_patch = dict(source.base_patch)
        current = self._merge_properties(conn, object_type["id"], base_patch, {})
        current_properties = dict(current)
        now = _now()
        self.object_index_repository.insert_object_record(
            transaction=conn,
            record=ObjectRecordInsert(
                record_id=_new_id("obj"),
                tenant_id=ctx.tenant_id,
                object_type_id=object_type["id"],
                object_type_api_name=object_type["api_name"],
                object_id=source.object_id,
                properties=current_properties,
                base_properties=base_patch,
                edit_properties={},
                property_versions={key: 1 for key in current_properties},
                source_dataset_version_id=source_dataset_version_id,
                source_hash=_json_hash(base_patch),
                object_version=1,
                deleted=False,
                deletion_reason=None,
                created_at=now,
                updated_at=now,
            ),
        )

    def _refresh_existing_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        source: ObjectIndexSourceRow,
        existing: ObjectRecordRow,
        source_dataset_version_id: str,
    ) -> None:
        base_patch = dict(source.base_patch)
        self._record_conflicts_for_base_update(
            conn,
            ctx,
            object_type,
            source.object_id,
            existing,
            base_patch,
            source_dataset_version_id=source_dataset_version_id,
        )
        current = self._merge_properties(conn, object_type["id"], base_patch, existing["edit_properties"])
        self._update_object_record_from_source(conn, ctx, existing, current, base_patch, source_dataset_version_id)

    def _emit_object_changed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        source_dataset_version_id: str,
    ) -> None:
        self.runtime_service._outbox(
            conn,
            ctx,
            "object.changed",
            "object",
            f"{object_type_api_name}/{object_id}",
            {"objectType": object_type_api_name, "objectId": object_id},
            idempotency_key=f"{source_dataset_version_id}:{object_type_api_name}:{object_id}",
            correlation_id=source_dataset_version_id,
        )

    def _record_conflicts_for_base_update(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        object_id: str,
        existing: ObjectRecordRow,
        base_patch: ObjectPropertyMap,
        source_dataset_version_id: str,
    ) -> None:
        for prop in self.ontology_service._properties_for_object_type(conn, object_type["id"]):
            if prop["edit_policy"] != "conflict_requires_review":
                continue
            api_name = prop["api_name"]
            has_conflicting_edit = api_name in existing["edit_properties"] and existing["edit_properties"][
                api_name
            ] != base_patch.get(api_name)
            if has_conflicting_edit:
                self.object_index_repository.insert_object_conflict(
                    transaction=conn,
                    record=ObjectConflictRecord(
                        conflict_id=_new_id("conflict"),
                        tenant_id=ctx.tenant_id,
                        object_type_id=object_type["id"],
                        object_id=object_id,
                        property_api_name=api_name,
                        source_value=base_patch.get(api_name),
                        edit_value=existing["edit_properties"][api_name],
                        source_dataset_version_id=source_dataset_version_id,
                        edit_id=None,
                        status="open",
                        created_at=_now(),
                    ),
                )

    def _merge_properties(
        self,
        conn: TransactionContext,
        object_type_id: str,
        base: ObjectPropertyMap,
        edits: ObjectPropertyMap,
    ) -> ObjectPropertyMap:
        current: dict[str, object] = {}
        for prop in self.ontology_service._properties_for_object_type(conn, object_type_id):
            name = prop["api_name"]
            policy = prop["edit_policy"]
            if policy == "edit_only":
                if name in edits:
                    current[name] = edits[name]
            elif policy in {"edit_wins", "conflict_requires_review"}:
                current[name] = edits[name] if name in edits else base.get(name)
            elif policy == "source_wins":
                current[name] = base[name] if name in base else edits.get(name)
            else:
                current[name] = base.get(name)
        return current

    def _index_links_for_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        rows: Sequence[TabularRow],
        source_dataset_version_id: str,
    ) -> int:
        active = self.ontology_service._active_ontology_version(conn, ctx)
        links = self.object_index_repository.link_types_for_object_type(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            from_object_type_id=object_type["id"],
        )
        count = 0
        for link in links:
            for row in rows:
                count += self._index_link_row(conn, ctx, link, row, source_dataset_version_id)
        return count

    def _index_link_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        link: LinkTypeRow,
        row: TabularRow,
        source_dataset_version_id: str,
    ) -> int:
        from_id = row.get(link["backing"]["fromKey"])
        to_id = row.get(link["backing"]["toKey"])
        if from_id in {None, ""} or to_id in {None, ""}:
            return 0
        existing = self.object_index_repository.object_link(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            link_type_id=link["id"],
            from_object_id=str(from_id),
            to_object_id=str(to_id),
        )
        if existing:
            self.object_index_repository.refresh_object_link(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                link_id=existing["id"],
                link_version=existing["link_version"] + 1,
                source_dataset_version_id=source_dataset_version_id,
                updated_at=_now(),
            )
        else:
            self.object_index_repository.insert_object_link(
                transaction=conn,
                record=build_object_link_insert(
                    ctx,
                    link,
                    str(from_id),
                    str(to_id),
                    source_dataset_version_id,
                ),
            )
        return 1

    def _mark_index_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        rows_read: int,
        objects_upserted: int,
        links_upserted: int,
    ) -> None:
        """Persist successful index run counters within the current tenant."""
        self.object_index_repository.mark_index_run_succeeded(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            rows_read=rows_read,
            objects_upserted=objects_upserted,
            links_upserted=links_upserted,
            cursor={"last_row": rows_read},
            completed_at=_now(),
        )

    def _mark_index_run_failed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        exc: Exception,
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
                adapter="compute_adapter.rows_from_parquet",
            ),
            completed_at=_now(),
        )

    def _update_object_record_from_source(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: ObjectRecordRow,
        properties: ObjectPropertyMap,
        base_properties: ObjectPropertyMap,
        source_dataset_version_id: str,
    ) -> None:
        """Apply a source dataset refresh to an existing object record."""
        current_properties = dict(properties)
        current_base_properties = dict(base_properties)
        self.object_index_repository.update_object_record_from_source(
            transaction=conn,
            record=ObjectRecordSourceUpdate(
                record_id=existing["id"],
                tenant_id=ctx.tenant_id,
                properties=current_properties,
                base_properties=current_base_properties,
                source_dataset_version_id=source_dataset_version_id,
                source_hash=_json_hash(current_base_properties),
                object_version=existing["object_version"] + 1,
                updated_at=_now(),
            ),
        )
