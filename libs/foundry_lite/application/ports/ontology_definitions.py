"""Typed ontology definition payloads shared across the ontology repository port.

Types-only sibling of ``ontology_repository`` (same pattern as
``runtime_repository_types``): the backing/parameter/interface declaration
shapes parsed from ontology YAML and persisted in definition JSON columns.
The persistence Protocol and its row/record types stay in
``ontology_repository``, which re-exports everything here so callers keep a
single import surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict

OntologyJsonObject = Mapping[str, object]


class ObjectTypeBacking(TypedDict):
    """Dataset backing declaration for an ontology object type."""

    dataset: str
    mode: NotRequired[str]
    primaryKeyColumns: NotRequired[Sequence[str]]
    cdc: NotRequired[ObjectTypeCdcBacking]


class ObjectTypeCdcBacking(TypedDict):
    """Optional CDC changelog source for incremental object indexing."""

    dataset: str
    primaryKeyColumns: NotRequired[Sequence[str]]
    deletePolicy: NotRequired[str]


class LinkTypeBacking(TypedDict):
    """Dataset backing declaration for an ontology link type."""

    dataset: str
    fromKey: str
    toKey: str


class PropertyDerivation(TypedDict, total=False):
    """Optional derivation metadata for a property type."""

    expression: str


class InterfacePropertyDefinition(TypedDict):
    """One shared property contract declared by an ontology interface.

    Implementing object types must declare a property with the identical
    ``apiName`` and ``type``; ``indexed: true`` and ``nullable: false`` are
    minimum-capability requirements the implementer may strengthen but never
    weaken.
    """

    apiName: str
    type: str
    nullable: bool
    indexed: bool


class InterfaceTypeDefinition(TypedDict):
    """Interface definition payload persisted with an interface type."""

    apiName: str
    displayName: str
    properties: Sequence[InterfacePropertyDefinition]


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


class OntologyCatalogProperty(TypedDict):
    """Frontend-safe property metadata for the active ontology catalog."""

    apiName: str
    displayName: str
    dataType: str
    nullable: bool
    indexed: bool
    searchable: bool
    editable: bool
    classification: str | None
    source: str
    columnName: str | None
    editPolicy: str
    derivation: PropertyDerivation | None


class OntologyCatalogAction(TypedDict):
    """Frontend-safe action metadata for the active ontology catalog."""

    apiName: str
    displayName: str
    target: str
    parameterSchema: ActionParameterSchema
    definition: ActionTypeDefinition
    enabled: bool


class OntologyCatalogObject(TypedDict):
    """Frontend-safe object type metadata for the active ontology catalog."""

    apiName: str
    displayName: str
    description: str | None
    primaryKeyProperty: str
    titleProperty: str | None
    materialization: OntologyJsonObject | None
    rowPolicies: Sequence[OntologyJsonObject]
    implements: Sequence[str]
    backing: ObjectTypeBacking
    properties: Sequence[OntologyCatalogProperty]
    actions: Sequence[OntologyCatalogAction]
    config: OntologyJsonObject


class OntologyCatalogInterface(TypedDict):
    """Frontend-safe interface type metadata for the active ontology catalog."""

    apiName: str
    displayName: str
    properties: Sequence[InterfacePropertyDefinition]
    implementedBy: Sequence[str]


class OntologyCatalogLink(TypedDict):
    """Frontend-safe link type metadata for the active ontology catalog."""

    apiName: str
    displayName: str
    fromObjectType: str
    toObjectType: str
    cardinality: str
    backing: LinkTypeBacking


class OntologyCatalogResult(TypedDict):
    """Public read-only catalog for building object/action UI dynamically."""

    ontologyVersionId: str
    versionNumber: int
    status: str
    createdAt: str
    activatedAt: str | None
    interfaces: Sequence[OntologyCatalogInterface]
    objectTypes: Sequence[OntologyCatalogObject]
    linkTypes: Sequence[OntologyCatalogLink]
