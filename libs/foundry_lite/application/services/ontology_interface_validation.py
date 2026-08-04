"""Interface (shared property contract) parsing, conformance validation, and import.

Ontology YAML may declare top-level ``interfaces`` whose properties are shared
contracts, and object types may declare ``implements: [InterfaceName]``. An
implementing object type MUST declare every interface property with an
identical ``apiName`` and ``type``; capability flags are minimums the
implementer may strengthen but never weaken (``indexed: true`` on the
interface requires an indexed implementer property, ``nullable: false``
requires a non-nullable one). Violations are collected and reported together
so an operator fixes one apply, not one error per apply.

The ``implements`` list rides in the object type's config JSON (like
``titleProperty``/``rowPolicies``) so no object-type schema migration is
needed and snapshot/rollback replay preserves it; interface definitions
persist in the ``interface_types`` table mirroring ``action_types``.
"""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    InterfacePropertyDefinition,
    InterfaceTypeDefinition,
    InterfaceTypeRecord,
    OntologyJsonObject,
    OntologyRepository,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.ontology_action_import import import_action_types as import_action_types
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    mapping_sequence,
    optional_bool,
    optional_str,
    required_str,
    string_sequence,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

#: Config JSON key holding the persisted ``implements`` declarations.
IMPLEMENTS_CONFIG_KEY = "implements"

#: Interface properties use the object property type vocabulary. Kept in sync
#: with OBJECT_PROPERTY_DATA_TYPES in ontology_validation (importing it here
#: would create a module cycle once validation delegates to this module).
INTERFACE_PROPERTY_DATA_TYPES = frozenset({"boolean", "float", "integer", "media_reference", "string"})


def object_type_implements(item: YamlObject) -> tuple[str, ...]:
    """Return the interface apiNames one YAML object type declares."""
    if "implements" not in item or item["implements"] is None:
        return ()
    return string_sequence(item["implements"], "implements")


def persisted_implements(config: OntologyJsonObject) -> tuple[str, ...]:
    """Return the ``implements`` list persisted in an object type config.

    Malformed persisted values raise instead of being skipped so snapshot,
    catalog, and interface query never silently disagree about membership.
    """
    if IMPLEMENTS_CONFIG_KEY not in config or config[IMPLEMENTS_CONFIG_KEY] is None:
        return ()
    return string_sequence(config[IMPLEMENTS_CONFIG_KEY], IMPLEMENTS_CONFIG_KEY)


def interface_definitions_by_api(definition: YamlObject) -> dict[str, InterfaceTypeDefinition]:
    """Parse top-level ``interfaces`` into normalized definitions, rejecting duplicates."""
    interfaces: dict[str, InterfaceTypeDefinition] = {}
    for item in mapping_sequence(definition, "interfaces"):
        api_name = required_str(item, "apiName")
        if api_name in interfaces:
            raise ValidationFailed("duplicate interface apiName", details={"interface": api_name})
        interfaces[api_name] = _interface_type_definition(item, api_name)
    return interfaces


def _interface_type_definition(item: YamlObject, api_name: str) -> InterfaceTypeDefinition:
    properties: list[InterfacePropertyDefinition] = []
    seen_properties: set[str] = set()
    for prop in mapping_sequence(item, "properties"):
        contract = _interface_property_contract(prop, api_name)
        if contract["apiName"] in seen_properties:
            raise ValidationFailed(
                "duplicate interface property apiName",
                details={"interface": api_name, "property": contract["apiName"]},
            )
        seen_properties.add(contract["apiName"])
        properties.append(contract)
    return {
        "apiName": api_name,
        "displayName": optional_str(item, "displayName", api_name) or api_name,
        "properties": properties,
    }


def _interface_property_contract(prop: YamlObject, interface_api_name: str) -> InterfacePropertyDefinition:
    prop_api = required_str(prop, "apiName")
    prop_type = required_str(prop, "type")
    if prop_type not in INTERFACE_PROPERTY_DATA_TYPES:
        raise ValidationFailed(
            "unsupported interface property type",
            details={
                "interface": interface_api_name,
                "property": prop_api,
                "value": prop_type,
                "allowed": sorted(INTERFACE_PROPERTY_DATA_TYPES),
            },
        )
    return {
        "apiName": prop_api,
        "type": prop_type,
        "nullable": optional_bool(prop, "nullable", True),
        "indexed": optional_bool(prop, "indexed", False),
    }


