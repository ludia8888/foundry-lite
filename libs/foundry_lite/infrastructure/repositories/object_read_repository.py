from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

from sqlalchemy import String, and_, asc, desc, func, literal, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import ObjectLinkRow, ObjectOrderBy, ObjectQueryCursor, ObjectRecordRow
from foundry_lite.infrastructure import schema as db


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
    ) -> ObjectRecordRow | None:
        row = (
            transaction.execute(
                select(db.object_records).where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id == object_id,
                    )
                )
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
    ) -> list[ObjectRecordRow]:
        rows = (
            transaction.execute(
                select(db.object_records)
                .where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.deleted == False,  # noqa: E712
                    )
                )
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
    ) -> list[ObjectRecordRow]:
        sort_columns = self._sort_columns(transaction, tenant_id, object_type_api_name, order_by)
        conditions = _active_object_conditions(tenant_id, object_type_api_name)
        if filter_ast:
            conditions.append(_filter_condition(filter_ast))
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

    def _sort_columns(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        order_by: Sequence[ObjectOrderBy],
    ) -> list[tuple[Any, str, str]]:
        return [self._sort_column(transaction, tenant_id, object_type_api_name, order) for order in order_by]

    def _sort_column(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        order: ObjectOrderBy,
    ) -> tuple[Any, str, str]:
        prop = db.object_records.c.properties[order["property"]]
        data_type = self._property_data_type(transaction, tenant_id, object_type_api_name, order["property"])
        if data_type in {"float", "integer"}:
            return func.coalesce(prop.as_float(), -1e308), order["direction"], "number"
        if data_type == "boolean":
            return func.coalesce(prop.as_boolean(), False), order["direction"], "boolean"
        return func.coalesce(prop.as_string(), ""), order["direction"], "string"

    def _property_data_type(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        property_name: str,
    ) -> str:
        row = transaction.execute(
            select(db.property_types.c.data_type)
            .select_from(
                db.property_types.join(
                    db.object_types,
                    db.property_types.c.object_type_id == db.object_types.c.id,
                )
            )
            .where(
                and_(
                    db.object_types.c.tenant_id == tenant_id,
                    db.object_types.c.api_name == object_type_api_name,
                    db.property_types.c.api_name == property_name,
                )
            )
            .limit(1)
        ).scalar_one_or_none()
        return str(row) if row else "string"


def _active_object_conditions(tenant_id: str, object_type_api_name: str) -> list[Any]:
    return [
        db.object_records.c.tenant_id == tenant_id,
        db.object_records.c.object_type_api_name == object_type_api_name,
        db.object_records.c.deleted == False,  # noqa: E712
    ]


def _filter_condition(filter_ast: Mapping[str, object]) -> Any:
    if "and" in filter_ast:
        return and_(*[_filter_condition(item) for item in _filter_group(filter_ast["and"])])
    if "or" in filter_ast:
        return or_(*[_filter_condition(item) for item in _filter_group(filter_ast["or"])])
    return _property_filter_condition(
        str(filter_ast["property"]),
        str(filter_ast["op"]),
        filter_ast["value"],
    )


def _filter_group(value: object) -> list[Mapping[str, object]]:
    return [cast(Mapping[str, object], item) for item in cast(Sequence[object], value)]


def _property_filter_condition(property_name: str, op: str, value: object) -> Any:
    if op == "eq":
        return _property_value_expression(property_name, value) == value
    if op == "in":
        return _property_in_condition(property_name, value)
    if op == "gte":
        return _property_range_condition(property_name, value, is_lower_bound=True)
    if op == "lte":
        return _property_range_condition(property_name, value, is_lower_bound=False)
    return _property_contains_condition(property_name, value)


def _property_in_condition(property_name: str, value: object) -> Any:
    if not isinstance(value, Collection) or isinstance(value, str):
        return literal(False)
    values = list(value)
    if not values:
        return literal(False)
    return or_(*[_property_value_expression(property_name, item) == item for item in values])


def _property_range_condition(property_name: str, value: object, *, is_lower_bound: bool) -> Any:
    if isinstance(value, bool):
        return literal(False)
    if isinstance(value, int | float):
        expr = db.object_records.c.properties[property_name].as_float()
        return expr >= float(value) if is_lower_bound else expr <= float(value)
    if isinstance(value, str):
        expr = db.object_records.c.properties[property_name].as_string()
        return expr >= value if is_lower_bound else expr <= value
    return literal(False)


def _property_contains_condition(property_name: str, value: object) -> Any:
    expr = func.lower(sa_cast(db.object_records.c.properties[property_name].as_string(), String))
    return expr.like(f"%{str(value).lower()}%")


def _property_value_expression(property_name: str, value: object) -> Any:
    prop = db.object_records.c.properties[property_name]
    if isinstance(value, bool):
        return prop.as_boolean()
    if isinstance(value, int | float):
        return prop.as_float()
    return prop.as_string()


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
        return float(value) if isinstance(value, int | float) else -1e308
    if data_type == "boolean":
        return bool(value) if isinstance(value, bool) else False
    return str(value) if value is not None else ""


def _order_terms(sort_columns: Sequence[tuple[Any, str, str]]) -> list[Any]:
    terms = [asc(expr) if direction == "asc" else desc(expr) for expr, direction, _data_type in sort_columns]
    terms.append(asc(db.object_records.c.object_id))
    return terms
