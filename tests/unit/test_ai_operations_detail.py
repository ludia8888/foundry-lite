from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from foundry_lite.application.ports.ai_run_repository import (
    AiCitationRecord,
    AiContextItemRecord,
    AiExecutionEventRecord,
    AiExecutionRunRecord,
    AiModelCallRecord,
    AiPromptArtifactRecord,
    AiSessionRecord,
    AiToolCallRecord,
    AiUsageLedgerRecord,
)
from foundry_lite.application.services.ai_operations_payload import ai_operations_payload
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyAiRunRepository,
    SqlAlchemyDatasetQualityRepository,
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyRuntimeRepository,
)
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_TENANT = "tenant-demo"
_CTX = RequestContext(tenant_id=_TENANT, actor_user_id="ops-user", roles=("ops_manager",))


def test_ai_operations_run_list_and_detail_expose_safe_ledger_trace() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.create_database(engine)
    ai_repository = SqlAlchemyAiRunRepository(engine)
    _seed_ai_ledger(ai_repository, engine)
    service = RuntimeService(
        engine=engine,
        policy=PolicyService(allow_unwired_classification_provider=True),
        runtime_repository=SqlAlchemyRuntimeRepository(engine),
        ai_run_repository=ai_repository,
        dataset_transaction_repository=SqlAlchemyDatasetTransactionRepository(engine),
        dataset_quality_repository=SqlAlchemyDatasetQualityRepository(engine),
    )

    listed = service.query_runs(ctx=_CTX, run_type="ai", limit=1)
    detail = service.run_detail("ai", "ai-run-ops", ctx=_CTX)

    assert [row["id"] for row in listed["aiRuns"]] == ["ai-run-ops"]
    assert listed["nextCursor"] is None
    assert detail["runType"] == "ai"
    assert detail["runId"] == "ai-run-ops"
    assert detail["correlationId"] == "trace-ops"
    assert detail["errorMessage"] == "provider timeout"
    assert detail["references"]["compiled_prompt_hash"] == "sha256:compiled-prompt"
    ai = detail["ai"]
    assert ai["summary"]["eventCount"] == 2
    assert ai["summary"]["modelCallCount"] == 1
    assert ai["summary"]["promptArtifactCount"] == 1
    assert ai["summary"]["inputTokens"] == 128
    assert ai["summary"]["outputTokens"] == 64
    assert ai["summary"]["estimatedCost"] == 0.0123
    assert ai["trace"]["traceId"] == "trace-ops"
    assert [item["kind"] for item in ai["trace"]["timeline"]] == ["event", "event", "tool_call", "model_call"]
    assert ai["events"][0]["payload_ref"] == "blob://ai-runs/ai-run-ops/intent.json"
    assert ai["modelCalls"][0]["request_hash"] == "sha256:model-request"
    assert ai["promptArtifacts"][0]["artifact_ref"].startswith("local-prompt-artifact://")
    assert ai["promptArtifacts"][0]["content_hash"] == "sha256:compiled-prompt"
    assert ai["toolCalls"][0]["arguments_hash"] == "sha256:tool-args"
    assert ai["toolCalls"][0]["result_hash"] == "sha256:tool-result"
    _assert_no_raw_ai_payload_leaks(detail)


