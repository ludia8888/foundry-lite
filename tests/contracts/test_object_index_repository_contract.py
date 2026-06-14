from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports import (
    IndexRunCursor,
    IndexRunError,
    IndexRunRecord,
    LinkTypeRow,
    ObjectConflictRecord,
    ObjectIndexLinkRow,
    ObjectIndexRepository,
    ObjectLinkInsert,
    ObjectRecordCdcUpdate,
    ObjectRecordInsert,
    ObjectRecordRow,
    ObjectRecordSourceUpdate,
)
from foundry_lite.application.ports.object_index_repository import IndexRunRow
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyObjectIndexRepository
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class ObjectIndexHarness(Protocol):
    repository: ObjectIndexRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_link_type(self, **kwargs: Any) -> None: ...

    def add_link(self, record: ObjectLinkInsert) -> None: ...

    def index_run(self, run_id: str) -> dict[str, Any] | None: ...

    def object_record(self, record_id: str) -> dict[str, Any] | None: ...

    def conflicts(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeObjectIndexRepository:
    index_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    object_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    object_conflicts: list[dict[str, Any]] = field(default_factory=list)
    link_types: list[dict[str, Any]] = field(default_factory=list)
    object_links: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_index_versions: dict[tuple[str, str], str] = field(default_factory=dict)

    def index_run_by_id(self, *, transaction: Any, tenant_id: str, run_id: str) -> IndexRunRow | None:
        del transaction
        row = self.index_runs.get(run_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return cast(IndexRunRow, dict(row))

    def create_index_run(self, *, transaction: Any, record: IndexRunRecord) -> None:
        del transaction
        self.index_runs[record.run_id] = _index_run_row(record)

    def active_index_version(self, *, transaction: Any, tenant_id: str, object_type_id: str) -> str:
        del transaction
        pointer = self.active_index_versions.get((tenant_id, object_type_id))
        if pointer is not None:
            return pointer
        for row in self.object_records.values():
            if row["tenant_id"] == tenant_id and row["object_type_id"] == object_type_id and row["is_active"]:
                return str(row["index_version"])
        return "active"

    def mark_index_run_succeeded(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        rows_read: int,
        objects_upserted: int,
        objects_deleted: int,
        links_upserted: int,
        cursor: IndexRunCursor,
        completed_at: str,
    ) -> None:
        del transaction
        if self.index_runs[run_id]["tenant_id"] != tenant_id:
            return
        self.index_runs[run_id].update(
            status="succeeded",
            rows_read=rows_read,
            objects_upserted=objects_upserted,
            objects_deleted=objects_deleted,
            links_upserted=links_upserted,
            cursor=cursor,
            completed_at=completed_at,
        )

    def mark_index_run_failed(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        error: IndexRunError,
        completed_at: str,
    ) -> None:
        del transaction
        if self.index_runs[run_id]["tenant_id"] == tenant_id:
            self.index_runs[run_id].update(status="failed", error=error, completed_at=completed_at)

    def insert_object_record(self, *, transaction: Any, record: ObjectRecordInsert) -> None:
        del transaction
        self.object_records[record.record_id] = _object_record_row(record)

    def object_record_in_index(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
        index_version: str,
    ) -> ObjectRecordRow | None:
        del transaction
        for row in self.object_records.values():
            if (
                row["tenant_id"] == tenant_id
                and row["object_type_api_name"] == object_type_api_name
                and row["object_id"] == object_id
                and row["index_version"] == index_version
            ):
                return cast(ObjectRecordRow, dict(row))
        return None

    def object_records_for_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
    ) -> list[ObjectRecordRow]:
        del transaction
        return [
            cast(ObjectRecordRow, dict(row))
            for row in sorted(self.object_records.values(), key=lambda item: item["object_id"])
            if row["tenant_id"] == tenant_id
            and row["object_type_id"] == object_type_id
            and row["index_version"] == index_version
        ]

    def update_object_record_from_source(self, *, transaction: Any, record: ObjectRecordSourceUpdate) -> None:
        del transaction
        if self.object_records[record.record_id]["tenant_id"] != record.tenant_id:
            return
        self.object_records[record.record_id].update(
            properties=record.properties,
            base_properties=record.base_properties,
            source_dataset_version_id=record.source_dataset_version_id,
            source_hash=record.source_hash,
            object_version=record.object_version,
            updated_at=record.updated_at,
        )

    def update_object_record_from_cdc(self, *, transaction: Any, record: ObjectRecordCdcUpdate) -> None:
        del transaction
        if self.object_records[record.record_id]["tenant_id"] != record.tenant_id:
            return
        self.object_records[record.record_id].update(
            properties=record.properties,
            base_properties=record.base_properties,
            property_versions=record.property_versions,
            source_dataset_version_id=record.source_dataset_version_id,
            source_hash=record.source_hash,
            object_version=record.object_version,
            deleted=record.deleted,
            deletion_reason=record.deletion_reason,
            updated_at=record.updated_at,
        )

    def insert_object_conflict(self, *, transaction: Any, record: ObjectConflictRecord) -> None:
        del transaction
        self.object_conflicts.append(_conflict_row(record))

    def link_types_for_object_type(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        from_object_type_id: str,
    ) -> list[LinkTypeRow]:
        del transaction
        return [
            cast(LinkTypeRow, dict(row))
            for row in self.link_types
            if row["tenant_id"] == tenant_id
            and row["ontology_version_id"] == ontology_version_id
            and row["from_object_type_id"] == from_object_type_id
        ]

    def object_link(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_id: str,
        from_object_id: str,
        to_object_id: str,
        index_version: str,
    ) -> ObjectIndexLinkRow | None:
        del transaction
        for row in self.object_links.values():
            if (
                row["tenant_id"] == tenant_id
                and row["link_type_id"] == link_type_id
                and row["from_object_id"] == from_object_id
                and row["to_object_id"] == to_object_id
                and row["index_version"] == index_version
            ):
                return cast(ObjectIndexLinkRow, dict(row))
        return None

    def refresh_object_link(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_id: str,
        link_version: int,
        source_dataset_version_id: str,
        updated_at: str,
    ) -> None:
        del transaction
        if self.object_links[link_id]["tenant_id"] != tenant_id:
            return
        self.object_links[link_id].update(
            link_version=link_version,
            source_dataset_version_id=source_dataset_version_id,
            deleted=False,
            updated_at=updated_at,
        )

    def insert_object_link(self, *, transaction: Any, record: ObjectLinkInsert) -> None:
        del transaction
        self.object_links[record.link_id] = _link_row(record)

    def switch_active_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
        updated_at: str,
    ) -> None:
        del transaction
        for row in self.object_records.values():
            if row["tenant_id"] == tenant_id and row["object_type_id"] == object_type_id:
                row["is_active"] = row["index_version"] == index_version
                row["updated_at"] = updated_at
        for row in self.object_links.values():
            if row["tenant_id"] == tenant_id and row["from_object_type_id"] == object_type_id:
                row["is_active"] = row["index_version"] == index_version
                row["updated_at"] = updated_at
        self.active_index_versions[(tenant_id, object_type_id)] = index_version

    def delete_inactive_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
    ) -> None:
        del transaction
        self.object_records = {
            key: row
            for key, row in self.object_records.items()
            if not _matches_inactive_index(row, tenant_id, object_type_id, index_version)
        }
        self.object_links = {
            key: row
            for key, row in self.object_links.items()
            if not _matches_inactive_link_index(row, tenant_id, object_type_id, index_version)
        }


