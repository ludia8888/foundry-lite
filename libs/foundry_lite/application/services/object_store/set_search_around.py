"""Parsing and response shaping for bounded ObjectSet search-around traversal."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import ObjectSetRow
from foundry_lite.application.services.object_store.set_members import (
    MAX_SEARCH_AROUND_HOPS,
    collect_dynamic_object_set_members,
    search_around_next_object_type,
)
from foundry_lite.application.services.object_store.set_protocols import SetObjectQuery
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

SearchAroundHops = Sequence[Mapping[str, object]]
RequireLinkRead = Callable[[str], None]


def search_around_parts(search_around: object) -> tuple[str, Mapping[str, object], SearchAroundHops]:
    """Parse the stored traversal definition and enforce its bounded hop shape."""
    if not isinstance(search_around, Mapping):
        raise ValidationFailed("searchAround must be an object")
    source = _search_around_source(search_around)
    return _source_type(source), _source_filter(source), _search_around_hops(search_around)


def resolve_search_around_result_type(
    source_type: str,
    hops: SearchAroundHops,
    link_type_rows: Mapping[str, Mapping[str, object]],
    require_link_read: RequireLinkRead,
) -> str:
    result_type = source_type
    for index, hop in enumerate(hops):
        link_api = hop.get("link")
        if not isinstance(link_api, str) or not link_api:
            raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
        link_type = link_type_rows.get(link_api)
        if link_type is None:
            raise ValidationFailed("search-around link type not found", details={"linkType": link_api})
        require_link_read(link_api)
        result_type = search_around_next_object_type(link_type, result_type)
    return result_type


def search_around_source_ids(
    object_query_service: SetObjectQuery,
    ctx: RequestContext,
    source_type: str,
    filter_ast: Mapping[str, object],
) -> list[str]:
    object_ids, _ = collect_dynamic_object_set_members(
        object_query_service,
        source_type,
        ctx=ctx,
        filter_ast=filter_ast,
        include_items=False,
    )
    return object_ids


def require_search_around_link_reads(hops: SearchAroundHops, require_link_read: RequireLinkRead) -> None:
    for index, hop in enumerate(hops):
        link_api = hop.get("link")
        if not isinstance(link_api, str) or not link_api:
            raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
        require_link_read(link_api)


def _search_around_source(search_around: Mapping[object, object]) -> Mapping[object, object]:
    source = search_around.get("from")
    if not isinstance(source, Mapping):
        raise ValidationFailed("searchAround requires a from set")
    return source


def _source_type(source: Mapping[object, object]) -> str:
    source_type = source.get("objectType")
    if not isinstance(source_type, str) or not source_type:
        raise ValidationFailed("searchAround from requires an objectType")
    return source_type


def _source_filter(source: Mapping[object, object]) -> Mapping[str, object]:
    filter_ast = source.get("filter") or {}
    if not isinstance(filter_ast, Mapping):
        raise ValidationFailed("searchAround from filter must be an object")
    return cast(Mapping[str, object], filter_ast)


def _search_around_hops(search_around: Mapping[object, object]) -> SearchAroundHops:
    hops = search_around.get("hops")
    if not isinstance(hops, Sequence) or isinstance(hops, str) or not hops:
        raise ValidationFailed("searchAround requires at least one hop")
    if len(hops) > MAX_SEARCH_AROUND_HOPS:
        raise ValidationFailed(
            "search-around chain is too long",
            details={"hops": len(hops), "maxHops": MAX_SEARCH_AROUND_HOPS},
        )
    if not all(isinstance(hop, Mapping) for hop in hops):
        raise ValidationFailed("searchAround hop must be an object")
    return [cast(Mapping[str, object], hop) for hop in hops]


def transient_search_around_row(
    from_object_type_api_name: str,
    filter_ast: Mapping[str, object] | None,
    hops: SearchAroundHops,
) -> ObjectSetRow:
    return cast(
        ObjectSetRow,
        {
            "definition": {
                "searchAround": {
                    "from": {"objectType": from_object_type_api_name, "filter": dict(filter_ast or {})},
                    "hops": list(hops),
                }
            }
        },
    )


def search_around_payload(
    result_type: str,
    from_object_type_api_name: str,
    link_types: Sequence[str],
    object_ids: Sequence[str],
    items: Sequence[object],
    include_items: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "objectType": result_type,
        "fromObjectType": from_object_type_api_name,
        "linkTypes": list(link_types),
        "objectIds": list(object_ids),
        "count": len(object_ids),
    }
    if include_items:
        payload["items"] = list(items)
    return payload
