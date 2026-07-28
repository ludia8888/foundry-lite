"""Shared authorization rules for raw media and derived evidence reads."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.content_index import is_classification_cleared
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.media.clearance import allowed_media_classifications
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


def require_media_version_clearance(ctx: RequestContext, version: MediaItemVersionRecord) -> None:
    require_media_security_clearance(
        ctx,
        version.security_envelope,
        resource_id=version.media_item_version_id,
        resource_type="media_version",
    )


def require_media_derivative_clearance(ctx: RequestContext, derivative: MediaDerivativeRecord) -> None:
    require_media_security_clearance(
        ctx,
        derivative.security_envelope,
        resource_id=derivative.media_derivative_id,
        resource_type="media_derivative",
    )


def require_content_unit_clearance(ctx: RequestContext, unit: ContentUnitRecord) -> None:
    require_media_security_clearance(
        ctx,
        unit.security_envelope,
        resource_id=unit.content_unit_id,
        resource_type="content_unit",
    )


def require_media_security_clearance(
    ctx: RequestContext,
    security_envelope: Mapping[str, object],
    *,
    resource_id: str,
    resource_type: str,
) -> None:
    classification = str(security_envelope.get("classification", "public"))
    if is_classification_cleared(classification, allowed_media_classifications(ctx)):
        return
    raise PermissionDenied(
        "caller clearance does not cover the requested media resource",
        details={
            "resource_id": resource_id,
            "resource_type": resource_type,
            "classification": classification,
        },
    )
