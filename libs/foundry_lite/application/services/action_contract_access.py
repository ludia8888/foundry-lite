"""Compile canonical Action contracts together with operation-specific access."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, compile_action_contract
from foundry_lite.domain.action_runtime.action_permissions import ActionPermissionOperation, require_action_access
from foundry_lite.domain.context import RequestContext


def authorized_action_contract(
    definition: Mapping[str, object],
    ctx: RequestContext,
    operation: ActionPermissionOperation,
) -> ActionDefinitionV3:
    """Compile one immutable contract and require its declared role grant."""
    contract = compile_action_contract(definition)
    require_action_access(ctx, contract.api_name, contract.permissions, operation)
    return contract
