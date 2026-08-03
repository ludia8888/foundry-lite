"""Fail-closed risk classification for canonical Action contracts and edit plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.errors import ValidationFailed

_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True, slots=True)
class ActionRiskAssessment:
    declared_level: str
    effective_level: str
    reasons: tuple[str, ...]

    @property
    def is_approval_required_for_agent(self) -> bool:
        return self.effective_level != "low"


def structural_action_risk(contract: ActionDefinitionV3) -> ActionRiskAssessment:
    """Infer the minimum risk visible from an immutable Action definition."""
    reasons: list[str] = []
    minimum = "low"
    if not contract.permissions:
        minimum = _raise_risk(minimum, "high")
        reasons.append("permissions_missing")
    if contract.effects:
        minimum = _raise_risk(minimum, "high")
        reasons.append("external_effect")
    if contract.function is not None:
        minimum = _raise_risk(minimum, "high")
        reasons.append("function_execution_fail_closed")
    for rule in contract.rules:
        minimum = _rule_risk(rule, minimum, reasons)
    return ActionRiskAssessment(contract.risk_level, _raise_risk(contract.risk_level, minimum), tuple(reasons))


def action_plan_risk(
    contract: ActionDefinitionV3,
    plan: EditPlan,
    sensitive_properties: Mapping[str, frozenset[str]],
) -> ActionRiskAssessment:
    """Raise structural risk using the concrete edits resolved for one request."""
    structural = structural_action_risk(contract)
    minimum = structural.effective_level
    reasons = list(structural.reasons)
    edit_count = _edit_count(plan)
    if edit_count > 1:
        minimum = _raise_risk(minimum, "medium")
        reasons.append("multi_edit")
    if plan.objects_to_delete:
        minimum = _raise_risk(minimum, "high")
        reasons.append("object_delete")
    if _touches_sensitive_property(plan, sensitive_properties):
        minimum = _raise_risk(minimum, "high")
        reasons.append("sensitive_property_edit")
    return ActionRiskAssessment(contract.risk_level, minimum, tuple(dict.fromkeys(reasons)))


def require_declared_risk_floor(assessment: ActionRiskAssessment) -> None:
    """Reject definitions that claim less risk than the system can prove."""
    if _RISK_RANK[assessment.declared_level] >= _RISK_RANK[assessment.effective_level]:
        return
    raise ValidationFailed(
        "action risk level is below the system-derived minimum",
        details={
            "declaredRiskLevel": assessment.declared_level,
            "minimumRiskLevel": assessment.effective_level,
            "reasons": list(assessment.reasons),
        },
    )


def can_agent_execute_autonomously(contract: ActionDefinitionV3, assessment: ActionRiskAssessment) -> bool:
    return contract.agent_execution_policy == "autonomous" and assessment.effective_level == "low"


def _rule_risk(rule: Mapping[str, object], current: str, reasons: list[str]) -> str:
    kind = str(rule.get("kind") or "")
    if kind in {"deleteObject", "deleteObjects"}:
        reasons.append("object_delete")
        return _raise_risk(current, "high")
    if kind in {"modifyObjects"}:
        reasons.append("large_batch")
        return _raise_risk(current, "high")
    if kind in {"createObject", "createLink", "deleteLink", "createOrModifyObject"}:
        reasons.append("multi_resource_edit")
        return _raise_risk(current, "medium")
    if kind == "functionEdit":
        reasons.append("function_execution_fail_closed")
        return _raise_risk(current, "high")
    return current


def _touches_sensitive_property(
    plan: EditPlan,
    sensitive_properties: Mapping[str, frozenset[str]],
) -> bool:
    for create in plan.objects_to_create:
        if set(create.properties) & sensitive_properties.get(create.object_type, frozenset()):
            return True
    for modify in plan.objects_to_modify:
        if set(modify.patch) & sensitive_properties.get(modify.object_type, frozenset()):
            return True
    return bool(plan.objects_to_delete and any(sensitive_properties.values()))


def _edit_count(plan: EditPlan) -> int:
    return sum(
        len(items)
        for items in (
            plan.objects_to_create,
            plan.objects_to_modify,
            plan.objects_to_delete,
            plan.links_to_create,
            plan.links_to_delete,
        )
    )


def _raise_risk(current: str, minimum: str) -> str:
    return current if _RISK_RANK[current] >= _RISK_RANK[minimum] else minimum
