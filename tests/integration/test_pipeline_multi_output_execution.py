from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.pipeline_execution_contracts import pipeline_execution_plan_payload
from foundry_lite.application.services.pipeline_plan_compiler import compile_pipeline_plan
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select, update


@dataclass(frozen=True)
class _DeployedMultiOutputPipeline:
    foundry: FoundryLite
    ctx: RequestContext
    pipeline_id: str
    first_output_ref: str
    second_output_ref: str
    deployment: Mapping[str, object]


def test_pipeline_run_pairs_each_output_node_with_its_committed_dataset_version(tmp_path: Path) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "paired_outputs")

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-paired-outputs",
        ctx=fixture.ctx,
    )
    first_version_id = _only_dataset_version_id(fixture, fixture.first_output_ref)
    second_version_id = _only_dataset_version_id(fixture, fixture.second_output_ref)

    assert run["status"] == "succeeded"
    assert run["outputs"] == [
        _committed_output("output_a", fixture.first_output_ref, first_version_id),
        _committed_output("output_b", fixture.second_output_ref, second_version_id),
    ]
    assert run["outputDatasetRef"] is None
    assert run["outputVersionId"] is None
    node_runs = _node_runs_by_id(run)
    source_version_id = _only_dataset_version_id(fixture, f"raw.{fixture.pipeline_id.removeprefix('multi_output_')}")
    assert set(node_runs) == {"source", "output_a", "output_b"}
    assert node_runs["source"]["status"] == "succeeded"
    assert node_runs["source"]["attempts"][0]["attemptNumber"] == 1
    assert node_runs["output_a"]["inputArtifacts"][0]["versionId"] == source_version_id
    assert node_runs["output_a"]["inputArtifacts"][0]["sourcePortId"] == "dataset"
    assert node_runs["output_a"]["inputArtifacts"][0]["targetPortId"] == "input"
    assert node_runs["output_a"]["outputArtifacts"][0]["versionId"] == first_version_id
    assert node_runs["output_a"]["outputArtifacts"][0]["portId"] == "dataset"
    assert node_runs["output_b"]["outputArtifacts"][0]["versionId"] == second_version_id
    assert {
        (artifact["nodeId"], artifact["portId"], artifact["artifactRef"]["versionId"])
        for artifact in cast(list[dict[str, Any]], run["artifacts"])
    } == {
        ("source", "dataset", source_version_id),
        ("output_a", "dataset", first_version_id),
        ("output_b", "dataset", second_version_id),
    }


def test_pipeline_run_preserves_first_commit_when_second_output_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "partial_outputs")
    failed_api_name = _compiled_api_name(fixture.deployment, "output_b")
    transform_service = fixture.foundry._services.pipelines.run.transform_service
    original_execute = transform_service._execute_pipeline_transform_run

    def fail_second_output(
        ctx: RequestContext,
        plan,
        *,
        after_transform_commit=None,
    ) -> CommitResult:
        if plan.definition_snapshot["api_name"] == failed_api_name:
            raise ValidationFailed("forced second output failure")
        return original_execute(ctx, plan, after_transform_commit=after_transform_commit)

    monkeypatch.setattr(transform_service, "_execute_pipeline_transform_run", fail_second_output)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-partial-outputs",
        ctx=fixture.ctx,
    )
    first_version_id = _only_dataset_version_id(fixture, fixture.first_output_ref)
    outputs = cast(list[dict[str, object]], run["outputs"])

    assert run["status"] == "partial"
    assert outputs[0] == _committed_output("output_a", fixture.first_output_ref, first_version_id)
    assert outputs[1]["nodeId"] == "output_b"
    assert outputs[1]["artifactKind"] == "dataset_version"
    assert outputs[1]["plane"] == "dataset"
    assert outputs[1]["status"] == "FAILED"
    assert outputs[1]["ref"] == {"datasetRef": fixture.second_output_ref}
    assert outputs[1]["error"]
    assert _dataset_version_ids(fixture, fixture.second_output_ref) == []
    assert run["outputDatasetRef"] is None
    assert run["outputVersionId"] is None
    node_runs = _node_runs_by_id(run)
    assert node_runs["output_a"]["status"] == "succeeded"
    assert node_runs["output_b"]["status"] == "failed"
    assert node_runs["output_b"]["attempts"][0]["status"] == "failed"


