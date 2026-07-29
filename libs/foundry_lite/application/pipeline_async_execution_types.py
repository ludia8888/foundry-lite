"""Shared persistence rows and commands for fenced pipeline execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

JsonObject = dict[str, object]


class PipelinePreviewRunRow(TypedDict):
    """Durable no-commit preview execution row."""

    id: str
    tenant_id: str
    pipeline_id: str
    branch_id: str
    status: str
    graph: JsonObject
    graph_fingerprint: str
    target_node_id: str | None
    limits: JsonObject
    outputs: list[JsonObject]
    artifacts: list[JsonObject]
    idempotency_key: str
    request_fingerprint: str
    is_commit_forbidden: bool
    workflow_run_id: str | None
    dispatch_status: str
    dispatch_attempt_count: int
    dispatch_error: JsonObject | None
    execution_context: JsonObject
    execution_lease_token: str | None
    execution_lease_expires_at: str | None
    execution_heartbeat_at: str | None
    cancel_requested_at: str | None
    error: JsonObject | None
    created_by: str
    created_at: str
    started_at: str | None
    completed_at: str | None


class PipelineNodeAttemptRow(TypedDict):
    """One fenced worker attempt for a Pipeline node run."""

    id: str
    tenant_id: str
    node_run_id: str
    attempt_number: int
    status: str
    executor_profile: str
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    fencing_token: int
    heartbeat_at: str | None
    retry_at: str | None
    error_kind: str | None
    external_execution_id: str | None
    input_manifest: JsonObject
    output_manifest: JsonObject
    error: JsonObject | None
    started_at: str
    completed_at: str | None


class PipelineRunArtifactRow(TypedDict):
    """Immutable Pipeline output artifact evidence row."""

    id: str
    tenant_id: str
    run_id: str
    node_run_id: str | None
    attempt_id: str | None
    fencing_token: int | None
    node_id: str
    port_id: str
    artifact_kind: str
    plane: str
    artifact_ref: JsonObject
    manifest: JsonObject
    content_fingerprint: str
    security_envelope: JsonObject
    status: str
    is_serving: bool
    idempotency_key: str
    committed_at: str | None
    created_at: str


class PipelineRunEventRow(TypedDict):
    """One append-only, per-run sequenced execution event."""

    id: str
    tenant_id: str
    run_id: str
    sequence: int
    event_type: str
    node_id: str | None
    attempt_number: int | None
    worker_id: str | None
    fencing_token: int | None
    payload: JsonObject
    created_at: str


@dataclass(frozen=True)
class PipelinePreviewRunRecord:
    """Command used to create an immutable preview run."""

    preview_run_id: str
    tenant_id: str
    pipeline_id: str
    branch_id: str
    graph: JsonObject
    graph_fingerprint: str
    target_node_id: str | None
    limits: JsonObject
    idempotency_key: str
    request_fingerprint: str
    execution_context: JsonObject
    created_by: str
    created_at: str


@dataclass(frozen=True)
class PipelineNodeAttemptRecord:
    """Command used to insert a non-leased attempt record."""

    attempt_id: str
    tenant_id: str
    node_run_id: str
    attempt_number: int
    executor_profile: str
    input_manifest: JsonObject
    started_at: str


@dataclass(frozen=True)
class PipelineNodeAttemptClaim:
    """Lease and fencing coordinates used to claim node execution."""

    tenant_id: str
    node_run_id: str
    worker_id: str
    lease_token: str
    lease_expires_at: str
    heartbeat_at: str
    executor_profile: str
    input_manifest: JsonObject
    external_execution_id: str | None = None


@dataclass(frozen=True)
class PipelineRunEventRecord:
    """Command used to append a durable run event."""

    event_id: str
    tenant_id: str
    run_id: str
    event_type: str
    payload: JsonObject
    created_at: str
    node_id: str | None = None
    attempt_number: int | None = None
    worker_id: str | None = None
    fencing_token: int | None = None


@dataclass(frozen=True)
class PipelineRunArtifactRecord:
    """Command used to persist fenced output artifact evidence."""

    artifact_id: str
    tenant_id: str
    run_id: str
    node_run_id: str | None
    node_id: str
    port_id: str
    artifact_kind: str
    plane: str
    artifact_ref: JsonObject
    manifest: JsonObject
    content_fingerprint: str
    security_envelope: JsonObject
    status: str
    is_serving: bool
    idempotency_key: str
    committed_at: str | None
    created_at: str
    attempt_id: str | None = None
    fencing_token: int | None = None
