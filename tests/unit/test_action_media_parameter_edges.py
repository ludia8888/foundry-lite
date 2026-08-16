from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.ports.media_repository import (
    MediaItemRecord,
    MediaItemVersionRecord,
    MediaSetRecord,
)
from foundry_lite.application.services.action_media_parameters import (
    action_media_parameter,
    action_media_values,
    resolve_action_media_parameters,
)
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService

_ADMIN = RequestContext(tenant_id="tenant-a", actor_user_id="admin-1", roles=("admin",))


def _contract():
    return compile_action_contract(
        {
            "apiName": "ReviewReceipt",
            "contractVersion": 3,
            "target": "Expense",
            "parameters": [
                {
                    "apiName": "receipt",
                    "type": "media",
                    "mediaSet": "legal.receipts",
                    "allowedMimeTypes": ["application/pdf", "image/*"],
                    "maxBytes": 1024,
                },
                {
                    "apiName": "attachments",
                    "type": "array",
                    "itemType": "attachment",
                    "mediaSet": "legal.receipts",
                },
                {
                    "apiName": "packet",
                    "type": "struct",
                    "fields": [
                        {"apiName": "photo", "type": "media", "mediaSet": "legal.receipts"},
                        {"apiName": "note", "type": "string"},
                    ],
                },
                {"apiName": "comment", "type": "string"},
            ],
            "rules": [],
        }
    )


def _media_set(*, media_set_id: str = "set-receipts") -> MediaSetRecord:
    return MediaSetRecord(
        media_set_id=media_set_id,
        tenant_id="tenant-a",
        namespace="legal",
        name="receipts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf", "png"),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="local",
        classification="confidential",
        retention_policy_id=None,
        created_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:00:00Z",
    )


def _item(number: int, *, media_set_id: str = "set-receipts") -> MediaItemRecord:
    return MediaItemRecord(
        media_item_id=f"item-{number}",
        tenant_id="tenant-a",
        media_set_id=media_set_id,
        logical_path=f"receipt-{number}.pdf",
        head_version_id=f"version-{number}",
        is_deleted=False,
        created_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:00:00Z",
    )


def _version(number: int, **changes: object) -> MediaItemVersionRecord:
    base = MediaItemVersionRecord(
        media_item_version_id=f"version-{number}",
        tenant_id="tenant-a",
        media_item_id=f"item-{number}",
        media_transaction_id="tx-1",
        version_number=number,
        blob_key=f"media/receipt-{number}.pdf",
        content_hash=f"sha256:receipt-{number}",
        byte_size=512,
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={},
        security_envelope={"tenantId": "tenant-a", "classification": "confidential"},
        source_ref=None,
        status="COMMITTED",
        created_at="2026-08-13T00:00:00Z",
        committed_at="2026-08-13T00:00:01Z",
    )
    return replace(base, **changes)


class _Repository:
    def __init__(self) -> None:
        self.versions = {f"version-{number}": _version(number) for number in range(1, 4)}
        self.items = {f"item-{number}": _item(number) for number in range(1, 4)}
        self.media_set = _media_set()
        self.requested_ids: list[str] = []

    def get_media_item_versions(self, *, ids: list[str], **_kwargs: object) -> list[MediaItemVersionRecord]:
        self.requested_ids = ids
        return [self.versions[item] for item in ids if item in self.versions]

    def media_item_by_id(self, *, media_item_id: str, **_kwargs: object) -> MediaItemRecord | None:
        return self.items.get(media_item_id)

    def media_set_by_ref(self, *, namespace: str, name: str, **_kwargs: object) -> MediaSetRecord | None:
        if (namespace, name) != ("legal", "receipts"):
            return None
        return self.media_set


def _values() -> dict[str, object]:
    return {
        "receipt": {"referenceKind": "media", "mediaItemVersionId": "version-1"},
        "attachments": ["version-2"],
        "packet": {"photo": "version-3", "note": "manager supplied"},
        "comment": "review required",
    }


def _resolve(repository: _Repository, values: dict[str, object] | None = None, ctx: RequestContext = _ADMIN):
    return resolve_action_media_parameters(
        object(),
        ctx,
        PolicyService(),
        repository,  # type: ignore[arg-type]
        _contract(),
        values or _values(),
    )


