"""Application port contract for media reference binding repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


@dataclass(frozen=True)
class MediaReferenceBindingRecord:
    """One Ontology object property bound to an immutable media version (doc §1.6/§6.4)."""

    media_reference_binding_id: str
    tenant_id: str
    holder_type: str
    holder_id: str
    property_name: str
    media_set_id: str
    media_item_id: str
    media_item_version_id: str
    logical_path: str
    content_hash: str
    security_envelope: dict[str, object]
    idempotency_key: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AttachmentHolderAssociationRecord:
    """One lifetime attachment-to-object association; never deleted with the live binding."""

    association_id: str
    tenant_id: str
    media_item_version_id: str
    holder_type: str
    holder_id: str
    first_action_run_id: str
    created_at: str


@dataclass(frozen=True)
class AttachmentHolderReservationResult:
    is_reserved: bool
    is_existing: bool
    holder_count: int


class MediaReferenceBindingRepository(Protocol):
    """DB boundary for media-reference bindings. CAS / uniqueness / durable-row only."""

    def binding_by_holder(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        holder_type: str,
        holder_id: str,
        property_name: str,
    ) -> MediaReferenceBindingRecord | None:
        """Return the current binding for one (object, property), or None."""
        ...

    def bindings_for_media_versions(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_item_version_ids: list[str],
    ) -> list[MediaReferenceBindingRecord]:
        """Reverse lookup (media version -> owning object), batched to stay N+1-safe.

        Returns every tenant-scoped binding whose ``media_item_version_id`` is in the input
        set, so a content/media search hit can be lifted to the holder object(s) that
        reference it (L12b OAG: the result unit is the object, reached via the media edge).
        """
        ...

    def create_binding(self, *, transaction: TransactionContext, record: MediaReferenceBindingRecord) -> None:
        """Insert a new binding (uq tenant/holder_type/holder_id/property)."""
        ...

    def update_binding(self, *, transaction: TransactionContext, record: MediaReferenceBindingRecord) -> None:
        """Re-point an existing binding to a new media version + idempotency key."""
        ...

    def delete_bindings_by_holder_property(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        holder_type: str,
        holder_id: str,
        property_name: str,
    ) -> int:
        """Delete the prior direct/list bindings for one property before an atomic replacement."""
        ...

    def delete_bindings_by_holder(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        holder_type: str,
        holder_id: str,
    ) -> int:
        """Delete every binding when its owning object is soft-deleted."""
        ...

    def reserve_attachment_holder(
        self,
        *,
        transaction: TransactionContext,
        record: AttachmentHolderAssociationRecord,
        max_holders: int,
    ) -> AttachmentHolderReservationResult:
        """Atomically reserve a lifetime holder slot, retaining deleted/unmapped history forever."""
        ...

    def attachment_associations_for_media_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        media_item_version_id: str,
    ) -> list[AttachmentHolderAssociationRecord]:
        """Return immutable lifetime holder associations for audit and contract verification."""
        ...
