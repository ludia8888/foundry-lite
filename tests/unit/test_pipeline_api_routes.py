from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import foundry_lite.application.services.pipeline_async_run_service as pipeline_async_run_module
import foundry_lite.application.services.pipeline_deployment_service as pipeline_deployment_module
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.application.services.pipeline_deployment_admission import PipelineDeploymentOutcomeUnknown
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime
from pytest import raises
from sqlalchemy import delete, update


def test_pipeline_api_routes_cover_builder_review_deploy_run_and_schedule(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    reviewer_headers = {**headers, "X-User-ID": "reviewer-api"}

    branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "api_orders_readiness", "name": "api-v1"},
            headers={**headers, "Idempotency-Key": "api-pipeline-branch"},
        )
    )
    legacy_graph = _orders_pipeline_graph(output_ref="clean.api_orders_readiness")
    updated = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/graph",
            json={
                "graph": legacy_graph,
                "expectedFingerprint": branch["graphFingerprint"],
            },
            headers=headers,
        )
    )
    validation = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/validate", headers=headers))
    casts = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/nodes/clean_sql/casts", headers=headers))
    preview = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/nodes/clean_sql/preview",
            json={"limit": 10},
            headers=headers,
        )
    )
    stats = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/nodes/clean_sql/stats",
            json={"limit": 10},
            headers=headers,
        )
    )
    tests = _ok(client.post(f"/api/pipelines/branches/{branch['id']}/tests/run", headers=headers))
    diff = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/diff", headers=headers))
    proposal = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/propose",
            json={"title": "Deploy API orders readiness", "description": "HTTP route coverage"},
            headers={**headers, "Idempotency-Key": "api-pipeline-proposal"},
        )
    )
    proposals = _ok(client.get("/api/pipelines/proposals?limit=10", headers=headers))
    proposal_detail = _ok(client.get(f"/api/pipelines/proposals/{proposal['id']}", headers=headers))
    assigned = _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/assign",
            json={"assigneeUserId": "reviewer-api"},
            headers=headers,
        )
    )
    approved = _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/decision",
            json={"decision": "approve", "comment": "ship it"},
            headers=reviewer_headers,
        )
    )
    version = _ok(client.post(f"/api/pipelines/proposals/{proposal['id']}/execute", headers=headers))
    versions = _ok(client.get("/api/pipelines/api_orders_readiness/versions?limit=10", headers=headers))
    version_detail = _ok(client.get(f"/api/pipelines/versions/{version['id']}", headers=headers))
    deployment_result = pipeline_deployment_module._deployment_result
    has_failed_projection = False

    def fail_first_committed_projection(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal has_failed_projection
        if not has_failed_projection:
            has_failed_projection = True
            raise RuntimeError("deployment projection failed after commit")
        return deployment_result(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_deployment_module, "_deployment_result", fail_first_committed_projection)
    with raises(PipelineDeploymentOutcomeUnknown):
        client.post(
            f"/api/pipelines/api_orders_readiness/deploy/{version['id']}",
            json={"options": {"mode": "api-test"}},
            headers={**headers, "Idempotency-Key": "api-pipeline-deployment"},
        )
    monkeypatch.setattr(pipeline_deployment_module, "_deployment_result", deployment_result)
    deployed = _ok(
        client.post(
            f"/api/pipelines/api_orders_readiness/deploy/{version['id']}",
            json={"options": {"mode": "api-test"}},
            headers={**headers, "Idempotency-Key": "api-pipeline-deployment"},
        )
    )
    deployments = _ok(client.get("/api/pipelines/api_orders_readiness/deployments?limit=10", headers=headers))
    legacy_projection_started = Event()
    legacy_projection_release = Event()
    legacy_projection = pipeline_async_run_module.append_legacy_terminal_event

    def hold_legacy_projection(*args: object, **kwargs: object) -> None:
        legacy_projection_started.set()
        if not legacy_projection_release.wait(timeout=10):
            raise RuntimeError("legacy terminal projection release timed out")
        legacy_projection(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_async_run_module, "append_legacy_terminal_event", hold_legacy_projection)
    run = _ok(
        client.post(
            "/api/pipelines/api_orders_readiness/runs?waitSeconds=30",
            json={},
            headers={**headers, "Idempotency-Key": "api-pipeline-run"},
        )
    )
    assert legacy_projection_started.wait(timeout=5)
    try:
        run_detail = _ok(client.get(f"/api/pipelines/runs/{run['id']}", headers=headers))
        timeline = _ok(client.get(f"/api/pipelines/runs/{run['id']}/timeline", headers=headers))
    finally:
        legacy_projection_release.set()
    schedule = _ok(
        client.put(
            "/api/pipelines/api_orders_readiness/schedule",
            json={
                "versionId": version["id"],
                "schedule": {
                    "triggerType": "interval",
                    "timezone": "UTC",
                    "intervalSeconds": 900,
                },
                "enabled": True,
            },
            headers={**headers, "Idempotency-Key": "api-pipeline-schedule"},
        )
    )
    schedule_detail = _ok(client.get("/api/pipelines/api_orders_readiness/schedule", headers=headers))
    due = _ok(client.get("/api/pipelines/scheduler/due?maxRuns=1", headers=headers))
    tick = _ok(client.post("/api/pipelines/scheduler/tick?maxRuns=1", headers=headers))
    paused = _ok(
        client.post(
            "/api/pipelines/api_orders_readiness/schedule/pause",
            headers={**headers, "Idempotency-Key": "api-pipeline-schedule-pause"},
        )
    )
    resumed = _ok(
        client.post(
            "/api/pipelines/api_orders_readiness/schedule/resume",
            headers={**headers, "Idempotency-Key": "api-pipeline-schedule-resume"},
        )
    )
    deleted = _ok(
        client.delete(
            "/api/pipelines/api_orders_readiness/schedule",
            headers={**headers, "Idempotency-Key": "api-pipeline-schedule-delete"},
        )
    )
    branch_detail = _ok(client.get(f"/api/pipelines/branches/{branch['id']}", headers=headers))
    branch_list = _ok(client.get("/api/pipelines/branches?status=merged&limit=10", headers=headers))

    assert branch["graphSchemaVersion"] == 2
    assert branch["graph"]["schemaVersion"] == 2
    assert updated["graphSchemaVersion"] == 2
    assert updated["graph"] == normalize_pipeline_graph(legacy_graph)
    assert updated["graphFingerprint"] == pipeline_graph_fingerprint(updated["graph"])
    assert updated["graphFingerprint"] != branch["graphFingerprint"]
    assert validation["valid"] is True
    assert validation["fingerprint"] == updated["graphFingerprint"]
    assert validation["normalizedFingerprint"] == updated["graphFingerprint"]
    assert casts["suggestions"][0]["column"] == "order_id"
    assert preview["noCommit"] is True
    assert preview["schema"][0]["name"] == "order_id"
    assert stats["rowCount"] is None
    assert tests["status"] == "passed"
    assert diff["baseStale"] is False
    assert proposals["items"][0]["id"] == proposal["id"]
    assert proposal_detail["status"] == "submitted"
    assert assigned["assignedTo"] == "reviewer-api"
    assert approved["status"] == "approved"
    assert version["versionNumber"] == 1
    assert proposal["graphSchemaVersion"] == 2
    assert version["graphSchemaVersion"] == 2
    assert versions["items"][0]["id"] == version["id"]
    assert version_detail["pipelineId"] == "api_orders_readiness"
    assert deployed["options"] == {"mode": "api-test"}
    assert deployments["items"][0]["id"] == deployed["deployment"]["id"]
    assert len(deployments["items"]) == 1
    assert run["status"] == "succeeded"
    assert run_detail["outputDatasetRef"] == "clean.api_orders_readiness"
    assert [node["nodeId"] for node in run_detail["nodeRuns"]] == ["raw_orders", "clean_sql", "out"]
    assert all(node["status"] == "succeeded" for node in run_detail["nodeRuns"])
    assert all(node["attempts"][0]["attemptNumber"] == 1 for node in run_detail["nodeRuns"])
    assert run_detail["artifacts"][-1]["artifactRef"]["versionId"] == run_detail["outputVersionId"]
    assert run_detail["outputs"][0]["commitKind"] == "SERVING_ASSET"
    assert run_detail["outputs"][0]["isServing"] is True
    assert run_detail["artifacts"][-1]["manifest"]["manifestVersion"] == 1
    assert (
        run_detail["artifacts"][-1]["manifest"]["contentFingerprint"]
        == run_detail["artifacts"][-1]["contentFingerprint"]
    )
    assert any(item["event"] == "pipeline.run.succeeded" for item in timeline["timeline"])
    assert schedule["enabled"] is True
    assert schedule_detail["id"] == schedule["id"]
    assert due["items"][0]["pipelineId"] == "api_orders_readiness"
    assert tick["started"][0]["run"]["status"] in {"queued", "running", "succeeded"}
    assert paused["status"] == "paused"
    assert resumed["status"] == "active"
    assert deleted == {"deleted": True}
    assert branch_detail["status"] == "merged"
    assert branch_list["items"][0]["id"] == branch["id"]

    terminal_cancel = client.post(
        f"/api/pipelines/runs/{run['id']}/cancel",
        json={},
        headers={**headers, "Idempotency-Key": "api-pipeline-terminal-cancel"},
    )
    assert terminal_cancel.status_code == 202
    assert terminal_cancel.json()["status"] == "succeeded"
    missing_branch = client.get("/api/pipelines/branches/missing-branch", headers=headers)
    assert missing_branch.status_code == 404


def test_pipeline_api_routes_cover_rebase_abandon_and_withdraw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    reviewer_headers = {**headers, "X-User-ID": "reviewer-api"}

    seed = _create_graph_branch(
        client,
        headers,
        pipeline_id="api_rebase_orders",
        name="seed",
        branch_key="api-rebase-seed",
        output_ref="clean.api_rebase_seed",
    )
    _ok(client.post(f"/api/pipelines/branches/{seed['id']}/tests/run", headers=headers))
    seed_proposal = _ok(
        client.post(
            f"/api/pipelines/branches/{seed['id']}/propose",
            json={"title": "Seed"},
            headers={**headers, "Idempotency-Key": "api-rebase-seed-proposal"},
        )
    )
    _ok(
        client.post(
            f"/api/pipelines/proposals/{seed_proposal['id']}/assign",
            json={"assigneeUserId": "reviewer-api"},
            headers=headers,
        )
    )
    _ok(
        client.post(
            f"/api/pipelines/proposals/{seed_proposal['id']}/decision",
            json={"decision": "approve"},
            headers=reviewer_headers,
        )
    )
    seed_version = _ok(client.post(f"/api/pipelines/proposals/{seed_proposal['id']}/execute", headers=headers))

    branch = _create_graph_branch(
        client,
        headers,
        pipeline_id="api_rebase_orders",
        name="candidate",
        branch_key="api-rebase-candidate",
        output_ref="clean.api_rebase_candidate",
    )
    rebased = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/rebase",
            json={"expectedFingerprint": branch["graphFingerprint"]},
            headers=headers,
        )
    )
    abandoned = _ok(client.post(f"/api/pipelines/branches/{branch['id']}/abandon", headers=headers))

    withdraw_branch = _create_graph_branch(
        client,
        headers,
        pipeline_id="api_rebase_orders",
        name="withdraw",
        branch_key="api-rebase-withdraw",
        output_ref="clean.api_rebase_withdraw",
    )
    withdraw_proposal = _ok(
        client.post(
            f"/api/pipelines/branches/{withdraw_branch['id']}/propose",
            json={"title": "Withdraw"},
            headers={**headers, "Idempotency-Key": "api-rebase-withdraw-proposal"},
        )
    )
    withdrawn = _ok(client.post(f"/api/pipelines/proposals/{withdraw_proposal['id']}/withdraw", headers=headers))

    assert seed_version["versionNumber"] == 1
    assert rebased["baseVersionId"] == seed_version["id"]
    assert abandoned["status"] == "abandoned"
    assert withdrawn["status"] == "withdrawn"


