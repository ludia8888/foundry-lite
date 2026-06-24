from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    ObjectIndexCdcResult,
    ObjectIndexRebuildResult,
    ObjectIndexShadowRebuildResult,
    ObjectLinkPayload,
    ObjectPayload,
    ObjectQueryResult,
    ObjectSetPayload,
    ObjectSetQueryResult,
)
from foundry_lite.application.services.object_service import ObjectServices
from foundry_lite.application.services.ontology_search import OntologySearchService, UnifiedSearchHit
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class ObjectStore:
    """Object bounded context: read, query, link, set, index, and search ontology objects."""

    def __init__(self, objects: ObjectServices, ontology_search: OntologySearchService) -> None:
        self._objects = objects
        self._ontology_search = ontology_search

    def unified_search(
        self,
        ctx: RequestContext,
        *,
        query_text: str,
        object_type: str,
        filters: Mapping[str, object] | None = None,
        limit: int = 20,
    ) -> list[UnifiedSearchHit]:
        """OAG unified search: ranked ontology objects fusing own + bound-media matches (L12b)."""
        return self._ontology_search.unified_search(
            ctx, query_text=query_text, object_type=object_type, filters=filters, limit=limit
        )

    def reindex(self, object_type_api_name: str, *, ctx: RequestContext | None = None) -> ObjectIndexRebuildResult:
        return self._objects.indexing.index_rebuild(object_type_api_name, ctx=ctx)

    def shadow_reindex(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        expected_count: int | None = None,
        expected_hash: str | None = None,
    ) -> ObjectIndexShadowRebuildResult:
        return self._objects.indexing.index_shadow_rebuild(
            object_type_api_name,
            ctx=ctx,
            expected_count=expected_count,
            expected_hash=expected_hash,
        )

    def replay_index_run(self, index_run_id: str, *, ctx: RequestContext | None = None) -> ObjectIndexRebuildResult:
        return self._objects.indexing.index_replay_run(index_run_id, ctx=ctx)

    def index_cdc_events(
        self,
        object_type_api_name: str,
        events: Sequence[Mapping[str, object]],
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexCdcResult:
        return self._objects.indexing.index_cdc_events(object_type_api_name, events, ctx=ctx)

    def rebuild_search(self, object_type_api_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._objects.search.index_search_rebuild(object_type_api_name, ctx=ctx)

    def consume_search_change(
        self, object_type_api_name: str, object_id: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._objects.search.index_search_object_changed(object_type_api_name, object_id, ctx=ctx)

    def links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[ObjectLinkPayload]:
        return self._objects.links.get_links(object_type_api_name, object_id, link_type_api_name, ctx=ctx)

    def get(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload:
        return self._objects.query.get_object(
            object_type_api_name,
            object_id,
            ctx=ctx,
            include_explain=include_explain,
        )

    def query(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        search_text: str | None = None,
        semantic_text: str | None = None,
    ) -> ObjectQueryResult:
        return self._objects.query.query_objects(
            object_type_api_name,
            ctx=ctx,
            filter_ast=filter_ast,
            order_by=order_by,
            limit=limit,
            cursor=cursor,
            search_text=search_text,
            semantic_text=semantic_text,
        )

    def create_set(
        self,
        name: str,
        object_type_api_name: str,
        *,
        set_type: str,
        ctx: RequestContext | None = None,
        definition: Mapping[str, object] | None = None,
        object_ids: list[str] | None = None,
        filter_ast: Mapping[str, object] | None = None,
        visibility: str = "private",
        ttl_seconds: int | None = None,
    ) -> ObjectSetPayload:
        return self._objects.sets.create_object_set(
            name,
            object_type_api_name,
            set_type=set_type,
            ctx=ctx,
            definition=definition,
            object_ids=object_ids,
            filter_ast=filter_ast,
            visibility=visibility,
            ttl_seconds=ttl_seconds,
        )

    def get_set(
        self, set_id: str, *, ctx: RequestContext | None = None, include_items: bool = True
    ) -> ObjectSetPayload:
        return self._objects.sets.get_object_set(set_id, ctx=ctx, include_items=include_items)

    def query_sets(
        self, *, ctx: RequestContext | None = None, object_type_api_name: str | None = None
    ) -> ObjectSetQueryResult:
        return self._objects.sets.query_object_sets(ctx=ctx, object_type_api_name=object_type_api_name)

    def cleanup_expired_sets(self, *, ctx: RequestContext | None = None) -> dict[str, int]:
        return self._objects.sets.cleanup_expired_object_sets(ctx=ctx)
