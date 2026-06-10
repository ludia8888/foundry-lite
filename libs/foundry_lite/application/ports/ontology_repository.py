from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class OntologyVersionRecord:
    ontology_version_id: str
    tenant_id: str
    version_number: int
    status: str
    created_by: str
    created_at: str
    activated_at: str | None


@dataclass(frozen=True)
class ObjectTypeRecord:
    object_type_id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    description: str | None
    primary_key_property: str
    backing: dict[str, Any]
    config: dict[str, Any]


@dataclass(frozen=True)
class PropertyTypeRecord:
    property_type_id: str
    tenant_id: str
    object_type_id: str
    api_name: str
    display_name: str
    data_type: str
    nullable: bool
    indexed: bool
    searchable: bool
    editable: bool
    classification: str | None
    source: str
    column_name: str | None
    edit_policy: str
    derivation: dict[str, Any] | None


@dataclass(frozen=True)
class LinkTypeRecord:
    link_type_id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    from_object_type_id: str
    from_api_name: str
    to_object_type_id: str
    to_api_name: str
    cardinality: str
    backing: dict[str, Any]


@dataclass(frozen=True)
class ActionTypeRecord:
    action_type_id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    target_object_type_id: str
    target_api_name: str
    parameter_schema: dict[str, Any]
    definition: dict[str, Any]
    enabled: bool


class OntologyRepository(Protocol):
    """DB boundary for ontology version and type metadata persistence."""

    def next_ontology_version_number(self, *, transaction: Any, tenant_id: str) -> int:
        """Return the next ontology version number for a tenant."""
        ...

    def insert_ontology_version(self, *, transaction: Any, record: OntologyVersionRecord) -> None:
        """Persist one ontology version row."""
        ...

    def archive_active_ontology_versions(self, *, transaction: Any, tenant_id: str) -> None:
        """Archive currently active ontology versions for a tenant."""
        ...

    def activate_ontology_version(
        self,
        *,
        transaction: Any,
        ontology_version_id: str,
        activated_at: str,
    ) -> None:
        """Mark an ontology version active."""
        ...

    def insert_object_type(self, *, transaction: Any, record: ObjectTypeRecord) -> None:
        """Persist one object type."""
        ...

    def insert_property_type(self, *, transaction: Any, record: PropertyTypeRecord) -> None:
        """Persist one property type."""
        ...

    def insert_link_type(self, *, transaction: Any, record: LinkTypeRecord) -> None:
        """Persist one link type."""
        ...

    def insert_action_type(self, *, transaction: Any, record: ActionTypeRecord) -> None:
        """Persist one action type."""
        ...

    def object_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[dict[str, Any]]:
        """Return object types for an ontology version."""
        ...

    def link_types_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[dict[str, Any]]:
        """Return link types for an ontology version."""
        ...

    def properties_for_object_type(self, *, transaction: Any, object_type_id: str) -> list[dict[str, Any]]:
        """Return property types for one object type."""
        ...

    def actions_for_target(self, *, transaction: Any, object_type_id: str) -> list[dict[str, Any]]:
        """Return action types for one target object type."""
        ...

    def active_ontology_version(self, *, transaction: Any, tenant_id: str) -> dict[str, Any] | None:
        """Return the active ontology version for a tenant."""
        ...

    def object_type_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> dict[str, Any] | None:
        """Return one object type in a version by API name."""
        ...

    def enabled_action_type_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> dict[str, Any] | None:
        """Return one enabled action type in a version by API name."""
        ...
