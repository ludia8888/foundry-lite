"""Pure Action Type authoring helpers for ontology branch working copies."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import yaml

from foundry_lite.domain.action_runtime.action_contract import (
    action_contract_fingerprint,
    action_contract_payload,
    action_parameter_json_schema,
    compile_action_contract,
)
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


@dataclass(frozen=True, slots=True)
class ActionTypeMutation:
    yaml_text: str
    action_type: dict[str, object] | None
    is_replay: bool


def branch_action_type_items(yaml_text: str) -> list[dict[str, object]]:
    definitions = sorted(_action_definitions(yaml_text), key=lambda item: str(item["apiName"]))
    return [_public_action_type(definition) for definition in definitions]


def branch_action_type_item(yaml_text: str, api_name: str) -> dict[str, object]:
    definition = _definition_by_name(_action_definitions(yaml_text), api_name)
    if definition is None:
        raise NotFound("ontology branch Action Type not found", details={"apiName": api_name})
    return _public_action_type(definition)


def create_branch_action_type(yaml_text: str, definition: Mapping[str, object]) -> ActionTypeMutation:
    normalized = _normalized_definition(definition)
    definitions = _action_definitions(yaml_text)
    existing = _definition_by_name(definitions, str(normalized["apiName"]))
    if existing is not None and existing == normalized:
        return ActionTypeMutation(yaml_text, _public_action_type(existing), True)
    if existing is not None:
        raise ConflictDetected("ontology branch Action Type already exists", details={"apiName": normalized["apiName"]})
    definitions.append(normalized)
    return ActionTypeMutation(
        _replace_action_definitions(yaml_text, definitions), _public_action_type(normalized), False
    )


def update_branch_action_type(
    yaml_text: str,
    api_name: str,
    definition: Mapping[str, object],
) -> ActionTypeMutation:
    normalized = _normalized_definition(definition)
    if normalized["apiName"] != api_name:
        raise ValidationFailed("Action Type path and definition apiName must match")
    definitions = _action_definitions(yaml_text)
    index = _definition_index(definitions, api_name)
    if index is None:
        raise NotFound("ontology branch Action Type not found", details={"apiName": api_name})
    if definitions[index] == normalized:
        return ActionTypeMutation(yaml_text, _public_action_type(definitions[index]), True)
    definitions[index] = normalized
    return ActionTypeMutation(
        _replace_action_definitions(yaml_text, definitions), _public_action_type(normalized), False
    )


def delete_branch_action_type(yaml_text: str, api_name: str) -> ActionTypeMutation:
    definitions = _action_definitions(yaml_text)
    index = _definition_index(definitions, api_name)
    if index is None:
        return ActionTypeMutation(yaml_text, None, True)
    definitions.pop(index)
    return ActionTypeMutation(_replace_action_definitions(yaml_text, definitions), None, False)


def _public_action_type(definition: Mapping[str, object]) -> dict[str, object]:
    contract = compile_action_contract(definition)
    contract_payload = action_contract_payload(contract)
    return {
        "apiName": contract.api_name,
        "displayName": contract.display_name,
        "definition": dict(definition),
        "contract": contract_payload,
        "contractFingerprint": action_contract_fingerprint(contract),
        "parameterSchema": action_parameter_json_schema(contract),
        "inlineEligibility": contract_payload["inlineEligibility"],
    }


def _normalized_definition(definition: Mapping[str, object]) -> dict[str, object]:
    normalized = cast(dict[str, object], json.loads(json.dumps(definition, sort_keys=True)))
    contract = compile_action_contract(normalized)
    if contract.source_version != 3:
        raise ValidationFailed("branch Action Type authoring requires contractVersion 3")
    return normalized


def _loaded_definition(yaml_text: str) -> dict[str, object]:
    loaded = yaml.safe_load(yaml_text)
    if not isinstance(loaded, Mapping):
        raise ValidationFailed("ontology branch content must be a mapping")
    return cast(dict[str, object], json.loads(json.dumps(loaded, sort_keys=True)))


def _action_definitions(yaml_text: str) -> list[dict[str, object]]:
    raw = _loaded_definition(yaml_text).get("actionTypes", [])
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed("actionTypes must be a list")
    if not all(isinstance(item, Mapping) for item in raw):
        raise ValidationFailed("actionTypes entries must be objects")
    return [cast(dict[str, object], item) for item in raw]


def _replace_action_definitions(yaml_text: str, definitions: list[dict[str, object]]) -> str:
    loaded = _loaded_definition(yaml_text)
    loaded["actionTypes"] = sorted(definitions, key=lambda item: str(item.get("apiName", "")))
    return json.dumps(loaded, indent=2, sort_keys=True)


def _definition_by_name(definitions: list[dict[str, object]], api_name: str) -> dict[str, object] | None:
    index = _definition_index(definitions, api_name)
    return definitions[index] if index is not None else None


def _definition_index(definitions: list[dict[str, object]], api_name: str) -> int | None:
    for index, definition in enumerate(definitions):
        if definition.get("apiName") == api_name:
            return index
    return None
