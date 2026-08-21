"""Object-anchored unified search surface for the consumer Ontology MCP.

Kept beside the gateway rather than inside it: the gateway is already at the application
module size ceiling, and this is a self-contained boundary — one runtime protocol and the
payload shape an external agent receives.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from foundry_lite.application.services.ontology_mcp_contracts import OntologyMcpObjectRuntime
from foundry_lite.application.services.ontology_mcp_values import (
    bounded_int,
    optional_mapping,
    optional_text,
    text,
    text_list,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]
ObjectContext = Callable[[RequestContext, str], RequestContext]
RequireObjectRead = Callable[[RequestContext, str], None]


class OntologyMcpUnifiedSearchRuntime(Protocol):
    """Object-anchored search across an object type and the media bound to it."""

    def unified_search(
        self,
        ctx: RequestContext,
        *,
        query_text: str,
        object_type: str,
        filters: Mapping[str, object] | None = None,
        limit: int = 20,
    ) -> Sequence[object]: ...


def execute_object_tool(
    *,
    objects: OntologyMcpObjectRuntime,
    unified_search: OntologyMcpUnifiedSearchRuntime,
    require_object_read: RequireObjectRead,
    object_context: ObjectContext,
    ctx: RequestContext,
    name: str,
    operation: str,
    arguments: JsonObject,
) -> Mapping[str, object]:
    require_object_read(ctx, name)
    read_ctx = object_context(ctx, name)
    return _object_operation(objects, unified_search, read_ctx, name, operation, arguments)


def _object_operation(
    objects: OntologyMcpObjectRuntime,
    unified_search: OntologyMcpUnifiedSearchRuntime,
    ctx: RequestContext,
    name: str,
    operation: str,
    arguments: JsonObject,
) -> Mapping[str, object]:
    if operation == "get":
        return objects.get(name, text(arguments, "objectId"), ctx=ctx)
    if operation == "search":
        return _search_objects(objects, ctx, name, arguments)
    if operation == "links":
        return _object_links(objects, ctx, name, arguments)
    if operation == "searchAround":
        return _search_around(objects, ctx, name, arguments)
    if operation == "unifiedSearch":
        return _unified_search(unified_search, ctx, name, arguments)
    raise ValidationFailed("unsupported Ontology MCP object operation")


def _search_objects(
    objects: OntologyMcpObjectRuntime,
    ctx: RequestContext,
    name: str,
    arguments: JsonObject,
) -> Mapping[str, object]:
    return objects.query(
        name,
        ctx=ctx,
        filter_ast=optional_mapping(arguments.get("filter")),
        limit=bounded_int(arguments.get("limit"), 20, 1, 50),
        cursor=optional_text(arguments.get("cursor")),
        search_text=optional_text(arguments.get("search")),
        semantic_text=optional_text(arguments.get("semanticText")),
    )


def _object_links(
    objects: OntologyMcpObjectRuntime,
    ctx: RequestContext,
    name: str,
    arguments: JsonObject,
) -> Mapping[str, object]:
    link_type = text(arguments, "linkType")
    links = objects.links(name, text(arguments, "objectId"), link_type, ctx=ctx)
    return {"linkType": link_type, "links": [dict(link) for link in links]}


def _search_around(
    objects: OntologyMcpObjectRuntime,
    ctx: RequestContext,
    name: str,
    arguments: JsonObject,
) -> Mapping[str, object]:
    return objects.search_around(
        name,
        text_list(arguments, "linkTypes"),
        ctx=ctx,
        filter_ast=optional_mapping(arguments.get("filter")),
    )


def _unified_search(
    unified_search: OntologyMcpUnifiedSearchRuntime,
    ctx: RequestContext,
    name: str,
    arguments: JsonObject,
) -> Mapping[str, object]:
    hits = unified_search.unified_search(
        ctx,
        query_text=text(arguments, "query"),
        object_type=name,
        filters=optional_mapping(arguments.get("filter")),
        limit=bounded_int(arguments.get("limit"), 20, 1, 50),
    )
    return {"hits": [_unified_hit_payload(hit) for hit in hits]}


def _unified_hit_payload(hit: object) -> dict[str, object]:
    """Serialize one object-anchored hit, keeping the citation that lifted it.

    An agent that cannot see why an object matched will either re-search or assert the object
    is relevant without grounds, so the media citation travels with the object rather than
    being dropped in favour of a bare score.
    """
    citations = getattr(hit, "media_citations", ())
    return {
        "objectType": getattr(hit, "object_type", ""),
        "objectId": getattr(hit, "object_id", ""),
        "objectVersion": getattr(hit, "object_version", 0),
        "properties": getattr(hit, "properties", {}),
        "score": getattr(hit, "score", 0.0),
        "hasOwnTextMatch": getattr(hit, "has_own_text_match", False),
        "mediaCitations": [
            {
                "contentUnitId": citation.content_unit_id,
                "sourceMediaItemVersionId": citation.source_media_item_version_id,
                "propertyName": citation.property_name,
                "indexGeneration": citation.index_generation,
            }
            for citation in citations
        ],
    }
