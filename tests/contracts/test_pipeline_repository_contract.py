from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any

from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRecord,
    PipelineProposalRecord,
    PipelineRunRecord,
    PipelineScheduleOperationRecord,
    PipelineScheduleRecord,
    PipelineTestResultRecord,
    PipelineVersionRecord,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_PARTIAL,
    PIPELINE_RUN_RECONCILED_PARTIAL,
    PIPELINE_RUN_SUCCEEDED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_repository import SqlAlchemyPipelineRepository
from sqlalchemy import create_engine, update
from sqlalchemy.engine import Engine


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
        rebased = repository.rebase_branch(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            expected_fingerprint="fp-updated",
            base_version_id="version-base",
            base_graph=_graph("version-base"),
            graph=_graph("rebased"),
            graph_fingerprint="fp-rebased",
            rebased_at="2026-07-05T00:00:03Z",
            updated_at="2026-07-05T00:00:03Z",
        )
        stale_rebase = repository.rebase_branch(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            expected_fingerprint="fp-updated",
            base_version_id="version-stale",
            base_graph=_graph("version-stale"),
            graph=_graph("stale"),
            graph_fingerprint="fp-stale",
            rebased_at="2026-07-05T00:00:04Z",
            updated_at="2026-07-05T00:00:04Z",
        )
        closed = repository.close_branch(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            status="abandoned",
            merged_version_id=None,
            updated_at="2026-07-05T00:00:05Z",
        )
        stale_close = repository.close_branch(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            status="merged",
            merged_version_id="version-stale",
            updated_at="2026-07-05T00:00:06Z",
        )
        open_branches = repository.list_branches(transaction=transaction, tenant_id="tenant-a", status="open", limit=10)
        hidden = repository.branch_by_id(transaction=transaction, tenant_id="tenant-other", branch_id="branch_a")

    assert created is not None
    assert duplicate is None
    assert other_tenant is not None
    assert stale_update is None
    assert updated is not None
    assert updated["graph_fingerprint"] == "fp-updated"
    assert rebased is not None
    assert rebased["base_version_id"] == "version-base"
    assert stale_rebase is None
    assert closed is not None
    assert closed["status"] == "abandoned"
    assert stale_close is None
    assert open_branches == []
    assert hidden is None


