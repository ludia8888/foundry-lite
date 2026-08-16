from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports import (
    ObjectAggregationGroup,
    ObjectAggregationMetric,
    ObjectLinkRow,
    ObjectOrderBy,
    ObjectQueryCursor,
    ObjectReadRepository,
    ObjectRecordRow,
)
from foundry_lite.domain.scalar_values import (
    finite_number_value,
    integer_value,
    is_iso_date,
    timestamp_from_microseconds,
    timestamp_microseconds,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyObjectReadRepository
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class ObjectReadHarness(Protocol):
    @property
    def repository(self) -> ObjectReadRepository: ...

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_object(self, **kwargs: Any) -> None: ...

    def add_property_type(self, **kwargs: Any) -> None: ...

    def add_link(self, **kwargs: Any) -> None: ...


@dataclass
class FakeObjectReadRepository:
    record_rows: list[dict[str, Any]] = field(default_factory=list)
    object_links: list[dict[str, Any]] = field(default_factory=list)
    property_data_types: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def object_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> ObjectRecordRow | None:
        del transaction
        for row in self.record_rows:
            if (
                row["tenant_id"] == tenant_id
                and row["object_type_api_name"] == object_type_api_name
                and row["object_id"] == object_id
                and (object_type_id is None or row["object_type_id"] == object_type_id)
                and row.get("is_active", True) is True
            ):
                return cast(ObjectRecordRow, dict(row))
        return None

    def object_records(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: Sequence[str],
    ) -> list[ObjectRecordRow]:
        del transaction
        wanted = set(object_ids)
        return [
            cast(ObjectRecordRow, dict(row))
            for row in self.record_rows
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row["object_id"] in wanted
            and row.get("is_active", True) is True
        ]

    def active_object_rows(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_type_id: str | None = None,
    ) -> list[ObjectRecordRow]:
        del transaction
        rows = [
            cast(ObjectRecordRow, dict(row))
            for row in self.record_rows
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and (object_type_id is None or row["object_type_id"] == object_type_id)
            and row.get("is_active", True) is True
            and row["deleted"] is False
        ]
        return sorted(rows, key=lambda row: row["object_id"])

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
        rows = self.active_object_rows(
            transaction=transaction,
            tenant_id=tenant_id,
            object_type_api_name=object_type_api_name,
            object_type_id=object_type_id,
        )
        property_data_types = self._property_data_types(
            object_type_api_name,
            property_object_type_id or object_type_id,
        )
        if filter_ast:
            rows = [row for row in rows if _matches_typed_filter(row["properties"], filter_ast, property_data_types)]
        rows = _sort_rows(rows, order_by, property_data_types)
        rows = _rows_after_cursor(rows, order_by, cursor, property_data_types)
        return rows[:limit]

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
        rows = self.active_object_rows(
            transaction=transaction,
            tenant_id=tenant_id,
            object_type_api_name=object_type_api_name,
            object_type_id=object_type_id,
        )
        property_data_types = self._property_data_types(
            object_type_api_name,
            property_object_type_id or object_type_id,
        )
        if filter_ast:
            rows = [row for row in rows if _matches_typed_filter(row["properties"], filter_ast, property_data_types)]
        groups = _fake_aggregated_groups(rows, group_by, metrics, property_data_types)
        return groups[:group_limit]

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
        del transaction
        rows = [
            cast(ObjectLinkRow, dict(row))
            for row in self.object_links
            if row["tenant_id"] == tenant_id
            and row["link_type_api_name"] == link_type_api_name
            and row.get("is_active", True) is True
            and row["from_api_name"] == from_api_name
            and row["from_object_id"] == from_object_id
            and row["deleted"] is False
        ]
        rows.sort(key=lambda row: row["to_object_id"])
        return rows[:limit] if limit is not None else rows

    def latest_object_change_sequence(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        include_deleted: bool = True,
    ) -> int:
        del transaction
        rows = [
            row
            for row in self.record_rows
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row.get("is_active", True) is True
            and (include_deleted or row["deleted"] is False)
        ]
        return max((int(row.get("object_change_sequence") or 0) for row in rows), default=0)

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
        del transaction
        rows = [
            cast(ObjectLinkRow, dict(row))
            for row in self.object_links
            if row["tenant_id"] == tenant_id
            and row["link_type_api_name"] == link_type_api_name
            and row.get("is_active", True) is True
            and row["to_api_name"] == to_api_name
            and row["to_object_id"] == to_object_id
            and row["deleted"] is False
        ]
        rows.sort(key=lambda row: row["from_object_id"])
        return rows[:limit] if limit is not None else rows

    def _property_data_types(self, object_type_api_name: str, object_type_id: str | None) -> dict[str, str]:
        return {
            property_name: data_type
            for (row_object_type, row_object_type_id, property_name), data_type in self.property_data_types.items()
            if row_object_type == object_type_api_name
            and (object_type_id is None or row_object_type_id == object_type_id)
        }


@dataclass
class FakeObjectReadHarness:
    repository: FakeObjectReadRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_object(self, **kwargs: Any) -> None:
        self.repository.record_rows.append(_object_row(**kwargs))

    def add_property_type(self, **kwargs: Any) -> None:
        self.repository.property_data_types[
            (
                str(kwargs.get("object_type_api_name", "Order")),
                str(kwargs.get("object_type_id", "ot_order")),
                str(kwargs.get("api_name", "amount")),
            )
        ] = str(kwargs.get("data_type", "float"))

    def add_link(self, **kwargs: Any) -> None:
        self.repository.object_links.append(_link_row(**kwargs))


@dataclass
class SqlAlchemyObjectReadHarness:
    repository: SqlAlchemyObjectReadRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def add_object(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_records).values(**_object_row(**kwargs)))

    def add_property_type(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            object_type_id = str(kwargs.get("object_type_id", "ot_order"))
            existing = conn.execute(
                select(db.object_types.c.id).where(db.object_types.c.id == object_type_id)
            ).scalar_one_or_none()
            if existing is None:
                conn.execute(
                    insert(db.object_types).values(
                        **_object_type_row(object_type_id, kwargs.get("ontology_version_id", "ont_1"))
                    )
                )
            property_kwargs = {key: value for key, value in kwargs.items() if key != "ontology_version_id"}
            conn.execute(insert(db.property_types).values(**_property_type_row(**property_kwargs)))

    def add_link(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_links).values(**_link_row(**kwargs)))


