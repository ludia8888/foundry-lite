"""Cross-resource validation for version-pinned Action function contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.ontology_yaml import YamlObject, mapping_sequence
from foundry_lite.domain.action_runtime.action_contract import (
    ActionDefinitionV3,
    ActionParameterV3,
    action_parameter_json_schema,
    compile_action_contract,
)
from foundry_lite.domain.errors import ValidationFailed


def require_action_function_contract(
    contract: ActionDefinitionV3,
    functions: Mapping[str, YamlObject],
) -> None:
    """Require the pinned version and the exact parameter-to-function mapping."""
    if contract.function is None:
        return
    if contract.target.kind == "interface":
        raise ValidationFailed(
            "interface Actions cannot use functions",
            details={"actionType": contract.api_name, "interface": contract.target.api_name},
        )
    function = functions.get(contract.function.api_name)
    actual_version = str(function.get("version") or "v1") if function is not None else None
    if actual_version != contract.function.version:
        raise ValidationFailed(
            "action function reference or pinned version was not found",
            details={
                "actionType": contract.api_name,
                "function": contract.function.api_name,
                "expectedVersion": contract.function.version,
                "actualVersion": actual_version,
            },
        )
    if function is None:
        raise ValidationFailed("action function reference was not found")
    _require_parameter_contract(contract, function)


def _require_parameter_contract(contract: ActionDefinitionV3, function: YamlObject) -> None:
    function_inputs = list(mapping_sequence(function, "inputs"))
    if contract.function is None:
        return
    expected = _parameter_contract_schema([_parameter_payload(parameter) for parameter in contract.parameters])
    if contract.function.execution_mode == "per_request":
        actual = _parameter_contract_schema(function_inputs)
    else:
        batch_input = _required_batch_input(contract, function_inputs)
        actual = _parameter_contract_schema(list(mapping_sequence(batch_input, "fields")))
    if actual != expected:
        raise ValidationFailed(
            "Action parameters do not match the pinned function input contract",
            details={
                "actionType": contract.api_name,
                "function": contract.function.api_name,
                "executionMode": contract.function.execution_mode,
            },
        )


def _required_batch_input(contract: ActionDefinitionV3, function_inputs: list[YamlObject]) -> YamlObject:
    function = contract.function
    if function is None or len(function_inputs) != 1:
        raise ValidationFailed("batched Action function must declare exactly one input")
    batch_input = function_inputs[0]
    if (
        batch_input.get("apiName") != function.batch_input_name
        or batch_input.get("type") != "array"
        or batch_input.get("itemType") != "struct"
        or batch_input.get("required") is not True
    ):
        raise ValidationFailed(
            "batched Action function input must be one required list of structs",
            details={"actionType": contract.api_name, "batchInputName": function.batch_input_name},
        )
    return batch_input


def _parameter_contract_schema(parameters: Sequence[YamlObject]) -> Mapping[str, object]:
    contract = compile_action_contract(
        {"apiName": "__function_parameter_contract__", "target": "__target__", "parameters": parameters}
    )
    schema = action_parameter_json_schema(contract)
    return {"properties": schema["properties"], "required": schema["required"]}


def _parameter_payload(parameter: ActionParameterV3) -> dict[str, object]:
    payload: dict[str, object] = {
        "apiName": parameter.api_name,
        "type": parameter.data_type,
        "required": parameter.required,
    }
    payload.update({key: _canonical_contract_value(value) for key, value in parameter.metadata.items()})
    if parameter.description is not None:
        payload["description"] = parameter.description
    if parameter.constraints:
        payload["constraints"] = dict(parameter.constraints)
    return payload


def _canonical_contract_value(value: object) -> object:
    """Normalize compiled parameter metadata to the JSON shape used by Function YAML."""
    if isinstance(value, Mapping):
        return {str(key): _canonical_contract_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_contract_value(item) for item in value]
    return value