@dataclass
class FakeObjectIndexHarness:
    repository: ObjectIndexRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_link_type(self, **kwargs: Any) -> None:
        assert isinstance(self.repository, FakeObjectIndexRepository)
        self.repository.link_types.append(_link_type_row(**kwargs))

    def add_link(self, record: ObjectLinkInsert) -> None:
        assert isinstance(self.repository, FakeObjectIndexRepository)
        self.repository.object_links[record.link_id] = _link_row(record)

    def index_run(self, run_id: str) -> dict[str, Any] | None:
        assert isinstance(self.repository, FakeObjectIndexRepository)
        row = self.repository.index_runs.get(run_id)
        return dict(row) if row else None

    def object_record(self, record_id: str) -> dict[str, Any] | None:
        assert isinstance(self.repository, FakeObjectIndexRepository)
        row = self.repository.object_records.get(record_id)
        return dict(row) if row else None

    def conflicts(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeObjectIndexRepository)
        return [dict(row) for row in self.repository.object_conflicts]


@dataclass
class SqlAlchemyObjectIndexHarness:
    repository: ObjectIndexRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def add_link_type(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.link_types).values(**_link_type_row(**kwargs)))

    def add_link(self, record: ObjectLinkInsert) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_links).values(**_link_row(record)))

    def index_run(self, run_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(db.index_runs).where(db.index_runs.c.id == run_id)).mappings().first()
            return dict(row) if row else None

    def object_record(self, record_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(db.object_records).where(db.object_records.c.id == record_id)).mappings().first()
            return dict(row) if row else None

    def conflicts(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(db.object_conflicts)).mappings().all()
            return [dict(row) for row in rows]


