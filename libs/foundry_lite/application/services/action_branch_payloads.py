"""Pure records and response payloads for branch-isolated Action execution."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import cast

from foundry_lite.application.action_log_types import ActionLogObjectRecord
from foundry_lite.application.action_types import ActionExecutionPlanResponse
from foundry_lite.application.ports import (
    ActionRunRecord,
    ActionRunRow,
    ActionTypeRow,
    ObjectRecordRow,
    TransactionContext,
)
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRow
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.application.services.ontology_branch_action_types import branch_action_type_item
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, compile_action_contract
from foundry_lite.domain.action_runtime.action_permissions import require_action_access
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


def require_branch_action_apply_access(ctx: RequestContext, contract: ActionDefinitionV3) -> None:
    """Apply the canonical Action grant to a branch-resolved definition."""
    require_action_access(ctx, contract.api_name, contract.permissions, "apply")


def resolve_branch_action_type(
    transaction: TransactionContext,
    ctx: RequestContext,
    branch: OntologyBranchRow,
    action_api_name: str,
    ontology: ActionOntologyLookup,
) -> ActionTypeRow:
    active = ontology._active_ontology_version(transaction, ctx)
    if branch["base_version_id"] != active["id"]:
        raise ConflictDetected("ontology branch must be rebased before Action scenario execution")
    item = branch_action_type_item(branch["content_yaml_text"], action_api_name)
    definition = _mapping(item.get("definition"), "branch Action definition")
    contract = compile_action_contract(definition)
    _require_branch_policy(contract)
    return _branch_action_row(transaction, ctx, branch, item, definition, contract, ontology)


def _branch_action_row(
    transaction: TransactionContext,
    ctx: RequestContext,
    branch: OntologyBranchRow,
    item: Mapping[str, object],
    definition: dict[str, object],
    contract: ActionDefinitionV3,
    ontology: ActionOntologyLookup,
) -> ActionTypeRow:
    return cast(
        ActionTypeRow,
        {
            "id": _branch_action_id(branch["id"], contract.api_name),
            "tenant_id": ctx.tenant_id,
            "ontology_version_id": f"branch:{branch['id']}:{branch['content_fingerprint']}",
            "api_name": contract.api_name,
            "display_name": contract.display_name,
            "target_kind": contract.target.kind,
            "target_object_type_id": _target_id(
                transaction, ctx, branch["base_version_id"], contract.target.kind, contract.target.api_name, ontology
            ),
            "target_api_name": contract.target.api_name,
            "parameter_schema": _mapping(item.get("parameterSchema"), "branch Action parameter schema"),
            "definition": definition,
            "enabled": True,
        },
    )


def _require_branch_policy(contract: ActionDefinitionV3) -> None:
    if contract.branch_policy.get("enabled") is not True:
        raise ValidationFailed("Action definition does not allow branch execution")
    if contract.function is not None:
        raise ValidationFailed("function-backed branch Actions require an isolated function runtime")


def _target_id(
    transaction: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    target_kind: str,
    target_api_name: str,
    ontology: ActionOntologyLookup,
) -> str:
    if target_kind == "object":
        return ontology._active_object_type(transaction, ctx, target_api_name)["id"]
    for row in ontology._interface_types_for_version(transaction, ctx, ontology_version_id):
        if row["api_name"] == target_api_name:
            return row["id"]
    raise NotFound("branch Action interface target not found", details={"interface": target_api_name})


def _branch_action_id(branch_id: str, action_api_name: str) -> str:
    digest = hashlib.sha256(f"{branch_id}:{action_api_name}".encode()).hexdigest()[:24]
    return f"branch_action_{digest}"


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"{field} must be an object")
    return {str(key): item for key, item in value.items()}


def branch_run_record(
    ctx: RequestContext,
    action_type: ActionTypeRow,
    concrete_type_id: str,
    branch_id: str,
    idempotency_key: str,
    fingerprint: str,
    plan: ActionExecutionPlanResponse,
) -> ActionRunRecord:
    target = plan["target"]
    return ActionRunRecord(
        action_run_id=_new_id("action_run"),
        tenant_id=ctx.tenant_id,
        action_type_id=action_type["id"],
        action_type_api_name=action_type["api_name"],
        actor_user_id=ctx.actor_user_id,
        target_object_type_id=concrete_type_id,
        target_object_type_api_name=str(target["objectType"]),
        target_object_id=str(target["objectId"]),
        expected_object_version=plan_version(target),
        parameters=plan["parameters"],
        status="received",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        result=None,
        error=None,
        created_at=_now(),
        completed_at=None,
        definition_version=str(plan["definitionFingerprint"]),
        plan_hash=str(plan["planHash"]),
        execution_plan=plan,
        branch_id=branch_id,
    )


def branch_request_fingerprint(
    action_api_name: str,
    branch_id: str,
    definition_version: str,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
) -> str:
    return _json_hash(
        {
            "actionApiName": action_api_name,
            "branchId": branch_id,
            "definitionVersion": definition_version,
            "target": {"objectType": object_type, "objectId": object_id},
            "expectedObjectVersion": expected_object_version,
            "params": params,
        }
    )


def branch_result(plan: ActionExecutionPlanResponse, edits: tuple[object, ...]) -> dict[str, object]:
    return {
        "branchId": plan_branch_id(plan),
        "planHash": plan["planHash"],
        "editCount": len(edits),
        "suppressedEffects": plan.get("suppressedEffects", []),
        "mainOntologyChanged": False,
    }


def plan_branch_id(plan: ActionExecutionPlanResponse) -> str:
    branch_id = plan.get("branchId")
    if isinstance(branch_id, str) and branch_id:
        return branch_id
    raise ValidationFailed("branch Action plan is missing branchId")


def plan_version(target: Mapping[str, object]) -> int:
    value = target.get("expectedObjectVersion")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValidationFailed("branch Action plan target version is invalid")


def branch_log_object(log_id: str, tenant_id: str, raw: object, index: int) -> ActionLogObjectRecord:
    edit = cast(Mapping[str, object], raw)
    return ActionLogObjectRecord(
        log_object_link_id=f"{log_id}_object_{index}",
        tenant_id=tenant_id,
        action_log_entry_id=log_id,
        object_edit_id=str(edit["id"]),
        object_type_id=str(edit["object_type_id"]),
        object_type_api_name=str(edit["object_type_api_name"]),
        object_id=str(edit["object_id"]),
        edit_type=str(edit["edit_kind"]),
        ordinal=index,
    )


def branch_run_payload(row: ActionRunRow, *, is_replay: bool) -> dict[str, object]:
    return {
        "actionRunId": row["id"],
        "actionApiName": row["action_type_api_name"],
        "status": row["status"],
        "branchId": row.get("branch_id"),
        "target": {"objectType": row["target_object_type_api_name"], "objectId": row["target_object_id"]},
        "planHash": row.get("plan_hash"),
        "result": row["result"],
        "error": row["error"],
        "idempotentReplay": is_replay,
    }


def branch_object_payload(
    branch_id: str,
    record: ObjectRecordRow,
    overlay: Mapping[str, object] | None,
    base: ObjectRecordRow | None,
) -> dict[str, object]:
    return {
        "branchId": branch_id,
        "objectType": record["object_type_api_name"],
        "objectId": record["object_id"],
        "objectVersion": record["object_version"],
        "properties": record["properties"],
        "isDeleted": record["deleted"],
        "baseObjectVersion": overlay["base_object_version"] if overlay else (base["object_version"] if base else None),
        "lastActionRunId": overlay["last_action_run_id"] if overlay else None,
    }
