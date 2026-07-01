"""Unit tests for AIP Agent Runtime read-only execution (P0n, §8.5/§8.6/§8.9/§10.2)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.services.aip.agent_runtime_citations import (
    citation_error_payload,
    resolve_agent_answer_citations,
)
from foundry_lite.application.services.aip.agent_runtime_contracts import (
    AgentRuntimeError,
    AgentRuntimeRequest,
    validate_request,
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


class _ToolCallingThenAnswerLanguageModel:
    profile_name = "tool-calling-then-answer-language-model"

    def __init__(self) -> None:
        self.followup_request_hash = ""
        self.followup_prompt = ""

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.model_call_attempt == 1:
            return ModelResponse(
                provider="fake",
                resolved_model_id="",
                resolved_model_revision="",
                content="I need the order object.",
                finish_reason="tool_calls",
                input_tokens=4,
                output_tokens=4,
                normalized_tool_calls=(
                    ModelToolCall(
                        tool_name="ontology.get_object",
                        arguments_json=json.dumps(
                            {
                                "object_type": "Order",
                                "object_id": "O-1001",
                                "property_names": ["orderId", "status"],
                            },
                            sort_keys=True,
                        ),
                    ),
                ),
                provider_request_id="tool-call-request-1",
            )
        self.followup_request_hash = request.request_hash
        self.followup_prompt = request.messages[-1].content
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="Order O-1001 was checked through the brokered object tool.",
            finish_reason="stop",
            input_tokens=7,
            output_tokens=8,
            normalized_tool_calls=(),
            provider_request_id="tool-call-request-2",
        )


class _DirectVendorToolCallingLanguageModel:
    profile_name = "direct-vendor-tool-calling-language-model"

    def __init__(self) -> None:
        self.offered_tools: tuple[str, ...] = ()
        self.system_prompt = ""

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.offered_tools = request.tools
        self.system_prompt = request.messages[0].content
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="I should call a vendor API directly.",
            finish_reason="tool_calls",
            input_tokens=4,
            output_tokens=4,
            normalized_tool_calls=(
                ModelToolCall(
                    tool_name="http.request",
                    arguments_json='{"url":"https://api.vendor.example/orders"}',
                ),
            ),
            provider_request_id="direct-vendor-tool-request",
        )


class _ActionProposingLanguageModel:
    profile_name = "action-proposing-language-model"

    def __init__(self, *, expected_object_version: int, tool_name: str = "action.propose") -> None:
        self._expected_object_version = expected_object_version
        self._tool_name = tool_name

    def complete(self, request: ModelRequest) -> ModelResponse:
        context_id = _first_citation_context_id(request)
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="I should propose an approval action for review.",
            finish_reason="tool_calls",
            input_tokens=6,
            output_tokens=5,
            normalized_tool_calls=(
                ModelToolCall(
                    tool_name=self._tool_name,
                    arguments_json=json.dumps(
                        {
                            "actionType": "ApproveOrder",
                            "targetObjectType": "Order",
                            "targetObjectId": "O-1001",
                            "expectedObjectVersion": self._expected_object_version,
                            "parameters": {"reason": "Inventory confirmed"},
                            "evidenceContextIds": [context_id],
                            "expiresAt": "2026-06-26T23:59:00Z",
                            "claimText": "Approve O-1001 based on selected AI evidence.",
                        },
                        sort_keys=True,
                    ),
                ),
            ),
            provider_request_id="action-proposal-request-1",
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


class _FailIfCalledLanguageModel:
    profile_name = "fail-if-called-language-model"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("model should not be called when prompt artifact write fails")


class _FailingPromptArtifactService:
    def record_compiled_prompt(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("prompt artifact store unavailable")


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
    assert detail["ai"]["summary"]["promptArtifactCount"] == 1
    assert detail["ai"]["summary"]["toolCallCount"] == 0
    assert detail["row"]["resolved_model_id"] == "local-fake-model"
    assert detail["row"]["compiled_prompt_hash"].startswith("sha256:")
    prompt_artifact = detail["ai"]["promptArtifacts"][0]
    assert prompt_artifact["content_hash"] == detail["row"]["compiled_prompt_hash"]
    assert prompt_artifact["artifact_ref"].startswith("local-prompt-artifact://")
    reader_ctx = RequestContext(
        tenant_id=_CTX.tenant_id,
        actor_user_id="auditor",
        roles=(*_CTX.roles, "aip_prompt_artifact_reader"),
        request_id="req-agent-runtime-reader",
    )
    decrypted = foundry._services.prompt_artifact.read_prompt_artifact(
        reader_ctx, artifact_id=str(prompt_artifact["id"])
    )
    decrypted_payload = json.loads(decrypted.plaintext)
    assert decrypted_payload["messages"][1]["content"] == "Explain Order O-1001 for the operator."
    assert _hash_text(decrypted.plaintext) == decrypted.content_hash
    assert detail["ai"]["events"][0]["event_type"] == "received"
    assert "Explain Order O-1001 for the operator." not in json.dumps(detail, sort_keys=True)


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
    citation = payload["citations"][0]
    assert citation["contextId"] == result.context_ids[0]
    assert str(citation["navigationRef"]).startswith("flite-citation-nav.v1.")
    assert "agent-runtime-citation-secret" not in str(citation["navigationRef"])
    source_preview = citation["sourcePreview"]
    assert isinstance(source_preview, dict)
    assert source_preview["contextItemId"] == f"{result.ai_run_id}-context-1"
    assert source_preview["kind"] == "object"
    assert source_preview["sourceResourceId"] == "object://Order/O-1001"
    assert source_preview["retrievalMethod"] == "object_authoritative_reread"
    assert source_preview["selected"] is True
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


def test_agent_runtime_executes_one_model_tool_call_through_broker_and_finishes(foundry: Any) -> None:
    prepare_indexed_demo(foundry)
    adapter = _ToolCallingThenAnswerLanguageModel()
    foundry._services.model_gateway.language_model_adapter = adapter

    result = foundry.aip.run_agent_payload(
        payload={
            **_payload(),
            "agentRunId": "agent-runtime-tool-loop",
            "agentInstruction": "Use only brokered tools when a tool is required.",
            "maxModelCalls": 2,
            "maxLoopIterations": 2,
            "maxToolCalls": 1,
            "toolManifest": [_tool_spec_payload()],
            "agentAllowedTools": ["ontology.get_object"],
            "modelAllowedClassifications": ["public", "internal"],
        },
        ctx=_CTX,
    )

    assert result.run_status == "succeeded"
    assert result.answer == "Order O-1001 was checked through the brokered object tool."
    assert adapter.followup_request_hash.startswith("sha256:")
    assert "brokered_tool_result" in adapter.followup_prompt

    detail = foundry.operations.run_detail("ai", result.ai_run_id or "", ctx=_CTX)
    assert detail["ai"]["summary"]["status"] == "succeeded"
    assert detail["ai"]["summary"]["modelCallCount"] == 2
    assert detail["ai"]["summary"]["toolCallCount"] == 1
    assert detail["ai"]["summary"]["promptArtifactCount"] == 2
    assert detail["ai"]["toolCalls"][0]["tool_id"] == "ontology.get_object"
    assert detail["ai"]["toolCalls"][0]["arguments_hash"].startswith("sha256:")
    assert detail["ai"]["toolCalls"][0]["result_hash"].startswith("sha256:")
    assert any(row["content_hash"] == adapter.followup_request_hash for row in detail["ai"]["promptArtifacts"])
    serialized_detail = json.dumps(detail, sort_keys=True)
    assert "brokered_tool_result" not in serialized_detail
    assert "property_names" not in serialized_detail


def test_agent_runtime_action_propose_tool_creates_review_without_executing_action(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry._services.model_gateway.language_model_adapter = _ActionProposingLanguageModel(
        expected_object_version=int(order["objectVersion"])
    )
    before_action_runs = _table_count(foundry.engine, db.action_runs)

    result = foundry.aip.run_agent_payload(
        payload={
            **_payload(),
            "agentRunId": "agent-runtime-action-proposal-tool",
            "agentInstruction": "Propose actions for human review; never execute writes directly.",
            "maxModelCalls": 2,
            "maxLoopIterations": 2,
            "maxToolCalls": 1,
            "toolManifest": [_action_proposal_tool_spec_payload()],
            "agentAllowedTools": ["action.propose"],
            "agentAllowedActions": ["ApproveOrder"],
            "modelAllowedClassifications": ["public", "internal"],
        },
        ctx=_CTX,
    )

    assert result.run_status == "succeeded"
    assert result.answer is not None
    assert "awaiting human review" in result.answer
    assert result.usage is not None
    assert result.usage["modelCallCount"] == 1
    assert result.usage["toolCallCount"] == 1
    assert result.usage["actionProposalCount"] == 1

    detail = foundry.operations.run_detail("ai", result.ai_run_id or "", ctx=_CTX)
    tool_call = detail["ai"]["toolCalls"][0]
    review = _review_for_run(foundry.engine, result.ai_run_id or "")
    assert detail["ai"]["summary"]["modelCallCount"] == 1
    assert detail["ai"]["summary"]["toolCallCount"] == 1
    assert detail["ai"]["summary"]["promptArtifactCount"] == 1
    assert detail["ai"]["events"][-2]["event_type"] == "waiting_human_review"
    assert tool_call["tool_id"] == "action.propose"
    assert tool_call["effect"] == "PROPOSE_WRITE"
    assert tool_call["authorization_decision"] == "pending_human_review"
    assert tool_call["confirmation_policy"] == "HUMAN_REVIEW"
    assert tool_call["status"] == "pending_review"
    assert tool_call["result_hash"] == review["proposal_fingerprint"]
    assert review["originating_tool_call_id"] == f"{result.ai_run_id}-tool-1"
    assert review["execution_status"] == "pending_review"
    assert _table_count(foundry.engine, db.action_runs) == before_action_runs
    assert "Inventory confirmed" not in json.dumps(detail, sort_keys=True)


def test_agent_runtime_direct_write_tool_is_denied_without_review_or_action(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry._services.model_gateway.language_model_adapter = _ActionProposingLanguageModel(
        expected_object_version=int(order["objectVersion"]),
        tool_name="action.apply",
    )

    result = foundry.aip.run_agent_payload(
        payload={
            **_payload(),
            "agentRunId": "agent-runtime-direct-write-denied",
            "maxModelCalls": 2,
            "maxLoopIterations": 2,
            "maxToolCalls": 1,
            "toolManifest": [_action_proposal_tool_spec_payload(tool_id="action.apply", effect="WRITE")],
            "agentAllowedTools": ["action.apply"],
            "agentAllowedActions": ["ApproveOrder"],
        },
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.error == {
        "reason": "direct_write_tool_denied",
        "detail": "direct WRITE tools must use action proposals",
    }
    assert _table_count(foundry.engine, db.insight_reviews) == 0
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_agent_runtime_rejects_direct_vendor_api_tool_without_executor(foundry: Any) -> None:
    prepare_indexed_demo(foundry)
    adapter = _DirectVendorToolCallingLanguageModel()
    foundry._services.model_gateway.language_model_adapter = adapter

    result = foundry.aip.run_agent_payload(
        payload={
            **_payload(),
            "agentRunId": "agent-runtime-direct-vendor-tool-denied",
            "maxModelCalls": 2,
            "maxLoopIterations": 2,
            "maxToolCalls": 1,
            "toolManifest": [_direct_vendor_tool_spec_payload()],
            "agentAllowedTools": ["http.request"],
            "modelAllowedClassifications": ["public", "internal"],
        },
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.ai_run_id is not None
    assert result.error == {
        "reason": "direct_vendor_tool_denied",
        "detail": "direct vendor/API tools must use governed connectors, webhooks, or action proposals",
    }
    detail = foundry.operations.run_detail("ai", result.ai_run_id, ctx=_CTX)
    assert detail["ai"]["summary"]["toolCallCount"] == 0
    assert _table_count(foundry.engine, db.action_runs) == 0
    assert adapter.offered_tools == ()
    assert "http.request" not in adapter.system_prompt


def test_agent_runtime_fails_before_model_when_prompt_artifact_write_fails(foundry: Any) -> None:
    prepare_indexed_demo(foundry)
    foundry._services.agent_runtime.prompt_artifact_service = _FailingPromptArtifactService()
    foundry._services.model_gateway.language_model_adapter = _FailIfCalledLanguageModel()

    result = foundry.aip.run_agent_payload(
        payload={**_payload(), "agentRunId": "agent-runtime-prompt-artifact-fail"},
        ctx=_CTX,
    )

    assert result.run_status == "failed"
    assert result.ai_run_id is not None
    assert result.error == {"reason": "RuntimeError", "detail": "prompt artifact store unavailable"}
    detail = foundry.operations.run_detail("ai", result.ai_run_id, ctx=_CTX)
    assert detail["row"]["status"] == "failed"
    assert detail["ai"]["summary"]["modelCallCount"] == 0
    assert detail["ai"]["summary"]["promptArtifactCount"] == 0


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
        "detail": "agent runtime supports one model turn or one tool loop",
    }
    assert _table_count(foundry.engine, db.ai_execution_runs) == 0


def test_agent_runtime_request_validation_requires_exact_allowlisted_partition() -> None:
    request = _runtime_request(security_partition="tenant-demo:secret")

    with pytest.raises(AgentRuntimeError) as excinfo:
        validate_request(_CTX, request)

    assert excinfo.value.args == (
        "security_partition_mismatch",
        "security partition must be explicitly allowlisted",
    )


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


def _runtime_request(**overrides: object) -> AgentRuntimeRequest:
    values = {
        "agent_run_id": "agent-runtime-validation",
        "agent_version_id": "agent.order-ops.v1",
        "model_alias": "default-completion",
        "prompt_version_id": "prompt-order-copilot@v1",
        "user_message": "Explain Order O-1001 for the operator.",
        "agent_instruction": "Answer as the Order Operations Copilot.",
        "security_partition": "tenant-demo:internal",
        "allowed_security_partitions": ("tenant-demo:internal",),
        "state_json": {"objectType": "Order", "objectId": "O-1001"},
    }
    values.update(overrides)
    return AgentRuntimeRequest(**values)


def _tool_spec_payload() -> dict[str, object]:
    return {
        "toolId": "ontology.get_object",
        "version": "2026-06-25",
        "inputSchema": {
            "type": "object",
            "required": ["object_type", "object_id"],
            "properties": {
                "object_type": {"type": "string"},
                "object_id": {"type": "string"},
                "property_names": {"type": "array"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "effect": "READ",
        "requiredPermission": "object:read",
        "confirmationPolicy": "NONE",
        "objectTypeAllowlist": ["Order"],
        "propertyAllowlist": ["orderId", "status"],
        "timeoutSeconds": 30,
        "maxResultItems": 10,
        "resultClassification": "internal",
        "status": "published",
    }


def _action_proposal_tool_spec_payload(
    *, tool_id: str = "action.propose", effect: str = "PROPOSE_WRITE"
) -> dict[str, object]:
    return {
        "toolId": tool_id,
        "version": "2026-06-26",
        "inputSchema": {
            "type": "object",
            "required": [
                "actionType",
                "targetObjectType",
                "targetObjectId",
                "expectedObjectVersion",
                "parameters",
                "evidenceContextIds",
                "expiresAt",
                "claimText",
            ],
            "properties": {
                "actionType": {"type": "string"},
                "targetObjectType": {"type": "string"},
                "targetObjectId": {"type": "string"},
                "expectedObjectVersion": {"type": "integer"},
                "parameters": {"type": "object"},
                "evidenceContextIds": {"type": "array"},
                "expiresAt": {"type": "string"},
                "claimText": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "effect": effect,
        "requiredPermission": "insight:create",
        "confirmationPolicy": "HUMAN_REVIEW",
        "objectTypeAllowlist": ["Order"],
        "propertyAllowlist": [],
        "timeoutSeconds": 30,
        "maxResultItems": 1,
        "resultClassification": "internal",
        "status": "published",
    }


def _direct_vendor_tool_spec_payload() -> dict[str, object]:
    return {
        "toolId": "http.request",
        "version": "2026-06-25",
        "inputSchema": {
            "type": "object",
            "required": ["url"],
            "properties": {"url": {"type": "string"}},
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "effect": "READ",
        "requiredPermission": "object:read",
        "confirmationPolicy": "NONE",
        "objectTypeAllowlist": [],
        "propertyAllowlist": [],
        "timeoutSeconds": 30,
        "maxResultItems": 1,
        "resultClassification": "internal",
        "status": "published",
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


def _review_for_run(engine: Any, ai_run_id: str) -> dict[str, object]:
    with engine.begin() as conn:
        row = (
            conn.execute(select(db.insight_reviews).where(db.insight_reviews.c.originating_ai_run_id == ai_run_id))
            .mappings()
            .one()
        )
    return dict(row)


def _first_citation_context_id(request: ModelRequest) -> str:
    system = request.messages[0].content
    marker = "## citation_mapping\n"
    start = system.index(marker) + len(marker)
    end = system.find("\n\n## ", start)
    payload = json.loads(system[start:] if end == -1 else system[start:end])
    return str(payload["citations"][0]["context_id"])


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
