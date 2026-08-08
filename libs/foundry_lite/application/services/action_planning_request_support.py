"""Request-side contract resolution helpers for Action planning."""

from __future__ import annotations

from foundry_lite.application.services.action_planning_contracts import (
    ActionDefinitionV3,
    ActionTypeRow,
    OntologyLookupService,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.action_planning_resolution_support import authorized_action_contract
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.errors import NotFound


def resolve_plan_contract(
    ontology: OntologyLookupService,
    conn: TransactionContext,
    ctx: RequestContext,
    action_api_name: str,
    action_type_override: ActionTypeRow | None,
    *,
    is_external_mcp: bool,
) -> tuple[ActionTypeRow, ActionDefinitionV3]:
    action_type = action_type_override or ontology._active_action_type(conn, ctx, action_api_name)
    if action_type["api_name"] != action_api_name:
        raise NotFound("Action override does not match the requested Action")
    contract = (
        compile_action_contract(action_type["definition"])
        if is_external_mcp
        else authorized_action_contract(action_type["definition"], ctx, "apply")
    )
    return action_type, contract
