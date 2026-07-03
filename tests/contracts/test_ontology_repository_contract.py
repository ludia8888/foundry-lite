from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRecord,
    ActionTypeRow,
    InterfaceTypeRecord,
    InterfaceTypeRow,
    LinkTypeRecord,
    LinkTypeRow,
    ObjectTypeRecord,
    ObjectTypeRow,
    OntologyRepository,
    OntologyVersionRecord,
    OntologyVersionRow,
    PropertyTypeRecord,
    PropertyTypeRow,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyOntologyRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


class OntologyHarness(Protocol):
    repository: OntologyRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def ontology_version_rows(self) -> list[dict[str, Any]]: ...

    def object_type_rows(self) -> list[dict[str, Any]]: ...

    def property_type_rows(self) -> list[dict[str, Any]]: ...

    def link_type_rows(self) -> list[dict[str, Any]]: ...

    def action_type_rows(self) -> list[dict[str, Any]]: ...

    def interface_type_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeOntologyRepository:
    ontology_versions: list[dict[str, Any]] = field(default_factory=list)
    object_types: list[dict[str, Any]] = field(default_factory=list)
    property_types: list[dict[str, Any]] = field(default_factory=list)
    link_types: list[dict[str, Any]] = field(default_factory=list)
    action_types: list[dict[str, Any]] = field(default_factory=list)
    interface_types: list[dict[str, Any]] = field(default_factory=list)

    def next_ontology_version_number(self, *, transaction: Any, tenant_id: str) -> int:
        del transaction
        current = max(
            (row["version_number"] for row in self.ontology_versions if row["tenant_id"] == tenant_id),
            default=0,
        )
        return int(current) + 1

    def insert_ontology_version(self, *, transaction: Any, record: OntologyVersionRecord) -> None:
        del transaction
        if record.status == "active" and any(
            row["tenant_id"] == record.tenant_id and row["status"] == "active" for row in self.ontology_versions
        ):
            raise ValueError("tenant already has an active ontology version")
        self.ontology_versions.append(_ontology_version_row(record))

    def archive_active_ontology_versions(self, *, transaction: Any, tenant_id: str) -> int:
        del transaction
        archived = 0
        for row in self.ontology_versions:
            if row["tenant_id"] == tenant_id and row["status"] == "active":
                row["status"] = "archived"
                archived += 1
        return archived

    def activate_ontology_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        activated_at: str,
    ) -> bool:
        del transaction
        for row in self.ontology_versions:
            if row["tenant_id"] == tenant_id and row["id"] == ontology_version_id and row["status"] == "draft":
                row.update(status="active", activated_at=activated_at)
                return True
        return False

    def insert_object_type(self, *, transaction: Any, record: ObjectTypeRecord) -> None:
        del transaction
        self.object_types.append(_object_type_row(record))

    def insert_property_type(self, *, transaction: Any, record: PropertyTypeRecord) -> None:
        del transaction
        self.property_types.append(_property_type_row(record))

    def insert_link_type(self, *, transaction: Any, record: LinkTypeRecord) -> None:
        del transaction
        self.link_types.append(_link_type_row(record))

    def insert_action_type(self, *, transaction: Any, record: ActionTypeRecord) -> None:
        del transaction
        self.action_types.append(_action_type_row(record))

    def insert_interface_type(self, *, transaction: Any, record: InterfaceTypeRecord) -> None:
        del transaction
        if any(
            row["tenant_id"] == record.tenant_id
            and row["ontology_version_id"] == record.ontology_version_id
            and row["api_name"] == record.api_name
            for row in self.interface_types
        ):
            raise ValueError("duplicate interface apiName within an ontology version")
        self.interface_types.append(_interface_type_row(record))

    def interface_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[InterfaceTypeRow]:
        del transaction
        rows = [
            cast(InterfaceTypeRow, dict(row))
            for row in self.interface_types
            if row["tenant_id"] == tenant_id and row["ontology_version_id"] == ontology_version_id
        ]
        return sorted(rows, key=lambda row: row["api_name"])

    def object_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[ObjectTypeRow]:
        del transaction
        rows = [
            cast(ObjectTypeRow, dict(row))
            for row in self.object_types
            if row["tenant_id"] == tenant_id and row["ontology_version_id"] == ontology_version_id
        ]
        return sorted(rows, key=lambda row: row["api_name"])

    def link_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[LinkTypeRow]:
        del transaction
        rows = [
            cast(LinkTypeRow, dict(row))
            for row in self.link_types
            if row["tenant_id"] == tenant_id and row["ontology_version_id"] == ontology_version_id
        ]
        return sorted(rows, key=lambda row: row["api_name"])

    def properties_for_object_type(self, *, transaction: Any, object_type_id: str) -> list[PropertyTypeRow]:
        del transaction
        rows = [
            cast(PropertyTypeRow, dict(row)) for row in self.property_types if row["object_type_id"] == object_type_id
        ]
        return sorted(rows, key=lambda row: row["api_name"])

    def actions_for_target(self, *, transaction: Any, object_type_id: str) -> list[ActionTypeRow]:
        del transaction
        rows = [
            cast(ActionTypeRow, dict(row))
            for row in self.action_types
            if row["target_object_type_id"] == object_type_id
        ]
        return sorted(rows, key=lambda row: row["api_name"])

    def active_ontology_version(self, *, transaction: Any, tenant_id: str) -> OntologyVersionRow | None:
        del transaction
        for row in self.ontology_versions:
            if row["tenant_id"] == tenant_id and row["status"] == "active":
                return cast(OntologyVersionRow, dict(row))
        return None

    def ontology_version_by_number(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        version_number: int,
    ) -> OntologyVersionRow | None:
        del transaction
        for row in self.ontology_versions:
            if row["tenant_id"] == tenant_id and row["version_number"] == version_number:
                return cast(OntologyVersionRow, dict(row))
        return None

    def object_type_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> ObjectTypeRow | None:
        del transaction
        for row in self.object_types:
            if (
                row["tenant_id"] == tenant_id
                and row["ontology_version_id"] == ontology_version_id
                and row["api_name"] == api_name
            ):
                return cast(ObjectTypeRow, dict(row))
        return None

    def object_type_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
    ) -> ObjectTypeRow | None:
        del transaction
        for row in self.object_types:
            if row["tenant_id"] == tenant_id and row["id"] == object_type_id:
                return cast(ObjectTypeRow, dict(row))
        return None

    def update_object_type_config(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        config: Mapping[str, object],
    ) -> bool:
        del transaction
        for row in self.object_types:
            if row["tenant_id"] == tenant_id and row["id"] == object_type_id:
                row["config"] = dict(config)
                return True
        return False

    def enabled_action_type_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> ActionTypeRow | None:
        del transaction
        for row in self.action_types:
            if (
                row["tenant_id"] == tenant_id
                and row["ontology_version_id"] == ontology_version_id
                and row["api_name"] == api_name
                and row["enabled"] is True
            ):
                return cast(ActionTypeRow, dict(row))
        return None

    def action_type_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_id: str,
    ) -> ActionTypeRow | None:
        del transaction
        for row in self.action_types:
            if row["tenant_id"] == tenant_id and row["id"] == action_type_id:
                return cast(ActionTypeRow, dict(row))
        return None


