"""Contract for ``AiRunRepository`` (AIP-lite P0c run/event ledger, §10.2).

The ledger is the durable execution diary for an AI turn: intent before the
provider call, event sequence, model accounting, selected context, tool
decisions, citations, and usage. It runs on SQLite and PostgreSQL so the schema
and repository behavior match the production database shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from foundry_lite.application.ports import (
    AiCitationRecord,
    AiContextItemRecord,
    AiExecutionEventRecord,
    AiExecutionRunRecord,
    AiMessageRecord,
    AiModelCallRecord,
    AiSessionRecord,
    AiSessionStateVersionRecord,
    AiToolCallRecord,
    AiUsageLedgerRecord,
)
from foundry_lite.application.ports.ai_run_repository import AiPromptArtifactRecord
from foundry_lite.application.ports.transaction_context import AI_RUN_SUCCEEDED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from sqlalchemy import UniqueConstraint, create_engine
from sqlalchemy.engine import Engine

from tests.contracts.conftest import PostgresFixture, skip_if_no_postgres

_TENANT = "tenant-demo"
_AI_RUN_LEDGER_MIGRATION_TABLES = (
    "ai_sessions",
    "ai_session_state_versions",
    "ai_messages",
    "ai_execution_runs",
    "ai_execution_events",
    "ai_model_calls",
    "ai_context_items",
    "ai_tool_calls",
    "ai_citations",
    "ai_usage_ledger",
)


@contextmanager
def _txn(engine: Engine) -> Iterator[Any]:
    with engine.begin() as conn:
        yield conn


def test_ai_run_ledger_tables_match_canonical_section_10_2() -> None:
    assert _columns(db.ai_sessions) == [
        "id",
        "tenant_id",
        "agent_version_id",
        "actor_user_id",
        "status",
        "created_at",
        "last_activity_at",
    ]
    assert _columns(db.ai_execution_runs) == [
        "id",
        "tenant_id",
        "session_id",
        "agent_version_id",
        "actor_user_id",
        "request_id",
        "trace_id",
        "status",
        "ontology_version_id",
        "model_alias_version",
        "resolved_model_id",
        "resolved_model_revision",
        "prompt_version_id",
        "compiled_prompt_hash",
        "tool_manifest_hash",
        "context_manifest_hash",
        "state_snapshot_hash",
        "policy_snapshot_hash",
        "budget_json",
        "usage_json",
        "error_json",
        "started_at",
        "completed_at",
    ]
    assert _unique_constraints(db.ai_messages) == {
        "uq_ai_message_client_id": ("tenant_id", "session_id", "client_message_id")
    }
    assert _unique_constraints(db.ai_execution_events) == {"uq_ai_execution_event_sequence": ("ai_run_id", "sequence")}
    assert _columns(db.ai_usage_ledger) == [
        "id",
        "tenant_id",
        "ai_run_id",
        "provider",
        "model_revision",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "currency",
        "recorded_at",
    ]
    assert _columns(db.ai_prompt_artifacts) == [
        "id",
        "tenant_id",
        "ai_run_id",
        "artifact_kind",
        "artifact_ref",
        "content_hash",
        "artifact_hash",
        "byte_size",
        "encryption_key_ref",
        "encryption_algorithm",
        "retention_until",
        "legal_hold",
        "erasure_request_id",
        "export_marking",
        "created_at",
    ]


def test_ai_run_ledger_migration_applies_postgres_rls_to_tenant_tables() -> None:
    migration_path = Path(__file__).resolve().parents[2] / "migrations/versions/f7a9c1e3b5d8_aip_ai_run_ledger.py"
    migration = migration_path.read_text(encoding="utf-8")

    assert 'context.get_context().dialect.name == "postgresql"' in migration
    assert "current_setting('foundry_lite.tenant_id', true)" in migration
    for table in _AI_RUN_LEDGER_MIGRATION_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in migration
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in migration
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in migration


def test_prompt_artifact_migration_applies_postgres_rls_to_tenant_table() -> None:
    migration_path = Path(__file__).resolve().parents[2] / "migrations/versions/f9b1c3d5e7a9_aip_prompt_artifacts.py"
    migration = migration_path.read_text(encoding="utf-8")

    assert 'context.get_context().dialect.name == "postgresql"' in migration
    assert "ALTER TABLE ai_prompt_artifacts ENABLE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE ai_prompt_artifacts FORCE ROW LEVEL SECURITY" in migration
    assert "CREATE POLICY ai_prompt_artifacts_tenant_isolation ON ai_prompt_artifacts" in migration


def _assert_round_trip(engine: Engine) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with _txn(engine) as transaction:
        duplicate_message, updated_run = _seed_ledger(repository, transaction)
        session = repository.session_by_id(transaction=transaction, tenant_id=_TENANT, session_id="ai-session-1")
        message = repository.message_by_client_id(
            transaction=transaction,
            tenant_id=_TENANT,
            session_id="ai-session-1",
            client_message_id="client-msg-1",
        )
        state_versions = repository.state_versions_for_session(
            transaction=transaction, tenant_id=_TENANT, session_id="ai-session-1"
        )
        ledger = repository.ledger_for_run(transaction=transaction, tenant_id=_TENANT, ai_run_id="ai-run-1")
        other_tenant = repository.ledger_for_run(transaction=transaction, tenant_id="tenant-test", ai_run_id="ai-run-1")

    assert duplicate_message is not None
    assert duplicate_message["id"] == "ai-message-1"
    assert updated_run is not None
    assert updated_run["status"] == "succeeded"
    assert session is not None and session["last_activity_at"] == "2026-06-25T00:00:03Z"
    assert message is not None and message["content_hash"] == "sha256:user-message"
    assert state_versions[0]["state_json"] == {"memory": "short"}
    assert ledger is not None
    _assert_ledger_rows(ledger)
    assert other_tenant is None


def _seed_ledger(repository: SqlAlchemyAiRunRepository, transaction: Any) -> tuple[Any, Any]:
    repository.create_session(transaction=transaction, record=_session_record())
    repository.create_execution_run(transaction=transaction, record=_run_record())
    repository.create_session_state_version(transaction=transaction, record=_state_version_record())
    duplicate = _insert_idempotent_message(repository, transaction)
    assert repository.append_execution_event(transaction=transaction, record=_event_record("event-1", 1, "run_intent"))
    assert not repository.append_execution_event(
        transaction=transaction, record=_event_record("event-dupe", 1, "run_intent")
    )
    assert repository.append_execution_event(
        transaction=transaction, record=_event_record("event-2", 2, "model_response")
    )
    repository.record_model_call(transaction=transaction, record=_model_call_record())
    repository.record_context_item(transaction=transaction, record=_context_item_record())
    repository.record_tool_call(transaction=transaction, record=_tool_call_record())
    repository.record_citation(transaction=transaction, record=_citation_record())
    repository.record_usage(transaction=transaction, record=_usage_record())
    repository.record_prompt_artifact(transaction=transaction, record=_prompt_artifact_record())
    updated = repository.update_execution_run_status(
        transaction=transaction,
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
        transition=AI_RUN_SUCCEEDED,
        usage_json={"inputTokens": 128, "outputTokens": 64},
        error_json=None,
        completed_at="2026-06-25T00:00:05Z",
    )
    return duplicate, updated


def _assert_ledger_rows(ledger: Any) -> None:
    assert ledger["run"]["trace_id"] == "trace-1"
    assert ledger["run"]["budget_json"] == {"maxTokens": 512, "maxCostUsd": 0.25}
    assert [row["sequence"] for row in ledger["events"]] == [1, 2]
    assert ledger["events"][0]["payload_ref"] == "blob://ai-runs/ai-run-1/run_intent.json"
    assert ledger["modelCalls"][0]["request_hash"] == "sha256:model-request"
    assert ledger["contextItems"][0]["security_partition"] == "tenant-demo:internal"
    assert ledger["toolCalls"][0]["authorization_decision"] == "allowed"
    assert ledger["citations"][0]["claim_span"] == {"start": 0, "end": 18}
    assert ledger["usage"][0]["estimated_cost"] == 0.0123
    assert ledger["promptArtifacts"][0]["artifact_kind"] == "compiled_prompt"
    assert ledger["promptArtifacts"][0]["content_hash"] == "sha256:compiled-prompt"


def _insert_idempotent_message(repository: SqlAlchemyAiRunRepository, transaction: Any) -> Any:
    first = repository.insert_message_or_get_existing(transaction=transaction, record=_message_record("ai-message-1"))
    duplicate = repository.insert_message_or_get_existing(
        transaction=transaction, record=_message_record("ai-message-2")
    )
    assert first is None
    return duplicate


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-1",
        tenant_id=_TENANT,
        agent_version_id="agent-v1",
        actor_user_id="user-1",
        status="active",
        created_at="2026-06-25T00:00:00Z",
        last_activity_at="2026-06-25T00:00:03Z",
    )


def _state_version_record() -> AiSessionStateVersionRecord:
    return AiSessionStateVersionRecord(
        id="state-v1",
        tenant_id=_TENANT,
        session_id="ai-session-1",
        version=1,
        state_json={"memory": "short"},
        state_hash="sha256:state",
        created_by_run_id="ai-run-1",
        created_at="2026-06-25T00:00:01Z",
    )


def _message_record(message_id: str) -> AiMessageRecord:
    return AiMessageRecord(
        id=message_id,
        tenant_id=_TENANT,
        session_id="ai-session-1",
        role="user",
        client_message_id="client-msg-1",
        content_ref="blob://ai-sessions/ai-session-1/messages/client-msg-1.txt",
        content_hash="sha256:user-message",
        created_at="2026-06-25T00:00:02Z",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-1",
        tenant_id=_TENANT,
        session_id="ai-session-1",
        agent_version_id="agent-v1",
        actor_user_id="user-1",
        request_id="req-1",
        trace_id="trace-1",
        status="started",
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
        budget_json={"maxTokens": 512, "maxCostUsd": 0.25},
        usage_json=None,
        error_json=None,
        started_at="2026-06-25T00:00:03Z",
        completed_at=None,
    )


def _event_record(event_id: str, sequence: int, event_type: str) -> AiExecutionEventRecord:
    return AiExecutionEventRecord(
        id=event_id,
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
        sequence=sequence,
        event_type=event_type,
        payload_ref=f"blob://ai-runs/ai-run-1/{event_type}.json",
        payload_hash=f"sha256:{event_type}",
        redacted_preview=f"{event_type} preview",
        created_at=f"2026-06-25T00:00:0{sequence}Z",
    )


def _model_call_record() -> AiModelCallRecord:
    return AiModelCallRecord(
        id="model-call-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
        attempt=1,
        provider="acme",
        model_revision="2026-06-01",
        request_hash="sha256:model-request",
        response_hash="sha256:model-response",
        provider_request_id="provider-req-1",
        input_tokens=128,
        output_tokens=64,
        latency_ms=240,
        status="succeeded",
        error_json=None,
    )


def _context_item_record() -> AiContextItemRecord:
    return AiContextItemRecord(
        id="context-item-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
        context_id="ctx-1",
        kind="object",
        source_resource_type="object",
        source_resource_id="Order:O-1",
        source_version="object-version-1",
        content_hash="sha256:context-item",
        retrieval_method="semantic",
        relevance_score=0.91,
        security_partition="tenant-demo:internal",
        token_estimate=42,
        is_selected=True,
        omission_reason=None,
    )


def _tool_call_record() -> AiToolCallRecord:
    return AiToolCallRecord(
        id="tool-call-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
        sequence=1,
        tool_id="create-action-proposal",
        tool_version="v1",
        arguments_hash="sha256:tool-args",
        effect="propose",
        authorization_decision="allowed",
        confirmation_policy="human_required",
        status="succeeded",
        result_hash="sha256:tool-result",
        linked_action_run_id="action-run-1",
        started_at="2026-06-25T00:00:04Z",
        completed_at="2026-06-25T00:00:05Z",
        error_json=None,
    )


def _citation_record() -> AiCitationRecord:
    return AiCitationRecord(
        id="citation-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
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
        ai_run_id="ai-run-1",
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
        id="prompt-artifact-1",
        tenant_id=_TENANT,
        ai_run_id="ai-run-1",
        artifact_kind="compiled_prompt",
        artifact_ref="local-prompt-artifact://tenant-demo/ai-run-1/prompt-artifact-1.fernet",
        content_hash="sha256:compiled-prompt",
        artifact_hash="sha256:encrypted-prompt",
        byte_size=256,
        encryption_key_ref="secret://aip_prompt_artifact_encryption_key@sha256:key",
        encryption_algorithm="fernet-v1",
        retention_until="2026-07-02T00:00:00Z",
        is_legal_hold=False,
        erasure_request_id=None,
        export_marking="aip-trace-restricted",
        created_at="2026-06-25T00:00:05Z",
    )


def _sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine


def _columns(table: Any) -> list[str]:
    return [column.name for column in table.columns]


def _unique_constraints(table: Any) -> dict[str, tuple[str, ...]]:
    constraints: dict[str, tuple[str, ...]] = {}
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.name is not None:
            constraints[str(constraint.name)] = tuple(column.name for column in constraint.columns)
    return constraints


def test_ai_run_repository_round_trip_sqlite() -> None:
    _assert_round_trip(_sqlite_engine())


@skip_if_no_postgres
def test_ai_run_repository_round_trip_postgres(postgres_fixture: PostgresFixture) -> None:
    _assert_round_trip(postgres_fixture.engine)
