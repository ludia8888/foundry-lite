"""Application-layer models and helpers for safe expression."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from foundry_lite.domain.action_runtime.action_conditions import (
    StaticActionConditionContext,
    evaluate_action_condition,
)
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.action_parameters import (
    ActionParameterResolution,
    default_action_parameter_context,
    resolve_action_parameters,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

OBJECT_IN_PATTERN = re.compile(r"^object\.([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\[(.*)]$")
OBJECT_EQ_PATTERN = re.compile(r"^object\.([A-Za-z_][A-Za-z0-9_]*)\s*==\s*'([^']*)'$")


def precondition_expression(precondition: Mapping[str, object]) -> str:
    # Use explicit None checks so an empty-string safeExpression does not
    # silently fall back to ``expression`` or ``cel`` — root-cause hardening
    # surfaced by hypothesis P5 property tests.
    if "safeExpression" in precondition:
        return _string_or_empty(precondition["safeExpression"])
    if "expression" in precondition:
        return _string_or_empty(precondition["expression"])
    return _string_or_empty(precondition.get("cel", ""))


def evaluate_safe_expression(expression: str, properties: Mapping[str, object]) -> bool:
    match = OBJECT_IN_PATTERN.match(expression)
    if match:
        prop = match.group(1)
        return _comparable_value(properties.get(prop)) in _in_list_values(match.group(2), expression)
    match = OBJECT_EQ_PATTERN.match(expression)
    if match:
        return _comparable_value(properties.get(match.group(1))) == match.group(2)
    raise ValidationFailed("unsupported safe expression", details={"expression": expression})


def _comparable_value(value: object) -> object:
    """Coerce a property value for comparison against a string literal.

    Expression literals are always quoted strings, so a numeric or boolean
    property value would never equal (or appear in) them and the precondition
    could only ever be satisfied by string properties. Stringify non-string
    scalars consistently (booleans as ``true``/``false``, numbers as their text)
    so numeric and boolean guards evaluate as authored. ``None`` is left unchanged
    so a missing property never matches a string literal.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return value


def _in_list_values(raw_values: str, expression: str) -> tuple[str, ...]:
    """Split an ``in [...]`` member list without breaking quoted literals.

    Splitting on every comma before stripping quotes made a quoted member that
    contains a comma parse as several members: ``in ['a,b', 'c']`` admitted the
    values ``a`` and ``b`` while rejecting the member ``a,b`` that was actually
    declared. Because this gates action preconditions, that widened the accepted
    set beyond what the ontology author wrote. The same split turned the empty
    list ``in []`` into a single empty-string member, so a property whose value
    was ``""`` satisfied a precondition that listed nothing.

    Malformed lists raise, matching how an unsupported expression is handled: a
    precondition that cannot be parsed must not silently admit the write.
    """
    values: list[str] = []
    index = 0
    end = len(raw_values)
    while index < end:
        index = _skip_spaces(raw_values, index, end)
        if index >= end:
            break
        if raw_values[index] in "'\"":
            value, index = _quoted_member(raw_values, index, end, expression)
        else:
            value, index = _bare_member(raw_values, index, end, expression)
        values.append(value)
    return tuple(values)


def _skip_spaces(raw_values: str, index: int, end: int) -> int:
    while index < end and raw_values[index].isspace():
        index += 1
    return index


def _quoted_member(raw_values: str, index: int, end: int, expression: str) -> tuple[str, int]:
    """Read one quoted member and consume the separator that must follow it."""
    quote = raw_values[index]
    closing = raw_values.find(quote, index + 1)
    if closing == -1:
        raise ValidationFailed("unterminated safe expression literal", details={"expression": expression})
    value = raw_values[index + 1 : closing]
    index = _skip_spaces(raw_values, closing + 1, end)
    if index < end:
        if raw_values[index] != ",":
            raise ValidationFailed("malformed safe expression list", details={"expression": expression})
        index += 1
    return value, index


def _bare_member(raw_values: str, index: int, end: int, expression: str) -> tuple[str, int]:
    """Read one unquoted member up to the next separator, rejecting stray quotes."""
    separator = raw_values.find(",", index)
    token = raw_values[index:] if separator == -1 else raw_values[index:separator]
    next_index = end if separator == -1 else separator + 1
    if "'" in token or '"' in token:
        raise ValidationFailed("malformed safe expression list", details={"expression": expression})
    return token.strip(), next_index


def validate_action_request(
    action_type: Mapping[str, object],
    record: Mapping[str, object],
    params: Mapping[str, object],
    ctx: RequestContext | None = None,
    generate_id: Callable[[str], str] | None = None,
) -> Exception | None:
    resolution, resolution_error = _resolve_action_parameters_or_error(action_type, record, params, ctx, generate_id)
    if resolution_error is not None:
        return resolution_error
    effective_params = resolution.values if resolution is not None else params
    return _validated_action_request_error(action_type, record, effective_params, ctx)


