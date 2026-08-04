"""Atomic committer for branch-only Action object overlays."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_branch_types import (
    ActionBranchEditRecord,
    ActionBranchEditRow,
    ActionBranchObjectRow,
    ActionBranchObjectWrite,
)
from foundry_lite.application.ports import ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_branch_lookup import BranchObjectRecordLookup
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, ObjectCreate, ObjectDelete, ObjectModify
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


@dataclass(frozen=True, slots=True)
class ActionBranchCommitter:
    repository: ActionBranchRepository
    lookup: BranchObjectRecordLookup
    ontology: ActionOntologyLookup
    branch_id: str

    def commit(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        plan: EditPlan,
    ) -> tuple[ActionBranchEditRow, ...]:
        if plan.links_to_create or plan.links_to_delete:
            raise ValidationFailed("branch Action link overlays are not enabled for this release")
        edits: list[ActionBranchEditRow] = []
        operations: tuple[ObjectCreate | ObjectModify | ObjectDelete, ...] = (
            *plan.objects_to_create,
            *plan.objects_to_modify,
            *plan.objects_to_delete,
        )
        for ordinal, operation in enumerate(operations):
            edits.append(self._apply_one(transaction, ctx, action_run_id, ordinal, operation))
        return tuple(edits)

    def _apply_one(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        ordinal: int,
        operation: ObjectCreate | ObjectModify | ObjectDelete,
    ) -> ActionBranchEditRow:
        object_type = operation.object_type
        object_id = str(operation.primary_key) if isinstance(operation, ObjectCreate) else operation.object_id
        current = self.lookup._object_record(transaction, ctx, object_type, object_id)
        existing = self.repository.object_overlay(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            branch_id=self.branch_id,
            object_type_api_name=object_type,
            object_id=object_id,
        )
        before, after, is_deleted = _next_state(operation, current)
        stored = self._store(transaction, ctx, action_run_id, operation, object_id, existing, after, is_deleted)
        return self._record_edit(transaction, ctx, action_run_id, ordinal, operation, stored, before, after)

    def _store(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        operation: ObjectCreate | ObjectModify | ObjectDelete,
        object_id: str,
        existing: ActionBranchObjectRow | None,
        after: dict[str, object],
        is_deleted: bool,
    ) -> ActionBranchObjectRow:
        object_type = operation.object_type
        type_row = self.ontology._active_object_type(transaction, ctx, object_type)
        now = _now()
        base_version, next_version = _overlay_versions(operation, existing)
        stored = self.repository.store_object_overlay(
            transaction=transaction,
            record=ActionBranchObjectWrite(
                overlay_id=_new_id("abranch_obj"),
                tenant_id=ctx.tenant_id,
                branch_id=self.branch_id,
                object_type_id=type_row["id"],
                object_type_api_name=object_type,
                object_id=object_id,
                base_object_version=base_version,
                expected_overlay_version=existing["overlay_version"] if existing else None,
                overlay_version=next_version,
                properties=after,
                is_deleted=is_deleted,
                action_run_id=action_run_id,
                created_at=existing["created_at"] if existing else now,
                updated_at=now,
            ),
        )
        if stored is None:
            raise ConflictDetected("branch object overlay changed concurrently")
        return stored

    def _record_edit(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        ordinal: int,
        operation: ObjectCreate | ObjectModify | ObjectDelete,
        stored: ActionBranchObjectRow,
        before: dict[str, object],
        after: dict[str, object],
    ) -> ActionBranchEditRow:
        inserted = self.repository.insert_edit(
            transaction=transaction,
            record=ActionBranchEditRecord(
                edit_id=_new_id("abranch_edit"),
                tenant_id=ctx.tenant_id,
                branch_id=self.branch_id,
                action_run_id=action_run_id,
                operation_key=operation.operation_key,
                ordinal=ordinal,
                edit_kind=_edit_kind(operation),
                object_type_id=stored["object_type_id"],
                object_type_api_name=stored["object_type_api_name"],
                object_id=stored["object_id"],
                before=before,
                after=after,
                created_at=_now(),
            ),
        )
        if inserted is None:
            raise ConflictDetected("branch edit operation was already committed")
        return inserted


def _next_state(
    operation: ObjectCreate | ObjectModify | ObjectDelete,
    current: ObjectRecordRow | None,
) -> tuple[dict[str, object], dict[str, object], bool]:
    if isinstance(operation, ObjectCreate):
        if current is not None:
            raise ConflictDetected("branch create targets an existing object")
        return {}, dict(operation.properties), False
    if current is None or current["object_version"] != operation.expected_version:
        raise ConflictDetected("branch Action object version changed since planning")
    before = dict(current["properties"])
    if isinstance(operation, ObjectDelete):
        return before, before, True
    return before, {**before, **operation.patch}, False


def _expected_version(operation: ObjectModify | ObjectDelete) -> int:
    return operation.expected_version


def _overlay_versions(
    operation: ObjectCreate | ObjectModify | ObjectDelete,
    existing: ActionBranchObjectRow | None,
) -> tuple[int | None, int]:
    if existing is not None:
        return existing["base_object_version"], existing["overlay_version"] + 1
    if isinstance(operation, ObjectCreate):
        return None, 1
    base_version = _expected_version(operation)
    return base_version, base_version + 1


def _edit_kind(operation: ObjectCreate | ObjectModify | ObjectDelete) -> str:
    if isinstance(operation, ObjectCreate):
        return "create_object"
    if isinstance(operation, ObjectDelete):
        return "delete_object"
    return "set_property"