def test_pipeline_proposal_allows_author_review_but_blocks_unassigned_and_stale_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'protected-proposal.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    creator_headers = _context_headers(ctx.actor_user_id, ctx.tenant_id, ctx.roles)
    reviewer_headers = _context_headers("reviewer-api", ctx.tenant_id, ctx.roles)
    third_party_headers = _context_headers("reviewer-other", ctx.tenant_id, ctx.roles)

    candidate = _create_graph_branch(
        client,
        creator_headers,
        pipeline_id="protected_pipeline",
        name="candidate",
        branch_key="protected-candidate",
        output_ref="clean.protected_candidate",
    )
    proposal = _propose(client, creator_headers, candidate, "candidate-proposal")
    self_assignment = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/assign",
        json={"assigneeUserId": ctx.actor_user_id},
        headers=creator_headers,
    )
    assert self_assignment.status_code == 200
    self_approved = _decide(client, creator_headers, proposal, "approve")
    assert self_approved["canCurrentUserReview"] is True
    assert self_approved["reviewPolicy"]["requiresSeparateReviewer"] is False

    candidate = _create_graph_branch(
        client,
        creator_headers,
        pipeline_id="protected_pipeline",
        name="reviewed-candidate",
        branch_key="protected-reviewed-candidate",
        output_ref="clean.protected_reviewed_candidate",
    )
    proposal = _propose(client, creator_headers, candidate, "reviewed-candidate-proposal")
    unassigned_decision = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/decision",
        json={"decision": "approve"},
        headers=reviewer_headers,
    )
    assert unassigned_decision.status_code == 400

    _assign(client, creator_headers, proposal, "reviewer-api")
    third_party_decision = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/decision",
        json={"decision": "approve"},
        headers=third_party_headers,
    )
    creator_decision = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/decision",
        json={"decision": "approve"},
        headers=creator_headers,
    )
    assert third_party_decision.status_code == 400
    assert creator_decision.status_code == 400
    approved = _decide(client, reviewer_headers, proposal, "approve")
    assert approved["canCurrentUserReview"] is True
    reviewer_replay = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/decision",
        json={"decision": "approve"},
        headers=reviewer_headers,
    )
    third_party_replay = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/decision",
        json={"decision": "approve"},
        headers=third_party_headers,
    )
    creator_replay = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/decision",
        json={"decision": "approve"},
        headers=creator_headers,
    )
    assert reviewer_replay.status_code == 200
    assert third_party_replay.status_code == 400
    assert creator_replay.status_code == 400

    winner = _create_graph_branch(
        client,
        creator_headers,
        pipeline_id="protected_pipeline",
        name="winner",
        branch_key="protected-winner",
        output_ref="clean.protected_winner",
    )
    winner_proposal = _propose(client, creator_headers, winner, "winner-proposal")
    _assign(client, creator_headers, winner_proposal, "reviewer-api")
    _decide(client, reviewer_headers, winner_proposal, "approve")
    winner_version = _ok(
        client.post(
            f"/api/pipelines/proposals/{winner_proposal['id']}/execute",
            headers=creator_headers,
        )
    )

    corrupt_actor = _create_graph_branch(
        client,
        creator_headers,
        pipeline_id="corrupt_actor_pipeline",
        name="candidate",
        branch_key="corrupt-actor-branch",
        output_ref="clean.corrupt_actor",
    )
    corrupt_actor_proposal = _propose(client, creator_headers, corrupt_actor, "corrupt-actor-proposal")
    _assign(client, creator_headers, corrupt_actor_proposal, "reviewer-api")
    _decide(client, reviewer_headers, corrupt_actor_proposal, "approve")
    with foundry.engine.begin() as conn:
        conn.execute(
            update(db.audit_events)
            .where(
                db.audit_events.c.event_type == "pipeline.proposal.approved",
                db.audit_events.c.resource_id == corrupt_actor_proposal["id"],
            )
            .values(actor_user_id="reviewer-other")
        )
    corrupt_actor_execute = client.post(
        f"/api/pipelines/proposals/{corrupt_actor_proposal['id']}/execute",
        headers=creator_headers,
    )

    missing_audit = _create_graph_branch(
        client,
        creator_headers,
        pipeline_id="missing_audit_pipeline",
        name="candidate",
        branch_key="missing-audit-branch",
        output_ref="clean.missing_audit",
    )
    missing_audit_proposal = _propose(client, creator_headers, missing_audit, "missing-audit-proposal")
    _assign(client, creator_headers, missing_audit_proposal, "reviewer-api")
    _decide(client, reviewer_headers, missing_audit_proposal, "approve")
    with foundry.engine.begin() as conn:
        conn.execute(
            delete(db.audit_events).where(
                db.audit_events.c.event_type == "pipeline.proposal.approved",
                db.audit_events.c.resource_id == missing_audit_proposal["id"],
            )
        )
    missing_audit_execute = client.post(
        f"/api/pipelines/proposals/{missing_audit_proposal['id']}/execute",
        headers=creator_headers,
    )

    stale_execute = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/execute",
        headers=creator_headers,
    )
    stale_view = _ok(
        client.get(
            f"/api/pipelines/proposals/{proposal['id']}",
            headers=creator_headers,
        )
    )
    current_branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "protected_pipeline", "name": "current"},
            headers={**creator_headers, "Idempotency-Key": "protected-current"},
        )
    )
    assert stale_execute.status_code == 409
    assert corrupt_actor_execute.status_code == 403
    assert missing_audit_execute.status_code == 403
    assert stale_view["isStale"] is True
    assert "newer_pipeline_version_exists" in stale_view["staleReasons"]
    withdrawn = _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/withdraw",
            headers=creator_headers,
        )
    )
    assert withdrawn["status"] == "withdrawn"
    assert current_branch["baseVersionId"] == winner_version["id"]
    assert current_branch["graphFingerprint"] == winner_version["graphFingerprint"]


