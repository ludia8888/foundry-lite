from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

OntologyJsonObject = Mapping[str, object]


class OntologyApplyResult(TypedDict):
    """Public result returned after activating an ontology version."""

    ontology_version_id: str
    version_number: int


class ObjectTypeBacking(TypedDict):
    """Dataset backing declaration for an ontology object type."""

    dataset: str
    mode: NotRequired[str]
    primaryKeyColumns: NotRequired[Sequence[str]]


class LinkTypeBacking(TypedDict):
    """Dataset backing declaration for an ontology link type."""

    dataset: str
    fromKey: str
    toKey: str


class PropertyDerivation(TypedDict, total=False):
    """Optional derivation metadata for a property type."""

    expression: str


class ActionParameterSchema(TypedDict, total=False):
    """JSON-schema subset used by action parameter validation."""

    type: str
    required: Sequence[str]
    properties: OntologyJsonObject


class ActionParameterDefinition(TypedDict):
    """Single action parameter declaration from ontology YAML."""

    apiName: str
    type: str
    required: NotRequired[bool]


class RequiredActionMutationFields(TypedDict):
    """Fields every action mutation must declare."""

    type: str
    property: str


class ActionMutationDefinition(RequiredActionMutationFields, total=False):
    """Single action mutation declared in ontology YAML."""

    value: object
    valueFrom: str


class ActionTypeDefinition(TypedDict, total=False):
    """Action definition payload persisted with an action type."""

    apiName: str
    displayName: str
    target: str
    parameters: Sequence[ActionParameterDefinition]
    permissions: OntologyJsonObject
    preconditions: Sequence[OntologyJsonObject]
    mutations: Sequence[ActionMutationDefinition]
    writebacks: Sequence[OntologyJsonObject]
    sideEffects: Sequence[OntologyJsonObject]


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


class OntologyRepository(Protocol):
    """DB boundary for ontology version and type metadata persistence."""

    def next_ontology_version_number(self, *, transaction: TransactionContext, tenant_id: str) -> int:
        """Return the next ontology version number for a tenant."""
        ...

    def insert_ontology_version(self, *, transaction: TransactionContext, record: OntologyVersionRecord) -> None:
        """Persist one ontology version row."""
        ...

    def archive_active_ontology_versions(self, *, transaction: TransactionContext, tenant_id: str) -> None:
        """Archive currently active ontology versions for a tenant."""
        ...

    def activate_ontology_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
        activated_at: str,
    ) -> None:
        """Mark an ontology version active."""
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

    def active_ontology_version(self, *, transaction: TransactionContext, tenant_id: str) -> OntologyVersionRow | None:
        """Return the active ontology version for a tenant."""
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
