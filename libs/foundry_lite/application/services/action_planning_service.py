"""Resolve, authorize, risk-classify, and seal immutable Action execution plans."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.services.action_media_runtime_service import ActionMediaRuntimeService
from foundry_lite.application.services.action_planning_contracts import (
    ActionApplyCommand,
    ActionDefinitionV3,
    ActionExecutionPlanResponse,
    ActionNotificationRecipientDirectory,
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
from foundry_lite.application.services.action_planning_output import (
    PreparedActionPlan,
    build_action_plan_response,
    plan_action_command,
)
from foundry_lite.application.services.action_planning_request_support import resolve_plan_contract
from foundry_lite.application.services.action_planning_resolution_support import (
    LivePlanResolutionContext,
    action_target_record_error,
    authorize_action_edit_plan,
    build_edit_plan,
    compile_action_definition,
    require_action_permission,
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
    action_plan_risk,
    require_declared_risk_floor,
)
from foundry_lite.application.services.action_planning_rules import (
    ResolvedLinkedCriteria,
    authorize_action_effects,
    authorize_external_mcp_action_plan,
    function_edit_plan,
    inspect_action_edit_plan,
    interface_create_target_record,
    require_interface_action_target,
    require_interface_create_plan_target,
    resolve_interface_action_definition,
    resolve_linked_condition_context,
    validate_action_effect_targets,
    with_criteria_expectations,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_service_principal_authorization import ServicePrincipalAccessSessionBoundary


class ActionPlanningService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "connector_registry_repository",
        "object_read_repository",
        "action_branch_repository",
        "action_notification_recipient_directory",
    )
    required_collaborators = (
        "object_records_service",
        "action_media_runtime_service",
        "ontology_lookup_service",
        "osdk_access_session_service",
        "osdk_application_scope_service",
        "runtime_service",
    )
    object_records_service: ActionObjectRecordLookup
    action_media_runtime_service: ActionMediaRuntimeService
    ontology_lookup_service: OntologyLookupService
    osdk_access_session_service: ServicePrincipalAccessSessionBoundary
    osdk_application_scope_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary
    connector_registry_repository: ConnectorRegistryRepository
    action_branch_repository: ActionBranchRepository
    action_notification_recipient_directory: ActionNotificationRecipientDirectory

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
        return self.plan_action_with_object_lookup(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            object_lookup=self.object_records_service,
            ctx=ctx,
            is_dry_run=is_dry_run,
        )

    def plan_external_mcp_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext,
    ) -> ActionExecutionPlanResponse:
        reader_ctx = self._authorize_external_mcp_request(ctx, action_api_name, object_type, object_id)
        with self.engine.begin() as conn:
            prepared = self._prepare_plan(
                conn,
                reader_ctx,
                action_api_name,
                object_type,
                object_id,
                expected_object_version,
                params,
                self.object_records_service,
                None,
                None,
                is_external_mcp=True,
            )
            return build_action_plan_response(
                conn,
                reader_ctx,
                self.policy,
                prepared,
                self.object_records_service,
                branch_id=None,
                is_dry_run=False,
                authorization_ctx=ctx,
            )

    def plan_action_with_object_lookup(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        object_lookup: ActionObjectRecordLookup,
        action_type_override: ActionTypeRow | None = None,
        branch_id: str | None = None,
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
                object_lookup,
                action_type_override,
                branch_id,
            )
            return build_action_plan_response(
                conn,
                request_context,
                self.policy,
                prepared,
                object_lookup,
                branch_id=branch_id,
                is_dry_run=is_dry_run,
            )

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

    def _authorize_external_mcp_request(
        self,
        ctx: RequestContext,
        action_api_name: str,
        object_type: str,
        object_id: str,
    ) -> RequestContext:
        return authorize_external_mcp_action_plan(
            self.engine,
            self.policy,
            self.runtime_service,
            self.osdk_access_session_service,
            self.osdk_application_scope_service,
            ctx,
            action_api_name,
            object_type,
            object_id,
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
        object_lookup: ActionObjectRecordLookup,
        action_type_override: ActionTypeRow | None,
        branch_id: str | None,
        *,
        is_external_mcp: bool = False,
    ) -> PreparedActionPlan:
        action_type, contract = resolve_plan_contract(
            self.ontology_lookup_service,
            conn,
            ctx,
            action_api_name,
            action_type_override,
            is_external_mcp=is_external_mcp,
        )
        require_interface_action_target(conn, ctx, self.ontology_lookup_service, contract, object_type)
        command = plan_action_command(action_api_name, object_type, object_id, expected_object_version, params)
        target = self._target_record(
            conn, ctx, action_type, contract, object_type, object_id, expected_object_version, object_lookup
        )
        return self._prepare_resolved_plan(
            conn,
            ctx,
            action_type,
            contract,
            target,
            command,
            object_lookup,
            branch_id,
            is_external_mcp=is_external_mcp,
        )

    def _prepare_resolved_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        contract: ActionDefinitionV3,
        target: ObjectRecordRow,
        command: ActionApplyCommand,
        object_lookup: ActionObjectRecordLookup,
        branch_id: str | None,
        *,
        is_external_mcp: bool,
    ) -> PreparedActionPlan:
        effective, criteria = self._effective_command(conn, ctx, action_type, contract, target, command, branch_id)
        plan, risk = self._authorized_plan(
            conn,
            ctx,
            action_type,
            contract,
            effective,
            object_lookup,
            branch_id,
            criteria,
            is_external_mcp=is_external_mcp,
        )
        return PreparedActionPlan(action_type, contract, effective, plan, target, risk)

    def _effective_command(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        contract: ActionDefinitionV3,
        target: ObjectRecordRow,
        command: ActionApplyCommand,
        branch_id: str | None,
    ) -> tuple[ActionApplyCommand, ResolvedLinkedCriteria]:
        criteria = self._criteria_context(conn, ctx, action_type, target, branch_id)
        if (error := self._request_error(ctx, action_type, target, command, criteria)) is not None:
            raise error
        effective = resolved_action_command(ctx, action_type, target, command)
        effective = self.action_media_runtime_service.resolve_command(conn, ctx, contract, effective)
        return effective, criteria

    def _authorized_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        contract: ActionDefinitionV3,
        command: ActionApplyCommand,
        object_lookup: ActionObjectRecordLookup,
        branch_id: str | None,
        criteria: ResolvedLinkedCriteria,
        *,
        is_external_mcp: bool,
    ) -> tuple[EditPlan, ActionRiskAssessment]:
        if branch_id is None:
            self._authorize_or_validate_effects(conn, ctx, contract, is_external_mcp=is_external_mcp)
        plan = self._resolved_edit_plan(
            conn, ctx, action_type, command, contract, object_lookup, is_external_mcp=is_external_mcp
        )
        plan = with_criteria_expectations(plan, criteria.expectations)
        sensitive = (
            inspect_action_edit_plan(conn, ctx, self.ontology_lookup_service, contract, plan)
            if is_external_mcp
            else authorize_action_edit_plan(conn, ctx, self.policy, self.ontology_lookup_service, contract, plan)
        )
        risk = action_plan_risk(contract, plan, sensitive)
        require_declared_risk_floor(risk)
        return plan, risk

    def _authorize_or_validate_effects(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        *,
        is_external_mcp: bool,
    ) -> None:
        if is_external_mcp:
            validate_action_effect_targets(
                conn,
                ctx,
                self.connector_registry_repository,
                self.action_notification_recipient_directory,
                contract,
            )
            return
        authorize_action_effects(
            conn,
            ctx,
            self.policy,
            self.osdk_application_scope_service,
            self.connector_registry_repository,
            self.action_notification_recipient_directory,
            contract,
        )

    def _target_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        contract: ActionDefinitionV3,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        object_lookup: ActionObjectRecordLookup,
    ) -> ObjectRecordRow:
        record = object_lookup._object_record(conn, ctx, object_type, object_id)
        target_type = self.ontology_lookup_service._active_object_type(conn, ctx, object_type)
        record = visible_record(
            record,
            target_type,
            ctx.roles,
            self.ontology_lookup_service._properties_for_object_type(conn, target_type["id"]),
        )
        if record is None:
            record = interface_create_target_record(
                contract,
                compile_action_definition(action_type["definition"]),
                target_type,
                object_id,
                expected_object_version,
                ctx.tenant_id,
            )
        if record is None:
            raise NotFound("target object not found")
        record_type = self.ontology_lookup_service._object_type_by_id_or_none(conn, ctx, record["object_type_id"])
        if (error := action_target_record_error(action_type, record, record_type)) is not None:
            raise error
        return record

    def _request_error(
        self,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        target: ObjectRecordRow,
        command: ActionApplyCommand,
        criteria: ResolvedLinkedCriteria,
    ) -> Exception | None:
        if (error := segment_mutation_denied_error(self.policy, ctx, action_type)) is not None:
            return error
        return validate_action_request(
            action_type,
            target,
            command.params,
            ctx,
            generate_id=stable_parameter_id_generator(command.idempotency_key),
            linked_object_properties=criteria.values,
        )

    def _criteria_context(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        target: ObjectRecordRow,
        branch_id: str | None,
    ) -> ResolvedLinkedCriteria:
        return resolve_linked_condition_context(
            conn,
            ctx,
            self.policy,
            self.object_read_repository,
            self.ontology_lookup_service,
            self.osdk_application_scope_service,
            action_type,
            target,
            branch_repository=self.action_branch_repository if branch_id else None,
            branch_id=branch_id,
        )

    def _resolved_edit_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        command: ActionApplyCommand,
        contract: ActionDefinitionV3,
        object_lookup: ActionObjectRecordLookup,
        *,
        is_external_mcp: bool,
    ) -> EditPlan:
        if contract.function is not None:
            return function_edit_plan(
                ctx, contract, self.policy, self.osdk_application_scope_service, is_external_mcp=is_external_mcp
            )
        return self._rule_edit_plan(conn, ctx, action_type, command, contract, object_lookup)

    def _rule_edit_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        command: ActionApplyCommand,
        contract: ActionDefinitionV3,
        object_lookup: ActionObjectRecordLookup,
    ) -> EditPlan:
        compiled = resolve_interface_action_definition(
            conn,
            ctx,
            self.ontology_lookup_service,
            contract,
            compile_action_definition(action_type["definition"]),
            command.object_type,
        )
        resolution = LivePlanResolutionContext(
            conn,
            ctx,
            command,
            object_lookup,
            self.ontology_lookup_service,
            self.ontology_lookup_service,
        )
        plan = build_edit_plan(compiled, resolution)
        validate_edit_plan(plan)
        require_interface_create_plan_target(contract, compiled, plan, command.object_type, command.object_id)
        return plan