def _index_run_record(run_id: str, *, status: str = "running") -> IndexRunRecord:
    return IndexRunRecord(
        run_id=run_id,
        tenant_id="tenant-demo",
        object_type_id="ot_order",
        object_type_api_name="Order",
        trigger_type="reindex",
        source_ref={"dataset_version_id": "dsv_orders_1"},
        status=status,
        cursor={},
        rows_read=0,
        objects_upserted=0,
        objects_deleted=0,
        links_upserted=0,
        error=None,
        started_at="2026-06-10T00:00:00Z",
        completed_at=None,
        created_at="2026-06-10T00:00:00Z",
    )


def _object_insert_record(
    record_id: str = "obj_order_1",
    *,
    index_version: str = "active",
    is_active: bool = True,
) -> ObjectRecordInsert:
    return ObjectRecordInsert(
        record_id=record_id,
        tenant_id="tenant-demo",
        object_type_id="ot_order",
        object_type_api_name="Order",
        object_id="O-1",
        properties={"status": "PENDING", "amount": 100},
        base_properties={"status": "PENDING", "amount": 100},
        edit_properties={},
        property_versions={"status": 1, "amount": 1},
        source_dataset_version_id="dsv_orders_1",
        source_hash="hash-v1",
        object_version=1,
        deleted=False,
        deletion_reason=None,
        created_at="2026-06-10T00:00:00Z",
        updated_at="2026-06-10T00:00:00Z",
        index_version=index_version,
        is_active=is_active,
    )


def _object_update_record(record_id: str = "obj_order_1") -> ObjectRecordSourceUpdate:
    return ObjectRecordSourceUpdate(
        record_id=record_id,
        tenant_id="tenant-demo",
        properties={"status": "REVIEW", "amount": 120},
        base_properties={"status": "REVIEW", "amount": 120},
        source_dataset_version_id="dsv_orders_2",
        source_hash="hash-v2",
        object_version=2,
        updated_at="2026-06-10T00:01:00Z",
    )


def _object_cdc_update_record(record_id: str = "obj_order_1") -> ObjectRecordCdcUpdate:
    return ObjectRecordCdcUpdate(
        record_id=record_id,
        tenant_id="tenant-demo",
        properties={"status": "CANCELLED", "amount": 120},
        base_properties={"status": "CANCELLED", "amount": 120},
        property_versions={"status": 3, "amount": 2, "_cdc": {"eventId": "topic:0:3", "ordering": {"lsn": 3}}},
        source_dataset_version_id="cdc:topic:0:3",
        source_hash="hash-v3",
        object_version=3,
        deleted=True,
        deletion_reason="source_deleted",
        updated_at="2026-06-10T00:02:00Z",
    )