@dataclass
class FakeOntologyHarness:
    repository: OntologyRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def ontology_version_rows(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeOntologyRepository)
        return [dict(row) for row in self.repository.ontology_versions]

    def object_type_rows(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeOntologyRepository)
        return [dict(row) for row in self.repository.object_types]

    def property_type_rows(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeOntologyRepository)
        return [dict(row) for row in self.repository.property_types]

    def link_type_rows(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeOntologyRepository)
        return [dict(row) for row in self.repository.link_types]

    def action_type_rows(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeOntologyRepository)
        return [dict(row) for row in self.repository.action_types]

    def interface_type_rows(self) -> list[dict[str, Any]]:
        assert isinstance(self.repository, FakeOntologyRepository)
        return [dict(row) for row in self.repository.interface_types]


@dataclass
class SqlAlchemyOntologyHarness:
    repository: OntologyRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def ontology_version_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.ontology_versions)

    def object_type_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_types)

    def property_type_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.property_types)

    def link_type_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.link_types)

    def action_type_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.action_types)

    def interface_type_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.interface_types)

    def _rows(self, table: Any) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(table)).mappings().all()
        return [dict(row) for row in rows]


def _ontology_version_record(
    version_id: str = "ont_1",
    *,
    tenant_id: str = "tenant-demo",
    version_number: int = 1,
    status: str = "draft",
    activated_at: str | None = None,
) -> OntologyVersionRecord:
    return OntologyVersionRecord(
        ontology_version_id=version_id,
        tenant_id=tenant_id,
        version_number=version_number,
        status=status,
        created_by="actor-demo",
        created_at="2026-06-10T00:00:00Z",
        activated_at=activated_at,
    )


