"""Typed, eval-free value expressions for Action IR v2 rule values.

Palantir action rules source a property value from one of a fixed set of value
sources (an action parameter, a property of an object-reference parameter, a
prior rule's output, a function output, the authenticated current user or an
attribute/group of theirs, the current timestamp/date, a generated stable id, or
a webhook response field). This module models those sources as an immutable,
pattern-matchable AST so a value is never produced by string interpolation or
``eval`` — the executor resolves each node against an injected context.

The domain layer stays pure: resolution is delegated to a ``ValueResolutionContext``
protocol implemented in the application layer, so the AST carries no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.domain.errors import ValidationFailed

CONTEXTUAL_TIME_UNITS = frozenset({"timestamp", "date"})


@dataclass(frozen=True)
class LiteralValue:
    """A value hardcoded in the rule."""

    value: object


@dataclass(frozen=True)
class ParameterValue:
    """The submitted value of an action parameter."""

    parameter: str


@dataclass(frozen=True)
class ObjectPropertyValue:
    """A property read from an object-reference parameter's target object."""

    parameter: str
    property: str


@dataclass(frozen=True)
class PriorRuleOutputValue:
    """An output (e.g. the created object's primary key) of an earlier rule."""

    rule_id: str
    output: str


@dataclass(frozen=True)
class FunctionOutputValue:
    """A named field of a function rule's result."""

    key: str


@dataclass(frozen=True)
class CurrentUserValue:
    """The submitting user's id, or one of their attributes/groups.

    ``attribute is None`` resolves the user id; otherwise the named attribute
    (e.g. ``group`` or an organization attribute) is resolved.
    """

    attribute: str | None = None


@dataclass(frozen=True)
class CurrentTimeValue:
    """The current timestamp or date at submission."""

    unit: str


@dataclass(frozen=True)
class GeneratedIdValue:
    """A stable generated identifier (e.g. a UUID) for a created object."""

    strategy: str


@dataclass(frozen=True)
class WebhookResponseValue:
    """A field of a before-commit (gating) webhook's response."""

    field: str


ValueExpression = (
    LiteralValue
    | ParameterValue
    | ObjectPropertyValue
    | PriorRuleOutputValue
    | FunctionOutputValue
    | CurrentUserValue
    | CurrentTimeValue
    | GeneratedIdValue
    | WebhookResponseValue
)


@runtime_checkable
class ValueResolutionContext(Protocol):
    """Application-provided resolver for the leaf value sources."""

    def parameter(self, name: str) -> object: ...

    def object_property(self, parameter: str, prop: str) -> object: ...

    def prior_rule_output(self, rule_id: str, output: str) -> object: ...

    def function_output(self, key: str) -> object: ...

    def current_user(self, attribute: str | None) -> object: ...

    def current_time(self, unit: str) -> str: ...

    def generated_id(self, strategy: str) -> str: ...

    def webhook_response(self, field: str) -> object: ...


def evaluate_value(expression: ValueExpression, context: ValueResolutionContext) -> object:
    """Resolve one value expression against the injected context."""
    match expression:
        case LiteralValue(value=value):
            return value
        case ParameterValue(parameter=name):
            return context.parameter(name)
        case ObjectPropertyValue(parameter=name, property=prop):
            return context.object_property(name, prop)
        case PriorRuleOutputValue(rule_id=rule_id, output=output):
            return context.prior_rule_output(rule_id, output)
        case FunctionOutputValue(key=key):
            return context.function_output(key)
        case CurrentUserValue(attribute=attribute):
            return context.current_user(attribute)
        case CurrentTimeValue(unit=unit):
            return context.current_time(unit)
        case GeneratedIdValue(strategy=strategy):
            return context.generated_id(strategy)
        case WebhookResponseValue(field=field):
            return context.webhook_response(field)


def _require_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("value expression field is required", details={"field": key})
    return value


def _parse_literal(payload: Mapping[str, object]) -> ValueExpression:
    return LiteralValue(value=payload.get("value"))


def _parse_parameter(payload: Mapping[str, object]) -> ValueExpression:
    return ParameterValue(parameter=_require_text(payload, "parameter"))


def _parse_object_property(payload: Mapping[str, object]) -> ValueExpression:
    return ObjectPropertyValue(
        parameter=_require_text(payload, "parameter"), property=_require_text(payload, "property")
    )


def _parse_prior_rule_output(payload: Mapping[str, object]) -> ValueExpression:
    return PriorRuleOutputValue(rule_id=_require_text(payload, "ruleId"), output=_require_text(payload, "output"))


def _parse_function_output(payload: Mapping[str, object]) -> ValueExpression:
    return FunctionOutputValue(key=_require_text(payload, "key"))


def _parse_current_user(payload: Mapping[str, object]) -> ValueExpression:
    attribute = payload.get("attribute")
    return CurrentUserValue(attribute=attribute if isinstance(attribute, str) and attribute else None)


def _parse_current_time(payload: Mapping[str, object]) -> ValueExpression:
    unit = _require_text(payload, "unit")
    if unit not in CONTEXTUAL_TIME_UNITS:
        raise ValidationFailed("unsupported current-time unit", details={"unit": unit})
    return CurrentTimeValue(unit=unit)


def _parse_generated_id(payload: Mapping[str, object]) -> ValueExpression:
    return GeneratedIdValue(strategy=_require_text(payload, "strategy"))


def _parse_webhook_response(payload: Mapping[str, object]) -> ValueExpression:
    return WebhookResponseValue(field=_require_text(payload, "field"))


_VALUE_PARSERS = {
    "literal": _parse_literal,
    "parameter": _parse_parameter,
    "objectProperty": _parse_object_property,
    "priorRuleOutput": _parse_prior_rule_output,
    "functionOutput": _parse_function_output,
    "currentUser": _parse_current_user,
    "currentTime": _parse_current_time,
    "generatedId": _parse_generated_id,
    "webhookResponse": _parse_webhook_response,
}


def parse_value_expression(payload: Mapping[str, object]) -> ValueExpression:
    """Parse a serialized value expression, failing closed on an unknown kind.

    The wire shape is ``{"kind": <source>, ...}`` — a closed discriminated union
    so a definition can never smuggle an interpretable expression string.
    """
    parser = _VALUE_PARSERS.get(str(payload.get("kind")))
    if parser is None:
        raise ValidationFailed("unsupported value expression kind", details={"kind": payload.get("kind")})
    return parser(payload)