def _object_row(
    *,
    row_id: str,
    tenant_id: str = "tenant-demo",
    object_type_id: str = "ot_order",
    object_type_api_name: str = "Order",
    object_id: str = "O-1",
    deleted: bool = False,
    object_version: int = 1,
    object_change_sequence: int = 1,
    index_version: str = "active",
    is_active: bool = True,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "tenant_id": tenant_id,
        "object_type_id": object_type_id,
        "object_type_api_name": object_type_api_name,
        "object_id": object_id,
        "index_version": index_version,
        "is_active": is_active,
        "properties": properties or {"status": "PENDING"},
        "base_properties": properties or {"status": "PENDING"},
        "edit_properties": {},
        "property_versions": {"status": 1},
        "source_dataset_version_id": "dsv_orders_1",
        "source_hash": "hash-demo",
        "object_version": object_version,
        "object_change_sequence": object_change_sequence,
        "deleted": deleted,
        "deletion_reason": "test delete" if deleted else None,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


def _link_row(
    *,
    row_id: str,
    tenant_id: str = "tenant-demo",
    link_type_id: str = "lt_order_customer",
    link_type_api_name: str = "OrderCustomer",
    from_api_name: str = "Order",
    from_object_id: str = "O-1",
    to_api_name: str = "Customer",
    to_object_id: str = "C-1",
    deleted: bool = False,
    index_version: str = "active",
    is_active: bool = True,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "tenant_id": tenant_id,
        "link_type_id": link_type_id,
        "link_type_api_name": link_type_api_name,
        "index_version": index_version,
        "is_active": is_active,
        "from_object_type_id": "ot_order",
        "from_api_name": from_api_name,
        "from_object_id": from_object_id,
        "to_object_type_id": "ot_customer",
        "to_api_name": to_api_name,
        "to_object_id": to_object_id,
        "properties": {},
        "source_dataset_version_id": "dsv_orders_1",
        "link_version": 1,
        "deleted": deleted,
        "deletion_reason": "test delete" if deleted else None,
        "updated_at": "2026-06-10T00:00:00Z",
    }


def _property_type_row(
    *,
    row_id: str,
    object_type_id: str = "ot_order",
    api_name: str = "amount",
    data_type: str = "float",
) -> dict[str, Any]:
    return {
        "id": row_id,
        "tenant_id": "tenant-demo",
        "object_type_id": object_type_id,
        "api_name": api_name,
        "display_name": api_name,
        "data_type": data_type,
        "nullable": True,
        "indexed": True,
        "searchable": False,
        "editable": False,
        "classification": None,
        "source": "base",
        "column_name": api_name,
        "edit_policy": "immutable",
        "derivation": None,
    }


def _object_type_row(object_type_id: object, ontology_version_id: object = "ont_1") -> dict[str, Any]:
    return {
        "id": str(object_type_id),
        "tenant_id": "tenant-demo",
        "ontology_version_id": str(ontology_version_id),
        "api_name": "Order",
        "display_name": "Order",
        "primary_key_property": "order_id",
        "backing": {"type": "dataset"},
        "config": {},
    }


_INVALID_VALUE = object()


def _sort_rows(
    rows: list[ObjectRecordRow],
    order_by: Sequence[ObjectOrderBy],
    property_data_types: Mapping[str, str],
) -> list[ObjectRecordRow]:
    sorted_rows = sorted(rows, key=lambda row: row["object_id"])
    for order in reversed(order_by):
        reverse = order["direction"] == "desc"
        sorted_rows.sort(
            key=lambda row: _property_sort_key(row, order["property"], property_data_types),
            reverse=reverse,
        )
    return sorted_rows


def _rows_after_cursor(
    rows: list[ObjectRecordRow],
    order_by: Sequence[ObjectOrderBy],
    cursor: ObjectQueryCursor | None,
    property_data_types: Mapping[str, str],
) -> list[ObjectRecordRow]:
    if cursor is None:
        return rows
    return [row for row in rows if _row_after_cursor(row, order_by, cursor, property_data_types)]


def _row_after_cursor(
    row: ObjectRecordRow,
    order_by: Sequence[ObjectOrderBy],
    cursor: ObjectQueryCursor,
    property_data_types: Mapping[str, str],
) -> bool:
    for index, order in enumerate(order_by):
        data_type = _require_property_data_type(property_data_types, order["property"])
        current = _property_sort_key(row, order["property"], property_data_types)
        previous = _sort_key(cursor["values"][index], data_type)
        if current == previous:
            continue
        return current > previous if order["direction"] == "asc" else current < previous
    return row["object_id"] > cursor["object_id"]


def _property_sort_key(
    row: ObjectRecordRow,
    property_name: str,
    property_data_types: Mapping[str, str],
) -> tuple[int, float, str]:
    data_type = _require_property_data_type(property_data_types, property_name)
    return _sort_key(row["properties"].get(property_name), data_type)


def _sort_key(value: object, data_type: str) -> tuple[int, float, str]:
    if data_type in {"float", "integer", "number"}:
        number = _integer_value(value) if data_type == "integer" else _number_value(value)
        return (0, cast(float, number) if number is not _INVALID_VALUE else -1e308, "")
    if data_type == "boolean":
        return (1, 1.0 if value is True else 0.0, "")
    if data_type == "timestamp":
        microseconds = timestamp_microseconds(value)
        return (2, microseconds if microseconds is not None else -1e308, "")
    if data_type == "date":
        return (2, 0.0, str(value) if is_iso_date(value) else "")
    return (2, 0.0, value if isinstance(value, str) else "")


def _matches_typed_filter(
    properties: Mapping[str, object],
    filter_ast: Mapping[str, object],
    property_data_types: Mapping[str, str],
) -> bool:
    if "and" in filter_ast:
        return all(
            _matches_typed_filter(properties, item, property_data_types) for item in _filter_group(filter_ast["and"])
        )
    if "or" in filter_ast:
        return any(
            _matches_typed_filter(properties, item, property_data_types) for item in _filter_group(filter_ast["or"])
        )
    return _matches_property_filter(properties, filter_ast, property_data_types)


def _matches_property_filter(
    properties: Mapping[str, object],
    filter_ast: Mapping[str, object],
    property_data_types: Mapping[str, str],
) -> bool:
    prop = str(filter_ast["property"])
    data_type = _require_property_data_type(property_data_types, prop)
    op = str(filter_ast["op"])
    if op not in {"eq", "in", "gt", "gte", "lt", "lte", "contains"}:
        return False
    if op == "eq" and filter_ast["value"] is None:
        return properties.get(prop) is None
    current = _filter_value(properties.get(prop), data_type)
    if op == "in":
        return _matches_in_filter(current, filter_ast["value"], data_type)
    expected = _filter_value(filter_ast["value"], data_type)
    if current is _INVALID_VALUE or expected is _INVALID_VALUE:
        return False
    return _evaluate_filter(current, op, expected)


def _filter_group(value: object) -> list[Mapping[str, object]]:
    return [cast(Mapping[str, object], item) for item in cast(Sequence[object], value)]


def _filter_value(value: object, data_type: str) -> object:
    if data_type in {"float", "integer", "number"}:
        return _integer_value(value) if data_type == "integer" else _number_value(value)
    if data_type == "boolean":
        return value if isinstance(value, bool) else _INVALID_VALUE
    if data_type == "timestamp":
        microseconds = timestamp_microseconds(value)
        return microseconds if microseconds is not None else _INVALID_VALUE
    if data_type == "date":
        return value if is_iso_date(value) else _INVALID_VALUE
    return value if isinstance(value, str) else _INVALID_VALUE


def _matches_in_filter(current: object, value: object, data_type: str) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return False
    if current is _INVALID_VALUE:
        return any(item is None for item in value)
    candidates = [_filter_value(item, data_type) for item in value]
    return current in [item for item in candidates if item is not _INVALID_VALUE]


def _number_value(value: object) -> float | object:
    parsed = finite_number_value(value)
    return parsed if parsed is not None else _INVALID_VALUE


def _integer_value(value: object) -> int | object:
    parsed = integer_value(value)
    return parsed if parsed is not None else _INVALID_VALUE


def _evaluate_filter(current: object, op: str, expected: object) -> bool:
    if op == "eq":
        return current == expected
    if op == "gte":
        return _same_orderable_type(current, expected) and current >= expected  # type: ignore[operator]
    if op == "gt":
        return _same_orderable_type(current, expected) and current > expected  # type: ignore[operator]
    if op == "lte":
        return _same_orderable_type(current, expected) and current <= expected  # type: ignore[operator]
    if op == "lt":
        return _same_orderable_type(current, expected) and current < expected  # type: ignore[operator]
    return isinstance(current, str) and isinstance(expected, str) and expected.lower() in current.lower()


def _same_orderable_type(current: object, expected: object) -> bool:
    return (isinstance(current, int | float) and isinstance(expected, int | float)) or (
        isinstance(current, str) and isinstance(expected, str)
    )


def _require_property_data_type(property_data_types: Mapping[str, str], property_name: str) -> str:
    return property_data_types.get(property_name, "string")


def _fake_aggregated_groups(
    rows: list[ObjectRecordRow],
    group_by: Sequence[str],
    metrics: Sequence[ObjectAggregationMetric],
    property_data_types: Mapping[str, str],
) -> list[ObjectAggregationGroup]:
    states: dict[tuple[object, ...], dict[str, Any]] = {}
    for row in rows:
        key = {
            name: _fake_key_value(row["properties"].get(name), _require_property_data_type(property_data_types, name))
            for name in group_by
        }
        state = states.setdefault(
            tuple(key.values()),
            {"key": key, "count": 0, "values": {metric["name"]: [] for metric in metrics}},
        )
        state["count"] += 1
        for metric in metrics:
            if metric["function"] == "count":
                continue
            property_name = str(metric["property"])
            data_type = _require_property_data_type(property_data_types, property_name)
            number = (
                _integer_value(row["properties"].get(property_name))
                if data_type == "integer"
                else _number_value(row["properties"].get(property_name))
            )
            if number is not _INVALID_VALUE:
                state["values"][metric["name"]].append(cast(float, number))
    if not states and not group_by:
        states[()] = {"key": {}, "count": 0, "values": {metric["name"]: [] for metric in metrics}}
    return [_fake_group_from_state(state, metrics) for state in states.values()]


def _fake_group_from_state(state: dict[str, Any], metrics: Sequence[ObjectAggregationMetric]) -> ObjectAggregationGroup:
    computed: dict[str, float | int | None] = {}
    for metric in metrics:
        if metric["function"] == "count":
            computed[metric["name"]] = int(state["count"])
        else:
            computed[metric["name"]] = _fake_metric_value(metric["function"], state["values"][metric["name"]])
    return {"key": dict(state["key"]), "metrics": computed}


def _fake_metric_value(function: str, values: list[float]) -> float | None:
    if not values:
        return None
    if function == "sum":
        return float(sum(values))
    if function == "avg":
        return float(sum(values) / len(values))
    if function == "min":
        return float(min(values))
    return float(max(values))


def _fake_key_value(value: object, data_type: str) -> object:
    normalized = _filter_value(value, data_type)
    if normalized is _INVALID_VALUE or normalized is None:
        return None
    if data_type == "boolean":
        return bool(normalized)
    if data_type == "integer":
        return int(cast("str | int | float", normalized))
    if data_type in {"float", "number"}:
        return float(cast("str | int | float", normalized))
    if data_type == "timestamp":
        return timestamp_from_microseconds(normalized)
    return str(normalized)


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectReadHarness:
    if request.param == "fake":
        return FakeObjectReadHarness(FakeObjectReadRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyObjectReadHarness(SqlAlchemyObjectReadRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyObjectReadHarness(
        SqlAlchemyObjectReadRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_object_read_repository_contract_reads_one_record_by_tenant(harness: ObjectReadHarness) -> None:
    harness.add_object(row_id="obj_order_1", object_id="O-1", object_version=3)
    harness.add_object(row_id="obj_other_tenant", tenant_id="tenant-other", object_id="O-1")

    with harness.transaction() as transaction:
        row = harness.repository.object_record(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_id="O-1",
        )
        missing = harness.repository.object_record(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_id="O-missing",
        )

    assert row is not None
    assert row["id"] == "obj_order_1"
    assert row["object_version"] == 3
    assert missing is None


def test_object_read_repository_contract_lists_active_rows_in_object_id_order(
    harness: ObjectReadHarness,
) -> None:
    harness.add_object(row_id="obj_order_2", object_id="O-2")
    harness.add_object(row_id="obj_order_deleted", object_id="O-0", deleted=True)
    harness.add_object(row_id="obj_order_1", object_id="O-1")
    harness.add_object(
        row_id="obj_customer_1",
        object_type_id="ot_customer",
        object_type_api_name="Customer",
        object_id="C-1",
    )
    harness.add_object(row_id="obj_other_tenant", tenant_id="tenant-other", object_id="O-3")

    with harness.transaction() as transaction:
        rows = harness.repository.active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
        )

    assert [row["object_id"] for row in rows] == ["O-1", "O-2"]


def test_object_read_repository_contract_ignores_shadow_index_rows(
    harness: ObjectReadHarness,
) -> None:
    harness.add_object(row_id="obj_active", object_id="O-1", properties={"status": "ACTIVE"})
    harness.add_object(
        row_id="obj_shadow",
        object_id="O-1",
        properties={"status": "SHADOW"},
        index_version="index_run_shadow",
        is_active=False,
    )

    with harness.transaction() as transaction:
        row = harness.repository.object_record(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_id="O-1",
        )
        rows = harness.repository.active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
        )

    assert row is not None
    assert row["properties"]["status"] == "ACTIVE"
    assert [item["id"] for item in rows] == ["obj_active"]


def test_object_read_repository_contract_scopes_rows_by_object_type_id_when_requested(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_new", object_type_id="ot_new", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_old", object_type_id="ot_old", object_id="O-1")

    with harness.transaction() as transaction:
        legacy = harness.repository.object_record(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_id="O-1",
        )
        scoped = harness.repository.object_record(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_id="O-1",
            object_type_id="ot_new",
        )
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[],
            cursor=None,
            limit=10,
            object_type_id="ot_new",
        )

    assert legacy is not None
    assert scoped is None
    assert rows == []


def test_object_read_repository_contract_scopes_property_types_to_active_object_type(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_old_amount", object_type_id="ot_old", api_name="amount", data_type="string")
    harness.add_property_type(
        row_id="pt_new_amount",
        object_type_id="ot_new",
        ontology_version_id="ont_2",
        api_name="amount",
        data_type="float",
    )
    harness.add_object(row_id="obj_10", object_type_id="ot_old", object_id="O-10", properties={"amount": "10"})
    harness.add_object(row_id="obj_02", object_type_id="ot_old", object_id="O-02", properties={"amount": "2"})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": "gte", "value": "7"},
            order_by=[{"property": "amount", "direction": "desc"}],
            cursor=None,
            limit=10,
            object_type_id=None,
            property_object_type_id="ot_new",
        )

    assert [row["object_id"] for row in rows] == ["O-10"]


def test_object_read_repository_contract_queries_active_rows_with_db_keyset_page(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_property_type(row_id="pt_status", api_name="status", data_type="string")
    harness.add_object(row_id="obj_order_1", object_id="O-1", properties={"amount": 2.0, "status": "PENDING"})
    harness.add_object(row_id="obj_order_2", object_id="O-2", properties={"amount": 10.0, "status": "PENDING"})
    harness.add_object(row_id="obj_order_3", object_id="O-3", properties={"amount": 10.0, "status": "REVIEW"})
    harness.add_object(row_id="obj_order_4", object_id="O-4", properties={"amount": 5.0, "status": "PENDING"})
    order_by: list[ObjectOrderBy] = [{"property": "amount", "direction": "desc"}]

    with harness.transaction() as transaction:
        first_page = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
            order_by=order_by,
            cursor=None,
            limit=1,
        )
        second_page = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
            order_by=order_by,
            cursor={"values": [10.0], "object_id": "O-2"},
            limit=1,
        )
        remaining_page = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
            order_by=order_by,
            cursor={"values": [10.0], "object_id": "O-3"},
            limit=2,
        )
        ascending_page = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[{"property": "amount", "direction": "asc"}],
            cursor=None,
            limit=4,
        )

    assert [row["object_id"] for row in first_page] == ["O-2"]
    assert [row["object_id"] for row in second_page] == ["O-3"]
    assert [row["object_id"] for row in remaining_page] == ["O-4", "O-1"]
    assert [row["object_id"] for row in ascending_page] == ["O-1", "O-4", "O-2", "O-3"]


