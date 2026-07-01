"""Application port contract for object set repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.object_read_repository import ObjectQueryItem, ObjectRecordRow
from foundry_lite.application.ports.transaction_context import TransactionContext

ObjectSetDefinition = dict[str, object]


class ObjectSetRow(TypedDict):
    id: str
    tenant_id: str
    name: str
    object_type_id: str
    set_type: str
    definition: ObjectSetDefinition
    visibility: str
    owner_user_id: str
    expires_at: str | None
    created_at: str


class ObjectSetObjectTypeRow(TypedDict):
    id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    description: str | None
    primary_key_property: str
    backing: Mapping[str, object]
    config: Mapping[str, object]


class ObjectSetPayload(TypedDict):
    id: str
    name: str
    objectType: str
    setType: str
    definition: ObjectSetDefinition
    visibility: str
    accessScope: str
    lifecycle: str
    ownerUserId: str
    expiresAt: str | None
    createdAt: str
    objectIds: list[str]
    items: NotRequired[list[ObjectQueryItem]]


class ObjectSetQueryResult(TypedDict):
    items: list[ObjectSetPayload]


@dataclass(frozen=True)
class ObjectSetRecord:
    set_id: str
    tenant_id: str
    name: str
    object_type_id: str
    set_type: str
    definition: ObjectSetDefinition
    visibility: str
    owner_user_id: str
    expires_at: str | None
    created_at: str


class ObjectSetRepository(Protocol):
    """DB boundary for object set rows and supporting object membership reads."""

    def create_object_set(self, *, transaction: TransactionContext, record: ObjectSetRecord) -> None:
        """Persist a new object set."""
        ...

    def object_set_by_id(self, *, transaction: TransactionContext, tenant_id: str, set_id: str) -> ObjectSetRow | None:
        """Return one object set row for a tenant."""
        ...

    def object_sets(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str | None = None,
    ) -> list[ObjectSetRow]:
        """Return object set rows for a tenant, optionally narrowed to one object type."""
        ...

    def delete_object_sets(self, *, transaction: TransactionContext, tenant_id: str, set_ids: list[str]) -> None:
        """Delete object sets by id."""
        ...

    def active_object_ids(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> set[str]:
        """Return active object ids that exist for a requested id list."""
        ...

    def active_object_records_by_ids(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> list[ObjectRecordRow]:
        """Return active object records for a requested id list."""
        ...

    def property_names_for_object_type(self, *, transaction: TransactionContext, object_type_id: str) -> set[str]:
        """Return property API names for one object type."""
        ...

    def object_type_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
    ) -> ObjectSetObjectTypeRow | None:
        """Return one object type row for a tenant."""
        ...