def test_pipeline_repository_contract_governance_run_schedule_and_tests(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_flow_contract.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)

    with engine.begin() as transaction:
        repository.insert_branch_if_name_free(transaction=transaction, record=_branch_record())
        proposal = repository.insert_proposal(transaction=transaction, record=_proposal_record())
        branch_with_proposal = repository.set_branch_proposal(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch_a",
            proposal_id=proposal["id"],
            updated_at="2026-07-05T00:00:45Z",
        )
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
        approved_proposals = repository.list_proposals(
            transaction=transaction,
            tenant_id="tenant-a",
            status="approved",
            limit=10,
        )
        version = repository.insert_version(transaction=transaction, record=_version_record())
        latest_version = repository.latest_version(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipe_orders",
        )
        versions = repository.list_versions(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipe_orders",
            limit=10,
        )
        executed = repository.mark_proposal_executed(
            transaction=transaction,
            tenant_id="tenant-a",
            proposal_id=proposal["id"],
            updated_at="2026-07-05T00:03:00Z",
        )
        stale_withdraw = repository.withdraw_proposal(
            transaction=transaction,
            tenant_id="tenant-a",
            proposal_id=proposal["id"],
            updated_at="2026-07-05T00:03:30Z",
        )
        deployed = repository.mark_version_deployed(
            transaction=transaction,
            tenant_id="tenant-a",
            version_id=version["id"],
            execution_plan={"compilerVersion": "pipeline-plan-v2.0", "nodes": []},
            plan_fingerprint="plan-fp",
            compiler_version="pipeline-plan-v2.0",
            deployed_at="2026-07-05T00:04:00Z",
        )
        run = repository.insert_run(transaction=transaction, record=_run_record())
        executing_run = repository.claim_run_execution(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            timeline=[{"event": "pipeline.run.execution_claimed"}],
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-05T00:07:00Z",
            execution_heartbeat_at="2026-07-05T00:05:00Z",
        )
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
        stale_terminal_run = repository.update_run_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            transition=PIPELINE_RUN_FAILED,
            output_dataset_ref=None,
            output_version_id=None,
            timeline=[],
            error={"message": "too late"},
            completed_at="2026-07-05T00:05:30Z",
        )
        schedule = repository.upsert_schedule(transaction=transaction, record=_schedule_record(enabled=True))
        fetched_schedule = repository.schedule_by_pipeline(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipe_orders",
        )
        due_before_disable = repository.list_due_schedules(
            transaction=transaction,
            tenant_id="tenant-a",
            due_at="2026-07-05T00:06:00Z",
            limit=10,
        )
        disabled_schedule = repository.upsert_schedule(transaction=transaction, record=_schedule_record(enabled=False))
        test_result = repository.insert_test_result(transaction=transaction, record=_test_result_record())
        due_after_disable = repository.list_due_schedules(
            transaction=transaction,
            tenant_id="tenant-a",
            due_at="2026-07-05T00:06:00Z",
            limit=10,
        )
        deleted = repository.delete_schedule(transaction=transaction, tenant_id="tenant-a", pipeline_id="pipe_orders")
        deleted_again = repository.delete_schedule(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipe_orders",
        )
        hidden_run = repository.run_by_id(transaction=transaction, tenant_id="tenant-other", run_id=run["id"])

    assert branch_with_proposal is not None
    assert branch_with_proposal["proposal_id"] == proposal["id"]
    assert assigned is not None
    assert assigned["assigned_to"] == "reviewer-a"
    assert approved is not None
    assert approved["status"] == "approved"
    assert stale_withdraw is None
    assert approved_proposals[0]["id"] == proposal["id"]
    assert latest_version is not None
    assert latest_version["id"] == version["id"]
    assert versions[0]["id"] == version["id"]
    assert executed is not None
    assert executed["status"] == "executed"
    assert deployed is not None
    assert deployed["deployed_at"] == "2026-07-05T00:04:00Z"
    assert deployed["plan_fingerprint"] == "plan-fp"
    assert deployed["compiler_version"] == "pipeline-plan-v2.0"
    assert executing_run is not None
    assert executing_run["status"] == "running"
    assert executing_run["execution_lease_token"] == "lease-a"
    assert terminal_run is not None
    assert terminal_run["status"] == "succeeded"
    assert terminal_run["execution_lease_token"] is None
    assert stale_terminal_run is None
    assert schedule["enabled"] is True
    assert fetched_schedule is not None
    assert fetched_schedule["id"] == schedule["id"]
    assert due_before_disable[0]["id"] == schedule["id"]
    assert disabled_schedule["enabled"] is False
    assert due_after_disable == []
    assert deleted is True
    assert deleted_again is False
    assert test_result["result"] == {"valid": True}
    assert hidden_run is None


def test_pipeline_partial_reconciliation_fences_stale_replays(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_reconciliation.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)
    record = replace(
        _run_record(),
        run_id="run_reconciliation",
        status="partial",
        completed_at="2026-07-05T00:05:00Z",
    )

    with engine.begin() as transaction:
        repository.insert_run(transaction=transaction, record=record)
        first = repository.update_run_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=record.run_id,
            transition=PIPELINE_RUN_RECONCILED_PARTIAL,
            output_dataset_ref=None,
            output_version_id=None,
            timeline=[{"event": "pipeline.run.commit_outcome_reconciled"}],
            error={"message": "mixed outputs"},
            completed_at="2026-07-05T00:06:00Z",
            expected_completed_at=record.completed_at,
        )
        stale = repository.update_run_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=record.run_id,
            transition=PIPELINE_RUN_RECONCILED_PARTIAL,
            output_dataset_ref=None,
            output_version_id=None,
            timeline=[{"event": "duplicate reconciliation"}],
            error={"message": "duplicate"},
            completed_at="2026-07-05T00:07:00Z",
            expected_completed_at=record.completed_at,
        )

    assert first is not None
    assert first["completed_at"] == "2026-07-05T00:06:00Z"
    assert stale is None


