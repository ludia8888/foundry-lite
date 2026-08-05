"""Object-inherited authorization for Action attachment references."""

from __future__ import annotations

from dataclasses import replace

from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.application.services.media.attachment_access import has_visible_attachment_holder
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class _ObjectQuery:
    def __init__(self, visible_ids: set[str]) -> None:
        self.visible_ids = visible_ids

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> object:
        if object_id not in self.visible_ids:
            raise NotFound("holder object is not visible")
        return {"objectType": object_type_api_name, "objectId": object_id}


def _binding(*, reference_kind: str = "attachment", holder_id: str = "order-1") -> MediaReferenceBindingRecord:
    return MediaReferenceBindingRecord(
        media_reference_binding_id="mrb-1",
        tenant_id="tenant-demo",
        holder_type="Order",
        holder_id=holder_id,
        property_name="receipt",
        media_set_id="receipts",
        media_item_id="mi-1",
        media_item_version_id="miv-1",
        logical_path="orders/order-1/receipt.pdf",
        content_hash="sha256:abc",
        security_envelope={"referenceKind": reference_kind},
        idempotency_key="bind-1",
        created_at="2026-08-04T00:00:00Z",
        updated_at="2026-08-04T00:00:00Z",
    )


def test_preexisting_media_becomes_object_inherited_when_bound_as_attachment() -> None:
    binding = _binding()

    assert not has_visible_attachment_holder(RequestContext(), {}, [binding], _ObjectQuery(set()))
    assert has_visible_attachment_holder(RequestContext(), {}, [binding], _ObjectQuery({"order-1"}))


def test_unbound_action_attachment_upload_is_not_directly_readable() -> None:
    assert not has_visible_attachment_holder(
        RequestContext(),
        {"actionParameterKind": "attachment"},
        [],
        _ObjectQuery(set()),
    )


def test_normal_media_remains_directly_readable() -> None:
    normal_binding = replace(_binding(), security_envelope={"referenceKind": "media"})

    assert has_visible_attachment_holder(RequestContext(), {}, [normal_binding], _ObjectQuery(set()))