def _ontology_version_row(record: OntologyVersionRecord) -> dict[str, Any]:
    return {
        "id": record.ontology_version_id,
        "tenant_id": record.tenant_id,
        "version_number": record.version_number,
        "status": record.status,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "activated_at": record.activated_at,
    }


def _object_type_record(
    object_type_id: str = "ot_order",
    *,
    tenant_id: str = "tenant-demo",
    ontology_version_id: str = "ont_1",
    api_name: str = "Order",
    config: dict[str, object] | None = None,
) -> ObjectTypeRecord:
    return ObjectTypeRecord(
        object_type_id=object_type_id,
        tenant_id=tenant_id,
        ontology_version_id=ontology_version_id,
        api_name=api_name,
        display_name=api_name,
        description=None,
        primary_key_property="order_id",
        backing={"dataset": "clean.orders"},
        config=config or {},
    )


def _object_type_row(record: ObjectTypeRecord) -> dict[str, Any]:
    return {
        "id": record.object_type_id,
        "tenant_id": record.tenant_id,
        "ontology_version_id": record.ontology_version_id,
        "api_name": record.api_name,
        "display_name": record.display_name,
        "description": record.description,
        "primary_key_property": record.primary_key_property,
        "backing": record.backing,
        "config": record.config,
    }


def _property_type_record(
    property_type_id: str = "pt_order_id",
    *,
    tenant_id: str = "tenant-demo",
    object_type_id: str = "ot_order",
    api_name: str = "order_id",
    editable: bool = False,
) -> PropertyTypeRecord:
    return PropertyTypeRecord(
        property_type_id=property_type_id,
        tenant_id=tenant_id,
        object_type_id=object_type_id,
        api_name=api_name,
        display_name=api_name,
        data_type="string",
        nullable=False,
        indexed=True,
        searchable=True,
        editable=editable,
        classification=None,
        source="dataset" if not editable else "edit_layer",
        column_name=api_name if not editable else None,
        edit_policy="source_wins" if not editable else "edit_only",
        derivation=None,
    )


def _property_type_row(record: PropertyTypeRecord) -> dict[str, Any]:
    return {
        "id": record.property_type_id,
        "tenant_id": record.tenant_id,
        "object_type_id": record.object_type_id,
        "api_name": record.api_name,
        "display_name": record.display_name,
        "data_type": record.data_type,
        "nullable": record.nullable,
        "indexed": record.indexed,
        "searchable": record.searchable,
        "editable": record.editable,
        "classification": record.classification,
        "source": record.source,
        "column_name": record.column_name,
        "edit_policy": record.edit_policy,
        "derivation": record.derivation,
    }


def _link_type_record(link_type_id: str = "lt_order_customer") -> LinkTypeRecord:
    return LinkTypeRecord(
        link_type_id=link_type_id,
        tenant_id="tenant-demo",
        ontology_version_id="ont_1",
        api_name="OrderCustomer",
        display_name="OrderCustomer",
        from_object_type_id="ot_order",
        from_api_name="Order",
        to_object_type_id="ot_customer",
        to_api_name="Customer",
        cardinality="many_to_one",
        backing={"dataset": "clean.order_customer", "fromKey": "order_id", "toKey": "customer_id"},
    )