def test_object_read_repository_contract_casts_numeric_property_sort_and_cursor(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_order_10", object_id="O-10", properties={"amount": "10"})
    harness.add_object(row_id="obj_order_02", object_id="O-02", properties={"amount": "2"})
    harness.add_object(row_id="obj_order_07", object_id="O-07", properties={"amount": 7})
    order_by: list[ObjectOrderBy] = [{"property": "amount", "direction": "desc"}]

    with harness.transaction() as transaction:
        first_page = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=order_by,
            cursor=None,
            limit=1,
        )
        second_page = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=order_by,
            cursor={"values": ["10"], "object_id": "O-10"},
            limit=2,
        )

    assert [row["object_id"] for row in first_page] == ["O-10"]
    assert [row["object_id"] for row in second_page] == ["O-07", "O-02"]


def test_object_query_numeric_property_casts_for_sort_and_filter(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_order_10", object_id="O-10", properties={"amount": "10"})
    harness.add_object(row_id="obj_order_02", object_id="O-02", properties={"amount": "2"})
    harness.add_object(row_id="obj_order_07", object_id="O-07", properties={"amount": 7})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": "gte", "value": "7"},
            order_by=[{"property": "amount", "direction": "asc"}],
            cursor=None,
            limit=3,
        )

    assert [row["object_id"] for row in rows] == ["O-07", "O-10"]


