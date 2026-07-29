from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRecord,
    PipelineProposalRecord,
    PipelineVersionRecord,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_compiler_service import PipelineCompilerService
from foundry_lite.application.services.pipeline_execution_contracts import pipeline_execution_plan_payload
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_payloads import run_record
from foundry_lite.application.services.pipeline_plan_compiler import PipelinePlanCompiler
from foundry_lite.application.services.pipeline_run_recovery import PipelineExecutionLeaseLost
from foundry_lite.application.services.pipeline_run_requests import run_request_fingerprint
from foundry_lite.application.services.pipeline_schedule_runtime import (
    parse_schedule_timestamp,
    schedule_iso,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select, update


def _assign_and_approve(
    foundry: FoundryLite,
    proposal: dict[str, object],
    ctx: RequestContext,
) -> None:
    reviewer_id = f"{ctx.actor_user_id}-reviewer"
    foundry.pipelines.assign(str(proposal["id"]), assignee_user_id=reviewer_id, ctx=ctx)
    reviewer = RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=reviewer_id,
        roles=ctx.roles,
    )
    foundry.pipelines.approve(str(proposal["id"]), ctx=reviewer)


def test_pipeline_builder_graph_preview_review_deploy_and_run(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_readiness",
        name="dataset-output-v1",
        idempotency_key="pipeline-branch-orders-readiness",
        ctx=ctx,
    )
    legacy_graph = _orders_pipeline_graph()
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=legacy_graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    validation = foundry.pipelines.validate(str(branch["id"]), ctx=ctx)
    preview = foundry.pipelines.preview_node(str(branch["id"]), "clean_sql", options={"limit": 25}, ctx=ctx)
    test_result = foundry.pipelines.run_tests(str(branch["id"]), ctx=ctx)
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy orders readiness",
        idempotency_key="pipeline-proposal-orders-readiness",
        ctx=ctx,
    )
    replayed_proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy orders readiness",
        idempotency_key="pipeline-proposal-orders-readiness-replay",
        ctx=ctx,
    )
    assigned = foundry.pipelines.assign(str(proposal["id"]), assignee_user_id="reviewer-a", ctx=ctx)
    reviewer_ctx = RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id="reviewer-a",
        roles=ctx.roles,
    )
    approved = foundry.pipelines.approve(str(proposal["id"]), ctx=reviewer_ctx)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)
    deployed = foundry.pipelines.deploy(
        "orders_readiness",
        str(version["id"]),
        idempotency_key="deploy-orders-readiness-v1",
        options={"mode": "manual"},
        ctx=ctx,
    )
    replayed_deployment = foundry.pipelines.deploy(
        "orders_readiness",
        str(version["id"]),
        idempotency_key="deploy-orders-readiness-v1",
        options={"mode": "manual"},
        ctx=ctx,
    )
    deployments = foundry.pipelines.list_deployments("orders_readiness", ctx=ctx)
    with pytest.raises(ConflictDetected, match="idempotency key"):
        foundry.pipelines.deploy(
            "orders_readiness",
            str(version["id"]),
            idempotency_key="deploy-orders-readiness-v1",
            options={"mode": "different"},
            ctx=ctx,
        )
    run = foundry.pipelines.run("orders_readiness", ctx=ctx)
    schedule = foundry.pipelines.upsert_schedule(
        "orders_readiness",
        version_id=str(version["id"]),
        schedule={
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": 900,
            "startAt": "2099-01-01T00:00:00Z",
        },
        idempotency_key="schedule-orders-readiness-v1",
        ctx=ctx,
    )
    replayed_schedule = foundry.pipelines.upsert_schedule(
        "orders_readiness",
        version_id=str(version["id"]),
        schedule={
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": 900,
            "startAt": "2099-01-01T00:00:00Z",
        },
        idempotency_key="schedule-orders-readiness-v1",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="idempotency key"):
        foundry.pipelines.upsert_schedule(
            "orders_readiness",
            version_id=str(version["id"]),
            schedule={"triggerType": "interval", "timezone": "UTC", "intervalSeconds": 1_800},
            idempotency_key="schedule-orders-readiness-v1",
            ctx=ctx,
        )
    legacy_updated_at = schedule_iso(parse_schedule_timestamp(_now(), field="now") + timedelta(seconds=1))
    with foundry.engine.begin() as transaction:
        transaction.execute(
            update(db.pipeline_schedules)
            .where(db.pipeline_schedules.c.id == schedule["id"])
            .values(
                schedule={"type": "interval", "timezone": "UTC", "intervalMinutes": 15},
                enabled=True,
                updated_by="legacy-app-user",
                updated_at=legacy_updated_at,
            )
        )
    loaded_schedule = foundry.pipelines.get_schedule("orders_readiness", ctx=ctx)
    due = foundry.pipelines.preview_due(max_runs=0, ctx=ctx)
    ticked = foundry.pipelines.tick(max_runs=1, ctx=ctx)
    with foundry.engine.connect() as connection:
        runtime_config_updated_at = connection.execute(
            select(db.pipeline_schedules.c.runtime_config_updated_at).where(
                db.pipeline_schedules.c.id == schedule["id"]
            )
        ).scalar_one()
    paused_schedule = foundry.pipelines.pause_schedule(
        "orders_readiness",
        idempotency_key="pause-orders-readiness-v1",
        ctx=ctx,
    )
    replayed_pause = foundry.pipelines.pause_schedule(
        "orders_readiness",
        idempotency_key="pause-orders-readiness-v1",
        ctx=ctx,
    )
    resumed_schedule = foundry.pipelines.resume_schedule(
        "orders_readiness",
        idempotency_key="resume-orders-readiness-v1",
        ctx=ctx,
    )

    rows = foundry.datasets.preview("clean.orders_readiness", ctx=ctx)
    branch_detail = foundry.pipelines.get_branch(str(branch["id"]), ctx=ctx)
    merged_branches = foundry.pipelines.list_branches(status="merged", limit=10, ctx=ctx)
    versions = foundry.pipelines.list_versions("orders_readiness", ctx=ctx)
    version_detail = foundry.pipelines.get_version(str(version["id"]), ctx=ctx)
    proposal_detail = foundry.pipelines.get_proposal(str(proposal["id"]), ctx=ctx)
    executed_proposals = foundry.pipelines.list_proposals(status="executed", ctx=ctx)
    run_detail = foundry.pipelines.get_run(str(run["id"]), ctx=ctx)
    timeline = foundry.pipelines.timeline(str(run["id"]), ctx=ctx)
    deleted_schedule = foundry.pipelines.delete_schedule(
        "orders_readiness",
        idempotency_key="delete-orders-readiness-v1",
        ctx=ctx,
    )
    replayed_delete = foundry.pipelines.delete_schedule(
        "orders_readiness",
        idempotency_key="delete-orders-readiness-v1",
        ctx=ctx,
    )
    with foundry.engine.begin() as transaction:
        cancellable_run = foundry.pipeline_repository.insert_run(
            transaction=transaction,
            record=run_record(ctx, pipeline_id="orders_readiness", version_id=str(version["id"]), now=_now()),
        )
    cancelled = foundry.pipelines.cancel(str(cancellable_run["id"]), ctx=ctx)
    with foundry.engine.begin() as transaction:
        executing_run = foundry.pipeline_repository.insert_run(
            transaction=transaction,
            record=run_record(ctx, pipeline_id="orders_readiness", version_id=str(version["id"]), now=_now()),
        )
        claimed_run = foundry.pipeline_repository.claim_run_execution(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(executing_run["id"]),
            timeline=[*executing_run["timeline"], {"event": "pipeline.run.execution_claimed", "at": _now()}],
            execution_lease_token="active-test-lease",
            execution_lease_expires_at=_now(),
            execution_heartbeat_at=_now(),
        )

    assert branch["graphSchemaVersion"] == 2
    assert branch["graph"]["schemaVersion"] == 2
    assert updated["graph"] == normalize_pipeline_graph(legacy_graph)
    assert updated["graphFingerprint"] == pipeline_graph_fingerprint(updated["graph"])
    assert updated["graphFingerprint"] != branch["graphFingerprint"]
    assert validation["valid"] is True
    assert validation["fingerprint"] == updated["graphFingerprint"]
    assert validation["normalizedFingerprint"] == updated["graphFingerprint"]
    assert preview["noCommit"] is True
    assert test_result["status"] == "passed"
    assert replayed_proposal["id"] == proposal["id"]
    assert assigned["assignedTo"] == "reviewer-a"
    assert approved["status"] == "approved"
    assert version["versionNumber"] == 1
    assert proposal["graphSchemaVersion"] == 2
    assert version["graphSchemaVersion"] == 2
    assert version["graph"] == updated["graph"]
    assert deployed["version"]["deployedAt"] is not None
    assert deployed["version"]["planFingerprint"] == deployed["deployment"]["planFingerprint"]
    assert replayed_deployment["deployment"]["id"] == deployed["deployment"]["id"]
    assert replayed_deployment["options"] == deployed["options"]
    assert deployments["items"][0]["id"] == deployed["deployment"]["id"]
    assert deployed["options"] == {"mode": "manual"}
    assert run["status"] == "succeeded"
    assert schedule["enabled"] is True
    assert replayed_schedule["id"] == schedule["id"]
    assert loaded_schedule is not None
    assert loaded_schedule["id"] == schedule["id"]
    assert due["maxRuns"] == 1
    assert due["items"] == []
    assert ticked["reconciled"] == 1
    assert ticked["started"][0]["run"]["status"] in {"queued", "running", "succeeded"}
    assert runtime_config_updated_at == legacy_updated_at
    assert paused_schedule["status"] == "paused"
    assert replayed_pause["status"] == "paused"
    assert resumed_schedule["status"] == "active"
    assert [row["order_id"] for row in rows] == ["O-1", "O-2"]
    assert branch_detail["status"] == "merged"
    assert merged_branches["items"][0]["id"] == branch["id"]
    assert versions["items"][0]["id"] == version["id"]
    assert version_detail["pipelineId"] == "orders_readiness"
    assert proposal_detail["status"] == "executed"
    assert executed_proposals["items"][0]["id"] == proposal["id"]
    assert run_detail["outputDatasetRef"] == "clean.orders_readiness"
    assert any(item["event"] == "pipeline.run.succeeded" for item in timeline["timeline"])
    assert deleted_schedule == {"deleted": True}
    assert replayed_delete == deleted_schedule
    assert cancelled["status"] == "cancelled"
    assert cancelled["timeline"][-1]["event"] == "pipeline.run.cancelled"
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(
        event["event_type"] == "pipeline.cancelled" and event["resource_id"] == cancelled["id"]
        for event in audit_events
    )
    assert any(event["event_type"] == "pipeline.schedule.reconciled" for event in audit_events)
    assert claimed_run is not None and claimed_run["status"] == "running"
    executing_cancelled = foundry.pipelines.cancel(str(executing_run["id"]), ctx=ctx)
    assert executing_cancelled["status"] == "cancelled"

    with pytest.raises(ConflictDetected, match="pipeline run is already terminal"):
        foundry.pipelines.cancel(str(run["id"]), ctx=ctx)


