"""Canonical document-context evidence shared by retrieval and citation verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.content_index import ContentSearchHit
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord


@dataclass(frozen=True, slots=True)
class DocumentContextEvidence:
    """Authoritative content-unit fields that define one model-visible document context."""

    content_unit_id: str
    source_media_item_version_id: str
    media_derivative_id: str
    page_number: int | None
    start_ms: int | None
    end_ms: int | None
    bbox: Mapping[str, object] | None
    source_locator: Mapping[str, object] | None
    chunk_spec_hash: str
    text_hash: str
    text: str


def document_context_from_hit(hit: ContentSearchHit) -> DocumentContextEvidence:
    return DocumentContextEvidence(
        content_unit_id=hit.content_unit_id,
        source_media_item_version_id=hit.source_media_item_version_id,
        media_derivative_id=hit.media_derivative_id or "",
        page_number=hit.page_number,
        start_ms=hit.start_ms,
        end_ms=hit.end_ms,
        bbox=hit.bbox,
        source_locator=hit.source_locator,
        chunk_spec_hash=hit.chunk_spec_hash,
        text_hash=hit.text_hash or "",
        text=hit.text,
    )


def document_context_from_unit(unit: ContentUnitRecord) -> DocumentContextEvidence:
    return DocumentContextEvidence(
        content_unit_id=unit.content_unit_id,
        source_media_item_version_id=unit.source_media_item_version_id,
        media_derivative_id=unit.derivative_id,
        page_number=unit.page_number,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        bbox=unit.bbox,
        source_locator=unit.source_locator,
        chunk_spec_hash=unit.chunk_spec_hash,
        text_hash=unit.text_hash,
        text=unit.text,
    )


def document_context_text(evidence: DocumentContextEvidence) -> str:
    """Return the exact JSON block compiled into the model context."""

    return json.dumps(
        _document_context_payload(evidence),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def document_context_hash(evidence: DocumentContextEvidence) -> str:
    payload = document_context_text(evidence).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _document_context_payload(evidence: DocumentContextEvidence) -> dict[str, object]:
    return {
        "contentUnitId": evidence.content_unit_id,
        "sourceMediaItemVersionId": evidence.source_media_item_version_id,
        "mediaDerivativeId": evidence.media_derivative_id,
        "pageNumber": evidence.page_number,
        "startMs": evidence.start_ms,
        "endMs": evidence.end_ms,
        "bbox": dict(evidence.bbox) if evidence.bbox is not None else None,
        "timecode": _timecode(evidence),
        "sourceLocator": dict(evidence.source_locator) if evidence.source_locator is not None else None,
        "chunkSpecHash": evidence.chunk_spec_hash,
        "textHash": evidence.text_hash,
        "text": evidence.text,
    }


def _timecode(evidence: DocumentContextEvidence) -> dict[str, object] | None:
    if evidence.start_ms is None and evidence.end_ms is None:
        return None
    return {"startMs": evidence.start_ms, "endMs": evidence.end_ms}
