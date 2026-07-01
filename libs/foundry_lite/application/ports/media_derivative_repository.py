"""Application port contract for media derivative repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.transaction_context import TransactionContext


@dataclass(frozen=True)
class MediaDerivativeRecord:
    """Durable row for one processing output pinned to (source version + processor spec).

    The (source version, kind, spec hash, model version, params hash) tuple is the
    media-processing idempotency anchor — a replay resolves to the existing row.
    """

    media_derivative_id: str
    tenant_id: str
    source_media_item_version_id: str
    derivative_kind: str
    processor_spec_hash: str
    processor_name: str
    processor_version: str
    model_name: str | None
    model_version: str
    params_hash: str
    security_envelope: dict[str, object]
    status: str
    blob_key: str | None = None
    content_hash: str | None = None
    byte_size: int | None = None
    mime_type: str | None = None
    error: dict[str, object] | None = None
    created_at: str = ""
    committed_at: str | None = None


@dataclass(frozen=True)
class ContentUnitRecord:
    """Normalized unit (page/chunk/segment) extracted from a derivative, pinned to its source."""

    content_unit_id: str
    tenant_id: str
    source_media_item_version_id: str
    derivative_id: str
    unit_kind: str
    ordinal: int
    text: str
    text_hash: str
    chunk_spec_hash: str
    security_envelope: dict[str, object]
    page_number: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    bbox: dict[str, object] | None = None
    speaker: str | None = None
    language: str | None = None
    embedding: EmbeddingVector = field(default_factory=tuple)
    created_at: str = ""


@dataclass(frozen=True)
class MediaProcessingRunRecord:
    """One media-processing attempt as durable operator evidence (L0 Operations surface).

    ``status`` is RUNNING -> SUCCEEDED/FAILED. This row records that an attempt happened and
    how it ended; it is NEVER serving truth — only a COMMITTED derivative is (invariant
    ``workflow_status_does_not_replace_domain_commit``). On success it points at the committed
    derivative; on failure it carries the typed failure_kind/reason an operator can act on.
    """

    media_processing_run_id: str
    tenant_id: str
    source_media_item_version_id: str
    processor_name: str
    derivative_kind: str
    processing_spec_hash: str
    status: str
    started_at: str
    created_at: str
    media_derivative_id: str | None = None
    failure_kind: str | None = None
    failure_reason: str | None = None
    finished_at: str | None = None


class MediaDerivativeRepository(Protocol):
    """DB boundary for media derivatives + content units. CAS / uniqueness / durable-row only."""

    def create_media_run(
        self, *, transaction: TransactionContext, record: MediaProcessingRunRecord
    ) -> MediaProcessingRunRecord:
        """Insert a RUNNING processing-run row (operator evidence)."""
        ...

    def complete_media_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_processing_run_id: str,
        status: str,
        finished_at: str,
        media_derivative_id: str | None = None,
        failure_kind: str | None = None,
        failure_reason: str | None = None,
    ) -> MediaProcessingRunRecord | None:
        """Transition a run RUNNING -> SUCCEEDED/FAILED with its outcome evidence."""
        ...

    def get_media_runs(self, *, transaction: TransactionContext, ids: list[str]) -> list[MediaProcessingRunRecord]:
        """Return processing-run rows for the given ids (batch-first)."""
        ...

    def list_media_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        source_media_item_version_id: str | None = None,
        limit: int = 50,
    ) -> list[MediaProcessingRunRecord]:
        """Return tenant-scoped processing runs newest-first (bounded), for the Operations surface."""
        ...

    def create_derivative_or_get_existing(
        self, *, transaction: TransactionContext, record: MediaDerivativeRecord
    ) -> MediaDerivativeRecord | None:
        """Insert a STAGED derivative, returning the existing winner on the idempotency key."""
        ...

    def derivative_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, media_derivative_id: str
    ) -> MediaDerivativeRecord | None:
        """Return one tenant-scoped derivative by id."""
        ...

    def get_derivatives(self, *, transaction: TransactionContext, ids: list[str]) -> list[MediaDerivativeRecord]:
        """Return derivative rows for the given ids (batch-first)."""
        ...

    def commit_derivative(
        self, *, transaction: TransactionContext, tenant_id: str, media_derivative_id: str, committed_at: str
    ) -> MediaDerivativeRecord | None:
        """CAS a derivative STAGED -> COMMITTED; a replay finds it already committed."""
        ...

    def fail_derivative(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_derivative_id: str,
        error: dict[str, object],
        failed_at: str,
    ) -> MediaDerivativeRecord | None:
        """CAS a derivative STAGED -> FAILED with operator error evidence."""
        ...

    def insert_content_units(self, *, transaction: TransactionContext, records: list[ContentUnitRecord]) -> int:
        """Insert content units (idempotent on the ordinal/chunk-spec uniqueness)."""
        ...

    def get_content_units(
        self, *, transaction: TransactionContext, tenant_id: str, derivative_id: str
    ) -> list[ContentUnitRecord]:
        """Return the content units a derivative produced, ordered by ordinal."""
        ...

    def get_content_units_by_ids(self, *, transaction: TransactionContext, ids: list[str]) -> list[ContentUnitRecord]:
        """Return content units for the given ids (batch-first; authoritative truth re-read)."""
        ...

    def get_committed_content_units_for_versions(
        self, *, transaction: TransactionContext, tenant_id: str, source_media_item_version_ids: list[str]
    ) -> list[ContentUnitRecord]:
        """Return committed content units for the given source versions (rebuild-from-truth)."""
        ...

    def fetch_unreachable_staged_derivatives(
        self, *, transaction: TransactionContext, tenant_id: str, older_than: str
    ) -> list[MediaDerivativeRecord]:
        """Return STAGED derivatives older than the cutoff, for orphan-blob cleanup."""
        ...