def test_protected_pipeline_proposal_rejects_author_assignment_and_reports_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'protected-reviewer.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    creator_headers = _context_headers(ctx.actor_user_id, ctx.tenant_id, ctx.roles)
    candidate = _create_graph_branch(
        client,
        creator_headers,
        pipeline_id="protected_reviewer_pipeline",
        name="candidate",
        branch_key="protected-reviewer-candidate",
        output_ref="clean.protected_reviewer_candidate",
    )
    proposal = _propose(client, creator_headers, candidate, "protected-reviewer-proposal")
    foundry._services.pipelines.governance.profile = RuntimeProfile.from_value("production")

    self_assignment = client.post(
        f"/api/pipelines/proposals/{proposal['id']}/assign",
        json={"assigneeUserId": ctx.actor_user_id},
        headers=creator_headers,
    )
    assert self_assignment.status_code == 403
    assert "reviewer other than" in self_assignment.text

    assigned = _assign(client, creator_headers, proposal, "reviewer-api")
    assert assigned["reviewPolicy"]["requiresSeparateReviewer"] is True
    assert assigned["canCurrentUserReview"] is False


def test_pipeline_rebase_three_way_merges_independent_changes_and_reports_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'three-way-rebase.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    creator = _context_headers(ctx.actor_user_id, ctx.tenant_id, ctx.roles)
    reviewer = _context_headers("reviewer-api", ctx.tenant_id, ctx.roles)

    seed = _create_graph_branch(
        client,
        creator,
        pipeline_id="three_way_pipeline",
        name="seed",
        branch_key="three-way-seed",
        output_ref="clean.three_way_seed",
    )
    version_one = _approve_and_execute(client, creator, reviewer, seed, "three-way-v1")
    candidate = _create_branch_only(client, creator, "three_way_pipeline", "candidate", "three-way-candidate")
    candidate_graph = deepcopy(candidate["graph"])
    _set_node_config(candidate_graph, "out", "outputDatasetRef", "clean.three_way_candidate")
    candidate = _update_branch_graph(client, creator, candidate, candidate_graph)

    winner = _create_branch_only(client, creator, "three_way_pipeline", "winner", "three-way-winner")
    winner_graph = deepcopy(winner["graph"])
    _set_node_config(winner_graph, "clean_sql", "label", "winner-v2-label")
    winner = _update_branch_graph(client, creator, winner, winner_graph)
    version_two = _approve_and_execute(client, creator, reviewer, winner, "three-way-v2")

    rebased = _ok(
        client.post(
            f"/api/pipelines/branches/{candidate['id']}/rebase",
            json={"expectedFingerprint": candidate["graphFingerprint"]},
            headers=creator,
        )
    )
    assert rebased["baseVersionId"] == version_two["id"]
    assert _node_config(rebased["graph"], "out", "outputDatasetRef") == "clean.three_way_candidate"
    assert _node_config(rebased["graph"], "clean_sql", "label") == "winner-v2-label"

    conflicted = _create_branch_only(client, creator, "three_way_pipeline", "conflicted", "three-way-conflicted")
    conflicted_graph = deepcopy(conflicted["graph"])
    _set_node_config(conflicted_graph, "out", "outputDatasetRef", "clean.ours")
    conflicted = _update_branch_graph(client, creator, conflicted, conflicted_graph)
    winner_three = _create_branch_only(client, creator, "three_way_pipeline", "winner-three", "three-way-winner-3")
    winner_three_graph = deepcopy(winner_three["graph"])
    _set_node_config(winner_three_graph, "out", "outputDatasetRef", "clean.theirs")
    winner_three = _update_branch_graph(client, creator, winner_three, winner_three_graph)
    _approve_and_execute(client, creator, reviewer, winner_three, "three-way-v3")

    conflict = client.post(
        f"/api/pipelines/branches/{conflicted['id']}/rebase",
        json={"expectedFingerprint": conflicted["graphFingerprint"]},
        headers=creator,
    )
    assert conflict.status_code == 409
    conflicts = conflict.json()["detail"]["details"]["conflicts"]
    assert conflicts == [
        {
            "path": "$.nodes[out].config.outputDatasetRef",
            "kind": "concurrent_change",
        }
    ]
    assert version_one["versionNumber"] == 1