def test_ai_operations_payload_redacts_nested_non_json_primitives_and_sorts_fallback_timeline() -> None:
    payload = ai_operations_payload(
        {
            "run": {
                "id": "ai-run-ops",
                "tenant_id": _TENANT,
                "trace_id": "trace-ops",
                "status": "succeeded",
                "budget_json": {
                    "limits": (Decimal("2.5"), {"rawPrompt": "secret prompt"}),
                    "safeList": [{"content": "secret content"}, "safe marker"],
                },
                "started_at": "2026-06-25T00:00:03Z",
            },
            "events": [],
            "modelCalls": [
                {
                    "id": "model-call-1",
                    "ai_run_id": "ai-run-ops",
                    "attempt": "retry",
                    "model_revision": "2026-06-01",
                    "request_hash": "sha256:model-request",
                    "response_hash": "sha256:model-response",
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "latency_ms": 12,
                    "status": "succeeded",
                }
            ],
            "contextItems": [],
            "toolCalls": [],
            "citations": [],
            "usage": [
                {
                    "id": "usage-1",
                    "ai_run_id": "ai-run-ops",
                    "provider": "acme",
                    "model_revision": "2026-06-01",
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "estimated_cost": Decimal("0.125"),
                    "currency": "USD",
                    "recorded_at": "2026-06-25T00:00:04Z",
                },
                {
                    "id": "usage-2",
                    "ai_run_id": "ai-run-ops",
                    "provider": "acme",
                    "model_revision": "2026-06-01",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "estimated_cost": Decimal("0.250"),
                    "currency": "EUR",
                    "recorded_at": "2026-06-25T00:00:05Z",
                },
            ],
            "promptArtifacts": [],
        }
    )

    serialized = json.dumps(payload, sort_keys=True)
    assert "secret prompt" not in serialized
    assert "secret content" not in serialized
    assert payload["run"]["budget_json"]["limits"] == [2.5, {"rawPrompt": "[redacted]"}]
    assert payload["run"]["budget_json"]["safeList"] == [{"content": "[redacted]"}, "safe marker"]
    assert payload["summary"]["estimatedCost"] == 0.375
    assert payload["summary"]["currency"] == "mixed"
    assert payload["trace"]["timeline"][0]["order"] == "retry"


def _assert_no_raw_ai_payload_leaks(detail: dict[str, Any]) -> None:
    serialized = json.dumps(detail, sort_keys=True)
    assert "do not expose this raw prompt" not in serialized
    assert "raw tool body" not in serialized
    assert "Bearer secret-token" not in serialized
    assert "provider raw response body" not in serialized


def _seed_ai_ledger(ai_repository: SqlAlchemyAiRunRepository, engine: Any) -> None:
    with engine.begin() as transaction:
        ai_repository.create_session(transaction=transaction, record=_session_record())
        ai_repository.create_execution_run(transaction=transaction, record=_run_record())
        ai_repository.append_execution_event(transaction=transaction, record=_event_record("event-1", 1, "run_intent"))
        ai_repository.append_execution_event(
            transaction=transaction,
            record=_event_record("event-2", 2, "model_response"),
        )
        ai_repository.record_model_call(transaction=transaction, record=_model_call_record())
        ai_repository.record_context_item(transaction=transaction, record=_context_item_record(selected=True))
        ai_repository.record_context_item(
            transaction=transaction,
            record=_context_item_record(
                record_id="context-item-2",
                context_id="ctx-omitted",
                selected=False,
                omission_reason="token_budget",
            ),
        )
        ai_repository.record_tool_call(transaction=transaction, record=_tool_call_record())
        ai_repository.record_citation(transaction=transaction, record=_citation_record())
        ai_repository.record_usage(transaction=transaction, record=_usage_record())
        ai_repository.record_prompt_artifact(transaction=transaction, record=_prompt_artifact_record())


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-ops",
        tenant_id=_TENANT,
        agent_version_id="agent-v1",
        actor_user_id="user-1",
        status="active",
        created_at="2026-06-25T00:00:00Z",
        last_activity_at="2026-06-25T00:00:03Z",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-ops",
        tenant_id=_TENANT,
        session_id="ai-session-ops",
        agent_version_id="agent-v1",
        actor_user_id="user-1",
        request_id="req-ops",
        trace_id="trace-ops",
        status="failed",
        ontology_version_id="ontology-v1",
        model_alias_version="default-completion@2026-06-01",
        resolved_model_id="model-1",
        resolved_model_revision="2026-06-01",
        prompt_version_id="prompt-v1",
        compiled_prompt_hash="sha256:compiled-prompt",
        tool_manifest_hash="sha256:tools",
        context_manifest_hash="sha256:context",
        state_snapshot_hash="sha256:state",
        policy_snapshot_hash="sha256:policy",
        budget_json={"maxTokens": 512, "rawPrompt": "do not expose this raw prompt"},
        usage_json={"inputTokens": 128, "outputTokens": 64, "toolResult": "raw tool body"},
        error_json={"message": "provider timeout", "rawResponse": "provider raw response body"},
        started_at="2026-06-25T00:00:03Z",
        completed_at="2026-06-25T00:00:08Z",
    )