def test_pipeline_run_first_output_failure_marks_later_outputs_failed_and_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "first_output_failure")
    first_api_name = _compiled_api_name(fixture.deployment, "output_a")
    second_api_name = _compiled_api_name(fixture.deployment, "output_b")
    transform_service = fixture.foundry._services.pipelines.run.transform_service
    original_execute = transform_service._execute_pipeline_transform_run
    called_api_names: list[str] = []

    def fail_first_output(
        ctx: RequestContext,
        plan,
        *,
        after_transform_commit=None,
    ) -> CommitResult:
        api_name = str(plan.definition_snapshot["api_name"])
        called_api_names.append(api_name)
        if api_name == first_api_name:
            raise ValidationFailed("forced first output failure")
        return original_execute(ctx, plan, after_transform_commit=after_transform_commit)

    monkeypatch.setattr(transform_service, "_execute_pipeline_transform_run", fail_first_output)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-first-output-failure",
        ctx=fixture.ctx,
    )
    outputs = cast(list[dict[str, object]], run["outputs"])
    timeline = cast(list[dict[str, object]], run["timeline"])
    node_terminal_events = [
        (event["event"], event.get("nodeId"))
        for event in timeline
        if event["event"] in {"pipeline.node.failed", "pipeline.node.skipped"}
    ]

    assert run["status"] == "failed"
    assert [(output["nodeId"], output["status"], output["ref"]) for output in outputs] == [
        ("output_a", "FAILED", {"datasetRef": fixture.first_output_ref}),
        ("output_b", "FAILED", {"datasetRef": fixture.second_output_ref}),
    ]
    assert all(output["error"] for output in outputs)
    assert called_api_names == [first_api_name]
    assert second_api_name not in called_api_names
    assert _dataset_version_ids(fixture, fixture.first_output_ref) == []
    assert _dataset_version_ids(fixture, fixture.second_output_ref) == []
    assert node_terminal_events == [
        ("pipeline.node.failed", "output_a"),
        ("pipeline.node.skipped", "output_b"),
    ]
    assert run["outputDatasetRef"] is None
    assert run["outputVersionId"] is None
    node_runs = _node_runs_by_id(run)
    assert node_runs["output_a"]["status"] == "failed"
    assert node_runs["output_a"]["attemptCount"] == 1
    assert node_runs["output_b"]["status"] == "skipped"
    assert node_runs["output_b"]["attemptCount"] == 0
    assert node_runs["output_b"]["attempts"] == []


def test_pipeline_run_target_node_ids_execute_only_the_selected_output(tmp_path: Path) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "targeted_output")

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-targeted-output",
        target_node_ids=["output_b"],
        ctx=fixture.ctx,
    )
    second_version_id = _only_dataset_version_id(fixture, fixture.second_output_ref)

    assert run["status"] == "succeeded"
    assert run["targetNodeIds"] == ["output_b"]
    assert run["outputs"] == [
        _committed_output("output_b", fixture.second_output_ref, second_version_id),
    ]
    assert _dataset_version_ids(fixture, fixture.first_output_ref) == []
    assert run["outputDatasetRef"] == fixture.second_output_ref
    assert run["outputVersionId"] == second_version_id
    assert set(_node_runs_by_id(run)) == {"source", "output_b"}
    assert {artifact["nodeId"] for artifact in cast(list[dict[str, object]], run["artifacts"])} == {
        "source",
        "output_b",
    }