def _link_type_row(record: LinkTypeRecord) -> dict[str, Any]:
    return {
        "id": record.link_type_id,
        "tenant_id": record.tenant_id,
        "ontology_version_id": record.ontology_version_id,
        "api_name": record.api_name,
        "display_name": record.display_name,
        "from_object_type_id": record.from_object_type_id,
        "from_api_name": record.from_api_name,
        "to_object_type_id": record.to_object_type_id,
        "to_api_name": record.to_api_name,
        "cardinality": record.cardinality,
        "backing": record.backing,
    }


def _action_type_record(
    action_type_id: str = "at_approve",
    *,
    api_name: str = "ApproveOrder",
    enabled: bool = True,
) -> ActionTypeRecord:
    return ActionTypeRecord(
        action_type_id=action_type_id,
        tenant_id="tenant-demo",
        ontology_version_id="ont_1",
        api_name=api_name,
        display_name=api_name,
        target_object_type_id="ot_order",
        target_api_name="Order",
        parameter_schema={"type": "object", "required": [], "properties": {}},
        definition={"apiName": api_name, "target": "Order", "mutations": []},
        enabled=enabled,
    )


def _action_type_row(record: ActionTypeRecord) -> dict[str, Any]:
    return {
        "id": record.action_type_id,
        "tenant_id": record.tenant_id,
        "ontology_version_id": record.ontology_version_id,
        "api_name": record.api_name,
        "display_name": record.display_name,
        "target_object_type_id": record.target_object_type_id,
        "target_api_name": record.target_api_name,
        "parameter_schema": record.parameter_schema,
        "definition": record.definition,
        "enabled": record.enabled,
    }


def _interface_type_record(
    interface_type_id: str = "it_asset",
    *,
    tenant_id: str = "tenant-demo",
    ontology_version_id: str = "ont_1",
    api_name: str = "Asset",
) -> InterfaceTypeRecord:
    return InterfaceTypeRecord(
        interface_type_id=interface_type_id,
        tenant_id=tenant_id,
        ontology_version_id=ontology_version_id,
        api_name=api_name,
        display_name=api_name,
        definition={
            "apiName": api_name,
            "displayName": api_name,
            "properties": [{"apiName": "riskScore", "type": "float", "nullable": True, "indexed": True}],
        },
    )


def _interface_type_row(record: InterfaceTypeRecord) -> dict[str, Any]:
    return {
        "id": record.interface_type_id,
        "tenant_id": record.tenant_id,
        "ontology_version_id": record.ontology_version_id,
        "api_name": record.api_name,
        "display_name": record.display_name,
        "definition": record.definition,
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> OntologyHarness:
    if request.param == "fake":
        return FakeOntologyHarness(FakeOntologyRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyOntologyHarness(SqlAlchemyOntologyRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyOntologyHarness(
        SqlAlchemyOntologyRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_ontology_repository_contract_manages_version_lifecycle(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        assert harness.repository.next_ontology_version_number(transaction=transaction, tenant_id="tenant-demo") == 1
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record("ont_old", version_number=1, status="active"),
        )
        assert harness.repository.next_ontology_version_number(transaction=transaction, tenant_id="tenant-demo") == 2
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record("ont_new", version_number=2),
        )
        archived = harness.repository.archive_active_ontology_versions(transaction=transaction, tenant_id="tenant-demo")
        activated = harness.repository.activate_ontology_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_new",
            activated_at="2026-06-10T00:00:01Z",
        )
        active = harness.repository.active_ontology_version(transaction=transaction, tenant_id="tenant-demo")

    assert archived == 1
    assert activated is True
    rows = {row["id"]: row for row in harness.ontology_version_rows()}
    assert active is not None
    assert active["id"] == "ont_new"
    assert rows["ont_old"]["status"] == "archived"
    assert rows["ont_new"]["status"] == "active"
    assert rows["ont_new"]["activated_at"] == "2026-06-10T00:00:01Z"


def test_ontology_repository_contract_activation_requires_draft_state(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record("ont_archived", version_number=1, status="archived"),
        )
        activated = harness.repository.activate_ontology_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_archived",
            activated_at="2026-06-10T00:00:01Z",
        )

    rows = {row["id"]: row for row in harness.ontology_version_rows()}
    assert activated is False
    assert rows["ont_archived"]["status"] == "archived"
    assert rows["ont_archived"]["activated_at"] is None


def test_ontology_repository_contract_finds_versions_by_tenant_scoped_number(
    harness: OntologyHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record("ont_archived", version_number=1, status="archived"),
        )
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record(
                "ont_other_tenant",
                tenant_id="tenant-other",
                version_number=2,
                status="active",
            ),
        )
        found = harness.repository.ontology_version_by_number(
            transaction=transaction,
            tenant_id="tenant-demo",
            version_number=1,
        )
        missing = harness.repository.ontology_version_by_number(
            transaction=transaction,
            tenant_id="tenant-demo",
            version_number=2,
        )

    assert found is not None
    assert found["id"] == "ont_archived"
    assert found["status"] == "archived"
    assert missing is None