def test_pipeline_builder_output_node_dataset_ref_alias_deploys(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_dataset_ref_alias",
        name="dataset-ref-output",
        idempotency_key="pipeline-branch-output-dataset-ref-alias",
        ctx=ctx,
    )
    graph = _orders_pipeline_graph(output_ref="clean.orders_dataset_ref_alias")
    output_node = graph["nodes"][2]["config"]
    output_node["datasetRef"] = output_node.pop("outputDatasetRef")
    foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy datasetRef output alias",
        idempotency_key="pipeline-proposal-output-dataset-ref-alias",
        ctx=ctx,
    )
    _assign_and_approve(foundry, proposal, ctx)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)
    deployed = foundry.pipelines.deploy(
        "orders_dataset_ref_alias",
        str(version["id"]),
        idempotency_key="deploy-orders-alias-v1",
        ctx=ctx,
    )
    run = foundry.pipelines.run("orders_dataset_ref_alias", ctx=ctx)

    assert deployed["version"]["deployedAt"] is not None
    assert run["status"] == "succeeded"
    assert run["outputDatasetRef"] == "clean.orders_dataset_ref_alias"


def test_pipeline_unexpected_failure_preserves_execution_claim_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _insert_legacy_governance_rows(foundry, ctx, graph=_orders_pipeline_graph())
    with foundry.engine.begin() as transaction:
        foundry.pipeline_repository.mark_version_deployed(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="legacy-version",
            execution_plan={"kind": "tabular_v1"},
            plan_fingerprint="plan-fingerprint",
            compiler_version="test",
            deployed_at=_now(),
        )

    def fail_compile(self: PipelineCompilerService, **kwargs: object) -> dict[str, object]:
        del self, kwargs
        raise RuntimeError("compiler exploded after execution claim")

    monkeypatch.setattr(PipelineCompilerService, "compile_graph", fail_compile)

    run = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="run-failure-preserves-claim",
        ctx=ctx,
    )

    assert run["status"] == "failed"
    assert [item["event"] for item in run["timeline"]] == [
        "pipeline.run.running",
        "pipeline.run.execution_claimed",
        "pipeline.run.failed",
    ]