def test_pipeline_run_target_prunes_an_unrelated_validation_only_branch(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    pipeline_id = "multi_output_target_prunes_validation_only"
    source_ref = "raw.target_prunes_validation_only"
    output_ref = "clean.target_prunes_validation_only"
    csv_path = tmp_path / "target-prunes-validation-only.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure(source_ref, ctx=ctx)
    foundry.datasets.upload_csv(source_ref, csv_path, ctx=ctx)
    graph = _target_with_validation_only_media_graph(source_ref, output_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id=pipeline_id,
        slug="target-prunes-validation-only",
        graph=graph,
    )
    plan = pipeline_execution_plan_payload(
        compile_pipeline_plan(graph, target_node_ids=["output_b"]),
    )
    with foundry.engine.begin() as conn:
        deployed_version = foundry.pipeline_repository.mark_version_deployed(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            version_id=str(version["id"]),
            execution_plan=plan,
            plan_fingerprint=str(plan["planFingerprint"]),
            compiler_version=str(plan["compilerVersion"]),
            deployed_at=_now(),
        )
    assert deployed_version is not None

    run = foundry.pipelines.run(
        pipeline_id,
        version_id=str(version["id"]),
        idempotency_key="run-target-prunes-validation-only",
        target_node_ids=["output_b"],
        ctx=ctx,
    )
    version_ids = _dataset_version_ids_for(foundry, ctx, output_ref)

    assert run["status"] == "succeeded"
    assert len(version_ids) == 1
    assert run["outputs"] == [
        _committed_output("output_b", output_ref, version_ids[0]),
    ]
    assert run["outputDatasetRef"] == output_ref
    assert run["outputVersionId"] == version_ids[0]
    assert all(
        event.get("nodeId") not in {"media_source", "media_out"}
        for event in cast(list[dict[str, object]], run["timeline"])
    )
    assert set(_node_runs_by_id(run)) == {"source", "output_b"}
    assert {artifact["nodeId"] for artifact in cast(list[dict[str, object]], run["artifacts"])} == {
        "source",
        "output_b",
    }


def test_pipeline_run_idempotent_replay_creates_no_additional_output_versions(tmp_path: Path) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "idempotent_outputs")

    first_run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-idempotent-outputs",
        ctx=fixture.ctx,
    )
    versions_after_first_run = {
        fixture.first_output_ref: _dataset_version_ids(fixture, fixture.first_output_ref),
        fixture.second_output_ref: _dataset_version_ids(fixture, fixture.second_output_ref),
    }
    evidence_counts = _pipeline_evidence_counts(fixture, str(first_run["id"]))

    replayed_run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-idempotent-outputs",
        ctx=fixture.ctx,
    )

    assert replayed_run["id"] == first_run["id"]
    assert replayed_run["outputs"] == first_run["outputs"]
    assert _dataset_version_ids(fixture, fixture.first_output_ref) == versions_after_first_run[fixture.first_output_ref]
    assert (
        _dataset_version_ids(fixture, fixture.second_output_ref) == versions_after_first_run[fixture.second_output_ref]
    )
    assert all(len(version_ids) == 1 for version_ids in versions_after_first_run.values())
    assert _pipeline_evidence_counts(fixture, str(first_run["id"])) == evidence_counts
    assert replayed_run["nodeRuns"] == first_run["nodeRuns"]
    assert replayed_run["artifacts"] == first_run["artifacts"]


def test_pipeline_artifact_evidence_failure_rolls_back_dataset_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "artifact_evidence_failure")
    repository = fixture.foundry._services.pipelines.run.pipeline_execution_repository
    original_insert_artifact = repository.insert_artifact

    def fail_output_artifact(*, transaction, record):
        if record.node_id == "output_a":
            raise RuntimeError("forced artifact evidence failure")
        return original_insert_artifact(transaction=transaction, record=record)

    monkeypatch.setattr(repository, "insert_artifact", fail_output_artifact)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-artifact-evidence-failure",
        ctx=fixture.ctx,
    )
    node_runs = _node_runs_by_id(run)

    assert run["status"] == "failed"
    assert _dataset_version_ids(fixture, fixture.first_output_ref) == []
    assert _dataset_version_ids(fixture, fixture.second_output_ref) == []
    assert node_runs["output_a"]["status"] == "failed"
    assert node_runs["output_a"]["attempts"][0]["status"] == "failed"
    assert node_runs["output_b"]["status"] == "skipped"
    assert all(artifact["nodeId"] != "output_a" for artifact in cast(list[dict[str, object]], run["artifacts"]))