def _resolve_action_parameters_or_error(
    action_type: Mapping[str, object],
    record: Mapping[str, object],
    params: Mapping[str, object],
    ctx: RequestContext | None,
    generate_id: Callable[[str], str] | None,
) -> tuple[ActionParameterResolution | None, ValidationFailed | None]:
    try:
        resolution = resolve_action_request_parameters(action_type, record, params, ctx, generate_id=generate_id)
    except ValidationFailed as exc:
        return None, exc
    return resolution, None


def _validated_action_request_error(
    action_type: Mapping[str, object],
    record: Mapping[str, object],
    effective_params: Mapping[str, object],
    ctx: RequestContext | None,
) -> Exception | None:
    criteria_error = _submission_criteria_error(action_type, record, effective_params, ctx)
    if criteria_error is not None:
        return criteria_error
    schema_error = _parameter_schema_error(action_type, effective_params)
    if schema_error is not None:
        return schema_error
    return _legacy_precondition_error(action_type, record)


def _parameter_schema_error(
    action_type: Mapping[str, object], effective_params: Mapping[str, object]
) -> Exception | None:
    schema = _mapping_or_empty(action_type.get("parameter_schema"))
    required = _string_sequence(schema.get("required", ()))
    missing = [name for name in required if name not in effective_params]
    if missing:
        return ValidationFailed("missing required action parameters", details={"missing": missing})
    properties = schema.get("properties")
    if properties is not None:
        # A declared parameter schema rejects unknown parameters and enforces the
        # declared type, so a stray or wrong-typed value never reaches the patch.
        type_error = _validate_parameter_values(_mapping_or_empty(properties), effective_params)
        if type_error is not None:
            return type_error
    return None


def _legacy_precondition_error(action_type: Mapping[str, object], record: Mapping[str, object]) -> Exception | None:
    definition = _mapping_or_empty(action_type.get("definition"))
    for raw_precondition in _object_sequence(definition.get("preconditions", ())):
        precondition = _mapping_or_empty(raw_precondition)
        expression = precondition_expression(precondition)
        if not evaluate_safe_expression(expression, _mapping_or_empty(record.get("properties"))):
            return ValidationFailed(
                _string_or_empty(precondition.get("message")) or "action precondition failed",
                details={"expression": expression},
            )
    return None


def resolve_action_request_parameters(
    action_type: Mapping[str, object],
    record: Mapping[str, object],
    params: Mapping[str, object],
    ctx: RequestContext | None = None,
    *,
    generate_id: Callable[[str], str] | None = None,
) -> ActionParameterResolution | None:
    """Resolve canonical v3 parameters; return None for non-Action schemas."""
    definition = _mapping_or_empty(action_type.get("definition"))
    if not _is_action_definition(definition):
        return None
    request_context = ctx or RequestContext()
    contract = compile_action_contract(definition)
    resolver_context = default_action_parameter_context(
        params,
        _mapping_or_empty(record.get("properties")),
        request_context.actor_user_id,
        request_context.roles,
        generate_id or (lambda strategy: f"preview-{strategy}"),
    )
    return resolve_action_parameters(contract, resolver_context)


def _submission_criteria_error(
    action_type: Mapping[str, object],
    record: Mapping[str, object],
    params: Mapping[str, object],
    ctx: RequestContext | None,
) -> ValidationFailed | None:
    definition = _mapping_or_empty(action_type.get("definition"))
    raw = definition.get("submissionCriteria")
    if not isinstance(raw, Mapping):
        return None
    request_context = ctx or RequestContext()
    condition_context = StaticActionConditionContext(
        parameters=params,
        object_properties=_mapping_or_empty(record.get("properties")),
        actor_user_id=request_context.actor_user_id,
        actor_groups=request_context.roles,
    )
    if evaluate_action_condition(raw, condition_context):
        return None
    message = raw.get("message")
    return ValidationFailed(
        message if isinstance(message, str) and message else "action submission criteria failed",
        details={"criteria": dict(raw)},
    )


def _is_action_definition(definition: Mapping[str, object]) -> bool:
    return isinstance(definition.get("apiName"), str) and definition.get("target") is not None


def _validate_parameter_values(properties: Mapping[str, object], params: Mapping[str, object]) -> Exception | None:
    unexpected = sorted(name for name in params if name not in properties)
    if unexpected:
        return ValidationFailed("unexpected action parameters", details={"unexpected": unexpected})
    invalid = sorted(
        name
        for name, value in params.items()
        if not _value_matches_type(value, _string_or_empty(_mapping_or_empty(properties.get(name)).get("type")))
    )
    if invalid:
        return ValidationFailed("invalid action parameter types", details={"invalid": invalid})
    return None


def _value_matches_type(value: object, declared_type: str) -> bool:
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if declared_type == "boolean":
        return isinstance(value, bool)
    # Unknown/unspecified declared types stay permissive (e.g. future date types).
    return True


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _object_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        return value
    return ()


def _string_sequence(value: object) -> tuple[str, ...]:
    return tuple(item for item in _object_sequence(value) if isinstance(item, str))