def test_action_media_resolution_batches_ids_and_rebuilds_nested_server_owned_references() -> None:
    repository = _Repository()
    resolved = _resolve(repository)

    assert repository.requested_ids == ["version-1", "version-2", "version-3"]
    assert resolved["receipt"]["mediaItemId"] == "item-1"  # type: ignore[index]
    assert resolved["attachments"][0]["referenceKind"] == "attachment"  # type: ignore[index]
    assert resolved["packet"]["photo"]["classification"] == "confidential"  # type: ignore[index]
    assert resolved["packet"]["note"] == "manager supplied"  # type: ignore[index]
    assert resolved["comment"] == "review required"
    assert "secret" not in str(resolved)


def test_action_media_collection_discovers_only_declared_media_leaves() -> None:
    references = action_media_values(_contract(), _values())

    assert [(item.parameter_name, item.path, item.reference_kind) for item in references] == [
        ("receipt", (), "media"),
        ("attachments", (0,), "attachment"),
        ("packet", ("photo",), "media"),
    ]
    assert action_media_values(_contract(), {"comment": "plain text"}) == ()
    assert _resolve(_Repository(), {"comment": "plain text"}) == {"comment": "plain text"}


def test_action_media_parameter_path_requires_declared_media_leaf() -> None:
    parameter, kind = action_media_parameter(_contract(), "packet.photo")
    assert parameter.api_name == "photo" and kind == "media"
    with pytest.raises(NotFound, match="parameter not found"):
        action_media_parameter(_contract(), "missing")
    with pytest.raises(NotFound, match="not a struct"):
        action_media_parameter(_contract(), "receipt.photo")
    with pytest.raises(NotFound, match="field not found"):
        action_media_parameter(_contract(), "packet.missing")
    with pytest.raises(ValidationFailed, match="does not accept media"):
        action_media_parameter(_contract(), "comment")


@pytest.mark.parametrize(
    "value",
    [
        "",
        3,
        {"referenceKind": "attachment", "mediaItemVersionId": "version-1"},
        {"referenceKind": "media", "mediaItemVersionId": ""},
    ],
)
def test_action_media_reference_requires_exact_kind_and_immutable_version(value: object) -> None:
    with pytest.raises(ValidationFailed):
        action_media_values(_contract(), {"receipt": value})


def test_action_media_resolution_rejects_missing_uncommitted_item_set_and_wrong_membership() -> None:
    repository = _Repository()
    del repository.versions["version-1"]
    with pytest.raises(NotFound, match="version not found"):
        _resolve(repository)

    repository = _Repository()
    repository.versions["version-1"] = _version(1, status="STAGED")
    with pytest.raises(ConflictDetected, match="committed immutable"):
        _resolve(repository)

    repository = _Repository()
    del repository.items["item-1"]
    with pytest.raises(NotFound, match="media item not found"):
        _resolve(repository)

    repository = _Repository()
    repository.media_set = None  # type: ignore[assignment]
    with pytest.raises(NotFound, match="Media Set not found"):
        _resolve(repository)

    repository = _Repository()
    repository.items["item-1"] = _item(1, media_set_id="set-other")
    with pytest.raises(ValidationFailed, match="different Media Set"):
        _resolve(repository)


def test_action_media_resolution_enforces_clearance_mime_and_size() -> None:
    repository = _Repository()
    with pytest.raises(PermissionDenied, match="clearance"):
        _resolve(repository, ctx=RequestContext(tenant_id="tenant-a", roles=("viewer",)))

    repository = _Repository()
    repository.versions["version-1"] = _version(1, sniffed_mime_type="text/plain")
    with pytest.raises(ValidationFailed, match="MIME type"):
        _resolve(repository)

    repository = _Repository()
    repository.versions["version-1"] = _version(1, byte_size=1025)
    with pytest.raises(ValidationFailed, match="size limit"):
        _resolve(repository)

    repository = _Repository()
    repository.versions["version-1"] = _version(
        1,
        supplied_mime_type="image/png",
        sniffed_mime_type="image/png",
        format="png",
    )
    assert _resolve(repository)["receipt"]["mimeType"] == "image/png"  # type: ignore[index]
