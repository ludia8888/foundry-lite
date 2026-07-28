"""Direct rule tests for committed Content Unit chunk source validation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.media.content_chunking_records import validate_source_chain
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

_SECURITY = {"tenantId": "tenant-demo", "classification": "confidential"}


def test_validate_source_chain_accepts_exact_committed_ancestry() -> None:
    validate_source_chain(
        RequestContext(tenant_id="tenant-demo"),
        _derivative(),
        _version(),
        (_unit(),),
    )


def test_validate_source_chain_rejects_weakened_content_unit_security() -> None:
    weakened = replace(
        _unit(),
        security_envelope={"tenantId": "tenant-demo", "classification": "public"},
    )

    with pytest.raises(ConflictDetected, match="security envelope"):
        validate_source_chain(
            RequestContext(tenant_id="tenant-demo"),
            _derivative(),
            _version(),
            (weakened,),
        )


def _version() -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id="miv-source",
        tenant_id="tenant-demo",
        media_item_id="mi-source",
        media_transaction_id="mtx-source",
        version_number=1,
        blob_key="media/source.pdf",
        content_hash="source-hash",
        byte_size=100,
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={},
        security_envelope=dict(_SECURITY),
        source_ref=None,
        status="COMMITTED",
        created_at="2026-07-17T00:00:00+09:00",
        committed_at="2026-07-17T00:00:01+09:00",
    )


def _derivative() -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id="mder-source",
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-source",
        derivative_kind="pdf_layout",
        processor_spec_hash="layout-spec",
        processor_name="pdf_layout_v1",
        processor_version="1.0.0",
        model_name="layout-model",
        model_version="1.0.0",
        params_hash="layout-params",
        security_envelope=dict(_SECURITY),
        status="COMMITTED",
        content_hash="layout-hash",
        created_at="2026-07-17T00:00:02+09:00",
        committed_at="2026-07-17T00:00:03+09:00",
    )


def _unit() -> ContentUnitRecord:
    return ContentUnitRecord(
        content_unit_id="cu-source",
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-source",
        derivative_id="mder-source",
        unit_kind="layout_block",
        ordinal=0,
        text="heading body",
        text_hash="text-hash",
        chunk_spec_hash="layout-spec",
        security_envelope=dict(_SECURITY),
        page_number=1,
        bbox={"x": 10, "y": 10, "width": 100, "height": 20},
        source_locator={"pageNumber": 1},
        structure={"kind": "heading", "level": 1},
        created_at="2026-07-17T00:00:04+09:00",
    )