def test_pipeline_artifacts_preserve_the_sources_exact_classification(tmp_path: Path) -> None:
    fixture = _deploy_multi_output_pipeline(
        tmp_path,
        "confidential_artifacts",
        source_classification="CONFIDENTIAL",
        output_classification="CONFIDENTIAL",
    )

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-confidential-artifacts",
        ctx=fixture.ctx,
    )

    assert run["status"] == "succeeded"
    assert {
        artifact["securityEnvelope"]["classification"] for artifact in cast(list[dict[str, Any]], run["artifacts"])
    } == {"CONFIDENTIAL"}


def test_pipeline_output_cannot_weaken_source_classification(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    source_ref = "raw.classification_downgrade"
    first_output_ref = "clean.classification_downgrade_a"
    second_output_ref = "clean.classification_downgrade_b"
    csv_path = tmp_path / "classification_downgrade.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    _ensure_classified_dataset(foundry, ctx, source_ref, "CONFIDENTIAL")
    _ensure_classified_dataset(foundry, ctx, first_output_ref, "PUBLIC")
    _ensure_classified_dataset(foundry, ctx, second_output_ref, "PUBLIC")
    foundry.datasets.upload_csv(source_ref, csv_path, ctx=ctx)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="multi_output_classification_downgrade",
        slug="classification_downgrade",
        graph=_multi_output_graph(source_ref, first_output_ref, second_output_ref),
    )

    with pytest.raises(ValidationFailed, match="classification would weaken") as exc_info:
        foundry.pipelines.deploy(
            "multi_output_classification_downgrade",
            str(version["id"]),
            idempotency_key="deploy-classification-downgrade",
            ctx=ctx,
        )

    assert exc_info.value.details == {
        "datasetRef": first_output_ref,
        "datasetClassification": "PUBLIC",
        "requiredClassification": "CONFIDENTIAL",
    }
    assert foundry.datasets.list_versions(first_output_ref, ctx=ctx) == []
    assert foundry.datasets.list_versions(second_output_ref, ctx=ctx) == []


def test_pipeline_failed_and_skipped_evidence_rolls_back_with_terminal_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "terminal_evidence_atomicity")
    _force_transform_failure(monkeypatch, fixture, "output_a")
    repository = fixture.foundry._services.pipelines.run.pipeline_execution_repository
    original_terminal = repository.update_node_run_terminal

    def fail_skipped_terminal(*, transition, **kwargs):
        if transition.to_status == "SKIPPED":
            raise RuntimeError("forced skipped evidence failure")
        return original_terminal(transition=transition, **kwargs)

    monkeypatch.setattr(repository, "update_node_run_terminal", fail_skipped_terminal)

    with pytest.raises(RuntimeError, match="terminal evidence transaction failed"):
        fixture.foundry.pipelines.run(
            fixture.pipeline_id,
            idempotency_key="run-terminal-evidence-atomicity",
            ctx=fixture.ctx,
        )

    run = _run_by_idempotency_key(fixture, "run-terminal-evidence-atomicity")
    detail = fixture.foundry.pipelines.get_run(str(run["id"]), ctx=fixture.ctx)
    assert detail["status"] == "executing"
    assert _node_runs_by_id(detail)["output_a"]["status"] == "running"
    assert "output_b" not in _node_runs_by_id(detail)
    assert _pipeline_failure_audit_count(fixture, str(run["id"])) == 0


