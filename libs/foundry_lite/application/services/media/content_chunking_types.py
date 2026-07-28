"""Typed commands and results for committed Content Unit chunking."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.services.media.content_chunking_rules import ContentChunkSpec, ContentChunkWindow


@dataclass(frozen=True, slots=True)
class CommittedContentUnitSetRef:
    """Reference to all Content Units owned by one COMMITTED derivative."""

    media_derivative_id: str


@dataclass(frozen=True, slots=True)
class ContentChunkOutcome:
    """Committed output identity returned to a Graph v2 content artifact."""

    media_derivative_id: str
    source_media_derivative_id: str
    source_media_item_version_id: str
    status: str
    content_unit_ids: tuple[str, ...]
    chunk_spec_hash: str
    chunk_config_hash: str
    is_duplicate: bool = False

    @property
    def content_unit_count(self) -> int:
        return len(self.content_unit_ids)


@dataclass(frozen=True, slots=True)
class SourceContentUnitSet:
    derivative: MediaDerivativeRecord
    units: tuple[ContentUnitRecord, ...]
    input_set_hash: str


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    parent: ContentUnitRecord
    window: ContentChunkWindow
    ordinal: int


@dataclass(frozen=True, slots=True)
class ChunkCommit:
    source_ref: CommittedContentUnitSetRef
    source: SourceContentUnitSet
    spec: ContentChunkSpec
    chunk_config_hash: str
    chunk_spec_hash: str
    drafts: tuple[ChunkDraft, ...]
    run_id: str
