"""Object search projection service built from ontology-backed object records."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import (
    ObjectQueryItem,
    ObjectQueryResult,
    ObjectRecordRow,
    ObjectTypeRow,
    PropertyTypeRow,
    TransactionContext,
)
from foundry_lite.application.ports.search_adapter import (
    SearchHit,
    SearchIndexAdapter,
    SearchIndexMapping,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.row_policies import row_policy_scope, row_visible
from foundry_lite.application.services.object_store.search_projection_context import (
    search_projection_context as projection_ctx,
)
from foundry_lite.application.services.object_store.search_runs import (
    fail_search_index_run,
    start_search_index_run,
    succeed_search_object_change,
    succeed_search_rebuild,
)
from foundry_lite.application.services.object_store.semantic_search import (
    EmbeddingModelAdapter,
    keyword_search_query,
    require_semantic_model,
    search_document,
    search_limit,
    semantic_search_query,
)
from foundry_lite.application.services.object_store.serving_contract import record_scope_object_type_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.security.policy import PolicyService


class ObjectSearchOntologyLookup(Protocol):
    """Ontology collaborator surface needed to derive search index mappings."""

    def _active_object_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> ObjectTypeRow: ...

    def _properties_for_object_type(
        self, conn: TransactionContext, object_type_id: str
    ) -> Sequence[PropertyTypeRow]: ...


class ObjectSearchRuntimeBoundary(Protocol):
    """Runtime collaborator surface for permission checks and audit evidence."""

    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _require_or_audit(self, ctx: RequestContext, permission: str, resource_type: str, resource_id: str) -> None: ...

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> Mapping[str, object]: ...

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


class ObjectSearchService(CoreService):
    """Keep object search as a rebuildable projection, not the source of truth."""

    required_dependencies = (
        "engine",
        "policy",
        "object_index_repository",
        "object_read_repository",
        "search_adapter",
        "embedding_model_adapter",
    )
    required_collaborators = ("ontology_service", "runtime_service")
    ontology_service: ObjectSearchOntologyLookup
    runtime_service: ObjectSearchRuntimeBoundary
    search_adapter: SearchIndexAdapter
    embedding_model_adapter: EmbeddingModelAdapter

    def search_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext,
        search_text: str,
        limit: int,
    ) -> ObjectQueryResult:
        """Search via the adapter, then re-read active objects for policy masking."""
        self.policy.require(ctx, "object:read")
        query_limit = search_limit(limit)
        mapping = self._search_mapping(ctx, object_type_api_name)
        _require_searchable_properties(mapping)
        hits = self.search_adapter.search(
            keyword_search_query(ctx, object_type_api_name, search_text, mapping, query_limit)
        )
        items = self._items_for_hits(ctx, object_type_api_name, hits)
        return {"items": items, "nextCursor": None}

    def search_objects_semantic(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext,
        semantic_text: str,
        limit: int,
    ) -> ObjectQueryResult:
        """nearestNeighbors object search: embed the query, rank by model-pinned cosine.

        Returns a relevance-ordered Object Set; the adapter fails closed on a model mismatch.
        """
        self.policy.require(ctx, "object:read")
        require_semantic_model(self.embedding_model_adapter)
        query_limit = search_limit(limit)
        mapping = self._search_mapping(ctx, object_type_api_name)
        _require_searchable_properties(mapping)
        query = semantic_search_query(
            ctx, object_type_api_name, semantic_text, mapping, query_limit, self.embedding_model_adapter
        )
        hits = self.search_adapter.search(query)
        items = self._items_for_hits(ctx, object_type_api_name, hits)
        return {"items": items, "nextCursor": None}

    def index_search_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Rebuild all documents for one object type and report projection drift."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:index", "search_index", object_type_api_name)
        self.runtime_service._require_or_audit(ctx, "operations:retry", "search_index", object_type_api_name)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_search_rebuild",
            resource_type="search_index",
            resource_id=object_type_api_name,
        )
        object_type, mapping = self._search_object_type_and_mapping(projection_ctx(ctx), object_type_api_name)
        run_id = self._start_search_run(ctx, object_type, object_type_api_name, "search_rebuild")
        try:
            rows = self._active_rows(ctx, object_type_api_name, object_type)
            self.search_adapter.configure_index(mapping)
            for row in rows:
                self.search_adapter.upsert_document(
                    search_document(ctx, object_type_api_name, row, mapping, self.embedding_model_adapter)
                )
            result = self._consistency_result(ctx, object_type_api_name, rows)
        except Exception as exc:
            self._fail_search_run(ctx, run_id, object_type_api_name, "rebuild_search_index", exc)
            raise
        succeed_search_rebuild(self.engine, self.object_index_repository, ctx, run_id, result)
        result = {**result, "indexRunId": run_id}
        self._audit_search_index(
            ctx,
            object_type_api_name,
            "object.search.rebuilt",
            "rebuild_search_index",
            result,
            run_id,
        )
        return result

    def index_search_object_changed(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Apply one object.changed-style update to the search projection."""
        ctx = ctx or RequestContext()
        resource_id = f"{object_type_api_name}/{object_id}"
        self.runtime_service._require_or_audit(ctx, "object:index", "search_index", resource_id)
        self.runtime_service._require_or_audit(ctx, "operations:retry", "search_index", resource_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_search_object_changed",
            resource_type="search_index",
            resource_id=resource_id,
        )
        object_type, mapping = self._search_object_type_and_mapping(projection_ctx(ctx), object_type_api_name)
        run_id = self._start_search_run(ctx, object_type, object_type_api_name, "search_object_changed", object_id)
        try:
            record = self._object_record(ctx, object_type_api_name, object_id)
            self.search_adapter.configure_index(mapping)
            result = self._sync_changed_record(ctx, object_type_api_name, object_id, record, mapping)
        except Exception as exc:
            self._fail_search_run(ctx, run_id, resource_id, "consume_object_changed", exc)
            raise
        succeed_search_object_change(self.engine, self.object_index_repository, ctx, run_id, result)
        result = {**result, "indexRunId": run_id}
        self._audit_search_index(ctx, resource_id, "object.search.updated", "consume_object_changed", result, run_id)
        return result

    def _search_object_type_and_mapping(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
    ) -> tuple[ObjectTypeRow, SearchIndexMapping]:
        """Build search mapping while keeping the object type id for run evidence."""

        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            properties = self.ontology_service._properties_for_object_type(conn, object_type["id"])
        mapping = _search_mapping(ctx, object_type_api_name, properties, self.policy.masked_property_names)
        return object_type, mapping

    def _start_search_run(
        self,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        object_type_api_name: str,
        trigger_type: str,
        object_id: str | None = None,
    ) -> str:
        return start_search_index_run(
            self.engine,
            self.object_index_repository,
            self.search_adapter.profile_name,
            ctx,
            object_type,
            object_type_api_name,
            trigger_type,
            object_id,
        )

    def _fail_search_run(
        self,
        ctx: RequestContext,
        run_id: str,
        resource_id: str,
        action: str,
        exc: Exception,
    ) -> None:
        fail_search_index_run(
            self.engine,
            self.object_index_repository,
            self.runtime_service,
            self.search_adapter.profile_name,
            ctx,
            run_id,
            resource_id,
            action,
            exc,
        )

    def _items_for_hits(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        hits: Sequence[SearchHit],
    ) -> list[ObjectQueryItem]:
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            object_type_id = record_scope_object_type_id(object_type)
            # Search hits re-read source rows, so row policies filter here: a
            # projection hit must never surface a row the caller cannot query.
            scope = row_policy_scope(object_type, ctx.roles)
            rows = [
                (hit, self._record_if_active(conn, ctx, object_type_api_name, hit.document_id, object_type_id))
                for hit in hits
            ]
        return [
            _object_query_item(
                ctx,
                self.policy,
                object_type_api_name,
                row,
                search_projection_version=hit.document.version,
            )
            for hit, row in rows
            if row is not None and row_visible(scope, row["properties"])
        ]

    def _record_if_active(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None,
    ) -> ObjectRecordRow | None:
        record = self.object_read_repository.object_record(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type_api_name,
            object_id=object_id,
            object_type_id=object_type_id,
        )
        return None if record is None or record["deleted"] else record

    def _active_rows(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        object_type: ObjectTypeRow,
    ) -> list[ObjectRecordRow]:
        with self.engine.begin() as conn:
            return self.object_read_repository.active_object_rows(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=object_type_api_name,
                object_type_id=record_scope_object_type_id(object_type),
            )

    def _object_record(self, ctx: RequestContext, object_type_api_name: str, object_id: str) -> ObjectRecordRow | None:
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            return self.object_read_repository.object_record(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=object_type_api_name,
                object_id=object_id,
                object_type_id=record_scope_object_type_id(object_type),
            )

    def _search_mapping(self, ctx: RequestContext, object_type_api_name: str) -> SearchIndexMapping:
        """Build the adapter mapping from active ontology property metadata."""

        return self._search_object_type_and_mapping(ctx, object_type_api_name)[1]

    def _sync_changed_record(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        record: ObjectRecordRow | None,
        mapping: SearchIndexMapping,
    ) -> dict[str, object]:
        """Upsert or delete a projection document based on the source row state."""

        if record is None or record["deleted"]:
            self.search_adapter.delete_document(
                tenant_id=ctx.tenant_id,
                object_type=object_type_api_name,
                document_id=object_id,
            )
            return {"objectType": object_type_api_name, "objectId": object_id, "status": "deleted"}
        self.search_adapter.upsert_document(
            search_document(ctx, object_type_api_name, record, mapping, self.embedding_model_adapter)
        )
        return {"objectType": object_type_api_name, "objectId": object_id, "status": "indexed"}

    def _consistency_result(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        rows: Sequence[ObjectRecordRow],
    ) -> dict[str, object]:
        """Compare object-store ids and search-index ids for rebuild evidence."""

        object_ids = {row["object_id"] for row in rows}
        search_ids = set(self.search_adapter.document_ids(tenant_id=ctx.tenant_id, object_type=object_type_api_name))
        missing = sorted(object_ids - search_ids)
        orphan = sorted(search_ids - object_ids)
        deleted_orphans = self._delete_orphan_documents(ctx, object_type_api_name, orphan)
        remaining_search_ids = search_ids - set(deleted_orphans)
        remaining_orphan = sorted(remaining_search_ids - object_ids)
        return {
            "objectType": object_type_api_name,
            "objectCount": len(object_ids),
            "searchCount": len(remaining_search_ids),
            "missingDocumentIds": missing,
            "orphanDocumentIds": orphan,
            "orphanDocumentsDeleted": deleted_orphans,
            "remainingOrphanDocumentIds": remaining_orphan,
            "status": "consistent" if not missing and not remaining_orphan else "drift_detected",
        }

    def _delete_orphan_documents(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        orphan_document_ids: Sequence[str],
    ) -> list[str]:
        deleted: list[str] = []
        for document_id in orphan_document_ids:
            self.search_adapter.delete_document(
                tenant_id=ctx.tenant_id,
                object_type=object_type_api_name,
                document_id=document_id,
            )
            deleted.append(document_id)
        return deleted

    def _audit_search_index(
        self,
        ctx: RequestContext,
        resource_id: str,
        event_type: str,
        action: str,
        result: Mapping[str, object],
        run_id: str | None = None,
    ) -> None:
        """Persist audit evidence for rebuild and object-change projection work."""

        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type=event_type,
                resource_type="search_index",
                resource_id=resource_id,
                action=action,
                after_ref=result,
                correlation_id=run_id,
            )


