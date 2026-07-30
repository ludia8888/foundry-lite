"""Application port contract for Pipeline Builder graph persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypedDict

from foundry_lite.application.ports.pipeline_execution_repository import PipelineRunRow
from foundry_lite.application.ports.pipeline_schedule_repository import (
    PipelineScheduleOperationRecord as PipelineScheduleOperationRecord,
)
from foundry_lite.application.ports.pipeline_schedule_repository import (
    PipelineScheduleOperationRow as PipelineScheduleOperationRow,
)
from foundry_lite.application.ports.pipeline_schedule_repository import (
    PipelineScheduleRecord as PipelineScheduleRecord,
)
from foundry_lite.application.ports.pipeline_schedule_repository import (
    PipelineScheduleRepository,
)
from foundry_lite.application.ports.pipeline_schedule_repository import (
    PipelineScheduleRow as PipelineScheduleRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

JsonObject = dict[str, object]


class PipelineExecutionLeaseFence(Protocol):
    """Prove that the current Pipeline executor still owns its durable lease."""

    def require_active(self, transaction: TransactionContext | None = None) -> None: ...


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
    schedule_id: str | None = None
    schedule_slot_at: str | None = None


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


class PipelineRepository(PipelineScheduleRepository, Protocol):
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

    def list_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        pipeline_id: str,
        after_started_at: str | None,
        after_run_id: str | None,
        limit: int,
    ) -> list[PipelineRunRow]: ...

    def update_run_dispatch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        workflow_run_id: str,
        dispatch_status: str,
        dispatch_error: JsonObject | None,
    ) -> PipelineRunRow | None: ...

    def pending_dispatch_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        limit: int,
    ) -> list[PipelineRunRow]: ...

    def unobserved_terminal_schedule_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        limit: int,
    ) -> list[PipelineRunRow]: ...

    def cancelling_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        limit: int,
    ) -> list[PipelineRunRow]: ...

    def stale_execution_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        now: str,
        limit: int,
    ) -> list[PipelineRunRow]: ...

    def claim_terminal_schedule_observation(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        observed_at: str,
    ) -> PipelineRunRow | None: ...

    def request_run_cancel(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        requested_at: str,
        reason: str | None,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PipelineRunRow | None: ...

    def claim_run_execution(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        timeline: list[JsonObject],
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelineRunRow | None: ...

    def renew_run_execution_lease(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelineRunRow | None: ...

    def expire_run_execution(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        execution_lease_token: str,
        expired_at: str,
        transition: StatusTransition,
        output_dataset_ref: str | None,
        output_version_id: str | None,
        outputs: list[JsonObject],
        timeline: list[JsonObject],
        error: JsonObject,
        completed_at: str,
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
        expected_completed_at: str | None = None,
    ) -> PipelineRunRow | None: ...

    def insert_test_result(
        self, *, transaction: TransactionContext, record: PipelineTestResultRecord
    ) -> PipelineTestResultRow: ...