def test_pipeline_lease_loss_fences_commit_until_expired_owner_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)
    _insert_legacy_governance_rows(foundry, ctx, graph=_orders_pipeline_graph())
    with foundry.engine.begin() as transaction:
        undeployed = foundry.pipeline_repository.version_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="legacy-version",
        )
        assert undeployed is not None
        plan = PipelinePlanCompiler().compile(undeployed["graph"])
        foundry.pipeline_repository.mark_version_deployed(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="legacy-version",
            execution_plan=pipeline_execution_plan_payload(plan),
            plan_fingerprint=plan.plan_fingerprint,
            compiler_version=plan.compiler_version,
            deployed_at=_now(),
        )
        versions_before = transaction.execute(select(func.count()).select_from(db.dataset_versions)).scalar_one()

    monkeypatch.setattr(foundry.pipeline_repository, "renew_run_execution_lease", lambda **_kwargs: None)

    with pytest.raises(PipelineExecutionLeaseLost, match="lost before commit"):
        foundry.pipelines.run(
            "historical_v1",
            version_id="legacy-version",
            idempotency_key="lease-loss-fences-commit",
            ctx=ctx,
        )
    with foundry.engine.begin() as transaction:
        versions_after = transaction.execute(select(func.count()).select_from(db.dataset_versions)).scalar_one()
        row = foundry.pipeline_repository.run_by_idempotency_key(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            idempotency_key="lease-loss-fences-commit",
        )
        assert row is not None
        assert row["status"] == "running"
        transaction.execute(
            update(db.pipeline_runs)
            .where(
                db.pipeline_runs.c.tenant_id == ctx.tenant_id,
                db.pipeline_runs.c.id == row["id"],
            )
            .values(execution_lease_expires_at="2000-01-01T00:00:00Z")
        )

    recovered = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="lease-loss-fences-commit",
        ctx=ctx,
    )

    assert recovered["status"] == "failed"
    assert recovered["error"]["message"] == "expired pipeline execution lease was recovered as terminal failure"
    assert versions_after == versions_before


