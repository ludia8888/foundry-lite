"""Application service helpers for set members workflows."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from foundry_lite.application.ports import ObjectRecordRow, TransactionContext
from foundry_lite.application.services.object_store.links import MAX_LINK_FANOUT
from foundry_lite.application.services.object_store.row_policies import RowPolicyScope, row_visible
from foundry_lite.application.services.object_store.set_protocols import SetLinkReader, SetObjectQuery
from foundry_lite.application.services.object_store.set_semantics import ObjectSetMembers
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed

OBJECT_SET_MEMBER_PAGE_LIMIT = 500


def collect_static_object_set_members(
    object_query_service: SetObjectQuery,
    object_type_api_name: str,
    *,
    ctx: RequestContext,
    object_ids: Sequence[str],
    records: Mapping[str, ObjectRecordRow],
    scope: RowPolicyScope,
    include_items: bool,
) -> ObjectSetMembers:
    """Materialize static set members under the caller's row policy scope.

    When the type is row-policy protected, hidden rows drop from BOTH ids and
    items so a static set cannot leak the existence of rows its reader may not
    see; unrestricted types keep the historical behavior of returning every
    declared id (even ones whose records are currently missing).
    """
    visible_ids = [oid for oid in object_ids if oid in records and row_visible(scope, records[oid]["properties"])]
    member_ids = list(object_ids) if scope.is_unrestricted else visible_ids
    if not include_items:
        return member_ids, []
    items = [object_query_service._object_query_item(ctx, object_type_api_name, records[oid]) for oid in visible_ids]
    return member_ids, items


def collect_dynamic_object_set_members(
    object_query_service: SetObjectQuery,
    object_type_api_name: str,
    *,
    ctx: RequestContext,
    filter_ast: Mapping[str, object],
    include_items: bool,
) -> ObjectSetMembers:
    object_ids: list[str] = []
    items = []
    cursor: str | None = None
    while True:
        result = object_query_service.query_objects(
            object_type_api_name,
            ctx=ctx,
            filter_ast=filter_ast,
            limit=OBJECT_SET_MEMBER_PAGE_LIMIT,
            cursor=cursor,
        )
        object_ids.extend(item["objectId"] for item in result["items"])
        if include_items:
            items.extend(result["items"])
        next_cursor = result["nextCursor"]
        if next_cursor is None:
            return object_ids, items
        if next_cursor == cursor:
            raise InvariantViolation("object set member cursor did not advance")
        cursor = next_cursor


# The public Function ObjectSet contract permits at most three Search Around operations. This
# local Object Storage v1-compatible execution also fails at 100k results rather than silently
# truncating. OSv2's larger distributed-compute limits require a separate execution backend.
MAX_SEARCH_AROUND_HOPS = 3
SEARCH_AROUND_RESULT_LIMIT = 100_000


def resolve_search_around_object_ids(
    link_reader: SetLinkReader,
    *,
    transaction: TransactionContext,
    tenant_id: str,
    from_object_type_api_name: str,
    from_object_ids: Sequence[str],
    hops: Sequence[Mapping[str, object]],
    link_types_by_api: Mapping[str, Mapping[str, object]],
) -> tuple[str, list[str]]:
    """Walk a link chain from a starting set and return (result object type, distinct ids).

    Traversal is a set-to-set operation, not a filter: each hop changes the object type, so the
    caller gets the new type back rather than having to infer it. That is the whole reason
    Palantir models this as `searchAround` instead of a predicate inside a filter.
    """
    current_type = from_object_type_api_name
    current_ids = list(dict.fromkeys(from_object_ids))
    for link_api, link_type in _validated_search_around_hops(hops, link_types_by_api):
        current_type, current_ids = _search_around_hop(
            link_reader,
            transaction=transaction,
            tenant_id=tenant_id,
            current_type=current_type,
            current_ids=current_ids,
            link_api=link_api,
            link_type=link_type,
        )
    return current_type, current_ids


def _validated_search_around_hops(
    hops: Sequence[Mapping[str, object]],
    link_types_by_api: Mapping[str, Mapping[str, object]],
) -> list[tuple[str, Mapping[str, object]]]:
    if not hops:
        raise ValidationFailed("search-around requires at least one link hop")
    if len(hops) > MAX_SEARCH_AROUND_HOPS:
        raise ValidationFailed(
            "search-around chain is too long",
            details={"hops": len(hops), "maxHops": MAX_SEARCH_AROUND_HOPS},
        )
    resolved: list[tuple[str, Mapping[str, object]]] = []
    for index, hop in enumerate(hops):
        link_api = hop.get("link")
        if not isinstance(link_api, str) or not link_api:
            raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
        link_type = link_types_by_api.get(link_api)
        if link_type is None:
            raise ValidationFailed("search-around link type not found", details={"linkType": link_api})
        resolved.append((link_api, link_type))
    return resolved


def search_around_next_object_type(link_type: Mapping[str, object], current_type: str) -> str:
    """Return the opposite side of a link, allowing Search Around from either endpoint."""
    if link_type["from_api_name"] == current_type:
        return str(link_type["to_api_name"])
    if link_type["to_api_name"] == current_type:
        return str(link_type["from_api_name"])
    raise ValidationFailed(
        "search-around link does not touch the current object type",
        details={
            "expected": current_type,
            "from": link_type["from_api_name"],
            "to": link_type["to_api_name"],
        },
    )


def _search_around_hop(
    link_reader: SetLinkReader,
    *,
    transaction: TransactionContext,
    tenant_id: str,
    current_type: str,
    current_ids: Sequence[str],
    link_api: str,
    link_type: Mapping[str, object],
) -> tuple[str, list[str]]:
    next_type = search_around_next_object_type(link_type, current_type)
    is_outgoing = link_type["from_api_name"] == current_type
    object_ids = _bounded_distinct_search_around_ids(
        _search_around_candidates(
            link_reader,
            transaction=transaction,
            tenant_id=tenant_id,
            current_type=current_type,
            current_ids=current_ids,
            link_api=link_api,
            is_outgoing=is_outgoing,
        ),
        link_api,
    )
    return next_type, object_ids


def _search_around_candidates(
    link_reader: SetLinkReader,
    *,
    transaction: TransactionContext,
    tenant_id: str,
    current_type: str,
    current_ids: Sequence[str],
    link_api: str,
    is_outgoing: bool,
) -> Iterator[str]:
    """Yield one opposite-end id at a time so the result bound is a real memory bound."""
    for object_id in current_ids:
        yield from _search_around_link_targets(
            link_reader,
            transaction=transaction,
            tenant_id=tenant_id,
            current_type=current_type,
            object_id=object_id,
            link_api=link_api,
            is_outgoing=is_outgoing,
        )


def _search_around_link_targets(
    link_reader: SetLinkReader,
    *,
    transaction: TransactionContext,
    tenant_id: str,
    current_type: str,
    object_id: str,
    link_api: str,
    is_outgoing: bool,
) -> Iterator[str]:
    links = _search_around_links(
        link_reader,
        transaction=transaction,
        tenant_id=tenant_id,
        current_type=current_type,
        object_id=object_id,
        link_api=link_api,
        is_outgoing=is_outgoing,
    )
    for link in links:
        yield str(link["to_object_id"] if is_outgoing else link["from_object_id"])


def _search_around_links(
    link_reader: SetLinkReader,
    *,
    transaction: TransactionContext,
    tenant_id: str,
    current_type: str,
    object_id: str,
    link_api: str,
    is_outgoing: bool,
) -> Sequence[Mapping[str, object]]:
    if is_outgoing:
        return link_reader.active_links_from(
            transaction=transaction,
            tenant_id=tenant_id,
            link_type_api_name=link_api,
            from_api_name=current_type,
            from_object_id=object_id,
            limit=MAX_LINK_FANOUT,
        )
    return link_reader.active_links_to(
        transaction=transaction,
        tenant_id=tenant_id,
        link_type_api_name=link_api,
        to_api_name=current_type,
        to_object_id=object_id,
        limit=MAX_LINK_FANOUT,
    )


def _bounded_distinct_search_around_ids(candidates: Iterator[str], link_api: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for object_id in candidates:
        if object_id in seen:
            continue
        seen.add(object_id)
        result.append(object_id)
        if len(result) > SEARCH_AROUND_RESULT_LIMIT:
            raise ValidationFailed(
                "search-around result exceeds the object set limit",
                details={"linkType": link_api, "limit": SEARCH_AROUND_RESULT_LIMIT},
            )
    return result
