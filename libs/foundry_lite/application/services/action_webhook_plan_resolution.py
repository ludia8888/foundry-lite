"""Re-resolve rule-backed EditPlans from typed before-commit webhook outputs."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_async_execution_types import ActionAsyncRunRow
from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.action_interface_resolution import (
    require_interface_create_plan_target,
    resolve_interface_action_definition,
)
from foundry_lite.application.services.action_ir_compiler import (
    ActionDefinitionV3,
    compile_canonical_action_definition,
)
from foundry_lite.application.services.action_plan_resolution import LivePlanResolutionContext
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.domain.action_runtime.action_contract import action_contract_uses_webhook_response
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, validate_edit_plan
from foundry_lite.domain.action_runtime.edit_plan_builder import build_edit_plan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


def requires_webhook_plan_resolution(contract: ActionDefinitionV3) -> bool:
    """Return whether the plan must be rebuilt from a typed webhook receipt."""
    return action_contract_uses_webhook_response(contract)


def resolve_webhook_edit_plan(
    transaction: TransactionContext,
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    contract: ActionDefinitionV3,
    before_effect: Mapping[str, object] | None,
    object_lookup: ActionObjectRecordLookup,
    ontology: OntologyLookupService,
) -> EditPlan:
    """Resolve the sealed rule source against the fenced, typed effect receipt."""
    response = _effect_response(before_effect)
    command = _action_command(row)
    compiled = resolve_interface_action_definition(
        transaction,
        ctx,
        ontology,
        contract,
        compile_canonical_action_definition(contract),
        command.object_type,
    )
    resolution = LivePlanResolutionContext(
        transaction,
        ctx,
        command,
        object_lookup,
        ontology,
        ontology,
        webhook_response_values=response,
    )
    plan = build_edit_plan(compiled, resolution)
    validate_edit_plan(plan)
    require_interface_create_plan_target(contract, compiled, plan, command.object_type, command.object_id)
    return plan


def _effect_response(before_effect: Mapping[str, object] | None) -> Mapping[str, object]:
    """Require the fenced typed response from the before-commit receipt."""
    if not isinstance(before_effect, Mapping):
        raise InvariantViolation("webhook-backed Action run is missing before-effect evidence")
    response = before_effect.get("response")
    if not isinstance(response, Mapping):
        raise InvariantViolation("webhook-backed Action run is missing a typed response")
    return response


def _action_command(row: ActionAsyncRunRow) -> ActionApplyCommand:
    """Rehydrate the immutable Action request sealed into a durable run."""
    return ActionApplyCommand(
        action_api_name=row["action_type_api_name"],
        object_type=row["target_object_type_api_name"],
        object_id=row["target_object_id"],
        expected_object_version=row["expected_object_version"],
        params=dict(row["parameters"]),
        idempotency_key=row["idempotency_key"],
        request_fingerprint=row["request_fingerprint"],
        simulate_writeback_failure=False,
        simulate_writeback_retryable=False,
        simulate_writeback_outcome_unknown=False,
        simulate_writeback_compensation_required=False,
    )
