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
    """Dataset backing declaration for an ontology object type.

    Persisted object types declare either ``dataset`` (single-datasource,
    normalized internally to ``primary``) or ``datasources`` (column-wise
    multi-datasource), never both. Read-only virtual catalog projections may
    instead use a named ``mode`` such as ``action_log`` plus ``actionType``.
    """

    dataset: NotRequired[str]
    mode: NotRequired[str]
    actionType: NotRequired[str]
    primaryKeyColumns: NotRequired[Sequence[str]]
    cdc: NotRequired[ObjectTypeCdcBacking]
    datasources: NotRequired[Sequence[ObjectTypeDatasourceBacking]]


class ObjectTypeDatasourceBacking(TypedDict):
    """One declared datasource segment of a multi-datasource object type."""

    name: str
    dataset: str
    primaryKeyColumns: NotRequired[Sequence[str]]
    requiredRole: NotRequired[str]


class ObjectTypeCdcBacking(TypedDict):
    """Optional CDC changelog source for incremental object indexing."""

    dataset: str
    primaryKeyColumns: NotRequired[Sequence[str]]
    deletePolicy: NotRequired[str]


class PropertyClassificationRow(TypedDict):
    """One classified property of the active ontology (security source of truth)."""

    object_type_api_name: str
    property_api_name: str
    column_name: str | None
    classification: str | None


class PropertyDatasourceRow(TypedDict):
    """One active property joined with its object type's backing/config JSON.

    Raw material for deriving per-property datasource segment roles (the pure
    derivation lives in ``foundry_lite.domain.ontology.datasources``); the
    repository only joins rows, it never interprets the backing declaration.
    """

    object_type_api_name: str
    property_api_name: str
    column_name: str | None
    source: str
    backing: ObjectTypeBacking
    config: OntologyJsonObject


class LinkTypeBacking(TypedDict):
    """Dataset backing declaration for an ontology link type."""

    dataset: NotRequired[str]
    fromKey: NotRequired[str]
    toKey: NotRequired[str]
    mode: NotRequired[str]
    actionType: NotRequired[str]


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


class InterfaceLinkConstraintDefinition(TypedDict):
    """One interface relationship implemented by concrete ontology link types."""

    apiName: str
    displayName: str
    targetKind: str
    target: str
    cardinality: str
    required: bool


class InterfaceTypeDefinition(TypedDict):
    """Interface definition payload persisted with an interface type."""

    apiName: str
    displayName: str
    properties: Sequence[InterfacePropertyDefinition]
    linkConstraints: Sequence[InterfaceLinkConstraintDefinition]


class FunctionInputDefinition(TypedDict):
    """One typed input declared by an ontology function type."""

    apiName: str
    type: str
    required: bool


class FunctionOutputDefinition(TypedDict):
    """Declared output type of an ontology function type."""

    type: str


class FunctionTypeDefinition(TypedDict):
    """Function definition payload persisted with a function type.

    ``definition`` carries the bounded Logic DAG (``blocks``) plus the
    governed tool manifest (``tools``) so execution never needs a
    per-request manifest; ``permissions.allowedRoles`` is enforced at
    execute time (missing permissions fail closed to admin-only).
    """

    apiName: str
    displayName: str
    version: str
    runtime: str
    inputs: Sequence[FunctionInputDefinition]
    output: FunctionOutputDefinition
    definition: OntologyJsonObject
    permissions: NotRequired[OntologyJsonObject]


ActionParameterSchema = TypedDict(
    "ActionParameterSchema",
    {
        "$schema": str,
        "type": str,
        "additionalProperties": bool,
        "properties": OntologyJsonObject,
        "required": Sequence[str],
        "x-foundry-action": str,
        "x-foundry-contract-fingerprint": str,
        "x-foundry-form-layout": OntologyJsonObject,
        "x-foundry-inline-eligibility": OntologyJsonObject,
    },
    total=False,
)
"""Canonical Action JSON Schema delivered without stripping Foundry extensions."""


class ActionParameterDefinition(TypedDict):
    """Single action parameter declaration from ontology YAML."""

    apiName: str
    type: str
    required: NotRequired[bool]
    description: NotRequired[str]
    default: NotRequired[OntologyJsonObject]
    constraints: NotRequired[OntologyJsonObject]
    overrides: NotRequired[Sequence[OntologyJsonObject]]
    objectType: NotRequired[str]
    interfaceType: NotRequired[str]
    itemType: NotRequired[str]
    fields: NotRequired[Sequence[OntologyJsonObject]]
    mediaSet: NotRequired[str]
    allowedMimeTypes: NotRequired[Sequence[str]]
    maxBytes: NotRequired[int]
    render: NotRequired[str]


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
    contractVersion: int
    displayName: str
    description: str
    target: str
    targetKind: str
    parameters: Sequence[ActionParameterDefinition]
    permissions: OntologyJsonObject
    preconditions: Sequence[OntologyJsonObject]
    submissionCriteria: OntologyJsonObject
    riskLevel: str
    agentExecutionPolicy: str
    agentToolDescription: str
    actionLog: OntologyJsonObject
    revert: OntologyJsonObject
    branchPolicy: OntologyJsonObject
    formLayout: OntologyJsonObject
    mutations: Sequence[ActionMutationDefinition]
    # Native Action IR v2 rules (createObject/modifyObject(s)/createOrModifyObject/
    # deleteObject(s)/createLink/deleteLink/functionEdit). Presence of this key selects
    # the multi-object v2 commit path; its absence keeps the legacy setProperty path.
    rulesV2: Sequence[OntologyJsonObject]
    rules: Sequence[OntologyJsonObject]
    function: OntologyJsonObject
    effects: Sequence[OntologyJsonObject]
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
    datasource: str | None


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
    linkConstraints: Sequence[InterfaceLinkConstraintDefinition]
    implementedBy: Sequence[str]


class OntologyCatalogFunction(TypedDict):
    """Frontend-safe function type metadata for the active ontology catalog."""

    apiName: str
    displayName: str
    version: str
    runtime: str
    inputs: Sequence[FunctionInputDefinition]
    output: FunctionOutputDefinition
    allowedRoles: Sequence[str] | None


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
    functionTypes: Sequence[OntologyCatalogFunction]
    objectTypes: Sequence[OntologyCatalogObject]
    linkTypes: Sequence[OntologyCatalogLink]
