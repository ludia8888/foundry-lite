"""Pure validation and payload helpers for the Graph v2 Media Set output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from foundry_lite.application.ports.media_derivative_repository import (
    MediaDerivativeRecord,
)
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaSetRecord,
)
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    JsonObject,
    MediaOutputContract,
    MediaOutputEntry,
    PipelineMediaDerivativeBytesUnavailable,
    PipelineMediaOutputSourceCorrupt,
    PipelineMediaSetOutputContractMismatch,
    normalized_classification,
)
from foundry_lite.application.services.pipeline_media_set_output_security import (
    require_item_security,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import (
    strongest_classification,
)


def media_output_entry(
    *,
    source_version: MediaItemVersionRecord,
    media_set: MediaSetRecord | None,
    logical_path: str,
    blob_key: str,
    content_hash: str,
    byte_size: int,
    mime_type: str,
    schema_type: str,
    format: str,
    derivative: MediaDerivativeRecord | None = None,
) -> MediaOutputEntry:
    coordinates = {
        "sourceMediaItemVersionId": source_version.media_item_version_id,
        "mediaDerivativeId": derivative.media_derivative_id if derivative is not None else None,
        "contentHash": content_hash,
        "byteSize": byte_size,
        "logicalPath": logical_path,
        "securityEnvelope": dict(
            derivative.security_envelope if derivative is not None else source_version.security_envelope
        ),
    }
    return MediaOutputEntry(
        entry_fingerprint=_json_hash(coordinates),
        source_media_item_version_id=source_version.media_item_version_id,
        media_derivative_id=derivative.media_derivative_id if derivative is not None else None,
        logical_path=logical_path,
        blob_key=blob_key,
        content_hash=content_hash,
        byte_size=byte_size,
        mime_type=mime_type,
        schema_type=schema_type,
        format=format,
        primary_format=media_set.primary_format if media_set is not None else format,
        allowed_formats=media_set.allowed_input_formats if media_set is not None else (format,),
        security_envelope=dict(
            derivative.security_envelope if derivative is not None else source_version.security_envelope
        ),
    )


def output_contract(entries: Sequence[MediaOutputEntry]) -> MediaOutputContract:
    schema_types = {entry.schema_type for entry in entries}
    classifications = {normalized_classification(entry.security_envelope.get("classification")) for entry in entries}
    if len(schema_types) != 1 or len(classifications) != 1:
        raise PipelineMediaSetOutputContractMismatch(
            "pipeline Media Set output inputs do not share one safe target contract",
            details={
                "schemaTypes": sorted(schema_types),
                "classifications": sorted(classifications),
            },
        )
    formats = tuple(sorted({value for entry in entries for value in entry.allowed_formats}))
    primary_formats = {entry.primary_format for entry in entries}
    if len(primary_formats) != 1:
        raise PipelineMediaSetOutputContractMismatch(
            "pipeline Media Set output inputs do not share one primary format",
            details={"primaryFormats": sorted(primary_formats)},
        )
    return MediaOutputContract(
        schema_type=next(iter(schema_types)),
        primary_format=next(iter(primary_formats)),
        allowed_formats=formats,
        classification=strongest_classification(tuple(classifications)),
    )


def require_target_contract(
    target: MediaSetRecord,
    required: MediaOutputContract,
    target_ref: str,
) -> None:
    actual_classification = normalized_classification(target.classification)
    mismatches: JsonObject = {}
    if target.is_virtual or target.transaction_policy != "transactional":
        mismatches["transactionPolicy"] = target.transaction_policy
    if target.schema_type != required.schema_type:
        mismatches["schemaType"] = target.schema_type
    if target.primary_format != required.primary_format:
        mismatches["primaryFormat"] = target.primary_format
    if not set(required.allowed_formats).issubset(target.allowed_input_formats):
        mismatches["allowedInputFormats"] = list(target.allowed_input_formats)
    if actual_classification != required.classification:
        mismatches["classification"] = actual_classification
    if mismatches:
        raise PipelineMediaSetOutputContractMismatch(
            "pipeline Media Set output target contract does not match its input",
            details={
                "mediaSetRef": target_ref,
                "mismatches": mismatches,
                "requiredClassification": required.classification,
            },
        )


def validate_output_versions(
    versions: Sequence[MediaItemVersionRecord],
    entries: Sequence[MediaOutputEntry],
    target: MediaSetRecord,
    request_fingerprint: str,
    transaction_generation: int,
    storage: MediaStorageAdapter,
    *,
    is_committed: bool = False,
) -> None:
    by_entry = versions_by_entry(versions)
    expected = {entry.entry_fingerprint: entry for entry in entries}
    if set(by_entry) != set(expected):
        raise PipelineMediaSetOutputContractMismatch(
            "pipeline Media Set output staged version set does not match its immutable input",
            details={"mediaSetId": target.media_set_id, "reason": "staged_entry_set_mismatch"},
        )
    for fingerprint, entry in expected.items():
        _validate_output_version(
            by_entry[fingerprint],
            entry,
            target,
            request_fingerprint,
            transaction_generation,
            storage,
            is_committed,
        )


def _validate_output_version(
    version: MediaItemVersionRecord,
    entry: MediaOutputEntry,
    target: MediaSetRecord,
    request_fingerprint: str,
    transaction_generation: int,
    storage: MediaStorageAdapter,
    is_committed: bool,
) -> None:
    expected_status = "COMMITTED" if is_committed else {"STAGED", "COMMITTED"}
    status_matches = (
        version.status == expected_status if isinstance(expected_status, str) else version.status in expected_status
    )
    lineage = pipeline_output_lineage(version)
    semantics = (
        version.content_hash,
        version.byte_size,
        version.schema_type,
        version.format,
        version.security_envelope,
        lineage.get("requestFingerprint"),
        lineage.get("transactionGeneration"),
    )
    expected = (
        entry.content_hash,
        entry.byte_size,
        entry.schema_type,
        entry.format,
        entry.security_envelope,
        request_fingerprint,
        transaction_generation,
    )
    stat = storage.stat(version.blob_key)
    if status_matches and semantics == expected and stat.is_present:
        if stat.content_hash == entry.content_hash and stat.byte_size == entry.byte_size:
            return
    raise PipelineMediaSetOutputContractMismatch(
        "pipeline Media Set output staged version failed validation",
        details={"mediaSetId": target.media_set_id, "mediaItemVersionId": version.media_item_version_id},
    )


def versions_by_entry(
    versions: Sequence[MediaItemVersionRecord],
) -> dict[str, MediaItemVersionRecord]:
    by_entry: dict[str, MediaItemVersionRecord] = {}
    for version in versions:
        fingerprint = pipeline_output_lineage(version).get("entryFingerprint")
        if not isinstance(fingerprint, str) or not fingerprint or fingerprint in by_entry:
            raise PipelineMediaSetOutputContractMismatch(
                "pipeline Media Set output transaction contains ambiguous lineage",
                details={"mediaItemVersionId": version.media_item_version_id},
            )
        by_entry[fingerprint] = version
    return by_entry


def pipeline_output_lineage(version: MediaItemVersionRecord) -> Mapping[str, object]:
    source_ref = version.source_ref
    lineage = source_ref.get("pipelineOutput") if isinstance(source_ref, Mapping) else None
    return lineage if isinstance(lineage, Mapping) else {}


def lineage_payload(
    run_id: str,
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    entry: MediaOutputEntry,
    transaction_generation: int,
) -> JsonObject:
    return {
        "pipelineRunId": run_id,
        "pipelineNodeId": node.node_id,
        "descriptorId": node.descriptor_id,
        "specVersion": node.spec_version,
        "inputNodeId": source.node_id,
        "inputArtifactKind": source.artifact_kind,
        "inputContentFingerprint": source.content_fingerprint,
        "sourceMediaItemVersionId": entry.source_media_item_version_id,
        "mediaDerivativeId": entry.media_derivative_id,
        "entryFingerprint": entry.entry_fingerprint,
        "transactionGeneration": transaction_generation,
    }


def request_fingerprint(
    run_id: str,
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    target_ref: str,
    entries: Sequence[MediaOutputEntry],
) -> str:
    return _json_hash(
        {
            "pipelineRunId": run_id,
            "nodeId": node.node_id,
            "descriptorId": node.descriptor_id,
            "specVersion": node.spec_version,
            "targetMediaSetRef": target_ref,
            "inputContentFingerprint": source.content_fingerprint,
            "entries": [entry.entry_fingerprint for entry in entries],
        }
    )


def derivative_bytes(
    derivative: MediaDerivativeRecord,
) -> tuple[str, str, int, str]:
    values = (
        derivative.blob_key,
        derivative.content_hash,
        derivative.byte_size,
        derivative.mime_type,
    )
    if (
        isinstance(values[0], str)
        and values[0]
        and isinstance(values[1], str)
        and values[1]
        and isinstance(values[2], int)
        and values[2] >= 0
        and isinstance(values[3], str)
        and values[3]
    ):
        return values[0], values[1], values[2], values[3]
    raise PipelineMediaDerivativeBytesUnavailable(
        "pipeline Media Set output derivative has no durable bytes",
        details={
            "mediaDerivativeId": derivative.media_derivative_id,
            "reason": "durable_bytes_missing",
        },
    )


def require_derivative_coordinates(
    item: Mapping[str, object],
    derivative: MediaDerivativeRecord,
) -> None:
    require_item_coordinate(
        item,
        "mediaItemVersionId",
        derivative.source_media_item_version_id,
        derivative.media_derivative_id,
    )
    if item.get("contentHash") not in {None, derivative.content_hash}:
        raise PipelineMediaOutputSourceCorrupt(
            "pipeline derivative artifact does not match committed truth",
            details={"mediaDerivativeId": derivative.media_derivative_id},
        )
    require_item_security(
        item,
        derivative.security_envelope,
        derivative.media_derivative_id,
    )


def require_item_coordinate(
    item: Mapping[str, object],
    field: str,
    expected: str,
    source_id: str,
) -> None:
    if item.get(field) == expected:
        return
    raise PipelineMediaOutputSourceCorrupt(
        "pipeline media artifact coordinate does not match committed truth",
        details={"sourceId": source_id, "field": field},
    )


def derivative_logical_path(
    source_path: str,
    derivative: MediaDerivativeRecord,
    output_format: str,
) -> str:
    path = PurePosixPath(source_path)
    stem = path.stem or "media"
    kind = "".join(character if character.isalnum() else "-" for character in derivative.derivative_kind).strip("-")
    name = f"{stem}.{kind or 'derivative'}.{output_format}"
    return str(path.with_name(name))


def format_for_mime(mime_type: str) -> str:
    known = {
        "application/json": "json",
        "application/pdf": "pdf",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "image/jpeg": "jpeg",
        "image/png": "png",
        "text/plain": "txt",
        "video/mp4": "mp4",
    }
    if mime_type in known:
        return known[mime_type]
    suffix = mime_type.partition("/")[2].partition(";")[0].strip().lower()
    if suffix and all(character.isalnum() or character in {"-", "."} for character in suffix):
        return suffix
    raise PipelineMediaSetOutputContractMismatch(
        "pipeline derivative MIME type cannot be mapped to a Media Set format",
        details={"mimeType": mime_type},
    )


def schema_for_mime(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("text/") or mime_type in {"application/json", "application/pdf"}:
        return "document"
    return "binary"


def media_set_ref_parts(media_set_ref: str) -> tuple[str, str]:
    namespace, separator, name = media_set_ref.partition(".")
    if separator and namespace.strip() and name.strip():
        return namespace.strip(), name.strip()
    raise PipelineMediaSetOutputContractMismatch(
        "pipeline Media Set output ref must be namespace.name",
        details={"mediaSetRef": media_set_ref},
    )


def item_text(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise PipelineMediaOutputSourceCorrupt(
        "pipeline Media Set output item coordinate is missing",
        details={"field": field},
    )


def required_text(
    config: Mapping[str, object],
    field: str,
    node_id: str,
) -> str:
    value = config.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise PipelineMediaSetOutputContractMismatch(
        "pipeline Media Set output config field is required",
        details={"nodeId": node_id, "field": field},
    )
