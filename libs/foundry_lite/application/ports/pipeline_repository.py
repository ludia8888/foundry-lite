"""Application port contract for Pipeline Builder graph persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

JsonObject = dict[str, object]


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
    updated_by: str
    updated_at: str
    last_tick_at: str | None


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


@dataclass(frozen=True)
class PipelineScheduleRecord:
    schedule_id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    schedule: JsonObject
    enabled: bool
    updated_by: str
    updated_at: str


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
        self, *, transaction: TransactionContext, tenant_id: str, version_id: str, deployed_at: str
    ) -> PipelineVersionRow | None: ...

    def insert_run(self, *, transaction: TransactionContext, record: PipelineRunRecord) -> PipelineRunRow: ...

    def run_by_id(self, *, transaction: TransactionContext, tenant_id: str, run_id: str) -> PipelineRunRow | None: ...

    def update_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        transition: StatusTransition,
        output_dataset_ref: str | None,
        output_version_id: str | None,
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
        self, *, transaction: TransactionContext, tenant_id: str, limit: int
    ) -> list[PipelineScheduleRow]: ...

    def insert_test_result(
        self, *, transaction: TransactionContext, record: PipelineTestResultRecord
    ) -> PipelineTestResultRow: ...