def _conflict_record(conflict_id: str = "conflict_1") -> ObjectConflictRecord:
    return ObjectConflictRecord(
        conflict_id=conflict_id,
        tenant_id="tenant-demo",
        object_type_id="ot_order",
        object_id="O-1",
        property_api_name="status",
        source_value="REVIEW",
        edit_value="APPROVED",
        source_dataset_version_id="dsv_orders_2",
        edit_id=None,
        status="open",
        created_at="2026-06-10T00:01:00Z",
    )


def _link_insert_record(
    *,
    link_id: str = "olink_1",
    from_object_id: str = "O-1",
    to_object_id: str = "C-1",
    deleted: bool = False,
    link_version: int = 1,
    index_version: str = "active",
    is_active: bool = True,
) -> ObjectLinkInsert:
    return ObjectLinkInsert(
        link_id=link_id,
        tenant_id="tenant-demo",
        link_type_id="lt_order_customer",
        link_type_api_name="OrderCustomer",
        from_object_type_id="ot_order",
        from_api_name="Order",
        from_object_id=from_object_id,
        to_object_type_id="ot_customer",
        to_api_name="Customer",
        to_object_id=to_object_id,
        properties={},
        source_dataset_version_id="dsv_orders_1",
        link_version=link_version,
        deleted=deleted,
        deletion_reason="old source" if deleted else None,
        updated_at="2026-06-10T00:00:00Z",
        index_version=index_version,
        is_active=is_active,
    )


def _index_run_row(record: IndexRunRecord) -> dict[str, Any]:
    return {
        "id": record.run_id,
        "tenant_id": record.tenant_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "trigger_type": record.trigger_type,
        "source_ref": record.source_ref,
        "status": record.status,
        "cursor": record.cursor,
        "rows_read": record.rows_read,
        "objects_upserted": record.objects_upserted,
        "objects_deleted": record.objects_deleted,
        "links_upserted": record.links_upserted,
        "error": record.error,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "created_at": record.created_at,
    }