def test_ontology_repository_contract_rejects_two_active_versions_for_one_tenant(
    harness: OntologyHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record("ont_active_1", version_number=1, status="active"),
        )
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record(
                "ont_other_tenant_active",
                tenant_id="tenant-other",
                version_number=1,
                status="active",
            ),
        )
    if isinstance(harness, FakeOntologyHarness):
        with harness.transaction() as transaction:
            with pytest.raises(ValueError, match="already has an active"):
                harness.repository.insert_ontology_version(
                    transaction=transaction,
                    record=_ontology_version_record("ont_active_2", version_number=2, status="active"),
                )
    else:
        with pytest.raises(IntegrityError):
            with harness.transaction() as transaction:
                harness.repository.insert_ontology_version(
                    transaction=transaction,
                    record=_ontology_version_record("ont_active_2", version_number=2, status="active"),
                )


def test_ontology_repository_contract_persists_and_reads_type_metadata(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_object_type(transaction=transaction, record=_object_type_record())
        harness.repository.insert_object_type(
            transaction=transaction,
            record=_object_type_record("ot_customer", api_name="Customer"),
        )
        harness.repository.insert_property_type(transaction=transaction, record=_property_type_record())
        harness.repository.insert_property_type(
            transaction=transaction,
            record=_property_type_record("pt_status", api_name="status", editable=True),
        )
        harness.repository.insert_link_type(transaction=transaction, record=_link_type_record())
        harness.repository.insert_action_type(transaction=transaction, record=_action_type_record())

        object_types = harness.repository.object_types_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
        )
        properties = harness.repository.properties_for_object_type(transaction=transaction, object_type_id="ot_order")
        link_types = harness.repository.link_types_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
        )
        actions = harness.repository.actions_for_target(transaction=transaction, object_type_id="ot_order")

    assert [row["api_name"] for row in object_types] == ["Customer", "Order"]
    assert [row["api_name"] for row in properties] == ["order_id", "status"]
    assert [row["api_name"] for row in link_types] == ["OrderCustomer"]
    assert [row["api_name"] for row in actions] == ["ApproveOrder"]


def test_ontology_repository_contract_updates_object_type_config(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_object_type(
            transaction=transaction,
            record=_object_type_record(config={"servingContractStatus": "object_reindex_required"}),
        )
        updated = harness.repository.update_object_type_config(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            config={"servingContractStatus": "object_reindex_complete", "indexRunId": "index_run_1"},
        )
        other_tenant_updated = harness.repository.update_object_type_config(
            transaction=transaction,
            tenant_id="tenant-other",
            object_type_id="ot_order",
            config={"servingContractStatus": "wrong-tenant"},
        )
        row = harness.repository.object_type_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )

    assert updated is True
    assert other_tenant_updated is False
    assert row is not None
    assert row["config"]["servingContractStatus"] == "object_reindex_complete"
    assert row["config"]["indexRunId"] == "index_run_1"