def test_pipeline_run_execution_lease_contract(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_lease_contract.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)

    with engine.begin() as transaction:
        run = repository.insert_run(transaction=transaction, record=_run_record())
        claimed = repository.claim_run_execution(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            timeline=[{"event": "pipeline.run.execution_claimed"}],
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-05T00:07:00Z",
            execution_heartbeat_at="2026-07-05T00:05:00Z",
        )
        wrong_owner = repository.renew_run_execution_lease(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            execution_lease_token="lease-b",
            execution_lease_expires_at="2026-07-05T00:09:00Z",
            execution_heartbeat_at="2026-07-05T00:06:00Z",
        )
        renewed = repository.renew_run_execution_lease(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-05T00:09:00Z",
            execution_heartbeat_at="2026-07-05T00:06:00Z",
        )
        premature = repository.expire_run_execution(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            execution_lease_token="lease-a",
            expired_at="2026-07-05T00:08:59Z",
            transition=PIPELINE_RUN_FAILED,
            output_dataset_ref=None,
            output_version_id=None,
            outputs=[],
            timeline=[{"event": "pipeline.run.failed"}],
            error={"message": "premature"},
            completed_at="2026-07-05T00:08:59Z",
        )
        expired = repository.expire_run_execution(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=run["id"],
            execution_lease_token="lease-a",
            expired_at="2026-07-05T00:09:00Z",
            transition=PIPELINE_RUN_PARTIAL,
            output_dataset_ref="clean.orders",
            output_version_id="version-1",
            outputs=[{"nodeId": "output", "status": "COMMITTED"}],
            timeline=[{"event": "pipeline.run.reconciliation_required"}],
            error={"message": "reconciliation required"},
            completed_at="2026-07-05T00:09:00Z",
        )

    assert claimed is not None
    assert wrong_owner is None
    assert renewed is not None
    assert renewed["execution_lease_expires_at"] == "2026-07-05T00:09:00Z"
    assert premature is None
    assert expired is not None
    assert expired["status"] == "partial"
    assert expired["output_dataset_ref"] == "clean.orders"
    assert expired["output_version_id"] == "version-1"
    assert expired["outputs"] == [{"nodeId": "output", "status": "COMMITTED"}]
    assert expired["execution_lease_token"] is None


def test_pipeline_stale_execution_runs_scan_targets_only_expired_leases(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_stale_scan.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)

    with engine.begin() as transaction:
        claimed_run = repository.insert_run(transaction=transaction, record=_run_record())
        repository.claim_run_execution(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id=claimed_run["id"],
            timeline=[{"event": "pipeline.run.execution_claimed"}],
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-05T00:07:00Z",
            execution_heartbeat_at="2026-07-05T00:05:00Z",
        )
        unclaimed_running = repository.insert_run(
            transaction=transaction,
            record=replace(_run_record(), run_id="run_b", idempotency_key="run_b"),
        )
        before_expiry = repository.stale_execution_runs(
            transaction=transaction,
            now="2026-07-05T00:06:59Z",
            limit=100,
        )
        at_expiry = repository.stale_execution_runs(
            transaction=transaction,
            now="2026-07-05T00:07:00Z",
            limit=100,
        )

    # A running run that was never claimed carries no lease and is never scanned.
    assert unclaimed_running["execution_lease_token"] is None
    # The claimed run only becomes stale once its lease expiry is reached.
    assert [row["id"] for row in before_expiry] == []
    assert [row["id"] for row in at_expiry] == ["run_a"]


