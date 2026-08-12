from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.pipeline_candidate_output_committer import (
    GovernedPipelineCandidateCommitter,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select


@dataclass(frozen=True)
class _GovernedOutputFixture:
    foundry: FoundryLite
    ctx: RequestContext
    pipeline_id: str
    source_ref: str
    dataset_ref: str
    index_ref: str
    mapping_ref: str
    source_version_id: str


def test_pipeline_commits_dataset_and_non_serving_governed_output_candidates(tmp_path: Path) -> None:
    fixture = _deploy_governed_output_pipeline(tmp_path, "candidate_success")
    ontology_count_before = _ontology_version_count(fixture)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-candidate-success",
        ctx=fixture.ctx,
    )
    outputs = cast(list[dict[str, Any]], run["outputs"])
    artifacts = cast(list[dict[str, Any]], run["artifacts"])
    artifacts_by_node = {str(item["nodeId"]): item for item in artifacts}

    assert run["status"] == "succeeded"
    assert [(item["nodeId"], item["commitKind"], item["isServing"]) for item in outputs] == [
        ("output_dataset", "SERVING_ASSET", True),
        ("output_index", "GOVERNED_CANDIDATE", False),
        ("output_ontology", "GOVERNED_CANDIDATE", False),
    ]
    assert run["outputDatasetRef"] is None
    assert run["outputVersionId"] is None
    assert set(artifacts_by_node) == {"source", "output_dataset", "output_index", "output_ontology"}
    assert artifacts_by_node["output_dataset"]["isServing"] is True
    _assert_candidate_artifact(
        artifacts_by_node["output_index"],
        artifact_kind="vector_index_generation",
        candidate_type="semantic_index_generation",
        resource_ref=fixture.index_ref,
    )
    _assert_candidate_artifact(
        artifacts_by_node["output_ontology"],
        artifact_kind="ontology_mapping",
        candidate_type="ontology_mapping",
        resource_ref=fixture.mapping_ref,
    )
    _assert_typed_manifests(artifacts)
    assert _ontology_version_count(fixture) == ontology_count_before
    assert _candidate_lineage(fixture, str(run["id"])) == {
        ("dataset_version", "vector_index_generation"),
        ("dataset_version", "ontology_mapping"),
    }
    assert _candidate_event_counts(fixture, str(run["id"])) == (2, 2)

    evidence_counts = _evidence_counts(fixture, str(run["id"]))
    dataset_version_count = _dataset_version_count(fixture)
    replay = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-candidate-success",
        ctx=fixture.ctx,
    )
    assert replay["id"] == run["id"]
    assert replay["outputs"] == run["outputs"]
    assert _evidence_counts(fixture, str(run["id"])) == evidence_counts
    assert _dataset_version_count(fixture) == dataset_version_count
    assert _candidate_event_counts(fixture, str(run["id"])) == (2, 2)


def test_targeted_candidate_only_run_never_creates_a_serving_output_dataset(tmp_path: Path) -> None:
    fixture = _deploy_governed_output_pipeline(tmp_path, "candidate_only")

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-candidate-only",
        target_node_ids=["output_index", "output_ontology"],
        ctx=fixture.ctx,
    )
    outputs = cast(list[dict[str, Any]], run["outputs"])
    artifacts = cast(list[dict[str, Any]], run["artifacts"])

    assert run["status"] == "succeeded"
    assert [(item["nodeId"], item["isServing"]) for item in outputs] == [
        ("output_index", False),
        ("output_ontology", False),
    ]
    assert run["outputDatasetRef"] is None
    assert run["outputVersionId"] is None
    assert _dataset_version_count(fixture) == 0
    assert {item["nodeId"] for item in artifacts} == {"source", "output_index", "output_ontology"}
    assert all(item["isServing"] is False for item in artifacts if item["nodeId"] != "source")
    assert {item["nodeId"] for item in cast(list[dict[str, Any]], run["nodeRuns"])} == {
        "source",
        "output_index",
        "output_ontology",
    }


