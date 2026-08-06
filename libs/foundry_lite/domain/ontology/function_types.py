"""Pure ontology function-type ("Functions on Objects") definition rules.

A function type is a first-class ontology citizen: a named, versioned,
permissioned Logic DAG persisted per ontology version. This module owns the
canonical persisted shape so YAML import, migration planning, and snapshot
replay all agree byte-for-byte about what one function definition looks like.
Block/tool semantics are NOT validated here — the application layer delegates
that to the existing visual-builder validation surface instead of forking it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.errors import ValidationFailed

FUNCTION_RUNTIME_LOGIC_DAG = "logic_dag"
# Palantir authors functions in TypeScript or Python from a code repository, alongside the
# no-code AIP Logic editor. `logic_dag` is our AIP Logic; `python` is the first code runtime.
# A code function carries source rather than a block graph, so `definition` is shaped per
# runtime rather than shared.
FUNCTION_RUNTIME_PYTHON = "python"
FUNCTION_RUNTIME_TYPESCRIPT = "typescript"
SUPPORTED_FUNCTION_RUNTIMES = frozenset(
    {FUNCTION_RUNTIME_LOGIC_DAG, FUNCTION_RUNTIME_PYTHON, FUNCTION_RUNTIME_TYPESCRIPT}
)
#: Runtimes whose definition is source text rather than a block graph.
CODE_FUNCTION_RUNTIMES = frozenset({FUNCTION_RUNTIME_PYTHON, FUNCTION_RUNTIME_TYPESCRIPT})

# Palantir's default is 60 seconds and, unlike their other deployment settings, timeout is
# configured per function version rather than per function. A version is immutable once
# published, so its timeout travels with it: raising the limit means publishing a new version,
# and an Action pinned to the old one keeps the budget it was tested against.
FUNCTION_DEFAULT_TIMEOUT_SECONDS = 60
# Palantir allows up to 280 seconds in live preview. The ceiling exists so one function cannot
# hold a sandbox slot indefinitely; the container policy timeout remains the outer bound.
FUNCTION_MAX_TIMEOUT_SECONDS = 280

#: Function inputs/outputs use the object property type vocabulary (kept in
#: sync with INTERFACE_PROPERTY_DATA_TYPES / OBJECT_PROPERTY_DATA_TYPES).
FUNCTION_DATA_TYPES = frozenset(
    {
        "array",
        "attachment",
        "boolean",
        "date",
        "decimal",
        "float",
        "integer",
        "interface",
        "long",
        "media",
        "media_reference",
        "object",
        "objectSet",
        "ontology_edit_batch",
        "string",
        "struct",
        "timestamp",
    }
)


def normalized_function_definition(item: Mapping[str, object]) -> dict[str, object]:
    """Return the canonical persisted payload for one ``functionTypes`` entry.

    Normalization is idempotent: replaying a persisted snapshot through this
    parser yields identical definition JSON, so rollback round-trips never
    read as a spurious ``function_definition_changed`` migration warning.
    """
    api_name = _required_str(item, "apiName")
    runtime = _function_runtime(item, api_name)
    normalized: dict[str, object] = {
        "apiName": api_name,
        "displayName": _optional_str(item, "displayName", api_name),
        "version": _optional_str(item, "version", "v1"),
        "runtime": runtime,
        "inputs": _normalized_inputs(item, api_name),
        "output": _normalized_output(item, api_name),
        "definition": _normalized_runtime_definition(item, api_name, runtime),
        "timeoutSeconds": _function_timeout_seconds(item, api_name),
    }
    permissions = _normalized_permissions(item, api_name)
    if permissions is not None:
        normalized["permissions"] = permissions
    return normalized


def _function_timeout_seconds(item: Mapping[str, object], api_name: str) -> int:
    timeout = _optional_int(item, "timeoutSeconds", FUNCTION_DEFAULT_TIMEOUT_SECONDS)
    if timeout < 1 or timeout > FUNCTION_MAX_TIMEOUT_SECONDS:
        raise ValidationFailed(
            "function timeout is outside the permitted range",
            details={"function": api_name, "value": timeout, "max": FUNCTION_MAX_TIMEOUT_SECONDS},
        )
    return timeout


def _function_runtime(item: Mapping[str, object], api_name: str) -> str:
    runtime = _required_str(item, "runtime")
    if runtime not in SUPPORTED_FUNCTION_RUNTIMES:
        raise ValidationFailed(
            "unsupported function runtime",
            details={"function": api_name, "value": runtime, "allowed": sorted(SUPPORTED_FUNCTION_RUNTIMES)},
        )
    return runtime


def _normalized_inputs(item: Mapping[str, object], api_name: str) -> list[dict[str, object]]:
    inputs: list[dict[str, object]] = []
    seen: set[str] = set()
    for input_def in _mapping_sequence(item, "inputs"):
        input_api = _required_str(input_def, "apiName")
        if input_api in seen:
            raise ValidationFailed(
                "duplicate function input apiName", details={"function": api_name, "input": input_api}
            )
        seen.add(input_api)
        inputs.append(_normalized_input(input_def, api_name, f"inputs.{input_api}"))
    return inputs


def _normalized_input(input_def: Mapping[str, object], api_name: str, path: str) -> dict[str, object]:
    _require_function_data_type(input_def, api_name, path)
    normalized: dict[str, object] = {
        "apiName": _required_str(input_def, "apiName"),
        "type": _required_str(input_def, "type"),
        "required": _required_flag(input_def),
    }
    for key in ("description", "objectType", "interfaceType", "itemType", "mediaSet", "render"):
        if key in input_def:
            normalized[key] = _required_str(input_def, key)
    for key in ("default", "constraints"):
        if key in input_def:
            normalized[key] = dict(_required_mapping(input_def, key, api_name))
    if "allowedMimeTypes" in input_def:
        normalized["allowedMimeTypes"] = list(_string_sequence(input_def["allowedMimeTypes"], "allowedMimeTypes"))
    if "maxBytes" in input_def:
        normalized["maxBytes"] = _optional_int(input_def, "maxBytes", 0)
    if "fields" in input_def:
        normalized["fields"] = [
            _normalized_input(field, api_name, f"{path}.fields.{_required_str(field, 'apiName')}")
            for field in _mapping_sequence(input_def, "fields")
        ]
    return normalized


def _normalized_output(item: Mapping[str, object], api_name: str) -> dict[str, object]:
    output = _required_mapping(item, "output", api_name)
    _require_function_data_type(output, api_name, "output")
    return {"type": _required_str(output, "type")}


def _require_function_data_type(container: Mapping[str, object], api_name: str, path: str) -> None:
    declared = _required_str(container, "type")
    if declared not in FUNCTION_DATA_TYPES:
        raise ValidationFailed(
            "unsupported function data type",
            details={"function": api_name, "path": path, "value": declared, "allowed": sorted(FUNCTION_DATA_TYPES)},
        )


def _normalized_runtime_definition(item: Mapping[str, object], api_name: str, runtime: str) -> dict[str, object]:
    """A code function carries source; a Logic DAG carries blocks. Neither accepts the other."""
    if runtime in CODE_FUNCTION_RUNTIMES:
        return _normalized_source(item, api_name)
    return _normalized_logic(item, api_name)


def _normalized_source(item: Mapping[str, object], api_name: str) -> dict[str, object]:
    definition = _required_mapping(item, "definition", api_name)
    source = _required_str(definition, "source")
    if not source.strip():
        raise ValidationFailed("function source must not be blank", details={"function": api_name})
    if "blocks" in definition:
        raise ValidationFailed(
            "a code function cannot declare Logic DAG blocks",
            details={"function": api_name},
        )
    return {
        "source": source,
        # Palantir names the exported entry point; ours defaults so single-function modules
        # need no ceremony, and the runner fails loudly when the name is absent.
        "entrypoint": _optional_str(definition, "entrypoint", "compute"),
    }


def _normalized_logic(item: Mapping[str, object], api_name: str) -> dict[str, object]:
    logic = _required_mapping(item, "definition", api_name)
    blocks = [_normalized_block(block, api_name) for block in _mapping_sequence(logic, "blocks")]
    if not blocks:
        raise ValidationFailed("function definition must declare blocks", details={"function": api_name})
    return {"blocks": blocks, "tools": [_normalized_tool(tool, api_name) for tool in _mapping_sequence(logic, "tools")]}


def _normalized_block(block: Mapping[str, object], api_name: str) -> dict[str, object]:
    return {
        "blockId": _required_str(block, "blockId"),
        "kind": _required_str(block, "kind"),
        "inputs": dict(_optional_mapping(block, "inputs", api_name)),
        "dependsOn": list(_string_sequence(block.get("dependsOn"), "dependsOn")),
    }


def _normalized_tool(tool: Mapping[str, object], api_name: str) -> dict[str, object]:
    """Normalize one governed tool spec payload with the ToolSpec defaults applied.

    Persisting the fully-defaulted shape keeps the registry self-contained (no
    per-request tool manifest) and makes definition-change detection stable.
    """
    return {
        "toolId": _required_str(tool, "toolId"),
        "version": _required_str(tool, "version"),
        "inputSchema": dict(_optional_mapping(tool, "inputSchema", api_name)),
        "outputSchema": dict(_optional_mapping(tool, "outputSchema", api_name)),
        "effect": _optional_str(tool, "effect", "READ"),
        "requiredPermission": _optional_str(tool, "requiredPermission", "object:read"),
        "confirmationPolicy": _optional_str(tool, "confirmationPolicy", "NONE"),
        "objectTypeAllowlist": list(_string_sequence(tool.get("objectTypeAllowlist"), "objectTypeAllowlist")),
        "propertyAllowlist": list(_string_sequence(tool.get("propertyAllowlist"), "propertyAllowlist")),
        "timeoutSeconds": _optional_int(tool, "timeoutSeconds", 30),
        "maxResultItems": _optional_int(tool, "maxResultItems", 50),
        "resultClassification": _optional_str(tool, "resultClassification", "public"),
        "status": _optional_str(tool, "status", "published"),
    }


def _normalized_permissions(item: Mapping[str, object], api_name: str) -> dict[str, object] | None:
    if "permissions" not in item or item["permissions"] is None:
        return None
    permissions = dict(_optional_mapping(item, "permissions", api_name))
    if permissions.get("allowedRoles") is not None:
        permissions["allowedRoles"] = list(_string_sequence(permissions["allowedRoles"], "permissions.allowedRoles"))
    return permissions


def _required_str(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed(f"function {key} must be a non-empty string")
    return value


def _optional_str(mapping: Mapping[str, object], key: str, default: str) -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValidationFailed(f"function {key} must be a non-empty string")
    return value


def _optional_int(mapping: Mapping[str, object], key: str, default: int) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed(f"function {key} must be an integer")
    return value


def _required_flag(mapping: Mapping[str, object]) -> bool:
    value = mapping.get("required", False)
    if not isinstance(value, bool):
        raise ValidationFailed("function input required must be a boolean")
    return value


def _required_mapping(mapping: Mapping[str, object], key: str, api_name: str) -> Mapping[str, object]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"function {key} must be a mapping", details={"function": api_name})
    return cast(Mapping[str, object], value)


def _optional_mapping(mapping: Mapping[str, object], key: str, api_name: str) -> Mapping[str, object]:
    if key not in mapping or mapping[key] is None:
        return {}
    return _required_mapping(mapping, key, api_name)


def _mapping_sequence(container: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    value = container.get(key, ())
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"function {key} must be a list")
    items: list[Mapping[str, object]] = []
    for item in cast(Sequence[object], value):
        if not isinstance(item, Mapping):
            raise ValidationFailed(f"function {key} entries must be mappings")
        items.append(cast(Mapping[str, object], item))
    return tuple(items)


def _string_sequence(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"function {field} must be a list of strings")
    items: list[str] = []
    for item in cast(Sequence[object], value):
        if not isinstance(item, str):
            raise ValidationFailed(f"function {field} entries must be strings")
        items.append(item)
    return tuple(items)
