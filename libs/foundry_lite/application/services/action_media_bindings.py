"""Atomic binding of Action media edits to immutable Media Plane versions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports.action_repository import ActionRepository, ObjectEditRow
from foundry_lite.application.ports.media_reference_binding_repository import (
    AttachmentHolderAssociationRecord,
    MediaReferenceBindingRecord,
    MediaReferenceBindingRepository,
)
from foundry_lite.application.ports.media_repository import MediaItemRecord, MediaItemVersionRecord, MediaRepository
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


@dataclass(frozen=True, slots=True)
class _PropertyEdit:
    holder_type: str
    holder_id: str
    property_name: str
    value: object


@dataclass(frozen=True, slots=True)
class _ReferenceEdit:
    holder_type: str
    holder_id: str
    property_name: str
    value: Mapping[str, object]


def bind_action_media_references(
    transaction: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    plan: EditPlan,
    media_repository: MediaRepository,
    binding_repository: MediaReferenceBindingRepository,
    runtime: ActionRuntimeBoundary,
) -> tuple[MediaReferenceBindingRecord, ...]:
    edits = _property_edits(plan)
    removed_count = _delete_replaced_bindings(transaction, ctx, binding_repository, edits)
    removed_count += _delete_object_bindings(transaction, ctx, binding_repository, plan)
    return _bind_reference_edits(
        transaction,
        ctx,
        action_run_id,
        edits,
        removed_count,
        media_repository,
        binding_repository,
        runtime,
    )


def bind_reverted_action_media_references(
    transaction: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    edits: list[ObjectEditRow],
    action_repository: ActionRepository,
    media_repository: MediaRepository,
    binding_repository: MediaReferenceBindingRepository,
    runtime: ActionRuntimeBoundary,
) -> tuple[MediaReferenceBindingRecord, ...]:
    property_edits, deleted_holders = _reverted_property_edits(transaction, ctx, edits, action_repository)
    removed_count = _delete_replaced_bindings(transaction, ctx, binding_repository, property_edits)
    removed_count += sum(
        binding_repository.delete_bindings_by_holder(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            holder_type=holder_type,
            holder_id=holder_id,
        )
        for holder_type, holder_id in deleted_holders
    )
    return _bind_reference_edits(
        transaction,
        ctx,
        action_run_id,
        property_edits,
        removed_count,
        media_repository,
        binding_repository,
        runtime,
    )


def _reverted_property_edits(
    transaction: TransactionContext,
    ctx: RequestContext,
    edits: list[ObjectEditRow],
    repository: ActionRepository,
) -> tuple[tuple[_PropertyEdit, ...], tuple[tuple[str, str], ...]]:
    properties: dict[tuple[str, str, str], _PropertyEdit] = {}
    deleted_holders: set[tuple[str, str]] = set()
    for edit in edits:
        payload = edit.get("revert_payload")
        if not isinstance(payload, Mapping):
            continue
        operation = payload.get("operation")
        holder = (edit["object_type_api_name"], edit["object_id"])
        if operation == "create_object":
            deleted_holders.add(holder)
            continue
        record = repository.object_target_for_revert(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            object_type_id=edit["object_type_id"],
            object_id=edit["object_id"],
        )
        if record is None:
            continue
        names = record["properties"] if operation == "delete_object" else _reverted_property_names(payload)
        for name in names:
            value = record["properties"].get(name)
            properties[(*holder, str(name))] = _PropertyEdit(*holder, str(name), value)
    return tuple(properties.values()), tuple(sorted(deleted_holders))


def _reverted_property_names(payload: Mapping[str, object]) -> tuple[str, ...]:
    values = payload.get("properties")
    return tuple(str(name) for name in values) if isinstance(values, Mapping) else ()


def _bind_reference_edits(
    transaction: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    edits: tuple[_PropertyEdit, ...],
    removed_count: int,
    media_repository: MediaRepository,
    binding_repository: MediaReferenceBindingRepository,
    runtime: ActionRuntimeBoundary,
) -> tuple[MediaReferenceBindingRecord, ...]:
    references = tuple(reference for edit in edits for reference in _references(edit))
    versions = _versions(transaction, ctx, media_repository, references)
    _reserve_attachment_lifetime(transaction, ctx, action_run_id, binding_repository, references)
    records = tuple(
        _binding_record(ctx, action_run_id, reference, *versions[_version_id(reference)]) for reference in references
    )
    for record in records:
        binding_repository.create_binding(transaction=transaction, record=record)
    _emit_binding_evidence(transaction, ctx, runtime, action_run_id, records, removed_count)
    return records


def _property_edits(plan: EditPlan) -> tuple[_PropertyEdit, ...]:
    edits: list[_PropertyEdit] = []
    for create in plan.objects_to_create:
        edits.extend(
            _PropertyEdit(create.object_type, str(create.primary_key), str(name), value)
            for name, value in create.properties.items()
        )
    for modify in plan.objects_to_modify:
        edits.extend(
            _PropertyEdit(modify.object_type, modify.object_id, str(name), value)
            for name, value in modify.patch.items()
        )
    return tuple(edits)


def _delete_replaced_bindings(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaReferenceBindingRepository,
    edits: tuple[_PropertyEdit, ...],
) -> int:
    removed_count = 0
    for edit in edits:
        removed_count += repository.delete_bindings_by_holder_property(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            holder_type=edit.holder_type,
            holder_id=edit.holder_id,
            property_name=edit.property_name,
        )
    return removed_count


def _delete_object_bindings(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaReferenceBindingRepository,
    plan: EditPlan,
) -> int:
    return sum(
        repository.delete_bindings_by_holder(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            holder_type=edit.object_type,
            holder_id=edit.object_id,
        )
        for edit in plan.objects_to_delete
    )


def _references(edit: _PropertyEdit) -> tuple[_ReferenceEdit, ...]:
    found: list[_ReferenceEdit] = []
    _collect_references(edit.value, edit.property_name, edit.holder_type, edit.holder_id, found)
    return tuple(found)


def _collect_references(
    value: object,
    path: str,
    holder_type: str,
    holder_id: str,
    found: list[_ReferenceEdit],
) -> None:
    if _is_canonical_reference(value):
        found.append(_ReferenceEdit(holder_type, holder_id, path, cast(Mapping[str, object], value)))
        return
    if isinstance(value, Mapping):
        for name, item in value.items():
            _collect_references(item, f"{path}.{name}", holder_type, holder_id, found)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            _collect_references(item, f"{path}[{index}]", holder_type, holder_id, found)


def _is_canonical_reference(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("referenceKind") in {"media", "attachment"}
        and all(
            isinstance(value.get(key), str) and bool(value.get(key))
            for key in ("mediaSetId", "mediaItemId", "mediaItemVersionId", "contentHash")
        )
    )


def _versions(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    references: tuple[_ReferenceEdit, ...],
) -> dict[str, tuple[MediaItemVersionRecord, MediaItemRecord]]:
    ids = sorted({_version_id(reference) for reference in references})
    rows = repository.get_media_item_versions(transaction=transaction, tenant_id=ctx.tenant_id, ids=ids)
    versions = {row.media_item_version_id: row for row in rows if row.status == "COMMITTED"}
    if set(ids) != set(versions):
        raise NotFound("Action media reference no longer resolves to a committed version")
    return {
        version_id: (version, _required_item(transaction, ctx, repository, version))
        for version_id, version in versions.items()
    }


def _required_item(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    version: MediaItemVersionRecord,
) -> MediaItemRecord:
    item = repository.media_item_by_id(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        media_item_id=version.media_item_id,
    )
    if item is None:
        raise NotFound("Action media item no longer exists")
    return item


def _reserve_attachment_lifetime(
    transaction: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    repository: MediaReferenceBindingRepository,
    references: tuple[_ReferenceEdit, ...],
) -> None:
    holders = sorted(
        {(_version_id(item), item.holder_type, item.holder_id) for item in references if _kind(item) == "attachment"}
    )
    for version_id, holder_type, holder_id in holders:
        result = repository.reserve_attachment_holder(
            transaction=transaction,
            record=AttachmentHolderAssociationRecord(
                association_id=_new_id("mattachassoc"),
                tenant_id=ctx.tenant_id,
                media_item_version_id=version_id,
                holder_type=holder_type,
                holder_id=holder_id,
                first_action_run_id=action_run_id,
                created_at=_now(),
            ),
            max_holders=10,
        )
        if not result.is_reserved:
            raise ValidationFailed(
                "An attachment can be associated with at most ten objects",
                details={"mediaItemVersionId": version_id, "objectCount": result.holder_count, "maxObjects": 10},
            )


def _binding_record(
    ctx: RequestContext,
    action_run_id: str,
    reference: _ReferenceEdit,
    version: MediaItemVersionRecord,
    item: MediaItemRecord,
) -> MediaReferenceBindingRecord:
    now = _now()
    value = reference.value
    _require_reference_matches(value, version, item)
    return MediaReferenceBindingRecord(
        media_reference_binding_id=_new_id("mrb"),
        tenant_id=ctx.tenant_id,
        holder_type=reference.holder_type,
        holder_id=reference.holder_id,
        property_name=reference.property_name,
        media_set_id=item.media_set_id,
        media_item_id=item.media_item_id,
        media_item_version_id=version.media_item_version_id,
        logical_path=item.logical_path,
        content_hash=version.content_hash,
        security_envelope={**version.security_envelope, "referenceKind": _kind(reference)},
        idempotency_key=f"{action_run_id}:{reference.holder_type}:{reference.holder_id}:{reference.property_name}",
        created_at=now,
        updated_at=now,
    )


def _require_reference_matches(
    value: Mapping[str, object],
    version: MediaItemVersionRecord,
    item: MediaItemRecord,
) -> None:
    expected = {
        "mediaSetId": item.media_set_id,
        "mediaItemId": item.media_item_id,
        "mediaItemVersionId": version.media_item_version_id,
        "logicalPath": item.logical_path,
        "contentHash": version.content_hash,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValidationFailed("Action edit plan contains a tampered media reference")


def _emit_binding_evidence(
    transaction: TransactionContext,
    ctx: RequestContext,
    runtime: ActionRuntimeBoundary,
    action_run_id: str,
    records: tuple[MediaReferenceBindingRecord, ...],
    removed_count: int,
) -> None:
    if not records and removed_count == 0:
        return
    payload = {
        "bindingCount": len(records),
        "replacedBindingCount": removed_count,
        "mediaItemVersionIds": sorted({record.media_item_version_id for record in records}),
    }
    runtime._outbox(
        transaction,
        ctx,
        "action.media.references.bound",
        "action_run",
        action_run_id,
        payload,
        idempotency_key=f"{action_run_id}:media-bindings",
        correlation_id=action_run_id,
    )
    runtime._audit(
        transaction,
        ctx,
        event_type="action.media.references.bound",
        resource_type="action_run",
        resource_id=action_run_id,
        action="commit",
        after_ref=payload,
        correlation_id=action_run_id,
    )


def _version_id(reference: _ReferenceEdit) -> str:
    return str(reference.value["mediaItemVersionId"])


def _kind(reference: _ReferenceEdit) -> str:
    return str(reference.value["referenceKind"])
