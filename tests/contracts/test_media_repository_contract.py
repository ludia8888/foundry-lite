"""Contract for ``MediaRepository`` (ADR-0001 §4 / §5).

This sprint ships only the Protocol + DTOs; the durable SQLAlchemy implementation
and its sqlite/PostgreSQL behavioural suite land with PR2 (local transaction core).
Here we lock the CAS / uniqueness shape against an in-memory fake so the repository
boundary is interchangeable and batch-first (``get_*`` take id lists, never scalars).
"""

from __future__ import annotations

from foundry_lite.application.ports.media_repository import (
    MediaItemRecord,
    MediaItemVersionRecord,
    MediaReference,
    MediaRepository,
    MediaSetRecord,
    MediaTransactionRecord,
    SecurityEnvelope,
)
from foundry_lite.application.ports.transaction_context import TransactionContext


def _media_set() -> MediaSetRecord:
    return MediaSetRecord(
        media_set_id="ms-1",
        tenant_id="t",
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="local",
        classification="confidential",
        retention_policy_id=None,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


def _item() -> MediaItemRecord:
    return MediaItemRecord(
        media_item_id="mi-1",
        tenant_id="t",
        media_set_id="ms-1",
        logical_path="/a.pdf",
        head_version_id=None,
        is_deleted=False,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


class _FakeMediaRepository:
    """In-memory repository pinning the create-or-get-existing CAS contract."""

    def __init__(self) -> None:
        self._sets: dict[str, MediaSetRecord] = {}

    def create_media_set_or_get_existing(
        self, *, transaction: TransactionContext, record: MediaSetRecord
    ) -> MediaSetRecord | None:
        key = (record.tenant_id, record.namespace, record.name)
        existing = next((s for s in self._sets.values() if (s.tenant_id, s.namespace, s.name) == key), None)
        if existing is not None:
            return existing
        self._sets[record.media_set_id] = record
        return None

    def get_media_sets(self, *, transaction: TransactionContext, ids: list[str]) -> list[MediaSetRecord]:
        return [self._sets[i] for i in ids if i in self._sets]

    def media_set_by_ref(
        self, *, transaction: TransactionContext, tenant_id: str, namespace: str, name: str
    ) -> MediaSetRecord | None:
        key = (tenant_id, namespace, name)
        return next((s for s in self._sets.values() if (s.tenant_id, s.namespace, s.name) == key), None)

    def create_open_transaction(
        self, *, transaction: TransactionContext, record: MediaTransactionRecord
    ) -> MediaTransactionRecord | None:
        return None

    def transaction_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, media_transaction_id: str
    ) -> MediaTransactionRecord | None:
        return None

    def commit_transaction(
        self, *, transaction: TransactionContext, tenant_id: str, media_transaction_id: str, committed_at: str
    ) -> MediaTransactionRecord | None:
        return None

    def abort_transaction(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_transaction_id: str,
        aborted_at: str,
        error: dict[str, object] | None,
    ) -> MediaTransactionRecord | None:
        return None

    def upsert_media_item(self, *, transaction: TransactionContext, record: MediaItemRecord) -> MediaItemRecord:
        return record

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
        return None

    def insert_version(
        self, *, transaction: TransactionContext, record: MediaItemVersionRecord
    ) -> MediaItemVersionRecord | None:
        return None

    def media_item_version_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, media_item_version_id: str
    ) -> MediaItemVersionRecord | None:
        return None

    def get_media_item_versions(
        self, *, transaction: TransactionContext, ids: list[str]
    ) -> list[MediaItemVersionRecord]:
        return []

    def next_version_number(self, *, transaction: TransactionContext, media_item_id: str) -> int:
        return 1

    def commit_staged_versions(
        self, *, transaction: TransactionContext, tenant_id: str, media_transaction_id: str, committed_at: str
    ) -> list[MediaItemVersionRecord]:
        return []

    def fetch_unreachable_staged_versions(
        self, *, transaction: TransactionContext, tenant_id: str, older_than: str
    ) -> list[MediaItemVersionRecord]:
        return []


def test_media_repository_create_media_set_is_idempotent() -> None:
    repo: MediaRepository = _FakeMediaRepository()
    handle: TransactionContext = object()  # type: ignore[assignment]

    fresh = repo.create_media_set_or_get_existing(transaction=handle, record=_media_set())
    replay = repo.create_media_set_or_get_existing(transaction=handle, record=_media_set())

    assert fresh is None
    assert replay is not None and replay.media_set_id == "ms-1"
    assert [s.media_set_id for s in repo.get_media_sets(transaction=handle, ids=["ms-1"])] == ["ms-1"]
    assert repo.media_set_by_ref(transaction=handle, tenant_id="t", namespace="legal", name="contracts") is not None
    assert repo.upsert_media_item(transaction=handle, record=_item()).media_item_id == "mi-1"


def test_media_reference_pins_immutable_version_and_envelope_carries_legal_hold() -> None:
    reference = MediaReference(
        media_set_id="ms-1",
        media_item_id="mi-1",
        media_item_version_id="miv-1",
        logical_path="/a.pdf",
        content_hash="h",
    )
    envelope = SecurityEnvelope(tenant_id="t", classification="confidential", policy_version="v1", has_legal_hold=True)

    # A reference pins the version id, not the (mutable) head logical path.
    assert reference.media_item_version_id == "miv-1"
    assert envelope.has_legal_hold is True
