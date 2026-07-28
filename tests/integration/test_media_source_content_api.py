from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime


def test_committed_media_source_endpoint_streams_exact_version_with_range(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'media-source.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    body = b"%PDF-1.7\nimmutable source bytes\n%%EOF"
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="documents",
        name="source_viewer",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="media-source-viewer",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/reports/source.pdf",
        source=io.BytesIO(body),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    path = f"/api/media/versions/{staged.media_item_version_id}/content"
    metadata = client.head(path, headers=headers)
    response = client.get(path, headers={**headers, "Range": "bytes=5-12"})
    full = client.get(path, headers=headers)
    cross_tenant_metadata = client.head(path, headers={**headers, "X-Tenant-ID": "tenant-other"})
    cross_tenant = client.get(path, headers={**headers, "X-Tenant-ID": "tenant-other"})
    viewer_headers = {**headers, "X-User-ID": "viewer-a", "X-Roles": "viewer"}
    uncleared_metadata = client.head(path, headers=viewer_headers)
    uncleared = client.get(path, headers=viewer_headers)

    assert metadata.status_code == 200
    assert metadata.content == b""
    assert metadata.headers["content-type"] == "application/pdf"
    assert metadata.headers["content-length"] == str(len(body))
    assert response.status_code == 206
    assert response.content == body[5:13]
    assert response.headers["content-range"] == f"bytes 5-12/{len(body)}"
    assert response.headers["content-length"] == "8"
    assert full.status_code == 200
    assert full.content == body
    assert cross_tenant_metadata.status_code == 404
    assert cross_tenant.status_code == 404
    assert uncleared_metadata.status_code == 403
    assert uncleared.status_code == 403
