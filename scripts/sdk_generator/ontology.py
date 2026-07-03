"""Ontology YAML loading and typed definition parsing for the OSDK generator."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

SCALAR_TYPES = {
    "boolean": "boolean",
    "float": "number",
    "integer": "number",
    "string": "string",
}


@dataclass(frozen=True)
class PropertyDef:
    api_name: str
    ts_type: str
    is_required: bool
    is_sensitive: bool


@dataclass(frozen=True)
class ObjectDef:
    api_name: str
    properties: tuple[PropertyDef, ...]
    # Part of the fingerprinted contract: retitling an object type must surface
    # as SDK drift just like adding or removing a property.
    title_property: str | None = None


@dataclass(frozen=True)
class ParameterDef:
    api_name: str
    ts_type: str
    is_required: bool


@dataclass(frozen=True)
class ActionDef:
    api_name: str
    target: str
    parameters: tuple[ParameterDef, ...]


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


def load_ontology(path: Path) -> OntologyDef:
    document = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "ontology")
    return OntologyDef(
        objects=tuple(_object_def(row) for row in _sequence(document.get("objectTypes"), "objectTypes")),
        links=tuple(_link_def(row) for row in _sequence(document.get("linkTypes"), "linkTypes")),
        actions=tuple(_action_def(row) for row in _sequence(document.get("actionTypes"), "actionTypes")),
    )


def _object_def(value: object) -> ObjectDef:
    row = _mapping(value, "objectTypes[]")
    properties = tuple(_property_def(item) for item in _sequence(row.get("properties"), "properties"))
    api_name = _string(row.get("apiName"), "object apiName")
    return ObjectDef(
        api_name=api_name,
        properties=properties,
        title_property=_title_property(row, api_name, properties),
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


def _property_def(value: object) -> PropertyDef:
    row = _mapping(value, "properties[]")
    is_sensitive = isinstance(row.get("classification"), str)
    ts_type = _ts_type(_string(row.get("type"), "property type"), is_sensitive=is_sensitive)
    return PropertyDef(
        api_name=_string(row.get("apiName"), "property apiName"),
        ts_type=ts_type,
        is_required=row.get("nullable") is False,
        is_sensitive=is_sensitive,
    )


def _action_def(value: object) -> ActionDef:
    row = _mapping(value, "actionTypes[]")
    parameters = tuple(_parameter_def(item) for item in _sequence(row.get("parameters"), "parameters"))
    return ActionDef(
        api_name=_string(row.get("apiName"), "action apiName"),
        target=_string(row.get("target"), "action target"),
        parameters=parameters,
    )


def _link_def(value: object) -> LinkDef:
    row = _mapping(value, "linkTypes[]")
    return LinkDef(
        api_name=_string(row.get("apiName"), "link apiName"),
        from_object=_string(row.get("from"), "link from"),
        to_object=_string(row.get("to"), "link to"),
    )


def _parameter_def(value: object) -> ParameterDef:
    row = _mapping(value, "parameters[]")
    return ParameterDef(
        api_name=_string(row.get("apiName"), "parameter apiName"),
        ts_type=_ts_type(_string(row.get("type"), "parameter type"), is_sensitive=False),
        is_required=row.get("required") is not False,
    )


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
