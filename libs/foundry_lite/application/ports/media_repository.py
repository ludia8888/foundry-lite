from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

MediaTransactionStatus = str
MediaItemVersionStatus = str


@dataclass(frozen=True)
class SecurityEnvelope:
    """Tenant/classification envelope copied onto every media version and derivative.

    Derivatives, content units, embeddings, and index documents must never carry a
    weaker envelope than their source (see ADR-0001 invariant 3 / doc §12.1).
    """

    tenant_id: str
    classification: str
    policy_version: str
    allowed_principal_set_id: str | None = None
    has_legal_hold: bool = False


@dataclass(frozen=True)
class MediaReference:
    """Immutable pointer to one media item version (never a bare logical path).

    The reference pins ``media_item_version_id``; ``logical_path`` is display/head-lookup
    only, so a later head overwrite never changes what an existing reference resolves to.
    """

    media_set_id: str
    media_item_id: str
    media_item_version_id: str
    logical_path: str
    content_hash: str


@dataclass(frozen=True)
class MediaSetRecord:
    """Durable catalog row for one media set (a first-class asset, parallel to a dataset)."""

    media_set_id: str
    tenant_id: str
    namespace: str
    name: str
    schema_type: str
    primary_format: str
    allowed_input_formats: tuple[str, ...]
    transaction_policy: str
    storage_profile: str
    processing_profile: str
    classification: str
    retention_policy_id: str | None
    created_at: str
    updated_at: str
    is_virtual: bool = False


@dataclass(frozen=True)
class MediaTransactionRecord:
    """Durable media transaction row (OPEN | COMMITTED | ABORTED)."""

    media_transaction_id: str
    tenant_id: str
    media_set_id: str
    status: str
    mode: str
    idempotency_key: str
    request_fingerprint: str
    opened_at: str
    committed_at: str | None = None
    aborted_at: str | None = None
    error: dict[str, object] | None = None
    trace: dict[str, object] | None = None


@dataclass(frozen=True)
class MediaItemRecord:
    """Logical-path identity row; ``head_version_id`` is a moving pointer, not history."""

    media_item_id: str
    tenant_id: str
    media_set_id: str
    logical_path: str
    head_version_id: str | None
    is_deleted: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MediaItemVersionRecord:
    """Immutable bytes-identity row (STAGED | QUARANTINED | COMMITTED | DELETED)."""

    media_item_version_id: str
    tenant_id: str
    media_item_id: str
    media_transaction_id: str
    version_number: int
    blob_key: str
    content_hash: str
    byte_size: int
    supplied_mime_type: str
    sniffed_mime_type: str
    schema_type: str
    format: str
    probe_metadata: dict[str, object]
    security_envelope: dict[str, object]
    source_ref: dict[str, object] | None
    status: str
    created_at: str
    committed_at: str | None = None
    # L7 retention: set when a retention policy marks this version as a sweep candidate.
    retention_marked_at: str | None = None
    # L7 legal hold: a held version is never purged, even past its retention grace.
    has_legal_hold: bool = False


