"""Inverse object and link mutations used by atomic Action revert."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_log_types import ObjectRestoreWrite
from foundry_lite.application.ports import ActionRepository, ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.action_repository import (
    ObjectDeleteWrite,
    ObjectEditRecord,
    ObjectEditRow,
    ObjectLinkDeleteWrite,
    ObjectLinkWrite,
    ObjectTargetUpdate,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_edit_plan_results import CommittedEdit
from foundry_lite.application.services.action_protocols import ActionObjectIndexer
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation


def apply_inverse_edit(
    repository: ActionRepository,
    object_indexer: ActionObjectIndexer,
    transaction: TransactionContext,
    ctx: RequestContext,
    revert_run_id: str,
    edit: ObjectEditRow,
) -> CommittedEdit:
    """Apply one captured inverse operation and append a new non-revertible edit row."""
    payload = _mapping(edit["revert_payload"])
    operation = _text(payload, "operation")
    if operation == "set_property":
        _restore_properties(repository, object_indexer, transaction, ctx, edit, payload)
    elif operation == "create_object":
        _delete_created_object(repository, transaction, ctx, edit, payload, revert_run_id)
    elif operation == "delete_object":
        _restore_deleted_object(repository, transaction, ctx, edit, payload)
    elif operation in {"create_link", "delete_link"}:
        _reverse_link(repository, transaction, ctx, payload, operation, revert_run_id)
    else:
        raise InvariantViolation("Action edit has unsupported revert evidence", details={"operation": operation})
    return _record_inverse_edit(repository, transaction, ctx, revert_run_id, edit)


def _restore_properties(
    repository: ActionRepository,
    object_indexer: ActionObjectIndexer,
    transaction: TransactionContext,
    ctx: RequestContext,
    edit: ObjectEditRow,
    payload: Mapping[str, object],
) -> None:
    record = _required_target(repository, transaction, ctx, edit)
    edit_properties = dict(record["edit_properties"])
    for key, raw in _mapping(payload.get("properties")).items():
        previous = _mapping(raw)
        if previous.get("wasPresent") is True:
            edit_properties[key] = previous.get("value")
        else:
            edit_properties.pop(key, None)
    merged = object_indexer._merge_properties(
        transaction, record["object_type_id"], record["base_properties"], edit_properties
    )
    updated = repository.update_object_target(
        transaction=transaction,
        record=ObjectTargetUpdate(
            object_record_id=record["id"],
            tenant_id=ctx.tenant_id,
            expected_object_version=record["object_version"],
            edit_properties=edit_properties,
            properties=merged,
            next_object_version=record["object_version"] + 1,
            updated_at=_now(),
        ),
    )
    if not updated:
        raise ConflictDetected("object changed during Action revert")


def _delete_created_object(
    repository: ActionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    edit: ObjectEditRow,
    payload: Mapping[str, object],
    revert_run_id: str,
) -> None:
    record = _required_target(repository, transaction, ctx, edit)
    deleted = repository.soft_delete_object_target(
        transaction=transaction,
        record=ObjectDeleteWrite(
            object_record_id=record["id"],
            tenant_id=ctx.tenant_id,
            expected_object_version=_integer(payload, "committedObjectVersion"),
            deletion_reason=f"revert:{revert_run_id}",
            updated_at=_now(),
        ),
    )
    if not deleted:
        raise ConflictDetected("created object changed before Action revert")


def _restore_deleted_object(
    repository: ActionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    edit: ObjectEditRow,
    payload: Mapping[str, object],
) -> None:
    record = _required_target(repository, transaction, ctx, edit)
    restored = repository.restore_object_target(
        transaction=transaction,
        record=ObjectRestoreWrite(
            object_record_id=record["id"],
            tenant_id=ctx.tenant_id,
            expected_object_version=_integer(payload, "committedObjectVersion"),
            updated_at=_now(),
        ),
    )
    if not restored:
        raise ConflictDetected("deleted object changed before Action revert")


def _reverse_link(
    repository: ActionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    payload: Mapping[str, object],
    operation: str,
    revert_run_id: str,
) -> None:
    if operation == "create_link":
        deleted = repository.soft_delete_object_link(
            transaction=transaction,
            record=ObjectLinkDeleteWrite(
                tenant_id=ctx.tenant_id,
                link_type_id=_text(payload, "linkTypeId"),
                from_object_id=_text(payload, "fromObjectId"),
                to_object_id=_text(payload, "toObjectId"),
                deletion_reason=f"revert:{revert_run_id}",
                updated_at=_now(),
            ),
        )
        if not deleted:
            raise ConflictDetected("link changed before Action revert")
        return
    repository.create_object_link(transaction=transaction, record=_link_write(ctx.tenant_id, payload))


def _link_write(tenant_id: str, payload: Mapping[str, object]) -> ObjectLinkWrite:
    return ObjectLinkWrite(
        link_record_id=_new_id("lnk"),
        tenant_id=tenant_id,
        link_type_id=_text(payload, "linkTypeId"),
        link_type_api_name=_text(payload, "linkType"),
        from_object_type_id=_text(payload, "fromObjectTypeId"),
        from_api_name=_text(payload, "fromApiName"),
        from_object_id=_text(payload, "fromObjectId"),
        to_object_type_id=_text(payload, "toObjectTypeId"),
        to_api_name=_text(payload, "toApiName"),
        to_object_id=_text(payload, "toObjectId"),
        updated_at=_now(),
    )


def _record_inverse_edit(
    repository: ActionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    revert_run_id: str,
    original: ObjectEditRow,
) -> CommittedEdit:
    edit_id = _new_id("edit")
    operation = f"revert_{original['edit_type']}"
    repository.insert_object_edit(
        transaction=transaction,
        record=ObjectEditRecord(
            edit_id=edit_id,
            tenant_id=ctx.tenant_id,
            action_run_id=revert_run_id,
            object_type_id=original["object_type_id"],
            object_type_api_name=original["object_type_api_name"],
            object_id=original["object_id"],
            edit_type=operation,
            patch={"revertOfEditId": original["id"]},
            previous_values={},
            actor_user_id=ctx.actor_user_id,
            idempotency_key=f"{revert_run_id}:{original['id']}",
            created_at=_now(),
            revert_payload=None,
        ),
    )
    return CommittedEdit(edit_id, original["object_type_api_name"], original["object_id"], operation)


def _required_target(
    repository: ActionRepository, transaction: TransactionContext, ctx: RequestContext, edit: ObjectEditRow
) -> ObjectRecordRow:
    row = repository.object_target_for_revert(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        object_type_id=edit["object_type_id"],
        object_id=edit["object_id"],
    )
    if row is None:
        raise ConflictDetected("Action revert target no longer exists")
    return row


def _mapping(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise InvariantViolation("Action edit is missing structured revert evidence")
    return raw


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise InvariantViolation("Action revert text evidence is invalid", details={"field": key})
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise InvariantViolation("Action revert version evidence is invalid", details={"field": key})
    return value
