from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.runtime_repository import RuntimeRunLink
from foundry_lite.application.ports.transaction_context import TransactionContext


class ObjectRecordRow(TypedDict):
    id: str
    tenant_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    properties: dict[str, object]
    base_properties: dict[str, object]
    edit_properties: dict[str, object]
    property_versions: dict[str, object]
    source_dataset_version_id: str | None
    source_hash: str | None
    object_version: int
    deleted: bool
    deletion_reason: str | None
    created_at: str
    updated_at: str


class ObjectLinkRow(TypedDict):
    id: str
    tenant_id: str
    link_type_id: str
    link_type_api_name: str
    from_object_type_id: str
    from_api_name: str
    from_object_id: str
    to_object_type_id: str
    to_api_name: str
    to_object_id: str
    properties: dict[str, object]
    source_dataset_version_id: str | None
    link_version: int
    deleted: bool
    deletion_reason: str | None
    updated_at: str


class ObjectExplain(TypedDict):
    baseProperties: dict[str, object]
    editProperties: dict[str, object]
    lineage: list[dict[str, object]]
    sourceRunChain: list[RuntimeRunLink]


class ObjectPayload(TypedDict):
    objectType: str
    objectId: str
    objectVersion: int
    properties: dict[str, object]
    sourceDatasetVersionId: str | None
    explain: NotRequired[ObjectExplain]


class ObjectQueryItem(TypedDict):
    objectType: str
    objectId: str
    objectVersion: int
    properties: dict[str, object]


class ObjectQueryResult(TypedDict):
    items: list[ObjectQueryItem]
    nextCursor: str | None


class ObjectReference(TypedDict):
    objectType: str
    objectId: str


class ObjectLinkTarget(TypedDict):
    objectType: str
    objectId: str
    properties: dict[str, object]


ObjectLinkPayload = TypedDict(
    "ObjectLinkPayload",
    {
        "linkType": str,
        "from": ObjectReference,
        "to": ObjectLinkTarget,
    },
)


class ObjectReadRepository(Protocol):
    """DB read boundary for object records and object links."""

    def object_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
    ) -> ObjectRecordRow | None:
        """Return one object record for a tenant and object reference."""
        ...

    def active_object_rows(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
    ) -> list[ObjectRecordRow]:
        """Return non-deleted object records for one object type, ordered by object id."""
        ...

    def active_links_from(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
    ) -> list[ObjectLinkRow]:
        """Return non-deleted outgoing links for one object and link type."""
        ...
