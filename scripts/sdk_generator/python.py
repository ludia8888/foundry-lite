"""Generated Python OSDK rendering for statically typed Action contracts."""

from __future__ import annotations

import json
import keyword
import re

from scripts.sdk_generator.ontology import ActionDef, OntologyDef, ParameterDef
from scripts.sdk_generator.surface import contract_fingerprint


def render_python(ontology: OntologyDef) -> str:
    parameters = tuple(parameter for action in ontology.actions for parameter in _walk_parameters(action.parameters))
    imports = _import_lines(parameters)
    runtime_types = ["GeneratedActionType"]
    if _uses_object_reference(parameters):
        runtime_types.append("OntologyObjectReference")
    if _uses_media_reference(parameters):
        runtime_types.append("ActionMediaReference")
    lines = [
        '"""Generated from the active Ontology; do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        *imports,
        "from foundry_lite.application.osdk_actions import action_type",
        "",
        "from foundry_lite_sdk.runtime import (",
        *(f"    {name}," for name in runtime_types),
        ")",
    ]
    lines.extend(["", f'ONTOLOGY_CONTRACT_FINGERPRINT = "{contract_fingerprint(ontology)}"', "", ""])
    for action in ontology.actions:
        lines.extend(_action_type_lines(action))
    lines.extend(_action_registry_lines(ontology.actions))
    lines.extend(_all_lines(ontology.actions))
    return "\n".join(lines) + "\n"


def render_python_init(ontology: OntologyDef) -> str:
    generated_names = ["ACTIONS", "ONTOLOGY_CONTRACT_FINGERPRINT"]
    for action in ontology.actions:
        generated_names.extend([_python_identifier(action.api_name), f"{_pascal_identifier(action.api_name)}Params"])
    runtime_names = [
        "ActionEffectReconcileRequest",
        "ActionEffectReconciliationEvidence",
        "ActionMediaReference",
        "ActionNotificationPolicyCreateRequest",
        "ActionNotificationPolicyUpdateRequest",
        "ActionNotificationRecipient",
        "GeneratedActionClient",
        "GeneratedActionEffectClient",
        "GeneratedActionNotificationPolicyClient",
        "GeneratedActionType",
        "OntologyObjectReference",
    ]
    lines = [
        '"""Generated version-pinned Python OSDK package surface."""',
        "",
        "from foundry_lite_sdk.generated import (",
        *(f"    {name}," for name in generated_names),
        ")",
        "from foundry_lite_sdk.runtime import (",
        *(f"    {name}," for name in runtime_names),
        ")",
        "",
        "__all__ = [",
        *(f'    "{name}",' for name in generated_names),
        '    "GeneratedActionClient",',
        '    "GeneratedActionEffectClient",',
        '    "GeneratedActionNotificationPolicyClient",',
        '    "GeneratedActionType",',
        '    "OntologyObjectReference",',
        '    "ActionMediaReference",',
        '    "ActionNotificationRecipient",',
        '    "ActionNotificationPolicyCreateRequest",',
        '    "ActionNotificationPolicyUpdateRequest",',
        '    "ActionEffectReconciliationEvidence",',
        '    "ActionEffectReconcileRequest",',
        "]",
    ]
    return "\n".join(lines) + "\n"


def _import_lines(parameters: tuple[ParameterDef, ...]) -> list[str]:
    data_types = {parameter.data_type for parameter in parameters}
    lines = [_collections_import(data_types)]
    if temporal_import := _temporal_import(data_types):
        lines.append(temporal_import)
    if "decimal" in data_types:
        lines.append("from decimal import Decimal")
    lines.extend((_typing_import(parameters), ""))
    return lines


def _collections_import(data_types: set[str]) -> str:
    if data_types & {"array", "objectSet"}:
        return "from collections.abc import Mapping, Sequence"
    return "from collections.abc import Mapping"


def _temporal_import(data_types: set[str]) -> str | None:
    names = [name for name in ("date", "datetime") if _requires_temporal_type(name, data_types)]
    return f"from datetime import {', '.join(names)}" if names else None


def _requires_temporal_type(name: str, data_types: set[str]) -> bool:
    source_type = "timestamp" if name == "datetime" else name
    return source_type in data_types


def _typing_import(parameters: tuple[ParameterDef, ...]) -> str:
    names = ["NotRequired", "TypedDict"] if any(not item.is_required for item in parameters) else ["TypedDict"]
    return f"from typing import {', '.join(names)}"


