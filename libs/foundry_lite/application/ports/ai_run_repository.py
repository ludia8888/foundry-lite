"""AI run ledger port (AIP-lite P0c — run/event ledger, §10.2).

This is the durable audit spine for a model-assisted turn. It records the run
intent before provider execution, then stores events, model-call accounting,
context selection, tool-call decisions, citations, and usage. Raw prompts and
tool payloads are represented by refs/hashes/redacted previews, matching the
canonical security boundary in §9.7.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

AiJsonObject = Mapping[str, object]
AiLedgerRow = Mapping[str, object]


class AiRunLedgerSnapshot(TypedDict):
    """Rows attached to one tenant-scoped AI execution run."""

    run: AiLedgerRow
    events: list[AiLedgerRow]
    modelCalls: list[AiLedgerRow]
    contextItems: list[AiLedgerRow]
    toolCalls: list[AiLedgerRow]
    citations: list[AiLedgerRow]
    usage: list[AiLedgerRow]
    promptArtifacts: list[AiLedgerRow]


@dataclass(frozen=True)
class AiSessionRecord:
    id: str
    tenant_id: str
    agent_version_id: str
    actor_user_id: str
    status: str
    created_at: str
    last_activity_at: str


@dataclass(frozen=True)
class AiSessionStateVersionRecord:
    id: str
    tenant_id: str
    session_id: str
    version: int
    state_json: AiJsonObject
    state_hash: str
    created_by_run_id: str | None
    created_at: str


@dataclass(frozen=True)
class AiMessageRecord:
    id: str
    tenant_id: str
    session_id: str
    role: str
    client_message_id: str
    content_ref: str
    content_hash: str
    created_at: str


@dataclass(frozen=True)
class AiExecutionRunRecord:
    id: str
    tenant_id: str
    session_id: str
    agent_version_id: str
    actor_user_id: str
    request_id: str
    trace_id: str
    status: str
    ontology_version_id: str
    model_alias_version: str
    resolved_model_id: str
    resolved_model_revision: str
    prompt_version_id: str
    compiled_prompt_hash: str
    tool_manifest_hash: str
    context_manifest_hash: str
    state_snapshot_hash: str
    policy_snapshot_hash: str
    budget_json: AiJsonObject
    usage_json: AiJsonObject | None
    error_json: AiJsonObject | None
    started_at: str
    completed_at: str | None


@dataclass(frozen=True)
class AiExecutionEventRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    sequence: int
    event_type: str
    payload_ref: str
    payload_hash: str
    redacted_preview: str
    created_at: str


@dataclass(frozen=True)
class AiModelCallRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    attempt: int
    provider: str
    model_revision: str
    request_hash: str
    response_hash: str
    provider_request_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    status: str
    error_json: AiJsonObject | None


@dataclass(frozen=True)
class AiContextItemRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    context_id: str
    kind: str
    source_resource_type: str
    source_resource_id: str
    source_version: str
    content_hash: str
    retrieval_method: str
    relevance_score: float
    security_partition: str
    token_estimate: int
    is_selected: bool
    omission_reason: str | None


@dataclass(frozen=True)
class AiToolCallRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    sequence: int
    tool_id: str
    tool_version: str
    arguments_hash: str
    effect: str
    authorization_decision: str
    confirmation_policy: str
    status: str
    result_hash: str
    linked_action_run_id: str | None
    started_at: str
    completed_at: str | None
    error_json: AiJsonObject | None
    result_json: AiJsonObject | None = None


@dataclass(frozen=True)
class AiCitationRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    message_id: str
    context_item_id: str
    claim_span: AiJsonObject
    citation_order: int
    rendered_ref: str


@dataclass(frozen=True)
class AiUsageLedgerRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    provider: str
    model_revision: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    currency: str
    recorded_at: str


@dataclass(frozen=True)
class AiPromptArtifactRecord:
    id: str
    tenant_id: str
    ai_run_id: str
    artifact_kind: str
    artifact_ref: str
    content_hash: str
    artifact_hash: str
    byte_size: int
    encryption_key_ref: str
    encryption_algorithm: str
    retention_until: str
    is_legal_hold: bool
    erasure_request_id: str | None
    export_marking: str
    created_at: str


class AiRunRepository(Protocol):
    """DB boundary for the canonical AI runtime ledger."""

    def create_session(self, *, transaction: TransactionContext, record: AiSessionRecord) -> None:
        """Persist one chat/agent session row."""
        ...

    def create_session_state_version(
        self, *, transaction: TransactionContext, record: AiSessionStateVersionRecord
    ) -> None:
        """Persist a hash-addressed session state snapshot."""
        ...

    def insert_message_or_get_existing(
        self, *, transaction: TransactionContext, record: AiMessageRecord
    ) -> AiLedgerRow | None:
        """Persist a client-idempotent message, returning the existing row on duplicate."""
        ...

    def create_execution_run(self, *, transaction: TransactionContext, record: AiExecutionRunRecord) -> None:
        """Persist run intent before any provider call."""
        ...

    def update_execution_run_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ai_run_id: str,
        transition: StatusTransition,
        usage_json: AiJsonObject | None,
        error_json: AiJsonObject | None,
        completed_at: str | None,
    ) -> AiLedgerRow | None:
        """Update the run terminal/intermediate status and return the updated row."""
        ...

    def append_execution_event(self, *, transaction: TransactionContext, record: AiExecutionEventRecord) -> bool:
        """Append a sequence-idempotent run event."""
        ...

    def record_model_call(self, *, transaction: TransactionContext, record: AiModelCallRecord) -> None:
        """Persist one provider attempt accounting row."""
        ...

    def record_context_item(self, *, transaction: TransactionContext, record: AiContextItemRecord) -> None:
        """Persist one selected or omitted context item."""
        ...

    def record_tool_call(self, *, transaction: TransactionContext, record: AiToolCallRecord) -> None:
        """Persist one tool authorization/execution row."""
        ...

    def link_tool_call_to_action_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ai_run_id: str,
        tool_call_id: str,
        action_run_id: str,
    ) -> AiLedgerRow | None:
        """Attach the approved action run produced by a prior action.propose tool call."""
        ...

    def record_citation(self, *, transaction: TransactionContext, record: AiCitationRecord) -> None:
        """Persist one rendered answer citation."""
        ...

    def record_usage(self, *, transaction: TransactionContext, record: AiUsageLedgerRecord) -> None:
        """Persist one usage/cost ledger row."""
        ...

    def record_prompt_artifact(self, *, transaction: TransactionContext, record: AiPromptArtifactRecord) -> None:
        """Persist metadata for a separately encrypted prompt artifact."""
        ...

    def session_by_id(self, *, transaction: TransactionContext, tenant_id: str, session_id: str) -> AiLedgerRow | None:
        """Return one tenant-scoped session row."""
        ...

    def message_by_client_id(
        self, *, transaction: TransactionContext, tenant_id: str, session_id: str, client_message_id: str
    ) -> AiLedgerRow | None:
        """Return one tenant-scoped message by idempotency key."""
        ...

    def state_versions_for_session(
        self, *, transaction: TransactionContext, tenant_id: str, session_id: str
    ) -> list[AiLedgerRow]:
        """Return ordered state versions for one session."""
        ...

    def execution_run_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, ai_run_id: str
    ) -> AiLedgerRow | None:
        """Return one tenant-scoped execution run."""
        ...

    def ledger_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, ai_run_id: str
    ) -> AiRunLedgerSnapshot | None:
        """Return the run plus its events/model/context/tool/citation/usage rows."""
        ...

    def prompt_artifact_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, artifact_id: str
    ) -> AiLedgerRow | None:
        """Return one tenant-scoped encrypted prompt artifact receipt."""
        ...
