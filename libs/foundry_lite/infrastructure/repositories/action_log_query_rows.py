"""Database-native filtering, keyset pagination, and aggregation for Action Logs."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, cast

from sqlalchemy import Boolean, Float, String, and_, func, literal, or_, select
from sqlalchemy import cast as sa_cast

from foundry_lite.application.action_log_types import ACTION_LOG_PROPERTY_TYPES, ActionLogEntryRow
from foundry_lite.application.ports.object_read_repository import (
    ObjectAggregationGroup,
    ObjectAggregationMetric,
    ObjectOrderBy,
    ObjectQueryCursor,
)
from foundry_lite.infrastructure import schema as db

_EDIT_COUNTS = (
    select(
        db.action_log_objects.c.tenant_id.label("tenant_id"),
        db.action_log_objects.c.action_log_entry_id.label("log_id"),
        func.count().label("edited_object_count"),
    )
    .group_by(db.action_log_objects.c.tenant_id, db.action_log_objects.c.action_log_entry_id)
    .subquery("action_log_edit_counts")
)
_EFFECT_COUNTS = (
    select(
        db.action_effect_receipts.c.tenant_id.label("tenant_id"),
        db.action_effect_receipts.c.action_run_id.label("run_id"),
        func.count().label("effect_receipt_count"),
    )
    .group_by(db.action_effect_receipts.c.tenant_id, db.action_effect_receipts.c.action_run_id)
    .subquery("action_log_effect_counts")
)
_LOG_FROM = db.action_log_entries.outerjoin(
    _EDIT_COUNTS,
    and_(
        _EDIT_COUNTS.c.tenant_id == db.action_log_entries.c.tenant_id,
        _EDIT_COUNTS.c.log_id == db.action_log_entries.c.id,
    ),
).outerjoin(
    _EFFECT_COUNTS,
    and_(
        _EFFECT_COUNTS.c.tenant_id == db.action_log_entries.c.tenant_id,
        _EFFECT_COUNTS.c.run_id == db.action_log_entries.c.action_run_id,
    ),
)


def query_action_log_rows(
    transaction: Any,
    tenant_id: str,
    action_type_api_name: str,
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[ObjectOrderBy],
    cursor: ObjectQueryCursor | None,
    search_text: str | None,
    limit: int,
) -> list[ActionLogEntryRow]:
    conditions = _base_conditions(tenant_id, action_type_api_name, filter_ast, search_text)
    sort_columns = [_sort_column(order) for order in order_by]
    cursor_condition = _cursor_condition(sort_columns, cursor)
    if cursor_condition is not None:
        conditions.append(cursor_condition)
    statement = (
        select(db.action_log_entries)
        .select_from(_LOG_FROM)
        .where(and_(*conditions))
        .order_by(*_order_terms(sort_columns))
        .limit(limit)
    )
    rows = transaction.execute(statement).mappings().all()
    return [cast(ActionLogEntryRow, dict(row)) for row in rows]


def aggregate_action_log_rows(
    transaction: Any,
    tenant_id: str,
    action_type_api_name: str,
    filter_ast: Mapping[str, object] | None,
    group_by: Sequence[str],
    metrics: Sequence[ObjectAggregationMetric],
    group_limit: int,
) -> list[ObjectAggregationGroup]:
    keys = [_group_expression(name) for name in group_by]
    values = [_metric_expression(metric) for metric in metrics]
    statement = (
        select(*keys, *values)
        .select_from(_LOG_FROM)
        .where(and_(*_base_conditions(tenant_id, action_type_api_name, filter_ast, None)))
        .limit(group_limit)
    )
    if keys:
        statement = statement.group_by(*keys)
    rows = transaction.execute(statement).all()
    return [_aggregation_group(tuple(row), group_by, metrics) for row in rows]


def _base_conditions(
    tenant_id: str,
    action_type_api_name: str,
    filter_ast: Mapping[str, object] | None,
    search_text: str | None,
) -> list[Any]:
    conditions = [
        db.action_log_entries.c.tenant_id == tenant_id,
        db.action_log_entries.c.action_type_api_name == action_type_api_name,
    ]
    if filter_ast:
        conditions.append(_filter_condition(filter_ast))
    if search_text:
        conditions.append(_search_condition(search_text))
    return conditions


def _property_expression(name: str) -> Any:
    columns = {
        "actionRunId": db.action_log_entries.c.action_run_id,
        "logEntryId": db.action_log_entries.c.id,
        "definitionVersion": db.action_log_entries.c.definition_version,
        "actorUserId": db.action_log_entries.c.actor_user_id,
        "status": db.action_log_entries.c.status,
        "parameters": sa_cast(db.action_log_entries.c.parameters, String),
        "result": sa_cast(db.action_log_entries.c.result, String),
        "branchId": db.action_log_entries.c.branch_id,
        "planHash": db.action_log_entries.c.plan_hash,
        "approvalId": db.action_log_entries.c.approval_id,
        "revertAllowed": db.action_log_entries.c.revert_allowed,
        "revertStatus": db.action_log_entries.c.revert_status,
        "revertedByRunId": db.action_log_entries.c.reverted_by_run_id,
        "effectReceiptCount": func.coalesce(_EFFECT_COUNTS.c.effect_receipt_count, 0),
        "editedObjectCount": func.coalesce(_EDIT_COUNTS.c.edited_object_count, 0),
        "createdAt": db.action_log_entries.c.created_at,
        "completedAt": db.action_log_entries.c.completed_at,
    }
    return columns[name]


def _filter_condition(filter_ast: Mapping[str, object]) -> Any:
    if "and" in filter_ast:
        return and_(*[_filter_condition(item) for item in _filter_group(filter_ast["and"])])
    if "or" in filter_ast:
        return or_(*[_filter_condition(item) for item in _filter_group(filter_ast["or"])])
    name = str(filter_ast["property"])
    if name == "editedObjects":
        return _edited_object_contains(filter_ast["value"])
    return _property_condition(name, str(filter_ast["op"]), filter_ast["value"])


def _property_condition(name: str, operation: str, value: object) -> Any:
    expression = _property_expression(name)
    data_type = ACTION_LOG_PROPERTY_TYPES[name]
    if operation == "contains":
        return func.lower(sa_cast(expression, String)).like(f"%{str(value).casefold()}%")
    if operation == "in":
        return _in_condition(expression, data_type, value)
    sql_value = _sql_value(value, data_type)
    if sql_value is _INVALID:
        return literal(False)
    if operation == "eq":
        return expression == sql_value
    if operation == "gte":
        return expression >= sql_value
    return expression <= sql_value


def _in_condition(expression: Any, data_type: str, value: object) -> Any:
    if not isinstance(value, Collection) or isinstance(value, str | bytes):
        return literal(False)
    values = [_sql_value(item, data_type) for item in value]
    usable = [item for item in values if item is not _INVALID]
    return expression.in_(usable) if usable else literal(False)


def _edited_object_contains(value: object) -> Any:
    needle = f"%{str(value).casefold()}%"
    return (
        select(literal(1))
        .where(
            and_(
                db.action_log_objects.c.tenant_id == db.action_log_entries.c.tenant_id,
                db.action_log_objects.c.action_log_entry_id == db.action_log_entries.c.id,
                or_(
                    func.lower(db.action_log_objects.c.object_type_api_name).like(needle),
                    func.lower(db.action_log_objects.c.object_id).like(needle),
                    func.lower(db.action_log_objects.c.edit_type).like(needle),
                ),
            )
        )
        .correlate(db.action_log_entries)
        .exists()
    )


def _search_condition(search_text: str) -> Any:
    needle = f"%{search_text.casefold()}%"
    names = (
        "actionRunId",
        "logEntryId",
        "definitionVersion",
        "actorUserId",
        "status",
        "parameters",
        "result",
        "branchId",
        "planHash",
        "approvalId",
        "revertStatus",
        "revertedByRunId",
    )
    return or_(
        *(func.lower(sa_cast(_property_expression(name), String)).like(needle) for name in names),
        _edited_object_contains(search_text),
    )


def _sort_column(order: ObjectOrderBy) -> tuple[Any, str, str]:
    name = order["property"]
    data_type = ACTION_LOG_PROPERTY_TYPES[name]
    expression = _property_expression(name)
    if data_type in {"integer", "float", "number"}:
        expression = func.coalesce(sa_cast(expression, Float), -1e308)
        return expression, order["direction"], "number"
    if data_type == "boolean":
        return func.coalesce(sa_cast(expression, Boolean), False), order["direction"], "boolean"
    return func.coalesce(sa_cast(expression, String), ""), order["direction"], "string"


def _order_terms(columns: Sequence[tuple[Any, str, str]]) -> list[Any]:
    return [expression.asc() if direction == "asc" else expression.desc() for expression, direction, _ in columns]


def _cursor_condition(columns: Sequence[tuple[Any, str, str]], cursor: ObjectQueryCursor | None) -> Any | None:
    if cursor is None:
        return None
    branches: list[Any] = []
    for index, (expression, direction, data_type) in enumerate(columns):
        value = _cursor_value(cursor["values"][index], data_type)
        prefix = [
            previous[0] == _cursor_value(cursor["values"][offset], previous[2])
            for offset, previous in enumerate(columns[:index])
        ]
        comparison = expression > value if direction == "asc" else expression < value
        branches.append(and_(*prefix, comparison))
    return or_(*branches)


def _cursor_value(value: object, data_type: str) -> object:
    if value is None:
        return -1e308 if data_type == "number" else False if data_type == "boolean" else ""
    return _sql_value(value, data_type)


def _group_expression(name: str) -> Any:
    expression, _, _ = _sort_column({"property": name, "direction": "asc"})
    return expression


def _metric_expression(metric: ObjectAggregationMetric) -> Any:
    if metric["function"] == "count":
        return func.count()
    expression = sa_cast(_property_expression(str(metric["property"])), Float)
    aggregates: Mapping[str, Callable[[Any], Any]] = {
        "sum": func.sum,
        "avg": func.avg,
        "min": func.min,
        "max": func.max,
    }
    aggregate = aggregates[metric["function"]]
    return aggregate(expression)


def _aggregation_group(
    row: tuple[Any, ...], group_by: Sequence[str], metrics: Sequence[ObjectAggregationMetric]
) -> ObjectAggregationGroup:
    key = {name: row[index] for index, name in enumerate(group_by)}
    values = {
        metric["name"]: _metric_value(row[len(group_by) + index], metric["function"])
        for index, metric in enumerate(metrics)
    }
    return {"key": key, "metrics": values}


def _metric_value(value: object, function: str) -> float | int | None:
    if value is None:
        return None
    if function == "count":
        return value if isinstance(value, int) else int(str(value))
    return float(str(value))


def _filter_group(value: object) -> list[Mapping[str, object]]:
    return [cast(Mapping[str, object], item) for item in cast(Sequence[object], value)]


_INVALID = object()


def _sql_value(value: object, data_type: str) -> object:
    if data_type in {"integer", "float", "number"}:
        if isinstance(value, bool):
            return _INVALID
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return _INVALID
    if data_type == "boolean":
        return value if isinstance(value, bool) else _INVALID
    return "" if value is None else str(value)
