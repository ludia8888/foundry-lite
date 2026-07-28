"""Application port contract for media processor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.embedding_model import EmbeddingVector


def _empty_parameters() -> Mapping[str, object]:
    return {}


@dataclass(frozen=True)
class ProcessorSpec:
    """Pinned processor identity; its canonical hash is the processing idempotency key (doc §7.3)."""

    processor: str
    processor_version: str
    model: str | None = None
    model_version: str | None = None
    parameters: Mapping[str, object] = field(default_factory=_empty_parameters)


@dataclass(frozen=True)
class MediaProcessingRequest:
    """One processing request bound to an exact media version + processing spec hash.

    ``source_path`` is a local sandbox file the application materialized for the processor
    (doc §5.2: processors get a scoped grant or sandbox path, never raw storage credentials).
    """

    tenant_id: str
    media_item_version_id: str
    blob_key: str
    spec: ProcessorSpec
    processing_spec_hash: str
    source_path: str | None = None
    source_format: str | None = None
    source_mime_type: str | None = None


@dataclass(frozen=True)
class ProcessedContentUnit:
    """One normalized unit a processor extracted (a PDF page, an OCR region, an audio segment)
    with a verifiable hash. Time-coded fields are set by ASR/AV processors (doc §8.3); page-based
    processors leave them ``None``. A vision processor (L11) attaches an ``embedding`` computed
    from the unit's IMAGE (a CLIP frame vector) — for those units the vector, not text, is the
    searchable content, so it is persisted on the unit and projected as-is at index time."""

    unit_kind: str
    ordinal: int
    text: str
    text_hash: str
    page_number: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    bbox: Mapping[str, object] | None = None
    parent_content_unit_id: str | None = None
    source_locator: Mapping[str, object] | None = None
    structure: Mapping[str, object] | None = None
    confidence: float | None = None
    speaker: str | None = None
    language: str | None = None
    embedding: EmbeddingVector = ()


@dataclass(frozen=True)
class MediaProcessingResult:
    """Result of one processing run: the derivative artifact identity + its content units."""

    media_item_version_id: str
    processing_spec_hash: str
    derivative_kind: str = ""
    content_hash: str = ""
    mime_type: str = "text/plain"
    units: tuple[ProcessedContentUnit, ...] = ()
    processing_evidence: Mapping[str, object] | None = None


class MediaProcessorAdapter(Protocol):
    """Typed processor boundary (OCR/ASR/FFmpeg/embedding), distinct from the SQL/Parquet ComputeAdapter.

    Contract only in this sprint; the first concrete processor (PDF raw text) lands in a later PR.
    Processors receive scoped read/write grants or sandbox paths, never raw storage credentials.
    """

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this processor profile."""
        ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        """Return whether this processor can handle the request's schema/format/spec."""
        ...

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        """Run the pinned processing spec against one immutable media version."""
        ...
