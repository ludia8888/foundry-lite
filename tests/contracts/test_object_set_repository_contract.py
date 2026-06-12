from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports import (
    ObjectRecordRow,
    ObjectSetObjectTypeRow,
    ObjectSetRecord,
    ObjectSetRepository,
    ObjectSetRow,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyObjectSetRepository
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine


class ObjectSetHarness(Protocol):
    @property
    def repository(self) -> ObjectSetRepository: ...

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_object_type(self, **kwargs: Any) -> None: ...

    def add_property_type(self, **kwargs: Any) -> None: ...

    def add_object_record(self, **kwargs: Any) -> None: ...


@dataclass
class FakeObjectSetRepository:
    object_sets_store: list[ObjectSetRow] = field(default_factory=list)
    object_types: list[ObjectSetObjectTypeRow] = field(default_factory=list)
    property_types: list[dict[str, Any]] = field(default_factory=list)
    object_records: list[ObjectRecordRow] = field(default_factory=list)

    def create_object_set(self, *, transaction: Any, record: ObjectSetRecord) -> None:
        del transaction
        self.object_sets_store.append(_object_set_row(record))

    def object_set_by_id(self, *, transaction: Any, tenant_id: str, set_id: str) -> ObjectSetRow | None:
        del transaction
        for row in self.object_sets_store:
            if row["tenant_id"] == tenant_id and row["id"] == set_id:
                return cast(ObjectSetRow, dict(row))
        return None

    def object_sets(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str | None = None,
    ) -> list[ObjectSetRow]:
        del transaction
        rows = [
            cast(ObjectSetRow, dict(row))
            for row in self.object_sets_store
            if row["tenant_id"] == tenant_id and (object_type_id is None or row["object_type_id"] == object_type_id)
        ]
        return sorted(rows, key=lambda row: row["created_at"])

    def delete_object_sets(self, *, transaction: Any, tenant_id: str, set_ids: list[str]) -> None:
        del transaction
        self.object_sets_store = [
            row for row in self.object_sets_store if row["tenant_id"] != tenant_id or row["id"] not in set_ids
        ]

    def active_object_ids(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> set[str]:
        del transaction
        return {
            row["object_id"]
            for row in self.object_records
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row["object_id"] in object_ids
            and row["deleted"] is False
        }

    def active_object_records_by_ids(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> list[ObjectRecordRow]:
        del transaction
        return [
            cast(ObjectRecordRow, dict(row))
            for row in self.object_records
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row["object_id"] in object_ids
            and row["deleted"] is False
        ]

    def property_names_for_object_type(self, *, transaction: Any, object_type_id: str) -> set[str]:
        del transaction
        return {row["api_name"] for row in self.property_types if row["object_type_id"] == object_type_id}

    def object_type_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
    ) -> ObjectSetObjectTypeRow | None:
        del transaction
        for row in self.object_types:
            if row["tenant_id"] == tenant_id and row["id"] == object_type_id:
                return cast(ObjectSetObjectTypeRow, dict(row))
        return None


@dataclass
class FakeObjectSetHarness:
    repository: FakeObjectSetRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_object_type(self, **kwargs: Any) -> None:
        self.repository.object_types.append(_object_type_row(**kwargs))

    def add_property_type(self, **kwargs: Any) -> None:
        self.repository.property_types.append(_property_type_row(**kwargs))

    def add_object_record(self, **kwargs: Any) -> None:
        self.repository.object_records.append(_object_record_row(**kwargs))


@dataclass
class SqlAlchemyObjectSetHarness:
    repository: SqlAlchemyObjectSetRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def add_object_type(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_types).values(**_object_type_row(**kwargs)))

    def add_property_type(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.property_types).values(**_property_type_row(**kwargs)))

    def add_object_record(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_records).values(**_object_record_row(**kwargs)))


def _object_set_record(set_id: str, *, object_type_id: str = "ot_order") -> ObjectSetRecord:
    return ObjectSetRecord(
        set_id=set_id,
        tenant_id="tenant-demo",
        name=f"Set {set_id}",
        object_type_id=object_type_id,
        set_type="static",
        definition={"ids": ["O-1"]},
        visibility="private",
        owner_user_id="user-demo",
        expires_at=None,
        created_at=f"2026-06-10T00:00:0{set_id[-1]}Z",
    )


def _object_set_row(record: ObjectSetRecord) -> ObjectSetRow:
    return {
        "id": record.set_id,
        "tenant_id": record.tenant_id,
        "name": record.name,
        "object_type_id": record.object_type_id,
        "set_type": record.set_type,
        "definition": record.definition,
        "visibility": record.visibility,
        "owner_user_id": record.owner_user_id,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
    }


