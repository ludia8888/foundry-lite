"""Pure record construction for media processing outcomes."""

from __future__ import annotations

import hashlib
import json

from foundry_lite.application.ports.media_derivative_repository import (
    ContentUnitRecord,
    MediaDerivativeRecord,
)
from foundry_lite.application.ports.media_processor import MediaProcessingResult, ProcessorSpec
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.media.content_unit_metadata import source_locator_payload
from foundry_lite.domain.context import RequestContext


def _canonical_spec_hash(spec: ProcessorSpec) -> str:
    canonical = json.dumps(
        {
            "processor": spec.processor,
            "processorVersion": spec.processor_version,
            "model": spec.model,
            "modelVersion": spec.model_version,
            "parameters": spec.parameters,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _derivative_record(
    ctx: RequestContext,
    version_id: str,
    spec: ProcessorSpec,
    spec_hash: str,
    envelope: dict[str, object],
    result: MediaProcessingResult | None,
    *,
    status: str,
    error: dict[str, object] | None = None,
) -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id=_new_id("mder"),
        tenant_id=ctx.tenant_id,
        source_media_item_version_id=version_id,
        derivative_kind=result.derivative_kind if result is not None else spec.processor,
        processor_spec_hash=spec_hash,
        processor_name=spec.processor,
        processor_version=spec.processor_version,
        model_name=spec.model,
        model_version=spec.model_version or "",
        params_hash=spec_hash,
        security_envelope=envelope,
        status=status,
        content_hash=result.content_hash if result is not None else None,
        mime_type=result.mime_type if result is not None else None,
        error=error,
        created_at=_now(),
    )


def _content_unit_records(
    ctx: RequestContext,
    version_id: str,
    derivative_id: str,
    spec_hash: str,
    envelope: dict[str, object],
    result: MediaProcessingResult,
) -> list[ContentUnitRecord]:
    return [
        ContentUnitRecord(
            content_unit_id=_new_id("cu"),
            tenant_id=ctx.tenant_id,
            source_media_item_version_id=version_id,
            derivative_id=derivative_id,
            unit_kind=unit.unit_kind,
            ordinal=unit.ordinal,
            page_number=unit.page_number,
            start_ms=unit.start_ms,
            end_ms=unit.end_ms,
            bbox=dict(unit.bbox) if unit.bbox is not None else None,
            parent_content_unit_id=unit.parent_content_unit_id,
            source_locator=source_locator_payload(unit),
            structure=dict(unit.structure) if unit.structure is not None else None,
            confidence=unit.confidence,
            speaker=unit.speaker,
            language=unit.language,
            embedding=unit.embedding,
            text=unit.text,
            text_hash=unit.text_hash,
            chunk_spec_hash=spec_hash,
            security_envelope=envelope,
            created_at=_now(),
        )
        for unit in result.units
    ]
