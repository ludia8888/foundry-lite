"""Build immutable Pipeline Builder source contracts from committed rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.dataset_repository import DatasetRow
from foundry_lite.application.ports.dataset_version_repository import DatasetVersionRow
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaSetRecord,
    MediaSetSelectionRecord,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.media.read_access import require_media_version_clearance
from foundry_lite.application.services.media.security_envelopes import (
    MediaSecurityEnvelopeInvalid,
    validated_media_source_envelope,
)
from foundry_lite.application.services.pipeline_graph_contracts import (
    JsonObject,
    PipelineArtifactKind,
    PipelineV2Node,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceContract,
    PipelineSourceContractResolutionFailed,
    PipelineSourceVersionPin,
)
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS as _CLASSIFICATION_RANK,
)
from foundry_lite.domain.classification import (
    normalize_classification as _classification,
)
from foundry_lite.domain.context import RequestContext


def build_dataset_source_contract(
    *,
    node: PipelineV2Node,
    dataset_ref: str,
    dataset: DatasetRow,
    version: DatasetVersionRow,
    schema: Mapping[str, object],
    ctx: RequestContext,
) -> PipelineSourceContract:
    """Pin one exact committed Dataset version and its authoritative schema."""

    schema_contract = _schema_json(schema)
    version_pin = PipelineSourceVersionPin(
        version_id=version["id"],
        ordinal=version["version_number"],
        content_fingerprint=_dataset_version_fingerprint(version, str(schema["schema_hash"])),
        metadata=_dataset_version_metadata(version),
    )
    return PipelineSourceContract(
        node_id=node["id"],
        descriptor_id=node["descriptorId"],
        artifact_kind=PipelineArtifactKind.DATASET_VERSION,
        resource_ref=dataset_ref,
        source_id=dataset["id"],
        schema_contract=schema_contract,
        schema_hash=str(schema["schema_hash"]),
        schema_version=_schema_version(schema),
        version_pins=(version_pin,),
        security_envelope=_dataset_security_envelope(dataset, ctx),
        access_evidence=_access_evidence(ctx, "dataset:read"),
    )


def build_media_source_contract(
    *,
    node: PipelineV2Node,
    media_set_ref: str,
    media_set: MediaSetRecord,
    selected: Sequence[MediaSetSelectionRecord],
    ctx: RequestContext,
) -> PipelineSourceContract:
    """Pin the exact committed media selection and inherited security."""

    envelopes = [_validated_media_envelope(media_set, item.version, ctx) for item in selected]
    schema_contract = _media_schema_contract(media_set, len(selected))
    pins = tuple(_media_version_pin(index, item, envelopes[index]) for index, item in enumerate(selected))
    return PipelineSourceContract(
        node_id=node["id"],
        descriptor_id=node["descriptorId"],
        artifact_kind=PipelineArtifactKind.MEDIA_SET_SELECTION,
        resource_ref=media_set_ref,
        source_id=media_set.media_set_id,
        schema_contract=schema_contract,
        schema_hash=_json_hash(schema_contract),
        schema_version=None,
        version_pins=pins,
        security_envelope=_aggregate_media_security(media_set, envelopes, ctx),
        access_evidence=_access_evidence(ctx, "pipeline:read"),
    )


def _dataset_version_fingerprint(version: DatasetVersionRow, schema_hash: str) -> str:
    return _json_hash(
        {
            "versionId": version["id"],
            "manifestUri": version["manifest_uri"],
            "rowCount": version["row_count"],
            "byteSize": version["byte_size"],
            "schemaHash": schema_hash,
        }
    )


def _dataset_version_metadata(version: DatasetVersionRow) -> JsonObject:
    return {
        "versionNumber": version["version_number"],
        "branch": version["branch"],
        "manifestUri": version["manifest_uri"],
        "rowCount": version["row_count"],
        "byteSize": version["byte_size"],
        "status": version["status"],
        "schemaVersion": version["schema_version"],
    }


def _dataset_security_envelope(dataset: DatasetRow, ctx: RequestContext) -> JsonObject:
    return {
        "tenantId": ctx.tenant_id,
        "classification": _classification(dataset["classification"]),
        "ownerTeam": dataset["owner_team"],
        "inheritance": "source",
    }


def _media_version_pin(
    index: int,
    selection: MediaSetSelectionRecord,
    envelope: Mapping[str, object],
) -> PipelineSourceVersionPin:
    version = selection.version
    return PipelineSourceVersionPin(
        version_id=version.media_item_version_id,
        ordinal=index + 1,
        content_fingerprint=version.content_hash,
        metadata={
            "logicalPath": selection.logical_path,
            "mediaItemId": version.media_item_id,
            "versionNumber": version.version_number,
            "byteSize": version.byte_size,
            "mimeType": version.sniffed_mime_type,
            "format": version.format,
            "status": version.status,
            "securityEnvelope": dict(envelope),
        },
    )


def _validated_media_envelope(
    media_set: MediaSetRecord,
    version: MediaItemVersionRecord,
    ctx: RequestContext,
) -> JsonObject:
    require_media_version_clearance(ctx, version)
    try:
        envelope = validated_media_source_envelope(media_set, version, tenant_id=ctx.tenant_id)
    except MediaSecurityEnvelopeInvalid as exc:
        raise _media_security_failure(
            version,
            str(exc.details["reason"]),
            weakened_fields=exc.details.get("weakenedFields"),
        ) from exc
    envelope["classification"] = _classification(envelope.get("classification"))
    return envelope


def _aggregate_media_security(
    media_set: MediaSetRecord,
    envelopes: Sequence[Mapping[str, object]],
    ctx: RequestContext,
) -> JsonObject:
    classifications = [_classification(media_set.classification)]
    classifications.extend(_classification(item.get("classification")) for item in envelopes)
    return {
        "tenantId": ctx.tenant_id,
        "classification": _strongest_classification(classifications),
        "sourceClassification": _classification(media_set.classification),
        "policyVersions": _unique_text_values(envelopes, "policyVersion"),
        "allowedPrincipalSetIds": _unique_text_values(envelopes, "allowedPrincipalSetId"),
        "hasLegalHold": any(bool(item.get("hasLegalHold")) for item in envelopes),
        "inheritance": "source",
    }


def _media_schema_contract(media_set: MediaSetRecord, item_count: int) -> JsonObject:
    return {
        "schemaType": media_set.schema_type,
        "primaryFormat": media_set.primary_format,
        "allowedInputFormats": list(media_set.allowed_input_formats),
        "selectionItemCount": item_count,
        "isVirtual": media_set.is_virtual,
    }


def _access_evidence(ctx: RequestContext, permission: str) -> JsonObject:
    return {
        "tenantId": ctx.tenant_id,
        "principalId": ctx.actor_user_id,
        "requestId": ctx.request_id,
        "permission": permission,
        "scopeEnforcement": "tenant_scoped_repository",
    }


def _schema_json(schema: Mapping[str, object]) -> JsonObject:
    value = schema.get("schema_json")
    if not isinstance(value, Mapping):
        raise PipelineSourceContractResolutionFailed(
            "committed dataset schema is invalid",
            details={"reason": "source_schema_invalid"},
        )
    return {str(key): item for key, item in value.items()}


def _schema_version(schema: Mapping[str, object]) -> int:
    value = schema.get("version")
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineSourceContractResolutionFailed(
            "committed dataset schema version is invalid",
            details={"reason": "source_schema_invalid"},
        )
    return value


def _media_security_failure(
    version: MediaItemVersionRecord,
    reason: str,
    *,
    weakened_fields: object = None,
) -> PipelineSourceContractResolutionFailed:
    details: JsonObject = {
        "reason": reason,
        "mediaItemVersionId": version.media_item_version_id,
    }
    if isinstance(weakened_fields, (list, tuple)):
        details["weakenedFields"] = [str(field) for field in weakened_fields]
    return PipelineSourceContractResolutionFailed(
        "pipeline media source security envelope is invalid",
        details=details,
    )


def _strongest_classification(values: Sequence[str]) -> str:
    if all(value in _CLASSIFICATION_RANK for value in values):
        return max(values, key=lambda item: _CLASSIFICATION_RANK[item])
    first = values[0]
    if all(value == first for value in values):
        return first
    raise PipelineSourceContractResolutionFailed(
        "pipeline source classifications cannot be ordered safely",
        details={"reason": "source_security_classification_unknown", "classifications": sorted(set(values))},
    )


def _unique_text_values(
    rows: Sequence[Mapping[str, object]],
    field: str,
) -> list[str]:
    return sorted({str(value) for row in rows if (value := row.get(field)) is not None and str(value).strip()})
