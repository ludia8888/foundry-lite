"""Media derivative payload projection shared by pipeline preview callers."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.media_processor import MediaProcessingResult, ProcessedContentUnit
from foundry_lite.application.ports.media_processor_registry import MediaProcessorDescriptor
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.media.content_unit_metadata import source_locator_payload
from foundry_lite.application.services.pipeline_media_reference import attach_source_media_reference

JsonObject = dict[str, object]


def _derivative_payload(
    result: MediaProcessingResult,
    version: MediaItemVersionRecord,
    descriptor: MediaProcessorDescriptor,
    source_media_reference: Mapping[str, object],
) -> JsonObject:
    processing_evidence = result.processing_evidence
    payload: JsonObject = {
        "mediaItemVersionId": version.media_item_version_id,
        "processingSpecHash": result.processing_spec_hash,
        "derivativeKind": result.derivative_kind,
        "contentHash": result.content_hash,
        "mimeType": result.mime_type,
        "processorId": f"{descriptor.processor}@{descriptor.processor_version}",
        "model": {"name": descriptor.model.name, "version": descriptor.model.version},
        "sourceMediaReference": dict(source_media_reference),
        "securityEnvelope": dict(version.security_envelope),
        "units": [
            _content_unit_payload(unit, version, source_media_reference, processing_evidence) for unit in result.units
        ],
    }
    return _with_processing_evidence(payload, processing_evidence)


def _content_unit_payload(
    unit: ProcessedContentUnit,
    version: MediaItemVersionRecord,
    source_media_reference: Mapping[str, object],
    processing_evidence: Mapping[str, object] | None = None,
) -> JsonObject:
    source_locator = source_locator_payload(unit)
    payload: JsonObject = {
        "sourceMediaItemVersionId": version.media_item_version_id,
        "unitKind": unit.unit_kind,
        "ordinal": unit.ordinal,
        "text": unit.text,
        "textHash": unit.text_hash,
        "pageNumber": unit.page_number,
        "startMs": unit.start_ms,
        "endMs": unit.end_ms,
        "bbox": dict(unit.bbox) if unit.bbox is not None else None,
        "parentContentUnitId": unit.parent_content_unit_id,
        "sourceLocator": source_locator,
        "structure": dict(unit.structure) if unit.structure is not None else None,
        "confidence": unit.confidence,
        "speaker": unit.speaker,
        "language": unit.language,
        "securityEnvelope": dict(version.security_envelope),
    }
    referenced = attach_source_media_reference(
        payload,
        {version.media_item_version_id: dict(source_media_reference)},
    )
    return _with_processing_evidence(referenced, processing_evidence)


def _with_processing_evidence(
    payload: JsonObject,
    processing_evidence: Mapping[str, object] | None,
) -> JsonObject:
    if processing_evidence is None:
        return payload
    return {**payload, "processingEvidence": dict(processing_evidence)}