def test_candidate_only_run_uses_deployment_pinned_source_after_new_commit(tmp_path: Path) -> None:
    fixture = _deploy_governed_output_pipeline(tmp_path, "candidate_pinned_source")
    newer_csv = tmp_path / "candidate_pinned_source_newer.csv"
    newer_csv.write_text("document_id,title\nD-3,Newer\n", encoding="utf-8")
    newer = fixture.foundry.datasets.upload_csv(fixture.source_ref, newer_csv, ctx=fixture.ctx)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-candidate-pinned-source",
        target_node_ids=["output_index"],
        ctx=fixture.ctx,
    )
    source = next(item for item in cast(list[dict[str, Any]], run["artifacts"]) if item["nodeId"] == "source")

    assert source["artifactRef"]["versionId"] == fixture.source_version_id
    assert source["artifactRef"]["versionId"] != newer.version_id


def test_pipeline_is_partial_when_later_governed_candidate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _deploy_governed_output_pipeline(tmp_path, "candidate_partial")
    original_commit = GovernedPipelineCandidateCommitter.commit

    def fail_ontology(
        committer: GovernedPipelineCandidateCommitter,
        item: Mapping[str, object],
    ):
        if item.get("descriptorId") == "output.ontology":
            raise ValidationFailed("forced Ontology candidate validation failure")
        return original_commit(committer, item)

    monkeypatch.setattr(GovernedPipelineCandidateCommitter, "commit", fail_ontology)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-candidate-partial",
        ctx=fixture.ctx,
    )
    outputs = cast(list[dict[str, Any]], run["outputs"])
    artifacts = cast(list[dict[str, Any]], run["artifacts"])
    node_runs = {str(item["nodeId"]): item for item in cast(list[dict[str, Any]], run["nodeRuns"])}

    assert run["status"] == "partial"
    assert [(item["nodeId"], item["status"]) for item in outputs] == [
        ("output_dataset", "COMMITTED"),
        ("output_index", "COMMITTED"),
        ("output_ontology", "FAILED"),
    ]
    assert outputs[1]["commitKind"] == "GOVERNED_CANDIDATE"
    assert outputs[1]["isServing"] is False
    assert outputs[2]["ref"] == {
        "requestedResourceRef": fixture.mapping_ref,
        "servingState": "CANDIDATE_NOT_COMMITTED",
        "servingAssetCreated": False,
    }
    assert outputs[2]["error"]["executionDisposition"] == "FAILED"
    assert {item["nodeId"] for item in artifacts} == {"source", "output_dataset", "output_index"}
    index_artifact = next(item for item in artifacts if item["nodeId"] == "output_index")
    assert index_artifact["isServing"] is False
    assert node_runs["output_dataset"]["status"] == "succeeded"
    assert node_runs["output_index"]["status"] == "succeeded"
    assert node_runs["output_ontology"]["status"] == "failed"
    assert node_runs["output_ontology"]["attemptCount"] == 1
    assert _candidate_lineage(fixture, str(run["id"])) == {("dataset_version", "vector_index_generation")}
    assert _candidate_event_counts(fixture, str(run["id"])) == (1, 1)
    assert _ontology_version_count(fixture) == 0


def _deploy_governed_output_pipeline(tmp_path: Path, slug: str) -> _GovernedOutputFixture:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    source_ref = f"raw.{slug}"
    dataset_ref = f"clean.{slug}"
    csv_path = tmp_path / f"{slug}.csv"
    csv_path.write_text("document_id,title\nD-1,First\nD-2,Second\n", encoding="utf-8")
    foundry.datasets.create(source_ref, classification="CONFIDENTIAL", ctx=ctx)
    foundry.datasets.create(dataset_ref, classification="CONFIDENTIAL", ctx=ctx)
    source_version = foundry.datasets.upload_csv(source_ref, csv_path, ctx=ctx)
    fixture = _GovernedOutputFixture(
        foundry=foundry,
        ctx=ctx,
        pipeline_id=f"governed_outputs_{slug}",
        source_ref=source_ref,
        dataset_ref=dataset_ref,
        index_ref=f"search.{slug}",
        mapping_ref=f"DocumentMapping_{slug}",
        source_version_id=source_version.version_id,
    )
    version = _execute_graph_version(fixture, _governed_output_graph(fixture))
    foundry.pipelines.deploy(
        fixture.pipeline_id,
        str(version["id"]),
        idempotency_key=f"deploy-{slug}",
        ctx=ctx,
    )
    return fixture


