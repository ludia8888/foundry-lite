"""Application port contract for ontology repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.ontology_definitions import (
    ActionMutationDefinition as ActionMutationDefinition,
)
from foundry_lite.application.ports.ontology_definitions import (
    ActionParameterDefinition as ActionParameterDefinition,
)
from foundry_lite.application.ports.ontology_definitions import (
    ActionParameterSchema as ActionParameterSchema,
)
from foundry_lite.application.ports.ontology_definitions import (
    ActionTypeDefinition as ActionTypeDefinition,
)
from foundry_lite.application.ports.ontology_definitions import (
    InterfacePropertyDefinition as InterfacePropertyDefinition,
)
from foundry_lite.application.ports.ontology_definitions import (
    InterfaceTypeDefinition as InterfaceTypeDefinition,
)
from foundry_lite.application.ports.ontology_definitions import (
    LinkTypeBacking as LinkTypeBacking,
)
from foundry_lite.application.ports.ontology_definitions import (
    ObjectTypeBacking as ObjectTypeBacking,
)
from foundry_lite.application.ports.ontology_definitions import (
    ObjectTypeCdcBacking as ObjectTypeCdcBacking,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyCatalogAction as OntologyCatalogAction,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyCatalogInterface as OntologyCatalogInterface,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyCatalogLink as OntologyCatalogLink,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyCatalogObject as OntologyCatalogObject,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyCatalogProperty as OntologyCatalogProperty,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyCatalogResult as OntologyCatalogResult,
)
from foundry_lite.application.ports.ontology_definitions import (
    OntologyJsonObject as OntologyJsonObject,
)
from foundry_lite.application.ports.ontology_definitions import (
    PropertyDerivation as PropertyDerivation,
)
from foundry_lite.application.ports.ontology_definitions import (
    RequiredActionMutationFields as RequiredActionMutationFields,
)
from foundry_lite.application.ports.transaction_context import TransactionContext


class OntologyApplyResult(TypedDict):
    """Public result returned after activating an ontology version."""

    ontology_version_id: str
    version_number: int
    migration_plan: OntologyJsonObject


class OntologyRollbackResult(TypedDict):
    """Public result returned after restoring an archived ontology version as a new version."""

    ontology_version_id: str
    version_number: int
    rolled_back_from_version_number: int | None
    rolled_back_to_version_number: int
    migration_plan: OntologyJsonObject


class OntologyValidationResult(TypedDict):
    """Public result returned after validating ontology YAML without activating it."""

    status: str
    object_type_count: int
    link_type_count: int
    action_type_count: int
    migration_plan: OntologyJsonObject


class OntologyVersionRow(TypedDict):
    """Persisted ontology version row."""

    id: str
    tenant_id: str
    version_number: int
    status: str
    created_by: str
    created_at: str
    activated_at: str | None


class ObjectTypeRow(TypedDict):
    """Persisted ontology object type row."""

    id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    description: str | None
    primary_key_property: str
    backing: ObjectTypeBacking
    config: OntologyJsonObject


class PropertyTypeRow(TypedDict):
    """Persisted ontology property type row."""

    id: str
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
    derivation: PropertyDerivation | None


class PropertyClassificationRow(TypedDict):
    """One classified property of the active ontology (security source of truth)."""

    object_type_api_name: str
    property_api_name: str
    column_name: str | None
    classification: str | None


class LinkTypeRow(TypedDict):
    """Persisted ontology link type row."""

    id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    from_object_type_id: str
    from_api_name: str
    to_object_type_id: str
    to_api_name: str
    cardinality: str
    backing: LinkTypeBacking


class ActionTypeRow(TypedDict):
    """Persisted ontology action type row."""

    id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    target_object_type_id: str
    target_api_name: str
    parameter_schema: ActionParameterSchema
    definition: ActionTypeDefinition
    enabled: bool


class InterfaceTypeRow(TypedDict):
    """Persisted ontology interface type row."""

    id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    definition: InterfaceTypeDefinition


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
    backing: ObjectTypeBacking
    config: OntologyJsonObject


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
    derivation: PropertyDerivation | None


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
    backing: LinkTypeBacking


@dataclass(frozen=True)
class ActionTypeRecord:
    action_type_id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    target_object_type_id: str
    target_api_name: str
    parameter_schema: ActionParameterSchema
    definition: ActionTypeDefinition
    enabled: bool


@dataclass(frozen=True)
class InterfaceTypeRecord:
    interface_type_id: str
    tenant_id: str
    ontology_version_id: str
    api_name: str
    display_name: str
    definition: InterfaceTypeDefinition


class OntologyRepository(Protocol):
    """DB boundary for ontology version and type metadata persistence."""

    def next_ontology_version_number(self, *, transaction: TransactionContext, tenant_id: str) -> int:
        """Return the next ontology version number for a tenant."""
        ...

    def insert_ontology_version(self, *, transaction: TransactionContext, record: OntologyVersionRecord) -> None:
        """Persist one ontology version row."""
        ...

    def archive_active_ontology_versions(self, *, transaction: TransactionContext, tenant_id: str) -> int:
        """Archive currently active ontology versions for a tenant and return the changed row count."""
        ...

    def activate_ontology_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
        activated_at: str,
    ) -> bool:
        """CAS a draft ontology version into active."""
        ...

    def insert_object_type(self, *, transaction: TransactionContext, record: ObjectTypeRecord) -> None:
        """Persist one object type."""
        ...

    def insert_property_type(self, *, transaction: TransactionContext, record: PropertyTypeRecord) -> None:
        """Persist one property type."""
        ...

    def insert_link_type(self, *, transaction: TransactionContext, record: LinkTypeRecord) -> None:
        """Persist one link type."""
        ...

    def insert_action_type(self, *, transaction: TransactionContext, record: ActionTypeRecord) -> None:
        """Persist one action type."""
        ...

    def insert_interface_type(self, *, transaction: TransactionContext, record: InterfaceTypeRecord) -> None:
        """Persist one interface type."""
        ...

    def interface_types_for_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[InterfaceTypeRow]:
        """Return interface types for an ontology version."""
        ...

    def object_types_for_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[ObjectTypeRow]:
        """Return object types for an ontology version."""
        ...

    def link_types_for_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
    ) -> list[LinkTypeRow]:
        """Return link types for an ontology version."""
        ...

    def properties_for_object_type(
        self, *, transaction: TransactionContext, object_type_id: str
    ) -> list[PropertyTypeRow]:
        """Return property types for one object type."""
        ...

    def actions_for_target(self, *, transaction: TransactionContext, object_type_id: str) -> list[ActionTypeRow]:
        """Return action types for one target object type."""
        ...

    def active_property_classifications(
        self, *, transaction: TransactionContext, tenant_id: str
    ) -> list[PropertyClassificationRow]:
        """Return (object_type, column, classification) for the tenant's active ontology.

        The single source of truth for which properties/columns are sensitive: the
        security policy derives masking from these classifications instead of
        hardcoding object/property/column names.
        """
        ...

    def active_ontology_version(self, *, transaction: TransactionContext, tenant_id: str) -> OntologyVersionRow | None:
        """Return the active ontology version for a tenant."""
        ...

    def ontology_version_by_number(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        version_number: int,
    ) -> OntologyVersionRow | None:
        """Return one ontology version by its tenant-scoped version number.

        Rollback addresses history by the stable version number operators see in
        the catalog, not by internal ids, so restoring an archived version needs
        this lookup.
        """
        ...

    def object_type_for_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> ObjectTypeRow | None:
        """Return one object type in a version by API name."""
        ...

    def object_type_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
    ) -> ObjectTypeRow | None:
        """Return one object type by immutable id."""
        ...

    def update_object_type_config(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        config: OntologyJsonObject,
    ) -> bool:
        """Replace one object type config within a tenant boundary."""
        ...

    def enabled_action_type_for_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
        api_name: str,
    ) -> ActionTypeRow | None:
        """Return one enabled action type in a version by API name."""
        ...

    def action_type_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        action_type_id: str,
    ) -> ActionTypeRow | None:
        """Return one action type by immutable persisted id."""
        ...
