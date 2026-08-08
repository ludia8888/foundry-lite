"""Commit-time revalidation for a sealed distributed Action plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports import ObjectReadRepository
from foundry_lite.application.services.action_criteria_resolution import ActionCriteriaCommitVerifier
from foundry_lite.application.services.action_distributed_contracts import (
    ActionAsyncRunRow,
    ActionDefinitionV3,
    ActionOsdkScopeBoundary,
    ActionRuntimeBoundary,
    EditPlan,
    OntologyLookupService,
    RequestContext,
    TransactionContext,
    TransactionManager,
    action_contract_fingerprint,
)
from foundry_lite.application.services.action_distributed_effects import (
    ActionNotificationRecipientDirectory,
    ConnectorRegistryRepository,
    authorize_action_effects,
)
from foundry_lite.application.services.action_distributed_run_support import stored_action_contract
from foundry_lite.application.services.action_function_batch import stored_action_function_batch_items
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
)
from foundry_lite.application.services.action_plan_authorization import (
    authorize_action_edit_plan,
    inspect_action_edit_plan,
)
from foundry_lite.application.services.osdk_service_principal_authorization import (
    ServicePrincipalAccessSessionBoundary,
    is_client_credentials_service_principal,
    require_service_principal_scope,
    service_principal_reader_context,
)
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True, slots=True)
class ActionRunRevalidator:
    engine: TransactionManager
    policy: PolicyService
    runtime: ActionRuntimeBoundary
    ontology: OntologyLookupService
    scopes: ActionOsdkScopeBoundary
    connectors: ConnectorRegistryRepository
    object_repository: ObjectReadRepository
    notification_directory: ActionNotificationRecipientDirectory
    access_sessions: ServicePrincipalAccessSessionBoundary

    def revalidate(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        plan: EditPlan,
    ) -> ActionDefinitionV3:
        contract = stored_action_contract(row)
        active = self.ontology._active_action_type(transaction, ctx, row["action_type_api_name"])
        if active["id"] != row["action_type_id"]:
            raise ConflictDetected("Action definition changed after planning")
        if action_contract_fingerprint(contract) != row["definition_version"]:
            raise ConflictDetected("Action definition fingerprint changed after planning")
        self._authorize_principal(transaction, ctx, row, contract, plan)
        return contract

    def _authorize_principal(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        contract: ActionDefinitionV3,
        plan: EditPlan,
    ) -> None:
        if is_client_credentials_service_principal(ctx):
            self._authorize_external_principal(transaction, ctx, row, contract, plan)
            return
        require_action_permission(self.engine, self.policy, self.runtime, ctx, contract.api_name, action="execute")
        self._authorize_targets(ctx, row, contract)
        self.scopes.require_resource_scope(
            ctx, resource_type="action", resource_api_name=contract.api_name, operation="execute"
        )
        authorize_action_effects(
            transaction,
            ctx,
            self.policy,
            self.scopes,
            self.connectors,
            self.notification_directory,
            contract,
        )
        authorize_action_edit_plan(transaction, ctx, self.policy, self.ontology, contract, plan)
        ActionCriteriaCommitVerifier(self.policy, self.object_repository, self.ontology, self.scopes).verify(
            transaction, ctx, plan
        )

    def _authorize_external_principal(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        contract: ActionDefinitionV3,
        plan: EditPlan,
    ) -> None:
        require_service_principal_scope(
            ctx,
            self.access_sessions,
            self.scopes,
            resource_type="action",
            resource_api_name=contract.api_name,
            operation="execute",
        )
        _require_external_autonomous_snapshot(row, contract)
        for object_type in {str(target.get("objectType") or "") for target in _stored_targets(row)}:
            require_service_principal_scope(
                ctx,
                self.access_sessions,
                self.scopes,
                resource_type="object",
                resource_api_name=object_type,
                operation="read",
            )
        reader_ctx = service_principal_reader_context(ctx)
        self._authorize_targets(reader_ctx, row, contract)
        inspect_action_edit_plan(transaction, reader_ctx, self.ontology, contract, plan)
        ActionCriteriaCommitVerifier(self.policy, self.object_repository, self.ontology, self.scopes).verify(
            transaction, reader_ctx, plan
        )

    def _authorize_targets(
        self,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        contract: ActionDefinitionV3,
    ) -> None:
        for target in _stored_targets(row):
            require_action_target_read(
                self.engine,
                self.policy,
                self.runtime,
                ctx,
                contract.api_name,
                str(target.get("objectType") or ""),
                str(target.get("objectId") or ""),
                action="execute",
            )


def _stored_targets(row: ActionAsyncRunRow) -> list[Mapping[str, object]]:
    snapshot = row["execution_plan"]
    items = stored_action_function_batch_items(snapshot if isinstance(snapshot, Mapping) else {})
    targets: list[object] = [item.get("target") for item in items] if items else []
    if not targets:
        targets = [{"objectType": row["target_object_type_api_name"], "objectId": row["target_object_id"]}]
    if any(not isinstance(target, Mapping) for target in targets):
        raise ConflictDetected("stored Action target changed after planning")
    return [target for target in targets if isinstance(target, Mapping)]


def _require_external_autonomous_snapshot(row: ActionAsyncRunRow, contract: ActionDefinitionV3) -> None:
    snapshot = row.get("execution_plan")
    approval = snapshot.get("approval") if isinstance(snapshot, Mapping) else None
    if (
        not isinstance(approval, Mapping)
        or approval.get("canAgentExecuteAutonomously") is not True
        or contract.function is not None
        or bool(contract.effects)
    ):
        raise ValidationFailed("external MCP autonomous Action proof is no longer valid")