def test_pipeline_api_preserves_unknown_v2_node_for_read_only_compatibility(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'unknown-v2-api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "unknown_v2", "name": "read-only-compatible"},
            headers={**headers, "Idempotency-Key": "unknown-v2-branch"},
        )
    )
    graph = _unknown_v2_graph()
    updated = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/graph",
            json={"graph": graph, "expectedFingerprint": branch["graphFingerprint"]},
            headers=headers,
        )
    )
    loaded = _ok(client.get(f"/api/pipelines/branches/{branch['id']}", headers=headers))
    validation = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/validate", headers=headers))
    future_node = next(node for node in loaded["graph"]["nodes"] if node["id"] == "future")

    assert updated["graphSchemaVersion"] == 2
    assert loaded["graph"] == updated["graph"]
    assert future_node["descriptorId"] == "transform.future_semantic"
    assert future_node["config"] == {
        "prompt": "Interpret this future artifact.",
        "nested": {"mode": "future"},
    }
    assert future_node["futureNodeContract"] == graph["nodes"][0]["futureNodeContract"]
    assert loaded["graph"]["edges"][0]["futureEdgeContract"] == graph["edges"][0]["futureEdgeContract"]
    assert loaded["graph"]["futureGraphContract"] == graph["futureGraphContract"]
    assert validation["valid"] is False
    assert any(error["code"] == "node_descriptor_not_found" for error in validation["errors"])