def _object_record_row(record: ObjectRecordInsert) -> dict[str, Any]:
    return {
        "id": record.record_id,
        "tenant_id": record.tenant_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "index_version": record.index_version,
        "is_active": record.is_active,
        "properties": record.properties,
        "base_properties": record.base_properties,
        "edit_properties": record.edit_properties,
        "property_versions": record.property_versions,
        "source_dataset_version_id": record.source_dataset_version_id,
        "source_hash": record.source_hash,
        "object_version": record.object_version,
        "deleted": record.deleted,
        "deletion_reason": record.deletion_reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _conflict_row(record: ObjectConflictRecord) -> dict[str, Any]:
    return {
        "id": record.conflict_id,
        "tenant_id": record.tenant_id,
        "object_type_id": record.object_type_id,
        "object_id": record.object_id,
        "property_api_name": record.property_api_name,
        "source_value": record.source_value,
        "edit_value": record.edit_value,
        "source_dataset_version_id": record.source_dataset_version_id,
        "edit_id": record.edit_id,
        "status": record.status,
        "created_at": record.created_at,
    }


def _link_type_row(
    *,
    link_type_id: str = "lt_order_customer",
    tenant_id: str = "tenant-demo",
    ontology_version_id: str = "ont_1",
    from_object_type_id: str = "ot_order",
) -> dict[str, Any]:
    return {
        "id": link_type_id,
        "tenant_id": tenant_id,
        "ontology_version_id": ontology_version_id,
        "api_name": "OrderCustomer",
        "display_name": "Order Customer",
        "from_object_type_id": from_object_type_id,
        "from_api_name": "Order",
        "to_object_type_id": "ot_customer",
        "to_api_name": "Customer",
        "cardinality": "many_to_one",
        "backing": {"fromKey": "order_id", "toKey": "customer_id"},
    }


def _link_row(record: ObjectLinkInsert) -> dict[str, Any]:
    return {
        "id": record.link_id,
        "tenant_id": record.tenant_id,
        "link_type_id": record.link_type_id,
        "link_type_api_name": record.link_type_api_name,
        "index_version": record.index_version,
        "is_active": record.is_active,
        "from_object_type_id": record.from_object_type_id,
        "from_api_name": record.from_api_name,
        "from_object_id": record.from_object_id,
        "to_object_type_id": record.to_object_type_id,
        "to_api_name": record.to_api_name,
        "to_object_id": record.to_object_id,
        "properties": record.properties,
        "source_dataset_version_id": record.source_dataset_version_id,
        "link_version": record.link_version,
        "deleted": record.deleted,
        "deletion_reason": record.deletion_reason,
        "updated_at": record.updated_at,
    }


def _matches_inactive_index(row: dict[str, Any], tenant_id: str, object_type_id: str, index_version: str) -> bool:
    return (
        row["tenant_id"] == tenant_id
        and row["object_type_id"] == object_type_id
        and row["index_version"] == index_version
        and row["is_active"] is False
    )


def _matches_inactive_link_index(row: dict[str, Any], tenant_id: str, object_type_id: str, index_version: str) -> bool:
    return (
        row["tenant_id"] == tenant_id
        and row["from_object_type_id"] == object_type_id
        and row["index_version"] == index_version
        and row["is_active"] is False
    )


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectIndexHarness:
    if request.param == "fake":
        return FakeObjectIndexHarness(FakeObjectIndexRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyObjectIndexHarness(SqlAlchemyObjectIndexRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyObjectIndexHarness(
        SqlAlchemyObjectIndexRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_object_index_repository_contract_records_index_run_lifecycle(
    harness: ObjectIndexHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.create_index_run(transaction=transaction, record=_index_run_record("index_run_1"))
        harness.repository.mark_index_run_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_id="index_run_1",
            rows_read=3,
            objects_upserted=2,
            objects_deleted=1,
            links_upserted=1,
            cursor={"last_row": 3},
            completed_at="2026-06-10T00:02:00Z",
        )
        harness.repository.create_index_run(transaction=transaction, record=_index_run_record("index_run_failed"))
        harness.repository.mark_index_run_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_id="index_run_failed",
            error={"type": "ValidationFailed"},
            completed_at="2026-06-10T00:03:00Z",
        )
        found_for_replay = harness.repository.index_run_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_id="index_run_failed",
        )
        hidden_tenant = harness.repository.index_run_by_id(
            transaction=transaction,
            tenant_id="tenant-other",
            run_id="index_run_failed",
        )

    succeeded = harness.index_run("index_run_1")
    failed = harness.index_run("index_run_failed")

    assert found_for_replay is not None
    assert found_for_replay["source_ref"] == {"dataset_version_id": "dsv_orders_1"}
    assert hidden_tenant is None
    assert succeeded is not None
    assert succeeded["status"] == "succeeded"
    assert succeeded["cursor"] == {"last_row": 3}
    assert succeeded["objects_upserted"] == 2
    assert succeeded["objects_deleted"] == 1
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error"] == {"type": "ValidationFailed"}


def test_object_index_repository_contract_writes_object_record_updates_and_conflicts(
    harness: ObjectIndexHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_object_record(transaction=transaction, record=_object_insert_record())
        harness.repository.update_object_record_from_source(transaction=transaction, record=_object_update_record())
        harness.repository.update_object_record_from_cdc(transaction=transaction, record=_object_cdc_update_record())
        harness.repository.insert_object_conflict(transaction=transaction, record=_conflict_record())

    record = harness.object_record("obj_order_1")
    conflicts = harness.conflicts()

    assert record is not None
    assert record["object_version"] == 3
    assert record["properties"] == {"status": "CANCELLED", "amount": 120}
    assert record["source_dataset_version_id"] == "cdc:topic:0:3"
    assert record["property_versions"]["_cdc"]["eventId"] == "topic:0:3"
    assert record["deleted"] is True
    assert record["deletion_reason"] == "source_deleted"
    assert len(conflicts) == 1
    assert conflicts[0]["property_api_name"] == "status"
    assert conflicts[0]["source_value"] == "REVIEW"
    assert conflicts[0]["edit_value"] == "APPROVED"


def test_object_index_repository_contract_switches_and_cleans_shadow_index(
    harness: ObjectIndexHarness,
) -> None:
    active = _object_insert_record("obj_active")
    shadow = _object_insert_record("obj_shadow", index_version="index_run_shadow", is_active=False)
    active_link = _link_insert_record(link_id="olink_active")
    shadow_link = _link_insert_record(link_id="olink_shadow", index_version="index_run_shadow", is_active=False)

    with harness.transaction() as transaction:
        harness.repository.insert_object_record(transaction=transaction, record=active)
        harness.repository.insert_object_record(transaction=transaction, record=shadow)
        harness.repository.insert_object_link(transaction=transaction, record=active_link)
        harness.repository.insert_object_link(transaction=transaction, record=shadow_link)
        before = harness.repository.active_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )
        shadow_rows = harness.repository.object_records_for_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            index_version="index_run_shadow",
        )
        harness.repository.switch_active_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            index_version="index_run_shadow",
            updated_at="2026-06-10T00:04:00Z",
        )
        after = harness.repository.active_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )
        harness.repository.delete_inactive_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            index_version="active",
        )

    assert before == "active"
    assert [row["id"] for row in shadow_rows] == ["obj_shadow"]
    assert after == "index_run_shadow"
    assert harness.object_record("obj_active") is None
    assert harness.object_record("obj_shadow") is not None


