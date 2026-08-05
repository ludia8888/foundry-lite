"""Deterministic presentation metadata for Action Contract v3.

The server owns this normalization so Builder, generated SDKs, runtime forms,
and MCP clients cannot silently interpret the same Action definition in
different ways.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.action_runtime.action_conditions import (
    referenced_condition_parameters,
    referenced_condition_value_kinds,
    validate_action_condition,
)
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class ActionFormSectionV3:
    """Canonical no-code form section shared by Builder, SDK, UI, and MCP."""

    section_id: str
    title: str
    description: str | None
    columns: int
    is_collapsible: bool
    is_initially_collapsed: bool
    parameter_names: tuple[str, ...]
    visible_when: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class ActionFormLayoutV3:
    """Ordered canonical form layout for an Action contract."""

    sections: tuple[ActionFormSectionV3, ...]


def compile_action_form_layout(raw: object, parameter_names: tuple[str, ...]) -> ActionFormLayoutV3:
    """Validate explicit sections and append a deterministic fallback section."""
    if raw is None:
        return ActionFormLayoutV3(sections=(_default_section("parameters", "Parameters", parameter_names),))
    payload = _mapping(raw, "formLayout")
    sections = tuple(_section(item) for item in _sequence(payload.get("sections", ()), "formLayout.sections"))
    _validate_sections(sections, parameter_names)
    assigned = {name for section in sections for name in section.parameter_names}
    unassigned = tuple(name for name in parameter_names if name not in assigned)
    if unassigned:
        sections += (_default_section("other-parameters", "Other parameters", unassigned),)
    if not sections:
        sections = (_default_section("parameters", "Parameters", ()),)
    return ActionFormLayoutV3(sections=sections)


def action_form_layout_payload(layout: ActionFormLayoutV3) -> dict[str, object]:
    """Serialize a form layout into its deterministic public shape."""
    return {"sections": [_section_payload(section) for section in layout.sections]}


def action_inline_eligibility(
    *,
    target_kind: str,
    target_api_name: str,
    parameter_types: Mapping[str, str],
    rules: tuple[Mapping[str, object], ...],
    has_function: bool,
    effect_count: int,
) -> dict[str, object]:
    """Explain whether an Action is safe to expose as an inline object edit."""
    reasons: list[str] = []
    if target_kind != "object":
        reasons.append("interface targets require concrete-type resolution")
    if has_function:
        reasons.append("function-backed Actions require the full execution runtime")
    if effect_count:
        reasons.append("Actions with side effects require the full execution runtime")
    binding, rule_reasons = _inline_rule_binding(target_api_name, parameter_types, rules)
    reasons.extend(rule_reasons)
    payload: dict[str, object] = {"isEligible": not reasons, "reasons": reasons}
    if not reasons and binding is not None:
        payload.update(binding)
    return payload


def _section(raw: object) -> ActionFormSectionV3:
    """Compile and validate one form section declaration."""
    payload = _mapping(raw, "form section")
    is_collapsible = _optional_bool(payload.get("isCollapsible"), False, "isCollapsible")
    is_collapsed = _optional_bool(payload.get("isInitiallyCollapsed"), False, "isInitiallyCollapsed")
    if is_collapsed and not is_collapsible:
        raise ValidationFailed("initially collapsed form section must be collapsible")
    columns = payload.get("columns", 1)
    if not isinstance(columns, int) or isinstance(columns, bool) or columns not in {1, 2}:
        raise ValidationFailed("form section columns must be 1 or 2")
    visible_when = _optional_mapping(payload.get("visibleWhen"), "visibleWhen")
    if visible_when is not None:
        validate_action_condition(visible_when)
        _validate_section_condition_sources(visible_when)
    return ActionFormSectionV3(
        section_id=_required_text(payload, "id"),
        title=_required_text(payload, "title"),
        description=_optional_text(payload.get("description")),
        columns=columns,
        is_collapsible=is_collapsible,
        is_initially_collapsed=is_collapsed,
        parameter_names=_text_sequence(payload.get("parameterNames", ()), "parameterNames"),
        visible_when=visible_when,
    )


def _validate_sections(sections: tuple[ActionFormSectionV3, ...], parameter_names: tuple[str, ...]) -> None:
    """Apply cross-section identity and parameter-reference invariants."""
    _validate_unique_section_ids(sections)
    assigned = _assigned_parameter_names(sections)
    _validate_unique_parameter_assignments(assigned)
    _validate_known_parameter_assignments(assigned, parameter_names)
    _validate_section_condition_references(sections, parameter_names)


def _validate_unique_section_ids(sections: tuple[ActionFormSectionV3, ...]) -> None:
    """Reject duplicate form section identifiers."""
    section_ids = [section.section_id for section in sections]
    if len(section_ids) != len(set(section_ids)):
        raise ValidationFailed("form section ids must be unique", details={"sectionIds": section_ids})


def _assigned_parameter_names(sections: tuple[ActionFormSectionV3, ...]) -> list[str]:
    """Flatten parameter placement in declaration order."""
    return [name for section in sections for name in section.parameter_names]


def _validate_unique_parameter_assignments(assigned: list[str]) -> None:
    """Ensure each Action parameter appears in at most one section."""
    if len(assigned) != len(set(assigned)):
        raise ValidationFailed("action parameter may appear in only one form section", details={"parameters": assigned})


def _validate_known_parameter_assignments(assigned: list[str], parameter_names: tuple[str, ...]) -> None:
    """Reject form placements for undeclared Action parameters."""
    unknown = sorted(set(assigned) - set(parameter_names))
    if unknown:
        raise ValidationFailed("form section references unknown action parameters", details={"parameters": unknown})


def _validate_section_condition_references(
    sections: tuple[ActionFormSectionV3, ...], parameter_names: tuple[str, ...]
) -> None:
    """Reject visibility expressions that reference unknown parameters."""
    invalid_condition_refs = sorted(
        {
            reference
            for section in sections
            if section.visible_when is not None
            for reference in referenced_condition_parameters(section.visible_when)
            if reference not in parameter_names
        }
    )
    if invalid_condition_refs:
        raise ValidationFailed(
            "form section condition references unknown action parameters",
            details={"parameters": invalid_condition_refs},
        )


def _section_payload(section: ActionFormSectionV3) -> dict[str, object]:
    """Serialize one canonical form section."""
    return {
        "id": section.section_id,
        "title": section.title,
        "description": section.description,
        "columns": section.columns,
        "isCollapsible": section.is_collapsible,
        "isInitiallyCollapsed": section.is_initially_collapsed,
        "parameterNames": list(section.parameter_names),
        "visibleWhen": dict(section.visible_when) if section.visible_when is not None else None,
    }


def _default_section(section_id: str, title: str, parameter_names: tuple[str, ...]) -> ActionFormSectionV3:
    """Build a deterministic non-collapsible fallback section."""
    return ActionFormSectionV3(section_id, title, None, 1, False, False, parameter_names, None)


def _validate_section_condition_sources(condition: Mapping[str, object]) -> None:
    """Limit form visibility to non-sensitive literal and parameter inputs."""
    invalid = sorted(referenced_condition_value_kinds(condition) - {"literal", "parameter"})
    if invalid:
        raise ValidationFailed(
            "form section visibility may use parameter and literal values only",
            details={"invalidValueKinds": invalid},
        )


def _inline_rule_binding(
    target_api_name: str,
    parameter_types: Mapping[str, str],
    rules: tuple[Mapping[str, object], ...],
) -> tuple[dict[str, object] | None, list[str]]:
    """Resolve an eligible single-property rule into an inline binding."""
    if len(rules) != 1 or rules[0].get("kind") != "modifyObject":
        return None, ["inline edit requires exactly one modifyObject rule"]
    rule = rules[0]
    reasons = _inline_target_reasons(rule, target_api_name)
    assignment = _single_inline_assignment(rule, reasons)
    binding = _inline_assignment_binding(assignment, parameter_types, reasons)
    return binding, reasons


def _inline_target_reasons(rule: Mapping[str, object], target_api_name: str) -> list[str]:
    """Explain target-shape conditions that make inline editing unsafe."""
    reasons: list[str] = []
    if rule.get("objectType") != target_api_name:
        reasons.append("inline edit rule must modify the declared Action target")
    target = rule.get("target")
    target_payload: Mapping[object, object] = (
        cast(Mapping[object, object], target) if isinstance(target, Mapping) else {}
    )
    if target_payload.get("kind") != "parameter" or target_payload.get("parameter") != "__target__":
        reasons.append("inline edit rule must target the selected object")
    if rule.get("cardinality", "one") != "one":
        reasons.append("inline edit cannot target an object set")
    if rule.get("shouldCreateIfAbsent", False) is not False:
        reasons.append("inline edit cannot create a missing object")
    return reasons


def _single_inline_assignment(
    rule: Mapping[str, object],
    reasons: list[str],
) -> Mapping[str, object] | None:
    """Return the sole assignment or append a precise rejection reason."""
    raw = rule.get("assignments")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        reasons.append("inline edit requires exactly one property assignment")
        return None
    assignments = cast(Sequence[object], raw)
    if len(assignments) != 1:
        reasons.append("inline edit requires exactly one property assignment")
        return None
    assignment = assignments[0]
    if not isinstance(assignment, Mapping):
        reasons.append("inline edit property assignment is invalid")
        return None
    return cast(Mapping[str, object], assignment)


def _inline_assignment_binding(
    assignment: Mapping[str, object] | None,
    parameter_types: Mapping[str, str],
    reasons: list[str],
) -> dict[str, object] | None:
    """Validate parameter cardinality before compiling an inline binding."""
    if len(parameter_types) != 1:
        reasons.append("inline edit requires exactly one Action parameter")
    if assignment is None:
        return None
    return _inline_binding_from_assignment(assignment, parameter_types, reasons)


def _inline_binding_from_assignment(
    assignment: Mapping[str, object],
    parameter_types: Mapping[str, str],
    reasons: list[str],
) -> dict[str, object] | None:
    """Compile one property-to-parameter inline binding."""
    property_name = assignment.get("property")
    if not isinstance(property_name, str) or not property_name:
        reasons.append("inline edit assignment requires one target property")
    parameter_name = _inline_parameter_name(assignment.get("value"), parameter_types, reasons)
    if parameter_name is None:
        return None
    parameter_type = parameter_types[parameter_name]
    _require_inline_primitive_type(parameter_type, reasons)
    if reasons or not isinstance(property_name, str):
        return None
    return {
        "propertyApiName": property_name,
        "parameterApiName": parameter_name,
        "parameterType": parameter_type,
    }


def _inline_parameter_name(
    value: object,
    parameter_types: Mapping[str, str],
    reasons: list[str],
) -> str | None:
    """Resolve a declared parameter reference from an assignment value."""
    value_payload: Mapping[object, object] = cast(Mapping[object, object], value) if isinstance(value, Mapping) else {}
    if value_payload.get("kind") != "parameter":
        reasons.append("inline edit assignment value must come from its Action parameter")
        return None
    parameter_name = value_payload.get("parameter")
    if not isinstance(parameter_name, str) or parameter_name not in parameter_types:
        reasons.append("inline edit assignment references an undeclared Action parameter")
        return None
    return parameter_name


def _require_inline_primitive_type(parameter_type: str, reasons: list[str]) -> None:
    """Restrict inline cells to primitive values with deterministic editors."""
    supported = {"string", "boolean", "integer", "long", "float", "decimal", "date", "timestamp"}
    if parameter_type not in supported:
        reasons.append("inline edit supports primitive Action parameters only")


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    """Require a mapping-shaped presentation field."""
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"{field} must be an object")
    return cast(Mapping[str, object], raw)


def _optional_mapping(raw: object, field: str) -> Mapping[str, object] | None:
    """Normalize an optional mapping-shaped presentation field."""
    if raw is None:
        return None
    return _mapping(raw, field)


def _sequence(raw: object, field: str) -> tuple[object, ...]:
    """Require a list-like presentation field without accepting text."""
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed(f"{field} must be a list")
    return tuple(cast(Sequence[object], raw))


def _text_sequence(raw: object, field: str) -> tuple[str, ...]:
    """Require a non-empty string sequence for parameter placement."""
    values = _sequence(raw, field)
    if not all(isinstance(value, str) and value for value in values):
        raise ValidationFailed(f"{field} must contain non-empty strings")
    return cast(tuple[str, ...], values)


def _required_text(payload: Mapping[str, object], field: str) -> str:
    """Require a non-empty text field in presentation metadata."""
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("action form field is required", details={"field": field})
    return value


def _optional_text(raw: object) -> str | None:
    """Normalize optional presentation text while rejecting other types."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValidationFailed("action form field must be text")
    return raw or None


def _optional_bool(raw: object, is_default: bool, field: str) -> bool:
    """Normalize an optional boolean presentation flag."""
    if raw is None:
        return is_default
    if not isinstance(raw, bool):
        raise ValidationFailed("action form field must be boolean", details={"field": field})
    return raw
