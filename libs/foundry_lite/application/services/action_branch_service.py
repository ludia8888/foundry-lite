"""Plan, execute, and inspect Action runs in a main-isolated branch overlay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.action_log_types import ActionLogEntryRecord, ActionLogObjectRecord
from foundry_lite.application.action_types import ActionExecutionPlanResponse
from foundry_lite.application.ports import (
    ACTION_RUN_CONFLICT,
    ACTION_RUN_SUCCEEDED,
    ActionRepository,
    ActionRunRecord,
    ActionRunRow,
    ActionTypeRow,
    ObjectRecordRow,
    TransactionContext,
)
from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRepository, OntologyBranchRow
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.action_branch_committer import ActionBranchCommitter
from foundry_lite.application.services.action_branch_lookup import BranchObjectRecordLookup, composed_branch_record
from foundry_lite.application.services.action_planning_service import ActionPlanningService
from foundry_lite.application.services.action_protocols import (
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.action_execution_plan import edit_plan_from_manifest
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class ActionBranchService(CoreService):
    """Own the branch overlay boundary; no method writes main object tables."""

    required_dependencies = (
        "engine",
        "policy",
        "action_branch_repository",
        "action_repository",
        "ontology_branch_repository",
    )
    required_collaborators = (
        "action_planning_service",
        "object_records_service",
        "ontology_lookup_service",
        "runtime_service",
    )
    action_branch_repository: ActionBranchRepository
    action_repository: ActionRepository
    ontology_branch_repository: OntologyBranchRepository
    action_planning_service: ActionPlanningService
    object_records_service: ActionObjectRecordLookup
    ontology_lookup_service: ActionOntologyLookup
    runtime_service: ActionRuntimeBoundary

    def plan(
        self,
        action_api_name: str,
        *,
        branch_id: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext,
        is_dry_run: bool = False,
    ) -> ActionExecutionPlanResponse:
        self._require_branch_action(ctx, branch_id, action_api_name)
        return self.action_planning_service.plan_action_with_object_lookup(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            object_lookup=self._lookup(branch_id),
            branch_id=branch_id,
            ctx=ctx,
            is_dry_run=is_dry_run,
        )

    def execute(
        self,
        action_api_name: str,
        *,
        branch_id: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext,
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ValidationFailed("Idempotency-Key is required")
        self.runtime_service._require_write_traffic_open(
            ctx, operation="execute_branch_action", resource_type="ontology_branch", resource_id=branch_id
        )
        request_fingerprint = _branch_request_fingerprint(
            action_api_name, branch_id, object_type, object_id, expected_object_version, params
        )
        existing = self._existing_run(ctx, action_api_name, idempotency_key)
        if existing is not None:
            return self._replay(existing, request_fingerprint)
        plan = self.plan(
            action_api_name,
            branch_id=branch_id,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
        )
        row, is_created = self._ensure_run(ctx, action_api_name, branch_id, idempotency_key, request_fingerprint, plan)
        if not is_created:
            return self._replay(row, request_fingerprint)
        try:
            return self._commit(ctx, row, plan)
        except ConflictDetected as exc:
            self._record_conflict(ctx, row, exc)
            raise

    def get_object(self, branch_id: str, object_type: str, object_id: str, *, ctx: RequestContext) -> dict[str, object]:
        self._require_branch(ctx, branch_id)
        with self.engine.begin() as transaction:
            overlay = self.action_branch_repository.object_overlay(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
                object_type_api_name=object_type,
                object_id=object_id,
            )
            base = self.object_records_service._object_record(transaction, ctx, object_type, object_id)
            record = composed_branch_record(ctx, overlay, base, include_deleted=True)
        if record is None:
            raise NotFound("branch object not found")
        return _branch_object_payload(branch_id, record, overlay, base)

    def diff(self, branch_id: str, *, ctx: RequestContext) -> dict[str, object]:
        self._require_branch(ctx, branch_id)
        with self.engine.begin() as transaction:
            overlays = self.action_branch_repository.list_object_overlays(
                transaction=transaction, tenant_id=ctx.tenant_id, branch_id=branch_id
            )
            edits = self.action_branch_repository.list_edits(
                transaction=transaction, tenant_id=ctx.tenant_id, branch_id=branch_id
            )
            items = [self._diff_item(transaction, ctx, overlay, edits) for overlay in overlays]
        return {"branchId": branch_id, "items": items, "editCount": len(edits)}

    def _require_branch_action(self, ctx: RequestContext, branch_id: str, action_api_name: str) -> None:
        branch = self._require_branch(ctx, branch_id)
        with self.engine.begin() as transaction:
            active = self.ontology_lookup_service._active_ontology_version(transaction, ctx)
            if branch["base_version_id"] != active["id"]:
                raise ConflictDetected("ontology branch must be rebased before Action scenario execution")
            action_type = self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
        contract = compile_action_contract(action_type["definition"])
        if contract.branch_policy.get("enabled") is not True:
            raise ValidationFailed("Action definition does not allow branch execution")
        if contract.function is not None:
            raise ValidationFailed("function-backed branch Actions require an isolated function runtime")

    def _require_branch(self, ctx: RequestContext, branch_id: str) -> OntologyBranchRow:
        self.policy.require(ctx, "action:apply")
        with self.engine.begin() as transaction:
            branch = self.ontology_branch_repository.branch_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, branch_id=branch_id
            )
        if branch is None:
            raise NotFound("ontology branch not found", details={"branchId": branch_id})
        if branch["status"] != "open":
            raise ConflictDetected("ontology branch is not open", details={"branchId": branch_id})
        return branch

    def _lookup(self, branch_id: str) -> BranchObjectRecordLookup:
        return BranchObjectRecordLookup(self.object_records_service, self.action_branch_repository, branch_id)

    def _ensure_run(
        self,
        ctx: RequestContext,
        action_api_name: str,
        branch_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        plan: ActionExecutionPlanResponse,
    ) -> tuple[ActionRunRow, bool]:
        with self.engine.begin() as transaction:
            action_type = self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            target = plan["target"]
            concrete = self.ontology_lookup_service._active_object_type(transaction, ctx, str(target["objectType"]))
            record = _branch_run_record(
                ctx, action_type, concrete["id"], branch_id, idempotency_key, request_fingerprint, plan
            )
            existing = self.action_repository.insert_action_run_or_get_existing(transaction=transaction, record=record)
            row = existing or self.action_repository.action_run_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=record.action_run_id
            )
        if row is None:
            raise ConflictDetected("branch Action run idempotency winner disappeared")
        return row, existing is None

    def _existing_run(self, ctx: RequestContext, action_api_name: str, idempotency_key: str) -> ActionRunRow | None:
        with self.engine.begin() as transaction:
            action_type = self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            return self.action_repository.action_run_by_idempotency(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                action_type_id=action_type["id"],
                actor_user_id=ctx.actor_user_id,
                idempotency_key=idempotency_key,
            )

    def _replay(self, row: ActionRunRow, request_fingerprint: str) -> dict[str, object]:
        if row["request_fingerprint"] != request_fingerprint:
            raise ConflictDetected("branch Action idempotency key was reused for a different plan")
        return _branch_run_payload(row, is_replay=True)

    def _commit(self, ctx: RequestContext, row: ActionRunRow, plan: ActionExecutionPlanResponse) -> dict[str, object]:
        edit_plan = edit_plan_from_manifest(plan["editManifest"])
        branch_id = _plan_branch_id(plan)
        with self.engine.begin() as transaction:
            self._require_open_in_transaction(transaction, ctx, branch_id)
            edits = ActionBranchCommitter(
                self.action_branch_repository,
                self._lookup(branch_id),
                self.ontology_lookup_service,
                branch_id,
            ).commit(transaction, ctx, row["id"], edit_plan)
            result = _branch_result(plan, edits)
            updated = self.action_repository.update_action_run_terminal(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                action_run_id=row["id"],
                transition=ACTION_RUN_SUCCEEDED,
                error=None,
                completed_at=_now(),
                result=result,
            )
            if not updated:
                raise ConflictDetected("branch Action run changed before commit")
            self._write_log(transaction, ctx, row, plan, result, edits)
            self._emit_commit_evidence(transaction, ctx, row, plan, edits)
            committed = self.action_repository.action_run_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=row["id"]
            )
        if committed is None:
            raise ConflictDetected("committed branch Action run disappeared")
        return _branch_run_payload(committed, is_replay=False)

    def _require_open_in_transaction(
        self, transaction: TransactionContext, ctx: RequestContext, branch_id: str
    ) -> None:
        branch = self.ontology_branch_repository.branch_by_id(
            transaction=transaction, tenant_id=ctx.tenant_id, branch_id=branch_id
        )
        if branch is None or branch["status"] != "open":
            raise ConflictDetected("ontology branch closed before Action commit")

    def _write_log(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionRunRow,
        plan: ActionExecutionPlanResponse,
        result: dict[str, object],
        edits: tuple[object, ...],
    ) -> None:
        completed_at = _now()
        entry = ActionLogEntryRecord(
            log_entry_id=f"action_log_{row['id']}",
            tenant_id=ctx.tenant_id,
            action_run_id=row["id"],
            log_object_type_api_name=f"[LOG] {row['action_type_api_name']}",
            log_object_id=row["id"],
            action_type_id=row["action_type_id"],
            action_type_api_name=row["action_type_api_name"],
            definition_version=str(plan["definitionFingerprint"]),
            actor_user_id=row["actor_user_id"],
            status="succeeded",
            parameters=row["parameters"],
            result=result,
            branch_id=_plan_branch_id(plan),
            plan_hash=str(plan["planHash"]),
            approval_id=None,
            revert_allowed=False,
            created_at=row["created_at"],
            completed_at=completed_at,
        )
        objects = tuple(
            _branch_log_object(entry.log_entry_id, ctx.tenant_id, edit, index) for index, edit in enumerate(edits)
        )
        self.action_repository.insert_action_log(transaction=transaction, entry=entry, objects=objects)

    def _emit_commit_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionRunRow,
        plan: ActionExecutionPlanResponse,
        edits: tuple[object, ...],
    ) -> None:
        evidence = {"branchId": _plan_branch_id(plan), "planHash": plan["planHash"], "editCount": len(edits)}
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="action.branch.committed",
            resource_type="action_run",
            resource_id=row["id"],
            action="apply",
            after_ref=evidence,
            correlation_id=row["id"],
        )
        self.runtime_service._outbox(
            transaction,
            ctx,
            "action.branch.committed",
            "action_run",
            row["id"],
            evidence,
            idempotency_key=f"action.branch.committed:{row['id']}",
            correlation_id=row["id"],
        )

    def _record_conflict(self, ctx: RequestContext, row: ActionRunRow, exc: ConflictDetected) -> None:
        with self.engine.begin() as transaction:
            self.action_repository.update_action_run_terminal(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                action_run_id=row["id"],
                transition=ACTION_RUN_CONFLICT,
                error={"kind": "conflict", "message": str(exc)},
                completed_at=_now(),
            )

    def _diff_item(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        overlay: Mapping[str, object],
        edits: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        object_type = str(overlay["object_type_api_name"])
        object_id = str(overlay["object_id"])
        base = self.object_records_service._object_record(transaction, ctx, object_type, object_id)
        matching = [
            item for item in edits if item["object_type_api_name"] == object_type and item["object_id"] == object_id
        ]
        original = matching[0]["before"] if matching else {}
        current_version = base["object_version"] if base else None
        return {
            "objectType": object_type,
            "objectId": object_id,
            "base": original,
            "overlay": overlay["properties"],
            "isDeleted": overlay["deleted"],
            "baseObjectVersion": overlay["base_object_version"],
            "currentMainVersion": current_version,
            "hasMainDrift": current_version != overlay["base_object_version"] if base else False,
            "lastActionRunId": overlay["last_action_run_id"],
        }


def _branch_run_record(
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
        expected_object_version=_plan_version(target),
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


def _branch_request_fingerprint(
    action_api_name: str,
    branch_id: str,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
) -> str:
    return _json_hash(
        {
            "actionApiName": action_api_name,
            "branchId": branch_id,
            "target": {"objectType": object_type, "objectId": object_id},
            "expectedObjectVersion": expected_object_version,
            "params": params,
        }
    )


def _branch_result(plan: ActionExecutionPlanResponse, edits: tuple[object, ...]) -> dict[str, object]:
    return {
        "branchId": _plan_branch_id(plan),
        "planHash": plan["planHash"],
        "editCount": len(edits),
        "suppressedEffects": plan.get("suppressedEffects", []),
        "mainOntologyChanged": False,
    }


def _plan_branch_id(plan: ActionExecutionPlanResponse) -> str:
    branch_id = plan.get("branchId")
    if isinstance(branch_id, str) and branch_id:
        return branch_id
    raise ValidationFailed("branch Action plan is missing branchId")


def _plan_version(target: Mapping[str, object]) -> int:
    value = target.get("expectedObjectVersion")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValidationFailed("branch Action plan target version is invalid")


def _branch_log_object(log_id: str, tenant_id: str, raw: object, index: int) -> ActionLogObjectRecord:
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


def _branch_run_payload(row: ActionRunRow, *, is_replay: bool) -> dict[str, object]:
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


def _branch_object_payload(
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
