"""ObjectSet definition normalization and validation rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, cast

from foundry_lite.application.ports import ObjectSetRepository, ObjectTypeRow, TransactionContext
from foundry_lite.application.query_filters import FILTER_OPERATIONS, validate_filter_ast
from foundry_lite.application.services.object_store.set_members import search_around_next_object_type
from foundry_lite.application.services.object_store.set_protocols import SetOntologyLookup
from foundry_lite.application.services.object_store.set_search_around import SearchAroundHops, search_around_parts
from foundry_lite.application.services.object_store.set_semantics import (
    NormalizedObjectSetDefinition,
    object_set_definition_from_inputs,
    object_set_expires_at,
    object_set_storage_visibility,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

OBJECT_SET_TYPES = frozenset({"static", "dynamic", "search_around"})
LinkTypesByApi = Callable[[TransactionContext, RequestContext], Mapping[str, Mapping[str, object]]]
RequireLinkRead = Callable[[RequestContext, str], None]


class ObjectSetPolicy(Protocol):
    def masked_property_names(self, ctx: RequestContext, object_type: str) -> set[str]: ...


def normalize_object_set_definition(
    name: str,
    *,
    set_type: str,
    definition: Mapping[str, object] | None,
    object_ids: list[str] | None,
    filter_ast: Mapping[str, object] | None,
    visibility: str | None,
    access_scope: str | None,
    lifecycle: str | None,
    ttl_seconds: int | None,
) -> NormalizedObjectSetDefinition:
    _validate_create_inputs(name, set_type, ttl_seconds)
    storage_visibility = object_set_storage_visibility(
        visibility=visibility,
        access_scope=access_scope,
        lifecycle=lifecycle,
        ttl_seconds=ttl_seconds,
    )
    return {
        "definition": object_set_definition_from_inputs(set_type, definition, object_ids, filter_ast),
        "visibility": storage_visibility,
        "expires_at": object_set_expires_at(ttl_seconds),
    }


def _validate_create_inputs(name: str, set_type: str, ttl_seconds: int | None) -> None:
    if not name.strip():
        raise ValidationFailed("object set name is required")
    if set_type not in OBJECT_SET_TYPES:
        raise ValidationFailed("unsupported object set type", details={"set_type": set_type})
    if ttl_seconds is not None and ttl_seconds <= 0:
        raise ValidationFailed("ttl_seconds must be positive", details={"ttl_seconds": ttl_seconds})


def validate_object_set_definition(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    normalized: NormalizedObjectSetDefinition,
    *,
    object_set_repository: ObjectSetRepository,
    ontology_service: SetOntologyLookup,
    policy: ObjectSetPolicy,
    link_types_by_api: LinkTypesByApi,
    require_link_read: RequireLinkRead,
) -> None:
    definition = normalized["definition"]
    if "ids" in definition:
        _validate_static_ids(conn, ctx, object_type, definition["ids"], object_set_repository)
        return
    if "filter" in definition:
        _validate_dynamic_filter(
            conn, ctx, object_type, definition["filter"], object_set_repository, ontology_service, policy
        )
        return
    if "searchAround" in definition:
        _validate_search_around_definition(
            conn,
            ctx,
            object_type,
            definition["searchAround"],
            ontology_service,
            object_set_repository,
            policy,
            link_types_by_api,
            require_link_read,
        )
        return
    raise ValidationFailed(
        "object set definition must include ids, filter, or searchAround", details={"definition": definition}
    )


def _validate_search_around_definition(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    search_around: object,
    ontology_service: SetOntologyLookup,
    object_set_repository: ObjectSetRepository,
    policy: ObjectSetPolicy,
    link_types_by_api: LinkTypesByApi,
    require_link_read: RequireLinkRead,
) -> None:
    source_type, filter_ast, hops = search_around_parts(search_around)
    result_type = _search_around_result_type(source_type, hops, link_types_by_api(conn, ctx), ctx, require_link_read)
    if result_type != object_type["api_name"]:
        raise ValidationFailed(
            "search-around result type does not match the declared object type",
            details={"declared": object_type["api_name"], "resolved": result_type},
        )
    source = ontology_service._active_object_type(conn, ctx, source_type)
    if filter_ast:
        _validate_dynamic_filter(conn, ctx, source, filter_ast, object_set_repository, ontology_service, policy)


def _search_around_result_type(
    source_type: str,
    hops: SearchAroundHops,
    link_types: Mapping[str, Mapping[str, object]],
    ctx: RequestContext,
    require_link_read: RequireLinkRead,
) -> str:
    result_type = source_type
    for index, hop in enumerate(hops):
        link_api = hop.get("link")
        if not isinstance(link_api, str) or not link_api:
            raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
        link_type = link_types.get(link_api)
        if link_type is None:
            raise ValidationFailed("search-around link type not found", details={"linkType": link_api})
        require_link_read(ctx, link_api)
        result_type = search_around_next_object_type(link_type, result_type)
    return result_type


def _validate_static_ids(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_ids: object,
    object_set_repository: ObjectSetRepository,
) -> None:
    if not isinstance(object_ids, list) or not all(isinstance(item, str) and item for item in object_ids):
        raise ValidationFailed("static object set ids must be non-empty strings")
    requested_ids = cast(list[str], object_ids)
    existing = object_set_repository.active_object_ids(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        object_type_api_name=object_type["api_name"],
        object_ids=requested_ids,
    )
    missing = [object_id for object_id in requested_ids if object_id not in existing]
    if missing:
        raise ValidationFailed("static object set references missing objects", details={"objectIds": missing})


def _validate_dynamic_filter(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    filter_ast: object,
    object_set_repository: ObjectSetRepository,
    ontology_service: SetOntologyLookup,
    policy: ObjectSetPolicy,
) -> None:
    if not isinstance(filter_ast, dict) or not filter_ast:
        raise ValidationFailed("dynamic object set filter is required")
    typed_filter = cast(Mapping[str, object], filter_ast)
    property_names = object_set_repository.property_names_for_object_type(
        transaction=conn, object_type_id=object_type["id"]
    )
    _validate_filter_ast(typed_filter, property_names, policy.masked_property_names(ctx, object_type["api_name"]))
    property_data_types = {
        row["api_name"]: row["data_type"]
        for row in ontology_service._properties_for_object_type(conn, object_type["id"])
    }
    validate_filter_ast(typed_filter, property_data_types=property_data_types)


def _validate_filter_ast(
    filter_ast: Mapping[str, object],
    property_names: set[str],
    masked_property_names: set[str],
) -> None:
    if "and" in filter_ast or "or" in filter_ast:
        _validate_filter_group(filter_ast.get("and", filter_ast.get("or")), property_names, masked_property_names)
        return
    _validate_filter_leaf(filter_ast, property_names, masked_property_names)


def _validate_filter_leaf(
    filter_ast: Mapping[str, object],
    property_names: set[str],
    masked_property_names: set[str],
) -> None:
    prop = filter_ast.get("property")
    if prop not in property_names:
        raise ValidationFailed("object set filter references missing property", details={"property": prop})
    if prop in masked_property_names:
        raise ValidationFailed("object set filter references masked property", details={"property": prop})
    if filter_ast.get("op") not in FILTER_OPERATIONS:
        raise ValidationFailed("unsupported filter operation", details={"op": filter_ast.get("op")})
    if "value" not in filter_ast:
        raise ValidationFailed("object set filter value is required", details={"property": prop})


def _validate_filter_group(items: object, property_names: set[str], masked_property_names: set[str]) -> None:
    if not isinstance(items, list) or not items:
        raise ValidationFailed("logical filter group must be a non-empty list")
    for item in items:
        if not isinstance(item, dict):
            raise ValidationFailed("logical filter item must be an object")
        _validate_filter_ast(cast(Mapping[str, object], item), property_names, masked_property_names)
