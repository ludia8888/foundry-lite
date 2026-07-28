"""Real DB citation navigation resolve API integration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.ai_run_repository import (
    AiContextItemRecord,
    AiExecutionRunRecord,
    AiSessionRecord,
)
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.aip import citation_service as citation_service_module
from foundry_lite.application.services.aip.citation_service import CitationClaim, CitationResolveRequest
from foundry_lite.application.services.media.document_context_evidence import (
    document_context_from_unit,
    document_context_hash,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime
from sqlalchemy import update

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="operator-1",
    roles=("admin",),
    request_id="req-citation-api",
)
_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "operator-1",
    "X-Roles": "admin",
}
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


def test_navigation_resolve_api_rereads_committed_content_unit_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER", "citation-api-secret")
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'citation-api.db'}",
        storage_root=tmp_path / "flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    unit = _seed_committed_chain(dependencies)
    _seed_ai_context(dependencies, unit)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(
        citation_service_module,
        "_now",
        lambda: datetime(2026, 7, 16, 0, 5, tzinfo=UTC),
    )
    citation = foundry._services.citation.resolve(_CTX, _citation_request()).citations[0]
    client = TestClient(api_main.app)

    response = client.post(
        "/api/aip/citations/navigation/resolve",
        json={"navigationRef": citation.signed_navigation_ref},
        headers=_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["navigationPath"] == "/document-intelligence"
    assert payload["sourceResourceId"] == "content-unit://miv-api-pdf-1/cu-api-pdf-1"
    assert payload["evidence"]["pageNumber"] == 2
    assert payload["evidence"]["bbox"] == _BBOX
    assert payload["evidence"]["sourceLocator"]["bbox"] == _BBOX
    assert payload["evidence"]["modelName"] is None
    assert payload["evidence"]["modelVersion"] is None
    assert unit.text not in json.dumps(payload)

    tampered = f"{citation.signed_navigation_ref[:-1]}0"
    assert _resolve(client, tampered).status_code == 403
    assert _resolve(client, citation.signed_navigation_ref, tenant_id="tenant-other").status_code == 403

    monkeypatch.setattr(
        citation_service_module,
        "_now",
        lambda: datetime(2026, 7, 16, 0, 16, tzinfo=UTC),
    )
    assert _resolve(client, citation.signed_navigation_ref).status_code == 403

    monkeypatch.setattr(
        citation_service_module,
        "_now",
        lambda: datetime(2026, 7, 16, 0, 5, tzinfo=UTC),
    )
    with dependencies.engine.begin() as transaction:
        cast(Any, transaction).execute(
            update(db.media_derivatives).where(db.media_derivatives.c.id == "mder-api-layout-1").values(status="FAILED")
        )
    stale = _resolve(client, citation.signed_navigation_ref)
    assert stale.status_code == 403
    assert stale.json()["detail"]["details"]["reason"] == "source_verification_failed"


def _resolve(
    client: TestClient,
    navigation_ref: str,
    *,
    tenant_id: str = "tenant-demo",
):
    return client.post(
        "/api/aip/citations/navigation/resolve",
        json={"navigationRef": navigation_ref},
        headers={**_HEADERS, "X-Tenant-ID": tenant_id},
    )


def _seed_committed_chain(dependencies) -> ContentUnitRecord:
    version = _version()
    derivative = _derivative()
    unit = _unit()
    with dependencies.engine.begin() as transaction:
        dependencies.media_repository.insert_version(transaction=transaction, record=version)
        dependencies.media_derivative_repository.create_derivative_or_get_existing(
            transaction=transaction,
            record=derivative,
        )
        dependencies.media_derivative_repository.insert_content_units(
            transaction=transaction,
            records=[unit],
        )
    return unit


def _seed_ai_context(dependencies, unit: ContentUnitRecord) -> None:
    repository = dependencies.ai_run_repository
    with dependencies.engine.begin() as transaction:
        repository.create_session(transaction=transaction, record=_session())
        repository.create_execution_run(transaction=transaction, record=_run())
        repository.record_context_item(
            transaction=transaction,
            record=AiContextItemRecord(
                id="context-item-api-pdf-1",
                tenant_id="tenant-demo",
                ai_run_id="ai-run-api-pdf-1",
                context_id="ctx-api-pdf-1",
                kind="document",
                source_resource_type="content_unit",
                source_resource_id="content-unit://miv-api-pdf-1/cu-api-pdf-1",
                source_version="miv-api-pdf-1",
                content_hash=document_context_hash(document_context_from_unit(unit)),
                retrieval_method="content_hybrid_authoritative_reread",
                relevance_score=0.99,
                security_partition="tenant-demo:internal",
                token_estimate=48,
                is_selected=True,
                omission_reason=None,
            ),
        )


def _citation_request() -> CitationResolveRequest:
    return CitationResolveRequest(
        ai_run_id="ai-run-api-pdf-1",
        message_id="message-api-pdf-1",
        claims=(CitationClaim("ctx-api-pdf-1", {"start": 0, "end": 24}, 1),),
        issued_at="2026-07-16T00:00:00Z",
        expires_at="2026-07-16T00:15:00Z",
    )


def _session() -> AiSessionRecord:
    return AiSessionRecord(
        id="session-api-pdf-1",
        tenant_id="tenant-demo",
        agent_version_id="agent-v1",
        actor_user_id="operator-1",
        status="active",
        created_at="2026-07-16T00:00:00Z",
        last_activity_at="2026-07-16T00:00:01Z",
    )


def _run() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-api-pdf-1",
        tenant_id="tenant-demo",
        session_id="session-api-pdf-1",
        agent_version_id="agent-v1",
        actor_user_id="operator-1",
        request_id="req-citation-api",
        trace_id="trace-citation-api",
        status="running",
        ontology_version_id="ontology-v1",
        model_alias_version="claude@2026-07-16",
        resolved_model_id="claude",
        resolved_model_revision="2026-07-16",
        prompt_version_id="prompt-v1",
        compiled_prompt_hash="sha256:prompt",
        tool_manifest_hash="sha256:tools",
        context_manifest_hash="sha256:context-manifest",
        state_snapshot_hash="sha256:state",
        policy_snapshot_hash="sha256:policy",
        budget_json={"maxTokens": 512},
        usage_json=None,
        error_json=None,
        started_at="2026-07-16T00:00:02Z",
        completed_at=None,
    )


def _version() -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id="miv-api-pdf-1",
        tenant_id="tenant-demo",
        media_item_id="mi-api-pdf-1",
        media_transaction_id="mtx-api-pdf-1",
        version_number=1,
        blob_key="media/api/report.pdf",
        content_hash="sha256:pdf-bytes",
        byte_size=2048,
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
        media_derivative_id="mder-api-layout-1",
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-api-pdf-1",
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
        content_unit_id="cu-api-pdf-1",
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-api-pdf-1",
        derivative_id="mder-api-layout-1",
        unit_kind="layout_block",
        ordinal=1,
        text="Raw PDF body must not appear in navigation responses or errors.",
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