def _event_record(event_id: str, sequence: int, event_type: str) -> AiExecutionEventRecord:
    return AiExecutionEventRecord(
        id=event_id,
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        sequence=sequence,
        event_type=event_type,
        payload_ref=f"blob://ai-runs/ai-run-ops/{event_type.removeprefix('run_')}.json",
        payload_hash=f"sha256:{event_type}",
        redacted_preview=f"{event_type} preview",
        created_at=f"2026-06-25T00:00:0{sequence}Z",
    )


def _model_call_record() -> AiModelCallRecord:
    return AiModelCallRecord(
        id="model-call-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        attempt=1,
        provider="acme",
        model_revision="2026-06-01",
        request_hash="sha256:model-request",
        response_hash="sha256:model-response",
        provider_request_id="provider-req-1",
        input_tokens=128,
        output_tokens=64,
        latency_ms=240,
        status="failed",
        error_json={"message": "timeout", "authorization": "Bearer secret-token"},
    )


def _context_item_record(
    *,
    record_id: str = "context-item-1",
    context_id: str = "ctx-selected",
    selected: bool,
    omission_reason: str | None = None,
) -> AiContextItemRecord:
    return AiContextItemRecord(
        id=record_id,
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        context_id=context_id,
        kind="object",
        source_resource_type="object",
        source_resource_id="Order:O-1",
        source_version="object-version-1",
        content_hash=f"sha256:{context_id}",
        retrieval_method="semantic",
        relevance_score=0.91,
        security_partition="tenant-demo:internal",
        token_estimate=42,
        is_selected=selected,
        omission_reason=omission_reason,
    )


def _tool_call_record() -> AiToolCallRecord:
    return AiToolCallRecord(
        id="tool-call-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        sequence=1,
        tool_id="create-action-proposal",
        tool_version="v1",
        arguments_hash="sha256:tool-args",
        effect="propose",
        authorization_decision="allowed",
        confirmation_policy="human_required",
        status="failed",
        result_hash="sha256:tool-result",
        linked_action_run_id=None,
        started_at="2026-06-25T00:00:04Z",
        completed_at="2026-06-25T00:00:05Z",
        error_json={"message": "tool failed", "toolResult": "raw tool body"},
    )


def _citation_record() -> AiCitationRecord:
    return AiCitationRecord(
        id="citation-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        message_id="ai-message-1",
        context_item_id="context-item-1",
        claim_span={"start": 0, "end": 18},
        citation_order=1,
        rendered_ref="Order O-1",
    )


def _usage_record() -> AiUsageLedgerRecord:
    return AiUsageLedgerRecord(
        id="usage-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        provider="acme",
        model_revision="2026-06-01",
        input_tokens=128,
        output_tokens=64,
        estimated_cost=0.0123,
        currency="USD",
        recorded_at="2026-06-25T00:00:06Z",
    )


def _prompt_artifact_record() -> AiPromptArtifactRecord:
    return AiPromptArtifactRecord(
        id="ai-run-ops-compiled-prompt",
        tenant_id=_TENANT,
        ai_run_id="ai-run-ops",
        artifact_kind="compiled_prompt",
        artifact_ref="local-prompt-artifact://tenant-demo/ai-run-ops/ai-run-ops-compiled-prompt.fernet",
        content_hash="sha256:compiled-prompt",
        artifact_hash="sha256:encrypted-prompt",
        byte_size=320,
        encryption_key_ref="secret://aip_prompt_artifact_encryption_key@sha256:key",
        encryption_algorithm="fernet-v1",
        retention_until="2026-07-02T00:00:00Z",
        is_legal_hold=False,
        erasure_request_id=None,
        export_marking="aip-trace-restricted",
        created_at="2026-06-25T00:00:06Z",
    )
