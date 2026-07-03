"""Aggregation planning and result shaping for ontology object queries.

Aggregation must execute in the database, not the client: paging whole object
sets through the SDK to count them collapses at scale and would move unmasked
rows toward the caller. This module owns the fail-closed request validation —
grouping or aggregating on a masked property is an authorization failure, not
a validation nit, because ``sum(margin) group by customer`` reconstructs the
very values masking hides. The repository only ever sees a plan that already
passed those gates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports import (
    ObjectAggregationFunction,
    ObjectAggregationGroup,
    ObjectAggregationMetric,
    ObjectAggregationResult,
    ObjectReadRepository,
    ObjectTypeRow,
    PropertyTypeRow,
    TransactionContext,
)
from foundry_lite.application.services.object_store.serving_contract import record_scope_object_type_id
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

#: Hard bound on returned groups: the endpoint is for dashboards/rollups, and an
#: unbounded high-cardinality groupBy would rebuild the full-scan problem the
#: server-side design exists to avoid.
AGGREGATION_GROUP_LIMIT = 1_000
AGGREGATION_MAX_GROUP_BY = 2
AGGREGATION_FUNCTIONS = frozenset({"count", "sum", "avg", "min", "max"})
#: sum/avg/min/max are only defined for numeric ontology types; strings would
#: silently coerce to 0.0 in SQL casts, so they are rejected up front.
NUMERIC_PROPERTY_DATA_TYPES = frozenset({"float", "integer", "number"})


@dataclass(frozen=True)
class ObjectAggregationPlan:
    """A fully validated aggregation request, safe to hand to the repository."""

    group_by: tuple[str, ...]
    metrics: tuple[ObjectAggregationMetric, ...]


def build_aggregation_plan(
    object_type_api_name: str,
    property_data_types: Mapping[str, str],
    masked_property_names: set[str],
    *,
    group_by: Sequence[str] | None,
    select: Sequence[Mapping[str, object]] | None,
) -> ObjectAggregationPlan:
    return ObjectAggregationPlan(
        group_by=_validated_group_by(object_type_api_name, property_data_types, masked_property_names, group_by),
        metrics=_validated_metrics(object_type_api_name, property_data_types, masked_property_names, select),
    )


def property_data_types(property_types: Sequence[PropertyTypeRow]) -> dict[str, str]:
    """Index ontology property rows by api name for plan validation."""
    return {property_type["api_name"]: property_type["data_type"] for property_type in property_types}


def aggregate_group_rows(
    repository: ObjectReadRepository,
    transaction: TransactionContext,
    *,
    tenant_id: str,
    object_type: ObjectTypeRow,
    filter_ast: Mapping[str, object] | None,
    plan: ObjectAggregationPlan,
) -> list[ObjectAggregationGroup]:
    """Fetch aggregated groups; one extra group is requested purely to detect cap overflow."""
    return repository.aggregate_active_object_rows(
        transaction=transaction,
        tenant_id=tenant_id,
        object_type_api_name=object_type["api_name"],
        filter_ast=filter_ast,
        group_by=plan.group_by,
        metrics=plan.metrics,
        group_limit=AGGREGATION_GROUP_LIMIT + 1,
        object_type_id=record_scope_object_type_id(object_type),
        property_object_type_id=object_type["id"],
    )


def finalize_aggregation_result(
    plan: ObjectAggregationPlan,
    groups: Sequence[ObjectAggregationGroup],
) -> ObjectAggregationResult:
    """Enforce the group cap and impose a deterministic group order.

    Sorting happens here (over at most cap+1 rows) rather than in SQL because
    NULL ordering differs between SQLite and PostgreSQL; a Python sort keeps
    the response byte-stable across backends.
    """
    if len(groups) > AGGREGATION_GROUP_LIMIT:
        raise ValidationFailed(
            "object aggregation exceeds the group limit; add a filter or group by a lower-cardinality property",
            details={"groupBy": list(plan.group_by), "groupLimit": AGGREGATION_GROUP_LIMIT},
        )
    ordered = sorted(groups, key=lambda group: _group_sort_key(plan.group_by, group))
    return {"groups": list(ordered), "totalGroups": len(ordered)}


def _validated_group_by(
    object_type_api_name: str,
    property_data_types: Mapping[str, str],
    masked_property_names: set[str],
    group_by: Sequence[str] | None,
) -> tuple[str, ...]:
    names = [str(name) for name in group_by or []]
    if len(names) > AGGREGATION_MAX_GROUP_BY:
        raise ValidationFailed(
            "object aggregation supports at most two groupBy properties",
            details={"objectType": object_type_api_name, "groupBy": names},
        )
    if len(set(names)) != len(names):
        raise ValidationFailed(
            "object aggregation groupBy properties must be unique",
            details={"objectType": object_type_api_name, "groupBy": names},
        )
    for name in names:
        _require_aggregation_property(object_type_api_name, property_data_types, masked_property_names, name, "groupBy")
    return tuple(names)


def _validated_metrics(
    object_type_api_name: str,
    property_data_types: Mapping[str, str],
    masked_property_names: set[str],
    select: Sequence[Mapping[str, object]] | None,
) -> tuple[ObjectAggregationMetric, ...]:
    items = list(select or [])
    if not items:
        raise ValidationFailed(
            "object aggregation select must contain at least one metric",
            details={"objectType": object_type_api_name},
        )
    metrics = [
        _metric_from_request(object_type_api_name, property_data_types, masked_property_names, item) for item in items
    ]
    names = [metric["name"] for metric in metrics]
    if len(set(names)) != len(names):
        raise ValidationFailed(
            "object aggregation metric names must be unique",
            details={"objectType": object_type_api_name, "metrics": names},
        )
    return tuple(metrics)


def _metric_from_request(
    object_type_api_name: str,
    property_data_types: Mapping[str, str],
    masked_property_names: set[str],
    item: Mapping[str, object],
) -> ObjectAggregationMetric:
    function = item.get("function")
    if not isinstance(function, str) or function not in AGGREGATION_FUNCTIONS:
        raise ValidationFailed(
            "object aggregation metric function must be count, sum, avg, min, or max",
            details={"objectType": object_type_api_name, "function": function},
        )
    if function == "count":
        return _count_metric(object_type_api_name, item)
    return _property_metric(object_type_api_name, property_data_types, masked_property_names, item, function)


def _count_metric(object_type_api_name: str, item: Mapping[str, object]) -> ObjectAggregationMetric:
    if item.get("property") is not None:
        raise ValidationFailed(
            "object aggregation count metric must not name a property",
            details={"objectType": object_type_api_name, "property": item.get("property")},
        )
    return {"name": _metric_name(item, "count"), "function": "count", "property": None}


def _property_metric(
    object_type_api_name: str,
    property_data_types: Mapping[str, str],
    masked_property_names: set[str],
    item: Mapping[str, object],
    function: str,
) -> ObjectAggregationMetric:
    property_name = item.get("property")
    if not isinstance(property_name, str) or not property_name:
        raise ValidationFailed(
            "object aggregation metric requires a property",
            details={"objectType": object_type_api_name, "function": function},
        )
    _require_aggregation_property(
        object_type_api_name, property_data_types, masked_property_names, property_name, "select"
    )
    if property_data_types[property_name] not in NUMERIC_PROPERTY_DATA_TYPES:
        raise ValidationFailed(
            "object aggregation metric requires a numeric property",
            details={
                "objectType": object_type_api_name,
                "property": property_name,
                "dataType": property_data_types[property_name],
            },
        )
    return {
        "name": _metric_name(item, f"{function}_{property_name}"),
        "function": cast(ObjectAggregationFunction, function),
        "property": property_name,
    }


def _metric_name(item: Mapping[str, object], default: str) -> str:
    name = item.get("name")
    return name if isinstance(name, str) and name else default


def _require_aggregation_property(
    object_type_api_name: str,
    property_data_types: Mapping[str, str],
    masked_property_names: set[str],
    property_name: str,
    source: str,
) -> None:
    if property_name not in property_data_types:
        raise ValidationFailed(
            "object aggregation references missing property",
            details={"objectType": object_type_api_name, "property": property_name, "source": source},
        )
    if property_name in masked_property_names:
        # Fail closed with an authz error (not validation): group counts or
        # min/max over a masked property are an inference channel for the
        # exact values masking is supposed to hide.
        raise PermissionDenied(
            "object aggregation references masked property",
            details={"objectType": object_type_api_name, "property": property_name, "source": source},
        )


def _group_sort_key(group_by: Sequence[str], group: ObjectAggregationGroup) -> tuple[tuple[int, float, str], ...]:
    return tuple(_key_value_sort(group["key"].get(name)) for name in group_by)


def _key_value_sort(value: object) -> tuple[int, float, str]:
    """Total order over heterogeneous key values: null, then bool, number, text."""
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, int | float):
        return (2, float(value), "")
    return (3, 0.0, str(value))