def test_committed_legacy_outputs_survive_success_terminal_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _deploy_multi_output_pipeline(tmp_path, "success_terminal_reconciliation")
    repository = fixture.foundry._services.pipelines.run.pipeline_repository
    original_terminal = repository.update_run_terminal

    def fail_success_terminal(*, transition, **kwargs):
        if transition.to_status == "succeeded":
            raise RuntimeError("forced success terminal persistence failure")
        return original_terminal(transition=transition, **kwargs)

    monkeypatch.setattr(repository, "update_run_terminal", fail_success_terminal)

    with pytest.raises(RuntimeError, match="success terminal transaction failed"):
        fixture.foundry.pipelines.run(
            fixture.pipeline_id,
            idempotency_key="run-success-terminal-reconciliation",
            ctx=fixture.ctx,
        )

    row = _run_by_idempotency_key(fixture, "run-success-terminal-reconciliation")
    detail = fixture.foundry.pipelines.get_run(str(row["id"]), ctx=fixture.ctx)
    assert detail["status"] == "executing"
    assert _node_runs_by_id(detail)["output_a"]["status"] == "succeeded"
    assert _node_runs_by_id(detail)["output_b"]["status"] == "succeeded"
    assert _only_dataset_version_id(fixture, fixture.first_output_ref)
    assert _only_dataset_version_id(fixture, fixture.second_output_ref)
    assert _pipeline_failure_audit_count(fixture, str(row["id"])) == 0

    replay = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-success-terminal-reconciliation",
        ctx=fixture.ctx,
    )
    assert replay["status"] == "executing"
    _expire_run_lease(fixture, str(row["id"]))

    reconciled = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-success-terminal-reconciliation",
        ctx=fixture.ctx,
    )
    assert reconciled["status"] == "partial"
    assert reconciled["outputDatasetRef"] is None
    assert reconciled["outputVersionId"] is None
    recovered_outputs = {str(item["nodeId"]): item for item in reconciled["outputs"]}
    assert recovered_outputs["output_a"]["ref"]["datasetRef"] == fixture.first_output_ref
    assert recovered_outputs["output_a"]["ref"]["versionId"] == _only_dataset_version_id(
        fixture, fixture.first_output_ref
    )
    assert recovered_outputs["output_b"]["ref"]["datasetRef"] == fixture.second_output_ref
    assert recovered_outputs["output_b"]["ref"]["versionId"] == _only_dataset_version_id(
        fixture, fixture.second_output_ref
    )
    assert {item["status"] for item in recovered_outputs.values()} == {"COMMITTED"}
    assert all(item["ref"]["artifactId"] for item in recovered_outputs.values())
    assert all(item["manifest"] for item in recovered_outputs.values())
    assert all("artifactEvidence" not in item for item in recovered_outputs.values())
    assert "terminal evidence requires reconciliation" in reconciled["error"]["message"]
    assert len(_dataset_version_ids(fixture, fixture.first_output_ref)) == 1
    assert len(_dataset_version_ids(fixture, fixture.second_output_ref)) == 1
    assert _pipeline_audit_count(fixture, str(row["id"]), "pipeline.reconciliation_required") == 1


def _deploy_multi_output_pipeline(
    tmp_path: Path,
    slug: str,
    *,
    source_classification: str | None = None,
    output_classification: str | None = None,
) -> _DeployedMultiOutputPipeline:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    pipeline_id = f"multi_output_{slug}"
    source_ref = f"raw.{slug}"
    first_output_ref = f"clean.{slug}_a"
    second_output_ref = f"clean.{slug}_b"
    csv_path = tmp_path / f"{slug}.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    _ensure_classified_dataset(foundry, ctx, source_ref, source_classification)
    if output_classification is not None:
        _ensure_classified_dataset(foundry, ctx, first_output_ref, output_classification)
        _ensure_classified_dataset(foundry, ctx, second_output_ref, output_classification)
    foundry.datasets.upload_csv(source_ref, csv_path, ctx=ctx)

    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id=pipeline_id,
        slug=slug,
        graph=_multi_output_graph(source_ref, first_output_ref, second_output_ref),
    )
    deployment = foundry.pipelines.deploy(
        pipeline_id,
        str(version["id"]),
        idempotency_key=f"deploy-{slug}",
        ctx=ctx,
    )
    return _DeployedMultiOutputPipeline(
        foundry=foundry,
        ctx=ctx,
        pipeline_id=pipeline_id,
        first_output_ref=first_output_ref,
        second_output_ref=second_output_ref,
        deployment=deployment,
    )


def _execute_graph_version(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    pipeline_id: str,
    slug: str,
    graph: Mapping[str, object],
) -> Mapping[str, object]:
    branch = foundry.pipelines.create_branch(
        pipeline_id=pipeline_id,
        name="two-dataset-outputs",
        idempotency_key=f"branch-{slug}",
        ctx=ctx,
    )
    foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy Dataset outputs",
        idempotency_key=f"proposal-{slug}",
        ctx=ctx,
    )
    reviewer_ctx = _reviewer_context(ctx)
    foundry.pipelines.assign(str(proposal["id"]), assignee_user_id=reviewer_ctx.actor_user_id, ctx=ctx)
    foundry.pipelines.approve(str(proposal["id"]), ctx=reviewer_ctx)
    return foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)


