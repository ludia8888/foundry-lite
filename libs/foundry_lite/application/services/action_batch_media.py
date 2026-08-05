"""Media parameter normalization and binding plans for atomic Action batches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from foundry_lite.application.action_types import ActionBatchApplyCommand
from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.services.action_batch_helpers import batch_request_fingerprint
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, compile_action_contract
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, ObjectModify
from foundry_lite.domain.context import RequestContext


class ActionMediaBatchBoundary(Protocol):
    def resolve_parameters(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        values: Mapping[str, object],
    ) -> dict[str, object]: ...

    def bind_plan_references(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        plan: EditPlan,
    ) -> None: ...


def resolved_batch_media_command(
    service: ActionMediaBatchBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    action_type: ActionTypeRow,
    command: ActionBatchApplyCommand,
) -> ActionBatchApplyCommand:
    params = service.resolve_parameters(conn, ctx, compile_action_contract(action_type["definition"]), command.params)
    fingerprint = batch_request_fingerprint(
        action_api_name=command.action_api_name,
        object_type=command.object_type,
        targets=command.targets,
        params=params,
    )
    return replace(command, params=params, request_fingerprint=fingerprint)


def batch_media_edit_plan(
    command: ActionBatchApplyCommand,
    records: list[ObjectRecordRow],
    patch: Mapping[str, object],
) -> EditPlan:
    return EditPlan(
        objects_to_modify=tuple(
            ObjectModify(
                operation_key=f"batch:{record['object_id']}",
                rule_id="batch",
                object_type=command.object_type,
                object_id=record["object_id"],
                expected_version=record["object_version"],
                patch=patch,
            )
            for record in records
        )
    )
