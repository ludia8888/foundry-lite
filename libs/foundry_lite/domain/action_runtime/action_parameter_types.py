"""Shared structural type matching for Action parameters and defaults."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.domain.action_runtime.action_parameter_constraints import (
    validate_action_parameter_constraints_for_value,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.scalar_values import matches_scalar_type

_SCALAR_TYPES = frozenset({"string", "integer", "long", "float", "decimal", "boolean", "date", "timestamp"})
_MEDIA_TYPES = frozenset({"media", "attachment"})


class ActionParameterShape(Protocol):
    @property
    def data_type(self) -> str: ...

    @property
    def metadata(self) -> Mapping[str, object]: ...


def matches_action_parameter_type(parameter: ActionParameterShape, value: object) -> bool:
    """Match one value against the canonical recursive Action parameter shape."""

    return matches_action_parameter_shape(parameter.data_type, parameter.metadata, value)


def matches_action_parameter_shape(data_type: str, metadata: Mapping[str, object], value: object) -> bool:
    """Match a value without importing the Action contract dataclass and creating a cycle."""

    if data_type in _SCALAR_TYPES:
        return matches_scalar_type(data_type, value)
    if data_type in {"object", "interface"}:
        return _matches_object_reference(value)
    if data_type in _MEDIA_TYPES:
        return _matches_media_reference(value, data_type)
    if data_type in {"array", "objectSet"}:
        return _matches_array(data_type, metadata, value)
    if data_type == "struct":
        return _matches_struct(metadata, value)
    return False


def _matches_object_reference(value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, Mapping):
        return False
    reference = cast(Mapping[object, object], value)
    return _non_empty_text(reference.get("objectType")) and _non_empty_text(reference.get("objectId"))


def _matches_media_reference(value: object, reference_kind: str) -> bool:
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, Mapping):
        return False
    reference = cast(Mapping[object, object], value)
    required_text = (
        "mediaSetId",
        "mediaItemId",
        "mediaItemVersionId",
        "logicalPath",
        "contentHash",
        "mimeType",
        "classification",
    )
    byte_size = reference.get("byteSize")
    return (
        reference.get("referenceKind") == reference_kind
        and all(_non_empty_text(reference.get(key)) for key in required_text)
        and isinstance(byte_size, int)
        and not isinstance(byte_size, bool)
        and byte_size >= 0
    )


def _matches_array(data_type: str, metadata: Mapping[str, object], value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return False
    item_type = metadata.get("itemType")
    if not isinstance(item_type, str) or not item_type:
        return False
    item_metadata = {key: item for key, item in metadata.items() if key != "itemType"}
    items = cast(Sequence[object], value)
    if not all(matches_action_parameter_shape(item_type, item_metadata, item) for item in items):
        return False
    return data_type != "objectSet" or _has_unique_json_values(items)


def _matches_struct(metadata: Mapping[str, object], value: object) -> bool:
    fields = metadata.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes) or not fields:
        return False
    if not isinstance(value, Mapping):
        return False
    values = cast(Mapping[object, object], value)
    raw_fields = cast(Sequence[object], fields)
    names = {_field_name(field) for field in raw_fields}
    if None in names or set(values) - names:
        return False
    return all(_matches_struct_field(field, values) for field in raw_fields)


def _matches_struct_field(raw: object, values: Mapping[object, object]) -> bool:
    if not isinstance(raw, Mapping):
        return False
    field = cast(Mapping[str, object], raw)
    name = _field_name(field)
    data_type = field.get("type")
    if name is None or not isinstance(data_type, str):
        return False
    if name not in values:
        return field.get("required") is not True
    metadata = _field_metadata(field)
    value = values[name]
    if not matches_action_parameter_shape(data_type, metadata, value):
        return False
    return _field_constraints_match(name, data_type, value, field.get("constraints"))


def _field_constraints_match(name: str, data_type: str, value: object, raw: object) -> bool:
    if raw is None:
        constraints: Mapping[str, object] = {}
    elif isinstance(raw, Mapping):
        raw_constraints = cast(Mapping[object, object], raw)
        if not all(isinstance(key, str) for key in raw_constraints):
            return False
        constraints = {cast(str, key): item for key, item in raw_constraints.items()}
    else:
        return False
    try:
        validate_action_parameter_constraints_for_value(name, data_type, value, constraints)
    except ValidationFailed:
        return False
    return True


def _field_metadata(field: Mapping[str, object]) -> dict[str, object]:
    known = {"apiName", "type", "required", "description", "default", "constraints", "overrides"}
    return {key: value for key, value in field.items() if key not in known}


def _field_name(raw: object) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    field = cast(Mapping[object, object], raw)
    name = field.get("apiName")
    return name if isinstance(name, str) and bool(name) else None


def _has_unique_json_values(values: Sequence[object]) -> bool:
    identities = [json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for value in values]
    return len(identities) == len(set(identities))


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


__all__ = ["matches_action_parameter_shape", "matches_action_parameter_type"]
