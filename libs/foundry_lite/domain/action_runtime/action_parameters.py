"""Deterministic parameter resolution for Action Contract v3."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from foundry_lite.domain.action_runtime.action_conditions import (
    StaticActionConditionContext,
    evaluate_action_condition,
)
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, ActionParameterV3
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class ResolvedActionParameterConfig:
    required: bool
    is_visible: bool
    is_editable: bool
    constraints: Mapping[str, object]
    default: Mapping[str, object] | None
    matched_override: int | None


@dataclass(frozen=True, slots=True)
class ActionParameterResolution:
    values: Mapping[str, object]
    configs: Mapping[str, ResolvedActionParameterConfig]


@dataclass(frozen=True, slots=True)
class ActionParameterContext:
    submitted: Mapping[str, object]
    object_properties: Mapping[str, object]
    actor_user_id: str
    actor_groups: tuple[str, ...]
    now: datetime
    generate_id: Callable[[str], str]
    actor_attributes: Mapping[str, object] = field(default_factory=dict[str, object])


def resolve_action_parameters(
    contract: ActionDefinitionV3,
    context: ActionParameterContext,
) -> ActionParameterResolution:
    """Resolve overrides/defaults in declaration order and validate values."""
    declared = {parameter.api_name for parameter in contract.parameters}
    unexpected = sorted(set(context.submitted) - declared)
    if unexpected:
        raise ValidationFailed("unexpected action parameters", details={"unexpected": unexpected})
    values: dict[str, object] = {}
    configs: dict[str, ResolvedActionParameterConfig] = {}
    for parameter in contract.parameters:
        config = _effective_config(parameter, values, context)
        value, is_present = _parameter_value(parameter, config, values, context)
        _validate_parameter_presence(parameter, config, is_present)
        if is_present:
            _validate_parameter_value(parameter, value, config.constraints)
            values[parameter.api_name] = value
        configs[parameter.api_name] = config
    return ActionParameterResolution(values=values, configs=configs)


def default_action_parameter_context(
    submitted: Mapping[str, object],
    object_properties: Mapping[str, object],
    actor_user_id: str,
    actor_groups: tuple[str, ...],
    actor_attributes: Mapping[str, object],
    generate_id: Callable[[str], str],
) -> ActionParameterContext:
    return ActionParameterContext(
        submitted=submitted,
        object_properties=object_properties,
        actor_user_id=actor_user_id,
        actor_groups=actor_groups,
        actor_attributes=actor_attributes,
        now=datetime.now(UTC),
        generate_id=generate_id,
    )


def parameter_config_payload(config: ResolvedActionParameterConfig) -> dict[str, object]:
    return {
        "required": config.required,
        "visible": config.is_visible,
        "editable": config.is_editable,
        "constraints": dict(config.constraints),
        "default": dict(config.default) if config.default else None,
        "matchedOverride": config.matched_override,
    }


def _effective_config(
    parameter: ActionParameterV3,
    values: Mapping[str, object],
    context: ActionParameterContext,
) -> ResolvedActionParameterConfig:
    base: dict[str, object] = {
        "required": parameter.required,
        "visible": True,
        "editable": True,
        "constraints": dict(parameter.constraints),
        "default": parameter.default,
    }
    matched: int | None = None
    condition_context = _condition_context(values, context)
    for index, override in enumerate(parameter.overrides):
        if evaluate_action_condition(override.when, condition_context):
            base = _merge_config(base, override.config)
            matched = index
            break
    return _resolved_config(base, matched)


def _condition_context(values: Mapping[str, object], context: ActionParameterContext) -> StaticActionConditionContext:
    return StaticActionConditionContext(
        parameters=values,
        object_properties=context.object_properties,
        actor_user_id=context.actor_user_id,
        actor_groups=context.actor_groups,
        actor_attributes=context.actor_attributes,
    )


def _merge_config(base: Mapping[str, object], override: Mapping[str, object]) -> dict[str, object]:
    allowed = {"required", "visible", "editable", "constraints", "default"}
    unexpected = sorted(set(override) - allowed)
    if unexpected:
        raise ValidationFailed("unsupported parameter override config", details={"fields": unexpected})
    result = dict(base)
    result.update(override)
    return result


def _resolved_config(raw: Mapping[str, object], matched: int | None) -> ResolvedActionParameterConfig:
    return ResolvedActionParameterConfig(
        required=_bool_config(raw, "required"),
        is_visible=_bool_config(raw, "visible"),
        is_editable=_bool_config(raw, "editable"),
        constraints=_mapping_config(raw.get("constraints"), "constraints"),
        default=_optional_mapping_config(raw.get("default"), "default"),
        matched_override=matched,
    )


def _parameter_value(
    parameter: ActionParameterV3,
    config: ResolvedActionParameterConfig,
    values: Mapping[str, object],
    context: ActionParameterContext,
) -> tuple[object, bool]:
    if parameter.api_name in context.submitted:
        if not config.is_editable:
            raise ValidationFailed(
                "action parameter is not editable",
                details={"parameter": parameter.api_name, "invalid": [parameter.api_name]},
            )
        return context.submitted[parameter.api_name], True
    if config.default is None:
        return None, False
    return _resolve_default(config.default, values, context), True


def _resolve_default(
    default: Mapping[str, object], values: Mapping[str, object], context: ActionParameterContext
) -> object:
    kind = default.get("kind")
    if kind == "literal":
        return default.get("value")
    if kind == "parameter":
        return values.get(_required_text(default, "parameter"))
    if kind == "objectProperty":
        return context.object_properties.get(_required_text(default, "property"))
    if kind == "currentUser":
        return _current_user_default(default, context)
    if kind == "currentTime":
        return _current_time_default(default, context)
    if kind == "generatedId":
        return context.generate_id(_required_text(default, "strategy"))
    raise ValidationFailed("unsupported action parameter default", details={"kind": kind})


def _current_user_default(default: Mapping[str, object], context: ActionParameterContext) -> object:
    attribute = default.get("attribute")
    if attribute in (None, "id"):
        return context.actor_user_id
    if attribute in {"group", "groups", "roles"}:
        return list(context.actor_groups)
    if isinstance(attribute, str) and attribute.strip():
        return context.actor_attributes.get(attribute)
    raise ValidationFailed("unsupported current-user default attribute", details={"attribute": attribute})


def _current_time_default(default: Mapping[str, object], context: ActionParameterContext) -> str:
    unit = default.get("unit")
    if unit == "date":
        return context.now.date().isoformat()
    if unit == "timestamp":
        return context.now.isoformat().replace("+00:00", "Z")
    raise ValidationFailed("unsupported current-time default unit", details={"unit": unit})


def _validate_parameter_presence(
    parameter: ActionParameterV3, config: ResolvedActionParameterConfig, is_present: bool
) -> None:
    if config.required and not is_present:
        raise ValidationFailed("missing required action parameters", details={"missing": [parameter.api_name]})


def _validate_parameter_value(parameter: ActionParameterV3, value: object, constraints: Mapping[str, object]) -> None:
    if not _matches_parameter_type(parameter, value):
        raise ValidationFailed(
            "invalid action parameter types", details={"invalid": [parameter.api_name], "type": parameter.data_type}
        )
    _validate_constraints(parameter.api_name, value, constraints)


def _matches_parameter_type(parameter: ActionParameterV3, value: object) -> bool:
    data_type = parameter.data_type
    if data_type in {"string", "integer", "long", "float", "decimal", "boolean"}:
        return _matches_scalar_type(data_type, value)
    if data_type in {"date", "timestamp"}:
        return _is_temporal(value, data_type)
    if data_type in {"object", "interface"}:
        return _matches_object_reference(value)
    if data_type in {"media", "attachment"}:
        return _matches_media_reference(value, data_type)
    if data_type in {"objectSet", "array"}:
        return _matches_array_type(parameter, value)
    return _matches_struct_type(parameter, value)


def _matches_object_reference(value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, Mapping):
        return False
    reference = cast(Mapping[object, object], value)
    object_type = reference.get("objectType")
    object_id = reference.get("objectId")
    return isinstance(object_type, str) and bool(object_type) and isinstance(object_id, str) and bool(object_id)


def _matches_array_type(parameter: ActionParameterV3, value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return False
    items = cast(Sequence[object], value)
    item_type = parameter.metadata.get("itemType")
    if not isinstance(item_type, str) or not item_type:
        return False
    metadata = {key: value for key, value in parameter.metadata.items() if key != "itemType"}
    synthetic = ActionParameterV3("item", item_type, False, None, None, {}, metadata, ())
    if not all(_matches_parameter_type(synthetic, item) for item in items):
        return False
    return parameter.data_type != "objectSet" or _has_unique_values(items)


def _matches_struct_type(parameter: ActionParameterV3, value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    values = cast(Mapping[object, object], value)
    fields = _struct_parameters(parameter)
    if fields is None:
        return False
    if set(values) - {field.api_name for field in fields}:
        return False
    return all(_matches_struct_field(field, values) for field in fields)


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
    if reference.get("referenceKind") != reference_kind:
        return False
    if not all(isinstance(reference.get(key), str) and reference.get(key) for key in required_text):
        return False
    byte_size = reference.get("byteSize")
    return isinstance(byte_size, int) and not isinstance(byte_size, bool) and byte_size >= 0


def _matches_struct_field(field: ActionParameterV3, value: Mapping[object, object]) -> bool:
    if field.api_name not in value:
        return not field.required
    field_value = value[field.api_name]
    return _matches_parameter_type(field, field_value) and _constraints_match(field, field_value)


def _struct_parameters(parameter: ActionParameterV3) -> tuple[ActionParameterV3, ...] | None:
    raw_fields = parameter.metadata.get("fields")
    if not isinstance(raw_fields, Sequence) or isinstance(raw_fields, str | bytes) or not raw_fields:
        return None
    result: list[ActionParameterV3] = []
    for raw in cast(Sequence[object], raw_fields):
        if not isinstance(raw, Mapping):
            return None
        payload = cast(Mapping[str, object], raw)
        data_type = payload.get("type")
        api_name = payload.get("apiName")
        if not isinstance(data_type, str) or not isinstance(api_name, str):
            return None
        result.append(_struct_parameter(payload, api_name, data_type))
    return tuple(result)


def _struct_parameter(payload: Mapping[str, object], api_name: str, data_type: str) -> ActionParameterV3:
    metadata = {
        key: value for key, value in payload.items() if key not in {"apiName", "type", "required", "constraints"}
    }
    constraints = payload.get("constraints")
    return ActionParameterV3(
        api_name,
        data_type,
        payload.get("required") is True,
        None,
        None,
        cast(Mapping[str, object], constraints) if isinstance(constraints, Mapping) else {},
        metadata,
        (),
    )


def _constraints_match(parameter: ActionParameterV3, value: object) -> bool:
    try:
        _validate_constraints(parameter.api_name, value, parameter.constraints)
    except ValidationFailed:
        return False
    return True


def _has_unique_values(values: Sequence[object]) -> bool:
    return all(value not in values[:index] for index, value in enumerate(values))


def _matches_scalar_type(data_type: str, value: object) -> bool:
    if data_type == "string":
        return isinstance(value, str)
    if data_type in {"integer", "long"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if data_type == "decimal":
        return _is_decimal(value)
    return isinstance(value, bool)


def _is_decimal(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return isinstance(value, int | float | str | Decimal)


def _is_temporal(value: object, data_type: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        if data_type == "date":
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_constraints(name: str, value: object, constraints: Mapping[str, object]) -> None:
    if "enum" in constraints and value not in _sequence_config(constraints["enum"], "enum"):
        raise _constraint_error(name, "enum")
    if isinstance(value, str):
        _validate_string_constraints(name, value, constraints)
    if isinstance(value, int | float) and not isinstance(value, bool):
        _validate_numeric_constraints(name, float(value), constraints)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        _validate_array_constraints(name, cast(Sequence[object], value), constraints)


def _validate_string_constraints(name: str, value: str, constraints: Mapping[str, object]) -> None:
    minimum = constraints.get("minLength")
    maximum = constraints.get("maxLength")
    if isinstance(minimum, int) and len(value) < minimum:
        raise _constraint_error(name, "minLength")
    if isinstance(maximum, int) and len(value) > maximum:
        raise _constraint_error(name, "maxLength")


def _validate_numeric_constraints(name: str, value: float, constraints: Mapping[str, object]) -> None:
    minimum = constraints.get("minimum")
    maximum = constraints.get("maximum")
    if isinstance(minimum, int | float) and value < float(minimum):
        raise _constraint_error(name, "minimum")
    if isinstance(maximum, int | float) and value > float(maximum):
        raise _constraint_error(name, "maximum")


def _validate_array_constraints(name: str, value: Sequence[object], constraints: Mapping[str, object]) -> None:
    minimum = constraints.get("minItems")
    maximum = constraints.get("maxItems")
    if isinstance(minimum, int) and len(value) < minimum:
        raise _constraint_error(name, "minItems")
    if isinstance(maximum, int) and len(value) > maximum:
        raise _constraint_error(name, "maxItems")


def _constraint_error(name: str, constraint: str) -> ValidationFailed:
    return ValidationFailed("action parameter constraint failed", details={"invalid": [name], "constraint": constraint})


def _bool_config(raw: Mapping[str, object], field: str) -> bool:
    value = raw.get(field)
    if not isinstance(value, bool):
        raise ValidationFailed("parameter config must be boolean", details={"field": field})
    return value


def _mapping_config(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed("parameter config must be an object", details={"field": field})
    return cast(Mapping[str, object], raw)


def _optional_mapping_config(raw: object, field: str) -> Mapping[str, object] | None:
    if raw is None:
        return None
    return _mapping_config(raw, field)


def _sequence_config(raw: object, field: str) -> Sequence[object]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed("parameter constraint must be a list", details={"field": field})
    return cast(Sequence[object], raw)


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("parameter default field is required", details={"field": key})
    return value
