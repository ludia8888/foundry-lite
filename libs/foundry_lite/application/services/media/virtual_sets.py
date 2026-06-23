"""Virtual media sets — external references without copying bytes (Media/Content Plane M9).

A virtual media set reads from an external source system; its versions are pointers, not
copied blobs. Because the platform "is not aware of updates/deletions in the source"
(ADR-0001 invariant ``external_virtual_object_requires_version_or_mutable_marker``), an
external version MUST either pin an immutable ``etag``/version (re-validated on resolve,
mismatch fails closed) OR be explicitly marked a ``mutable_external_reference`` (its freshness
is surfaced, never silently swapped). A version that does neither is rejected — it must never
silently read different bytes. The real external connector is deferred (``ExternalMediaReader``).
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.external_media_reader import ExternalReadRequest
from foundry_lite.application.ports.media_repository import (
    MediaItemRecord,
    MediaItemVersionRecord,
    MediaTransactionRecord,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, NotFound, ValidationFailed


@dataclass(frozen=True)
class ExternalResolution:
    """The outcome of resolving an external version: the version + a freshness signal."""

    version: MediaItemVersionRecord
    etag: str
    is_stale: bool


class VirtualMediaSetService(CoreService):
    """Register and resolve external versions of virtual media sets (no byte copy)."""

    required_dependencies = ("engine", "media_repository", "external_media_reader")
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def register_external_version(
        self,
        ctx: RequestContext,
        *,
        media_set_id: str,
        logical_path: str,
        source_ref: dict[str, object],
        supplied_mime_type: str = "application/octet-stream",
        schema_type: str = "document",
        format: str = "external",
    ) -> MediaItemVersionRecord:
        _require_version_or_marker(source_ref)
        media_set = self._virtual_media_set(ctx, media_set_id)
        version = self._create_external_version(
            ctx, media_set_id, media_set, logical_path, source_ref, supplied_mime_type, schema_type, format
        )
        return version

    def resolve_external_version(self, ctx: RequestContext, *, media_item_version_id: str) -> ExternalResolution:
        with self.engine.begin() as conn:
            version = self.media_repository.media_item_version_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=media_item_version_id
            )
        if version is None or version.status != "COMMITTED" or version.source_ref is None:
            raise NotFound("external media version not found", details={"media_item_version_id": media_item_version_id})
        source_ref = version.source_ref
        pinned_etag = _pinned_etag(source_ref)
        stat = self.external_media_reader.stat(
            ExternalReadRequest(uri=str(source_ref["uri"]), expected_etag=pinned_etag)
        )
        if pinned_etag is not None and stat.etag != pinned_etag:
            raise InvariantViolation(
                "virtual_external_version_etag_drifted",
                details={"media_item_version_id": media_item_version_id, "pinned": pinned_etag, "current": stat.etag},
            )
        return ExternalResolution(version=version, etag=stat.etag, is_stale=pinned_etag is None and not stat.is_present)

    def _virtual_media_set(self, ctx: RequestContext, media_set_id: str) -> object:
        with self.engine.begin() as conn:
            found = self.media_repository.get_media_sets(transaction=conn, ids=[media_set_id])
        if not found:
            raise NotFound("media set not found", details={"media_set_id": media_set_id})
        media_set = found[0]
        if not media_set.is_virtual:
            raise ValidationFailed("media set is not virtual", details={"media_set_id": media_set_id})
        return media_set

    def _create_external_version(
        self,
        ctx: RequestContext,
        media_set_id: str,
        media_set: object,
        logical_path: str,
        source_ref: dict[str, object],
        supplied_mime_type: str,
        schema_type: str,
        format: str,
    ) -> MediaItemVersionRecord:
        now = _now()
        envelope = {"tenantId": ctx.tenant_id, "classification": getattr(media_set, "classification", "internal")}
        with self.engine.begin() as conn:
            txn = self._open_external_transaction(conn, ctx, media_set_id, logical_path, now)
            item = self._external_item(conn, ctx, media_set_id, logical_path, now)
            version = self._insert_external_version(
                conn,
                ctx,
                item,
                txn.media_transaction_id,
                source_ref,
                supplied_mime_type,
                schema_type,
                format,
                envelope,
                now,
            )
            self._finalize_external_version(conn, ctx, media_set_id, item, version, txn.media_transaction_id, now)
        return version

    def _external_item(
        self, conn: object, ctx: RequestContext, media_set_id: str, logical_path: str, now: str
    ) -> MediaItemRecord:
        return self.media_repository.upsert_media_item(
            transaction=conn,
            record=MediaItemRecord(
                media_item_id=_new_id("mitem"),
                tenant_id=ctx.tenant_id,
                media_set_id=media_set_id,
                logical_path=logical_path,
                head_version_id=None,
                is_deleted=False,
                created_at=now,
                updated_at=now,
            ),
        )

    def _finalize_external_version(
        self,
        conn: object,
        ctx: RequestContext,
        media_set_id: str,
        item: MediaItemRecord,
        version: MediaItemVersionRecord,
        media_transaction_id: str,
        now: str,
    ) -> None:
        self.media_repository.cas_item_head_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            media_item_id=item.media_item_id,
            expected_head_version_id=item.head_version_id,
            new_head_version_id=version.media_item_version_id,
            updated_at=now,
        )
        self.media_repository.commit_transaction(
            transaction=conn, tenant_id=ctx.tenant_id, media_transaction_id=media_transaction_id, committed_at=now
        )
        self._emit_registered(conn, ctx, media_set_id, version)

    def _open_external_transaction(
        self, conn: object, ctx: RequestContext, media_set_id: str, logical_path: str, now: str
    ) -> MediaTransactionRecord:
        record = MediaTransactionRecord(
            media_transaction_id=_new_id("mtx"),
            tenant_id=ctx.tenant_id,
            media_set_id=media_set_id,
            status="OPEN",
            mode="APPEND",
            idempotency_key=_new_id("virtual"),
            request_fingerprint=logical_path,
            opened_at=now,
        )
        existing = self.media_repository.create_open_transaction(transaction=conn, record=record)
        return existing if existing is not None else record

    def _insert_external_version(
        self,
        conn: object,
        ctx: RequestContext,
        item: MediaItemRecord,
        media_transaction_id: str,
        source_ref: dict[str, object],
        supplied_mime_type: str,
        schema_type: str,
        format: str,
        envelope: dict[str, object],
        now: str,
    ) -> MediaItemVersionRecord:
        etag = _pinned_etag(source_ref) or ""
        raw_size = source_ref.get("byte_size", 0)
        byte_size = raw_size if isinstance(raw_size, int) else 0
        record = MediaItemVersionRecord(
            media_item_version_id=_new_id("miv"),
            tenant_id=ctx.tenant_id,
            media_item_id=item.media_item_id,
            media_transaction_id=media_transaction_id,
            version_number=self.media_repository.next_version_number(
                transaction=conn, media_item_id=item.media_item_id
            ),
            blob_key=str(source_ref["uri"]),
            content_hash=etag,
            byte_size=byte_size,
            supplied_mime_type=supplied_mime_type,
            sniffed_mime_type=supplied_mime_type,
            schema_type=schema_type,
            format=format,
            probe_metadata={},
            security_envelope=envelope,
            source_ref=source_ref,
            status="COMMITTED",
            created_at=now,
            committed_at=now,
        )
        inserted = self.media_repository.insert_version(transaction=conn, record=record)
        return inserted if inserted is not None else record

    def _emit_registered(
        self, conn: object, ctx: RequestContext, media_set_id: str, version: MediaItemVersionRecord
    ) -> None:
        payload = {"mediaSetId": media_set_id, "mediaItemVersionId": version.media_item_version_id, "isVirtual": True}
        self.runtime_service._outbox(
            conn,
            ctx,
            "media.virtual.registered",
            "media_item_version",
            version.media_item_version_id,
            payload,
            idempotency_key=version.media_item_version_id,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="media.virtual.registered",
            resource_type="media_item_version",
            resource_id=version.media_item_version_id,
            action="register",
            after_ref=payload,
            correlation_id=ctx.request_id,
        )


def _require_version_or_marker(source_ref: dict[str, object]) -> None:
    has_version = bool(source_ref.get("etag")) or bool(source_ref.get("version"))
    is_mutable_external_reference = bool(source_ref.get("is_mutable_external_reference"))
    if not has_version and not is_mutable_external_reference:
        raise InvariantViolation(
            "virtual_external_version_unversioned",
            details={
                "uri": source_ref.get("uri"),
                "reason": "must pin etag/version or set is_mutable_external_reference",
            },
        )


def _pinned_etag(source_ref: dict[str, object]) -> str | None:
    etag = source_ref.get("etag") or source_ref.get("version")
    if etag is None or bool(source_ref.get("is_mutable_external_reference")):
        return None
    return str(etag)
