from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRecord,
    PipelineProposalRecord,
    PipelineRunRecord,
    PipelineScheduleRecord,
    PipelineTestResultRecord,
    PipelineVersionRecord,
)
from foundry_lite.application.state_transitions import PIPELINE_RUN_SUCCEEDED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_repository import SqlAlchemyPipelineRepository
from sqlalchemy import create_engine


def test_pipeline_repository_contract_branch_cas_and_tenant_scope(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_contract.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)

    with engine.begin() as transaction:
        created = repository.insert_branch_if_name_free(transaction=transaction, record=_branch_record())
        duplicate = repository.insert_branch_if_name_free(transaction=transaction, record=_branch_record("branch_dup"))
        other_tenant = repository.insert_branch_if_name_free(
            transaction=transaction,
            record=_branch_record("branch_other", tenant_id="tenant-other"),
        )
        stale_update = repository.update_branch_graph(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            expected_fingerprint="wrong",
            graph=_graph("updated"),
            graph_fingerprint="fp-updated",
            updated_at="2026-07-05T00:00:01Z",
        )
        updated = repository.update_branch_graph(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            expected_fingerprint="fp-initial",
            graph=_graph("updated"),
            graph_fingerprint="fp-updated",
            updated_at="2026-07-05T00:00:02Z",
        )
        hidden = repository.branch_by_id(transaction=transaction, tenant_id="tenant-other", branch_id="branch_a")

    assert created is not None
    assert duplicate is None
    assert other_tenant is not None
    assert stale_update is None
    assert updated is not None
    assert updated["graph_fingerprint"] == "fp-updated"
    assert hidden is None


def test_pipeline_repository_contract_governance_run_schedule_and_tests(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_flow_contract.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)

    with engine.begin() as transaction:
        repository.insert_branch_if_name_free(transaction=transaction, record=_branch_record())
        proposal = repository.insert_proposal(transaction=transaction, record=_proposal_record())
        assigned = repository.update_proposal_assignment(
            transaction=transaction,
            tenant_id="tenant-a",
            proposal_id=proposal["id"],
            assigned_to="reviewer-a",
            updated_at="2026-07-05T00:01:00Z",
        )
        approved = repository.update_proposal_decision(
            transaction=transaction,
            tenant_id="tenant-a",
            proposal_id=proposal["id"],
            status="approved",
            decision="approve",
            comment="ship it",
            decided_at="2026-07-05T00:02:00Z",
            updated_at="2026-07-05T00:02:00Z",
        )
        version = repository.insert_version(transaction=transaction, record=_version_record())
        executed = repository.mark_proposal_executed(
            transaction=transaction,
            tenant_id="tenant-a",
            proposal_id=proposal["id"],
            updated_at="2026-07-05T00:03:00Z",
        )
        deployed = repository.mark_version_deployed(
            transaction=transaction,
            tenant_id="tenant-a",
            version_id=version["id"],
            deployed_at="2026-07-05T00:04:00Z",
        )
        run = repository.insert_run(transaction=transaction, record=_run_record())
        terminal_run = repository.update_run_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            transition=PIPELINE_RUN_SUCCEEDED,
            output_dataset_ref="analytics.orders_ready",
            output_version_id="dataset-version-1",
            timeline=[{"nodeId": "out", "status": "succeeded"}],
            error=None,
            completed_at="2026-07-05T00:05:00Z",
        )
        schedule = repository.upsert_schedule(transaction=transaction, record=_schedule_record(enabled=True))
        disabled_schedule = repository.upsert_schedule(transaction=transaction, record=_schedule_record(enabled=False))
        test_result = repository.insert_test_result(transaction=transaction, record=_test_result_record())
        due_after_disable = repository.list_due_schedules(transaction=transaction, tenant_id="tenant-a", limit=10)
        deleted = repository.delete_schedule(transaction=transaction, tenant_id="tenant-a", pipeline_id="pipe_orders")

    assert assigned is not None
    assert assigned["assigned_to"] == "reviewer-a"
    assert approved is not None
    assert approved["status"] == "approved"
    assert executed is not None
    assert executed["status"] == "executed"
    assert deployed is not None
    assert deployed["deployed_at"] == "2026-07-05T00:04:00Z"
    assert terminal_run is not None
    assert terminal_run["status"] == "succeeded"
    assert schedule["enabled"] is True
    assert disabled_schedule["enabled"] is False
    assert due_after_disable == []
    assert deleted is True
    assert test_result["result"] == {"valid": True}


def _branch_record(branch_id: str = "branch_a", *, tenant_id: str = "tenant-a") -> PipelineBranchRecord:
    return PipelineBranchRecord(
        branch_id=branch_id,
        tenant_id=tenant_id,
        pipeline_id="pipe_orders",
        name="orders-builder",
        base_version_id=None,
        base_graph=_graph("base"),
        graph=_graph("initial"),
        graph_fingerprint="fp-initial",
        created_by="user-a",
        created_at="2026-07-05T00:00:00Z",
        updated_at="2026-07-05T00:00:00Z",
    )


def _proposal_record() -> PipelineProposalRecord:
    return PipelineProposalRecord(
        proposal_id="proposal_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        branch_id="branch_a",
        title="Orders pipeline",
        description="Approve orders pipeline",
        graph=_graph("initial"),
        graph_fingerprint="fp-initial",
        created_by="user-a",
        created_at="2026-07-05T00:00:30Z",
    )


def _version_record() -> PipelineVersionRecord:
    return PipelineVersionRecord(
        version_id="version_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        version_number=1,
        graph=_graph("initial"),
        graph_fingerprint="fp-initial",
        proposal_id="proposal_a",
        created_by="reviewer-a",
        created_at="2026-07-05T00:03:00Z",
    )


def _run_record() -> PipelineRunRecord:
    return PipelineRunRecord(
        run_id="run_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        version_id="version_a",
        status="running",
        output_dataset_ref=None,
        output_version_id=None,
        timeline=[],
        error=None,
        created_by="user-a",
        started_at="2026-07-05T00:05:00Z",
        completed_at=None,
    )


def _schedule_record(*, enabled: bool) -> PipelineScheduleRecord:
    return PipelineScheduleRecord(
        schedule_id="schedule_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        version_id="version_a",
        schedule={"kind": "interval", "everyMinutes": 15},
        enabled=enabled,
        updated_by="user-a",
        updated_at="2026-07-05T00:06:00Z",
    )


def _test_result_record() -> PipelineTestResultRecord:
    return PipelineTestResultRecord(
        result_id="test_result_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        branch_id="branch_a",
        status="passed",
        result={"valid": True},
        created_by="user-a",
        created_at="2026-07-05T00:07:00Z",
    )


def _graph(label: str) -> dict[str, Any]:
    return {
        "nodes": [{"id": "out", "type": "output_dataset", "config": {"outputDatasetRef": f"analytics.{label}"}}],
        "edges": [],
        "layout": {},
        "outputContract": {"columns": [{"name": "id", "type": "string", "nullable": False}]},
        "tests": [],
        "schedule": None,
    }