def _reviewer_context(ctx: RequestContext) -> RequestContext:
    return RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=f"{ctx.actor_user_id}-reviewer",
        roles=ctx.roles,
    )


def _multi_output_graph(
    source_ref: str,
    first_output_ref: str,
    second_output_ref: str,
) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            _node("source", "source", "source.dataset", {"datasetRef": source_ref}),
            _node("output_a", "output", "output.dataset", {"outputDatasetRef": first_output_ref}),
            _node("output_b", "output", "output.dataset", {"outputDatasetRef": second_output_ref}),
        ],
        "edges": [
            _edge("source-output-a", "source", "output_a"),
            _edge("source-output-b", "source", "output_b"),
        ],
        "layout": {},
        "outputContract": {
            "columns": [
                {"name": "order_id", "type": "string", "nullable": False},
                {"name": "amount", "type": "integer", "nullable": False},
            ]
        },
        "tests": [],
        "schedule": None,
    }


def _target_with_validation_only_media_graph(
    source_ref: str,
    output_ref: str,
) -> dict[str, object]:
    graph = _multi_output_graph(source_ref, "clean.unselected_output", output_ref)
    nodes = cast(list[dict[str, object]], graph["nodes"])
    edges = cast(list[dict[str, object]], graph["edges"])
    nodes.pop(1)
    edges.pop(0)
    nodes.extend(
        [
            _node("media_source", "source", "source.media_set", {"mediaSetRef": "media.documents"}),
            _node("media_out", "output", "output.media_set", {"mediaSetRef": "media.processed"}),
        ]
    )
    edges.append(
        _edge(
            "media-output",
            "media_source",
            "media_out",
            source_port_id="media",
            target_port_id="media",
        )
    )
    return graph


def _node(
    node_id: str,
    kind: str,
    descriptor_id: str,
    config: Mapping[str, object],
) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": kind,
        "descriptorId": descriptor_id,
        "specVersion": 1,
        "config": dict(config),
    }


def _edge(
    edge_id: str,
    source_node_id: str,
    target_node_id: str,
    *,
    source_port_id: str = "dataset",
    target_port_id: str = "input",
) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source_node_id,
        "sourcePortId": source_port_id,
        "targetNodeId": target_node_id,
        "targetPortId": target_port_id,
    }


def _committed_output(node_id: str, dataset_ref: str, version_id: str) -> dict[str, object]:
    return {
        "nodeId": node_id,
        "artifactKind": "dataset_version",
        "plane": "dataset",
        "status": "COMMITTED",
        "commitKind": "SERVING_ASSET",
        "isServing": True,
        "ref": {"datasetRef": dataset_ref, "versionId": version_id},
    }


def _compiled_api_name(deployment: Mapping[str, object], node_id: str) -> str:
    compiled = cast(Mapping[str, object], deployment["compiled"])
    transforms = cast(list[Mapping[str, object]], compiled["transforms"])
    return str(next(item["apiName"] for item in transforms if item["nodeId"] == node_id))


def _only_dataset_version_id(fixture: _DeployedMultiOutputPipeline, dataset_ref: str) -> str:
    version_ids = _dataset_version_ids(fixture, dataset_ref)
    assert len(version_ids) == 1
    return version_ids[0]


def _dataset_version_ids(fixture: _DeployedMultiOutputPipeline, dataset_ref: str) -> list[str]:
    return _dataset_version_ids_for(fixture.foundry, fixture.ctx, dataset_ref)


def _dataset_version_ids_for(
    foundry: FoundryLite,
    ctx: RequestContext,
    dataset_ref: str,
) -> list[str]:
    namespace, name = dataset_ref.split(".", 1)
    with foundry.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        dataset_id = sql_conn.execute(
            select(db.datasets.c.id).where(
                db.datasets.c.tenant_id == ctx.tenant_id,
                db.datasets.c.namespace == namespace,
                db.datasets.c.name == name,
            )
        ).scalar_one_or_none()
        if dataset_id is None:
            return []
        rows = (
            sql_conn.execute(
                select(db.dataset_versions.c.id)
                .where(
                    db.dataset_versions.c.tenant_id == ctx.tenant_id,
                    db.dataset_versions.c.dataset_id == dataset_id,
                )
                .order_by(db.dataset_versions.c.version_number)
            )
            .scalars()
            .all()
        )
    return [str(version_id) for version_id in rows]