def _object_type_row(
    *,
    object_type_id: str = "ot_order",
    tenant_id: str = "tenant-demo",
    api_name: str = "Order",
) -> ObjectSetObjectTypeRow:
    return {
        "id": object_type_id,
        "tenant_id": tenant_id,
        "ontology_version_id": "ont_1",
        "api_name": api_name,
        "display_name": api_name,
        "description": None,
        "primary_key_property": "order_id",
        "backing": {"dataset": "clean.orders"},
        "config": {},
    }


def _property_type_row(
    *,
    property_id: str = "prop_status",
    object_type_id: str = "ot_order",
    api_name: str = "status",
) -> dict[str, Any]:
    return {
        "id": property_id,
        "tenant_id": "tenant-demo",
        "object_type_id": object_type_id,
        "api_name": api_name,
        "display_name": api_name,
        "data_type": "string",
        "nullable": False,
        "indexed": True,
        "searchable": True,
        "editable": True,
        "classification": None,
        "source": "dataset",
        "column_name": api_name,
        "edit_policy": "edit_wins",
        "derivation": None,
    }


def _object_record_row(
    *,
    record_id: str = "obj_order_1",
    tenant_id: str = "tenant-demo",
    object_type_id: str = "ot_order",
    object_type_api_name: str = "Order",
    object_id: str = "O-1",
    deleted: bool = False,
) -> ObjectRecordRow:
    return {
        "id": record_id,
        "tenant_id": tenant_id,
        "object_type_id": object_type_id,
        "object_type_api_name": object_type_api_name,
        "object_id": object_id,
        "properties": {"status": "PENDING"},
        "base_properties": {"status": "PENDING"},
        "edit_properties": {},
        "property_versions": {"status": 1},
        "source_dataset_version_id": "dsv_orders_1",
        "source_hash": "hash-demo",
        "object_version": 1,
        "deleted": deleted,
        "deletion_reason": "test delete" if deleted else None,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectSetHarness:
    if request.param == "fake":
        return FakeObjectSetHarness(FakeObjectSetRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyObjectSetHarness(SqlAlchemyObjectSetRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyObjectSetHarness(
        SqlAlchemyObjectSetRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_object_set_repository_contract_creates_lists_gets_and_deletes_sets(
    harness: ObjectSetHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.create_object_set(transaction=transaction, record=_object_set_record("oset_1"))
        harness.repository.create_object_set(
            transaction=transaction,
            record=_object_set_record("oset_2", object_type_id="ot_customer"),
        )

        found = harness.repository.object_set_by_id(transaction=transaction, tenant_id="tenant-demo", set_id="oset_1")
        all_sets = harness.repository.object_sets(transaction=transaction, tenant_id="tenant-demo")
        order_sets = harness.repository.object_sets(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )
        harness.repository.delete_object_sets(transaction=transaction, tenant_id="tenant-demo", set_ids=["oset_1"])
        remaining = harness.repository.object_sets(transaction=transaction, tenant_id="tenant-demo")

    assert found is not None
    assert found["id"] == "oset_1"
    assert [row["id"] for row in all_sets] == ["oset_1", "oset_2"]
    assert [row["id"] for row in order_sets] == ["oset_1"]
    assert [row["id"] for row in remaining] == ["oset_2"]


def test_object_set_repository_contract_reads_membership_and_metadata(
    harness: ObjectSetHarness,
) -> None:
    harness.add_object_type()
    harness.add_object_type(object_type_id="ot_other_tenant", tenant_id="tenant-other")
    harness.add_property_type()
    harness.add_property_type(property_id="prop_amount", api_name="amount")
    harness.add_object_record(record_id="obj_order_1", object_id="O-1")
    harness.add_object_record(record_id="obj_order_2", object_id="O-2")
    harness.add_object_record(record_id="obj_deleted", object_id="O-deleted", deleted=True)
    harness.add_object_record(record_id="obj_other_tenant", tenant_id="tenant-other", object_id="O-other")
    harness.add_object_record(
        record_id="obj_customer_1",
        object_type_id="ot_customer",
        object_type_api_name="Customer",
        object_id="C-1",
    )

    with harness.transaction() as transaction:
        object_type = harness.repository.object_type_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )
        other_tenant_object_type = harness.repository.object_type_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_other_tenant",
        )
        property_names = harness.repository.property_names_for_object_type(
            transaction=transaction,
            object_type_id="ot_order",
        )
        active_ids = harness.repository.active_object_ids(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_ids=["O-1", "O-2", "O-deleted", "O-other", "C-1"],
        )
        records = harness.repository.active_object_records_by_ids(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_api_name="Order",
            object_ids=["O-2", "O-1", "O-deleted"],
        )

    assert object_type is not None
    assert object_type["api_name"] == "Order"
    assert other_tenant_object_type is None
    assert property_names == {"status", "amount"}
    assert active_ids == {"O-1", "O-2"}
    assert {row["object_id"] for row in records} == {"O-1", "O-2"}
