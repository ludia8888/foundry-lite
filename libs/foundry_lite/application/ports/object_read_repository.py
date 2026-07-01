"""Application port contract for object read repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.runtime_repository import RuntimeJsonObject, RuntimeRunLink
from foundry_lite.application.ports.transaction_context import TransactionContext

ObjectSortDirection = Literal["asc", "desc"]


class ObjectOrderBy(TypedDict):
    property: str
    direction: ObjectSortDirection


class ObjectQueryCursor(TypedDict):
    values: list[object]
    object_id: str
    active_index_version: NotRequired[str]


class ObjectRecordRow(TypedDict):
    id: str
    tenant_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    index_version: str
    is_active: bool
    properties: dict[str, object]
    base_properties: dict[str, object]
    edit_properties: dict[str, object]
    property_versions: dict[str, object]
    source_dataset_version_id: str | None
    source_hash: str | None
    object_version: int
    object_change_sequence: NotRequired[int | None]
    deleted: bool
    deletion_reason: str | None
    created_at: str
    updated_at: str


class ObjectLinkRow(TypedDict):
    id: str
    tenant_id: str
    link_type_id: str
    link_type_api_name: str
    index_version: str
    is_active: bool
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


class ObjectPropertyLineageItem(TypedDict):
    """Property-level source evidence shown in object explain payloads."""

    propertyName: str
    valueSource: str
    sourceDatasetVersionId: str | None
    sourceObjectVersion: int
    sourceColumn: str | None
    propertyVersion: object
    sourceHash: str | None
    maskingStatus: str
    derivationExpression: NotRequired[str]


class ObjectExplain(TypedDict):
    baseProperties: dict[str, object]
    editProperties: dict[str, object]
    lineage: list[dict[str, object]]
    propertyLineage: list[ObjectPropertyLineageItem]
    sourceRunChain: list[RuntimeRunLink]
    lateDataBadge: NotRequired[RuntimeJsonObject | None]


class ObjectPayload(TypedDict):
    objectType: str
    objectId: str
    objectVersion: int
    properties: dict[str, object]
    sourceDatasetVersionId: str | None
    deleted: NotRequired[bool]
    deletionReason: NotRequired[str | None]
    explain: NotRequired[ObjectExplain]


class ObjectQueryItem(TypedDict):
    objectType: str
    objectId: str
    objectVersion: int
    properties: dict[str, object]
    searchProjectionVersion: NotRequired[int]
    searchProjectionStale: NotRequired[bool]


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
    targetMissing: NotRequired[bool]


class ObjectLinkWarning(TypedDict):
    type: str
    message: str


ObjectLinkPayload = TypedDict(
    "ObjectLinkPayload",
    {
        "linkType": str,
        "from": ObjectReference,
        "to": ObjectLinkTarget,
        "warning": NotRequired[ObjectLinkWarning],
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
        object_type_id: str | None = None,
    ) -> ObjectRecordRow | None:
        """Return one object record for a tenant and object reference."""
        ...

    def active_object_rows(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        object_type_id: str | None = None,
    ) -> list[ObjectRecordRow]:
        """Return non-deleted object records for one object type, ordered by object id."""
        ...

    def query_active_object_rows(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
        cursor: ObjectQueryCursor | None,
        limit: int,
        object_type_id: str | None = None,
        property_object_type_id: str | None = None,
    ) -> list[ObjectRecordRow]:
        """Return one DB-filtered, DB-sorted object page plus one lookahead row."""
        ...

    def latest_object_change_sequence(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        include_deleted: bool = True,
    ) -> int:
        """Return the current object change watermark for one object type."""
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

    def active_links_to(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        link_type_api_name: str,
        to_api_name: str,
        to_object_id: str,
    ) -> list[ObjectLinkRow]:
        """Return non-deleted incoming links for one object and link type."""
        ...
