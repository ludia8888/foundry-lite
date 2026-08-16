"""Interface-scoped polymorphic object query (union across implementing types).

``query_by_interface`` resolves the interface's shared property contracts,
restricts filter/orderBy to those properties, then delegates one query per
implementing object type through the regular ``query_objects`` path so
per-type masking and rowPolicies enforcement are inherited unchanged. Results
merge deterministically ordered by (orderBy interface properties, object
type apiName, objectId).

Pagination limitation (documented contract): the merged result is a single
offset-free page. Per-type keyset cursors cannot be re-derived for a merged
page without exposing cursor internals, so no ``nextCursor`` is issued and
the page size is capped at ``INTERFACE_QUERY_MAX_LIMIT`` (200) instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import cmp_to_key
from typing import Protocol

from foundry_lite.application.ports import (
    InterfaceTypeRow,
    ObjectOrderBy,
    ObjectQueryItem,
    ObjectQueryResult,
    ObjectSortDirection,
    ObjectTypeRow,
    OntologyVersionRow,
    TransactionContext,
)
from foundry_lite.application.query_filters import validate_filter_ast
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.query_validation import validate_query_properties
from foundry_lite.application.services.ontology_interface_validation import persisted_implements
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

INTERFACE_QUERY_MAX_LIMIT = 200


class _InterfaceQueryOntologyLookup(Protocol):
    def _active_ontology_version(self, conn: TransactionContext, ctx: RequestContext) -> OntologyVersionRow: ...

    def _object_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[ObjectTypeRow]: ...


class _InterfaceObjectQueryPlanner(Protocol):
    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[ObjectOrderBy] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        search_text: str | None = None,
        semantic_text: str | None = None,
    ) -> ObjectQueryResult: ...


class ObjectInterfaceQueryService(CoreService):
    """Query objects polymorphically through an ontology interface."""

    required_dependencies = ("engine", "policy", "ontology_repository")
    required_collaborators = ("object_query_service", "ontology_lookup_service")
    object_query_service: _InterfaceObjectQueryPlanner
    ontology_lookup_service: _InterfaceQueryOntologyLookup

    def query_by_interface(
        self,
        interface_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
    ) -> ObjectQueryResult:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        query_limit = _interface_query_limit(limit)
        normalized_order_by = _normalized_interface_order_by(order_by)
        if filter_ast:
            validate_filter_ast(filter_ast)
        interface_row, implementer_api_names = self._interface_scope(ctx, interface_api_name)
        _validate_interface_query_shape(interface_row, filter_ast, normalized_order_by)
        items: list[ObjectQueryItem] = []
        for object_type_api_name in implementer_api_names:
            # Each branch runs the full per-type query path (osdk scope, index
            # contract, masking, rowPolicies) so a caller cannot see more via
            # the interface than they could via the type itself.
            branch = self.object_query_service.query_objects(
                object_type_api_name,
                ctx=ctx,
                filter_ast=filter_ast,
                order_by=normalized_order_by,
                limit=query_limit,
            )
            items.extend(branch["items"])
        items.sort(key=cmp_to_key(_interface_item_comparator(normalized_order_by)))
        return {"items": items[:query_limit], "nextCursor": None}

    def _interface_scope(
        self,
        ctx: RequestContext,
        interface_api_name: str,
    ) -> tuple[InterfaceTypeRow, list[str]]:
        """Resolve the active interface row and its implementing type apiNames."""
        with self.engine.begin() as conn:
            active = self.ontology_lookup_service._active_ontology_version(conn, ctx)
            interface_row = self._active_interface_type(conn, ctx, active["id"], interface_api_name)
            object_rows = self.ontology_lookup_service._object_types_for_version(conn, ctx, active["id"])
        implementer_api_names = sorted(
            row["api_name"] for row in object_rows if interface_api_name in persisted_implements(row["config"])
        )
        return interface_row, implementer_api_names

    def _active_interface_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        interface_api_name: str,
    ) -> InterfaceTypeRow:
        rows = self.ontology_repository.interface_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )
        for row in rows:
            if row["api_name"] == interface_api_name:
                return row
        raise NotFound("interface type not found", details={"api_name": interface_api_name})


def _validate_interface_query_shape(
    interface_row: InterfaceTypeRow,
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[ObjectOrderBy],
) -> None:
    """Restrict filter/orderBy to the interface's shared property contracts.

    Masked-property enforcement stays per implementing type (each branch
    validates against its own masked set), so no masked names are passed here.
    """
    property_names = {prop["apiName"] for prop in interface_row["definition"]["properties"]}
    property_data_types = {
        str(prop["apiName"]): str(prop["type"]) for prop in interface_row["definition"]["properties"]
    }
    try:
        validate_query_properties(
            filter_ast,
            order_by,
            property_names,
            set(),
            property_data_types=property_data_types,
        )
    except ValidationFailed as exc:
        raise ValidationFailed(
            "interface query may only reference interface properties",
            details={**exc.details, "interface": interface_row["api_name"]},
        ) from exc


def _interface_query_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("interface query limit must be positive", details={"limit": limit})
    if limit > INTERFACE_QUERY_MAX_LIMIT:
        raise ValidationFailed(
            "interface query limit exceeds maximum",
            details={"limit": limit, "max_limit": INTERFACE_QUERY_MAX_LIMIT},
        )
    return limit


def _normalized_interface_order_by(order_by: Sequence[Mapping[str, str]] | None) -> list[ObjectOrderBy]:
    normalized: list[ObjectOrderBy] = []
    for item in order_by or []:
        prop = item.get("property")
        if not prop:
            raise ValidationFailed("interface query orderBy property is required")
        normalized.append({"property": prop, "direction": _normalized_interface_direction(item)})
    return normalized


def _normalized_interface_direction(item: Mapping[str, str]) -> ObjectSortDirection:
    direction = item.get("direction", "asc")
    if direction == "asc":
        return "asc"
    if direction == "desc":
        return "desc"
    raise ValidationFailed(
        "interface query orderBy direction must be asc or desc",
        details={"direction": direction},
    )


def _interface_item_comparator(
    order_by: Sequence[ObjectOrderBy],
) -> Callable[[ObjectQueryItem, ObjectQueryItem], int]:
    def compare(left: ObjectQueryItem, right: ObjectQueryItem) -> int:
        for order in order_by:
            outcome = _compare_values(
                left["properties"].get(order["property"]),
                right["properties"].get(order["property"]),
            )
            if outcome != 0:
                return -outcome if order["direction"] == "desc" else outcome
        return _compare_identity(left, right)

    return compare


def _compare_identity(left: ObjectQueryItem, right: ObjectQueryItem) -> int:
    left_key = (left["objectType"], left["objectId"])
    right_key = (right["objectType"], right["objectId"])
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def _compare_values(left: object, right: object) -> int:
    """Total order over scalar property values: None sorts first, then by type name."""
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    if isinstance(left, bool | int | float) and isinstance(right, bool | int | float):
        return (float(left) > float(right)) - (float(left) < float(right))
    if isinstance(left, str) and isinstance(right, str):
        return (left > right) - (left < right)
    left_repr = (type(left).__name__, str(left))
    right_repr = (type(right).__name__, str(right))
    return (left_repr > right_repr) - (left_repr < right_repr)
