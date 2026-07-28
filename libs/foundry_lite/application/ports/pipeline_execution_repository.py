"""Persistence port for Pipeline Builder v2 execution evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

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
    execution_lease_token: str | None
    execution_lease_expires_at: str | None
    execution_heartbeat_at: str | None
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


class PipelinePreviewRunRow(TypedDict):
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


class PipelineNodeAttemptRow(TypedDict):
    id: str
    tenant_id: str
    node_run_id: str
    attempt_number: int
    status: str
    executor_profile: str
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    input_manifest: JsonObject
    output_manifest: JsonObject
    error: JsonObject | None
    started_at: str
    completed_at: str | None


class PipelineRunArtifactRow(TypedDict):
    id: str
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
class PipelinePreviewRunRecord:
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
class PipelineNodeAttemptRecord:
    attempt_id: str
    tenant_id: str
    node_run_id: str
    attempt_number: int
    executor_profile: str
    input_manifest: JsonObject
    started_at: str


@dataclass(frozen=True)
class PipelineRunArtifactRecord:
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
