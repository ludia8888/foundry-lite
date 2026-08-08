"""Function-backed Action plan authorization helpers."""

from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.policy import PolicyService


def function_edit_plan(
    ctx: RequestContext,
    contract: ActionDefinitionV3,
    policy: PolicyService,
    scopes: ActionOsdkScopeBoundary,
    *,
    is_external_mcp: bool,
) -> EditPlan:
    if not is_external_mcp and contract.function is not None:
        policy.require(ctx, "function:execute")
        scopes.require_resource_scope(
            ctx,
            resource_type="function",
            resource_api_name=contract.function.api_name,
            operation="execute",
        )
    return EditPlan()
