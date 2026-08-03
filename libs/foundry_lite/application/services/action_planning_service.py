"""Resolve, authorize, risk-classify, and seal immutable Action execution plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.services.action_planning_contracts import (
    ActionApplyCommand,
    ActionDefinitionV3,
    ActionExecutionPlanResponse,
    ActionObjectRecordLookup,
    ActionOsdkScopeBoundary,
    ActionRuntimeBoundary,
    ActionTypeRow,
    EditPlan,
    NotFound,
    ObjectRecordRow,
    OntologyLookupService,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.action_planning_resolution_support import (
    LivePlanResolutionContext,
    action_command,
    action_request_fingerprint,
    action_target_record_error,
    authorize_action_edit_plan,
    build_edit_plan,
    compile_action_definition,
    require_action_permission,
    require_action_target_api_name,
    require_action_target_read,
    resolved_action_command,
    segment_mutation_denied_error,
    stable_parameter_id_generator,
    validate_action_request,
    validate_edit_plan,
    visible_record,
)
from foundry_lite.application.services.action_planning_response_support import (
    ActionRiskAssessment,
    _now,
    action_contract_fingerprint,
    action_plan_diffs,
    action_plan_risk,
    can_agent_execute_autonomously,
    compile_action_contract,
    edit_plan_manifest,
    require_declared_risk_floor,
    seal_action_execution_plan,
)
from foundry_lite.application.services.base import CoreService


@dataclass(frozen=True, slots=True)
class _PreparedPlan:
    action_type: ActionTypeRow
    contract: ActionDefinitionV3
    command: ActionApplyCommand
    plan: EditPlan
    target: ObjectRecordRow
    risk: ActionRiskAssessment


class ActionPlanningService(CoreService):
    """Read-only plan and dry-run use cases shared by UI, SDK, and future MCP."""

    required_dependencies = ("engine", "policy")
    required_collaborators = (
        "object_records_service",
        "ontology_lookup_service",
        "osdk_application_scope_service",
        "runtime_service",
    )
    object_records_service: ActionObjectRecordLookup
    ontology_lookup_service: OntologyLookupService
    osdk_application_scope_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary

    def plan_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
        is_dry_run: bool = False,
    ) -> ActionExecutionPlanResponse:
        request_context = ctx or RequestContext()
        self._authorize_request(request_context, action_api_name, object_type, object_id)
        with self.engine.begin() as conn:
            prepared = self._prepare_plan(
                conn,
                request_context,
                action_api_name,
                object_type,
                object_id,
                expected_object_version,
                params,
            )
            return self._plan_response(conn, request_context, prepared, is_dry_run=is_dry_run)

    def _authorize_request(
        self,
        ctx: RequestContext,
        action_api_name: str,
        object_type: str,
        object_id: str,
    ) -> None:
        require_action_permission(self.engine, self.policy, self.runtime_service, ctx, action_api_name, action="plan")
        require_action_target_read(
            self.engine,
            self.policy,
            self.runtime_service,
            ctx,
            action_api_name,
            object_type,
            object_id,
            action="plan",
        )
        self.osdk_application_scope_service.require_resource_scope(
            ctx, resource_type="action", resource_api_name=action_api_name, operation="validate"
        )

    def _prepare_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_api_name: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
    ) -> _PreparedPlan:
        action_type = self.ontology_lookup_service._active_action_type(conn, ctx, action_api_name)
        require_action_target_api_name(action_type, object_type)
        target = self._target_record(conn, ctx, action_type, object_type, object_id)
        command = _plan_command(action_api_name, object_type, object_id, expected_object_version, params)
        error = self._request_error(ctx, action_type, target, command)
        if error is not None:
            raise error
        effective = resolved_action_command(ctx, action_type, target, command)
        contract = compile_action_contract(action_type["definition"])
        plan = self._resolved_edit_plan(conn, ctx, action_type, effective, contract)
        sensitive = authorize_action_edit_plan(conn, ctx, self.policy, self.ontology_lookup_service, contract, plan)
        risk = action_plan_risk(contract, plan, sensitive)
        require_declared_risk_floor(risk)
        return _PreparedPlan(action_type, contract, effective, plan, target, risk)

    def _target_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        object_type: str,
        object_id: str,
    ) -> ObjectRecordRow:
        record = self.object_records_service._object_record(conn, ctx, object_type, object_id)
        target_type = self.ontology_lookup_service._active_object_type(conn, ctx, object_type)
        record = visible_record(record, target_type, ctx.roles)
        if record is None:
            raise NotFound("target object not found")
        if (error := action_target_record_error(action_type, record)) is not None:
            raise error
        return record

    def _request_error(
        self,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        target: ObjectRecordRow,
        command: ActionApplyCommand,
    ) -> Exception | None:
        if (error := segment_mutation_denied_error(self.policy, ctx, action_type)) is not None:
            return error
        return validate_action_request(
            action_type,
            target,
            command.params,
            ctx,
            generate_id=stable_parameter_id_generator(command.idempotency_key),
        )

    def _resolved_edit_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        command: ActionApplyCommand,
        contract: ActionDefinitionV3,
    ) -> EditPlan:
        if contract.function is not None:
            self.policy.require(ctx, "function:execute")
            self.osdk_application_scope_service.require_resource_scope(
                ctx,
                resource_type="function",
                resource_api_name=contract.function.api_name,
                operation="execute",
            )
            return EditPlan()
        compiled = compile_action_definition(action_type["definition"])
        resolution = LivePlanResolutionContext(
            conn,
            ctx,
            command,
            self.object_records_service,
            self.ontology_lookup_service,
            self.ontology_lookup_service,
        )
        plan = build_edit_plan(compiled, resolution)
        validate_edit_plan(plan)
        return plan

    def _plan_response(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        prepared: _PreparedPlan,
        *,
        is_dry_run: bool,
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
            "diffs": action_plan_diffs(conn, ctx, self.policy, self.object_records_service, prepared.plan),
            "effectManifest": [dict(effect) for effect in contract.effects],
            "risk": _risk_payload(prepared.risk),
            "authorization": _authorization_payload(ctx),
            "approval": _approval_payload(contract, prepared.risk),
            "executionMode": _execution_mode(contract, prepared.plan),
            "isDryRun": is_dry_run,
            "requestId": ctx.request_id,
            "createdAt": _now(),
        }
        return cast(ActionExecutionPlanResponse, seal_action_execution_plan(payload))


def _plan_command(
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


def _authorization_payload(ctx: RequestContext) -> dict[str, object]:
    return {
        "actorUserId": ctx.actor_user_id,
        "applicationId": ctx.application_id,
        "clientId": ctx.client_id,
        "roles": sorted(ctx.roles),
        "tokenScopes": sorted(ctx.token_scopes),
        "decision": "allow",
    }


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
