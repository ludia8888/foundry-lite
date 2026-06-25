"""Unit tests for ``CitationService`` (AIP-lite P0f, §8.7/§9.4)."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.ports.ai_run_repository import (
    AiContextItemRecord,
    AiExecutionRunRecord,
    AiSessionRecord,
)
from foundry_lite.application.ports.citation_source import (
    CitationSourceVerificationRequest,
    CitationSourceVerificationResult,
)
from foundry_lite.application.services.aip.citation_service import (
    CitationClaim,
    CitationResolveRequest,
    CitationService,
    CitationServiceError,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_CTX = RequestContext(tenant_id="tenant-demo", actor_user_id="user-1", roles=("viewer",), request_id="req-1")
_SECRET_ENV = {"FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER": "citation-test-secret"}


def test_citation_service_resolves_selected_context_records_ledger_and_returns_signed_navigation_ref() -> None:
    engine, repository = _seed_repository()
    verifier = _SpyCitationSourceVerifier()
    service = _service(engine, repository, verifier)

    result = service.resolve(_CTX, _resolve_request())
    ledger = _ledger(repository, engine)

    citation = result.citations[0]
    assert verifier.calls == 1
    assert citation.context_id == "ctx-order-1"
    assert citation.rendered_ref == "[1] object:Order:O-1001@object-version-1"
    assert citation.signed_navigation_ref.startswith("flite-citation-nav.v1.")
    assert citation.ledger_record.context_item_id == "context-item-1"
    assert citation.display_payload["sourceResourceId"] == "Order:O-1001"
    assert ledger["citations"][0]["rendered_ref"] == "[1] object:Order:O-1001@object-version-1"
    assert "citation-test-secret" not in citation.signed_navigation_ref

    payload = _decode_navigation_payload(citation.signed_navigation_ref)
    assert payload["tenant_id"] == "tenant-demo"
    assert payload["context_id"] == "ctx-order-1"
    assert payload["content_hash"] == "sha256:context"
    assert payload["navigation_path"] == "/sources/object/Order:O-1001"


def test_citation_service_rejects_context_id_not_in_run_manifest_before_source_reread() -> None:
    engine, repository = _seed_repository()
    verifier = _SpyCitationSourceVerifier()
    service = _service(engine, repository, verifier)

    with pytest.raises(CitationServiceError) as excinfo:
        service.resolve(
            _CTX,
            _resolve_request(claims=(CitationClaim("ctx-forged-url", {"start": 0, "end": 4}, 1),)),
        )

    assert excinfo.value.reason == "context_not_in_manifest"
    assert verifier.calls == 0
    assert _ledger(repository, engine)["citations"] == []


def test_citation_service_rejects_unselected_context_items() -> None:
    engine, repository = _seed_repository(context_record=_context_item_record(is_selected=False))
    verifier = _SpyCitationSourceVerifier()
    service = _service(engine, repository, verifier)

    with pytest.raises(CitationServiceError) as excinfo:
        service.resolve(_CTX, _resolve_request())

    assert excinfo.value.reason == "context_not_selected"
    assert verifier.calls == 0
    assert _ledger(repository, engine)["citations"] == []


def test_citation_service_rejects_stale_source_version_or_hash_before_ledger_write() -> None:
    engine, repository = _seed_repository()
    verifier = _SpyCitationSourceVerifier(content_hash="sha256:changed")
    service = _service(engine, repository, verifier)

    with pytest.raises(CitationServiceError) as excinfo:
        service.resolve(_CTX, _resolve_request())

    assert excinfo.value.reason == "source_stale"
    assert verifier.calls == 1
    assert _ledger(repository, engine)["citations"] == []


def test_citation_service_requires_source_read_permission() -> None:
    engine, repository = _seed_repository(context_record=_context_item_record(source_resource_type="dataset"))
    verifier = _SpyCitationSourceVerifier()
    service = _service(engine, repository, verifier)
    denied_ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-1", roles=(), request_id="req-1")

    with pytest.raises(CitationServiceError) as excinfo:
        service.resolve(denied_ctx, _resolve_request())

    assert excinfo.value.reason == "policy_denied"
    assert verifier.calls == 0


def test_local_runtime_composes_citation_service_with_ai_ledger_and_fake_source_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER", "runtime-citation-secret")
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "citation-runtime")
    dependencies.metadata_repository.initialize_schema()
    services = CoreServices.create(dependencies)
    _seed_repository(
        cast(Engine, dependencies.engine),
        cast(SqlAlchemyAiRunRepository, dependencies.ai_run_repository),
    )

    result = services.citation.resolve(_CTX, _resolve_request())

    assert result.citations[0].ledger_record.tenant_id == "tenant-demo"
    assert result.citations[0].signed_navigation_ref.startswith("flite-citation-nav.v1.")


def _service(
    engine: Engine, repository: SqlAlchemyAiRunRepository, verifier: _SpyCitationSourceVerifier
) -> CitationService:
    return CitationService(
        engine=engine,
        policy=PolicyService(),
        ai_run_repository=repository,
        citation_source_verifier=verifier,
        secret_provider=EnvSecretProvider(environ=_SECRET_ENV),
    )


def _seed_repository(
    engine: Engine | None = None,
    repository: SqlAlchemyAiRunRepository | None = None,
    *,
    context_record: AiContextItemRecord | None = None,
) -> tuple[Engine, SqlAlchemyAiRunRepository]:
    engine = engine or _sqlite_engine()
    repository = repository or SqlAlchemyAiRunRepository(engine)
    with engine.begin() as transaction:
        repository.create_session(transaction=transaction, record=_session_record())
        repository.create_execution_run(transaction=transaction, record=_run_record())
        repository.record_context_item(transaction=transaction, record=context_record or _context_item_record())
    return engine, repository


def _ledger(repository: SqlAlchemyAiRunRepository, engine: Engine) -> Mapping[str, Any]:
    with engine.begin() as transaction:
        ledger = repository.ledger_for_run(transaction=transaction, tenant_id="tenant-demo", ai_run_id="ai-run-1")
    assert ledger is not None
    return ledger


def _resolve_request(
    *, claims: tuple[CitationClaim, ...] = (CitationClaim("ctx-order-1", {"start": 0, "end": 32}, 1),)
) -> CitationResolveRequest:
    return CitationResolveRequest(
        ai_run_id="ai-run-1",
        message_id="ai-message-1",
        claims=claims,
        issued_at="2026-06-25T00:00:10Z",
        expires_at="2026-06-25T00:05:10Z",
    )


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-1",
        tenant_id="tenant-demo",
        agent_version_id="agent-v1",
        actor_user_id="user-1",
        status="active",
        created_at="2026-06-25T00:00:00Z",
        last_activity_at="2026-06-25T00:00:01Z",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-1",
        tenant_id="tenant-demo",
        session_id="ai-session-1",
        agent_version_id="agent-v1",
        actor_user_id="user-1",
        request_id="req-1",
        trace_id="trace-1",
        status="running",
        ontology_version_id="ontology-v1",
        model_alias_version="gpt-4.1@2026-06-25",
        resolved_model_id="gpt-4.1",
        resolved_model_revision="2026-06-25",
        prompt_version_id="prompt-v1",
        compiled_prompt_hash="sha256:prompt",
        tool_manifest_hash="sha256:tools",
        context_manifest_hash="sha256:context-manifest",
        state_snapshot_hash="sha256:state",
        policy_snapshot_hash="sha256:policy",
        budget_json={"maxTokens": 512},
        usage_json=None,
        error_json=None,
        started_at="2026-06-25T00:00:02Z",
        completed_at=None,
    )


def _context_item_record(
    *, is_selected: bool = True, source_resource_type: str = "object", source_version: str = "object-version-1"
) -> AiContextItemRecord:
    return AiContextItemRecord(
        id="context-item-1",
        tenant_id="tenant-demo",
        ai_run_id="ai-run-1",
        context_id="ctx-order-1",
        kind="object",
        source_resource_type=source_resource_type,
        source_resource_id="Order:O-1001",
        source_version=source_version,
        content_hash="sha256:context",
        retrieval_method="semantic",
        relevance_score=0.98,
        security_partition="tenant-demo:internal",
        token_estimate=44,
        is_selected=is_selected,
        omission_reason=None if is_selected else "budget",
    )


def _sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine


def _decode_navigation_payload(ref: str) -> Mapping[str, object]:
    encoded = ref.split(".")[2]
    padding = "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded + padding))


class _SpyCitationSourceVerifier:
    profile_name = "spy-citation-source-verifier"

    def __init__(self, *, source_version: str = "object-version-1", content_hash: str = "sha256:context") -> None:
        self.source_version = source_version
        self.content_hash = content_hash
        self.calls = 0

    def verify_source(
        self, ctx: RequestContext, request: CitationSourceVerificationRequest
    ) -> CitationSourceVerificationResult:
        self.calls += 1
        return CitationSourceVerificationResult(
            source_resource_type=request.source_resource_type,
            source_resource_id=request.source_resource_id,
            source_version=self.source_version,
            content_hash=self.content_hash,
            display_label=f"{request.source_resource_type}:{request.source_resource_id}@{self.source_version}",
            navigation_path=f"/sources/{request.source_resource_type}/{request.source_resource_id}",
        )
