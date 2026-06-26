"""Unit tests for AIP Agent Runtime read-only execution (P0n, §8.5/§8.6/§8.9/§10.2)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.services.aip.agent_runtime_citations import (
    citation_error_payload,
    resolve_agent_answer_citations,
)
from foundry_lite.application.services.aip.citation_service import (
    CitationResolveRequest,
    CitationResolveResult,
    CitationServiceError,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from sqlalchemy import func, select

from tests.conftest import prepare_indexed_demo

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "data_engineer", "ops_manager"),
    request_id="req-agent-runtime",
)


class _ToolCallingLanguageModel:
    profile_name = "tool-calling-language-model"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="I should call a tool.",
            finish_reason="tool_calls",
            input_tokens=1,
            output_tokens=1,
            normalized_tool_calls=(ModelToolCall(tool_name="order.lookup", arguments_json="{}"),),
            provider_request_id="tool-call-request",
        )


class _CitingLanguageModel:
    profile_name = "citing-language-model"

    def __init__(self, *, forged_context_id: str | None = None) -> None:
        self._forged_context_id = forged_context_id

    def complete(self, request: ModelRequest) -> ModelResponse:
        context_id = self._forged_context_id or _first_citation_context_id(request)
        answer = "Order O-1001 is delayed because fulfillment is blocked."
        content = json.dumps(
            {
                "answer": answer,
                "citations": [
                    {
                        "contextId": context_id,
                        "claimSpan": {"start": 0, "end": 13},
                        "citationOrder": 1,
                    }
                ],
            },
            sort_keys=True,
        )
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content=content,
            finish_reason="stop",
            input_tokens=8,
            output_tokens=8,
            normalized_tool_calls=(),
            provider_request_id="citation-request",
        )


class _CapturingSchemaLanguageModel:
    profile_name = "capturing-schema-language-model"

    def __init__(self) -> None:
        self.response_schema: str | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.response_schema = request.response_schema
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="schema captured",
            finish_reason="stop",
            input_tokens=2,
            output_tokens=2,
            normalized_tool_calls=(),
            provider_request_id="schema-request",
        )


class _UnexpectedCitationResolver:
    def resolve(self, ctx: RequestContext, request: CitationResolveRequest) -> CitationResolveResult:
        raise AssertionError("citation resolver should not be called")


def test_agent_runtime_retrieves_context_calls_model_and_links_operations(foundry: Any) -> None:
    prepare_indexed_demo(foundry)

    result = foundry.aip.run_agent_payload(payload=_payload(), ctx=_CTX)

    detail = foundry.operations.run_detail("ai", result.ai_run_id or "", ctx=_CTX)
    payload = result.to_payload()

    assert result.run_status == "succeeded"
    assert result.answer == "echo: Explain Order O-1001 for the operator."
    assert result.context_ids
    assert payload["operations"] == {
        "runType": "ai",
        "runId": result.ai_run_id,
        "detailPath": f"/api/operations/runs/ai/{result.ai_run_id}",
    }
    assert detail["ai"]["summary"]["status"] == "succeeded"
    assert detail["ai"]["summary"]["modelCallCount"] == 1
    assert detail["ai"]["summary"]["contextItemCount"] == 1
    assert detail["ai"]["summary"]["usageLedgerCount"] == 1
    assert detail["ai"]["summary"]["toolCallCount"] == 0
    assert detail["row"]["resolved_model_id"] == "local-fake-model"
    assert detail["row"]["compiled_prompt_hash"].startswith("sha256:")
    assert detail["ai"]["events"][0]["event_type"] == "received"


def test_agent_runtime_citation_payload_plain_text_and_empty_claims_skip_resolver() -> None:
    resolver = _UnexpectedCitationResolver()

    plain = resolve_agent_answer_citations(
        _CTX,
        resolver,
        ai_run_id="ai-run-plain",
        message_id="message-plain",
        model_content="plain answer",
        issued_at="2026-06-26T00:00:00Z",
    )
    structured = resolve_agent_answer_citations(
        _CTX,
        resolver,
        ai_run_id="ai-run-structured",
        message_id="message-structured",
        model_content=json.dumps({"answer": "structured answer"}),
        issued_at="2026-06-26T00:00:00Z",
    )

    assert plain.answer == "plain answer"
    assert plain.citations == ()
    assert structured.answer == "structured answer"
    assert structured.citations == ()
    assert citation_error_payload(RuntimeError("not a citation error")) is None


def test_agent_runtime_forwards_output_schema_to_model_request(foundry: Any) -> None:
    prepare_indexed_demo(foundry)
    adapter = _CapturingSchemaLanguageModel()
    foundry._services.model_gateway.language_model_adapter = adapter
    schema = {"type": "object", "properties": {"answer": {"type": "string"}, "citations": {"type": "array"}}}

    result = foundry.aip.run_agent_payload(payload={**_payload(), "outputSchema": schema}, ctx=_CTX)

    assert result.run_status == "succeeded"
    assert adapter.response_schema is not None
    assert json.loads(adapter.response_schema)["properties"]["citations"]["type"] == "array"


@pytest.mark.parametrize(
    ("model_payload", "expected_detail"),
    [
        ({"answer": "x", "citations": {"contextId": "ctx-1"}}, "citations must be a list of claim objects"),
        ({"answer": "x", "citations": [1]}, "each citation must be an object"),
        (
            {"answer": "x", "citations": [{"claimSpan": {"start": 0, "end": 1}, "citationOrder": 1}]},
            "contextId is required",
        ),
        (
            {"answer": "x", "citations": [{"contextId": "ctx-1", "citationOrder": 1}]},
            "claimSpan is required",
        ),
        (
            {"answer": "x", "citations": [{"contextId": "ctx-1", "claimSpan": {"start": 0}, "citationOrder": 0}]},
            "citationOrder must be a positive integer",
        ),
    ],
)
def test_agent_runtime_citation_payload_validation_rejects_malformed_claims(
    model_payload: dict[str, object], expected_detail: str
) -> None:
    with pytest.raises(CitationServiceError) as excinfo:
        resolve_agent_answer_citations(
            _CTX,
            _UnexpectedCitationResolver(),
            ai_run_id="ai-run-invalid",
            message_id="message-invalid",
            model_content=json.dumps(model_payload),
            issued_at="2026-06-26T00:00:00Z",
        )

    assert excinfo.value.reason == "invalid_citation_payload"
    assert excinfo.value.detail == expected_detail
    assert citation_error_payload(excinfo.value) == {
        "reason": "invalid_citation_payload",
        "detail": expected_detail,
    }


def test_agent_runtime_resolves_model_citations_into_payload_and_ai_ledger(foundry: Any, monkeypatch: Any) -> None:
    prepare_indexed_demo(foundry)
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER", "agent-runtime-citation-secret")
    foundry._services.model_gateway.language_model_adapter = _CitingLanguageModel()

    result = foundry.aip.run_agent_payload(
        payload={**_payload(), "agentRunId": "agent-runtime-citation"},
        ctx=_CTX,
    )
    payload = result.to_payload()
    detail = foundry.operations.run_detail("ai", result.ai_run_id or "", ctx=_CTX)

    assert result.run_status == "succeeded"
    assert result.answer == "Order O-1001 is delayed because fulfillment is blocked."
    assert len(payload["citations"]) == 1
    assert payload["citations"][0]["contextId"] == result.context_ids[0]
    assert str(payload["citations"][0]["navigationRef"]).startswith("flite-citation-nav.v1.")
    assert "agent-runtime-citation-secret" not in str(payload["citations"][0]["navigationRef"])
    assert detail["ai"]["summary"]["citationCount"] == 1
    assert detail["ai"]["citations"][0]["rendered_ref"].startswith("[1] object:")
    assert detail["ai"]["citations"][0]["context_item_id"] == f"{result.ai_run_id}-context-1"


def test_agent_runtime_packs_document_context_into_ai_ledger(foundry: Any) -> None:
    _seed_agent_document_context(foundry)

    result = foundry.aip.run_agent_payload(
        payload={
            **_payload(),
            "agentRunId": "agent-runtime-document-context",
            "userMessage": "Summarize the expedited payment terms.",
            "stateJson": {},
            "modelAllowedClassifications": ["internal"],
        },
        ctx=_CTX,
    )

    assert result.run_status == "succeeded"
    assert len(result.context_ids) == 1
    with foundry.engine.begin() as conn:
        row = (
            conn.execute(select(db.ai_context_items).where(db.ai_context_items.c.ai_run_id == result.ai_run_id))
            .mappings()
            .one()
        )
    assert row["kind"] == "document"
    assert row["source_resource_type"] == "content_unit"
    assert row["source_resource_id"] == "content-unit://miv-agent-doc-1/cu-agent-doc-1"
    assert row["retrieval_method"] == "content_hybrid_authoritative_reread"
    assert row["content_hash"].startswith("sha256:")


def test_agent_runtime_rejects_forged_model_citation_before_success(foundry: Any, monkeypatch: Any) -> None:
    prepare_indexed_demo(foundry)
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER", "agent-runtime-citation-secret")
    foundry._services.model_gateway.language_model_adapter = _CitingLanguageModel(forged_context_id="ctx-forged-url")

    result = foundry.aip.run_agent_payload(
        payload={**_payload(), "agentRunId": "agent-runtime-forged-citation"},
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.ai_run_id is not None
    assert result.error == {
        "reason": "context_not_in_manifest",
        "detail": "context id ctx-forged-url is not in the run manifest",
    }
    detail = foundry.operations.run_detail("ai", result.ai_run_id, ctx=_CTX)
    assert detail["row"]["status"] == "failed"
    assert detail["ai"]["summary"]["citationCount"] == 0
    assert detail["ai"]["events"][-1]["event_type"] == "failed"


def test_agent_runtime_rejects_tool_calls_and_marks_seeded_run_failed(foundry: Any) -> None:
    prepare_indexed_demo(foundry)
    foundry._services.model_gateway.language_model_adapter = _ToolCallingLanguageModel()

    result = foundry.aip.run_agent_payload(
        payload={**_payload(), "agentRunId": "agent-runtime-tool-call"},
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.ai_run_id is not None
    assert result.error == {
        "reason": "tool_calls_not_supported_in_readonly_runtime",
        "detail": "model returned tool calls",
    }
    detail = foundry.operations.run_detail("ai", result.ai_run_id, ctx=_CTX)
    assert detail["row"]["status"] == "failed"
    assert detail["ai"]["events"][-1]["event_type"] == "failed"
    assert detail["row"]["error_json"] == result.error


def test_agent_runtime_rejects_non_allowlisted_partition_before_ledger(foundry: Any) -> None:
    result = foundry.aip.run_agent_payload(
        payload={**_payload(), "securityPartition": "tenant-demo:secret"},
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.ai_run_id is None
    assert result.error == {
        "reason": "security_partition_mismatch",
        "detail": "security partition must be explicitly allowlisted",
    }
    assert _table_count(foundry.engine, db.ai_execution_runs) == 0


def test_agent_runtime_rejects_outside_tenant_partition_before_ledger(foundry: Any) -> None:
    result = foundry.aip.run_agent_payload(
        payload={
            **_payload(),
            "securityPartition": "tenant-other:internal",
            "allowedSecurityPartitions": ["tenant-other:internal"],
        },
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.to_payload()["operations"] is None
    assert result.error == {
        "reason": "security_partition_mismatch",
        "detail": "security partition is outside the tenant boundary",
    }
    assert _table_count(foundry.engine, db.ai_execution_runs) == 0


def test_agent_runtime_rejects_unsupported_budget_before_ledger(foundry: Any) -> None:
    result = foundry.aip.run_agent_payload(
        payload={**_payload(), "maxModelCalls": 2, "maxLoopIterations": 1},
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.to_payload()["error"] == {
        "reason": "unsupported_budget",
        "detail": "read-only runtime supports exactly one model call and loop",
    }
    assert _table_count(foundry.engine, db.ai_execution_runs) == 0


def _payload() -> dict[str, object]:
    return {
        "agentRunId": "agent-runtime-unit-1",
        "agentVersionId": "agent.order-ops.v1",
        "modelAlias": "default-completion",
        "promptVersionId": "prompt-order-copilot@v1",
        "userMessage": "Explain Order O-1001 for the operator.",
        "agentInstruction": "Answer as the Order Operations Copilot. Do not execute tools.",
        "securityPartition": "tenant-demo:internal",
        "allowedSecurityPartitions": ["tenant-demo:internal"],
        "stateJson": {"objectType": "Order", "objectId": "O-1001"},
        "outputSchema": {"type": "object"},
        "dataClassification": "internal",
        "maxContextItems": 4,
        "maxContextTokens": 1200,
        "maxModelCalls": 1,
        "maxLoopIterations": 1,
        "maxOutputTokens": 512,
        "policyVersion": "policy-v1",
    }


def _seed_agent_document_context(foundry: Any) -> None:
    derivative_id = "mder-agent-doc-1"
    envelope = {"tenantId": _CTX.tenant_id, "classification": "internal"}
    with foundry.engine.begin() as conn:
        foundry._services.media.retrieval.media_derivative_repository.create_derivative_or_get_existing(
            transaction=conn,
            record=MediaDerivativeRecord(
                media_derivative_id=derivative_id,
                tenant_id=_CTX.tenant_id,
                source_media_item_version_id="miv-agent-doc-1",
                derivative_kind="pdf_text",
                processor_spec_hash="agent-doc-spec",
                processor_name="pdf_text_v1",
                processor_version="1.0.0",
                model_name=None,
                model_version="",
                params_hash="agent-doc-spec",
                security_envelope=dict(envelope),
                status="COMMITTED",
                created_at="2026-06-25T00:00:00Z",
            ),
        )
        foundry._services.media.retrieval.media_derivative_repository.insert_content_units(
            transaction=conn,
            records=[
                ContentUnitRecord(
                    content_unit_id="cu-agent-doc-1",
                    tenant_id=_CTX.tenant_id,
                    source_media_item_version_id="miv-agent-doc-1",
                    derivative_id=derivative_id,
                    unit_kind="page",
                    ordinal=1,
                    text="Expedited payment terms require approval before supplier release.",
                    text_hash="agent-doc-text-hash",
                    chunk_spec_hash="agent-doc-chunk-v1",
                    security_envelope=dict(envelope),
                    page_number=7,
                    created_at="2026-06-25T00:00:00Z",
                )
            ],
        )
    foundry.media.configure_content_generation(_CTX, generation="agent-doc-g1")
    foundry.media.index_derivative(_CTX, media_derivative_id=derivative_id, generation="agent-doc-g1")
    foundry.media.promote_content_generation(_CTX, expected_active="", generation="agent-doc-g1")


def _table_count(engine: Any, table: Any) -> int:
    with engine.begin() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _first_citation_context_id(request: ModelRequest) -> str:
    system = request.messages[0].content
    marker = "## citation_mapping\n"
    start = system.index(marker) + len(marker)
    end = system.find("\n\n## ", start)
    payload = json.loads(system[start:] if end == -1 else system[start:end])
    return str(payload["citations"][0]["context_id"])