def test_pipeline_preview_run_executes_unsaved_graph_without_serving_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'preview-api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "preview_orders", "name": "draft"},
            headers={**headers, "Idempotency-Key": "preview-branch"},
        )
    )
    graph = _orders_preview_graph_v2()
    created_response = client.post(
        f"/api/pipelines/branches/{branch['id']}/preview-runs",
        json={"graph": graph, "targetNodeId": "out"},
        headers={**headers, "Idempotency-Key": "preview-run-key"},
    )
    assert created_response.status_code == 202, created_response.text
    created = created_response.json()
    completed = _wait_for_preview(client, str(created["id"]), headers)
    datasets = client.get("/api/datasets", headers=headers).json()
    node_types = _ok(client.get("/api/pipelines/node-types", headers=headers))
    processors = _ok(client.get("/api/media/processors", headers=headers))
    trained_models = _ok(client.get("/api/pipelines/trained-models", headers=headers))

    replay = client.post(
        f"/api/pipelines/branches/{branch['id']}/preview-runs",
        json={"graph": graph, "targetNodeId": "out"},
        headers={**headers, "Idempotency-Key": "preview-run-key"},
    )
    conflict = client.post(
        f"/api/pipelines/branches/{branch['id']}/preview-runs",
        json={"graph": graph, "targetNodeId": "out", "limits": {"tableRows": 1}},
        headers={**headers, "Idempotency-Key": "preview-run-key"},
    )
    oversized = client.post(
        f"/api/pipelines/branches/{branch['id']}/preview-runs",
        json={"graph": graph, "targetNodeId": "out", "limits": {"tableRows": 501}},
        headers={**headers, "Idempotency-Key": "preview-run-oversized"},
    )

    assert completed["status"] == "SUCCEEDED"
    assert completed["commitForbidden"] is True
    assert completed["servingVersionCreated"] is False
    assert completed["limits"]["tableRows"] == 500
    assert completed["outputs"][0]["items"] == [
        {"order_id": "O-1", "amount": 10},
        {"order_id": "O-2", "amount": 20},
    ]
    envelopes = completed["outputs"][0]["securityEnvelopes"]
    assert len(envelopes) == 1
    assert envelopes[0]["tenantId"] == ctx.tenant_id
    assert envelopes[0]["resourceType"] == "dataset"
    assert envelopes[0]["resourceId"].startswith("ds_")
    assert envelopes[0]["classification"] == "public"
    assert len(envelopes[0]["securityEnvelopeFingerprint"]) == 64
    assert completed["outputs"][0]["passport"]["securityEnvelopes"] == envelopes
    assert completed["artifacts"][0]["passport"]["securityEnvelopes"] == envelopes
    assert all(artifact["serving"] is False for artifact in completed["artifacts"])
    assert [f"{item['namespace']}.{item['name']}" for item in datasets] == ["raw.pipeline_orders"]
    assert node_types["schemaVersion"] == 2
    assert any(item["descriptorId"] == "transform.document_extract" for item in node_types["items"])
    assert processors["available"] is True
    assert any(item["processorId"] == "pdf_text_v1@1" for item in processors["items"])
    assert trained_models["available"] is True
    assert trained_models["items"][0]["modelRef"] == "demo.transaction-risk"
    assert trained_models["items"][0]["previewSupported"] is False
    assert replay.status_code == 202
    assert replay.json()["id"] == created["id"]
    assert conflict.status_code == 409
    assert oversized.status_code == 422


