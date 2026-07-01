"""SQLAlchemy repository adapter for object read repository persistence."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

from sqlalchemy import Float, String, and_, asc, desc, func, literal, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import ObjectLinkRow, ObjectOrderBy, ObjectQueryCursor, ObjectRecordRow
from foundry_lite.infrastructure import schema as db

INVALID_SQL_VALUE = object()


class SqlAlchemyObjectReadRepository:
    """SQLAlchemy implementation of object record and link reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def object_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> ObjectRecordRow | None:
        row = (
            transaction.execute(
                select(db.object_records).where(
                    and_(
                        *_active_object_conditions(
                            tenant_id,
                            object_type_api_name,
                            object_type_id,
                            include_deleted=True,
                        ),
                        db.object_records.c.object_id == object_id,
                    )
                ),
            )
            .mappings()
            .first()
        )
        return cast(ObjectRecordRow, dict(row)) if row else None

    def active_object_rows(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_type_id: str | None = None,
    ) -> list[ObjectRecordRow]:
        rows = (
            transaction.execute(
                select(db.object_records)
                .where(and_(*_active_object_conditions(tenant_id, object_type_api_name, object_type_id)))
                .order_by(db.object_records.c.object_id)
            )
            .mappings()
            .all()
        )
        return [cast(ObjectRecordRow, dict(row)) for row in rows]

    def query_active_object_rows(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
        cursor: ObjectQueryCursor | None,
        limit: int,
        object_type_id: str | None = None,
        property_object_type_id: str | None = None,
    ) -> list[ObjectRecordRow]:
        property_data_types = self._property_data_types(
            transaction,
            tenant_id,
            object_type_api_name,
            property_object_type_id or object_type_id,
        )
        sort_columns = _sort_columns(order_by, property_data_types)
        conditions = _active_object_conditions(tenant_id, object_type_api_name, object_type_id)
        if filter_ast:
            conditions.append(_filter_condition(filter_ast, property_data_types))
        cursor_condition = _cursor_condition(sort_columns, cursor)
        if cursor_condition is not None:
            conditions.append(cursor_condition)
        rows = (
            transaction.execute(
                select(db.object_records).where(and_(*conditions)).order_by(*_order_terms(sort_columns)).limit(limit)
            )
            .mappings()
            .all()
        )
        return [cast(ObjectRecordRow, dict(row)) for row in rows]

    def active_links_from(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
    ) -> list[ObjectLinkRow]:
        rows = (
            transaction.execute(
                select(db.object_links).where(
                    and_(
                        db.object_links.c.tenant_id == tenant_id,
                        db.object_links.c.link_type_api_name == link_type_api_name,
                        db.object_links.c.is_active == True,  # noqa: E712
                        db.object_links.c.from_api_name == from_api_name,
                        db.object_links.c.from_object_id == from_object_id,
                        db.object_links.c.deleted == False,  # noqa: E712
                    )
                )
            )
            .mappings()
            .all()
        )
        return [cast(ObjectLinkRow, dict(row)) for row in rows]

    def latest_object_change_sequence(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        include_deleted: bool = True,
    ) -> int:
        row = transaction.execute(
            select(func.max(db.object_records.c.object_change_sequence)).where(
                and_(
                    *_active_object_conditions(
                        tenant_id,
                        object_type_api_name,
                        None,
                        include_deleted=include_deleted,
                    )
                )
            )
        ).scalar_one_or_none()
        return int(row or 0)

    def active_links_to(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_api_name: str,
        to_api_name: str,
        to_object_id: str,
    ) -> list[ObjectLinkRow]:
        rows = (
            transaction.execute(
                select(db.object_links).where(
                    and_(
                        db.object_links.c.tenant_id == tenant_id,
                        db.object_links.c.link_type_api_name == link_type_api_name,
                        db.object_links.c.is_active == True,  # noqa: E712
                        db.object_links.c.to_api_name == to_api_name,
                        db.object_links.c.to_object_id == to_object_id,
                        db.object_links.c.deleted == False,  # noqa: E712
                    )
                )
            )
            .mappings()
            .all()
        )
        return [cast(ObjectLinkRow, dict(row)) for row in rows]

    def _property_data_types(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_type_id: str | None = None,
    ) -> dict[str, str]:
        object_type_condition = (
            db.object_types.c.id == object_type_id
            if object_type_id is not None
            else db.object_types.c.api_name == object_type_api_name
        )
        rows = transaction.execute(
            select(db.property_types.c.api_name, db.property_types.c.data_type)
            .select_from(
                db.property_types.join(
                    db.object_types,
                    db.property_types.c.object_type_id == db.object_types.c.id,
                )
            )
            .where(
                and_(
                    db.object_types.c.tenant_id == tenant_id,
                    object_type_condition,
                )
            )
        ).mappings()
        return {str(row["api_name"]): str(row["data_type"]) for row in rows}


def _active_object_conditions(
    tenant_id: str,
    object_type_api_name: str,
    object_type_id: str | None,
    *,
    include_deleted: bool = False,
) -> list[Any]:
    conditions = [
        db.object_records.c.tenant_id == tenant_id,
        db.object_records.c.object_type_api_name == object_type_api_name,
        db.object_records.c.is_active == True,  # noqa: E712
    ]
    if not include_deleted:
        conditions.append(db.object_records.c.deleted == False)  # noqa: E712
    if object_type_id is not None:
        conditions.append(db.object_records.c.object_type_id == object_type_id)
    return conditions


def _sort_columns(
    order_by: Sequence[ObjectOrderBy],
    property_data_types: Mapping[str, str],
) -> list[tuple[Any, str, str]]:
    return [_sort_column(order, property_data_types) for order in order_by]