def test_idempotent_replay_recovers_queued_run_and_terminalizes_stale_execution(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)
    _insert_legacy_governance_rows(foundry, ctx, graph=_orders_pipeline_graph())
    with foundry.engine.begin() as transaction:
        undeployed = foundry.pipeline_repository.version_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="legacy-version",
        )
        assert undeployed is not None
        plan = PipelinePlanCompiler().compile(undeployed["graph"])
        version = foundry.pipeline_repository.mark_version_deployed(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="legacy-version",
            execution_plan=pipeline_execution_plan_payload(plan),
            plan_fingerprint=plan.plan_fingerprint,
            compiler_version=plan.compiler_version,
            deployed_at=_now(),
        )
        assert version is not None
        fingerprint = run_request_fingerprint("historical_v1", version, None, None)
        queued = foundry.pipeline_repository.insert_run(
            transaction=transaction,
            record=run_record(
                ctx,
                pipeline_id="historical_v1",
                version_id="legacy-version",
                idempotency_key="recover-queued-run",
                request_fingerprint=fingerprint,
                plan_fingerprint=plan.plan_fingerprint,
                now=_now(),
            ),
        )
    recovered = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="recover-queued-run",
        ctx=ctx,
    )

    recovery_now = parse_schedule_timestamp(_now(), field="now")
    stale_started_at = schedule_iso(recovery_now - timedelta(hours=2))
    active_lease_expires_at = schedule_iso(recovery_now + timedelta(minutes=2))
    with foundry.engine.begin() as transaction:
        stale = foundry.pipeline_repository.insert_run(
            transaction=transaction,
            record=run_record(
                ctx,
                pipeline_id="historical_v1",
                version_id="legacy-version",
                idempotency_key="recover-stale-execution",
                request_fingerprint=fingerprint,
                plan_fingerprint=plan.plan_fingerprint,
                now=stale_started_at,
            ),
        )
        claimed = foundry.pipeline_repository.claim_run_execution(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(stale["id"]),
            timeline=[*stale["timeline"], {"event": "pipeline.run.execution_claimed", "at": stale_started_at}],
            execution_lease_token="expired-test-lease",
            execution_lease_expires_at=stale_started_at,
            execution_heartbeat_at=stale_started_at,
        )
        active = foundry.pipeline_repository.insert_run(
            transaction=transaction,
            record=run_record(
                ctx,
                pipeline_id="historical_v1",
                version_id="legacy-version",
                idempotency_key="replay-live-execution",
                request_fingerprint=fingerprint,
                plan_fingerprint=plan.plan_fingerprint,
                now=stale_started_at,
            ),
        )
        active_claim = foundry.pipeline_repository.claim_run_execution(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(active["id"]),
            timeline=[*active["timeline"], {"event": "pipeline.run.execution_claimed", "at": stale_started_at}],
            execution_lease_token="live-test-lease",
            execution_lease_expires_at=active_lease_expires_at,
            execution_heartbeat_at=schedule_iso(recovery_now),
        )
    failed = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="recover-stale-execution",
        ctx=ctx,
    )
    replayed_failed = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="recover-stale-execution",
        ctx=ctx,
    )
    live = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="replay-live-execution",
        ctx=ctx,
    )
    failed_audits = [
        event
        for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]
        if event["event_type"] == "pipeline.failed" and event["resource_id"] == stale["id"]
    ]

    assert recovered["id"] == queued["id"]
    assert recovered["status"] == "succeeded"
    assert claimed is not None and claimed["status"] == "running"
    assert active_claim is not None and active_claim["status"] == "running"
    assert failed["id"] == stale["id"]
    assert failed["status"] == "failed"
    assert replayed_failed["status"] == "failed"
    assert live["status"] == "running"
    assert failed["timeline"][-1]["event"] == "pipeline.run.failed"
    assert failed["error"]["message"] == "expired pipeline execution lease was recovered as terminal failure"
    assert len(failed_audits) == 1


def test_pre_v2_deployed_version_backfills_pinned_execution_plan_on_first_run(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)
    _insert_legacy_governance_rows(foundry, ctx, graph=_orders_pipeline_graph())
    deployed_at = "2026-07-15T00:02:00Z"
    with foundry.engine.begin() as transaction:
        transaction.execute(
            update(db.pipeline_versions)
            .where(db.pipeline_versions.c.id == "legacy-version")
            .values(deployed_at=deployed_at)
        )

    run = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="legacy-first-run-after-v2-upgrade",
        ctx=ctx,
    )
    version = foundry.pipelines.get_version("legacy-version", ctx=ctx)
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]

    assert run["status"] == "succeeded"
    assert version["deployedAt"] == deployed_at
    assert version["executionPlan"]["planFingerprint"] == version["planFingerprint"]
    assert any(
        event["event_type"] == "pipeline.execution_plan_backfilled" and event["resource_id"] == "legacy-version"
        for event in audit_events
    )


