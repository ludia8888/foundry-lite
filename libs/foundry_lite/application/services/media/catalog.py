from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.media_repository import MediaSetRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


@dataclass(frozen=True)
class MediaSetSpec:
    """Inputs that define one media set (a first-class asset, parallel to a dataset)."""

    namespace: str
    name: str
    schema_type: str
    primary_format: str
    allowed_input_formats: tuple[str, ...]
    classification: str
    transaction_policy: str = "transactional"
    storage_profile: str = "local"
    processing_profile: str = "local"
    retention_policy_id: str | None = None


class MediaCatalogService(CoreService):
    """Media set catalog lifecycle (parallel to the dataset registry).

    v1 is transactional-only: a media set whose policy is not ``transactional`` is
    rejected up front so no transactionless write path is implied (doc §1.3 / §6).
    """

    required_dependencies = ("engine", "media_repository")
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def create_media_set(self, ctx: RequestContext, spec: MediaSetSpec) -> MediaSetRecord:
        if spec.transaction_policy != "transactional":
            raise ValidationFailed(
                "v1 supports only transactional media sets",
                details={"transaction_policy": spec.transaction_policy},
            )
        record = _media_set_record(ctx, spec)
        with self.engine.begin() as conn:
            existing = self.media_repository.create_media_set_or_get_existing(transaction=conn, record=record)
            result = existing if existing is not None else record
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.set.created",
                resource_type="media_set",
                resource_id=result.media_set_id,
                action="create",
                after_ref={"namespace": spec.namespace, "name": spec.name},
                correlation_id=ctx.request_id,
            )
        return result

    def get_media_set(self, ctx: RequestContext, *, media_set_id: str) -> MediaSetRecord:
        with self.engine.begin() as conn:
            found = self.media_repository.get_media_sets(transaction=conn, ids=[media_set_id])
        if not found:
            raise NotFound("media set not found", details={"media_set_id": media_set_id, "tenant_id": ctx.tenant_id})
        return found[0]


def _media_set_record(ctx: RequestContext, spec: MediaSetSpec) -> MediaSetRecord:
    now = _now()
    return MediaSetRecord(
        media_set_id=_new_id("mset"),
        tenant_id=ctx.tenant_id,
        namespace=spec.namespace,
        name=spec.name,
        schema_type=spec.schema_type,
        primary_format=spec.primary_format,
        allowed_input_formats=spec.allowed_input_formats,
        transaction_policy=spec.transaction_policy,
        storage_profile=spec.storage_profile,
        processing_profile=spec.processing_profile,
        classification=spec.classification,
        retention_policy_id=spec.retention_policy_id,
        created_at=now,
        updated_at=now,
    )