class MediaRepository(Protocol):
    """DB boundary for the media catalog, transactions, items, and immutable versions.

    Provides only CAS / uniqueness / durable-row operations — no domain decisions.
    Application services own the transaction lifecycle and pass the handle in.
    """

    def create_media_set_or_get_existing(
        self, *, transaction: TransactionContext, record: MediaSetRecord
    ) -> MediaSetRecord | None:
        """Insert a media set, returning the existing winner on (tenant, namespace, name) conflict."""
        ...

    def get_media_sets(self, *, transaction: TransactionContext, ids: list[str]) -> list[MediaSetRecord]:
        """Return media set rows for the given ids (batch-first to avoid N+1)."""
        ...

    def media_set_by_ref(
        self, *, transaction: TransactionContext, tenant_id: str, namespace: str, name: str
    ) -> MediaSetRecord | None:
        """Resolve one media set by its tenant-scoped catalog reference."""
        ...

    def create_open_transaction(
        self, *, transaction: TransactionContext, record: MediaTransactionRecord
    ) -> MediaTransactionRecord | None:
        """Open a media transaction, returning the existing winner on idempotency conflict."""
        ...

    def transaction_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, media_transaction_id: str
    ) -> MediaTransactionRecord | None:
        """Return one tenant-scoped media transaction by id."""
        ...

    def commit_transaction(
        self, *, transaction: TransactionContext, tenant_id: str, media_transaction_id: str, committed_at: str
    ) -> MediaTransactionRecord | None:
        """CAS a media transaction OPEN -> COMMITTED; a replay finds it already committed."""
        ...

    def abort_transaction(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_transaction_id: str,
        aborted_at: str,
        error: dict[str, object] | None,
    ) -> MediaTransactionRecord | None:
        """CAS a media transaction OPEN -> ABORTED with operator error evidence."""
        ...

    def upsert_media_item(self, *, transaction: TransactionContext, record: MediaItemRecord) -> MediaItemRecord:
        """Insert a logical-path item or return the existing one (uq tenant/set/path)."""
        ...

    def media_item_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, media_item_id: str
    ) -> MediaItemRecord | None:
        """Return one tenant-scoped logical-path item (head pointer + identity) by id."""
        ...

    def cas_item_head_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_item_id: str,
        expected_head_version_id: str | None,
        new_head_version_id: str,
        updated_at: str,
    ) -> MediaItemRecord | None:
        """CAS the logical-path head pointer; concurrent same-path commits cannot lose a version."""
        ...

    def insert_version(
        self, *, transaction: TransactionContext, record: MediaItemVersionRecord
    ) -> MediaItemVersionRecord | None:
        """Insert an immutable version, returning the existing winner on blob/version-number conflict."""
        ...

    def media_item_version_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, media_item_version_id: str
    ) -> MediaItemVersionRecord | None:
        """Return one tenant-scoped immutable version by id."""
        ...

    def get_media_item_versions(
        self, *, transaction: TransactionContext, ids: list[str]
    ) -> list[MediaItemVersionRecord]:
        """Return immutable version rows for the given ids (batch-first)."""
        ...

    def next_version_number(self, *, transaction: TransactionContext, media_item_id: str) -> int:
        """Return the next monotonic version number for a logical-path item."""
        ...

    def commit_staged_versions(
        self, *, transaction: TransactionContext, tenant_id: str, media_transaction_id: str, committed_at: str
    ) -> list[MediaItemVersionRecord]:
        """Flip a transaction's STAGED versions to COMMITTED atomically with the transaction commit."""
        ...

    def fetch_unreachable_staged_versions(
        self, *, transaction: TransactionContext, tenant_id: str, older_than: str
    ) -> list[MediaItemVersionRecord]:
        """Return STAGED versions whose transaction aborted or never committed, for orphan sweep."""
        ...

    # -- L7 retention / legal hold / purge -------------------------------------

    def mark_versions_for_retention(
        self, *, transaction: TransactionContext, tenant_id: str, media_item_version_ids: list[str], marked_at: str
    ) -> int:
        """Set ``retention_marked_at`` on the given versions (sweep candidates). Returns rows marked."""
        ...

    def set_version_legal_hold(
        self, *, transaction: TransactionContext, tenant_id: str, media_item_version_id: str, has_legal_hold: bool
    ) -> MediaItemVersionRecord | None:
        """Toggle the legal hold flag on one version; a held version is never purged."""
        ...

    def fetch_retention_marked_versions(
        self, *, transaction: TransactionContext, tenant_id: str, marked_before: str
    ) -> list[MediaItemVersionRecord]:
        """Return versions whose ``retention_marked_at`` is at or before the grace cutoff."""
        ...

    def has_reference_binding(
        self, *, transaction: TransactionContext, tenant_id: str, media_item_version_id: str
    ) -> bool:
        """True if any MediaReference binding targets this version (reachability source)."""
        ...

    def purge_version(self, *, transaction: TransactionContext, tenant_id: str, media_item_version_id: str) -> None:
        """Physically delete one version and its derivatives, content units, and access caches."""
        ...
