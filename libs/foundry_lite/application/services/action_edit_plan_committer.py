"""Apply a validated Action IR v2 EditPlan atomically within one transaction.

This is the multi-object/multi-link generalization of ``ActionMutationUnitOfWork``
(which commits a single setProperty edit). It consumes an already-validated
``EditPlan`` and applies every object create/modify/delete and link create/delete
through the ``ActionRepository`` write primitives, records one ``object_edit`` per
object operation, emits outbox events, writes the masked audit summary, and CASes
the action run to its terminal state — all inside the caller's transaction, so any
optimistic-concurrency conflict raises ``ConflictDetected`` and rolls the whole
plan back (atomicity). It never performs external side effects; those are gated
before commit or dispatched after commit by the surrounding workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports import (
    ACTION_RUN_SUCCEEDED,
    LinkTypeRow,
    ObjectRecordRow,
    StatusTransition,
    TransactionContext,
)
from foundry_lite.application.ports.action_repository import (
    ActionRepository,
    ObjectCreateWrite,
    ObjectDeleteWrite,
    ObjectEditRecord,
    ObjectLinkDeleteWrite,
    ObjectLinkWrite,
    ObjectTargetUpdate,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_edit_plan_results import (
    ActionEditPlanResult,
    CommittedEdit,
    action_edit_audit_summary,
    action_edit_result_payload,
)
from foundry_lite.application.services.action_log_writer import record_action_log
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.action_revert_evidence import (
    created_object_revert_payload,
    deleted_object_revert_payload,
    link_revert_payload,
    property_revert_payload,
)
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
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


class CommitLinkTypeResolver(Protocol):
    """Resolve a link type api name to its persisted definition (endpoint types)."""

    def link_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> LinkTypeRow: ...


@dataclass(frozen=True)
class ActionEditPlanCommitter:
    action_repository: ActionRepository
    object_indexer: ActionObjectIndexer
    object_lookup: ActionObjectRecordLookup
    ontology_lookup: ActionOntologyLookup
    link_type_lookup: CommitLinkTypeResolver
    runtime: ActionRuntimeBoundary

    def commit_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_run_id: str,
        plan: EditPlan,
        contract: ActionDefinitionV3 | None = None,
        transition: StatusTransition = ACTION_RUN_SUCCEEDED,
    ) -> ActionEditPlanResult:
        create_edits = self._apply_creates(conn, ctx, action_run_id, plan)
        modify_edits = self._apply_modifies(conn, ctx, action_run_id, plan)
        delete_edits = self._apply_deletes(conn, ctx, action_run_id, plan)
        link_create_edits = self._apply_link_creates(conn, ctx, action_run_id, plan)
        link_delete_edits = self._apply_link_deletes(conn, ctx, action_run_id, plan)
        edits = (*create_edits, *modify_edits, *delete_edits, *link_create_edits, *link_delete_edits)
        # A createOrModify rule may create too, so derive created/deleted from the edits, not the plan buckets.
        created = tuple(edit.object_id for edit in edits if edit.operation == "create_object")
        deleted = tuple(edit.object_id for edit in edits if edit.operation == "delete_object")
        result = ActionEditPlanResult(
            action_run_id, transition.to_status, edits, created, deleted, len(link_create_edits), len(link_delete_edits)
        )
        self._finalize_run(conn, ctx, action_run_id, transition, result)
        if contract is not None:
            record_action_log(self.action_repository, conn, ctx, action_run_id, contract, edits)
        self._emit_events(conn, ctx, action_run_id, edits)
        self._audit_commit(conn, ctx, action_run_id, result)
        return result

    def _apply_creates(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, plan: EditPlan
    ) -> list[CommittedEdit]:
        return [
            self._create_one(conn, ctx, action_run_id, create.object_type, str(create.primary_key), create)
            for create in plan.objects_to_create
        ]

    def _create_one(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        object_type: str,
        object_id: str,
        source: ObjectCreate | ObjectModify,
    ) -> CommittedEdit:
        type_id = self.ontology_lookup._active_object_type(conn, ctx, object_type)["id"]
        properties = dict(_source_properties(source))
        inserted = self.action_repository.create_object_record(
            transaction=conn,
            record=_object_create_write(ctx.tenant_id, type_id, object_type, object_id, properties),
        )
        if not inserted:
            raise ConflictDetected(
                "action create targets an object that already exists",
                details={"objectType": object_type, "objectId": object_id},
            )
        edit_id = self._record_edit(
            conn,
            ctx,
            action_run_id,
            type_id,
            object_type,
            object_id,
            "create_object",
            properties,
            {},
            source.operation_key,
            created_object_revert_payload(),
        )
        return CommittedEdit(edit_id, object_type, object_id, "create_object")

    def _apply_modifies(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, plan: EditPlan
    ) -> list[CommittedEdit]:
        return [self._modify_one(conn, ctx, action_run_id, modify) for modify in plan.objects_to_modify]

    def _modify_one(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, modify: ObjectModify
    ) -> CommittedEdit:
        record = self.object_lookup._object_record(conn, ctx, modify.object_type, modify.object_id)
        if record is not None:
            return self._modify_existing(conn, ctx, action_run_id, modify, record)
        if modify.should_create_if_absent:
            return self._create_one(conn, ctx, action_run_id, modify.object_type, modify.object_id, modify)
        raise ConflictDetected(
            "action modify targets a missing object",
            details={"objectType": modify.object_type, "objectId": modify.object_id},
        )

    def _modify_existing(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        modify: ObjectModify,
        record: ObjectRecordRow,
    ) -> CommittedEdit:
        edit_properties = dict(record["edit_properties"])
        edit_properties.update(modify.patch)
        merged = self.object_indexer._merge_properties(
            conn, record["object_type_id"], record["base_properties"], edit_properties
        )
        updated = self.action_repository.update_object_target(
            transaction=conn,
            record=_object_target_update(ctx.tenant_id, record, modify, edit_properties, merged),
        )
        if not updated:
            raise ConflictDetected(
                "object version conflict during commit",
                details={"objectType": modify.object_type, "objectId": modify.object_id},
            )
        previous = {key: record["properties"].get(key) for key in modify.patch}
        edit_id = self._record_edit(
            conn,
            ctx,
            action_run_id,
            record["object_type_id"],
            modify.object_type,
            modify.object_id,
            "set_property",
            dict(modify.patch),
            previous,
            modify.operation_key,
            property_revert_payload(record, modify.patch),
        )
        return CommittedEdit(edit_id, modify.object_type, modify.object_id, "set_property")

    def _apply_deletes(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, plan: EditPlan
    ) -> list[CommittedEdit]:
        return [self._delete_one(conn, ctx, action_run_id, delete) for delete in plan.objects_to_delete]

    def _delete_one(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, delete: ObjectDelete
    ) -> CommittedEdit:
        record = self.object_lookup._object_record(conn, ctx, delete.object_type, delete.object_id)
        if record is None:
            raise ConflictDetected(
                "action delete targets a missing object",
                details={"objectType": delete.object_type, "objectId": delete.object_id},
            )
        deleted = self.action_repository.soft_delete_object_target(
            transaction=conn,
            record=ObjectDeleteWrite(
                object_record_id=record["id"],
                tenant_id=ctx.tenant_id,
                expected_object_version=delete.expected_version,
                deletion_reason=f"action:{action_run_id}",
                updated_at=_now(),
            ),
        )
        if not deleted:
            raise ConflictDetected(
                "object version conflict during delete",
                details={"objectType": delete.object_type, "objectId": delete.object_id},
            )
        edit_id = self._record_edit(
            conn,
            ctx,
            action_run_id,
            record["object_type_id"],
            delete.object_type,
            delete.object_id,
            "delete_object",
            {},
            dict(record["properties"]),
            delete.operation_key,
            deleted_object_revert_payload(record),
        )
        return CommittedEdit(edit_id, delete.object_type, delete.object_id, "delete_object")

    def _apply_link_creates(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, plan: EditPlan
    ) -> list[CommittedEdit]:
        edits: list[CommittedEdit] = []
        for link in plan.links_to_create:
            meta = self.link_type_lookup.link_type(conn, ctx, link.link_type)
            self.action_repository.create_object_link(transaction=conn, record=_link_write(ctx.tenant_id, link, meta))
            edits.append(self._record_link_edit(conn, ctx, action_run_id, link, meta, "create_link"))
        return edits

    def _apply_link_deletes(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, plan: EditPlan
    ) -> list[CommittedEdit]:
        edits: list[CommittedEdit] = []
        for link in plan.links_to_delete:
            meta = self.link_type_lookup.link_type(conn, ctx, link.link_type)
            deleted = self.action_repository.soft_delete_object_link(
                transaction=conn,
                record=ObjectLinkDeleteWrite(
                    tenant_id=ctx.tenant_id,
                    link_type_id=meta["id"],
                    from_object_id=link.source_object_id,
                    to_object_id=link.target_object_id,
                    deletion_reason=f"action:{action_run_id}",
                    updated_at=_now(),
                ),
            )
            if not deleted:
                raise ConflictDetected(
                    "link no longer exists during delete",
                    details={"linkType": link.link_type, "sourceObjectId": link.source_object_id},
                )
            edits.append(self._record_link_edit(conn, ctx, action_run_id, link, meta, "delete_link"))
        return edits

    def _record_link_edit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        link: LinkCreate | LinkDelete,
        meta: LinkTypeRow,
        edit_type: str,
    ) -> CommittedEdit:
        # Link operations are recorded in the unified edit log keyed on the source object so a
        # run's links are queryable and revertible; the patch carries the link identity.
        patch = {"linkType": link.link_type, "toApiName": meta["to_api_name"], "toObjectId": link.target_object_id}
        edit_id = self._record_edit(
            conn,
            ctx,
            action_run_id,
            meta["from_object_type_id"],
            meta["from_api_name"],
            link.source_object_id,
            edit_type,
            patch,
            {},
            link.operation_key,
            link_revert_payload(meta, link.link_type, link.source_object_id, link.target_object_id, edit_type),
        )
        return CommittedEdit(edit_id, meta["from_api_name"], link.source_object_id, edit_type)

    def _record_edit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        object_type_id: str,
        object_type: str,
        object_id: str,
        edit_type: str,
        patch: Mapping[str, object],
        previous: Mapping[str, object],
        operation_key: str,
        revert_payload: Mapping[str, object],
    ) -> str:
        edit_id = _new_id("edit")
        self.action_repository.insert_object_edit(
            transaction=conn,
            record=ObjectEditRecord(
                edit_id=edit_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                object_type_id=object_type_id,
                object_type_api_name=object_type,
                object_id=object_id,
                edit_type=edit_type,
                patch=dict(patch),
                previous_values=dict(previous),
                actor_user_id=ctx.actor_user_id,
                idempotency_key=operation_key,
                created_at=_now(),
                revert_payload=dict(revert_payload),
            ),
        )
        return edit_id

    def _finalize_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        transition: StatusTransition,
        result: ActionEditPlanResult,
    ) -> None:
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=transition,
            error=None,
            completed_at=_now(),
            result=action_edit_result_payload(result),
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")

    def _emit_events(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, edits: tuple[CommittedEdit, ...]
    ) -> None:
        self.runtime._outbox(
            conn,
            ctx,
            "action.run.committed",
            "action_run",
            action_run_id,
            {"actionRunId": action_run_id, "editCount": len(edits)},
            idempotency_key=f"action.run.committed:{action_run_id}",
            correlation_id=action_run_id,
        )
        for edit in edits:
            self.runtime._outbox(
                conn,
                ctx,
                "object.changed",
                "object",
                edit.object_id,
                {
                    "actionRunId": action_run_id,
                    "objectType": edit.object_type,
                    "objectId": edit.object_id,
                    "editId": edit.edit_id,
                    "operation": edit.operation,
                },
                idempotency_key=f"object.changed:{edit.edit_id}",
                correlation_id=action_run_id,
            )

    def _audit_commit(
        self, conn: TransactionContext, ctx: RequestContext, action_run_id: str, result: ActionEditPlanResult
    ) -> None:
        self.runtime._audit(
            conn,
            ctx,
            event_type="action.run.committed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref=action_edit_audit_summary(result),
            correlation_id=action_run_id,
        )


def _source_properties(source: ObjectCreate | ObjectModify) -> Mapping[str, object]:
    return source.properties if isinstance(source, ObjectCreate) else source.patch


def _object_create_write(
    tenant_id: str, type_id: str, object_type: str, object_id: str, properties: Mapping[str, object]
) -> ObjectCreateWrite:
    return ObjectCreateWrite(
        object_record_id=_new_id("obj"),
        tenant_id=tenant_id,
        object_type_id=type_id,
        object_type_api_name=object_type,
        object_id=object_id,
        properties=properties,
        created_at=_now(),
    )


def _object_target_update(
    tenant_id: str,
    record: ObjectRecordRow,
    modify: ObjectModify,
    edit_properties: Mapping[str, object],
    merged: Mapping[str, object],
) -> ObjectTargetUpdate:
    return ObjectTargetUpdate(
        object_record_id=record["id"],
        tenant_id=tenant_id,
        expected_object_version=modify.expected_version,
        edit_properties=edit_properties,
        properties=merged,
        next_object_version=modify.expected_version + 1,
        updated_at=_now(),
    )


def _link_write(tenant_id: str, link: LinkCreate, meta: LinkTypeRow) -> ObjectLinkWrite:
    return ObjectLinkWrite(
        link_record_id=_new_id("lnk"),
        tenant_id=tenant_id,
        link_type_id=meta["id"],
        link_type_api_name=link.link_type,
        from_object_type_id=meta["from_object_type_id"],
        from_api_name=meta["from_api_name"],
        from_object_id=link.source_object_id,
        to_object_type_id=meta["to_object_type_id"],
        to_api_name=meta["to_api_name"],
        to_object_id=link.target_object_id,
        updated_at=_now(),
    )