def test_object_read_repository_contract_preserves_null_and_empty_string_filter_semantics(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_note", api_name="note", data_type="string")
    harness.add_object(row_id="obj_null", object_id="O-null", properties={"note": None})
    harness.add_object(row_id="obj_missing", object_id="O-missing", properties={"status": "PENDING"})
    harness.add_object(row_id="obj_empty", object_id="O-empty", properties={"note": ""})

    with harness.transaction() as transaction:
        null_rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "note", "op": "eq", "value": None},
            order_by=[],
            cursor=None,
            limit=10,
        )
        empty_rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "note", "op": "eq", "value": ""},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert {row["object_id"] for row in null_rows} == {"O-null", "O-missing"}
    assert [row["object_id"] for row in empty_rows] == ["O-empty"]


def test_object_read_repository_contract_rejects_cross_type_contains_and_equality(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_note", api_name="note", data_type="string")
    harness.add_object(row_id="obj_number", object_id="O-number", properties={"note": 123})
    harness.add_object(row_id="obj_text", object_id="O-text", properties={"note": "123"})

    with harness.transaction() as transaction:
        numeric_eq_rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "note", "op": "eq", "value": 123},
            order_by=[],
            cursor=None,
            limit=10,
        )
        numeric_contains_rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "note", "op": "contains", "value": 23},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert numeric_eq_rows == []
    assert numeric_contains_rows == []


