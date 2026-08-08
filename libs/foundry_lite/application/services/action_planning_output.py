"""Immutable response assembly for canonical Action execution plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.services.action_planning_contracts import (
    ActionApplyCommand,
    ActionDefinitionV3,
    ActionExecutionPlanResponse,
    ActionObjectRecordLookup,
    ActionTypeRow,
    EditPlan,
    ObjectRecordRow,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.action_planning_resolution_support import (
    action_command,
    action_request_fingerprint,
)
from foundry_lite.application.services.action_planning_response_support import (
    ActionRiskAssessment,
    _now,
    action_contract_fingerprint,
    action_plan_diffs,
    can_agent_execute_autonomously,
    edit_plan_manifest,
    seal_action_execution_plan,
)
from foundry_lite.domain.action_runtime.action_effects import action_effect_payload
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True, slots=True)
class PreparedActionPlan:
    action_type: ActionTypeRow
    contract: ActionDefinitionV3
    command: ActionApplyCommand
    plan: EditPlan
    target: ObjectRecordRow
    risk: ActionRiskAssessment


def build_action_plan_response(
    conn: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    prepared: PreparedActionPlan,
    object_lookup: ActionObjectRecordLookup,
    *,
    branch_id: str | None,
    is_dry_run: bool,
    authorization_ctx: RequestContext | None = None,
) -> ActionExecutionPlanResponse:
    contract = prepared.contract
    payload: dict[str, object] = {
        "actionApiName": contract.api_name,
        "ontologyVersionId": prepared.action_type["ontology_version_id"],
        "definitionFingerprint": action_contract_fingerprint(contract),
        "functionVersion": contract.function.version if contract.function else None,
        "target": _target_payload(prepared.command, prepared.target),
        "parameters": dict(prepared.command.params),
        "editManifest": edit_plan_manifest(prepared.plan),
        "diffs": action_plan_diffs(conn, ctx, policy, object_lookup, prepared.plan),
        "effectManifest": [] if branch_id else [action_effect_payload(effect) for effect in contract.effects],
        "risk": _risk_payload(prepared.risk),
        "authorization": _authorization_payload(authorization_ctx or ctx, policy_ctx=ctx),
        "approval": _approval_payload(contract, prepared.risk),
        "executionMode": "branch_overlay" if branch_id else _execution_mode(contract, prepared.plan),
        "isDryRun": is_dry_run,
        "requestId": ctx.request_id,
        "createdAt": _now(),
    }
    if branch_id is not None:
        payload["branchId"] = branch_id
        payload["suppressedEffects"] = [action_effect_payload(effect) for effect in contract.effects]
    return cast(ActionExecutionPlanResponse, seal_action_execution_plan(payload))


def plan_action_command(
    action_api_name: str,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
) -> ActionApplyCommand:
    fingerprint = action_request_fingerprint(
        action_api_name=action_api_name,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=expected_object_version,
        params=params,
    )
    return action_command(
        action_api_name,
        object_type,
        object_id,
        expected_object_version,
        params,
        f"plan:{fingerprint}",
        False,
        False,
        False,
        False,
    )


def _target_payload(command: ActionApplyCommand, target: ObjectRecordRow) -> dict[str, object]:
    return {
        "objectType": command.object_type,
        "objectId": command.object_id,
        "expectedObjectVersion": command.expected_object_version,
        "readObjectVersion": target["object_version"],
    }


def _risk_payload(risk: ActionRiskAssessment) -> dict[str, object]:
    return {
        "declaredLevel": risk.declared_level,
        "effectiveLevel": risk.effective_level,
        "reasons": list(risk.reasons),
    }


def _authorization_payload(ctx: RequestContext, *, policy_ctx: RequestContext) -> dict[str, object]:
    payload: dict[str, object] = {
        "actorUserId": ctx.actor_user_id,
        "applicationId": ctx.application_id,
        "clientId": ctx.client_id,
        "roles": sorted(ctx.roles),
        "tokenScopes": sorted(ctx.token_scopes),
        "userAttributeKeys": sorted(ctx.user_attributes),
        "decision": "allow",
    }
    if policy_ctx.roles != ctx.roles:
        payload["effectivePolicyRoles"] = sorted(policy_ctx.roles)
    return payload


def _approval_payload(contract: ActionDefinitionV3, risk: ActionRiskAssessment) -> dict[str, object]:
    is_autonomous = can_agent_execute_autonomously(contract, risk)
    return {
        "requiredForAgent": not is_autonomous,
        "agentExecutionPolicy": contract.agent_execution_policy,
        "canAgentExecuteAutonomously": is_autonomous,
        "reason": None if is_autonomous else _approval_reason(contract, risk),
    }


def _approval_reason(contract: ActionDefinitionV3, risk: ActionRiskAssessment) -> str:
    if risk.effective_level != "low":
        return f"{risk.effective_level}_risk_requires_human_approval"
    return f"agent_policy_{contract.agent_execution_policy}"


def _execution_mode(contract: ActionDefinitionV3, plan: EditPlan) -> str:
    if contract.function is not None or contract.effects:
        return "async"
    edit_count = sum(
        len(items)
        for items in (
            plan.objects_to_create,
            plan.objects_to_modify,
            plan.objects_to_delete,
            plan.links_to_create,
            plan.links_to_delete,
        )
    )
    return "async" if edit_count > 25 else "sync"
