"""Shape validation for caller-supplied object query filters and ordering.

These checks run against the caller's request BEFORE any server-side (row
policy) filter is combined in, so a caller can never probe undeclared or
masked properties, while server-injected filters legitimately reference
masked properties without tripping the same gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import ObjectOrderBy, ObjectRecordRow
from foundry_lite.application.query_filters import validate_filter_ast
from foundry_lite.application.services.object_store.row_policies import RowPolicyScope, row_visible
from foundry_lite.domain.errors import NotFound, ValidationFailed

OBJECT_QUERY_MAX_LIMIT = 500


def query_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("object query limit must be positive", details={"limit": limit})
    if limit > OBJECT_QUERY_MAX_LIMIT:
        raise ValidationFailed(
            "object query limit exceeds maximum",
            details={"limit": limit, "max_limit": OBJECT_QUERY_MAX_LIMIT},
        )
    return limit


def require_visible_query_record(
    record: ObjectRecordRow | None,
    scope: RowPolicyScope,
    object_type_api_name: str,
    object_id: str,
) -> ObjectRecordRow:
    if record is None or not row_visible(scope, record["properties"]):
        raise NotFound(
            "object not found",
            details={"object_type": object_type_api_name, "object_id": object_id},
        )
    return record


def validate_query_properties(
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[ObjectOrderBy],
    property_names: set[str],
    masked_property_names: set[str],
    *,
    property_data_types: Mapping[str, str] | None = None,
) -> None:
    for order in order_by:
        _validate_query_property(order["property"], property_names, masked_property_names, source="orderBy")
    if filter_ast:
        _validate_filter_properties(filter_ast, property_names, masked_property_names)
        if property_data_types is not None:
            validate_filter_ast(filter_ast, property_data_types=property_data_types)


def _validate_filter_properties(
    filter_ast: Mapping[str, object],
    property_names: set[str],
    masked_property_names: set[str],
) -> None:
    if "and" in filter_ast:
        for item in cast(Sequence[Mapping[str, object]], filter_ast["and"]):
            _validate_filter_properties(item, property_names, masked_property_names)
        return
    if "or" in filter_ast:
        for item in cast(Sequence[Mapping[str, object]], filter_ast["or"]):
            _validate_filter_properties(item, property_names, masked_property_names)
        return
    _validate_query_property(str(filter_ast["property"]), property_names, masked_property_names, source="filter")


def _validate_query_property(
    property_name: str,
    property_names: set[str],
    masked_property_names: set[str],
    *,
    source: str,
) -> None:
    _require_known_query_property(property_name, property_names, source=source)
    if property_name in masked_property_names:
        raise ValidationFailed(
            "object query references masked property",
            details={"property": property_name, "source": source},
        )


def _require_known_query_property(property_name: str, property_names: set[str], *, source: str) -> None:
    if property_name not in property_names:
        raise ValidationFailed(
            "object query references missing property",
            details={"property": property_name, "source": source},
        )