def test_object_read_repository_contract_string_filter_does_not_coerce_json_numbers(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_note", api_name="note", data_type="string")
    harness.add_object(row_id="obj_number", object_id="O-number", properties={"note": 123})
    harness.add_object(row_id="obj_text", object_id="O-text", properties={"note": "123"})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "note", "op": "eq", "value": "123"},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-text"]


def test_object_read_repository_contract_invalid_stored_number_fails_closed_without_query_error(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_invalid", object_id="O-invalid", properties={"amount": "not-a-number"})
    harness.add_object(row_id="obj_valid", object_id="O-valid", properties={"amount": "7"})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": "gte", "value": 5},
            order_by=[{"property": "amount", "direction": "asc"}],
            cursor=None,
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-valid"]


def test_object_read_repository_contract_numeric_filter_does_not_treat_json_boolean_as_one(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_boolean", object_id="O-boolean", properties={"amount": True})
    harness.add_object(row_id="obj_number", object_id="O-number", properties={"amount": 1})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": "eq", "value": 1},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-number"]


def test_object_read_repository_contract_integer_filter_rejects_fractional_storage_and_operand(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_count", api_name="count", data_type="integer")
    harness.add_object(row_id="obj_fraction", object_id="O-fraction", properties={"count": 1.5})
    harness.add_object(row_id="obj_integer", object_id="O-integer", properties={"count": 2})

    with harness.transaction() as transaction:
        fractional_operand = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "count", "op": "gte", "value": 1.5},
            order_by=[],
            cursor=None,
            limit=10,
        )
        integer_operand = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "count", "op": "gte", "value": 1},
            order_by=[{"property": "count", "direction": "asc"}],
            cursor=None,
            limit=10,
        )

    assert fractional_operand == []
    assert [row["object_id"] for row in integer_operand] == ["O-integer"]


def test_object_read_repository_contract_invalid_stored_date_never_matches_range(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_due_date", api_name="dueDate", data_type="date")
    harness.add_object(row_id="obj_invalid", object_id="O-invalid", properties={"dueDate": "not-a-date"})
    harness.add_object(row_id="obj_valid", object_id="O-valid", properties={"dueDate": "2026-01-01"})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "dueDate", "op": "gte", "value": "2025-12-31"},
            order_by=[{"property": "dueDate", "direction": "asc"}],
            cursor=None,
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-valid"]


@pytest.mark.parametrize(
    ("data_type", "invalid_value", "valid_value"),
    [
        ("date", "not-a-date", "2026-01-01"),
        ("string", 123, "abc"),
        ("boolean", "false", True),
    ],
)
def test_object_read_repository_contract_cursor_normalizes_invalid_stored_sort_values(
    harness: ObjectReadHarness,
    data_type: str,
    invalid_value: object,
    valid_value: object,
) -> None:
    harness.add_property_type(row_id="pt_value", api_name="value", data_type=data_type)
    harness.add_object(row_id="obj_invalid", object_id="O-invalid", properties={"value": invalid_value})
    harness.add_object(row_id="obj_valid", object_id="O-valid", properties={"value": valid_value})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[{"property": "value", "direction": "asc"}],
            cursor={"values": [invalid_value], "object_id": "O-invalid"},
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-valid"]


