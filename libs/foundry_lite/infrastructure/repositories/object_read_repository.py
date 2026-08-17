"""SQLAlchemy repository adapter for object read repository persistence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Integer,
    String,
    and_,
    asc,
    case,
    desc,
    extract,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import (
    ObjectAggregationGroup,
    ObjectAggregationMetric,
    ObjectLinkRow,
    ObjectOrderBy,
    ObjectQueryCursor,
    ObjectRecordRow,
)
from foundry_lite.domain.scalar_values import (
    DECIMAL_NUMBER_SQL_PATTERN,
    INTEGER_MIN_VALUE,
    INTEGER_SQL_PATTERN,
    finite_number_value,
    integer_value,
    is_iso_date,
    timestamp_from_microseconds,
    timestamp_microseconds,
)
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

    def object_records(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: Sequence[str],
    ) -> list[ObjectRecordRow]:
        if not object_ids:
            return []
        rows = (
            transaction.execute(
                select(db.object_records).where(
                    and_(
                        *_active_object_conditions(
                            tenant_id,
                            object_type_api_name,
                            None,
                            include_deleted=True,
                        ),
                        db.object_records.c.object_id.in_(list(object_ids)),
                    )
                )
            )
            .mappings()
            .all()
        )
        return [cast(ObjectRecordRow, dict(row)) for row in rows]

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
        dialect_name = self.engine.dialect.name
        _ensure_sqlite_timestamp_function(transaction, dialect_name)
        sort_columns = _sort_columns(order_by, property_data_types, dialect_name)
        conditions = _active_object_conditions(tenant_id, object_type_api_name, object_type_id)
        if filter_ast:
            conditions.append(_filter_condition(filter_ast, property_data_types, dialect_name))
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

    def aggregate_active_object_rows(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        filter_ast: Mapping[str, object] | None,
        group_by: Sequence[str],
        metrics: Sequence[ObjectAggregationMetric],
        group_limit: int,
        object_type_id: str | None = None,
        property_object_type_id: str | None = None,
    ) -> list[ObjectAggregationGroup]:
        """Aggregate object rows with SQL GROUP BY so rows never leave the database."""
        property_data_types = self._property_data_types(
            transaction,
            tenant_id,
            object_type_api_name,
            property_object_type_id or object_type_id,
        )
        dialect_name = self.engine.dialect.name
        _ensure_sqlite_timestamp_function(transaction, dialect_name)
        conditions = _active_object_conditions(tenant_id, object_type_api_name, object_type_id)
        if filter_ast:
            conditions.append(_filter_condition(filter_ast, property_data_types, dialect_name))
        key_columns = [
            _property_value_expression(name, _require_property_data_type(property_data_types, name), dialect_name)
            for name in group_by
        ]
        metric_columns = [_metric_expression(metric, property_data_types, dialect_name) for metric in metrics]
        query = select(*key_columns, *metric_columns).where(and_(*conditions)).limit(group_limit)
        if key_columns:
            query = query.group_by(*key_columns)
        rows = transaction.execute(query).all()
        return [_aggregation_group(tuple(row), group_by, metrics, property_data_types) for row in rows]

    def active_links_from(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
        limit: int | None = None,
    ) -> list[ObjectLinkRow]:
        statement = (
            select(db.object_links)
            .where(
                and_(
                    db.object_links.c.tenant_id == tenant_id,
                    db.object_links.c.link_type_api_name == link_type_api_name,
                    db.object_links.c.is_active == True,  # noqa: E712
                    db.object_links.c.from_api_name == from_api_name,
                    db.object_links.c.from_object_id == from_object_id,
                    db.object_links.c.deleted == False,  # noqa: E712
                )
            )
            .order_by(db.object_links.c.to_object_id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = transaction.execute(statement).mappings().all()
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
        limit: int | None = None,
    ) -> list[ObjectLinkRow]:
        statement = (
            select(db.object_links)
            .where(
                and_(
                    db.object_links.c.tenant_id == tenant_id,
                    db.object_links.c.link_type_api_name == link_type_api_name,
                    db.object_links.c.is_active == True,  # noqa: E712
                    db.object_links.c.to_api_name == to_api_name,
                    db.object_links.c.to_object_id == to_object_id,
                    db.object_links.c.deleted == False,  # noqa: E712
                )
            )
            .order_by(db.object_links.c.from_object_id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = transaction.execute(statement).mappings().all()
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
    dialect_name: str = "sqlite",
) -> list[tuple[Any, str, str]]:
    return [_sort_column(order, property_data_types, dialect_name) for order in order_by]


def _sort_column(
    order: ObjectOrderBy,
    property_data_types: Mapping[str, str],
    dialect_name: str,
) -> tuple[Any, str, str]:
    property_name = order["property"]
    data_type = _require_property_data_type(property_data_types, property_name)
    if data_type == "integer":
        return (
            func.coalesce(_number_expression(property_name, data_type, dialect_name), INTEGER_MIN_VALUE - 1),
            order["direction"],
            data_type,
        )
    if _is_number_type(data_type):
        return (
            func.coalesce(_number_expression(property_name, data_type, dialect_name), -1e308),
            order["direction"],
            data_type,
        )
    if data_type == "boolean":
        boolean_value = _property_value_expression(property_name, data_type, dialect_name)
        return case((boolean_value.is_(True), 1), else_=0), order["direction"], "boolean"
    if data_type == "timestamp":
        return (
            func.coalesce(_timestamp_epoch_expression(property_name, dialect_name), -1e308),
            order["direction"],
            "timestamp",
        )
    if data_type == "date":
        return (
            func.coalesce(_property_value_expression(property_name, data_type, dialect_name), ""),
            order["direction"],
            "date",
        )
    return (
        func.coalesce(_property_value_expression(property_name, data_type, dialect_name), ""),
        order["direction"],
        "string",
    )


def _filter_condition(
    filter_ast: Mapping[str, object],
    property_data_types: Mapping[str, str],
    dialect_name: str,
) -> Any:
    if "and" in filter_ast:
        return and_(
            *[_filter_condition(item, property_data_types, dialect_name) for item in _filter_group(filter_ast["and"])]
        )
    if "or" in filter_ast:
        return or_(
            *[_filter_condition(item, property_data_types, dialect_name) for item in _filter_group(filter_ast["or"])]
        )
    return _property_filter_condition(
        str(filter_ast["property"]),
        str(filter_ast["op"]),
        filter_ast["value"],
        property_data_types,
        dialect_name,
    )


def _filter_group(value: object) -> list[Mapping[str, object]]:
    return [cast(Mapping[str, object], item) for item in cast(Sequence[object], value)]


def _property_filter_condition(
    property_name: str,
    op: str,
    value: object,
    property_data_types: Mapping[str, str],
    dialect_name: str,
) -> Any:
    data_type = _require_property_data_type(property_data_types, property_name)
    if op == "eq":
        return _property_eq_condition(property_name, data_type, value, dialect_name)
    if op == "in":
        return _property_in_condition(property_name, data_type, value, dialect_name)
    if op == "gte":
        return _property_range_condition(property_name, data_type, value, dialect_name, is_lower_bound=True)
    if op == "gt":
        return _property_strict_range_condition(property_name, data_type, value, dialect_name, is_lower_bound=True)
    if op == "lte":
        return _property_range_condition(property_name, data_type, value, dialect_name, is_lower_bound=False)
    if op == "lt":
        return _property_strict_range_condition(property_name, data_type, value, dialect_name, is_lower_bound=False)
    if op == "contains":
        return _property_contains_condition(property_name, data_type, value, dialect_name)
    return literal(False)


def _property_eq_condition(
    property_name: str,
    data_type: str,
    value: object,
    dialect_name: str = "sqlite",
) -> Any:
    if value is None:
        return _property_is_null_condition(property_name, dialect_name)
    sql_value = _filter_sql_value(value, data_type)
    if sql_value is INVALID_SQL_VALUE:
        return literal(False)
    if dialect_name == "postgresql" and data_type != "timestamp" and not _is_number_type(data_type):
        document = literal({property_name: sql_value}, type_=JSONB())
        return db.object_records.c.properties.op("@>")(document)
    return _property_value_expression(property_name, data_type, dialect_name) == sql_value


def _property_in_condition(
    property_name: str,
    data_type: str,
    value: object,
    dialect_name: str = "sqlite",
) -> Any:
    items = _filter_sequence(value)
    if items is None:
        return literal(False)
    conditions = [_property_eq_condition(property_name, data_type, item, dialect_name) for item in items]
    if not conditions:
        return literal(False)
    return or_(*conditions)


def _filter_sequence(value: object) -> Sequence[object] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return None
    return value


def _property_range_condition(
    property_name: str,
    data_type: str,
    value: object,
    dialect_name: str = "sqlite",
    *,
    is_lower_bound: bool,
) -> Any:
    sql_value = _filter_sql_value(value, data_type)
    if sql_value is INVALID_SQL_VALUE or data_type == "boolean":
        return literal(False)
    expr = _property_value_expression(property_name, data_type, dialect_name)
    return expr >= sql_value if is_lower_bound else expr <= sql_value


def _property_strict_range_condition(
    property_name: str,
    data_type: str,
    value: object,
    dialect_name: str,
    *,
    is_lower_bound: bool,
) -> Any:
    sql_value = _filter_sql_value(value, data_type)
    if sql_value is INVALID_SQL_VALUE or data_type == "boolean":
        return literal(False)
    expr = _property_value_expression(property_name, data_type, dialect_name)
    return expr > sql_value if is_lower_bound else expr < sql_value


def _property_contains_condition(property_name: str, data_type: str, value: object, dialect_name: str) -> Any:
    if data_type != "string" or not isinstance(value, str):
        return literal(False)
    expr = func.lower(sa_cast(_property_value_expression(property_name, data_type, dialect_name), String))
    escaped = value.lower().replace("/", "//").replace("%", "/%").replace("_", "/_")
    return expr.like(f"%{escaped}%", escape="/")


def _property_value_expression(
    property_name: str,
    data_type: str,
    dialect_name: str = "sqlite",
) -> Any:
    prop = db.object_records.c.properties[property_name]
    if data_type == "boolean":
        return case(
            (_json_type_expression(property_name, dialect_name) == "boolean", prop.as_boolean()),
            else_=None,
        )
    if _is_number_type(data_type):
        return _number_expression(property_name, data_type, dialect_name)
    if data_type == "date":
        return _date_expression(property_name, dialect_name)
    if data_type == "timestamp":
        return _timestamp_epoch_expression(property_name, dialect_name)
    if data_type == "string":
        return case(
            (_json_type_expression(property_name, dialect_name) == "string", prop.as_string()),
            else_=None,
        )
    return literal(None)


def _property_is_null_condition(property_name: str, dialect_name: str) -> Any:
    json_type = _json_type_expression(property_name, dialect_name)
    return or_(json_type.is_(None), json_type == "null")


def _json_type_expression(property_name: str, dialect_name: str) -> Any:
    if dialect_name == "postgresql":
        return func.jsonb_typeof(db.object_records.c.properties[property_name])
    escaped_name = property_name.replace('"', '\\"')
    raw_type = func.json_type(db.object_records.c.properties, f'$."{escaped_name}"')
    return case(
        (raw_type == "text", "string"),
        (raw_type.in_(("integer", "real")), "number"),
        (raw_type.in_(("true", "false")), "boolean"),
        (raw_type == "null", "null"),
        else_=None,
    )


def _number_expression(property_name: str, data_type: str, dialect_name: str) -> Any:
    prop = db.object_records.c.properties[property_name]
    value = prop.as_string()
    if dialect_name == "sqlite":
        json_type = _json_type_expression(property_name, dialect_name)
        converter = func.foundry_integer_number(value) if data_type == "integer" else func.foundry_finite_number(value)
        return case(
            (json_type.in_(("number", "string")), converter),
            else_=None,
        )
    json_type = _json_type_expression(property_name, dialect_name)
    is_number_shape = json_type.in_(("number", "string"))
    pattern = INTEGER_SQL_PATTERN if data_type == "integer" else DECIMAL_NUMBER_SQL_PATTERN
    is_decimal_shape = value.op("~")(pattern)
    sql_type_name = "integer" if data_type == "integer" else "double precision"
    cast_type = Integer if data_type == "integer" else Float
    is_valid = func.pg_input_is_valid(value, sql_type_name)
    return case((and_(is_number_shape, is_decimal_shape, is_valid), sa_cast(value, cast_type)), else_=None)


def _date_expression(property_name: str, dialect_name: str) -> Any:
    prop = db.object_records.c.properties[property_name]
    value = prop.as_string()
    if dialect_name == "sqlite":
        return func.foundry_iso_date(value)
    is_string = _json_type_expression(property_name, dialect_name) == "string"
    is_iso_shape = value.op("~")(r"^\d{4}-\d{2}-\d{2}$")
    is_valid = func.pg_input_is_valid(value, "date")
    return case((and_(is_string, is_iso_shape, is_valid), value), else_=None)


def _timestamp_epoch_expression(property_name: str, dialect_name: str) -> Any:
    prop = db.object_records.c.properties[property_name]
    value = prop.as_string()
    if dialect_name == "postgresql":
        is_string = _json_type_expression(property_name, dialect_name) == "string"
        is_iso_shape = value.op("~")(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")
        is_valid = func.pg_input_is_valid(value, "timestamp with time zone")
        parsed = sa_cast(value, DateTime(timezone=True))
        epoch = sa_cast(extract("epoch", parsed) * 1_000_000, BigInteger)
        return case((and_(is_string, is_iso_shape, is_valid), epoch), else_=None)
    return func.foundry_timestamp_microseconds(value)


def _ensure_sqlite_timestamp_function(transaction: Any, dialect_name: str) -> None:
    if dialect_name != "sqlite":
        return
    transaction.connection.driver_connection.create_function(
        "foundry_timestamp_microseconds",
        1,
        timestamp_microseconds,
        deterministic=True,
    )
    transaction.connection.driver_connection.create_function(
        "foundry_finite_number",
        1,
        _sqlite_finite_number,
        deterministic=True,
    )
    transaction.connection.driver_connection.create_function(
        "foundry_iso_date",
        1,
        _sqlite_iso_date,
        deterministic=True,
    )
    transaction.connection.driver_connection.create_function(
        "foundry_integer_number",
        1,
        _sqlite_integer_number,
        deterministic=True,
    )


def _sqlite_finite_number(value: object) -> float | None:
    parsed = _number_sql_value(value)
    return None if parsed is INVALID_SQL_VALUE else cast(float, parsed)


def _sqlite_iso_date(value: object) -> str | None:
    return cast(str, value) if is_iso_date(value) else None


def _sqlite_integer_number(value: object) -> int | None:
    return integer_value(value)


def _filter_sql_value(value: object, data_type: str) -> object:
    if data_type == "integer":
        return _integer_sql_value(value)
    if _is_number_type(data_type):
        return _number_sql_value(value)
    if data_type == "boolean":
        return value if isinstance(value, bool) else INVALID_SQL_VALUE
    if data_type == "timestamp":
        microseconds = timestamp_microseconds(value)
        return microseconds if microseconds is not None else INVALID_SQL_VALUE
    if data_type == "date":
        return value if is_iso_date(value) else INVALID_SQL_VALUE
    return value if isinstance(value, str) else INVALID_SQL_VALUE


def _number_sql_value(value: object) -> object:
    parsed = finite_number_value(value)
    return parsed if parsed is not None else INVALID_SQL_VALUE


def _integer_sql_value(value: object) -> object:
    parsed = integer_value(value)
    return parsed if parsed is not None else INVALID_SQL_VALUE


_METRIC_AGGREGATES: dict[str, Callable[[Any], Any]] = {
    "sum": func.sum,
    "avg": func.avg,
    "min": func.min,
    "max": func.max,
}


def _metric_expression(
    metric: ObjectAggregationMetric,
    property_data_types: Mapping[str, str],
    dialect_name: str,
) -> Any:
    """Build the SQL aggregate for one metric over the JSON property extraction."""
    if metric["function"] == "count":
        return func.count()
    property_name = str(metric["property"])
    data_type = _require_property_data_type(property_data_types, property_name)
    return _METRIC_AGGREGATES[metric["function"]](_number_expression(property_name, data_type, dialect_name))


def _aggregation_group(
    row: tuple[Any, ...],
    group_by: Sequence[str],
    metrics: Sequence[ObjectAggregationMetric],
    property_data_types: Mapping[str, str],
) -> ObjectAggregationGroup:
    key = {
        name: _aggregation_key_value(row[index], _require_property_data_type(property_data_types, name))
        for index, name in enumerate(group_by)
    }
    values: dict[str, float | int | None] = {}
    for offset, metric in enumerate(metrics):
        values[metric["name"]] = _aggregation_metric_value(row[len(group_by) + offset], metric["function"])
    return {"key": key, "metrics": values}


def _aggregation_key_value(value: object, data_type: str) -> object:
    """Coerce SQL-extracted JSON key values back to the ontology-declared type.

    The JSON extraction expressions return backend-specific scalars (SQLite may
    hand back ints for floats, 0/1 for booleans), so the declared data_type is
    the only stable contract for group keys.
    """
    if value is None:
        return None
    if data_type == "boolean":
        return bool(value)
    if data_type == "integer":
        return int(cast(str | int | float, value))
    if _is_number_type(data_type):
        return float(cast(str | int | float, value))
    if data_type == "timestamp":
        return timestamp_from_microseconds(value)
    return str(value)


def _aggregation_metric_value(value: object, function: str) -> float | int | None:
    if function == "count":
        return int(cast(int, value or 0))
    if value is None:
        return None
    return float(cast(str | int | float, value))


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
    if _is_number_type(data_type):
        number = _filter_sql_value(value, data_type)
        return number if number is not INVALID_SQL_VALUE else -1e308
    if data_type == "boolean":
        return 1 if value is True else 0
    if data_type == "timestamp":
        microseconds = timestamp_microseconds(value)
        return microseconds if microseconds is not None else -1e308
    if data_type == "date":
        return value if is_iso_date(value) else ""
    return value if isinstance(value, str) else ""


def _order_terms(sort_columns: Sequence[tuple[Any, str, str]]) -> list[Any]:
    terms = [asc(expr) if direction == "asc" else desc(expr) for expr, direction, _data_type in sort_columns]
    terms.append(asc(db.object_records.c.object_id))
    return terms


def _require_property_data_type(property_data_types: Mapping[str, str], property_name: str) -> str:
    return property_data_types.get(property_name, "string")


def _is_number_type(data_type: str) -> bool:
    return data_type in {"float", "integer", "number"}