def _action_type_lines(action: ActionDef) -> list[str]:
    lines: list[str] = []
    for parameter in action.parameters:
        lines.extend(_struct_type_lines(parameter))
    params_name = f"{_pascal_identifier(action.api_name)}Params"
    lines.extend(_typed_dict_lines(params_name, action.parameters))
    constant_name = _python_identifier(action.api_name)
    parameter_names = _tuple_literal(tuple(parameter.api_name for parameter in action.parameters))
    required_names = _tuple_literal(
        tuple(parameter.api_name for parameter in action.parameters if parameter.is_required)
    )
    lines.extend(
        [
            f"{constant_name}: GeneratedActionType[{params_name}] = GeneratedActionType(",
            "    action_type(",
            f'        "{action.api_name}",',
            f'        target_object_type="{action.target}",',
            f'        target_kind="{action.target_kind}",',
            f"        parameter_names={parameter_names},",
            f"        required_parameters={required_names},",
            "    )",
            ")",
            "",
        ]
    )
    return lines


def _struct_type_lines(parameter: ParameterDef) -> list[str]:
    if parameter.type_name is None:
        return []
    lines: list[str] = []
    for field in parameter.fields:
        lines.extend(_struct_type_lines(field))
    lines.extend(_typed_dict_lines(parameter.type_name, parameter.fields))
    return lines


def _typed_dict_lines(type_name: str, parameters: tuple[ParameterDef, ...]) -> list[str]:
    if all(_is_python_identifier(parameter.api_name) for parameter in parameters):
        lines = [f"class {type_name}(TypedDict):"]
        if not parameters:
            lines.append("    pass")
        for parameter in parameters:
            field_type = parameter.python_type
            if not parameter.is_required:
                field_type = f"NotRequired[{field_type}]"
            lines.append(f"    {parameter.api_name}: {field_type}")
        lines.extend(["", ""])
        return lines
    lines = [f'{type_name} = TypedDict("{type_name}", {{  # noqa: UP013']
    for parameter in parameters:
        field_type = parameter.python_type
        if not parameter.is_required:
            field_type = f"NotRequired[{field_type}]"
        lines.append(f'    "{parameter.api_name}": {field_type},')
    lines.extend(["})", "", ""])
    return lines


def _action_registry_lines(actions: tuple[ActionDef, ...]) -> list[str]:
    lines = ["ACTIONS: Mapping[str, object] = {"]
    for action in actions:
        lines.append(f'    "{action.api_name}": {_python_identifier(action.api_name)},')
    lines.extend(["}", ""])
    return lines


def _all_lines(actions: tuple[ActionDef, ...]) -> list[str]:
    names = ["ACTIONS", "ONTOLOGY_CONTRACT_FINGERPRINT"]
    for action in actions:
        names.extend([_python_identifier(action.api_name), f"{_pascal_identifier(action.api_name)}Params"])
    lines = ["__all__ = ["]
    lines.extend(f'    "{name}",' for name in names)
    lines.append("]")
    return lines


def _walk_parameters(parameters: tuple[ParameterDef, ...]) -> list[ParameterDef]:
    result: list[ParameterDef] = []
    for parameter in parameters:
        result.append(parameter)
        result.extend(_walk_parameters(parameter.fields))
    return result


def _uses_object_reference(parameters: tuple[ParameterDef, ...]) -> bool:
    return any(
        parameter.data_type in {"object", "interface"} or parameter.item_type in {"object", "interface"}
        for parameter in parameters
    )


def _uses_media_reference(parameters: tuple[ParameterDef, ...]) -> bool:
    return any(
        parameter.data_type in {"media", "attachment"} or parameter.item_type in {"media", "attachment"}
        for parameter in parameters
    )


def _tuple_literal(values: tuple[str, ...]) -> str:
    if not values:
        return "()"
    suffix = "," if len(values) == 1 else ""
    return f"({', '.join(json.dumps(value) for value in values)}{suffix})"


def _is_python_identifier(value: str) -> bool:
    return value.isidentifier() and not keyword.iskeyword(value)


def _python_identifier(value: str) -> str:
    normalized = re.sub(r"\W", "_", value)
    if not normalized or normalized[0].isdigit():
        normalized = f"action_{normalized}"
    return f"{normalized}_" if keyword.iskeyword(normalized) else normalized


def _pascal_identifier(value: str) -> str:
    chunks = [chunk for chunk in re.split(r"\W+|_+", value) if chunk]
    normalized = "".join(chunk[:1].upper() + chunk[1:] for chunk in chunks)
    return normalized if normalized and not normalized[0].isdigit() else f"Action{normalized}"
