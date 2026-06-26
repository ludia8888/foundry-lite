"""Unit tests for AIP Agent Runtime read-only execution (P0n, §8.5/§8.6/§8.9/§10.2)."""

from __future__ import annotations

from typing import Any

from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
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
