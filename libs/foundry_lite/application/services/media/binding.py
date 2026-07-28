"""Application service helpers for binding workflows."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.content_index import is_classification_cleared
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.application.ports.media_repository import MediaItemRecord, MediaItemVersionRecord, MediaReference
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.clearance import allowed_media_classifications
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied


class MediaReferenceBindingService(CoreService):
    """Bind an immutable media version onto an Ontology object property (doc §1.6/§6.4).

    The bind is one atomic unit of work: only a COMMITTED media version (serving truth) can
    be bound, and the binding row + audit + outbox commit together — a rejected bind writes
    nothing (the staged blob stays a sweepable orphan, never visible). The binding pins
    media_item_version_id, so a later same-path overwrite never changes an existing reference;
    reads mask the reference when the caller's clearance does not cover its classification.
    """

    required_dependencies = ("engine", "policy", "media_repository", "media_reference_binding_repository")
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def bind(
        self,
        ctx: RequestContext,
        *,
        holder_type: str,
        holder_id: str,
        property_name: str,
        media_item_version_id: str,
        idempotency_key: str,
    ) -> MediaReferenceBindingRecord:
        # Authorize the WRITE before touching the graph: the caller must hold the bind permission
        # (holder_type/holder_id are opaque, so this is the write-side gate that stops an
        # unprivileged caller from creating or OVERWRITING a binding — reference poisoning).
        self.policy.require(ctx, "media:reference:bind")
        with self.engine.begin() as conn:
            version = self._require_committed_version(conn, ctx, media_item_version_id)
            self._require_media_readable(ctx, version)
            item = self._require_item(conn, ctx, version.media_item_id)
            existing = self.media_reference_binding_repository.binding_by_holder(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                holder_type=holder_type,
                holder_id=holder_id,
                property_name=property_name,
            )
            replay = _idempotent_replay(existing, idempotency_key, media_item_version_id)
            if replay is not None:
                return replay
            record = _binding_record(
                ctx, holder_type, holder_id, property_name, version, item, idempotency_key, existing
            )
            if existing is None:
                self.media_reference_binding_repository.create_binding(transaction=conn, record=record)
            else:
                self.media_reference_binding_repository.update_binding(transaction=conn, record=record)
            self._emit(conn, ctx, record)
            return record

    def resolve(
        self,
        ctx: RequestContext,
        *,
        holder_type: str,
        holder_id: str,
        property_name: str,
        allowed_classifications: tuple[str, ...] | None = None,
    ) -> MediaReference | None:
        with self.engine.begin() as conn:
            binding = self.media_reference_binding_repository.binding_by_holder(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                holder_type=holder_type,
                holder_id=holder_id,
                property_name=property_name,
            )
        if binding is None:
            raise NotFound(
                "media reference binding not found",
                details={"holder_type": holder_type, "holder_id": holder_id, "property": property_name},
            )
        classification = str(binding.security_envelope.get("classification", "public"))
        if not is_classification_cleared(classification, allowed_classifications):
            return None  # masked: the caller may not see this media reference (no ACL leakage)
        return MediaReference(
            media_set_id=binding.media_set_id,
            media_item_id=binding.media_item_id,
            media_item_version_id=binding.media_item_version_id,
            logical_path=binding.logical_path,
            content_hash=binding.content_hash,
        )

    def _require_committed_version(
        self, conn: TransactionContext, ctx: RequestContext, media_item_version_id: str
    ) -> MediaItemVersionRecord:
        version = self.media_repository.media_item_version_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=media_item_version_id
        )
        if version is None:
            raise NotFound("media version not found", details={"media_item_version_id": media_item_version_id})
        if version.status != "COMMITTED":
            raise ConflictDetected(
                "only a committed media version can be bound to an object property",
                details={"media_item_version_id": media_item_version_id, "status": version.status},
            )
        return version

    def _require_media_readable(self, ctx: RequestContext, version: MediaItemVersionRecord) -> None:
        # You cannot bind media you may not read: the version's classification must be covered by
        # the caller's server-derived clearance, so a writer cannot pin a reference to media above
        # their clearance (which a reader would then resolve).
        classification = str(version.security_envelope.get("classification", "public"))
        if not is_classification_cleared(classification, allowed_media_classifications(ctx)):
            raise PermissionDenied(
                "caller clearance does not cover the media version being bound",
                details={"media_item_version_id": version.media_item_version_id, "classification": classification},
            )

    def _require_item(self, conn: TransactionContext, ctx: RequestContext, media_item_id: str) -> MediaItemRecord:
        item = self.media_repository.media_item_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, media_item_id=media_item_id
        )
        if item is None:
            raise NotFound("media item not found", details={"media_item_id": media_item_id})
        return item

    def _emit(self, conn: TransactionContext, ctx: RequestContext, record: MediaReferenceBindingRecord) -> None:
        payload = {
            "holderType": record.holder_type,
            "holderId": record.holder_id,
            "property": record.property_name,
            "mediaItemVersionId": record.media_item_version_id,
        }
        self.runtime_service._outbox(
            conn,
            ctx,
            "media.reference.bound",
            "media_reference_binding",
            record.media_reference_binding_id,
            payload,
            idempotency_key=record.media_reference_binding_id,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="media.reference.bound",
            resource_type="media_reference_binding",
            resource_id=record.media_reference_binding_id,
            action="bind",
            after_ref=payload,
            correlation_id=ctx.request_id,
        )


def _idempotent_replay(
    existing: MediaReferenceBindingRecord | None, idempotency_key: str, media_item_version_id: str
) -> MediaReferenceBindingRecord | None:
    if existing is None or existing.idempotency_key != idempotency_key:
        return None
    if existing.media_item_version_id == media_item_version_id:
        return existing
    raise ConflictDetected(
        "idempotency key already bound to a different media version",
        details={"idempotency_key": idempotency_key, "existing": existing.media_item_version_id},
    )


def _binding_record(
    ctx: RequestContext,
    holder_type: str,
    holder_id: str,
    property_name: str,
    version: MediaItemVersionRecord,
    item: MediaItemRecord,
    idempotency_key: str,
    existing: MediaReferenceBindingRecord | None,
) -> MediaReferenceBindingRecord:
    now = _now()
    return MediaReferenceBindingRecord(
        media_reference_binding_id=existing.media_reference_binding_id if existing else _new_id("mrb"),
        tenant_id=ctx.tenant_id,
        holder_type=holder_type,
        holder_id=holder_id,
        property_name=property_name,
        media_set_id=item.media_set_id,
        media_item_id=version.media_item_id,
        media_item_version_id=version.media_item_version_id,
        logical_path=item.logical_path,
        content_hash=version.content_hash,
        security_envelope=dict(version.security_envelope),
        idempotency_key=idempotency_key,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