def _sort_column(order: ObjectOrderBy, property_data_types: Mapping[str, str]) -> tuple[Any, str, str]:
    property_name = order["property"]
    prop = db.object_records.c.properties[property_name]
    data_type = _require_property_data_type(property_data_types, property_name)
    if _is_number_type(data_type):
        return func.coalesce(sa_cast(prop.as_float(), Float), -1e308), order["direction"], "number"
    if data_type == "boolean":
        return func.coalesce(prop.as_boolean(), False), order["direction"], "boolean"
    return func.coalesce(prop.as_string(), ""), order["direction"], "string"


def _filter_condition(filter_ast: Mapping[str, object], property_data_types: Mapping[str, str]) -> Any:
    if "and" in filter_ast:
        return and_(*[_filter_condition(item, property_data_types) for item in _filter_group(filter_ast["and"])])
    if "or" in filter_ast:
        return or_(*[_filter_condition(item, property_data_types) for item in _filter_group(filter_ast["or"])])
    return _property_filter_condition(
        str(filter_ast["property"]),
        str(filter_ast["op"]),
        filter_ast["value"],
        property_data_types,
    )


def _filter_group(value: object) -> list[Mapping[str, object]]:
    return [cast(Mapping[str, object], item) for item in cast(Sequence[object], value)]


def _property_filter_condition(
    property_name: str,
    op: str,
    value: object,
    property_data_types: Mapping[str, str],
) -> Any:
    data_type = _require_property_data_type(property_data_types, property_name)
    if op == "eq":
        return _property_eq_condition(property_name, data_type, value)
    if op == "in":
        return _property_in_condition(property_name, data_type, value)
    if op == "gte":
        return _property_range_condition(property_name, data_type, value, is_lower_bound=True)
    if op == "lte":
        return _property_range_condition(property_name, data_type, value, is_lower_bound=False)
    return _property_contains_condition(property_name, value)


def _property_eq_condition(property_name: str, data_type: str, value: object) -> Any:
    sql_value = _filter_sql_value(value, data_type)
    if sql_value is INVALID_SQL_VALUE:
        return literal(False)
    return _property_value_expression(property_name, data_type) == sql_value


def _property_in_condition(property_name: str, data_type: str, value: object) -> Any:
    if not isinstance(value, Collection) or isinstance(value, str):
        return literal(False)
    values = [_filter_sql_value(item, data_type) for item in value]
    values = [item for item in values if item is not INVALID_SQL_VALUE]
    if not values:
        return literal(False)
    return or_(*[_property_value_expression(property_name, data_type) == item for item in values])


def _property_range_condition(property_name: str, data_type: str, value: object, *, is_lower_bound: bool) -> Any:
    sql_value = _filter_sql_value(value, data_type)
    if sql_value is INVALID_SQL_VALUE or data_type == "boolean":
        return literal(False)
    expr = _property_value_expression(property_name, data_type)
    return expr >= sql_value if is_lower_bound else expr <= sql_value


def _property_contains_condition(property_name: str, value: object) -> Any:
    expr = func.lower(sa_cast(db.object_records.c.properties[property_name].as_string(), String))
    return expr.like(f"%{str(value).lower()}%")


def _property_value_expression(property_name: str, data_type: str) -> Any:
    prop = db.object_records.c.properties[property_name]
    if data_type == "boolean":
        return prop.as_boolean()
    if _is_number_type(data_type):
        return sa_cast(prop.as_float(), Float)
    return prop.as_string()


def _filter_sql_value(value: object, data_type: str) -> object:
    if _is_number_type(data_type):
        return _number_sql_value(value)
    if data_type == "boolean":
        return value if isinstance(value, bool) else INVALID_SQL_VALUE
    return str(value) if value is not None else ""


def _number_sql_value(value: object) -> object:
    if isinstance(value, bool):
        return INVALID_SQL_VALUE
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return INVALID_SQL_VALUE
    return INVALID_SQL_VALUE


def _cursor_condition(sort_columns: Sequence[tuple[Any, str, str]], cursor: ObjectQueryCursor | None) -> Any | None:
    if cursor is None:
        return None
    if not sort_columns:
        return db.object_records.c.object_id > cursor["object_id"]
    return or_(*_cursor_disjunctions(sort_columns, cursor))


def _cursor_disjunctions(sort_columns: Sequence[tuple[Any, str, str]], cursor: ObjectQueryCursor) -> list[Any]:
    equals: list[Any] = []
    disjunctions = []
    for index, (expr, direction, data_type) in enumerate(sort_columns):
        value = _cursor_sql_value(cursor["values"][index], data_type)
        comparison = expr > value if direction == "asc" else expr < value
        disjunctions.append(and_(*equals, comparison))
        equals.append(expr == value)
    disjunctions.append(and_(*equals, db.object_records.c.object_id > cursor["object_id"]))
    return disjunctions


def _cursor_sql_value(value: object, data_type: str) -> object:
    if data_type == "number":
        number = _number_sql_value(value)
        return number if number is not INVALID_SQL_VALUE else -1e308
    if data_type == "boolean":
        return bool(value) if isinstance(value, bool) else False
    return str(value) if value is not None else ""


def _order_terms(sort_columns: Sequence[tuple[Any, str, str]]) -> list[Any]:
    terms = [asc(expr) if direction == "asc" else desc(expr) for expr, direction, _data_type in sort_columns]
    terms.append(asc(db.object_records.c.object_id))
    return terms


def _require_property_data_type(property_data_types: Mapping[str, str], property_name: str) -> str:
    return property_data_types.get(property_name, "string")


def _is_number_type(data_type: str) -> bool:
    return data_type in {"float", "integer", "number"}
