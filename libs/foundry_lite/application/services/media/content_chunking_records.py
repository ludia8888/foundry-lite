"""Validation, fingerprints, and durable records for Content Unit chunking."""

from __future__ import annotations

from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.media.content_chunking_rules import (
    CONTENT_CHUNK_DERIVATIVE_KIND,
    CONTENT_CHUNK_PROCESSOR,
    CONTENT_CHUNK_PROCESSOR_VERSION,
    ContentChunkSpec,
    ContentChunkWindow,
    chunk_text_windows,
)
from foundry_lite.application.services.media.content_chunking_types import (
    ChunkCommit,
    ChunkDraft,
    ContentChunkOutcome,
    SourceContentUnitSet,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed

_CONTENT_UNIT_SET_MIME_TYPE = "application/vnd.foundry.content-unit-set+json"


def require_committed_derivative(derivative: MediaDerivativeRecord | None, derivative_id: str) -> None:
    if derivative is None:
        raise NotFound("content unit set derivative not found", details={"mediaDerivativeId": derivative_id})
    if derivative.status != "COMMITTED":
        raise ConflictDetected(
            "content unit chunking requires a committed input derivative",
            details={"mediaDerivativeId": derivative_id, "status": derivative.status},
        )


def validate_source_chain(
    ctx: RequestContext,
    derivative: MediaDerivativeRecord,
    version: MediaItemVersionRecord | None,
    units: tuple[ContentUnitRecord, ...],
) -> None:
    _require_committed_source_version(version)
    _require_source_units(derivative, units)
    assert version is not None
    _require_source_identity(ctx, derivative, version, units)
    _require_source_security(derivative, version, units)
    if not _has_processor_pins(derivative):
        raise ConflictDetected("content unit set derivative processor pins are incomplete")


def _require_committed_source_version(version: MediaItemVersionRecord | None) -> None:
    if version is None or version.status != "COMMITTED":
        raise ConflictDetected("content unit set source media version is not committed")


def _require_source_units(
    derivative: MediaDerivativeRecord,
    units: tuple[ContentUnitRecord, ...],
) -> None:
    if not units:
        raise ValidationFailed(
            "committed content unit set is empty",
            details={"mediaDerivativeId": derivative.media_derivative_id},
        )


def _require_source_identity(
    ctx: RequestContext,
    derivative: MediaDerivativeRecord,
    version: MediaItemVersionRecord,
    units: tuple[ContentUnitRecord, ...],
) -> None:
    expected = (ctx.tenant_id, derivative.source_media_item_version_id, derivative.media_derivative_id)
    if version.tenant_id != ctx.tenant_id or not all(_unit_identity(unit) == expected for unit in units):
        raise ConflictDetected("content unit set identity chain does not match its committed derivative")


def _require_source_security(
    derivative: MediaDerivativeRecord,
    version: MediaItemVersionRecord,
    units: tuple[ContentUnitRecord, ...],
) -> None:
    if not _inherits(version.security_envelope, derivative.security_envelope):
        raise ConflictDetected("content unit set derivative security envelope was weakened")
    if not all(unit.security_envelope == derivative.security_envelope for unit in units):
        raise ConflictDetected("content unit security envelope does not match its committed derivative")


def chunk_drafts(
    units: tuple[ContentUnitRecord, ...],
    spec: ContentChunkSpec,
) -> tuple[ChunkDraft, ...]:
    drafts: list[ChunkDraft] = []
    ordinal = 0
    for unit in units:
        for window in chunk_text_windows(unit.text, spec):
            drafts.append(ChunkDraft(parent=unit, window=window, ordinal=ordinal))
            ordinal += 1
    return tuple(drafts)


def require_chunk_drafts(drafts: tuple[ChunkDraft, ...], derivative_id: str) -> None:
    if drafts:
        return
    raise ValidationFailed(
        "committed content unit set contains no chunkable text",
        details={"mediaDerivativeId": derivative_id},
    )


def content_unit_records(
    ctx: RequestContext,
    derivative_id: str,
    source: SourceContentUnitSet,
    command: ChunkCommit,
) -> list[ContentUnitRecord]:
    created_at = _now()
    version_id = source.derivative.source_media_item_version_id
    return [
        _content_unit_record(ctx, derivative_id, version_id, command, draft, created_at) for draft in command.drafts
    ]


def _content_unit_record(
    ctx: RequestContext,
    derivative_id: str,
    version_id: str,
    command: ChunkCommit,
    draft: ChunkDraft,
    created_at: str,
) -> ContentUnitRecord:
    parent = draft.parent
    return ContentUnitRecord(
        content_unit_id=_new_id("cu"),
        tenant_id=ctx.tenant_id,
        source_media_item_version_id=version_id,
        derivative_id=derivative_id,
        unit_kind="chunk",
        ordinal=draft.ordinal,
        text=draft.window.text,
        text_hash=draft.window.text_hash,
        chunk_spec_hash=command.chunk_spec_hash,
        security_envelope=dict(parent.security_envelope),
        page_number=parent.page_number,
        start_ms=parent.start_ms,
        end_ms=parent.end_ms,
        bbox=dict(parent.bbox) if parent.bbox is not None else None,
        parent_content_unit_id=parent.content_unit_id,
        source_locator=dict(parent.source_locator) if parent.source_locator is not None else None,
        structure=_chunk_structure(parent, command.spec, draft.window),
        confidence=parent.confidence,
        speaker=parent.speaker,
        language=parent.language,
        created_at=created_at,
    )


def derivative_record(
    ctx: RequestContext,
    source: SourceContentUnitSet,
    command: ChunkCommit,
) -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id=_new_id("mder"),
        tenant_id=ctx.tenant_id,
        source_media_item_version_id=source.derivative.source_media_item_version_id,
        derivative_kind=CONTENT_CHUNK_DERIVATIVE_KIND,
        processor_spec_hash=command.chunk_spec_hash,
        processor_name=CONTENT_CHUNK_PROCESSOR,
        processor_version=CONTENT_CHUNK_PROCESSOR_VERSION,
        model_name=None,
        model_version="",
        params_hash=command.chunk_config_hash,
        security_envelope=dict(source.derivative.security_envelope),
        status="STAGED",
        content_hash=_output_content_hash(command.drafts),
        byte_size=sum(len(draft.window.text.encode("utf-8")) for draft in command.drafts),
        mime_type=_CONTENT_UNIT_SET_MIME_TYPE,
        created_at=_now(),
    )


