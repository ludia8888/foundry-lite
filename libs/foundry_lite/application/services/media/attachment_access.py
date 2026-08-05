"""Object-inherited read authorization for Action attachment media."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied


class AttachmentObjectQuery(Protocol):
    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> object: ...


def has_visible_attachment_holder(
    ctx: RequestContext,
    security_envelope: Mapping[str, object],
    bindings: Sequence[MediaReferenceBindingRecord],
    object_query: AttachmentObjectQuery,
) -> bool:
    """Normal media is directly readable; attachments require one visible holder object."""
    is_attachment_upload = security_envelope.get("actionParameterKind") == "attachment"
    is_attachment_binding = any(binding.security_envelope.get("referenceKind") == "attachment" for binding in bindings)
    if not is_attachment_upload and not is_attachment_binding:
        return True
    for binding in bindings:
        if binding.security_envelope.get("referenceKind") != "attachment":
            continue
        try:
            object_query.get_object(binding.holder_type, binding.holder_id, ctx=ctx)
            return True
        except (NotFound, PermissionDenied):
            continue
    return False