def _node_runs_by_id(run: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    rows = cast(list[dict[str, Any]], run["nodeRuns"])
    return {str(row["nodeId"]): row for row in rows}


def _pipeline_evidence_counts(
    fixture: _DeployedMultiOutputPipeline,
    run_id: str,
) -> tuple[int, int, int]:
    with fixture.foundry.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        node_count = sql_conn.execute(
            select(func.count())
            .select_from(db.pipeline_node_runs)
            .where(
                db.pipeline_node_runs.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        attempt_count = sql_conn.execute(
            select(func.count())
            .select_from(db.pipeline_node_attempts)
            .join(db.pipeline_node_runs, db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id)
            .where(
                db.pipeline_node_attempts.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        artifact_count = sql_conn.execute(
            select(func.count())
            .select_from(db.pipeline_run_artifacts)
            .where(
                db.pipeline_run_artifacts.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_run_artifacts.c.run_id == run_id,
            )
        ).scalar_one()
    return int(node_count), int(attempt_count), int(artifact_count)


def _ensure_classified_dataset(
    foundry: FoundryLite,
    ctx: RequestContext,
    dataset_ref: str,
    classification: str | None,
) -> None:
    if classification is None:
        foundry.datasets.ensure(dataset_ref, ctx=ctx)
        return
    foundry.datasets.create(dataset_ref, classification=classification, ctx=ctx)


def _force_transform_failure(
    monkeypatch: pytest.MonkeyPatch,
    fixture: _DeployedMultiOutputPipeline,
    node_id: str,
) -> None:
    failed_api_name = _compiled_api_name(fixture.deployment, node_id)
    transform_service = fixture.foundry._services.pipelines.run.transform_service
    original_execute = transform_service._execute_pipeline_transform_run

    def fail_selected(ctx: RequestContext, plan, *, after_transform_commit=None) -> CommitResult:
        if plan.definition_snapshot["api_name"] == failed_api_name:
            raise ValidationFailed("forced transform failure")
        return original_execute(ctx, plan, after_transform_commit=after_transform_commit)

    monkeypatch.setattr(transform_service, "_execute_pipeline_transform_run", fail_selected)


def _run_by_idempotency_key(
    fixture: _DeployedMultiOutputPipeline,
    idempotency_key: str,
) -> Mapping[str, object]:
    with fixture.foundry.engine.begin() as conn:
        row = fixture.foundry.pipeline_repository.run_by_idempotency_key(
            transaction=conn,
            tenant_id=fixture.ctx.tenant_id,
            idempotency_key=idempotency_key,
        )
    assert row is not None
    return row


def _pipeline_failure_audit_count(
    fixture: _DeployedMultiOutputPipeline,
    run_id: str,
) -> int:
    return _pipeline_audit_count(fixture, run_id, "pipeline.failed")


def _pipeline_audit_count(
    fixture: _DeployedMultiOutputPipeline,
    run_id: str,
    event_type: str,
) -> int:
    with fixture.foundry.engine.begin() as conn:
        count = (
            cast(Any, conn)
            .execute(
                select(func.count())
                .select_from(db.audit_events)
                .where(
                    db.audit_events.c.tenant_id == fixture.ctx.tenant_id,
                    db.audit_events.c.resource_id == run_id,
                    db.audit_events.c.event_type == event_type,
                )
            )
            .scalar_one()
        )
    return int(count)


def _expire_run_lease(fixture: _DeployedMultiOutputPipeline, run_id: str) -> None:
    with fixture.foundry.engine.begin() as conn:
        cast(Any, conn).execute(
            update(db.pipeline_runs)
            .where(
                db.pipeline_runs.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_runs.c.id == run_id,
            )
            .values(execution_lease_expires_at="2000-01-01T00:00:00Z")
        )
