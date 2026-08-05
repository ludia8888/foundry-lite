"""Redacted, deterministic explanations for Action condition decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.action_runtime.action_conditions import (
    ActionConditionContext,
    evaluate_action_condition,
)


def explain_action_condition(
    condition: Mapping[str, object],
    context: ActionConditionContext,
    *,
    path: str = "root",
) -> dict[str, object]:
    """Return a value-redacted evaluation tree suitable for UI and agents."""
    if "all" in condition:
        return _group_explanation(condition, context, path, "all")
    if "any" in condition:
        return _group_explanation(condition, context, path, "any")
    if "not" in condition:
        child = explain_action_condition(_mapping(condition["not"]), context, path=f"{path}.not")
        return _node_payload(condition, path, "not", not bool(child["isSatisfied"]), children=[child])
    return _comparison_explanation(condition, context, path)


def _group_explanation(
    condition: Mapping[str, object],
    context: ActionConditionContext,
    path: str,
    kind: str,
) -> dict[str, object]:
    children = [
        explain_action_condition(child, context, path=f"{path}.{kind}[{index}]")
        for index, child in enumerate(_condition_list(condition[kind]))
    ]
    results = [bool(child["isSatisfied"]) for child in children]
    is_satisfied = all(results) if kind == "all" else any(results)
    return _node_payload(condition, path, kind, is_satisfied, children=children)


def _comparison_explanation(
    condition: Mapping[str, object],
    context: ActionConditionContext,
    path: str,
) -> dict[str, object]:
    operator = str(condition.get("op"))
    payload = _node_payload(condition, path, "comparison", evaluate_action_condition(condition, context))
    payload["operator"] = operator
    payload["left"] = _source_payload(condition.get("left"))
    if operator != "exists":
        payload["right"] = _source_payload(condition.get("right"))
    return payload


def _node_payload(
    condition: Mapping[str, object],
    path: str,
    kind: str,
    is_satisfied: bool,
    *,
    children: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"path": path, "kind": kind, "isSatisfied": is_satisfied}
    message = condition.get("message")
    if isinstance(message, str) and message:
        payload["message"] = message
    if children is not None:
        payload["children"] = children
    return payload


def _source_payload(raw: object) -> dict[str, object]:
    source = _mapping(raw)
    kind = source.get("kind")
    payload: dict[str, object] = {"kind": str(kind)}
    if kind == "parameter":
        payload["reference"] = source.get("parameter")
    elif kind == "objectProperty":
        payload["reference"] = source.get("property")
    elif kind == "currentUser":
        payload["reference"] = source.get("attribute", "id")
    elif kind == "linkedObjectProperty":
        payload["reference"] = {
            "linkType": source.get("linkType"),
            "direction": source.get("direction", "outgoing"),
            "property": source.get("property"),
            "aggregation": source.get("aggregation", "values"),
        }
    elif kind == "literal":
        payload["literal"] = source.get("value")
    return payload


def _condition_list(raw: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    return tuple(_mapping(item) for item in cast(Sequence[object], raw))


def _mapping(raw: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], raw) if isinstance(raw, Mapping) else {}