def test_object_index_repository_contract_persists_empty_shadow_active_pointer(
    harness: ObjectIndexHarness,
) -> None:
    with harness.transaction() as transaction:
        before = harness.repository.active_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )
        harness.repository.switch_active_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            index_version="index_run_empty",
            updated_at="2026-06-10T00:04:00Z",
        )
        after = harness.repository.active_index_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )

    assert before == "active"
    assert after == "index_run_empty"
    assert harness.object_record("obj_active") is None


def test_object_index_repository_contract_reads_link_types_and_upserts_links(
    harness: ObjectIndexHarness,
) -> None:
    harness.add_link_type()
    stale_link = _link_insert_record(deleted=True, link_version=4)
    harness.add_link(stale_link)

    with harness.transaction() as transaction:
        link_types = harness.repository.link_types_for_object_type(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
            from_object_type_id="ot_order",
        )
        existing = harness.repository.object_link(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_id="lt_order_customer",
            from_object_id="O-1",
            to_object_id="C-1",
            index_version="active",
        )
        assert existing is not None
        harness.repository.refresh_object_link(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_id=existing["id"],
            link_version=existing["link_version"] + 1,
            source_dataset_version_id="dsv_orders_2",
            updated_at="2026-06-10T00:02:00Z",
        )
        harness.repository.insert_object_link(
            transaction=transaction,
            record=_link_insert_record(link_id="olink_2", to_object_id="C-2", link_version=1),
        )
        refreshed = harness.repository.object_link(
            transaction=transaction,
            tenant_id="tenant-demo",
            link_type_id="lt_order_customer",
            from_object_id="O-1",
            to_object_id="C-1",
            index_version="active",
        )

    assert [row["api_name"] for row in link_types] == ["OrderCustomer"]
    assert refreshed is not None
    assert refreshed["link_version"] == 5
    assert refreshed["deleted"] is False
