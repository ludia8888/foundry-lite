"""Ontology YAML loading and typed definition parsing for the OSDK generator."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

SCALAR_TYPES = {
    "boolean": "boolean",
    "float": "number",
    "integer": "number",
    "long": "number",
    "decimal": "number",
    "date": "string",
    "timestamp": "string",
    "string": "string",
    "media": "ActionMediaReference",
    "attachment": "ActionMediaReference",
}

ACTION_PARAMETER_TYPES = frozenset({*SCALAR_TYPES, "object", "interface", "objectSet", "array", "struct"})


@dataclass(frozen=True)
class PropertyDef:
    api_name: str
    ts_type: str
    is_required: bool
    is_sensitive: bool
    # Datasource segment feeding this property (None for edit-layer
    # properties). Part of the fingerprinted contract: moving a property to a
    # differently-governed datasource must surface as SDK drift.
    datasource: str | None = None


@dataclass(frozen=True)
class ObjectDef:
    api_name: str
    properties: tuple[PropertyDef, ...]
    # Part of the fingerprinted contract: retitling an object type must surface
    # as SDK drift just like adding or removing a property.
    title_property: str | None = None
    # Interfaces this object type implements; changing membership changes the
    # polymorphic SDK contract, so it fingerprints too.
    implements: tuple[str, ...] = ()


@dataclass(frozen=True)
class InterfacePropertyDef:
    api_name: str
    ts_type: str


@dataclass(frozen=True)
class InterfaceDef:
    api_name: str
    properties: tuple[InterfacePropertyDef, ...]
    implementers: tuple[str, ...]


@dataclass(frozen=True)
class FunctionInputDef:
    api_name: str
    ts_type: str
    is_required: bool


@dataclass(frozen=True)
class FunctionDef:
    """Callable signature of one registered ontology function.

    Only the signature (apiName, inputs, output) is part of the generated SDK
    contract; the Logic DAG body may change without an SDK-visible drift.
    """

    api_name: str
    inputs: tuple[FunctionInputDef, ...]
    output_ts_type: str


@dataclass(frozen=True)
class ParameterDef:
    api_name: str
    ts_type: str
    python_type: str
    is_required: bool
    data_type: str
    type_name: str | None = None
    item_type: str | None = None
    fields: tuple[ParameterDef, ...] = ()


@dataclass(frozen=True)
class ActionDef:
    api_name: str
    target: str
    parameters: tuple[ParameterDef, ...]
    target_kind: str = "object"


@dataclass(frozen=True)
class LinkDef:
    api_name: str
    from_object: str
    to_object: str


@dataclass(frozen=True)
class OntologyDef:
    objects: tuple[ObjectDef, ...]
    links: tuple[LinkDef, ...]
    actions: tuple[ActionDef, ...]
    interfaces: tuple[InterfaceDef, ...] = ()
    functions: tuple[FunctionDef, ...] = ()


def load_ontology(path: Path) -> OntologyDef:
    document = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "ontology")
    objects = tuple(_object_def(row) for row in _sequence(document.get("objectTypes"), "objectTypes"))
    interfaces = tuple(_interface_def(row, objects) for row in _sequence(document.get("interfaces"), "interfaces"))
    _require_known_implements(objects, interfaces)
    return OntologyDef(
        objects=objects,
        links=tuple(_link_def(row) for row in _sequence(document.get("linkTypes"), "linkTypes")),
        actions=tuple(_action_def(row) for row in _sequence(document.get("actionTypes"), "actionTypes")),
        interfaces=interfaces,
        functions=tuple(_function_def(row) for row in _sequence(document.get("functionTypes"), "functionTypes")),
    )


def _require_known_implements(objects: tuple[ObjectDef, ...], interfaces: tuple[InterfaceDef, ...]) -> None:
    interface_names = {interface.api_name for interface in interfaces}
    for obj in objects:
        unknown = [name for name in obj.implements if name not in interface_names]
        if unknown:
            raise ValueError(f"object {obj.api_name} implements must reference a declared interface")


def _object_def(value: object) -> ObjectDef:
    row = _mapping(value, "objectTypes[]")
    datasource_names = _datasource_names(row)
    properties = tuple(_property_def(item, datasource_names) for item in _sequence(row.get("properties"), "properties"))
    api_name = _string(row.get("apiName"), "object apiName")
    return ObjectDef(
        api_name=api_name,
        properties=properties,
        title_property=_title_property(row, api_name, properties),
        implements=tuple(
            _string(item, "object implements[]") for item in _sequence(row.get("implements"), "implements")
        ),
    )


def _datasource_names(row: Mapping[str, object]) -> tuple[str, ...]:
    """Declared datasource names in order; single-dataset backings normalize to ``primary``."""
    backing = row.get("backing")
    if not isinstance(backing, dict):
        return ("primary",)
    declared = backing.get("datasources")
    if not isinstance(declared, list) or not declared:
        return ("primary",)
    names: list[str] = []
    for item in declared:
        entry = _mapping(item, "backing datasources[]")
        names.append(_string(entry.get("name"), "datasource name"))
    return tuple(names)


def _interface_def(value: object, objects: tuple[ObjectDef, ...]) -> InterfaceDef:
    row = _mapping(value, "interfaces[]")
    api_name = _string(row.get("apiName"), "interface apiName")
    properties = tuple(_interface_property_def(item) for item in _sequence(row.get("properties"), "properties"))
    implementers = tuple(obj.api_name for obj in objects if api_name in obj.implements)
    _require_conforming_implementers(api_name, properties, objects)
    return InterfaceDef(api_name=api_name, properties=properties, implementers=implementers)


def _require_conforming_implementers(
    api_name: str,
    properties: tuple[InterfacePropertyDef, ...],
    objects: tuple[ObjectDef, ...],
) -> None:
    for obj in objects:
        if api_name not in obj.implements:
            continue
        declared = {prop.api_name for prop in obj.properties}
        missing = [prop.api_name for prop in properties if prop.api_name not in declared]
        if missing:
            raise ValueError(f"object {obj.api_name} implements {api_name} but is missing {', '.join(missing)}")


def _interface_property_def(value: object) -> InterfacePropertyDef:
    row = _mapping(value, "interface properties[]")
    return InterfacePropertyDef(
        api_name=_string(row.get("apiName"), "interface property apiName"),
        ts_type=_ts_type(_string(row.get("type"), "interface property type"), is_sensitive=False),
    )


def _title_property(
    row: Mapping[str, object],
    api_name: str,
    properties: tuple[PropertyDef, ...],
) -> str | None:
    value = row.get("titleProperty")
    if value is None:
        return None
    title = _string(value, "object titleProperty")
    if title not in {prop.api_name for prop in properties}:
        raise ValueError(f"object {api_name} titleProperty must reference a declared property")
    return title


def _property_def(value: object, datasource_names: tuple[str, ...]) -> PropertyDef:
    row = _mapping(value, "properties[]")
    is_sensitive = isinstance(row.get("classification"), str)
    ts_type = _ts_type(_string(row.get("type"), "property type"), is_sensitive=is_sensitive)
    return PropertyDef(
        api_name=_string(row.get("apiName"), "property apiName"),
        ts_type=ts_type,
        is_required=row.get("nullable") is False,
        is_sensitive=is_sensitive,
        datasource=_property_datasource(row, datasource_names),
    )


def _property_datasource(row: Mapping[str, object], datasource_names: tuple[str, ...]) -> str | None:
    source = row.get("source", "dataset" if "column" in row else "edit_layer")
    if source != "dataset":
        return None
    declared = row.get("datasource")
    if declared is None:
        return datasource_names[0]
    name = _string(declared, "property datasource")
    if name not in datasource_names:
        raise ValueError(f"property datasource {name} must reference a declared backing datasource")
    return name


def _function_def(value: object) -> FunctionDef:
    row = _mapping(value, "functionTypes[]")
    output = _mapping(row.get("output"), "function output")
    return FunctionDef(
        api_name=_string(row.get("apiName"), "function apiName"),
        inputs=tuple(_function_input_def(item) for item in _sequence(row.get("inputs"), "function inputs")),
        output_ts_type=_ts_type(_string(output.get("type"), "function output type"), is_sensitive=False),
    )


def _function_input_def(value: object) -> FunctionInputDef:
    row = _mapping(value, "function inputs[]")
    return FunctionInputDef(
        api_name=_string(row.get("apiName"), "function input apiName"),
        ts_type=_ts_type(_string(row.get("type"), "function input type"), is_sensitive=False),
        is_required=row.get("required") is True,
    )


def _action_def(value: object) -> ActionDef:
    row = _mapping(value, "actionTypes[]")
    api_name = _string(row.get("apiName"), "action apiName")
    target_kind, target = _action_target(row)
    parameters = tuple(
        _parameter_def(item, owner_name=api_name) for item in _sequence(row.get("parameters"), "parameters")
    )
    return ActionDef(
        api_name=api_name,
        target=target,
        parameters=parameters,
        target_kind=target_kind,
    )


def _action_target(row: Mapping[str, object]) -> tuple[str, str]:
    raw = row.get("target")
    if isinstance(raw, Mapping):
        target = _string(raw.get("apiName"), "action target apiName")
        kind = _string(raw.get("kind", "object"), "action target kind")
    else:
        target = _string(raw, "action target")
        kind = _string(row.get("targetKind", "object"), "action target kind")
    if kind not in {"object", "interface"}:
        raise ValueError("action target kind must be object or interface")
    return kind, target


def _link_def(value: object) -> LinkDef:
    row = _mapping(value, "linkTypes[]")
    return LinkDef(
        api_name=_string(row.get("apiName"), "link apiName"),
        from_object=_string(row.get("from"), "link from"),
        to_object=_string(row.get("to"), "link to"),
    )


def _parameter_def(value: object, *, owner_name: str) -> ParameterDef:
    row = _mapping(value, "parameters[]")
    api_name = _string(row.get("apiName"), "parameter apiName")
    data_type = _string(row.get("type"), "parameter type")
    if data_type not in ACTION_PARAMETER_TYPES:
        raise ValueError(f"unsupported action parameter type {data_type}")
    type_name = f"{owner_name}{_pascal_identifier(api_name)}Struct" if data_type == "struct" else None
    fields = _parameter_fields(row, type_name)
    item_type = _parameter_item_type(row, data_type)
    return ParameterDef(
        api_name=api_name,
        ts_type=_action_ts_type(data_type, type_name=type_name, item_type=item_type),
        python_type=_action_python_type(data_type, type_name=type_name, item_type=item_type),
        is_required=row.get("required") is not False,
        data_type=data_type,
        type_name=type_name,
        item_type=item_type,
        fields=fields,
    )


def _parameter_fields(row: Mapping[str, object], type_name: str | None) -> tuple[ParameterDef, ...]:
    if type_name is None:
        return ()
    raw_fields = _sequence(row.get("fields"), "struct fields")
    if not raw_fields:
        raise ValueError("struct action parameter requires at least one typed field")
    return tuple(_parameter_def(item, owner_name=type_name.removesuffix("Struct")) for item in raw_fields)


def _parameter_item_type(row: Mapping[str, object], data_type: str) -> str | None:
    if data_type not in {"array", "objectSet"}:
        return None
    item_type = _string(row.get("itemType", "string"), "parameter itemType")
    if item_type not in SCALAR_TYPES and item_type not in {"object", "interface"}:
        raise ValueError("array action parameter itemType is unsupported")
    return item_type


def _action_ts_type(data_type: str, *, type_name: str | None, item_type: str | None) -> str:
    if data_type in {"media", "attachment"}:
        return "ActionMediaInput"
    if data_type in {"object", "interface"}:
        return "string | ObjectReference"
    if data_type in {"array", "objectSet"}:
        assert item_type is not None
        return f"ReadonlyArray<{_action_ts_type(item_type, type_name=None, item_type=None)}>"
    if data_type == "struct":
        assert type_name is not None
        return type_name
    return SCALAR_TYPES.get(data_type, "unknown")


def _action_python_type(data_type: str, *, type_name: str | None, item_type: str | None) -> str:
    scalar = {
        "boolean": "bool",
        "float": "float",
        "integer": "int",
        "long": "int",
        "decimal": "Decimal",
        "date": "date",
        "timestamp": "datetime",
        "string": "str",
        "media": "str | ActionMediaReference",
        "attachment": "str | ActionMediaReference",
        "object": "str | OntologyObjectReference",
        "interface": "str | OntologyObjectReference",
    }
    if data_type in {"array", "objectSet"}:
        assert item_type is not None
        return f"Sequence[{_action_python_type(item_type, type_name=None, item_type=None)}]"
    if data_type == "struct":
        assert type_name is not None
        return type_name
    return scalar.get(data_type, "object")


def _pascal_identifier(value: str) -> str:
    chunks = [chunk for chunk in re.split(r"\W+|_+", value) if chunk]
    normalized = "".join(chunk[:1].upper() + chunk[1:] for chunk in chunks)
    if not normalized:
        return "Value"
    return normalized if not normalized[0].isdigit() else f"Value{normalized}"


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _ts_type(value: str, *, is_sensitive: bool) -> str:
    scalar = SCALAR_TYPES.get(value, "unknown")
    return f"{scalar} | string" if is_sensitive and scalar != "string" else scalar