def test_pipeline_api_routes_convert_domain_errors(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(pipelines=_FailingPipelines()))
    client = TestClient(api_main.app)
    headers = {"X-User-ID": "user-demo-admin", "X-Roles": "admin,data_engineer,ops_manager"}
    graph_update = {"graph": _orders_pipeline_graph(output_ref="clean.error"), "expectedFingerprint": "fp"}
    preview = {"limit": 1}

    requests = [
        lambda: client.get("/api/pipelines/node-types", headers=headers),
        lambda: client.get("/api/media/processors", headers=headers),
        lambda: client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "p", "name": "n"},
            headers={**headers, "Idempotency-Key": "k"},
        ),
        lambda: client.get("/api/pipelines/branches", headers=headers),
        lambda: client.get("/api/pipelines/branches/b", headers=headers),
        lambda: client.post("/api/pipelines/branches/b/graph", json=graph_update, headers=headers),
        lambda: client.get("/api/pipelines/branches/b/diff", headers=headers),
        lambda: client.post(
            "/api/pipelines/branches/b/rebase",
            json={"expectedFingerprint": "fp"},
            headers=headers,
        ),
        lambda: client.post(
            "/api/pipelines/branches/b/propose",
            json={"title": "t"},
            headers={**headers, "Idempotency-Key": "proposal-k"},
        ),
        lambda: client.post("/api/pipelines/branches/b/abandon", headers=headers),
        lambda: client.get("/api/pipelines/branches/b/validate", headers=headers),
        lambda: client.get("/api/pipelines/branches/b/nodes/n/casts", headers=headers),
        lambda: client.post("/api/pipelines/branches/b/nodes/n/preview", json=preview, headers=headers),
        lambda: client.post(
            "/api/pipelines/branches/b/preview-runs",
            json={"graph": _orders_preview_graph_v2(), "targetNodeId": "out"},
            headers={**headers, "Idempotency-Key": "preview-k"},
        ),
        lambda: client.get("/api/pipelines/preview-runs/preview", headers=headers),
        lambda: client.post("/api/pipelines/preview-runs/preview/cancel", headers=headers),
        lambda: client.post("/api/pipelines/branches/b/nodes/n/stats", json=preview, headers=headers),
        lambda: client.post("/api/pipelines/branches/b/tests/run", headers=headers),
        lambda: client.get("/api/pipelines/proposals", headers=headers),
        lambda: client.get("/api/pipelines/proposals/p", headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/assign", json={"assigneeUserId": "u"}, headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/decision", json={"decision": "approve"}, headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/execute", headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/withdraw", headers=headers),
        lambda: client.get("/api/pipelines/versions/v", headers=headers),
        lambda: client.get("/api/pipelines/runs/r", headers=headers),
        lambda: client.get("/api/pipelines/runs/r/timeline", headers=headers),
        lambda: client.post(
            "/api/pipelines/runs/r/cancel",
            json={},
            headers={**headers, "Idempotency-Key": "cancel-k"},
        ),
        lambda: client.get("/api/pipelines/scheduler/due?maxRuns=1", headers=headers),
        lambda: client.post("/api/pipelines/scheduler/tick?maxRuns=1", headers=headers),
        lambda: client.get("/api/pipelines/p/versions", headers=headers),
        lambda: client.post(
            "/api/pipelines/p/deploy/v",
            json={"options": {}},
            headers={**headers, "Idempotency-Key": "deploy-k"},
        ),
        lambda: client.get("/api/pipelines/p/deployments", headers=headers),
        lambda: client.post(
            "/api/pipelines/p/runs",
            json={},
            headers={**headers, "Idempotency-Key": "run-k"},
        ),
        lambda: client.put(
            "/api/pipelines/p/schedule",
            json={
                "versionId": "v",
                "schedule": {
                    "triggerType": "interval",
                    "timezone": "UTC",
                    "intervalSeconds": 900,
                },
                "enabled": True,
            },
            headers={**headers, "Idempotency-Key": "schedule-k"},
        ),
        lambda: client.get("/api/pipelines/p/schedule", headers=headers),
        lambda: client.post(
            "/api/pipelines/p/schedule/pause",
            headers={**headers, "Idempotency-Key": "schedule-pause-k"},
        ),
        lambda: client.post(
            "/api/pipelines/p/schedule/resume",
            headers={**headers, "Idempotency-Key": "schedule-resume-k"},
        ),
        lambda: client.delete(
            "/api/pipelines/p/schedule",
            headers={**headers, "Idempotency-Key": "schedule-delete-k"},
        ),
    ]

    for request in requests:
        response = request()
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"


