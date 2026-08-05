"""Permissioned contract lookup for Action media and attachment uploads."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.media_repository import MediaRepository, MediaSetRecord
from foundry_lite.application.services.action_interface_resolution import require_interface_action_target
from foundry_lite.application.services.action_media_parameters import action_media_parameter
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
)
from foundry_lite.application.services.action_protocols import ActionOntologyLookup, ActionRuntimeBoundary
from foundry_lite.domain.action_runtime.action_contract import (
    ActionDefinitionV3,
    ActionParameterV3,
    compile_action_contract,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True, slots=True)
class ActionMediaUploadContract:
    """Resolved immutable Action parameter and its governed Media Set."""

    contract: ActionDefinitionV3
    parameter: ActionParameterV3
    reference_kind: str
    media_set: MediaSetRecord


def resolve_action_media_upload_contract(
    engine: TransactionManager,
    policy: PolicyService,
    runtime: ActionRuntimeBoundary,
    ontology: ActionOntologyLookup,
    repository: MediaRepository,
    ctx: RequestContext,
    action_api_name: str,
    parameter_name: str,
    object_type: str,
    object_id: str,
) -> ActionMediaUploadContract:
    """Authorize the target and resolve the declared Action upload contract."""
    require_action_permission(engine, policy, runtime, ctx, action_api_name, action="upload")
    require_action_target_read(engine, policy, runtime, ctx, action_api_name, object_type, object_id, action="upload")
    with engine.begin() as transaction:
        row = ontology._active_action_type(transaction, ctx, action_api_name)
        contract = compile_action_contract(row["definition"])
        require_interface_action_target(transaction, ctx, ontology, contract, object_type)
        parameter, kind = action_media_parameter(contract, parameter_name)
        media_set = _required_media_set(transaction, ctx, repository, parameter)
    return ActionMediaUploadContract(contract, parameter, kind, media_set)


def _required_media_set(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    parameter: ActionParameterV3,
) -> MediaSetRecord:
    """Resolve the tenant Media Set pinned by an Action parameter."""
    namespace, name = str(parameter.metadata["mediaSet"]).split(".", 1)
    found = repository.media_set_by_ref(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        namespace=namespace,
        name=name,
    )
    if found is None:
        raise NotFound("Action parameter Media Set not found", details={"mediaSet": f"{namespace}.{name}"})
    return found


__all__ = [
    "ActionDefinitionV3",
    "ActionMediaUploadContract",
    "ActionOntologyLookup",
    "ActionParameterV3",
    "resolve_action_media_upload_contract",
]