def _chunk_structure(
    parent: ContentUnitRecord,
    spec: ContentChunkSpec,
    window: ContentChunkWindow,
) -> dict[str, object]:
    structure = dict(parent.structure) if parent.structure is not None else {}
    structure["foundryContentChunk"] = {
        "localOrdinal": window.local_ordinal,
        "startToken": window.start_token,
        "endToken": window.end_token,
        "chunkSize": spec.chunk_size,
        "overlap": spec.overlap,
        "tokenizerVersion": spec.tokenizer_version,
    }
    return structure


def input_set_hash(
    derivative: MediaDerivativeRecord,
    units: tuple[ContentUnitRecord, ...],
) -> str:
    return _json_hash(
        {
            "mediaDerivativeId": derivative.media_derivative_id,
            "processorSpecHash": derivative.processor_spec_hash,
            "securityEnvelope": derivative.security_envelope,
            "contentUnits": [_source_unit_fingerprint(unit) for unit in units],
        }
    )


def _source_unit_fingerprint(unit: ContentUnitRecord) -> dict[str, object]:
    return {
        "contentUnitId": unit.content_unit_id,
        "unitKind": unit.unit_kind,
        "ordinal": unit.ordinal,
        "storedTextHash": unit.text_hash,
        "actualTextHash": _json_hash({"text": unit.text}),
        "pageNumber": unit.page_number,
        "startMs": unit.start_ms,
        "endMs": unit.end_ms,
        "bbox": unit.bbox,
        "sourceLocator": unit.source_locator,
        "structure": unit.structure,
        "securityEnvelope": unit.security_envelope,
    }


def processing_spec_hash(config_hash: str, source_set_hash: str) -> str:
    return _json_hash(
        {
            "chunkConfigHash": config_hash,
            "inputContentUnitSetHash": source_set_hash,
            "derivativeKind": CONTENT_CHUNK_DERIVATIVE_KIND,
        }
    )


def _output_content_hash(drafts: tuple[ChunkDraft, ...]) -> str:
    return _json_hash(
        {
            "chunks": [
                {
                    "ordinal": draft.ordinal,
                    "parentContentUnitId": draft.parent.content_unit_id,
                    "textHash": draft.window.text_hash,
                }
                for draft in drafts
            ]
        }
    )


