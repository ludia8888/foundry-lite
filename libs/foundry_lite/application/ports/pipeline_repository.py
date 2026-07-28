"""Application port contract for Pipeline Builder graph persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

JsonObject = dict[str, object]


def _empty_json_object_list() -> list[JsonObject]:
    return []


class PipelineBranchRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    name: str
    status: str
    base_version_id: str | None
    base_graph: JsonObject
    graph: JsonObject
    graph_fingerprint: str
    graph_schema_version: int
    created_by: str
    created_at: str
    updated_at: str
    rebased_at: str | None
    proposal_id: str | None
    merged_version_id: str | None


class PipelineProposalRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    branch_id: str
    title: str
    description: str | None
    status: str
    graph: JsonObject
    graph_fingerprint: str
    graph_schema_version: int
    assigned_to: str | None
    decision: str | None
    decision_comment: str | None
    decided_at: str | None
    created_by: str
    created_at: str
    updated_at: str


class PipelineVersionRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    version_number: int
    graph: JsonObject
    graph_fingerprint: str
    graph_schema_version: int
    execution_plan: JsonObject | None
    plan_fingerprint: str | None
    compiler_version: str | None
    proposal_id: str
    created_by: str
    created_at: str
    deployed_at: str | None


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


class PipelineScheduleRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    schedule: JsonObject
    enabled: bool
    status: str
    updated_by: str
    updated_at: str
    last_tick_at: str | None
    last_slot_at: str | None
    trigger_type: str | None
    timezone: str | None
    next_due_at: str | None
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    fencing_token: int
    failure_count: int
    paused_reason: str | None
    last_failure_at: str | None
    last_error: JsonObject | None


class PipelineScheduleOperationRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    result: JsonObject | None
    created_by: str
    created_at: str


class PipelineTestResultRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    branch_id: str
    status: str
    result: JsonObject
    created_by: str
    created_at: str


@dataclass(frozen=True)
class PipelineBranchRecord:
    branch_id: str
    tenant_id: str
    pipeline_id: str
    name: str
    base_version_id: str | None
    base_graph: JsonObject
    graph: JsonObject
    graph_fingerprint: str
    created_by: str
    created_at: str
    updated_at: str
    graph_schema_version: int = 1


@dataclass(frozen=True)
class PipelineProposalRecord:
    proposal_id: str
    tenant_id: str
    pipeline_id: str
    branch_id: str
    title: str
    description: str | None
    graph: JsonObject
    graph_fingerprint: str
    created_by: str
    created_at: str
    graph_schema_version: int = 1


@dataclass(frozen=True)
class PipelineVersionRecord:
    version_id: str
    tenant_id: str
    pipeline_id: str
    version_number: int
    graph: JsonObject
    graph_fingerprint: str
    proposal_id: str
    created_by: str
    created_at: str
    graph_schema_version: int = 1


@dataclass(frozen=True)
class PipelineRunRecord:
    run_id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    status: str
    output_dataset_ref: str | None
    output_version_id: str | None
    timeline: list[JsonObject]
    error: JsonObject | None
    created_by: str
    started_at: str
    completed_at: str | None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    plan_fingerprint: str | None = None
    workflow_run_id: str | None = None
    parameters: JsonObject | None = None
    target_node_ids: list[str] | None = None
    outputs: list[JsonObject] = field(default_factory=_empty_json_object_list)


@dataclass(frozen=True)
class PipelineScheduleRecord:
    schedule_id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    schedule: JsonObject
    enabled: bool
    status: str
    trigger_type: str
    timezone: str
    next_due_at: str | None
    updated_by: str
    updated_at: str
    paused_reason: str | None = None


@dataclass(frozen=True)
class PipelineScheduleOperationRecord:
    operation_id: str
    tenant_id: str
    pipeline_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class PipelineTestResultRecord:
    result_id: str
    tenant_id: str
    pipeline_id: str
    branch_id: str
    status: str
    result: JsonObject
    created_by: str
    created_at: str


class PipelineRepository(Protocol):
    """DB boundary for tenant-scoped Pipeline Builder resources."""

    def insert_branch_if_name_free(
        self, *, transaction: TransactionContext, record: PipelineBranchRecord
    ) -> PipelineBranchRow | None: ...

    def branch_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, branch_id: str
    ) -> PipelineBranchRow | None: ...

    def list_branches(
        self, *, transaction: TransactionContext, tenant_id: str, status: str | None, limit: int
    ) -> list[PipelineBranchRow]: ...

    def latest_version(
        self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str
    ) -> PipelineVersionRow | None: ...

    def update_branch_graph(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        graph: JsonObject,
        graph_fingerprint: str,
        updated_at: str,
        graph_schema_version: int = 1,
    ) -> PipelineBranchRow | None: ...

    def rebase_branch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        base_version_id: str | None,
        base_graph: JsonObject,
        graph: JsonObject,
        graph_fingerprint: str,
        rebased_at: str,
        updated_at: str,
        graph_schema_version: int = 1,
    ) -> PipelineBranchRow | None: ...

    def set_branch_proposal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        proposal_id: str,
        updated_at: str,
    ) -> PipelineBranchRow | None: ...

    def close_branch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        status: str,
        merged_version_id: str | None,
        updated_at: str,
    ) -> PipelineBranchRow | None: ...

    def insert_proposal(
        self, *, transaction: TransactionContext, record: PipelineProposalRecord
    ) -> PipelineProposalRow: ...

    def proposal_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, proposal_id: str
    ) -> PipelineProposalRow | None: ...

    def list_proposals(
        self, *, transaction: TransactionContext, tenant_id: str, status: str | None, limit: int
    ) -> list[PipelineProposalRow]: ...

    def update_proposal_assignment(
        self, *, transaction: TransactionContext, tenant_id: str, proposal_id: str, assigned_to: str, updated_at: str
    ) -> PipelineProposalRow | None: ...

    def update_proposal_decision(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        proposal_id: str,
        status: str,
        decision: str,
        comment: str | None,
        decided_at: str,
        updated_at: str,
    ) -> PipelineProposalRow | None: ...

    def withdraw_proposal(
        self, *, transaction: TransactionContext, tenant_id: str, proposal_id: str, updated_at: str
    ) -> PipelineProposalRow | None: ...

    def mark_proposal_executed(
        self, *, transaction: TransactionContext, tenant_id: str, proposal_id: str, updated_at: str
    ) -> PipelineProposalRow | None: ...

    def insert_version(
        self, *, transaction: TransactionContext, record: PipelineVersionRecord
    ) -> PipelineVersionRow: ...

    def version_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, version_id: str
    ) -> PipelineVersionRow | None: ...

    def list_versions(
        self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str, limit: int
    ) -> list[PipelineVersionRow]: ...

    def mark_version_deployed(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        version_id: str,
        execution_plan: JsonObject,
        plan_fingerprint: str,
        compiler_version: str,
        deployed_at: str,
    ) -> PipelineVersionRow | None: ...

    def insert_run(self, *, transaction: TransactionContext, record: PipelineRunRecord) -> PipelineRunRow: ...

    def run_by_id(self, *, transaction: TransactionContext, tenant_id: str, run_id: str) -> PipelineRunRow | None: ...

    def run_by_idempotency_key(
        self, *, transaction: TransactionContext, tenant_id: str, idempotency_key: str
    ) -> PipelineRunRow | None: ...

    def claim_run_execution(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        timeline: list[JsonObject],
    ) -> PipelineRunRow | None: ...

    def update_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        transition: StatusTransition,
        output_dataset_ref: str | None,
        output_version_id: str | None,
        outputs: list[JsonObject] | None = None,
        timeline: list[JsonObject],
        error: JsonObject | None,
        completed_at: str,
    ) -> PipelineRunRow | None: ...

    def upsert_schedule(
        self, *, transaction: TransactionContext, record: PipelineScheduleRecord
    ) -> PipelineScheduleRow: ...

    def schedule_by_pipeline(
        self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str
    ) -> PipelineScheduleRow | None: ...

    def delete_schedule(self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str) -> bool: ...

    def list_due_schedules(
        self, *, transaction: TransactionContext, tenant_id: str, due_at: str, limit: int
    ) -> list[PipelineScheduleRow]: ...

    def claim_due_schedule(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        schedule_id: str,
        due_at: str,
        lease_owner: str,
        lease_token: str,
        lease_expires_at: str,
    ) -> PipelineScheduleRow | None: ...

    def complete_schedule_tick(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        schedule_id: str,
        lease_token: str,
        fencing_token: int,
        values: JsonObject,
    ) -> PipelineScheduleRow | None: ...

    def update_schedule_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        pipeline_id: str,
        status: str,
        enabled: bool,
        next_due_at: str | None,
        paused_reason: str | None,
        updated_by: str,
        updated_at: str,
    ) -> PipelineScheduleRow | None: ...

    def reserve_schedule_operation(
        self,
        *,
        transaction: TransactionContext,
        record: PipelineScheduleOperationRecord,
    ) -> tuple[PipelineScheduleOperationRow, bool]: ...

    def complete_schedule_operation(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        operation_id: str,
        result: JsonObject,
    ) -> PipelineScheduleOperationRow | None: ...

    def insert_test_result(
        self, *, transaction: TransactionContext, record: PipelineTestResultRecord
    ) -> PipelineTestResultRow: ...