def test_scheduler_takeover_treats_replayed_executing_run_as_enqueued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _insert_legacy_governance_rows(foundry, ctx, graph=_orders_pipeline_graph())
    with foundry.engine.begin() as transaction:
        foundry.pipeline_repository.mark_version_deployed(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="legacy-version",
            execution_plan={"kind": "tabular_v1"},
            plan_fingerprint="plan-fingerprint",
            compiler_version="test",
            deployed_at=_now(),
        )
    schedule = foundry.pipelines.upsert_schedule(
        "historical_v1",
        version_id="legacy-version",
        schedule={
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": 300,
            "autoPauseAfterFailures": 1,
        },
        idempotency_key="legacy-scheduler-takeover",
        ctx=ctx,
    )
    slot_start = str(schedule["nextDueAt"])
    with foundry.engine.begin() as transaction:
        claimed = foundry.pipeline_repository.claim_due_schedule(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            schedule_id=str(schedule["id"]),
            due_at=slot_start,
            lease_owner="worker-before-timeout",
            lease_token="expired-lease",
            lease_expires_at=slot_start,
        )
    assert claimed is not None

    monkeypatch.setattr(
        foundry._services.pipelines.async_run,
        "enqueue",
        lambda *_args, **_kwargs: {"id": "run-still-executing", "status": "executing"},
    )
    takeover_at = parse_schedule_timestamp(slot_start, field="slotStart") + timedelta(seconds=61)
    result = foundry._services.pipelines.scheduler.tick_schedules(
        max_runs=1,
        now=takeover_at,
        scheduler_owner="worker-after-timeout",
        ctx=ctx,
    )
    current = foundry.pipelines.get_schedule("historical_v1", ctx=ctx)

    assert result["skipped"] == []
    assert result["started"][0]["run"]["status"] == "executing"
    assert current["status"] == "active"
    assert current["failureCount"] == 0
    assert current["nextDueAt"] == schedule_iso(
        parse_schedule_timestamp(slot_start, field="slotStart") + timedelta(seconds=300)
    )
    assert current["leaseOwner"] is None
    assert current["leaseExpiresAt"] is None


def test_pipeline_builder_persisted_v2_tabular_graph_deploys_and_runs(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_graph_v2",
        name="canonical-v2",
        idempotency_key="pipeline-branch-orders-graph-v2",
        ctx=ctx,
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_orders_pipeline_graph_v2(),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy persisted Graph v2",
        idempotency_key="pipeline-proposal-orders-graph-v2",
        ctx=ctx,
    )
    _assign_and_approve(foundry, proposal, ctx)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)
    deployed = foundry.pipelines.deploy(
        "orders_graph_v2",
        str(version["id"]),
        idempotency_key="deploy-orders-graph-v2",
        ctx=ctx,
    )
    run = foundry.pipelines.run(
        "orders_graph_v2",
        idempotency_key="run-orders-graph-v2",
        ctx=ctx,
    )
    rows = foundry.datasets.preview("clean.orders_graph_v2", ctx=ctx)

    assert updated["graphSchemaVersion"] == 2
    assert version["graphSchemaVersion"] == 2
    assert [item["nodeId"] for item in deployed["compiled"]["transforms"]] == ["select", "out"]
    assert run["status"] == "succeeded"
    assert run["outputDatasetRef"] == "clean.orders_graph_v2"
    assert [(row["order_id"], row["amount"]) for row in rows] == [("O-1", 10), ("O-2", 20)]