@pytest.mark.parametrize(("needle", "expected_id"), [("%", "O-percent"), ("_", "O-underscore"), ("/", "O-slash")])
def test_object_read_repository_contract_contains_treats_sql_wildcards_as_literal_text(
    harness: ObjectReadHarness,
    needle: str,
    expected_id: str,
) -> None:
    harness.add_property_type(row_id="pt_note", api_name="note", data_type="string")
    harness.add_object(row_id="obj_plain", object_id="O-plain", properties={"note": "ordinary text"})
    harness.add_object(row_id="obj_percent", object_id="O-percent", properties={"note": "margin 10%"})
    harness.add_object(row_id="obj_underscore", object_id="O-underscore", properties={"note": "snake_case"})
    harness.add_object(row_id="obj_slash", object_id="O-slash", properties={"note": "path/name"})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "note", "op": "contains", "value": needle},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert [row["object_id"] for row in rows] == [expected_id]


@pytest.mark.parametrize("invalid_number", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
def test_object_read_repository_contract_rejects_non_finite_numeric_filter_values(
    harness: ObjectReadHarness,
    invalid_number: object,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_order", object_id="O-1", properties={"amount": 10})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": "eq", "value": invalid_number},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert rows == []


@pytest.mark.parametrize(
    ("op", "expected_ids"),
    [("gt", ["O-10"]), ("gte", ["O-07", "O-10"]), ("lt", ["O-02"]), ("lte", ["O-02", "O-07"])],
)
def test_object_read_repository_contract_supports_every_declared_numeric_range_operator(
    harness: ObjectReadHarness,
    op: str,
    expected_ids: list[str],
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_order_02", object_id="O-02", properties={"amount": 2})
    harness.add_object(row_id="obj_order_07", object_id="O-07", properties={"amount": 7})
    harness.add_object(row_id="obj_order_10", object_id="O-10", properties={"amount": 10})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": op, "value": 7},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert [row["object_id"] for row in rows] == expected_ids


def test_object_read_repository_contract_compares_timestamp_instants_not_offset_text(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_scheduled_at", api_name="scheduledAt", data_type="timestamp")
    harness.add_object(
        row_id="obj_early_offset",
        object_id="O-early",
        properties={"scheduledAt": "2026-01-01T01:00:00+02:00"},
    )
    harness.add_object(
        row_id="obj_middle_utc",
        object_id="O-middle",
        properties={"scheduledAt": "2025-12-31T23:30:00Z"},
    )
    harness.add_object(
        row_id="obj_late_utc",
        object_id="O-late",
        properties={"scheduledAt": "2026-01-01T00:00:00Z"},
    )

    with harness.transaction() as transaction:
        ordered = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[{"property": "scheduledAt", "direction": "asc"}],
            cursor=None,
            limit=10,
        )
        equal = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "scheduledAt", "op": "eq", "value": "2025-12-31T23:00:00Z"},
            order_by=[],
            cursor=None,
            limit=10,
        )
        after_cursor = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[{"property": "scheduledAt", "direction": "asc"}],
            cursor={"values": ["2025-12-31T23:00:00Z"], "object_id": "O-early"},
            limit=10,
        )

    assert [row["object_id"] for row in ordered] == ["O-early", "O-middle", "O-late"]
    assert [row["object_id"] for row in equal] == ["O-early"]
    assert [row["object_id"] for row in after_cursor] == ["O-middle", "O-late"]


@pytest.mark.parametrize("invalid_timestamp", ["2026-01-01", "2026-01-01 00:00:00Z", "not-a-timestamp"])
def test_object_read_repository_contract_rejects_invalid_timestamp_filter_values(
    harness: ObjectReadHarness,
    invalid_timestamp: str,
) -> None:
    harness.add_property_type(row_id="pt_scheduled_at", api_name="scheduledAt", data_type="timestamp")
    harness.add_object(
        row_id="obj_invalid",
        object_id="O-invalid",
        properties={"scheduledAt": invalid_timestamp},
    )

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "scheduledAt", "op": "eq", "value": invalid_timestamp},
            order_by=[],
            cursor=None,
            limit=10,
        )

    assert rows == []


