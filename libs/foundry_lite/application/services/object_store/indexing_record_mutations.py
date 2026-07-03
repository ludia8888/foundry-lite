"""Shared object-index record mutation and evidence service."""

from __future__ import annotations

from foundry_lite.application.ports import (
    IndexRunCursor,
    IndexRunSourceRef,
    ObjectConflictRecord,
    ObjectIndexRepository,
    ObjectPropertyMap,
    ObjectRecordRow,
    ObjectRecordSourceUpdate,
    ObjectTypeRow,
    PropertyTypeRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_types import ObjectIndexCdcCounts, ObjectIndexRebuildCounts
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.domain.object_store.indexing import should_record_base_update_conflict


class ObjectIndexRecordMutationService(CoreService):
    """Owns shared object-index state transitions and durable evidence writes."""

    required_dependencies = ("object_index_repository",)
    required_collaborators = ("ontology_service", "runtime_service")
    object_index_repository: ObjectIndexRepository
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    def record_conflicts_for_base_update(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        object_id: str,
        existing: ObjectRecordRow,
        base_patch: ObjectPropertyMap,
        source_dataset_version_id: str,
    ) -> None:
        properties = self.ontology_service._properties_for_object_type(conn, object_type["id"])
        for prop in properties:
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

    def merge_properties(
        self,
        conn: TransactionContext,
        object_type_id: str,
        base: ObjectPropertyMap,
        edits: ObjectPropertyMap,
    ) -> ObjectPropertyMap:
        current: dict[str, object] = {}
        for prop in self.ontology_service._properties_for_object_type(conn, object_type_id):
            _merge_property(current, prop, base, edits)
        return current

    def _merge_properties(
        self,
        conn: TransactionContext,
        object_type_id: str,
        base: ObjectPropertyMap,
        edits: ObjectPropertyMap,
    ) -> ObjectPropertyMap:
        return self.merge_properties(conn, object_type_id, base, edits)

    def update_object_record_from_source(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: ObjectRecordRow,
        properties: ObjectPropertyMap,
        base_properties: ObjectPropertyMap,
        source_dataset_version_id: str,
    ) -> None:
        base_patch = dict(base_properties)
        self.object_index_repository.update_object_record_from_source(
            transaction=conn,
            record=ObjectRecordSourceUpdate(
                record_id=existing["id"],
                tenant_id=ctx.tenant_id,
                properties=dict(properties),
                base_properties=base_patch,
                source_dataset_version_id=source_dataset_version_id,
                source_hash=_json_hash(base_patch),
                object_version=existing["object_version"] + 1,
                updated_at=_now(),
            ),
        )

    def emit_object_changed(
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

    def mark_index_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        counts: ObjectIndexRebuildCounts | ObjectIndexCdcCounts,
        cursor: IndexRunCursor | None = None,
        source_ref_updates: IndexRunSourceRef | None = None,
    ) -> None:
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
            source_ref_updates=source_ref_updates,
        )
        if not updated:
            raise ConflictDetected("index run terminal state changed concurrently", details={"run_id": run_id})

    def mark_index_run_failed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        exc: Exception,
        adapter: str = "compute_adapter.rows_from_parquet",
    ) -> None:
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
        if not should_record_base_update_conflict(
            edit_policy=prop["edit_policy"],
            property_api_name=api_name,
            edit_properties=existing["edit_properties"],
            source_value=base_patch.get(api_name),
        ):
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


def _merge_property(
    current: dict[str, object],
    prop: PropertyTypeRow,
    base: ObjectPropertyMap,
    edits: ObjectPropertyMap,
) -> None:
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
