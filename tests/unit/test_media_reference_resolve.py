"""Reference-resolution invariants for the local Media/Content Plane (doc §6.2 / §6.3).

A reference pins the exact immutable version, so a later same-path overwrite never changes
what an existing reference resolves to. The DB COMMITTED row is the serving truth: if the
blob is missing or disagrees with the committed stat/hash, resolution is a hard failure.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.media_storage import ByteRange
from foundry_lite.application.services.media.uploads import StagedUpload
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import InvariantViolation, NotFound, PermissionDenied, ValidationFailed


def _media_set(foundry: FoundryLite, ctx: RequestContext) -> str:
    record = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    return record.media_set_id


def _upload_and_commit(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    media_set_id: str,
    logical_path: str,
    body: bytes,
    idempotency_key: str,
) -> StagedUpload:
    tx = foundry.media.open_transaction(ctx, media_set_id=media_set_id, idempotency_key=idempotency_key)
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set_id,
        media_transaction_id=tx,
        logical_path=logical_path,
        source=io.BytesIO(body),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
    )
    foundry.media.commit(ctx, media_transaction_id=tx)
    return staged


def _blob_path(foundry: FoundryLite, blob_key: str) -> Path:
    return foundry.root / "media-storage" / blob_key


def test_reference_pins_immutable_version_after_same_path_overwrite(foundry: FoundryLite) -> None:
    # §6.3: overwriting /a.pdf creates v2; an existing reference to v1 still resolves to v1's bytes.
    ctx = demo_admin_context()
    media_set_id = _media_set(foundry, ctx)
    first = _upload_and_commit(
        foundry, ctx, media_set_id=media_set_id, logical_path="/a.pdf", body=b"%PDF-1.4 first", idempotency_key="t1"
    )
    second = _upload_and_commit(
        foundry, ctx, media_set_id=media_set_id, logical_path="/a.pdf", body=b"%PDF-1.4 second", idempotency_key="t2"
    )

    first_resolved = foundry.media.resolve_reference(ctx, media_item_version_id=first.media_item_version_id)
    second_resolved = foundry.media.resolve_reference(ctx, media_item_version_id=second.media_item_version_id)

    assert first_resolved.reference.media_item_version_id == first.media_item_version_id
    assert first_resolved.reference.logical_path == "/a.pdf"
    assert first_resolved.reference.content_hash != second_resolved.reference.content_hash


def test_source_read_issues_version_pinned_verified_range_grant(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()
    media_set_id = _media_set(foundry, ctx)
    body = b"%PDF-1.4 range-readable"
    staged = _upload_and_commit(
        foundry,
        ctx,
        media_set_id=media_set_id,
        logical_path="/a.pdf",
        body=body,
        idempotency_key="source-read",
    )

    source = foundry.media.source_read(
        ctx,
        media_item_version_id=staged.media_item_version_id,
        byte_range=ByteRange(5, 9),
    )

    assert source.resolved.version.content_hash == source.resolved.reference.content_hash
    assert source.byte_range == ByteRange(5, 9)
    assert source.grant.grant_kind == "inline"
    assert source.grant.stream is not None and source.grant.stream.read(5) == body[5:10]
    source.grant.stream.close()
    with pytest.raises(NotFound):
        foundry.media.source_read(
            RequestContext(tenant_id="tenant-other"),
            media_item_version_id=staged.media_item_version_id,
        )
    with pytest.raises(ValidationFailed, match="outside the committed source"):
        foundry.media.source_read(
            ctx,
            media_item_version_id=staged.media_item_version_id,
            byte_range=ByteRange(len(body), None),
        )
    with pytest.raises(PermissionDenied, match="clearance"):
        foundry.media.source_read(
            RequestContext(
                tenant_id=ctx.tenant_id,
                actor_user_id="viewer-without-confidential-clearance",
                roles=("viewer",),
            ),
            media_item_version_id=staged.media_item_version_id,
        )


def test_resolve_hard_fails_when_committed_blob_is_missing(foundry: FoundryLite) -> None:
    # §6.2: DB COMMITTED but blob absent -> committed_media_version_storage_missing.
    ctx = demo_admin_context()
    media_set_id = _media_set(foundry, ctx)
    staged = _upload_and_commit(
        foundry, ctx, media_set_id=media_set_id, logical_path="/a.pdf", body=b"%PDF-1.4 a", idempotency_key="t1"
    )
    _blob_path(foundry, staged.blob_key).unlink()

    with pytest.raises(InvariantViolation, match="committed_media_version_storage_missing"):
        foundry.media.resolve_reference(ctx, media_item_version_id=staged.media_item_version_id)


def test_resolve_hard_fails_when_committed_blob_is_corrupt(foundry: FoundryLite) -> None:
    # §6.2: blob present but stat/hash disagrees -> committed_media_version_storage_corrupt.
    ctx = demo_admin_context()
    media_set_id = _media_set(foundry, ctx)
    staged = _upload_and_commit(
        foundry, ctx, media_set_id=media_set_id, logical_path="/a.pdf", body=b"%PDF-1.4 a", idempotency_key="t1"
    )
    _blob_path(foundry, staged.blob_key).write_bytes(b"corrupted-and-longer")

    with pytest.raises(InvariantViolation, match="committed_media_version_storage_corrupt"):
        foundry.media.resolve_reference(ctx, media_item_version_id=staged.media_item_version_id)