def test_object_read_repository_contract_timestamp_cursor_preserves_fractional_seconds(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_scheduled_at", api_name="scheduledAt", data_type="timestamp")
    harness.add_object(
        row_id="obj_100ms",
        object_id="O-100ms",
        properties={"scheduledAt": "2026-01-01T00:00:00.100Z"},
    )
    harness.add_object(
        row_id="obj_900ms",
        object_id="O-900ms",
        properties={"scheduledAt": "2026-01-01T00:00:00.900Z"},
    )

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[{"property": "scheduledAt", "direction": "asc"}],
            cursor={"values": ["2026-01-01T00:00:00.100Z"], "object_id": "O-100ms"},
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-900ms"]


def test_object_read_repository_contract_timestamp_cursor_preserves_microseconds(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_scheduled_at", api_name="scheduledAt", data_type="timestamp")
    harness.add_object(
        row_id="obj_1us",
        object_id="O-1us",
        properties={"scheduledAt": "2026-01-01T00:00:00.000001Z"},
    )
    harness.add_object(
        row_id="obj_2us",
        object_id="O-2us",
        properties={"scheduledAt": "2026-01-01T00:00:00.000002Z"},
    )

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            order_by=[{"property": "scheduledAt", "direction": "asc"}],
            cursor={"values": ["2026-01-01T00:00:00.000001Z"], "object_id": "O-1us"},
            limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-2us"]


def test_object_read_repository_contract_lists_active_outgoing_links(
    harness: ObjectReadHarness,
) -> None:
    harness.add_link(row_id="link_1", to_object_id="C-1")
    harness.add_link(
        row_id="link_shadow",
        to_object_id="C-shadow",
        index_version="index_run_shadow",
        is_active=False,
    )
    harness.add_link(row_id="link_deleted", to_object_id="C-deleted", deleted=True)
    harness.add_link(row_id="link_other_type", link_type_api_name="OrderWarehouse", to_object_id="W-1")
    harness.add_link(row_id="link_other_tenant", tenant_id="tenant-other", to_object_id="C-other")

    with harness.transaction() as transaction:
        rows = harness.repository.active_links_from(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_api_name="OrderCustomer",
            from_api_name="Order",
            from_object_id="O-1",
        )

    assert [row["id"] for row in rows] == ["link_1"]
    assert rows[0]["to_api_name"] == "Customer"
    assert rows[0]["to_object_id"] == "C-1"


def test_object_read_repository_contract_lists_active_incoming_links(
    harness: ObjectReadHarness,
) -> None:
    harness.add_link(row_id="link_1", from_object_id="O-1", to_object_id="C-1")
    harness.add_link(row_id="link_2", from_object_id="O-2", to_object_id="C-1")
    harness.add_link(
        row_id="link_shadow",
        from_object_id="O-shadow",
        to_object_id="C-1",
        index_version="index_run_shadow",
        is_active=False,
    )
    harness.add_link(row_id="link_deleted", from_object_id="O-deleted", to_object_id="C-1", deleted=True)
    harness.add_link(
        row_id="link_other_type",
        link_type_id="lt_order_warehouse",
        link_type_api_name="OrderWarehouse",
        to_object_id="C-1",
    )
    harness.add_link(row_id="link_other_tenant", tenant_id="tenant-other", to_object_id="C-1")

    with harness.transaction() as transaction:
        rows = harness.repository.active_links_to(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_api_name="OrderCustomer",
            to_api_name="Customer",
            to_object_id="C-1",
        )

    assert [row["id"] for row in rows] == ["link_1", "link_2"]
    assert [row["from_object_id"] for row in rows] == ["O-1", "O-2"]


def test_object_read_repository_contract_caps_link_fanout_with_limit(
    harness: ObjectReadHarness,
) -> None:
    for index in range(5):
        harness.add_link(row_id=f"link_{index}", to_object_id=f"C-{index}")

    with harness.transaction() as transaction:
        outgoing = harness.repository.active_links_from(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_api_name="OrderCustomer",
            from_api_name="Order",
            from_object_id="O-1",
            limit=2,
        )
        incoming = harness.repository.active_links_to(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_api_name="OrderCustomer",
            to_api_name="Customer",
            to_object_id="C-1",
            limit=1,
        )

    assert len(outgoing) == 2
    assert len(incoming) == 1


def test_object_read_repository_contract_reads_many_records_in_one_batch(
    harness: ObjectReadHarness,
) -> None:
    harness.add_object(row_id="obj_order_1", object_id="O-1")
    harness.add_object(row_id="obj_order_2", object_id="O-2")
    harness.add_object(row_id="obj_other_tenant", tenant_id="tenant-other", object_id="O-2")

    with harness.transaction() as transaction:
        rows = harness.repository.object_records(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_ids=["O-1", "O-2", "O-missing"],
        )

    assert {row["object_id"] for row in rows} == {"O-1", "O-2"}
    assert all(row["tenant_id"] == "tenant-demo" for row in rows)


def test_object_read_repository_contract_reads_latest_object_change_sequence(
    harness: ObjectReadHarness,
) -> None:
    harness.add_object(row_id="obj_1", object_id="O-1", object_change_sequence=4)
    harness.add_object(row_id="obj_2", object_id="O-2", object_change_sequence=9)
    harness.add_object(row_id="obj_3", object_id="O-3", object_change_sequence=12, deleted=True)
    harness.add_object(
        row_id="obj_other",
        object_type_api_name="Customer",
        object_type_id="ot_customer",
        object_id="C-1",
        object_change_sequence=99,
    )

    with harness.transaction() as transaction:
        including_deleted = harness.repository.latest_object_change_sequence(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            include_deleted=True,
        )
        active_only = harness.repository.latest_object_change_sequence(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            include_deleted=False,
        )

    assert including_deleted == 12
    assert active_only == 9


def test_object_read_repository_contract_aggregates_groups_with_metrics(harness: ObjectReadHarness) -> None:
    harness.add_property_type(row_id="pt_status", api_name="status", data_type="string")
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_1", object_id="O-1", properties={"status": "PENDING", "amount": 10.0})
    harness.add_object(row_id="obj_2", object_id="O-2", properties={"status": "PENDING", "amount": 30.0})
    harness.add_object(row_id="obj_3", object_id="O-3", properties={"status": "REVIEW", "amount": 5.0})
    harness.add_object(row_id="obj_deleted", object_id="O-4", deleted=True, properties={"status": "PENDING"})
    harness.add_object(
        row_id="obj_other_tenant",
        tenant_id="tenant-other",
        object_id="O-5",
        properties={"status": "PENDING", "amount": 999.0},
    )
    metrics: list[ObjectAggregationMetric] = [
        {"name": "count", "function": "count", "property": None},
        {"name": "sum_amount", "function": "sum", "property": "amount"},
        {"name": "avg_amount", "function": "avg", "property": "amount"},
        {"name": "min_amount", "function": "min", "property": "amount"},
        {"name": "max_amount", "function": "max", "property": "amount"},
    ]

    with harness.transaction() as transaction:
        groups = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            group_by=["status"],
            metrics=metrics,
            group_limit=10,
        )

    by_status = {group["key"]["status"]: group["metrics"] for group in groups}
    assert by_status == {
        "PENDING": {"count": 2, "sum_amount": 40.0, "avg_amount": 20.0, "min_amount": 10.0, "max_amount": 30.0},
        "REVIEW": {"count": 1, "sum_amount": 5.0, "avg_amount": 5.0, "min_amount": 5.0, "max_amount": 5.0},
    }


def test_object_read_repository_contract_aggregate_ignores_invalid_numeric_storage(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_valid", object_id="O-valid", properties={"amount": 10.0})
    harness.add_object(row_id="obj_invalid", object_id="O-invalid", properties={"amount": "not-a-number"})
    harness.add_object(row_id="obj_boolean", object_id="O-boolean", properties={"amount": True})
    metrics: list[ObjectAggregationMetric] = [
        {"name": "count", "function": "count", "property": None},
        {"name": "sum_amount", "function": "sum", "property": "amount"},
        {"name": "avg_amount", "function": "avg", "property": "amount"},
        {"name": "min_amount", "function": "min", "property": "amount"},
        {"name": "max_amount", "function": "max", "property": "amount"},
    ]

    with harness.transaction() as transaction:
        groups = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            group_by=[],
            metrics=metrics,
            group_limit=10,
        )

    assert groups == [
        {
            "key": {},
            "metrics": {
                "count": 3,
                "sum_amount": 10.0,
                "avg_amount": 10.0,
                "min_amount": 10.0,
                "max_amount": 10.0,
            },
        }
    ]


def test_object_read_repository_contract_aggregate_normalizes_timestamp_groups_without_precision_loss(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_observed_at", api_name="observedAt", data_type="timestamp")
    maximum_timestamp = "9999-12-31T23:59:59.999999Z"
    harness.add_object(row_id="obj_valid", object_id="O-valid", properties={"observedAt": maximum_timestamp})
    harness.add_object(row_id="obj_naive", object_id="O-naive", properties={"observedAt": "2026-01-01T00:00:00"})
    harness.add_object(row_id="obj_number", object_id="O-number", properties={"observedAt": 123})

    with harness.transaction() as transaction:
        groups = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            group_by=["observedAt"],
            metrics=[{"name": "count", "function": "count", "property": None}],
            group_limit=10,
        )

    by_timestamp = {group["key"]["observedAt"]: group["metrics"]["count"] for group in groups}
    assert by_timestamp == {None: 2, maximum_timestamp: 1}


def test_object_read_repository_contract_numeric_strings_use_one_decimal_grammar(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    for index, value in enumerate(("12", "+12", "01", ".5", "1.", "1e1", "1_2", " 12 ")):
        harness.add_object(row_id=f"obj_{index}", object_id=f"O-{index}", properties={"amount": value})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "amount", "op": "eq", "value": 12},
            order_by=[],
            cursor=None,
            limit=20,
        )

    assert {row["object_id"] for row in rows} == {"O-0", "O-1"}