def validate_ontology_interfaces(definition: YamlObject) -> None:
    """Validate interface declarations and every object type's conformance."""
    interfaces = interface_definitions_by_api(definition)
    object_api_names = {required_str(item, "apiName"): item for item in mapping_sequence(definition, "objectTypes")}
    _require_interface_names_disjoint_from_objects(interfaces, object_api_names)
    violations: list[dict[str, object]] = []
    for object_api_name, object_def in object_api_names.items():
        violations.extend(_object_type_conformance_violations(object_api_name, object_def, interfaces))
    if violations:
        raise ValidationFailed(
            "object types do not conform to declared interfaces",
            details={"violations": violations},
        )


def _require_interface_names_disjoint_from_objects(
    interfaces: dict[str, InterfaceTypeDefinition],
    object_api_names: dict[str, YamlObject],
) -> None:
    # Generated SDKs emit one const per apiName, so an interface shadowing an
    # object type would collide in every generated module.
    collisions = sorted(set(interfaces) & set(object_api_names))
    if collisions:
        raise ValidationFailed(
            "interface apiName collides with an object type apiName",
            details={"apiNames": collisions},
        )


def _object_type_conformance_violations(
    object_api_name: str,
    object_def: YamlObject,
    interfaces: dict[str, InterfaceTypeDefinition],
) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    properties = {required_str(prop, "apiName"): prop for prop in mapping_sequence(object_def, "properties")}
    for interface_api_name in object_type_implements(object_def):
        interface = interfaces.get(interface_api_name)
        if interface is None:
            violations.append(
                _violation("unknown_interface", object_api_name, interface_api_name, property_api_name=None)
            )
            continue
        for contract in interface["properties"]:
            violations.extend(_property_conformance_violations(object_api_name, interface, contract, properties))
    return violations


def _property_conformance_violations(
    object_api_name: str,
    interface: InterfaceTypeDefinition,
    contract: InterfacePropertyDefinition,
    properties: dict[str, YamlObject],
) -> list[dict[str, object]]:
    interface_api_name = interface["apiName"]
    declared = properties.get(contract["apiName"])
    if declared is None:
        return [_violation("missing_property", object_api_name, interface_api_name, contract["apiName"])]
    violations: list[dict[str, object]] = []
    declared_type = required_str(declared, "type")
    if declared_type != contract["type"]:
        violation = _violation("type_mismatch", object_api_name, interface_api_name, contract["apiName"])
        violation["expectedType"] = contract["type"]
        violation["declaredType"] = declared_type
        violations.append(violation)
    if contract["indexed"] and not optional_bool(declared, "indexed", False):
        violations.append(_violation("indexed_downgrade", object_api_name, interface_api_name, contract["apiName"]))
    if not contract["nullable"] and optional_bool(declared, "nullable", True):
        violations.append(_violation("nullable_loosened", object_api_name, interface_api_name, contract["apiName"]))
    return violations


def _violation(
    kind: str,
    object_api_name: str,
    interface_api_name: str,
    property_api_name: str | None,
) -> dict[str, object]:
    violation: dict[str, object] = {
        "kind": kind,
        "objectType": object_api_name,
        "interface": interface_api_name,
    }
    if property_api_name is not None:
        violation["property"] = property_api_name
    return violation


def import_interface_types(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    definition: YamlObject,
) -> None:
    """Persist the normalized interface definitions for a draft ontology version."""
    for api_name, interface in sorted(interface_definitions_by_api(definition).items()):
        repository.insert_interface_type(
            transaction=conn,
            record=InterfaceTypeRecord(
                interface_type_id=_new_id("itype"),
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
                api_name=api_name,
                display_name=interface["displayName"],
                definition=interface,
            ),
        )
