"""Fail-closed Action risk floor and autonomous-agent policy tests."""

from __future__ import annotations

import pytest
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.action_risk import (
    can_agent_execute_autonomously,
    require_declared_risk_floor,
    structural_action_risk,
)
from foundry_lite.domain.errors import ValidationFailed


def test_missing_permission_policy_forces_high_risk() -> None:
    contract = compile_action_contract(_definition(risk="low", permissions=None))
    assessment = structural_action_risk(contract)

    assert assessment.effective_level == "high"
    assert assessment.reasons == ("permissions_missing",)
    with pytest.raises(ValidationFailed, match="below the system-derived minimum"):
        require_declared_risk_floor(assessment)


def test_delete_cannot_be_declared_low_or_run_autonomously() -> None:
    contract = compile_action_contract(
        _definition(
            risk="low",
            permissions={"allowedRoles": ["ops_manager"]},
            rule_kind="deleteObject",
        )
    )
    assessment = structural_action_risk(contract)

    assert assessment.effective_level == "high"
    assert "object_delete" in assessment.reasons
    assert can_agent_execute_autonomously(contract, assessment) is False


def test_one_non_sensitive_modify_with_explicit_policy_can_be_autonomous() -> None:
    contract = compile_action_contract(_definition(risk="low", permissions={"allowedRoles": ["ops_manager"]}))
    assessment = structural_action_risk(contract)

    require_declared_risk_floor(assessment)
    assert assessment.effective_level == "low"
    assert can_agent_execute_autonomously(contract, assessment) is True


def _definition(
    *,
    risk: str,
    permissions: dict[str, object] | None,
    rule_kind: str = "modifyObject",
) -> dict[str, object]:
    rule: dict[str, object] = {
        "kind": rule_kind,
        "ruleId": "edit",
        "objectType": "Order",
        "target": {"kind": "parameter", "parameter": "__target__"},
    }
    if rule_kind == "modifyObject":
        rule["assignments"] = []
    result: dict[str, object] = {
        "contractVersion": 3,
        "apiName": "GovernedOrderEdit",
        "target": "Order",
        "riskLevel": risk,
        "agentExecutionPolicy": "autonomous",
        "rules": [rule],
    }
    if permissions is not None:
        result["permissions"] = permissions
    return result
