"""Typed commands and rows for durable, fenced Action execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

JsonObject = dict[str, object]


class ActionAsyncRunRow(TypedDict):
    """Persisted source-of-truth row for one durable Action execution."""

    id: str
    tenant_id: str
    action_type_id: str
    action_type_api_name: str
    actor_user_id: str
    target_object_type_id: str
    target_object_type_api_name: str
    target_object_id: str
    expected_object_version: int
    parameters: JsonObject
    status: str
    idempotency_key: str
    request_fingerprint: str
    result: JsonObject | None
    error: JsonObject | None
    external_writeback_uri: str | None
    definition_version: str | None
    plan_hash: str | None
    execution_plan: JsonObject | None
    execution_mode: str
    workflow_run_id: str | None
    dispatch_status: str
    dispatch_attempt_count: int
    dispatch_error: JsonObject | None
    event_sequence: int
    cancel_requested_at: str | None
    cancel_reason: str | None
    cancel_idempotency_key: str | None
    cancel_request_fingerprint: str | None
    started_at: str | None
    updated_at: str | None
    created_at: str
    completed_at: str | None


class ActionRunStepRow(TypedDict):
    """Persisted logical step within an Action execution."""

    id: str
    tenant_id: str
    run_id: str
    step_key: str
    step_kind: str
    status: str
    attempt_count: int
    input_manifest: JsonObject
    output_manifest: JsonObject
    error: JsonObject | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


class ActionStepAttemptRow(TypedDict):
    """Leased and fenced worker attempt for one Action step."""

    id: str
    tenant_id: str
    step_id: str
    attempt_number: int
    status: str
    worker_id: str
    lease_token: str
    lease_expires_at: str
    fencing_token: int
    heartbeat_at: str
    retry_at: str | None
    error_kind: str | None
    external_execution_id: str | None
    input_manifest: JsonObject
    output_manifest: JsonObject
    error: JsonObject | None
    started_at: str
    completed_at: str | None


class ActionRunEventRow(TypedDict):
    """Append-only sequenced lifecycle event for an Action run."""

    id: str
    tenant_id: str
    run_id: str
    sequence: int
    event_type: str
    step_key: str | None
    attempt_number: int | None
    worker_id: str | None
    fencing_token: int | None
    payload: JsonObject
    created_at: str


class ActionEffectReceiptRow(TypedDict):
    """Durable delivery and reconciliation evidence for one side effect."""

    id: str
    tenant_id: str
    action_run_id: str
    effect_id: str
    phase: str
    effect_kind: str
    target_ref: str
    status: str
    idempotency_key: str
    attempt_count: int
    max_attempts: int
    worker_id: str | None
    lease_token: str | None
    lease_expires_at: str | None
    fencing_token: int
    heartbeat_at: str | None
    dispatch_started_at: str | None
    cancel_requested_at: str | None
    cancel_reason: str | None
    request: JsonObject
    response: JsonObject | None
    error: JsonObject | None
    retry_at: str | None
    external_execution_id: str | None
    outbox_event_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    reconciled_at: str | None
    reconciled_by_user_id: str | None
    reconciliation: JsonObject | None


class ActionEffectOperationRow(TypedDict):
    """Durable idempotency and operator-decision evidence for one effect mutation."""

    id: str
    tenant_id: str
    actor_user_id: str
    receipt_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    response_json: JsonObject
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionAsyncRunRecord:
    """Immutable creation payload for a queued durable Action run."""

    run_id: str
    tenant_id: str
    action_type_id: str
    action_api_name: str
    actor_user_id: str
    target_object_type_id: str
    target_object_type: str
    target_object_id: str
    expected_object_version: int
    parameters: JsonObject
    idempotency_key: str
    request_fingerprint: str
    definition_version: str
    plan_hash: str
    execution_plan: JsonObject
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionRunStepRecord:
    """Immutable creation payload for a durable Action step."""

    step_id: str
    tenant_id: str
    run_id: str
    step_key: str
    step_kind: str
    input_manifest: JsonObject
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionStepAttemptClaim:
    """Worker lease request for a fenced Action step attempt."""

    tenant_id: str
    run_id: str
    step_key: str
    worker_id: str
    lease_token: str
    lease_expires_at: str
    claimed_at: str
    input_manifest: JsonObject
    is_cancellation: bool = False


@dataclass(frozen=True, slots=True)
class ActionRunEventRecord:
    """Append request for one durable Action run event."""

    event_id: str
    tenant_id: str
    run_id: str
    event_type: str
    payload: JsonObject
    created_at: str
    step_key: str | None = None
    attempt_number: int | None = None
    worker_id: str | None = None
    fencing_token: int | None = None


@dataclass(frozen=True, slots=True)
class ActionEffectReceiptRecord:
    """Creation payload for a governed Action effect receipt."""

    receipt_id: str
    tenant_id: str
    action_run_id: str
    effect_id: str
    phase: str
    effect_kind: str
    target_ref: str
    idempotency_key: str
    max_attempts: int
    request: JsonObject
    created_at: str
    outbox_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActionEffectClaim:
    """Worker lease request for effect delivery or reconciliation."""

    tenant_id: str
    receipt_id: str
    worker_id: str
    lease_token: str
    lease_expires_at: str
    claimed_at: str
    is_reconciliation: bool = False


@dataclass(frozen=True, slots=True)
class ActionEffectOperationRecord:
    """Immutable operator mutation evidence used for exact request replay."""

    operation_id: str
    tenant_id: str
    actor_user_id: str
    receipt_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    response_json: JsonObject
    created_at: str