def require_same_input_set(expected: SourceContentUnitSet, current: SourceContentUnitSet) -> None:
    if expected.input_set_hash == current.input_set_hash:
        return
    raise ConflictDetected(
        "committed content unit set changed before chunk commit",
        details={"mediaDerivativeId": expected.derivative.media_derivative_id},
    )


def require_replay_units(
    units: list[ContentUnitRecord],
    expected: list[ContentUnitRecord],
    output_derivative_id: str,
) -> None:
    if unit_signatures(units) != unit_signatures(expected):
        raise ConflictDetected(
            "committed chunk derivative does not match the deterministic replay output",
            details={"mediaDerivativeId": output_derivative_id},
        )


def chunk_outcome(
    command: ChunkCommit,
    source: SourceContentUnitSet,
    derivative_id: str,
    units: list[ContentUnitRecord],
    is_duplicate: bool,
) -> ContentChunkOutcome:
    ordered = sorted(units, key=lambda unit: unit.ordinal)
    return ContentChunkOutcome(
        media_derivative_id=derivative_id,
        source_media_derivative_id=source.derivative.media_derivative_id,
        source_media_item_version_id=source.derivative.source_media_item_version_id,
        status="COMMITTED",
        content_unit_ids=tuple(unit.content_unit_id for unit in ordered),
        chunk_spec_hash=command.chunk_spec_hash,
        chunk_config_hash=command.chunk_config_hash,
        is_duplicate=is_duplicate,
    )


def commit_payload(
    command: ChunkCommit,
    derivative_id: str,
    content_unit_count: int,
) -> dict[str, object]:
    return {
        "mediaDerivativeId": derivative_id,
        "sourceMediaDerivativeId": command.source.derivative.media_derivative_id,
        "sourceMediaItemVersionId": command.source.derivative.source_media_item_version_id,
        "sourceContentUnitCount": len(command.source.units),
        "derivativeKind": CONTENT_CHUNK_DERIVATIVE_KIND,
        "processorName": CONTENT_CHUNK_PROCESSOR,
        "processorVersion": CONTENT_CHUNK_PROCESSOR_VERSION,
        "contentUnitCount": content_unit_count,
        "chunkSize": command.spec.chunk_size,
        "overlap": command.spec.overlap,
        "tokenizerVersion": command.spec.tokenizer_version,
        "chunkSpecHash": command.chunk_spec_hash,
        "chunkConfigHash": command.chunk_config_hash,
    }


def source_unit_order(unit: ContentUnitRecord) -> tuple[int, str, str]:
    return (unit.ordinal, unit.unit_kind, unit.content_unit_id)


def unit_signatures(units: list[ContentUnitRecord]) -> set[str]:
    return {_json_hash(_unit_signature_payload(unit)) for unit in units}


def _unit_signature_payload(unit: ContentUnitRecord) -> dict[str, object]:
    return {
        "ordinal": unit.ordinal,
        "parentContentUnitId": unit.parent_content_unit_id,
        "text": unit.text,
        "textHash": unit.text_hash,
        "chunkSpecHash": unit.chunk_spec_hash,
        "derivativeId": unit.derivative_id,
        "pageNumber": unit.page_number,
        "startMs": unit.start_ms,
        "endMs": unit.end_ms,
        "bbox": unit.bbox,
        "sourceLocator": unit.source_locator,
        "structure": unit.structure,
        "confidence": unit.confidence,
        "speaker": unit.speaker,
        "language": unit.language,
        "securityEnvelope": unit.security_envelope,
    }


def safe_failure_kind(exc: Exception) -> str:
    if isinstance(exc, FoundryLiteError):
        return exc.code.lower()
    return exc.__class__.__name__.lower()


def _unit_identity(unit: ContentUnitRecord) -> tuple[str, str, str]:
    return (unit.tenant_id, unit.source_media_item_version_id, unit.derivative_id)


def _has_processor_pins(derivative: MediaDerivativeRecord) -> bool:
    required = (
        derivative.processor_spec_hash,
        derivative.processor_name,
        derivative.processor_version,
        derivative.params_hash,
    )
    return all(value.strip() for value in required) and (
        derivative.model_name is None or bool(derivative.model_version.strip())
    )


def _inherits(source: dict[str, object], derived: dict[str, object]) -> bool:
    return all(derived.get(key) == value for key, value in source.items())