def test_pipeline_scheduler_repository_contract_sqlite(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline_scheduler_contract.db'}", future=True)
    db.create_database(engine)

    _assert_pipeline_scheduler_repository_contract(engine)


def test_pipeline_scheduler_repository_contract_postgres(postgres_fixture: Any) -> None:
    _assert_pipeline_scheduler_repository_contract(postgres_fixture.engine)


def test_pipeline_scheduler_claim_has_one_postgres_winner(postgres_fixture: Any) -> None:
    engine = postgres_fixture.engine
    repository = SqlAlchemyPipelineRepository(engine)
    with engine.begin() as transaction:
        schedule = repository.upsert_schedule(transaction=transaction, record=_schedule_record(enabled=True))
    barrier = Barrier(2, timeout=5)

    def claim(owner: str) -> dict[str, object] | None:
        barrier.wait()
        return _claim_schedule(
            repository,
            engine,
            schedule,
            owner,
            f"lease-{owner}",
            "2026-07-05T00:07:00Z",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-a", "worker-b")))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0]["fencing_token"] == 1


def _assert_pipeline_scheduler_repository_contract(engine: Engine) -> None:
    repository = SqlAlchemyPipelineRepository(engine)
    with engine.begin() as transaction:
        schedule = repository.upsert_schedule(transaction=transaction, record=_schedule_record(enabled=True))
        not_due = repository.list_due_schedules(
            transaction=transaction,
            tenant_id="tenant-a",
            due_at="2026-07-05T00:05:59Z",
            limit=10,
        )
        due = repository.list_due_schedules(
            transaction=transaction,
            tenant_id="tenant-a",
            due_at="2026-07-05T00:06:00Z",
            limit=10,
        )
    assert not_due == []
    assert [row["id"] for row in due] == [schedule["id"]]
    _assert_legacy_schedule_write_is_reconcilable(repository, engine, schedule)

    first_claim = _claim_schedule(repository, engine, schedule, "worker-a", "lease-a", "2026-07-05T00:07:00Z")
    duplicate_claim = _claim_schedule(repository, engine, schedule, "worker-b", "lease-b", "2026-07-05T00:08:00Z")
    assert first_claim is not None
    assert first_claim["fencing_token"] == 1
    assert duplicate_claim is None

    with engine.begin() as transaction:
        stale_completion = repository.complete_schedule_tick(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            lease_token="wrong-lease",
            fencing_token=1,
            values=_completed_tick_values("2026-07-05T00:21:00Z"),
        )
        completed = repository.complete_schedule_tick(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            lease_token="lease-a",
            fencing_token=1,
            values=_completed_tick_values("2026-07-05T00:21:00Z"),
        )
    assert stale_completion is None
    assert completed is not None
    assert completed["next_due_at"] == "2026-07-05T00:21:00Z"
    assert completed["lease_token"] is None

    _assert_expired_lease_takeover(repository, engine, completed)
    _assert_schedule_operation_idempotency(repository, engine)


def _assert_legacy_schedule_write_is_reconcilable(
    repository: SqlAlchemyPipelineRepository,
    engine: Engine,
    schedule: dict[str, object],
) -> None:
    legacy_updated_at = "2026-07-05T00:05:30Z"
    with engine.begin() as transaction:
        transaction.execute(
            update(db.pipeline_schedules)
            .where(db.pipeline_schedules.c.id == schedule["id"])
            .values(
                schedule={"type": "interval", "intervalMinutes": 5, "timezone": "UTC"},
                enabled=True,
                updated_at=legacy_updated_at,
            )
        )
        stale = repository.list_schedules_needing_reconciliation(
            transaction=transaction,
            tenant_id="tenant-a",
            observed_at="2026-07-05T00:06:00Z",
            limit=10,
        )
        reconciled = repository.reconcile_schedule_runtime(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            expected_updated_at=legacy_updated_at,
            observed_at="2026-07-05T00:06:00Z",
            schedule={"triggerType": "interval", "intervalSeconds": 300, "timezone": "UTC"},
            status="active",
            trigger_type="interval",
            timezone="UTC",
            next_due_at="2026-07-05T00:06:00Z",
            paused_reason=None,
        )
        stale_retry = repository.reconcile_schedule_runtime(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            expected_updated_at=legacy_updated_at,
            observed_at="2026-07-05T00:06:00Z",
            schedule={"triggerType": "interval", "intervalSeconds": 300, "timezone": "UTC"},
            status="active",
            trigger_type="interval",
            timezone="UTC",
            next_due_at="2026-07-05T00:06:00Z",
            paused_reason=None,
        )
    assert [row["id"] for row in stale] == [schedule["id"]]
    assert reconciled is not None
    assert reconciled["runtime_config_updated_at"] == legacy_updated_at
    assert stale_retry is None


def _claim_schedule(
    repository: SqlAlchemyPipelineRepository,
    engine: Engine,
    schedule: dict[str, object],
    owner: str,
    lease_token: str,
    lease_expires_at: str,
) -> dict[str, object] | None:
    with engine.begin() as transaction:
        return repository.claim_due_schedule(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            due_at="2026-07-05T00:06:00Z",
            lease_owner=owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
        )


def _assert_expired_lease_takeover(
    repository: SqlAlchemyPipelineRepository,
    engine: Engine,
    schedule: dict[str, object],
) -> None:
    with engine.begin() as transaction:
        reset = repository.upsert_schedule(
            transaction=transaction,
            record=_schedule_record(enabled=True, next_due_at="2026-07-05T00:21:00Z"),
        )
    first = _claim_due_at(repository, engine, reset, "worker-old", "lease-old", "2026-07-05T00:21:00Z")
    early = _claim_due_at(repository, engine, reset, "worker-new", "lease-early", "2026-07-05T00:21:30Z")
    takeover = _claim_due_at(repository, engine, reset, "worker-new", "lease-new", "2026-07-05T00:22:00Z")
    assert first is not None
    assert early is None
    assert takeover is not None
    assert takeover["fencing_token"] == int(first["fencing_token"]) + 1
    with engine.begin() as transaction:
        stale = repository.complete_schedule_tick(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            lease_token="lease-old",
            fencing_token=int(first["fencing_token"]),
            values=_completed_tick_values("2026-07-05T00:36:00Z"),
        )
    assert stale is None


def _claim_due_at(
    repository: SqlAlchemyPipelineRepository,
    engine: Engine,
    schedule: dict[str, object],
    owner: str,
    lease_token: str,
    due_at: str,
) -> dict[str, object] | None:
    with engine.begin() as transaction:
        return repository.claim_due_schedule(
            transaction=transaction,
            tenant_id="tenant-a",
            schedule_id=str(schedule["id"]),
            due_at=due_at,
            lease_owner=owner,
            lease_token=lease_token,
            lease_expires_at="2026-07-05T00:22:00Z",
        )


def _assert_schedule_operation_idempotency(
    repository: SqlAlchemyPipelineRepository,
    engine: Engine,
) -> None:
    operation = _schedule_operation_record()
    with engine.begin() as transaction:
        reserved, is_created = repository.reserve_schedule_operation(transaction=transaction, record=operation)
        completed = repository.complete_schedule_operation(
            transaction=transaction,
            tenant_id="tenant-a",
            operation_id=str(reserved["id"]),
            result={"status": "active"},
        )
        replayed, is_replayed = repository.reserve_schedule_operation(transaction=transaction, record=operation)
    assert is_created is True
    assert completed is not None
    assert completed["result"] == {"status": "active"}
    assert is_replayed is False
    assert replayed["id"] == reserved["id"]


def _completed_tick_values(next_due_at: str) -> dict[str, object]:
    return {
        "last_tick_at": "2026-07-05T00:06:00Z",
        "last_slot_at": "2026-07-05T00:06:00Z",
        "next_due_at": next_due_at,
        "failure_count": 0,
        "last_failure_at": None,
        "last_error": None,
        "status": "active",
        "enabled": True,
        "paused_reason": None,
    }


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


def _schedule_record(
    *,
    enabled: bool,
    next_due_at: str | None = "2026-07-05T00:06:00Z",
) -> PipelineScheduleRecord:
    return PipelineScheduleRecord(
        schedule_id="schedule_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        version_id="version_a",
        schedule={
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": 900,
            "startAt": "2026-07-05T00:06:00Z",
        },
        enabled=enabled,
        status="active" if enabled else "paused",
        trigger_type="interval",
        timezone="UTC",
        next_due_at=next_due_at if enabled else None,
        updated_by="user-a",
        updated_at="2026-07-05T00:06:00Z",
        paused_reason=None if enabled else "disabled_on_upsert",
    )


def _schedule_operation_record() -> PipelineScheduleOperationRecord:
    return PipelineScheduleOperationRecord(
        operation_id="schedule_operation_a",
        tenant_id="tenant-a",
        pipeline_id="pipe_orders",
        operation="pause",
        idempotency_key="pause-pipe-orders",
        request_fingerprint="fingerprint-a",
        created_by="user-a",
        created_at="2026-07-05T00:30:00Z",
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
