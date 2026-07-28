"""Authoritative content-unit citation verification tests."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from foundry_lite.application.ports.citation_source import (
    CitationSourceVerificationFailed,
    CitationSourceVerificationRequest,
    citation_source_evidence_payload,
)
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.media.content_unit_evidence import ContentUnitEvidenceService
from foundry_lite.application.services.media.document_context_evidence import (
    document_context_from_unit,
    document_context_hash,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.content_unit_citation_source_verifier import (
    AuthoritativeCitationSourceVerifier,
)
from foundry_lite.infrastructure.adapters.fake_citation_source_verifier import FakeCitationSourceVerifier
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaRepository,
)
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="operator-1",
    roles=("admin",),
    request_id="req-citation-evidence",
)
_ENVELOPE = {
    "tenantId": "tenant-demo",
    "classification": "internal",
    "policyVersion": "policy-v1",
}
_BBOX = {
    "x": 60.0,
    "y": 160.0,
    "width": 180.0,
    "height": 80.0,
    "pageWidth": 600.0,
    "pageHeight": 800.0,
    "unit": "pt",
}


def test_authoritative_verifier_returns_committed_pdf_locator_and_pins_without_raw_text() -> None:
    version, derivative, unit = _records()
    verifier = _verifier(version, derivative, unit)

    result = verifier.verify_source(_CTX, _request(unit))
    evidence = citation_source_evidence_payload(result.evidence)

    assert result.source_resource_id == "content-unit://miv-pdf-1/cu-pdf-1"
    assert result.source_version == "miv-pdf-1"
    assert result.content_hash == document_context_hash(document_context_from_unit(unit))
    assert evidence == {
        "mediaItemVersionId": "miv-pdf-1",
        "mediaDerivativeId": "mder-layout-1",
        "contentUnitId": "cu-pdf-1",
        "pageNumber": 2,
        "bbox": _BBOX,
        "timecode": None,
        "sourceLocator": {
            "pageNumber": 2,
            "bbox": _BBOX,
            "coordinateSystem": "pdf_top_left_points",
        },
        "derivativeKind": "pdf_layout",
        "processorName": "pdf_layout_v1",
        "processorVersion": "1",
        "processorSpecHash": "sha256:layout-spec",
        "modelName": None,
        "modelVersion": None,
        "paramsHash": "sha256:layout-params",
        "securityEnvelope": {
            "tenantId": "tenant-demo",
            "classification": "internal",
            "policyVersion": "policy-v1",
            "hasLegalHold": False,
        },
    }
    assert unit.text not in json.dumps(result.__dict__, default=str)


def test_authoritative_verifier_rejects_uri_version_hash_and_cross_tenant_mismatch() -> None:
    version, derivative, unit = _records()
    verifier = _verifier(version, derivative, unit)

    with pytest.raises(CitationSourceVerificationFailed, match="URI version") as version_error:
        verifier.verify_source(_CTX, replace(_request(unit), source_version="miv-other"))
    with pytest.raises(CitationSourceVerificationFailed, match="hash") as hash_error:
        verifier.verify_source(_CTX, replace(_request(unit), content_hash="sha256:stale"))
    with pytest.raises(CitationSourceVerificationFailed) as tenant_error:
        verifier.verify_source(replace(_CTX, tenant_id="tenant-other"), _request(unit))

    assert version_error.value.reason == "source_version_mismatch"
    assert hash_error.value.reason == "source_hash_mismatch"
    assert tenant_error.value.reason == "source_verification_failed"


@pytest.mark.parametrize(
    ("record_name", "record_change"),
    [
        ("version", {"status": "STAGED"}),
        ("derivative", {"status": "STAGED"}),
        ("derivative", {"processor_spec_hash": ""}),
        ("derivative", {"source_media_item_version_id": "miv-other"}),
        ("unit", {"security_envelope": {"tenantId": "tenant-demo", "classification": "public"}}),
        ("unit", {"bbox": None}),
        ("unit", {"source_locator": None}),
    ],
)
def test_authoritative_verifier_fails_closed_for_stale_or_weakened_chain(
    record_name: str,
    record_change: dict[str, object],
) -> None:
    version, derivative, unit = _records()
    records = {"version": version, "derivative": derivative, "unit": unit}
    records[record_name] = replace(records[record_name], **record_change)
    verifier = _verifier(records["version"], records["derivative"], records["unit"])

    with pytest.raises(CitationSourceVerificationFailed) as excinfo:
        verifier.verify_source(_CTX, _request(records["unit"]))

    assert excinfo.value.reason == "source_verification_failed"


def _verifier(
    version: MediaItemVersionRecord,
    derivative: MediaDerivativeRecord,
    unit: ContentUnitRecord,
) -> AuthoritativeCitationSourceVerifier:
    engine = _engine()
    media_repository = SqlAlchemyMediaRepository(engine)
    derivative_repository = SqlAlchemyMediaDerivativeRepository(engine)
    with engine.begin() as transaction:
        media_repository.insert_version(transaction=transaction, record=version)
        derivative_repository.create_derivative_or_get_existing(transaction=transaction, record=derivative)
        derivative_repository.insert_content_units(transaction=transaction, records=[unit])
    service = ContentUnitEvidenceService(
        engine=engine,
        policy=PolicyService(),
        media_repository=media_repository,
        media_derivative_repository=derivative_repository,
    )
    return AuthoritativeCitationSourceVerifier(service, FakeCitationSourceVerifier())


def _request(unit: ContentUnitRecord) -> CitationSourceVerificationRequest:
    return CitationSourceVerificationRequest(
        source_resource_type="content_unit",
        source_resource_id=f"content-unit://{unit.source_media_item_version_id}/{unit.content_unit_id}",
        source_version=unit.source_media_item_version_id,
        content_hash=document_context_hash(document_context_from_unit(unit)),
    )


def _records() -> tuple[MediaItemVersionRecord, MediaDerivativeRecord, ContentUnitRecord]:
    return _version(), _derivative(), _unit()


def _version() -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id="miv-pdf-1",
        tenant_id="tenant-demo",
        media_item_id="mi-pdf-1",
        media_transaction_id="mtx-pdf-1",
        version_number=1,
        blob_key="media/pdf/report.pdf",
        content_hash="sha256:pdf-bytes",
        byte_size=1024,
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={},
        security_envelope=dict(_ENVELOPE),
        source_ref=None,
        status="COMMITTED",
        created_at="2026-07-16T00:00:00Z",
        committed_at="2026-07-16T00:00:01Z",
    )


def _derivative() -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id="mder-layout-1",
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-pdf-1",
        derivative_kind="pdf_layout",
        processor_spec_hash="sha256:layout-spec",
        processor_name="pdf_layout_v1",
        processor_version="1",
        model_name=None,
        model_version="",
        params_hash="sha256:layout-params",
        security_envelope=dict(_ENVELOPE),
        status="COMMITTED",
        content_hash="sha256:layout",
        created_at="2026-07-16T00:00:02Z",
        committed_at="2026-07-16T00:00:03Z",
    )


def _unit() -> ContentUnitRecord:
    return ContentUnitRecord(
        content_unit_id="cu-pdf-1",
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-pdf-1",
        derivative_id="mder-layout-1",
        unit_kind="layout_block",
        ordinal=1,
        text="Confidential source body that must never enter navigation payloads.",
        text_hash="sha256:unit-text",
        chunk_spec_hash="sha256:chunk-v1",
        security_envelope=dict(_ENVELOPE),
        page_number=2,
        bbox=dict(_BBOX),
        source_locator={
            "pageNumber": 2,
            "bbox": dict(_BBOX),
            "coordinateSystem": "pdf_top_left_points",
        },
        structure={"role": "heading_1"},
        confidence=0.97,
        created_at="2026-07-16T00:00:04Z",
    )


def _engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine
