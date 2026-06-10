from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import ObjectReadRepository
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyObjectReadRepository
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine


class ObjectReadHarness(Protocol):
    repository: ObjectReadRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_object(self, **kwargs: Any) -> None: ...

    def add_link(self, **kwargs: Any) -> None: ...


@dataclass
class FakeObjectReadRepository:
    object_records: list[dict[str, Any]] = field(default_factory=list)
    object_links: list[dict[str, Any]] = field(default_factory=list)

    def object_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        del transaction
        for row in self.object_records:
            if (
                row["tenant_id"] == tenant_id
                and row["object_type_api_name"] == object_type_api_name
                and row["object_id"] == object_id
            ):
                return dict(row)
        return None

    def active_object_rows(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
    ) -> list[dict[str, Any]]:
        del transaction
        rows = [
            dict(row)
            for row in self.object_records
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row["deleted"] is False
        ]
        return sorted(rows, key=lambda row: row["object_id"])

    def active_links_from(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
    ) -> list[dict[str, Any]]:
        del transaction
        return [
            dict(row)
            for row in self.object_links
            if row["tenant_id"] == tenant_id
            and row["link_type_api_name"] == link_type_api_name
            and row["from_api_name"] == from_api_name
            and row["from_object_id"] == from_object_id
            and row["deleted"] is False
        ]


@dataclass
class FakeObjectReadHarness:
    repository: FakeObjectReadRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_object(self, **kwargs: Any) -> None:
        self.repository.object_records.append(_object_row(**kwargs))

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
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "tenant_id": tenant_id,
        "object_type_id": object_type_id,
        "object_type_api_name": object_type_api_name,
        "object_id": object_id,
        "properties": properties or {"status": "PENDING"},
        "base_properties": properties or {"status": "PENDING"},
        "edit_properties": {},
        "property_versions": {"status": 1},
        "source_dataset_version_id": "dsv_orders_1",
        "source_hash": "hash-demo",
        "object_version": object_version,
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
) -> dict[str, Any]:
    return {
        "id": row_id,
        "tenant_id": tenant_id,
        "link_type_id": link_type_id,
        "link_type_api_name": link_type_api_name,
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


@pytest.fixture(params=["sqlalchemy", "fake"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectReadHarness:
    if request.param == "fake":
        return FakeObjectReadHarness(FakeObjectReadRepository())
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
    db.create_database(engine)
    return SqlAlchemyObjectReadHarness(SqlAlchemyObjectReadRepository(engine), engine)


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


def test_object_read_repository_contract_lists_active_outgoing_links(
    harness: ObjectReadHarness,
) -> None:
    harness.add_link(row_id="link_1", to_object_id="C-1")
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
