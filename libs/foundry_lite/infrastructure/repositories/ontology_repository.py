from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, func, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRecord,
    ActionTypeRow,
    LinkTypeRecord,
    LinkTypeRow,
    ObjectTypeRecord,
    ObjectTypeRow,
    OntologyVersionRecord,
    OntologyVersionRow,
    PropertyClassificationRow,
    PropertyTypeRecord,
    PropertyTypeRow,
)
from foundry_lite.application.ports.transaction_context import ONTOLOGY_VERSION_ACTIVE, ONTOLOGY_VERSION_ARCHIVED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


class SqlAlchemyOntologyRepository:
    """SQLAlchemy implementation of ontology metadata persistence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def next_ontology_version_number(self, *, transaction: Any, tenant_id: str) -> int:
        current = (
            transaction.execute(
                select(func.max(db.ontology_versions.c.version_number)).where(
                    db.ontology_versions.c.tenant_id == tenant_id
                )
            ).scalar()
            or 0
        )
        return int(current) + 1

    def insert_ontology_version(self, *, transaction: Any, record: OntologyVersionRecord) -> None:
        transaction.execute(
            insert(db.ontology_versions).values(
                id=record.ontology_version_id,
                tenant_id=record.tenant_id,
                version_number=record.version_number,
                status=record.status,
                created_by=record.created_by,
                created_at=record.created_at,
                activated_at=record.activated_at,
            )
        )

    def archive_active_ontology_versions(self, *, transaction: Any, tenant_id: str) -> int:
        return cas_status_update_many(
            transaction,
            db.ontology_versions,
            tenant_id=tenant_id,
            transition=ONTOLOGY_VERSION_ARCHIVED,
            values={},
            conditions=(),
        )

    def activate_ontology_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        activated_at: str,
    ) -> bool:
        return cas_status_update(
            transaction,
            db.ontology_versions,
            tenant_id=tenant_id,
            row_id=ontology_version_id,
            transition=ONTOLOGY_VERSION_ACTIVE,
            values={"activated_at": activated_at},
        )

    def insert_object_type(self, *, transaction: Any, record: ObjectTypeRecord) -> None:
        transaction.execute(
            insert(db.object_types).values(
                id=record.object_type_id,
                tenant_id=record.tenant_id,
                ontology_version_id=record.ontology_version_id,
                api_name=record.api_name,
                display_name=record.display_name,
                description=record.description,
                primary_key_property=record.primary_key_property,
                backing=record.backing,
                config=record.config,
            )
        )

    def insert_property_type(self, *, transaction: Any, record: PropertyTypeRecord) -> None:
        transaction.execute(
            insert(db.property_types).values(
                id=record.property_type_id,
                tenant_id=record.tenant_id,
                object_type_id=record.object_type_id,
                api_name=record.api_name,
                display_name=record.display_name,
                data_type=record.data_type,
                nullable=record.nullable,
                indexed=record.indexed,
                searchable=record.searchable,
                editable=record.editable,
                classification=record.classification,
                source=record.source,
                column_name=record.column_name,
                edit_policy=record.edit_policy,
                derivation=record.derivation,
            )
        )

    def insert_link_type(self, *, transaction: Any, record: LinkTypeRecord) -> None:
        transaction.execute(
            insert(db.link_types).values(
                id=record.link_type_id,
                tenant_id=record.tenant_id,
                ontology_version_id=record.ontology_version_id,
                api_name=record.api_name,
                display_name=record.display_name,
                from_object_type_id=record.from_object_type_id,
                from_api_name=record.from_api_name,
                to_object_type_id=record.to_object_type_id,
                to_api_name=record.to_api_name,
                cardinality=record.cardinality,
                backing=record.backing,
            )
        )

    def insert_action_type(self, *, transaction: Any, record: ActionTypeRecord) -> None:
        transaction.execute(
            insert(db.action_types).values(
                id=record.action_type_id,
                tenant_id=record.tenant_id,
                ontology_version_id=record.ontology_version_id,
                api_name=record.api_name,
                display_name=record.display_name,
                target_object_type_id=record.target_object_type_id,
                target_api_name=record.target_api_name,
                parameter_schema=record.parameter_schema,
                definition=record.definition,
                enabled=record.enabled,
            )
        )

    def object_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[ObjectTypeRow]:
        rows = (
            transaction.execute(
                select(db.object_types)
                .where(
                    and_(
                        db.object_types.c.tenant_id == tenant_id,
                        db.object_types.c.ontology_version_id == ontology_version_id,
                    )
                )
                .order_by(db.object_types.c.api_name)
            )
            .mappings()
            .all()
        )
        return [cast(ObjectTypeRow, dict(row)) for row in rows]

    def link_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[LinkTypeRow]:
        rows = (
            transaction.execute(
                select(db.link_types)
                .where(
                    and_(
                        db.link_types.c.tenant_id == tenant_id,
                        db.link_types.c.ontology_version_id == ontology_version_id,
                    )
                )
                .order_by(db.link_types.c.api_name)
            )
            .mappings()
            .all()
        )
        return [cast(LinkTypeRow, dict(row)) for row in rows]

    def properties_for_object_type(self, *, transaction: Any, object_type_id: str) -> list[PropertyTypeRow]:
        rows = (
            transaction.execute(
                select(db.property_types)
                .where(db.property_types.c.object_type_id == object_type_id)
                .order_by(db.property_types.c.api_name)
            )
            .mappings()
            .all()
        )
        return [cast(PropertyTypeRow, dict(row)) for row in rows]

    def active_property_classifications(self, *, transaction: Any, tenant_id: str) -> list[PropertyClassificationRow]:
        rows = (
            transaction.execute(
                select(
                    db.object_types.c.api_name.label("object_type_api_name"),
                    db.property_types.c.api_name.label("property_api_name"),
                    db.property_types.c.column_name,
                    db.property_types.c.classification,
                )
                .select_from(
                    db.property_types.join(
                        db.object_types, db.object_types.c.id == db.property_types.c.object_type_id
                    ).join(db.ontology_versions, db.ontology_versions.c.id == db.object_types.c.ontology_version_id)
                )
                .where(
                    and_(
                        db.property_types.c.tenant_id == tenant_id,
                        db.ontology_versions.c.status == "active",
                        db.property_types.c.classification.is_not(None),
                    )
                )
            )
            .mappings()
            .all()
        )
        return [cast(PropertyClassificationRow, dict(row)) for row in rows]

    def actions_for_target(self, *, transaction: Any, object_type_id: str) -> list[ActionTypeRow]:
        rows = (
            transaction.execute(
                select(db.action_types)
                .where(db.action_types.c.target_object_type_id == object_type_id)
                .order_by(db.action_types.c.api_name)
            )
            .mappings()
            .all()
        )
        return [cast(ActionTypeRow, dict(row)) for row in rows]

    def active_ontology_version(self, *, transaction: Any, tenant_id: str) -> OntologyVersionRow | None:
        row = (
            transaction.execute(
                select(db.ontology_versions).where(
                    and_(
                        db.ontology_versions.c.tenant_id == tenant_id,
                        db.ontology_versions.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OntologyVersionRow, dict(row)) if row else None

    def object_type_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> ObjectTypeRow | None:
        row = (
            transaction.execute(
                select(db.object_types).where(
                    and_(
                        db.object_types.c.tenant_id == tenant_id,
                        db.object_types.c.ontology_version_id == ontology_version_id,
                        db.object_types.c.api_name == api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ObjectTypeRow, dict(row)) if row else None

    def enabled_action_type_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> ActionTypeRow | None:
        row = (
            transaction.execute(
                select(db.action_types).where(
                    and_(
                        db.action_types.c.tenant_id == tenant_id,
                        db.action_types.c.ontology_version_id == ontology_version_id,
                        db.action_types.c.api_name == api_name,
                        db.action_types.c.enabled == True,  # noqa: E712
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ActionTypeRow, dict(row)) if row else None

    def action_type_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_id: str,
    ) -> ActionTypeRow | None:
        row = (
            transaction.execute(
                select(db.action_types).where(
                    and_(db.action_types.c.tenant_id == tenant_id, db.action_types.c.id == action_type_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(ActionTypeRow, dict(row)) if row else None
