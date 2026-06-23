from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


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
    """One processing request bound to an exact media version + processing spec hash."""

    tenant_id: str
    media_item_version_id: str
    blob_key: str
    spec: ProcessorSpec
    processing_spec_hash: str


@dataclass(frozen=True)
class MediaProcessingResult:
    """Result envelope for a processing run (derivative/content/metadata/embedding outputs)."""

    media_item_version_id: str
    processing_spec_hash: str
    outputs: tuple[object, ...] = ()


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
