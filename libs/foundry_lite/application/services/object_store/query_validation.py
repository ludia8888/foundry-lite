"""Shape validation for caller-supplied object query filters and ordering.

These checks run against the caller's request BEFORE any server-side (row
policy) filter is combined in, so a caller can never probe undeclared or
masked properties, while server-injected filters legitimately reference
masked properties without tripping the same gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import ObjectOrderBy
from foundry_lite.domain.errors import ValidationFailed


def validate_query_properties(
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[ObjectOrderBy],
    property_names: set[str],
    masked_property_names: set[str],
) -> None:
    for order in order_by:
        _validate_query_property(order["property"], property_names, masked_property_names, source="orderBy")
    if filter_ast:
        _validate_filter_properties(filter_ast, property_names, masked_property_names)


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