def _search_mapping(
    ctx: RequestContext,
    object_type_api_name: str,
    properties: Sequence[PropertyTypeRow],
    masked_property_names_for: Callable[[RequestContext, str], set[str]],
) -> SearchIndexMapping:
    """Translate ontology indexed/searchable flags into adapter fields."""

    masked_property_names = masked_property_names_for(ctx, object_type_api_name)
    return SearchIndexMapping(
        tenant_id=ctx.tenant_id,
        object_type=object_type_api_name,
        indexed_properties=tuple(
            prop["api_name"] for prop in properties if prop["indexed"] and prop["api_name"] not in masked_property_names
        ),
        searchable_properties=tuple(
            prop["api_name"]
            for prop in properties
            if prop["searchable"] and prop["api_name"] not in masked_property_names
        ),
    )


def _require_searchable_properties(mapping: SearchIndexMapping) -> None:
    """Reject full-text search on object types with no searchable properties."""

    if not mapping.searchable_properties:
        raise ValidationFailed("object type has no searchable properties", details={"object_type": mapping.object_type})


def _object_query_item(
    ctx: RequestContext,
    policy: PolicyService,
    object_type_api_name: str,
    row: ObjectRecordRow,
    *,
    search_projection_version: int | None = None,
) -> ObjectQueryItem:
    masked = policy.mask_properties(ctx, object_type_api_name, dict(row["properties"]))
    item: ObjectQueryItem = {
        "objectType": object_type_api_name,
        "objectId": row["object_id"],
        "objectVersion": row["object_version"],
        "properties": masked,
    }
    if search_projection_version is not None:
        item["searchProjectionVersion"] = search_projection_version
        item["searchProjectionStale"] = search_projection_version < row["object_version"]
    return item
