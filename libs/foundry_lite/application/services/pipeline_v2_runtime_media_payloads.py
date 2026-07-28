"""Artifact payload builders for the production Graph v2 Media runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.media_derivative_repository import (
    ContentUnitRecord,
    MediaDerivativeRecord,
)
from foundry_lite.application.ports.media_processor_registry import MediaProcessorDescriptor
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.media.content_chunking import (
    ContentChunkOutcome,
    ContentChunkSpec,
)
from foundry_lite.application.services.media.indexing import IndexingOutcome
from foundry_lite.application.services.pipeline_media_reference import (
    required_source_media_reference,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
    PipelineV2SourceVersion,
    artifact_input_refs,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import (
    inherited_runtime_security,
)
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, NotFound

JsonObject = dict[str, object]
RuntimeInputs = Mapping[str, Sequence[PipelineV2RuntimeArtifact]]


def verified_media_item(
    contract: PipelineV2SourceContract,
    pin: PipelineV2SourceVersion,
    version: MediaItemVersionRecord | None,
) -> JsonObject:
    """Re-read and verify an immutable source version against its deployment pin."""

    if version is None:
        raise NotFound("pipeline pinned media version was not found", details={"versionId": pin.version_id})
    if version.status != "COMMITTED" or version.content_hash != pin.content_fingerprint:
        raise ConflictDetected(
            "pipeline pinned media version no longer matches committed truth",
            details={"versionId": pin.version_id, "status": version.status},
        )
    return _media_item_payload(contract, pin, version)


def derivative_artifact(
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    items: tuple[JsonObject, ...],
    processor: MediaProcessorDescriptor,
) -> PipelineV2RuntimeArtifact:
    """Build a serving derivative-set artifact from committed derivative rows."""

    derivative_ids = [str(item["mediaDerivativeId"]) for item in items]
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="derivatives",
        artifact_kind="media_derivative_set",
        plane="media",
        items=items,
        artifact_ref={
            "mediaDerivativeIds": derivative_ids,
            "sourceMediaItemVersionIds": source_version_ids(source),
        },
        manifest={
            "inputArtifacts": artifact_input_refs(inputs),
            "processorId": f"{processor.processor}@{processor.processor_version}",
            "model": {"name": processor.model.name, "version": processor.model.version},
            "derivativeCount": len(items),
            "sourceMediaReferences": _source_media_references(items),
        },
        security_envelope=inherited_runtime_security([source]),
        status="COMMITTED",
        is_serving=True,
        committed_at=_now(),
    )


def index_artifact(
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    generation: str,
    model_ref: str,
    outcomes: Sequence[IndexingOutcome],
) -> PipelineV2RuntimeArtifact:
    """Build a non-serving shadow index generation artifact."""

    indexed = sum(outcome.indexed for outcome in outcomes)
    failed = sum(outcome.failed for outcome in outcomes)
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="index",
        artifact_kind="vector_index_generation",
        plane="index",
        items=(),
        artifact_ref={"generation": generation, "servingState": "SHADOW_NOT_PROMOTED"},
        manifest={
            "inputArtifacts": artifact_input_refs(inputs),
            "embeddingModelVersion": model_ref,
            "indexed": indexed,
            "failed": failed,
            "commitKind": "INTERMEDIATE",
        },
        security_envelope=inherited_runtime_security([source]),
        status="COMMITTED",
        is_serving=False,
        committed_at=_now(),
    )


def chunk_artifact(
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    spec: ContentChunkSpec,
    outcomes: Sequence[ContentChunkOutcome],
    units: tuple[JsonObject, ...],
) -> PipelineV2RuntimeArtifact:
    """Build a committed Content Unit set from deterministic chunk derivatives."""

    derivative_ids = [outcome.media_derivative_id for outcome in outcomes]
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="content",
        artifact_kind="content_unit_set",
        plane="content",
        items=units,
        artifact_ref={
            "mediaDerivativeIds": derivative_ids,
            "sourceMediaDerivativeIds": [outcome.source_media_derivative_id for outcome in outcomes],
            "contentUnitIds": [str(unit["contentUnitId"]) for unit in units],
        },
        manifest=_chunk_manifest(inputs, spec, outcomes, len(units)),
        security_envelope=inherited_runtime_security([source]),
        status="COMMITTED",
        is_serving=True,
        committed_at=_now(),
    )


def _chunk_manifest(
    inputs: RuntimeInputs,
    spec: ContentChunkSpec,
    outcomes: Sequence[ContentChunkOutcome],
    unit_count: int,
) -> JsonObject:
    return {
        "inputArtifacts": artifact_input_refs(inputs),
        "processorId": "content_chunk_v1@1.0.0",
        "tokenizerVersion": spec.tokenizer_version,
        "chunkSize": spec.chunk_size,
        "overlap": spec.overlap,
        "chunkSpecHashes": [outcome.chunk_spec_hash for outcome in outcomes],
        "chunkConfigHashes": [outcome.chunk_config_hash for outcome in outcomes],
        "contentUnitCount": unit_count,
        "duplicateDerivativeCount": sum(1 for outcome in outcomes if outcome.is_duplicate),
    }


def derivative_item(
    derivative: MediaDerivativeRecord,
    processor: MediaProcessorDescriptor,
    source_media_reference: Mapping[str, object],
) -> JsonObject:
    """Project one committed derivative row into a runtime value."""

    return {
        "mediaDerivativeId": derivative.media_derivative_id,
        "mediaItemVersionId": derivative.source_media_item_version_id,
        "derivativeKind": derivative.derivative_kind,
        "processorId": f"{processor.processor}@{processor.processor_version}",
        "processorSpecHash": derivative.processor_spec_hash,
        "model": {"name": processor.model.name, "version": processor.model.version},
        "contentHash": derivative.content_hash,
        "mimeType": derivative.mime_type,
        "sourceMediaReference": required_source_media_reference(source_media_reference),
        "securityEnvelope": dict(derivative.security_envelope),
        "committedAt": derivative.committed_at,
    }


def content_unit_item(unit: ContentUnitRecord) -> JsonObject:
    """Project exact page, bbox, timecode, structure, and parent coordinates."""

    return {
        "contentUnitId": unit.content_unit_id,
        "sourceMediaItemVersionId": unit.source_media_item_version_id,
        "mediaDerivativeId": unit.derivative_id,
        "unitKind": unit.unit_kind,
        "ordinal": unit.ordinal,
        "text": unit.text,
        "textHash": unit.text_hash,
        "chunkSpecHash": unit.chunk_spec_hash,
        "pageNumber": unit.page_number,
        "startMs": unit.start_ms,
        "endMs": unit.end_ms,
        "bbox": dict(unit.bbox) if unit.bbox is not None else None,
        "parentContentUnitId": unit.parent_content_unit_id,
        "sourceLocator": dict(unit.source_locator) if unit.source_locator is not None else {},
        "structure": dict(unit.structure) if unit.structure is not None else None,
        "confidence": unit.confidence,
        "speaker": unit.speaker,
        "language": unit.language,
        "securityEnvelope": dict(unit.security_envelope),
    }


def source_version_ids(artifact: PipelineV2RuntimeArtifact) -> list[str]:
    """Resolve source media version coordinates from a source or derivative artifact."""

    ids = [
        str(item["mediaItemVersionId"]) for item in artifact.items if isinstance(item.get("mediaItemVersionId"), str)
    ]
    if not ids:
        ids = _reference_ids(artifact, "sourceMediaItemVersionIds")
    if ids:
        return list(dict.fromkeys(ids))
    raise InvariantViolation(
        "pipeline media artifact has no source version coordinates",
        details={"nodeId": artifact.node_id},
    )


def derivative_ids(artifact: PipelineV2RuntimeArtifact) -> list[str]:
    """Resolve committed derivative coordinates from a Content or derivative artifact."""

    ids = _reference_ids(artifact, "mediaDerivativeIds")
    if ids:
        return ids
    raise InvariantViolation(
        "pipeline content artifact has no derivative coordinates",
        details={"nodeId": artifact.node_id},
    )


def latest_committed_at(items: Sequence[Mapping[str, object]]) -> str | None:
    values = sorted(str(item["committedAt"]) for item in items if item.get("committedAt"))
    return values[-1] if values else None


def _media_item_payload(
    contract: PipelineV2SourceContract,
    pin: PipelineV2SourceVersion,
    version: MediaItemVersionRecord,
) -> JsonObject:
    return {
        "mediaSetRef": contract.resource_ref,
        "mediaSetId": contract.source_id,
        "mediaItemVersionId": version.media_item_version_id,
        "mediaItemId": version.media_item_id,
        "logicalPath": pin.metadata.get("logicalPath"),
        "contentHash": version.content_hash,
        "byteSize": version.byte_size,
        "format": version.format,
        "mimeType": version.sniffed_mime_type,
        "committedAt": version.committed_at,
        "sourceLocator": {
            "mediaSetRef": contract.resource_ref,
            "logicalPath": pin.metadata.get("logicalPath"),
            **dict(version.source_ref or {}),
        },
        "securityEnvelope": dict(version.security_envelope),
    }


def _reference_ids(
    artifact: PipelineV2RuntimeArtifact,
    field: str,
) -> list[str]:
    value = artifact.artifact_ref.get(field)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _source_media_references(items: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    references = [required_source_media_reference(item) for item in items]
    unique = {str(reference["mediaItemVersionId"]): reference for reference in references}
    return [unique[version_id] for version_id in sorted(unique)]
