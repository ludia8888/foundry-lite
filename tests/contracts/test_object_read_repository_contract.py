from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports import (
    ObjectLinkRow,
    ObjectOrderBy,
    ObjectQueryCursor,
    ObjectReadRepository,
    ObjectRecordRow,
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
        number = _number_value(value)
        return (0, cast(float, number) if number is not _INVALID_VALUE else -1e308, "")
    if data_type == "boolean":
        return (1, 1.0 if value is True else 0.0, "")
    return (2, 0.0, str(value) if value is not None else "")


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
    current = _filter_value(properties.get(prop), data_type)
    if str(filter_ast["op"]) == "in":
        return _matches_in_filter(current, filter_ast["value"], data_type)
    expected = _filter_value(filter_ast["value"], data_type)
    if current is _INVALID_VALUE or expected is _INVALID_VALUE:
        return False
    return _evaluate_filter(current, str(filter_ast["op"]), expected)


def _filter_group(value: object) -> list[Mapping[str, object]]:
    return [cast(Mapping[str, object], item) for item in cast(Sequence[object], value)]


def _filter_value(value: object, data_type: str) -> object:
    if data_type in {"float", "integer", "number"}:
        return _number_value(value)
    if data_type == "boolean":
        return value if isinstance(value, bool) else _INVALID_VALUE
    return str(value) if value is not None else _INVALID_VALUE


def _matches_in_filter(current: object, value: object, data_type: str) -> bool:
    if current is _INVALID_VALUE or not isinstance(value, Collection) or isinstance(value, str):
        return False
    candidates = [_filter_value(item, data_type) for item in value]
    return current in [item for item in candidates if item is not _INVALID_VALUE]


def _number_value(value: object) -> float | object:
    if isinstance(value, bool):
        return _INVALID_VALUE
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return _INVALID_VALUE
    return _INVALID_VALUE


def _evaluate_filter(current: object, op: str, expected: object) -> bool:
    if op == "eq":
        return current == expected
    if op == "gte":
        return isinstance(current, int | float) and isinstance(expected, int | float) and current >= expected
    if op == "lte":
        return isinstance(current, int | float) and isinstance(expected, int | float) and current <= expected
    return str(expected).lower() in str(current).lower()


def _require_property_data_type(property_data_types: Mapping[str, str], property_name: str) -> str:
    return property_data_types.get(property_name, "string")


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