def _execute_graph_version(
    fixture: _GovernedOutputFixture,
    graph: Mapping[str, object],
) -> Mapping[str, object]:
    branch = fixture.foundry.pipelines.create_branch(
        pipeline_id=fixture.pipeline_id,
        name="governed-output-candidates",
        idempotency_key=f"branch-{fixture.pipeline_id}",
        ctx=fixture.ctx,
    )
    fixture.foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=fixture.ctx,
    )
    fixture.foundry.pipelines.run_tests(str(branch["id"]), ctx=fixture.ctx)
    proposal = fixture.foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy governed output candidates",
        idempotency_key=f"proposal-{fixture.pipeline_id}",
        ctx=fixture.ctx,
    )
    reviewer_ctx = _reviewer_context(fixture.ctx)
    fixture.foundry.pipelines.assign(
        str(proposal["id"]),
        assignee_user_id=reviewer_ctx.actor_user_id,
        ctx=fixture.ctx,
    )
    fixture.foundry.pipelines.approve(str(proposal["id"]), ctx=reviewer_ctx)
    return fixture.foundry.pipelines.execute(str(proposal["id"]), ctx=fixture.ctx)


def _reviewer_context(ctx: RequestContext) -> RequestContext:
    return RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=f"{ctx.actor_user_id}-reviewer",
        roles=ctx.roles,
    )


def _governed_output_graph(fixture: _GovernedOutputFixture) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            _node("source", "source", "source.dataset", datasetRef=fixture.source_ref),
            _node("output_dataset", "output", "output.dataset", outputDatasetRef=fixture.dataset_ref),
            _node("output_index", "output", "output.semantic_index", indexRef=fixture.index_ref),
            _node("output_ontology", "output", "output.ontology", mappingRef=fixture.mapping_ref),
        ],
        "edges": [
            _edge("source-dataset", "source", "dataset", "output_dataset", "input"),
            _edge("source-index", "source", "dataset", "output_index", "index"),
            _edge("source-ontology", "source", "dataset", "output_ontology", "input"),
        ],
        "layout": {},
        "outputContract": {
            "columns": [
                {"name": "document_id", "type": "string", "nullable": False},
                {"name": "title", "type": "string", "nullable": False},
            ]
        },
        "tests": [],
        "schedule": None,
    }


def _node(node_id: str, kind: str, descriptor_id: str, **config: object) -> dict[str, object]:
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
    source_port_id: str,
    target_node_id: str,
    target_port_id: str,
) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source_node_id,
        "sourcePortId": source_port_id,
        "targetNodeId": target_node_id,
        "targetPortId": target_port_id,
    }


def _assert_candidate_artifact(
    artifact: Mapping[str, Any],
    *,
    artifact_kind: str,
    candidate_type: str,
    resource_ref: str,
) -> None:
    manifest = cast(Mapping[str, Any], artifact["manifest"])
    metadata = cast(Mapping[str, Any], manifest["metadata"])
    candidate = cast(Mapping[str, Any], metadata["candidate"])
    lifecycle = cast(list[Mapping[str, Any]], metadata["lifecycle"])
    assert artifact["artifactKind"] == artifact_kind
    assert artifact["isServing"] is False
    assert artifact["artifactRef"]["servingState"] == "CANDIDATE_ONLY"
    assert candidate["candidateType"] == candidate_type
    assert candidate["requestedResourceRef"] == resource_ref
    assert candidate["servingAssetCreated"] is False
    assert candidate["promotionRequired"] is True
    assert [item["status"] for item in lifecycle] == ["STAGED", "VALIDATED", "COMMITTED"]


