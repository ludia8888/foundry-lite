"""Atomic committer for branch-only Action object overlays."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_branch_types import (
    ActionBranchEditRecord,
    ActionBranchEditRow,
    ActionBranchLinkRow,
    ActionBranchLinkWrite,
    ActionBranchObjectRow,
    ActionBranchObjectWrite,
)
from foundry_lite.application.ports import LinkTypeRow, ObjectLinkRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_branch_lookup import BranchObjectRecordLookup
from foundry_lite.application.services.action_criteria_resolution import ActionCriteriaCommitVerifier
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.domain.action_runtime.edit_plan import (
    EditPlan,
    LinkCreate,
    LinkDelete,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

__all__ = ["ActionBranchCommitter", "ActionCriteriaCommitVerifier"]


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
        edits: list[ActionBranchEditRow] = []
        operations: tuple[ObjectCreate | ObjectModify | ObjectDelete, ...] = (
            *plan.objects_to_create,
            *plan.objects_to_modify,
            *plan.objects_to_delete,
        )
        for ordinal, operation in enumerate(operations):
            edits.append(self._apply_one(transaction, ctx, action_run_id, ordinal, operation))
        link_operations: tuple[LinkCreate | LinkDelete, ...] = (*plan.links_to_create, *plan.links_to_delete)
        for ordinal, link_operation in enumerate(link_operations, start=len(operations)):
            edits.append(self._apply_link(transaction, ctx, action_run_id, ordinal, link_operation))
        return tuple(edits)

    def _apply_link(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        ordinal: int,
        operation: LinkCreate | LinkDelete,
    ) -> ActionBranchEditRow:
        meta = self.ontology.link_type(transaction, ctx, operation.link_type)
        existing = self._link_overlay(transaction, ctx, operation)
        base = self._base_link(transaction, ctx, operation)
        is_active = not existing["deleted"] if existing is not None else base is not None
        _validate_link_transition(operation, is_active)
        stored = self._store_link(transaction, ctx, action_run_id, operation, meta, existing, base)
        return self._record_link_edit(transaction, ctx, action_run_id, ordinal, operation, meta, stored, is_active)

    def _link_overlay(
        self, transaction: TransactionContext, ctx: RequestContext, operation: LinkCreate | LinkDelete
    ) -> ActionBranchLinkRow | None:
        return self.repository.link_overlay(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            branch_id=self.branch_id,
            link_type_api_name=operation.link_type,
            from_object_id=operation.source_object_id,
            to_object_id=operation.target_object_id,
        )

    def _base_link(
        self, transaction: TransactionContext, ctx: RequestContext, operation: LinkCreate | LinkDelete
    ) -> ObjectLinkRow | None:
        return self.repository.active_base_link(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            link_type_api_name=operation.link_type,
            from_object_id=operation.source_object_id,
            to_object_id=operation.target_object_id,
        )

    def _store_link(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        operation: LinkCreate | LinkDelete,
        meta: LinkTypeRow,
        existing: ActionBranchLinkRow | None,
        base: ObjectLinkRow | None,
    ) -> ActionBranchLinkRow:
        now = _now()
        base_version, next_version = _link_overlay_versions(existing, base)
        stored = self.repository.store_link_overlay(
            transaction=transaction,
            record=_link_write(
                ctx, self.branch_id, action_run_id, operation, meta, existing, base_version, next_version, now
            ),
        )
        if stored is None:
            raise ConflictDetected("branch link overlay changed concurrently")
        return stored

    def _record_link_edit(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        ordinal: int,
        operation: LinkCreate | LinkDelete,
        meta: LinkTypeRow,
        stored: ActionBranchLinkRow,
        is_previously_active: bool,
    ) -> ActionBranchEditRow:
        identity = _link_identity(operation, meta)
        inserted = self.repository.insert_edit(
            transaction=transaction,
            record=ActionBranchEditRecord(
                edit_id=_new_id("abranch_edit"),
                tenant_id=ctx.tenant_id,
                branch_id=self.branch_id,
                action_run_id=action_run_id,
                operation_key=operation.operation_key,
                ordinal=ordinal,
                edit_kind="delete_link" if isinstance(operation, LinkDelete) else "create_link",
                object_type_id=meta["from_object_type_id"],
                object_type_api_name=meta["from_api_name"],
                object_id=operation.source_object_id,
                before={**identity, "exists": is_previously_active},
                after={**identity, "exists": not stored["deleted"]},
                created_at=_now(),
            ),
        )
        if inserted is None:
            raise ConflictDetected("branch link edit operation was already committed")
        return inserted

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


def _validate_link_transition(operation: LinkCreate | LinkDelete, is_active: bool) -> None:
    if isinstance(operation, LinkCreate) and is_active:
        raise ConflictDetected("branch create targets an existing link")
    if isinstance(operation, LinkDelete) and not is_active:
        raise ConflictDetected("branch delete targets a missing link")


def _link_overlay_versions(existing: ActionBranchLinkRow | None, base: ObjectLinkRow | None) -> tuple[int | None, int]:
    if existing is not None:
        return existing["base_link_version"], existing["overlay_version"] + 1
    base_version = base["link_version"] if base is not None else None
    return base_version, (base_version or 0) + 1


def _link_write(
    ctx: RequestContext,
    branch_id: str,
    action_run_id: str,
    operation: LinkCreate | LinkDelete,
    meta: LinkTypeRow,
    existing: ActionBranchLinkRow | None,
    base_version: int | None,
    next_version: int,
    now: str,
) -> ActionBranchLinkWrite:
    return ActionBranchLinkWrite(
        overlay_id=_new_id("abranch_link"),
        tenant_id=ctx.tenant_id,
        branch_id=branch_id,
        link_type_id=meta["id"],
        link_type_api_name=operation.link_type,
        from_object_type_id=meta["from_object_type_id"],
        from_api_name=meta["from_api_name"],
        from_object_id=operation.source_object_id,
        to_object_type_id=meta["to_object_type_id"],
        to_api_name=meta["to_api_name"],
        to_object_id=operation.target_object_id,
        base_link_version=base_version,
        expected_overlay_version=existing["overlay_version"] if existing else None,
        overlay_version=next_version,
        is_deleted=isinstance(operation, LinkDelete),
        action_run_id=action_run_id,
        created_at=existing["created_at"] if existing else now,
        updated_at=now,
    )


def _link_identity(operation: LinkCreate | LinkDelete, meta: LinkTypeRow) -> dict[str, object]:
    return {
        "linkType": operation.link_type,
        "fromApiName": meta["from_api_name"],
        "fromObjectId": operation.source_object_id,
        "toApiName": meta["to_api_name"],
        "toObjectId": operation.target_object_id,
    }