class _FailingPipelines:
    def __getattr__(self, _name: str):
        def _raise(*_args, **_kwargs):
            raise ValidationFailed("pipeline route failure")

        return _raise


def _create_graph_branch(
    client: TestClient,
    headers: dict[str, str],
    *,
    pipeline_id: str,
    name: str,
    branch_key: str,
    output_ref: str,
) -> dict[str, Any]:
    branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": pipeline_id, "name": name},
            headers={**headers, "Idempotency-Key": branch_key},
        )
    )
    return _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/graph",
            json={
                "graph": _orders_pipeline_graph(output_ref=output_ref),
                "expectedFingerprint": branch["graphFingerprint"],
            },
            headers=headers,
        )
    )


def _create_branch_only(
    client: TestClient,
    headers: dict[str, str],
    pipeline_id: str,
    name: str,
    key: str,
) -> dict[str, Any]:
    return _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": pipeline_id, "name": name},
            headers={**headers, "Idempotency-Key": key},
        )
    )


def _update_branch_graph(
    client: TestClient,
    headers: dict[str, str],
    branch: dict[str, Any],
    graph: dict[str, Any],
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/graph",
            json={
                "graph": graph,
                "expectedFingerprint": branch["graphFingerprint"],
            },
            headers=headers,
        )
    )


