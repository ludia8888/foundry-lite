from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    LinkTypeRow,
    ObjectConflictRecord,
    ObjectIndexLinkRow,
    ObjectIndexRepository,
    ObjectPropertyMap,
    ObjectRecordInsert,
    ObjectRecordRow,
    ObjectRecordSourceDeletion,
    ObjectRecordSourceUpdate,
    ObjectTypeRow,
    PropertyTypeRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.object_store.index_records import build_object_link_insert
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
    ObjectIndexSourceRow,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class ObjectIndexingRebuildMixin:
    object_index_repository: ObjectIndexRepository
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    def _persist_index_rebuild(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> ObjectIndexRebuildCounts:
        objects_upserted = 0
        source_object_ids: set[str] = set()
        for row in rows:
            object_id = self._index_object_row(conn, ctx, plan, row)
            source_object_ids.add(object_id)
            objects_upserted += 1
        objects_deleted = self._delete_missing_source_records(conn, ctx, plan, source_object_ids)
        links_upserted = self._index_links_for_object_type(conn, ctx, plan, rows)
        return ObjectIndexRebuildCounts(len(rows), objects_upserted, objects_deleted, links_upserted)

    def _index_object_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        row: TabularRow,
    ) -> str:
        source = self._source_row_from_dataset_row(conn, plan.object_type, row)
        existing = self.object_index_repository.object_record_in_index(
            transaction=conn,
            tenant_id=ctx.tenant_id,
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
        if changed and plan.mode == "full":
            self._emit_object_changed(
                conn,
                ctx,
                plan.object_type["api_name"],
                source.object_id,
                plan.source_dataset_version_id,
            )
        return source.object_id

    def _delete_missing_source_records(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        source_object_ids: set[str],
    ) -> int:
        if plan.mode != "full":
            return 0
        records = self.object_index_repository.object_records_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=plan.object_type["id"],
            index_version=plan.index_version,
        )
        deleted_count = 0
        for record in records:
            if record["deleted"] or str(record["object_id"]) in source_object_ids:
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
                deletion_reason="source_missing",
                updated_at=_now(),
            ),
        )
        self._emit_object_changed(conn, ctx, plan.object_type["api_name"], object_id, plan.source_dataset_version_id)

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
        raise NotImplementedError

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
        current = self._merge_properties(conn, plan.object_type["id"], base_patch, edit_properties)
        current_properties = dict(current)
        now = _now()
        self.object_index_repository.insert_object_record(
            transaction=conn,
            record=ObjectRecordInsert(
                record_id=_new_id("obj"),
                tenant_id=ctx.tenant_id,
                object_type_id=plan.object_type["id"],
                object_type_api_name=plan.object_type["api_name"],
                object_id=source.object_id,
                properties=current_properties,
                base_properties=base_patch,
                edit_properties=edit_properties,
                property_versions=property_versions or {key: 1 for key in current_properties},
                source_dataset_version_id=plan.source_dataset_version_id,
                source_hash=_json_hash(base_patch),
                object_version=active["object_version"] if active is not None else 1,
                deleted=False,
                deletion_reason=None,
                created_at=now,
                updated_at=now,
                index_version=plan.index_version,
                is_active=plan.mode == "full",
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
        current = self._merge_properties(conn, plan.object_type["id"], base_patch, edit_source["edit_properties"])
        if self._reindex_is_noop(plan, existing, base_patch, current):
            return False
        self._record_conflicts_for_base_update(
            conn,
            ctx,
            plan.object_type,
            source.object_id,
            edit_source,
            base_patch,
            source_dataset_version_id=plan.source_dataset_version_id,
        )
        self._update_object_record_from_source(conn, ctx, existing, current, base_patch, plan.source_dataset_version_id)
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
            self._insert_conflict_if_needed(
                conn,
                ctx,
                object_type,
                object_id,
                existing,
                base_patch,
                prop,
                source_dataset_version_id,
            )

    def _insert_conflict_if_needed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        object_id: str,
        existing: ObjectRecordRow,
        base_patch: ObjectPropertyMap,
        prop: PropertyTypeRow,
        source_dataset_version_id: str,
    ) -> None:
        api_name = prop["api_name"]
        if api_name not in existing["edit_properties"]:
            return
        if existing["edit_properties"][api_name] == base_patch.get(api_name):
            return
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
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> int:
        active = self.ontology_service._active_ontology_version(conn, ctx)
        links = self.object_index_repository.link_types_for_object_type(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            from_object_type_id=plan.object_type["id"],
        )
        return sum(self._index_link_row(conn, ctx, plan, link, row) for link in links for row in rows)

    def _index_link_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
        row: TabularRow,
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
            index_version=plan.index_version,
        )
        if existing:
            self._refresh_existing_link(conn, ctx, plan, existing)
            return 1
        self._insert_new_link(conn, ctx, plan, link, str(from_id), str(to_id))
        return 1

    def _refresh_existing_link(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        existing: ObjectIndexLinkRow,
    ) -> None:
        self.object_index_repository.refresh_object_link(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            link_id=existing["id"],
            link_version=existing["link_version"] + 1,
            source_dataset_version_id=plan.source_dataset_version_id,
            updated_at=_now(),
        )

    def _insert_new_link(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
        from_id: str,
        to_id: str,
    ) -> None:
        self.object_index_repository.insert_object_link(
            transaction=conn,
            record=build_object_link_insert(
                ctx,
                link,
                from_id,
                to_id,
                plan.source_dataset_version_id,
                index_version=plan.index_version,
                is_active=plan.mode == "full",
            ),
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
