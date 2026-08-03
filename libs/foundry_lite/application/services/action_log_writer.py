"""Atomic normalized Action log writer shared by sync and durable commits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_log_types import ActionLogEntryRecord, ActionLogObjectRecord
from foundry_lite.application.ports import ActionRepository, TransactionContext
from foundry_lite.application.ports.action_repository import ActionRunRow
from foundry_lite.application.services.action_edit_plan_results import CommittedEdit
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, action_contract_fingerprint
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


def record_action_log(
    repository: ActionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    contract: ActionDefinitionV3,
    edits: Sequence[CommittedEdit],
    *,
    definition_version_override: str | None = None,
    is_revert_allowed_override: bool | None = None,
) -> str:
    """Write one Action log and every edited-object link in the commit transaction."""
    run = repository.action_run_by_id(transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=action_run_id)
    if run is None or run["status"] != "succeeded" or run["completed_at"] is None:
        raise InvariantViolation("successful Action run is required before writing its Action log")
    entry = _log_entry(
        ctx,
        run,
        contract,
        definition_version_override,
        is_revert_allowed_override,
        run["completed_at"],
    )
    objects = _log_objects(ctx.tenant_id, entry.log_entry_id, repository, transaction, action_run_id, edits)
    inserted = repository.insert_action_log(transaction=transaction, entry=entry, objects=objects)
    if inserted is None:
        existing = repository.action_log_by_run_id(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=action_run_id
        )
        if existing is None:
            raise InvariantViolation("Action log idempotency winner disappeared")
    return entry.log_entry_id


def _log_entry(
    ctx: RequestContext,
    run: ActionRunRow,
    contract: ActionDefinitionV3,
    definition_version_override: str | None,
    is_revert_allowed_override: bool | None,
    completed_at: str,
) -> ActionLogEntryRecord:
    execution_plan = _mapping(run.get("execution_plan"))
    return ActionLogEntryRecord(
        log_entry_id=f"action_log_{run['id']}",
        tenant_id=ctx.tenant_id,
        action_run_id=run["id"],
        log_object_type_api_name=f"[LOG] {run['action_type_api_name']}",
        log_object_id=run["id"],
        action_type_id=run["action_type_id"],
        action_type_api_name=run["action_type_api_name"],
        definition_version=definition_version_override or action_contract_fingerprint(contract),
        actor_user_id=run["actor_user_id"],
        status=run["status"],
        parameters=run["parameters"],
        result=run["result"] or {},
        branch_id=_optional_text(execution_plan.get("branchId")),
        plan_hash=_optional_text(run.get("plan_hash")),
        approval_id=_approval_id(execution_plan),
        revert_allowed=(
            is_revert_allowed_override
            if is_revert_allowed_override is not None
            else contract.revert_policy.get("enabled") is True
        ),
        created_at=run["created_at"],
        completed_at=completed_at,
    )


def _log_objects(
    tenant_id: str,
    log_entry_id: str,
    repository: ActionRepository,
    transaction: TransactionContext,
    action_run_id: str,
    edits: Sequence[CommittedEdit],
) -> tuple[ActionLogObjectRecord, ...]:
    persisted = {
        row["id"]: row
        for row in repository.object_edits_for_run(
            transaction=transaction, tenant_id=tenant_id, action_run_id=action_run_id
        )
    }
    return tuple(
        ActionLogObjectRecord(
            log_object_link_id=f"{log_entry_id}_object_{ordinal}",
            tenant_id=tenant_id,
            action_log_entry_id=log_entry_id,
            object_edit_id=edit.edit_id,
            object_type_id=persisted[edit.edit_id]["object_type_id"],
            object_type_api_name=edit.object_type,
            object_id=edit.object_id,
            edit_type=edit.operation,
            ordinal=ordinal,
        )
        for ordinal, edit in enumerate(edits)
    )


def _approval_id(execution_plan: Mapping[str, object]) -> str | None:
    approval = _mapping(execution_plan.get("approval"))
    return _optional_text(approval.get("approvalId"))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