def test_object_read_repository_contract_integer_range_is_safe_and_backend_independent(
    harness: ObjectReadHarness,
) -> None:
    maximum = 2**31 - 1
    harness.add_property_type(row_id="pt_quantity", api_name="quantity", data_type="integer")
    harness.add_object(row_id="obj_max", object_id="O-max", properties={"quantity": maximum})
    harness.add_object(row_id="obj_large", object_id="O-large", properties={"quantity": 2**31})
    harness.add_object(row_id="obj_huge", object_id="O-huge", properties={"quantity": 10**100})

    with harness.transaction() as transaction:
        rows = harness.repository.query_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "quantity", "op": "eq", "value": maximum},
            order_by=[{"property": "quantity", "direction": "asc"}],
            cursor=None,
            limit=20,
        )
        groups = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            group_by=["quantity"],
            metrics=[{"name": "count", "function": "count", "property": None}],
            group_limit=10,
        )

    assert [row["object_id"] for row in rows] == ["O-max"]
    by_quantity = {group["key"]["quantity"]: group["metrics"]["count"] for group in groups}
    assert by_quantity == {None: 2, maximum: 1}


def test_object_read_repository_contract_aggregate_applies_filter_and_group_limit(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_status", api_name="status", data_type="string")
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    harness.add_object(row_id="obj_1", object_id="O-1", properties={"status": "PENDING", "amount": 10.0})
    harness.add_object(row_id="obj_2", object_id="O-2", properties={"status": "REVIEW", "amount": 30.0})
    harness.add_object(row_id="obj_3", object_id="O-3", properties={"status": "APPROVED", "amount": 5.0})
    metrics: list[ObjectAggregationMetric] = [{"name": "count", "function": "count", "property": None}]

    with harness.transaction() as transaction:
        filtered = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast={"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
            group_by=["status"],
            metrics=metrics,
            group_limit=10,
        )
        capped = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            group_by=["status"],
            metrics=metrics,
            group_limit=2,
        )

    assert {group["key"]["status"] for group in filtered} == {"PENDING", "REVIEW"}
    assert all(group["metrics"] == {"count": 1} for group in filtered)
    assert len(capped) == 2


def test_object_read_repository_contract_aggregate_without_group_by_over_empty_set(
    harness: ObjectReadHarness,
) -> None:
    harness.add_property_type(row_id="pt_amount", api_name="amount", data_type="float")
    metrics: list[ObjectAggregationMetric] = [
        {"name": "count", "function": "count", "property": None},
        {"name": "sum_amount", "function": "sum", "property": "amount"},
    ]

    with harness.transaction() as transaction:
        groups = harness.repository.aggregate_active_object_rows(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            filter_ast=None,
            group_by=[],
            metrics=metrics,
            group_limit=10,
        )

    assert groups == [{"key": {}, "metrics": {"count": 0, "sum_amount": None}}]


def test_link_traversal_never_crosses_tenant_without_policy(harness: ObjectReadHarness) -> None:
    harness.add_link(row_id="link_demo", from_object_id="O-1", to_object_id="C-1")
    harness.add_link(
        row_id="link_other_tenant",
        tenant_id="tenant-other",
        from_object_id="O-1",
        to_object_id="C-other",
    )

    with harness.transaction() as transaction:
        rows = harness.repository.active_links_from(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_api_name="OrderCustomer",
            from_api_name="Order",
            from_object_id="O-1",
        )

    assert [row["to_object_id"] for row in rows] == ["C-1"]