def _approve_and_execute(
    client: TestClient,
    creator_headers: dict[str, str],
    reviewer_headers: dict[str, str],
    branch: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    proposal = _propose(client, creator_headers, branch, key)
    _assign(client, creator_headers, proposal, reviewer_headers["X-User-ID"])
    _decide(client, reviewer_headers, proposal, "approve")
    return _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/execute",
            headers=creator_headers,
        )
    )


def _set_node_config(
    graph: dict[str, Any],
    node_id: str,
    field: str,
    value: object,
) -> None:
    nodes = graph.get("nodes")
    assert isinstance(nodes, list)
    node = next(item for item in nodes if item.get("id") == node_id)
    node["config"][field] = value


def _node_config(
    graph: dict[str, Any],
    node_id: str,
    field: str,
) -> object:
    nodes = graph.get("nodes")
    assert isinstance(nodes, list)
    node = next(item for item in nodes if item.get("id") == node_id)
    return node["config"].get(field)


def _context_headers(
    user_id: str,
    tenant_id: str,
    roles: tuple[str, ...],
) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "X-User-ID": user_id,
        "X-Roles": ",".join(roles),
    }


def _propose(
    client: TestClient,
    headers: dict[str, str],
    branch: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/propose",
            json={"title": key},
            headers={**headers, "Idempotency-Key": key},
        )
    )


def _assign(
    client: TestClient,
    headers: dict[str, str],
    proposal: dict[str, Any],
    reviewer: str,
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/assign",
            json={"assigneeUserId": reviewer},
            headers=headers,
        )
    )


def _decide(
    client: TestClient,
    headers: dict[str, str],
    proposal: dict[str, Any],
    decision: str,
) -> dict[str, Any]:
    if decision == "approve":
        _ok(client.post(f"/api/pipelines/branches/{proposal['branchId']}/tests/run", headers=headers))
    return _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/decision",
            json={"decision": decision},
            headers=headers,
        )
    )


def _ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_preview(
    client: TestClient,
    preview_run_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
    for _ in range(200):
        payload = _ok(client.get(f"/api/pipelines/preview-runs/{preview_run_id}", headers=headers))
        if payload["status"] in terminal:
            return payload
        Event().wait(0.01)
    raise AssertionError("pipeline preview did not reach terminal state")


def _orders_pipeline_graph(*, output_ref: str) -> dict[str, object]:
    output_schema = [_column("order_id"), _column("amount", "int")]
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
        "tests": [{"name": "schema contract", "expected": {"columns": output_schema}}],
        "schedule": {"kind": "manual"},
    }


def _orders_preview_graph_v2() -> dict[str, object]:
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
                    "outputDatasetRef": "preview.orders",
                },
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "clean.preview_orders"},
            },
        ],
        "edges": [
            {
                "id": "raw-to-select",
                "sourceNodeId": "raw_orders",
                "sourcePortId": "dataset",
                "targetNodeId": "select",
                "targetPortId": "input",
            },
            {
                "id": "select-to-out",
                "sourceNodeId": "select",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            },
        ],
        "layout": {},
        "outputContract": {"columns": [_column("order_id"), _column("amount", "integer")]},
        "tests": [],
        "schedule": None,
    }


def _unknown_v2_graph() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "future",
                "kind": "transform",
                "descriptorId": "transform.future_semantic",
                "specVersion": 1,
                "config": {
                    "prompt": "Interpret this future artifact.",
                    "nested": {"mode": "future"},
                },
                "futureNodeContract": {
                    "introducedIn": 3,
                    "preservationMode": "opaque",
                },
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "clean.future"},
            },
        ],
        "edges": [
            {
                "id": "future-out",
                "sourceNodeId": "future",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
                "futureEdgeContract": {"lineageMode": "future-property-level"},
            }
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
        "futureGraphContract": {
            "introducedIn": 3,
            "preservationMode": "opaque",
        },
    }


def _column(name: str, column_type: str = "string") -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}