def _assert_typed_manifests(artifacts: list[Mapping[str, Any]]) -> None:
    for artifact in artifacts:
        manifest = cast(Mapping[str, Any], artifact["manifest"])
        assert manifest["manifestVersion"] == 1
        assert manifest["contentFingerprint"] == artifact["contentFingerprint"]
        assert manifest["artifact"]["producerNodeId"] == artifact["nodeId"]
        assert manifest["artifact"]["producerPortId"] == artifact["portId"]
        assert manifest["securityMarkings"] == ["CONFIDENTIAL"]
        assert artifact["securityEnvelope"]["classification"] == "CONFIDENTIAL"


def _ontology_version_count(fixture: _GovernedOutputFixture) -> int:
    with fixture.foundry.engine.begin() as conn:
        count = (
            cast(Any, conn)
            .execute(
                select(func.count())
                .select_from(db.ontology_versions)
                .where(db.ontology_versions.c.tenant_id == fixture.ctx.tenant_id)
            )
            .scalar_one()
        )
    return int(count)


def _dataset_version_count(fixture: _GovernedOutputFixture) -> int:
    namespace, name = fixture.dataset_ref.split(".", 1)
    with fixture.foundry.engine.begin() as conn:
        count = (
            cast(Any, conn)
            .execute(
                select(func.count())
                .select_from(db.dataset_versions)
                .join(db.datasets, db.datasets.c.id == db.dataset_versions.c.dataset_id)
                .where(
                    db.dataset_versions.c.tenant_id == fixture.ctx.tenant_id,
                    db.datasets.c.namespace == namespace,
                    db.datasets.c.name == name,
                )
            )
            .scalar_one()
        )
    return int(count)


def _candidate_lineage(
    fixture: _GovernedOutputFixture,
    run_id: str,
) -> set[tuple[str, str]]:
    with fixture.foundry.engine.begin() as conn:
        rows = (
            cast(Any, conn)
            .execute(
                select(
                    db.lineage_edges.c.from_resource_type,
                    db.lineage_edges.c.to_resource_type,
                ).where(
                    db.lineage_edges.c.tenant_id == fixture.ctx.tenant_id,
                    db.lineage_edges.c.created_by_run_id == run_id,
                    db.lineage_edges.c.relation == "pipeline_candidate_input_to",
                )
            )
            .all()
        )
    return {(str(row[0]), str(row[1])) for row in rows}


def _candidate_event_counts(
    fixture: _GovernedOutputFixture,
    run_id: str,
) -> tuple[int, int]:
    with fixture.foundry.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        outbox_count = sql_conn.execute(
            select(func.count())
            .select_from(db.outbox_events)
            .where(
                db.outbox_events.c.tenant_id == fixture.ctx.tenant_id,
                db.outbox_events.c.event_type == "pipeline.output_candidate.committed",
                db.outbox_events.c.payload["runId"].as_string() == run_id,
            )
        ).scalar_one()
        audit_count = sql_conn.execute(
            select(func.count())
            .select_from(db.audit_events)
            .where(
                db.audit_events.c.tenant_id == fixture.ctx.tenant_id,
                db.audit_events.c.event_type == "pipeline.output_candidate.committed",
            )
        ).scalar_one()
    return int(outbox_count), int(audit_count)


def _evidence_counts(
    fixture: _GovernedOutputFixture,
    run_id: str,
) -> tuple[int, int, int, int]:
    with fixture.foundry.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        node_count = _count_for_run(sql_conn, db.pipeline_node_runs, run_id)
        artifact_count = _count_for_run(sql_conn, db.pipeline_run_artifacts, run_id)
        attempt_count = sql_conn.execute(
            select(func.count())
            .select_from(db.pipeline_node_attempts)
            .join(db.pipeline_node_runs, db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id)
            .where(
                db.pipeline_node_runs.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        lineage_count = sql_conn.execute(
            select(func.count())
            .select_from(db.lineage_edges)
            .where(
                db.lineage_edges.c.tenant_id == fixture.ctx.tenant_id,
                db.lineage_edges.c.created_by_run_id == run_id,
            )
        ).scalar_one()
    return int(node_count), int(attempt_count), int(artifact_count), int(lineage_count)


def _count_for_run(sql_conn: Any, table: Any, run_id: str) -> int:
    return int(sql_conn.execute(select(func.count()).select_from(table).where(table.c.run_id == run_id)).scalar_one())
