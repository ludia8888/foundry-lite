"""Persistence port for Pipeline Builder v2 execution evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.pipeline_async_execution_types import (
    PipelineNodeAttemptClaim,
    PipelineNodeAttemptRecord,
    PipelineNodeAttemptRow,
    PipelinePreviewRunRecord,
    PipelinePreviewRunRow,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
    PipelineRunEventRecord,
    PipelineRunEventRow,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.state_transitions import StatusTransition

JsonObject = dict[str, object]


class PipelineRunRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    status: str
    idempotency_key: str | None
    request_fingerprint: str | None
    plan_fingerprint: str | None
    workflow_run_id: str | None
    dispatch_status: str
    dispatch_attempt_count: int
    dispatch_error: JsonObject | None
    event_sequence: int
    execution_lease_token: str | None
    execution_lease_expires_at: str | None
    execution_heartbeat_at: str | None
    cancel_requested_at: str | None
    cancel_reason: str | None
    cancel_idempotency_key: str | None
    cancel_request_fingerprint: str | None
    schedule_id: str | None
    schedule_slot_at: str | None
    terminal_observed_at: str | None
    parameters: JsonObject | None
    target_node_ids: list[str] | None
    outputs: list[JsonObject]
    output_dataset_ref: str | None
    output_version_id: str | None
    timeline: list[JsonObject]
    error: JsonObject | None
    created_by: str
    started_at: str
    completed_at: str | None


class PipelineNodeRunRow(TypedDict):
    id: str
    tenant_id: str
    run_id: str
    node_id: str
    descriptor_id: str
    spec_version: str
    status: str
    attempt_count: int
    input_artifacts: list[JsonObject]
    output_artifacts: list[JsonObject]
    error: JsonObject | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


class PipelineDeploymentRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    deployment_number: int
    status: str
    execution_plan: JsonObject
    plan_fingerprint: str
    compiler_version: str
    processor_pins: list[JsonObject]
    model_pins: list[JsonObject]
    function_pins: list[JsonObject]
    compute_profile: JsonObject
    idempotency_key: str
    request_fingerprint: str
    promoted_by: str | None
    promoted_at: str | None
    rolled_back_from_id: str | None
    created_by: str
    created_at: str


@dataclass(frozen=True)
class PipelineNodeRunRecord:
    node_run_id: str
    tenant_id: str
    run_id: str
    node_id: str
    descriptor_id: str
    spec_version: str
    input_artifacts: list[JsonObject]
    created_at: str


@dataclass(frozen=True)
class PipelineDeploymentRecord:
    deployment_id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    status: str
    execution_plan: JsonObject
    plan_fingerprint: str
    compiler_version: str
    processor_pins: list[JsonObject]
    model_pins: list[JsonObject]
    function_pins: list[JsonObject]
    compute_profile: JsonObject
    idempotency_key: str
    request_fingerprint: str
    promoted_by: str
    promoted_at: str
    rolled_back_from_id: str | None
    created_by: str
    created_at: str


class PipelineExecutionRepository(Protocol):
    """Tenant-scoped evidence store for previews and asynchronous DAG work."""

    def insert_preview(
        self, *, transaction: TransactionContext, record: PipelinePreviewRunRecord
    ) -> PipelinePreviewRunRow: ...

    def preview_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, preview_run_id: str
    ) -> PipelinePreviewRunRow | None: ...

    def preview_by_idempotency_key(
        self, *, transaction: TransactionContext, tenant_id: str, idempotency_key: str
    ) -> PipelinePreviewRunRow | None: ...

    def update_preview_dispatch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        workflow_run_id: str,
        dispatch_status: str,
        dispatch_error: JsonObject | None,
    ) -> PipelinePreviewRunRow | None: ...

    def pending_preview_dispatches(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        limit: int,
    ) -> list[PipelinePreviewRunRow]: ...

    def update_preview_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        transition: StatusTransition,
        outputs: list[JsonObject],
        artifacts: list[JsonObject],
        error: JsonObject | None,
        completed_at: str,
        execution_lease_token: str | None,
    ) -> PipelinePreviewRunRow | None: ...

    def claim_preview(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        started_at: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelinePreviewRunRow | None: ...

    def reclaim_expired_preview(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        reclaim_before: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelinePreviewRunRow | None: ...

    def renew_preview_execution_lease(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelinePreviewRunRow | None: ...

    def recoverable_previews(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        as_of: str,
        limit: int,
    ) -> list[PipelinePreviewRunRow]: ...

    def complete_preview_success(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        execution_lease_token: str,
        outputs: list[JsonObject],
        artifacts: list[JsonObject],
        completed_at: str,
    ) -> PipelinePreviewRunRow | None: ...

    def complete_preview_failure(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        preview_run_id: str,
        execution_lease_token: str,
        error: JsonObject,
        completed_at: str,
    ) -> PipelinePreviewRunRow | None: ...

    def request_preview_cancel(
        self, *, transaction: TransactionContext, tenant_id: str, preview_run_id: str, requested_at: str
    ) -> PipelinePreviewRunRow | None: ...

    def insert_node_run(
        self, *, transaction: TransactionContext, record: PipelineNodeRunRecord
    ) -> PipelineNodeRunRow: ...

    def node_runs_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> list[PipelineNodeRunRow]: ...

    def node_run_by_run_node(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        node_id: str,
    ) -> PipelineNodeRunRow | None: ...

    def claim_node_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        node_run_id: str,
        attempt_number: int,
        input_artifacts: list[JsonObject],
        started_at: str,
        updated_at: str,
    ) -> PipelineNodeRunRow | None: ...

    def update_node_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        node_run_id: str,
        transition: StatusTransition,
        output_artifacts: list[JsonObject],
        error: JsonObject | None,
        completed_at: str,
        updated_at: str,
    ) -> PipelineNodeRunRow | None: ...

    def cancel_inactive_node_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        completed_at: str,
    ) -> int: ...

    def cancel_active_node_attempt(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        node_id: str,
        worker_id: str,
        external_execution_id: str,
        completed_at: str,
    ) -> PipelineNodeAttemptRow | None: ...

    def active_node_run_count(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
    ) -> int: ...

    def insert_node_attempt(
        self, *, transaction: TransactionContext, record: PipelineNodeAttemptRecord
    ) -> PipelineNodeAttemptRow: ...

    def attempts_for_node_run(
        self, *, transaction: TransactionContext, tenant_id: str, node_run_id: str
    ) -> list[PipelineNodeAttemptRow]: ...

    def attempt_by_number(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        node_run_id: str,
        attempt_number: int,
    ) -> PipelineNodeAttemptRow | None: ...

    def update_node_attempt_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        attempt_id: str,
        transition: StatusTransition,
        output_manifest: JsonObject,
        error: JsonObject | None,
        completed_at: str,
    ) -> PipelineNodeAttemptRow | None: ...

    def claim_node_attempt(
        self,
        *,
        transaction: TransactionContext,
        claim: PipelineNodeAttemptClaim,
    ) -> PipelineNodeAttemptRow | None: ...

    def heartbeat_node_attempt(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        lease_expires_at: str,
        heartbeat_at: str,
    ) -> PipelineNodeAttemptRow | None: ...

    def update_fenced_node_attempt_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        status: str,
        output_manifest: JsonObject,
        error: JsonObject | None,
        error_kind: str | None,
        completed_at: str,
        retry_at: str | None = None,
    ) -> PipelineNodeAttemptRow | None: ...

    def schedule_node_retry(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        node_run_id: str,
        attempt_id: str,
        fencing_token: int,
        retry_at: str,
        error_kind: str,
    ) -> bool: ...

    def insert_artifact(
        self, *, transaction: TransactionContext, record: PipelineRunArtifactRecord
    ) -> PipelineRunArtifactRow: ...

    def artifacts_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> list[PipelineRunArtifactRow]: ...

    def artifact_by_idempotency_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        idempotency_key: str,
    ) -> PipelineRunArtifactRow | None: ...

    def insert_fenced_artifact(
        self,
        *,
        transaction: TransactionContext,
        record: PipelineRunArtifactRecord,
        worker_id: str,
        lease_token: str,
    ) -> PipelineRunArtifactRow | None: ...

    def append_run_event(
        self,
        *,
        transaction: TransactionContext,
        record: PipelineRunEventRecord,
    ) -> PipelineRunEventRow: ...

    def run_events(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[PipelineRunEventRow]: ...

    def insert_deployment(
        self, *, transaction: TransactionContext, record: PipelineDeploymentRecord
    ) -> PipelineDeploymentRow: ...

    def deployment_by_idempotency_key(
        self, *, transaction: TransactionContext, tenant_id: str, idempotency_key: str
    ) -> PipelineDeploymentRow | None: ...

    def promoted_deployment_for_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        pipeline_id: str,
        version_id: str,
        plan_fingerprint: str,
    ) -> PipelineDeploymentRow | None: ...

    def list_deployments(
        self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str, limit: int
    ) -> list[PipelineDeploymentRow]: ...
