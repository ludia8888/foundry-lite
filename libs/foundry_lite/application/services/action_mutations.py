from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.ports import (
    ACTION_RUN_SUCCEEDED,
    ActionTypeRow,
    ObjectRecordRow,
    StatusTransition,
    TransactionContext,
)
from foundry_lite.application.ports.action_repository import (
    ActionRepository,
    ObjectEditRecord,
    ObjectPatch,
    ObjectProperties,
    ObjectTargetUpdate,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_helpers import (
    action_patch,
    action_success_response,
    previous_action_values,
)
from foundry_lite.application.services.action_protocols import ActionObjectIndexer, ActionRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class ActionMutationUnitOfWork:
    action_repository: ActionRepository
    object_indexing_service: ActionObjectIndexer
    runtime_service: ActionRuntimeBoundary
    policy: PolicyService

    def commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        record: ObjectRecordRow,
        params: Mapping[str, object],
        idempotency_key: str,
        transition: StatusTransition = ACTION_RUN_SUCCEEDED,
    ) -> ActionApplyResponse:
        patch = dict(action_patch(action_type, params))
        previous_values = dict(previous_action_values(record, patch))
        self._update_action_target(conn, record, patch)
        edit_id = self._insert_object_edit(conn, ctx, action_run_id, record, patch, previous_values, idempotency_key)
        response = action_success_response(action_run_id, record, edit_id, patch)
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=transition,
            error=None,
            completed_at=_now(),
            result=response,
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")
        self._publish_action_commit_events(conn, ctx, action_run_id, record, edit_id)
        self._audit_action_commit(conn, ctx, action_run_id, record, previous_values, patch, edit_id)
        return response

    def _update_action_target(
        self,
        conn: TransactionContext,
        record: ObjectRecordRow,
        patch: ObjectPatch,
    ) -> None:
        edit_properties = dict(record["edit_properties"])
        edit_properties.update(patch)
        current = self.object_indexing_service._merge_properties(
            conn, record["object_type_id"], record["base_properties"], edit_properties
        )
        updated = self.action_repository.update_object_target(
            transaction=conn,
            record=ObjectTargetUpdate(
                object_record_id=record["id"],
                tenant_id=record["tenant_id"],
                expected_object_version=record["object_version"],
                edit_properties=edit_properties,
                properties=current,
                next_object_version=record["object_version"] + 1,
                updated_at=_now(),
            ),
        )
        if not updated:
            raise ConflictDetected("object version conflict during commit")

    def _insert_object_edit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        record: ObjectRecordRow,
        patch: ObjectPatch,
        previous_values: ObjectProperties,
        idempotency_key: str,
    ) -> str:
        edit_id = _new_id("edit")
        self.action_repository.insert_object_edit(
            transaction=conn,
            record=ObjectEditRecord(
                edit_id=edit_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                object_type_id=record["object_type_id"],
                object_type_api_name=record["object_type_api_name"],
                object_id=record["object_id"],
                edit_type="set_property",
                patch=dict(patch),
                previous_values=dict(previous_values),
                actor_user_id=ctx.actor_user_id,
                idempotency_key=idempotency_key,
                created_at=_now(),
            ),
        )
        return edit_id

    def _publish_action_commit_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        record: ObjectRecordRow,
        edit_id: str,
    ) -> None:
        payload = {
            "actionRunId": action_run_id,
            "objectType": record["object_type_api_name"],
            "objectId": record["object_id"],
            "editId": edit_id,
        }
        for event_type in ["action.run.committed", "object.edit.committed", "object.changed"]:
            aggregate_type = "action_run" if event_type == "action.run.committed" else "object"
            aggregate_id = action_run_id if event_type == "action.run.committed" else record["object_id"]
            outbox_event_id = self.runtime_service._outbox(
                conn,
                ctx,
                event_type,
                aggregate_type,
                aggregate_id,
                payload,
                idempotency_key=f"{event_type}:{action_run_id}",
                correlation_id=action_run_id,
            )
            if outbox_event_id is not None:
                self.runtime_service._run_relation(
                    conn,
                    ctx,
                    source_run_type="action",
                    source_run_id=action_run_id,
                    target_run_type="outbox",
                    target_run_id=outbox_event_id,
                    relation="emitted",
                    resource_type="outbox_event",
                    resource_id=outbox_event_id,
                    metadata={
                        "eventType": event_type,
                        "aggregateType": aggregate_type,
                        "aggregateId": aggregate_id,
                        "objectEditId": edit_id,
                    },
                )

    def _audit_action_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        record: ObjectRecordRow,
        previous_values: ObjectProperties,
        patch: ObjectPatch,
        edit_id: str,
    ) -> None:
        object_type = record["object_type_api_name"]
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.committed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            before_ref=self.policy.mask_sensitive_properties(ctx, object_type, dict(previous_values)),
            after_ref={
                "patch": self.policy.mask_sensitive_properties(ctx, object_type, dict(patch)),
                "object_edit_id": edit_id,
            },
            correlation_id=action_run_id,
        )
