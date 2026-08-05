"""Server-resolved media and attachment values for canonical Action plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports.media_repository import (
    MediaItemRecord,
    MediaItemVersionRecord,
    MediaRepository,
    MediaSetRecord,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.read_access import require_media_version_clearance
from foundry_lite.application.services.media.security_envelopes import validated_media_source_envelope
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, ActionParameterV3
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True, slots=True)
class ActionMediaValue:
    parameter_name: str
    path: tuple[str | int, ...]
    reference_kind: str
    parameter: ActionParameterV3
    media_item_version_id: str


def resolve_action_media_parameters(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    repository: MediaRepository,
    contract: ActionDefinitionV3,
    values: Mapping[str, object],
) -> dict[str, object]:
    references = action_media_values(contract, values)
    if not references:
        return dict(values)
    policy.require(ctx, "media:read")
    versions = _versions_by_id(transaction, ctx, repository, references)
    resolved = {
        (entry.reference_kind, entry.media_item_version_id): _canonical_reference(
            transaction, ctx, repository, entry, versions[entry.media_item_version_id]
        )
        for entry in references
    }
    return {
        parameter.api_name: _resolved_parameter_value(parameter, value, resolved)
        for parameter in contract.parameters
        if (value := values.get(parameter.api_name, _MISSING)) is not _MISSING
    }


def action_media_values(contract: ActionDefinitionV3, values: Mapping[str, object]) -> tuple[ActionMediaValue, ...]:
    result: list[ActionMediaValue] = []
    for parameter in contract.parameters:
        if parameter.api_name in values:
            _collect_media_values(parameter, values[parameter.api_name], parameter.api_name, (), result)
    return tuple(result)


def action_media_parameter(contract: ActionDefinitionV3, parameter_name: str) -> tuple[ActionParameterV3, str]:
    parts = tuple(part for part in parameter_name.split(".") if part)
    parameter = next((item for item in contract.parameters if item.api_name == parts[0]), None) if parts else None
    if parameter is None:
        raise NotFound("Action parameter not found", details={"parameter": parameter_name})
    for field_name in parts[1:]:
        parameter = _nested_struct_parameter(parameter, field_name, parameter_name)
    kind = _parameter_media_kind(parameter)
    if kind is None:
        raise ValidationFailed(
            "Action parameter does not accept media uploads",
            details={"parameter": parameter_name, "type": parameter.data_type},
        )
    return parameter, kind


def _nested_struct_parameter(
    parameter: ActionParameterV3,
    field_name: str,
    parameter_path: str,
) -> ActionParameterV3:
    if parameter.data_type != "struct":
        raise NotFound("Action parameter path is not a struct", details={"parameter": parameter_path})
    field = next((item for item in _struct_parameters(parameter) if item.api_name == field_name), None)
    if field is None:
        raise NotFound("Action parameter field not found", details={"parameter": parameter_path})
    return field


def _collect_media_values(
    parameter: ActionParameterV3,
    value: object,
    parameter_name: str,
    path: tuple[str | int, ...],
    result: list[ActionMediaValue],
) -> None:
    kind = _parameter_media_kind(parameter)
    if parameter.data_type in {"media", "attachment"} and kind is not None:
        result.append(ActionMediaValue(parameter_name, path, kind, parameter, _media_version_id(value, kind)))
        return
    if parameter.data_type in {"array", "objectSet"} and kind is not None:
        _collect_media_sequence(parameter, value, parameter_name, path, result)
        return
    if parameter.data_type == "struct" and isinstance(value, Mapping):
        _collect_media_struct(parameter, value, parameter_name, path, result)


def _collect_media_sequence(
    parameter: ActionParameterV3,
    value: object,
    parameter_name: str,
    path: tuple[str | int, ...],
    result: list[ActionMediaValue],
) -> None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return
    item = _item_parameter(parameter)
    for index, entry in enumerate(value):
        _collect_media_values(item, entry, parameter_name, (*path, index), result)


def _collect_media_struct(
    parameter: ActionParameterV3,
    value: Mapping[object, object],
    parameter_name: str,
    path: tuple[str | int, ...],
    result: list[ActionMediaValue],
) -> None:
    for field in _struct_parameters(parameter):
        if field.api_name in value:
            _collect_media_values(field, value[field.api_name], parameter_name, (*path, field.api_name), result)


def _versions_by_id(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    references: tuple[ActionMediaValue, ...],
) -> dict[str, MediaItemVersionRecord]:
    ids = sorted({entry.media_item_version_id for entry in references})
    rows = repository.get_media_item_versions(transaction=transaction, tenant_id=ctx.tenant_id, ids=ids)
    by_id = {row.media_item_version_id: row for row in rows}
    missing = [entry for entry in references if entry.media_item_version_id not in by_id]
    if missing:
        raise NotFound(
            "Action media version not found",
            details={"invalid": sorted({entry.parameter_name for entry in missing})},
        )
    return by_id


def _canonical_reference(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    entry: ActionMediaValue,
    version: MediaItemVersionRecord,
) -> dict[str, object]:
    if version.status != "COMMITTED":
        raise ConflictDetected(
            "Action media parameter requires a committed immutable version",
            details={"invalid": [entry.parameter_name], "status": version.status},
        )
    require_media_version_clearance(ctx, version)
    item = _media_item(transaction, ctx, repository, version.media_item_id, entry.parameter_name)
    media_set = _media_set(transaction, ctx, repository, entry.parameter, entry.parameter_name)
    if item.media_set_id != media_set.media_set_id:
        raise ValidationFailed(
            "Action media version belongs to a different Media Set",
            details={"invalid": [entry.parameter_name], "expectedMediaSet": _media_set_ref(entry.parameter)},
        )
    envelope = validated_media_source_envelope(media_set, version, tenant_id=ctx.tenant_id)
    _require_media_constraints(entry, version)
    return _reference_payload(entry.reference_kind, media_set, item, version, envelope)


def _media_item(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    media_item_id: str,
    parameter_name: str,
) -> MediaItemRecord:
    item = repository.media_item_by_id(transaction=transaction, tenant_id=ctx.tenant_id, media_item_id=media_item_id)
    if item is None:
        raise NotFound("Action media item not found", details={"invalid": [parameter_name]})
    return item


def _media_set(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: MediaRepository,
    parameter: ActionParameterV3,
    parameter_name: str,
) -> MediaSetRecord:
    namespace, name = _media_set_ref(parameter).split(".", 1)
    media_set = repository.media_set_by_ref(
        transaction=transaction, tenant_id=ctx.tenant_id, namespace=namespace, name=name
    )
    if media_set is None:
        raise NotFound("Action parameter Media Set not found", details={"invalid": [parameter_name]})
    return media_set


def _require_media_constraints(entry: ActionMediaValue, version: MediaItemVersionRecord) -> None:
    allowed = entry.parameter.metadata.get("allowedMimeTypes")
    if isinstance(allowed, Sequence) and not isinstance(allowed, str | bytes):
        patterns = tuple(item for item in allowed if isinstance(item, str))
        if not any(_mime_matches(version.sniffed_mime_type, pattern) for pattern in patterns):
            raise ValidationFailed(
                "Action media MIME type is not allowed",
                details={"invalid": [entry.parameter_name], "mimeType": version.sniffed_mime_type},
            )
    maximum = entry.parameter.metadata.get("maxBytes")
    if isinstance(maximum, int) and version.byte_size > maximum:
        raise ValidationFailed(
            "Action media file exceeds the parameter size limit",
            details={"invalid": [entry.parameter_name], "byteSize": version.byte_size, "maxBytes": maximum},
        )


def _reference_payload(
    kind: str,
    media_set: MediaSetRecord,
    item: MediaItemRecord,
    version: MediaItemVersionRecord,
    envelope: Mapping[str, object],
) -> dict[str, object]:
    return {
        "referenceKind": kind,
        "mediaSetId": media_set.media_set_id,
        "mediaItemId": item.media_item_id,
        "mediaItemVersionId": version.media_item_version_id,
        "logicalPath": item.logical_path,
        "contentHash": version.content_hash,
        "mimeType": version.sniffed_mime_type,
        "byteSize": version.byte_size,
        "classification": str(envelope["classification"]),
    }


def _resolved_parameter_value(
    parameter: ActionParameterV3,
    value: object,
    resolved: Mapping[tuple[str, str], Mapping[str, object]],
) -> object:
    if parameter.data_type in {"media", "attachment"}:
        return dict(resolved[(parameter.data_type, _media_version_id(value, parameter.data_type))])
    if parameter.data_type in {"array", "objectSet"}:
        return _resolved_collection_value(parameter, value, resolved)
    if parameter.data_type == "struct" and isinstance(value, Mapping):
        return _resolved_struct_value(parameter, cast(Mapping[object, object], value), resolved)
    return value


def _resolved_collection_value(
    parameter: ActionParameterV3,
    value: object,
    resolved: Mapping[tuple[str, str], Mapping[str, object]],
) -> object:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return value
    item_parameter = _item_parameter(parameter)
    return [_resolved_parameter_value(item_parameter, entry, resolved) for entry in cast(Sequence[object], value)]


def _resolved_struct_value(
    parameter: ActionParameterV3,
    value: Mapping[object, object],
    resolved: Mapping[tuple[str, str], Mapping[str, object]],
) -> dict[str, object]:
    fields = {field.api_name: field for field in _struct_parameters(parameter)}
    return {
        str(key): _resolved_parameter_value(fields[str(key)], entry, resolved) if str(key) in fields else entry
        for key, entry in value.items()
    }


def _media_version_id(value: object, expected_kind: str) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        kind = value.get("referenceKind")
        version_id = value.get("mediaItemVersionId")
        if kind not in {None, expected_kind}:
            raise ValidationFailed("Action media reference kind does not match its parameter")
        if isinstance(version_id, str) and version_id:
            return version_id
    raise ValidationFailed("Action media parameter requires an immutable version id")


def _parameter_media_kind(parameter: ActionParameterV3) -> str | None:
    if parameter.data_type in {"media", "attachment"}:
        return parameter.data_type
    item_type = parameter.metadata.get("itemType")
    if parameter.data_type in {"array", "objectSet"} and item_type in {"media", "attachment"}:
        return str(item_type)
    return None


def _item_parameter(parameter: ActionParameterV3) -> ActionParameterV3:
    item_type = str(parameter.metadata.get("itemType") or "")
    metadata = {key: value for key, value in parameter.metadata.items() if key != "itemType"}
    return ActionParameterV3("item", item_type, False, None, None, {}, metadata, ())


def _struct_parameters(parameter: ActionParameterV3) -> tuple[ActionParameterV3, ...]:
    fields = parameter.metadata.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes):
        return ()
    return tuple(_struct_parameter(cast(Mapping[str, object], field)) for field in fields if isinstance(field, Mapping))


def _struct_parameter(field: Mapping[str, object]) -> ActionParameterV3:
    known = {"apiName", "type", "required", "description", "constraints"}
    return ActionParameterV3(
        str(field.get("apiName") or ""),
        str(field.get("type") or ""),
        field.get("required") is True,
        str(field["description"]) if isinstance(field.get("description"), str) else None,
        None,
        cast(Mapping[str, object], field.get("constraints")) if isinstance(field.get("constraints"), Mapping) else {},
        {key: value for key, value in field.items() if key not in known},
        (),
    )


def _media_set_ref(parameter: ActionParameterV3) -> str:
    return str(parameter.metadata["mediaSet"])


def _mime_matches(actual: str, pattern: str) -> bool:
    if pattern.endswith("/*"):
        return actual.startswith(pattern[:-1])
    return actual == pattern


_MISSING = object()