def test_ontology_repository_contract_scopes_active_lookups(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record("ont_1", version_number=1, status="active"),
        )
        harness.repository.insert_ontology_version(
            transaction=transaction,
            record=_ontology_version_record(
                "ont_other_tenant",
                tenant_id="tenant-other",
                version_number=1,
                status="active",
            ),
        )
        harness.repository.insert_object_type(transaction=transaction, record=_object_type_record())
        harness.repository.insert_object_type(
            transaction=transaction,
            record=_object_type_record("ot_other_tenant", tenant_id="tenant-other"),
        )
        harness.repository.insert_action_type(transaction=transaction, record=_action_type_record())
        harness.repository.insert_action_type(
            transaction=transaction,
            record=_action_type_record("at_disabled", api_name="DisabledOrderAction", enabled=False),
        )

        active = harness.repository.active_ontology_version(transaction=transaction, tenant_id="tenant-demo")
        object_type = harness.repository.object_type_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
            api_name="Order",
        )
        other_tenant_object_type = harness.repository.object_type_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
            api_name="OtherTenantOnly",
        )
        action_type = harness.repository.enabled_action_type_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
            api_name="ApproveOrder",
        )
        disabled_action = harness.repository.enabled_action_type_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
            api_name="DisabledOrderAction",
        )
        action_by_id = harness.repository.action_type_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_id="at_approve",
        )
        disabled_action_by_id = harness.repository.action_type_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_id="at_disabled",
        )
        missing_action_by_id = harness.repository.action_type_by_id(
            transaction=transaction,
            tenant_id="tenant-other",
            action_type_id="at_approve",
        )

    assert active is not None
    assert active["id"] == "ont_1"
    assert object_type is not None
    assert object_type["id"] == "ot_order"
    assert other_tenant_object_type is None
    assert action_type is not None
    assert action_type["id"] == "at_approve"
    assert disabled_action is None
    assert action_by_id is not None
    assert action_by_id["id"] == "at_approve"
    assert disabled_action_by_id is not None
    assert disabled_action_by_id["id"] == "at_disabled"
    assert missing_action_by_id is None


def test_ontology_repository_contract_persists_and_scopes_interface_types(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_interface_type(transaction=transaction, record=_interface_type_record())
        harness.repository.insert_interface_type(
            transaction=transaction,
            record=_interface_type_record("it_resource", api_name="Resource"),
        )
        harness.repository.insert_interface_type(
            transaction=transaction,
            record=_interface_type_record("it_other_tenant", tenant_id="tenant-other"),
        )
        harness.repository.insert_interface_type(
            transaction=transaction,
            record=_interface_type_record("it_other_version", ontology_version_id="ont_2"),
        )

        interfaces = harness.repository.interface_types_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_1",
        )
        other_version = harness.repository.interface_types_for_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            ontology_version_id="ont_2",
        )

    assert [row["api_name"] for row in interfaces] == ["Asset", "Resource"]
    assert interfaces[0]["definition"]["properties"] == [
        {"apiName": "riskScore", "type": "float", "nullable": True, "indexed": True}
    ]
    assert [row["api_name"] for row in other_version] == ["Asset"]
    assert len(harness.interface_type_rows()) == 4


def test_ontology_repository_contract_rejects_duplicate_interface_api_names(harness: OntologyHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_interface_type(transaction=transaction, record=_interface_type_record())
    if isinstance(harness, FakeOntologyHarness):
        with harness.transaction() as transaction:
            with pytest.raises(ValueError, match="duplicate interface"):
                harness.repository.insert_interface_type(
                    transaction=transaction,
                    record=_interface_type_record("it_asset_2"),
                )
    else:
        with pytest.raises(IntegrityError):
            with harness.transaction() as transaction:
                harness.repository.insert_interface_type(
                    transaction=transaction,
                    record=_interface_type_record("it_asset_2"),
                )