def test_legacy_v1_branch_first_write_keeps_old_fingerprint_cas_and_upgrades_to_v2(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    legacy_graph = _orders_pipeline_graph(output_ref="clean.legacy_first_edit")
    old_fingerprint = pipeline_graph_fingerprint(legacy_graph)
    _insert_legacy_branch(
        foundry,
        ctx,
        branch_id="legacy-edit-branch",
        pipeline_id="legacy_first_edit",
        graph=legacy_graph,
        fingerprint=old_fingerprint,
    )

    edited_graph = _orders_pipeline_graph(output_ref="clean.legacy_first_edit_v2")
    with pytest.raises(ConflictDetected, match="pipeline graph fingerprint mismatch"):
        foundry.pipelines.update_graph(
            "legacy-edit-branch",
            graph=edited_graph,
            expected_fingerprint="stale-fingerprint",
            ctx=ctx,
        )
    unchanged = foundry.pipelines.get_branch("legacy-edit-branch", ctx=ctx)
    updated = foundry.pipelines.update_graph(
        "legacy-edit-branch",
        graph=edited_graph,
        expected_fingerprint=old_fingerprint,
        ctx=ctx,
    )
    canonical = normalize_pipeline_graph(edited_graph)

    assert unchanged["graphSchemaVersion"] == 1
    assert unchanged["graph"] == legacy_graph
    assert updated["graphSchemaVersion"] == 2
    assert updated["graph"] == canonical
    assert updated["graphFingerprint"] == pipeline_graph_fingerprint(canonical)
    with pytest.raises(ConflictDetected, match="pipeline graph fingerprint mismatch"):
        foundry.pipelines.update_graph(
            "legacy-edit-branch",
            graph=edited_graph,
            expected_fingerprint=old_fingerprint,
            ctx=ctx,
        )


def test_legacy_v1_branch_rebase_writes_v2_without_rewriting_immutable_version(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    version_graph = _orders_pipeline_graph(output_ref="clean.historical_v1")
    branch_graph = _orders_pipeline_graph(output_ref="clean.legacy_rebase")
    _insert_legacy_governance_rows(foundry, ctx, graph=version_graph)
    branch_fingerprint = pipeline_graph_fingerprint(branch_graph)
    _insert_legacy_branch(
        foundry,
        ctx,
        branch_id="legacy-rebase-branch",
        pipeline_id="historical_v1",
        graph=branch_graph,
        fingerprint=branch_fingerprint,
        base_version_id="legacy-version",
        base_graph=version_graph,
    )

    rebased = foundry.pipelines.rebase(
        "legacy-rebase-branch",
        expected_fingerprint=branch_fingerprint,
        ctx=ctx,
    )
    historical = foundry.pipelines.get_version("legacy-version", ctx=ctx)
    with foundry.engine.begin() as transaction:
        stored = foundry.pipeline_repository.branch_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            branch_id="legacy-rebase-branch",
        )

    assert stored is not None
    assert rebased["graphSchemaVersion"] == 2
    assert rebased["graph"] == normalize_pipeline_graph(branch_graph)
    assert stored["base_graph"] == normalize_pipeline_graph(version_graph)
    assert historical["graphSchemaVersion"] == 1
    assert historical["graph"] == version_graph
    assert historical["graphFingerprint"] == pipeline_graph_fingerprint(version_graph)


def test_existing_v1_proposal_and_version_remain_replayable_without_graph_rewrite(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)
    legacy_graph = _orders_pipeline_graph(output_ref="clean.historical_v1")
    _insert_legacy_governance_rows(foundry, ctx, graph=legacy_graph)

    before_proposal = foundry.pipelines.get_proposal("legacy-proposal", ctx=ctx)
    before_version = foundry.pipelines.get_version("legacy-version", ctx=ctx)
    foundry.pipelines.deploy(
        "historical_v1",
        "legacy-version",
        idempotency_key="deploy-historical-v1",
        ctx=ctx,
    )
    run = foundry.pipelines.run(
        "historical_v1",
        version_id="legacy-version",
        idempotency_key="run-historical-v1",
        ctx=ctx,
    )
    after_proposal = foundry.pipelines.get_proposal("legacy-proposal", ctx=ctx)
    after_version = foundry.pipelines.get_version("legacy-version", ctx=ctx)

    assert run["status"] == "succeeded"
    assert run["outputDatasetRef"] == "clean.historical_v1"
    assert before_proposal["graphSchemaVersion"] == 1
    assert after_proposal["graph"] == before_proposal["graph"] == legacy_graph
    assert after_proposal["graphFingerprint"] == before_proposal["graphFingerprint"]
    assert before_version["graphSchemaVersion"] == 1
    assert after_version["graphSchemaVersion"] == 1
    assert after_version["graph"] == before_version["graph"] == legacy_graph
    assert after_version["graphFingerprint"] == before_version["graphFingerprint"]


def test_pipeline_builder_branch_rebase_abandon_reject_and_withdraw(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    seed_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="seed",
        idempotency_key="pipeline-branch-orders-rebase-seed",
        ctx=ctx,
    )
    foundry.pipelines.update_graph(
        str(seed_branch["id"]),
        graph=_orders_pipeline_graph(output_ref="clean.orders_rebase_seed"),
        expected_fingerprint=str(seed_branch["graphFingerprint"]),
        ctx=ctx,
    )
    seed_proposal = foundry.pipelines.propose(
        str(seed_branch["id"]),
        title="Seed version",
        idempotency_key="pipeline-proposal-orders-rebase-seed",
        ctx=ctx,
    )
    _assign_and_approve(foundry, seed_proposal, ctx)
    seed_version = foundry.pipelines.execute(str(seed_proposal["id"]), ctx=ctx)
    with pytest.raises(ValidationFailed, match="different pipeline"):
        foundry.pipelines.deploy(
            "wrong_pipeline",
            str(seed_version["id"]),
            idempotency_key="deploy-wrong-pipeline",
            ctx=ctx,
        )
    with pytest.raises(ConflictDetected, match="pipeline version is not deployed"):
        foundry.pipelines.run("orders_rebase", version_id=str(seed_version["id"]), ctx=ctx)
    with pytest.raises(NotFound, match="deployed pipeline version not found"):
        foundry.pipelines.run("missing_pipeline", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline version not found"):
        foundry.pipelines.deploy(
            "orders_rebase",
            "missing-version",
            idempotency_key="deploy-missing-version",
            ctx=ctx,
        )
    with pytest.raises(NotFound, match="pipeline run not found"):
        foundry.pipelines.get_run("missing-run", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline run not found"):
        foundry.pipelines.cancel("missing-run", ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="candidate",
        idempotency_key="pipeline-branch-orders-rebase-candidate",
        ctx=ctx,
    )
    duplicate = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="candidate",
        idempotency_key="pipeline-branch-orders-rebase-candidate-replay",
        ctx=ctx,
    )
    cast_graph = _orders_pipeline_graph(
        output_ref="clean.orders_rebase_candidate",
        sql_schema=[_column("order_id"), _column("amount", "string"), _column("created_at")],
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=cast_graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="pipeline graph fingerprint mismatch"):
        foundry.pipelines.update_graph(
            str(branch["id"]),
            graph=cast_graph,
            expected_fingerprint=str(branch["graphFingerprint"]),
            ctx=ctx,
        )
    latest_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="latest-baseline",
        idempotency_key="pipeline-branch-orders-rebase-latest",
        ctx=ctx,
    )
    latest_proposal = foundry.pipelines.propose(
        str(latest_branch["id"]),
        title="Advance latest version",
        idempotency_key="pipeline-proposal-orders-rebase-latest",
        ctx=ctx,
    )
    _assign_and_approve(foundry, latest_proposal, ctx)
    latest_version = foundry.pipelines.execute(str(latest_proposal["id"]), ctx=ctx)
    diff = foundry.pipelines.diff(str(branch["id"]), ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline graph fingerprint mismatch"):
        foundry.pipelines.rebase(
            str(branch["id"]),
            expected_fingerprint=str(branch["graphFingerprint"]),
            ctx=ctx,
        )
    rebased = foundry.pipelines.rebase(
        str(branch["id"]),
        expected_fingerprint=str(updated["graphFingerprint"]),
        ctx=ctx,
    )
    suggestions = foundry.pipelines.suggest_casts(str(branch["id"]), "clean_sql", ctx=ctx)
    stats = foundry.pipelines.node_stats(str(branch["id"]), "clean_sql", options={"sample": True}, ctx=ctx)
    abandoned = foundry.pipelines.abandon(str(branch["id"]), ctx=ctx)
    abandoned_again = foundry.pipelines.abandon(str(branch["id"]), ctx=ctx)
    with pytest.raises(NotFound, match="pipeline branch not found"):
        foundry.pipelines.get_branch("missing-branch", ctx=ctx)

    reject_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="reject-me",
        idempotency_key="pipeline-branch-orders-rebase-reject",
        ctx=ctx,
    )
    reject_updated = foundry.pipelines.update_graph(
        str(reject_branch["id"]),
        graph=_orders_pipeline_graph(output_ref="clean.orders_rebase_reject"),
        expected_fingerprint=str(reject_branch["graphFingerprint"]),
        ctx=ctx,
    )
    reject_proposal = foundry.pipelines.propose(
        str(reject_updated["id"]),
        title="Reject candidate",
        idempotency_key="pipeline-proposal-orders-rebase-reject",
        ctx=ctx,
    )
    reject_reviewer_id = f"{ctx.actor_user_id}-reject-reviewer"
    foundry.pipelines.assign(
        str(reject_proposal["id"]),
        assignee_user_id=reject_reviewer_id,
        ctx=ctx,
    )
    reject_reviewer = RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=reject_reviewer_id,
        roles=ctx.roles,
    )
    rejected = foundry.pipelines.decide(
        str(reject_proposal["id"]),
        decision="reject",
        comment="needs work",
        ctx=reject_reviewer,
    )
    with pytest.raises(ConflictDetected, match="pipeline proposal is not in the required state"):
        foundry.pipelines.assign(str(reject_proposal["id"]), assignee_user_id="reviewer-b", ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline proposal is not in the required state"):
        foundry.pipelines.decide(str(reject_proposal["id"]), decision="approve", ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline proposal is not in the required state"):
        foundry.pipelines.withdraw(str(reject_proposal["id"]), ctx=ctx)
    with pytest.raises(ValidationFailed, match="unsupported pipeline proposal decision"):
        foundry.pipelines.decide(str(reject_proposal["id"]), decision="punt", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline proposal not found"):
        foundry.pipelines.get_proposal("missing-proposal", ctx=ctx)

    withdraw_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="withdraw-me",
        idempotency_key="pipeline-branch-orders-rebase-withdraw",
        ctx=ctx,
    )
    withdraw_updated = foundry.pipelines.update_graph(
        str(withdraw_branch["id"]),
        graph=_orders_pipeline_graph(output_ref="clean.orders_rebase_withdraw"),
        expected_fingerprint=str(withdraw_branch["graphFingerprint"]),
        ctx=ctx,
    )
    withdraw_proposal = foundry.pipelines.propose(
        str(withdraw_updated["id"]),
        title="Withdraw candidate",
        idempotency_key="pipeline-proposal-orders-rebase-withdraw",
        ctx=ctx,
    )
    withdrawn = foundry.pipelines.withdraw(str(withdraw_proposal["id"]), ctx=ctx)

    invalid_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="invalid",
        idempotency_key="pipeline-branch-orders-rebase-invalid",
        ctx=ctx,
    )
    invalid_graph = _orders_pipeline_graph(output_ref="clean.orders_rebase_invalid")
    invalid_graph["nodes"] = []
    foundry.pipelines.update_graph(
        str(invalid_branch["id"]),
        graph=invalid_graph,
        expected_fingerprint=str(invalid_branch["graphFingerprint"]),
        ctx=ctx,
    )
    with pytest.raises(ValidationFailed, match="pipeline graph is invalid"):
        foundry.pipelines.propose(
            str(invalid_branch["id"]),
            title="Invalid candidate",
            idempotency_key="pipeline-proposal-orders-rebase-invalid",
            ctx=ctx,
        )

    assert seed_version["versionNumber"] == 1
    assert duplicate["id"] == branch["id"]
    assert diff["baseStale"] is True
    assert rebased["baseVersionId"] == latest_version["id"]
    assert {"column": "created_at", "from": "string", "to": "timestamp", "confidence": "medium"} in suggestions[
        "suggestions"
    ]
    assert stats["columnCount"] == 3
    assert stats["options"] == {"sample": True}
    assert abandoned["status"] == "abandoned"
    assert abandoned_again["status"] == "abandoned"
    assert rejected["status"] == "rejected"
    assert rejected["decisionComment"] == "needs work"
    assert withdrawn["status"] == "withdrawn"


def _insert_legacy_governance_rows(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    graph: dict[str, object],
) -> None:
    fingerprint = pipeline_graph_fingerprint(graph)
    _insert_legacy_branch(
        foundry,
        ctx,
        branch_id="legacy-source-branch",
        pipeline_id="historical_v1",
        graph=graph,
        fingerprint=fingerprint,
    )
    with foundry.engine.begin() as transaction:
        foundry.pipeline_repository.insert_proposal(
            transaction=transaction,
            record=PipelineProposalRecord(
                proposal_id="legacy-proposal",
                tenant_id=ctx.tenant_id,
                pipeline_id="historical_v1",
                branch_id="legacy-source-branch",
                title="Historical v1 proposal",
                description="Permanent replay fixture",
                graph=graph,
                graph_fingerprint=fingerprint,
                created_by=ctx.actor_user_id,
                created_at="2026-07-15T00:00:00Z",
                graph_schema_version=1,
            ),
        )
        foundry.pipeline_repository.insert_version(
            transaction=transaction,
            record=PipelineVersionRecord(
                version_id="legacy-version",
                tenant_id=ctx.tenant_id,
                pipeline_id="historical_v1",
                version_number=1,
                graph=graph,
                graph_fingerprint=fingerprint,
                proposal_id="legacy-proposal",
                created_by=ctx.actor_user_id,
                created_at="2026-07-15T00:01:00Z",
                graph_schema_version=1,
            ),
        )


def _insert_legacy_branch(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    branch_id: str,
    pipeline_id: str,
    graph: dict[str, object],
    fingerprint: str,
    base_version_id: str | None = None,
    base_graph: dict[str, object] | None = None,
) -> None:
    with foundry.engine.begin() as transaction:
        inserted = foundry.pipeline_repository.insert_branch_if_name_free(
            transaction=transaction,
            record=PipelineBranchRecord(
                branch_id=branch_id,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
                name=branch_id,
                base_version_id=base_version_id,
                base_graph=base_graph or graph,
                graph=graph,
                graph_fingerprint=fingerprint,
                created_by=ctx.actor_user_id,
                created_at="2026-07-15T00:00:00Z",
                updated_at="2026-07-15T00:00:00Z",
                graph_schema_version=1,
            ),
        )
    assert inserted is not None


def _orders_pipeline_graph(
    *,
    output_ref: str = "clean.orders_readiness",
    sql_schema: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    output_schema = sql_schema or [_column("order_id"), _column("amount", "int")]
    return {
        "nodes": [
            {
                "id": "raw_orders",
                "type": "dataset",
                "config": {
                    "datasetRef": "raw.pipeline_orders",
                    "schema": [_column("order_id"), _column("amount", "int")],
                },
            },
            {
                "id": "clean_sql",
                "type": "sql",
                "config": {
                    "sql": "select order_id, amount from {{ input('raw.pipeline_orders') }} order by order_id",
                    "outputDatasetRef": "work.pipeline_orders_clean",
                    "schema": output_schema,
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": output_ref},
            },
        ],
        "edges": [
            {"source": "raw_orders", "target": "clean_sql"},
            {"source": "clean_sql", "target": "out"},
        ],
        "layout": {
            "raw_orders": {"x": 0, "y": 0},
            "clean_sql": {"x": 260, "y": 0},
            "out": {"x": 520, "y": 0},
        },
        "outputContract": {"columns": output_schema},
        "tests": [
            {
                "name": "schema contract",
                "expected": {"columns": output_schema},
            }
        ],
        "schedule": {"kind": "manual"},
    }


def _orders_pipeline_graph_v2() -> dict[str, object]:
    output_schema = [_column("order_id"), _column("amount", "integer")]
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "raw_orders",
                "kind": "source",
                "descriptorId": "source.dataset",
                "specVersion": 1,
                "config": {"datasetRef": "raw.pipeline_orders"},
            },
            {
                "id": "select",
                "kind": "transform",
                "descriptorId": "transform.select_cast",
                "specVersion": 1,
                "config": {
                    "columns": [
                        {"source": "order_id", "name": "order_id", "type": "string"},
                        {"source": "amount", "name": "amount", "type": "integer"},
                    ],
                    "outputDatasetRef": "work.orders_graph_v2",
                },
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "clean.orders_graph_v2"},
            },
        ],
        "edges": [
            {
                "id": "raw-select",
                "sourceNodeId": "raw_orders",
                "sourcePortId": "dataset",
                "targetNodeId": "select",
                "targetPortId": "input",
            },
            {
                "id": "select-out",
                "sourceNodeId": "select",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            },
        ],
        "layout": {},
        "outputContract": {"columns": output_schema},
        "tests": [],
        "schedule": None,
    }


def _column(name: str, column_type: str = "string") -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}
