from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse
from foundry_lite.application.ports.source_management_repository import (
    SourceSyncRecord,
    SourceSyncRunRecord,
)
from foundry_lite.application.primitives import _now
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.fake_language_model import FakeLanguageModel
from foundry_lite.infrastructure.adapters.model_media_resolver import RepositoryModelMediaResolver
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import delete, func, select, update


class _StructuredLanguageModel(FakeLanguageModel):
    def __init__(self, content: Mapping[str, object] | str) -> None:
        self._content = json.dumps(content, sort_keys=True) if isinstance(content, Mapping) else content
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            provider="structured-test",
            resolved_model_id="",
            resolved_model_revision="",
            content=self._content,
            finish_reason="stop",
            input_tokens=23,
            output_tokens=7,
            provider_request_id="structured-test-request",
        )


def test_graph_v2_stream_checkpoint_builds_reproducible_dataset_output(tmp_path: Path) -> None:
    foundry = _foundry_with_language_model(tmp_path, _StructuredLanguageModel({"unused": True}))
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_stream_orders"
    output_ref = "clean.graph_v2_stream_orders"
    version_id = _commit_dataset_source(foundry, ctx, tmp_path, source_ref)
    _register_stream_checkpoint(foundry, ctx, source_ref, version_id)
    graph = _stream_copy_graph("orders_live", output_ref)
    preview_branch = foundry.pipelines.create_branch(
        pipeline_id="graph_v2_stream_preview",
        name="preview",
        idempotency_key="branch-graph-v2-stream-preview",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(preview_branch["id"]),
        graph=graph,
        target_node_id="bridge",
        limits={"tableRows": 10},
        idempotency_key="preview-graph-v2-stream-orders",
        ctx=ctx,
    )
    preview = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)
    version = _execute_graph_version(foundry, ctx, pipeline_id="graph_v2_stream_orders", graph=graph)
    deployment = foundry.pipelines.deploy(
        "graph_v2_stream_orders",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-stream-orders",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_stream_orders",
        idempotency_key="run-graph-v2-stream-orders",
        ctx=ctx,
    )

    artifacts = _artifacts_by_node(run)
    assert deployment["compiled"]["executionKind"] == "pipeline_graph_v2"
    assert preview["outputs"][0]["servingVersionCreated"] is False
    assert preview["outputs"][0]["items"][0]["streamCheckpoint"] == {"partitionOffsets": {"0": 42}}
    assert run["status"] == "succeeded"
    assert artifacts["source"]["artifactKind"] == "stream_checkpoint"
    assert artifacts["source"]["artifactRef"]["checkpoint"] == {"partitionOffsets": {"0": 42}}
    assert artifacts["source"]["artifactRef"]["versionId"] == version_id
    assert artifacts["bridge"]["artifactKind"] == "dataset_version"
    assert foundry.datasets.preview(output_ref, ctx=ctx)[0]["order_id"] == "O-1"


def test_graph_v2_committed_output_survives_success_terminal_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry_with_language_model(tmp_path, _StructuredLanguageModel({"unused": True}))
    ctx = demo_admin_context()
    pipeline_id = "graph_v2_terminal_reconciliation"
    source_ref = "raw.graph_v2_terminal_reconciliation"
    output_ref = "clean.graph_v2_terminal_reconciliation"
    source_version_id = _commit_dataset_source(foundry, ctx, tmp_path, source_ref)
    _register_stream_checkpoint(foundry, ctx, source_ref, source_version_id)
    graph = _stream_copy_graph("orders_live", output_ref)
    version = _execute_graph_version(foundry, ctx, pipeline_id=pipeline_id, graph=graph)
    foundry.pipelines.deploy(
        pipeline_id,
        str(version["id"]),
        idempotency_key="deploy-graph-v2-terminal-reconciliation",
        ctx=ctx,
    )
    repository = foundry._services.pipelines.run.pipeline_repository
    original_terminal = repository.update_run_terminal

    def fail_success_terminal(*, transition, **kwargs):
        if transition.to_status == "succeeded":
            raise RuntimeError("forced Graph v2 success terminal persistence failure")
        return original_terminal(transition=transition, **kwargs)

    monkeypatch.setattr(repository, "update_run_terminal", fail_success_terminal)

    with pytest.raises(RuntimeError, match="Graph v2 terminal transaction failed"):
        foundry.pipelines.run(
            pipeline_id,
            idempotency_key="run-graph-v2-terminal-reconciliation",
            ctx=ctx,
        )

    with foundry.engine.begin() as transaction:
        row = repository.run_by_idempotency_key(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            idempotency_key="run-graph-v2-terminal-reconciliation",
        )
    assert row is not None
    detail = foundry.pipelines.get_run(str(row["id"]), ctx=ctx)
    assert detail["status"] == "running"
    assert (
        _artifacts_by_node(detail)["output"]["artifactRef"]["versionId"]
        == _dataset_version_ids(foundry, ctx, output_ref)[0]
    )
    replay = foundry.pipelines.run(
        pipeline_id,
        idempotency_key="run-graph-v2-terminal-reconciliation",
        ctx=ctx,
    )
    assert replay["status"] == "running"
    with foundry.engine.begin() as transaction:
        transaction.execute(
            delete(db.pipeline_run_artifacts).where(
                db.pipeline_run_artifacts.c.tenant_id == ctx.tenant_id,
                db.pipeline_run_artifacts.c.run_id == row["id"],
                db.pipeline_run_artifacts.c.node_id == "output",
            )
        )
        transaction.execute(
            update(db.pipeline_runs)
            .where(
                db.pipeline_runs.c.tenant_id == ctx.tenant_id,
                db.pipeline_runs.c.id == row["id"],
            )
            .values(execution_lease_expires_at="2000-01-01T00:00:00Z")
        )
    reconciled = foundry.pipelines.run(
        pipeline_id,
        idempotency_key="run-graph-v2-terminal-reconciliation",
        ctx=ctx,
    )
    output_version_id = _dataset_version_ids(foundry, ctx, output_ref)[0]
    assert reconciled["status"] == "partial"
    assert reconciled["outputDatasetRef"] == output_ref
    assert reconciled["outputVersionId"] == output_version_id
    assert reconciled["outputs"][0]["ref"]["versionId"] == output_version_id
    assert reconciled["outputs"][0]["ref"]["transactionId"]
    assert reconciled["outputs"][0]["manifest"]["metadata"]["pipelineRunId"] == row["id"]
    assert reconciled["outputs"][0]["artifactEvidence"]["status"] == "RECONCILIATION_REQUIRED"
    assert reconciled["outputs"][0]["artifactEvidence"]["recoverySource"] == "DATASET_TRANSACTION"
    assert "terminal evidence requires reconciliation" in reconciled["error"]["message"]
    assert len(_dataset_version_ids(foundry, ctx, output_ref)) == 1
    assert any(
        event["event_type"] == "pipeline.reconciliation_required" and event["resource_id"] == row["id"]
        for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    )


def test_graph_v2_dataset_artifact_evidence_failure_preserves_serving_commit_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry_with_language_model(
        tmp_path,
        _StructuredLanguageModel({"category": "payment", "risk": 1}),
    )
    ctx = demo_admin_context()
    pipeline_id = "graph_v2_dataset_evidence_reconciliation"
    source_ref = "raw.graph_v2_dataset_evidence_reconciliation"
    output_ref = "clean.graph_v2_dataset_evidence_reconciliation"
    _commit_dataset_source(foundry, ctx, tmp_path, source_ref)
    graph = _dataset_semantic_graph(source_ref, output_ref)
    version = _execute_graph_version(foundry, ctx, pipeline_id=pipeline_id, graph=graph)
    foundry.pipelines.deploy(pipeline_id, str(version["id"]), idempotency_key="deploy-evidence-failure", ctx=ctx)
    repository = foundry._services.pipelines.run.pipeline_execution_repository
    insert_artifact = repository.insert_artifact

    def fail_output_artifact(*, transaction: object, record: object):
        if getattr(record, "node_id", None) == "output":
            raise RuntimeError("injected dataset artifact evidence failure")
        return insert_artifact(transaction=transaction, record=record)

    monkeypatch.setattr(repository, "insert_artifact", fail_output_artifact)

    run = foundry.pipelines.run(pipeline_id, idempotency_key="run-evidence-failure", ctx=ctx)

    assert run["status"] == "partial", run
    assert run["error"]["type"] == "PIPELINE_OUTPUT_EVIDENCE_PERSISTENCE_FAILED"
    assert run["outputs"][0]["status"] == "COMMITTED"
    assert run["outputs"][0]["isServing"] is True
    assert run["outputs"][0]["ref"]["versionId"] == _dataset_version_ids(foundry, ctx, output_ref)[0]
    assert len(_dataset_version_ids(foundry, ctx, output_ref)) == 1


def test_graph_v2_geospatial_source_commits_governed_series_output(tmp_path: Path) -> None:
    foundry = _foundry_with_language_model(tmp_path, _StructuredLanguageModel({"unused": True}))
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_asset_locations"
    output_ref = "geo.graph_v2_asset_locations"
    source_version_id = _commit_geospatial_source(foundry, ctx, tmp_path, source_ref)
    graph = _geospatial_copy_graph(source_ref, output_ref)
    version = _execute_graph_version(foundry, ctx, pipeline_id="graph_v2_asset_locations", graph=graph)
    foundry.pipelines.deploy(
        "graph_v2_asset_locations",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-asset-locations",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_asset_locations",
        idempotency_key="run-graph-v2-asset-locations",
        ctx=ctx,
    )

    artifacts = _artifacts_by_node(run)
    assert run["status"] == "succeeded"
    assert artifacts["source"]["artifactKind"] == "geospatial_series"
    assert artifacts["source"]["artifactRef"]["versionId"] == source_version_id
    assert artifacts["output"]["artifactKind"] == "geospatial_series"
    assert artifacts["output"]["manifest"]["metadata"]["geospatialSpec"]["coordinateReferenceSystem"] == "EPSG:4326"
    rows = foundry.datasets.preview(output_ref, ctx=ctx)
    assert rows[0]["longitude"] == 127.0
    assert rows[0]["latitude"] == 37.5


def test_graph_v2_geojson_output_remains_readable_as_a_pinned_geospatial_source(
    tmp_path: Path,
) -> None:
    foundry = _foundry_with_language_model(tmp_path, _StructuredLanguageModel({"unused": True}))
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_geojson_source"
    first_output_ref = "geo.graph_v2_geojson_first"
    second_output_ref = "geo.graph_v2_geojson_second"
    _commit_geojson_source(foundry, ctx, source_ref)
    first_version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_geojson_first",
        graph=_geojson_copy_graph(source_ref, first_output_ref),
    )
    foundry.pipelines.deploy(
        "graph_v2_geojson_first",
        str(first_version["id"]),
        idempotency_key="deploy-graph-v2-geojson-first",
        ctx=ctx,
    )
    first = foundry.pipelines.run(
        "graph_v2_geojson_first",
        idempotency_key="run-graph-v2-geojson-first",
        ctx=ctx,
    )
    second_version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_geojson-second",
        graph=_geojson_copy_graph(first_output_ref, second_output_ref),
    )
    foundry.pipelines.deploy(
        "graph_v2_geojson-second",
        str(second_version["id"]),
        idempotency_key="deploy-graph-v2-geojson-second",
        ctx=ctx,
    )

    second = foundry.pipelines.run(
        "graph_v2_geojson-second",
        idempotency_key="run-graph-v2-geojson-second",
        ctx=ctx,
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert foundry.datasets.preview(second_output_ref, ctx=ctx)[0]["geometry"] == {
        "type": "Point",
        "coordinates": [127.0, 37.5],
    }


def test_geospatial_stale_recovery_deduplicates_dataset_transaction_and_passport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry_with_language_model(tmp_path, _StructuredLanguageModel({"unused": True}))
    ctx = demo_admin_context()
    pipeline_id = "graph_v2_geospatial_recovery"
    source_ref = "raw.graph_v2_geospatial_recovery"
    output_ref = "geo.graph_v2_geospatial_recovery"
    _commit_geospatial_source(foundry, ctx, tmp_path, source_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id=pipeline_id,
        graph=_geospatial_copy_graph(source_ref, output_ref),
    )
    foundry.pipelines.deploy(pipeline_id, str(version["id"]), idempotency_key="deploy-geo-recovery", ctx=ctx)
    repository = foundry._services.pipelines.run.pipeline_repository
    original_terminal = repository.update_run_terminal

    def fail_success_terminal(*, transition, **kwargs):
        if transition.to_status == "succeeded":
            raise RuntimeError("forced geospatial terminal persistence failure")
        return original_terminal(transition=transition, **kwargs)

    monkeypatch.setattr(repository, "update_run_terminal", fail_success_terminal)
    key = "run-geo-recovery"
    with pytest.raises(RuntimeError, match="Graph v2 terminal transaction failed"):
        foundry.pipelines.run(pipeline_id, idempotency_key=key, ctx=ctx)

    with foundry.engine.begin() as transaction:
        row = repository.run_by_idempotency_key(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            idempotency_key=key,
        )
        assert row is not None
        transaction.execute(
            update(db.pipeline_runs)
            .where(db.pipeline_runs.c.tenant_id == ctx.tenant_id, db.pipeline_runs.c.id == row["id"])
            .values(execution_lease_expires_at="2000-01-01T00:00:00Z")
        )

    reconciled = foundry.pipelines.run(pipeline_id, idempotency_key=key, ctx=ctx)

    assert reconciled["status"] == "partial"
    assert len(reconciled["outputs"]) == 1
    assert reconciled["outputs"][0]["artifactKind"] == "geospatial_series"
    assert reconciled["outputs"][0]["plane"] == "geospatial"
    assert reconciled["outputs"][0]["ref"]["resourceRef"] == output_ref
    assert reconciled["outputs"][0]["ref"]["artifactId"]
    assert "datasetRef" not in reconciled["outputs"][0]["ref"]


def test_geospatial_transaction_only_recovery_preserves_public_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry_with_language_model(tmp_path, _StructuredLanguageModel({"unused": True}))
    ctx = demo_admin_context()
    pipeline_id = "graph_v2_geospatial_transaction_recovery"
    source_ref = "raw.graph_v2_geospatial_transaction_recovery"
    output_ref = "geo.graph_v2_geospatial_transaction_recovery"
    _commit_geospatial_source(foundry, ctx, tmp_path, source_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id=pipeline_id,
        graph=_geospatial_copy_graph(source_ref, output_ref),
    )
    foundry.pipelines.deploy(pipeline_id, str(version["id"]), idempotency_key="deploy-geo-tx-recovery", ctx=ctx)
    execution_repository = foundry._services.pipelines.run.pipeline_execution_repository
    original_insert = execution_repository.insert_artifact

    class _SimulatedWorkerCrash(BaseException):
        pass

    def crash_before_output_passport(*, transaction: object, record: object):
        if getattr(record, "node_id", None) == "output":
            raise _SimulatedWorkerCrash
        return original_insert(transaction=transaction, record=record)

    monkeypatch.setattr(execution_repository, "insert_artifact", crash_before_output_passport)
    key = "run-geo-transaction-recovery"
    with pytest.raises(_SimulatedWorkerCrash):
        foundry.pipelines.run(pipeline_id, idempotency_key=key, ctx=ctx)
    repository = foundry._services.pipelines.run.pipeline_repository
    with foundry.engine.begin() as transaction:
        row = repository.run_by_idempotency_key(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            idempotency_key=key,
        )
        assert row is not None
        transaction.execute(
            update(db.pipeline_runs)
            .where(db.pipeline_runs.c.tenant_id == ctx.tenant_id, db.pipeline_runs.c.id == row["id"])
            .values(execution_lease_expires_at="2000-01-01T00:00:00Z")
        )

    reconciled = foundry.pipelines.run(pipeline_id, idempotency_key=key, ctx=ctx)
    output = cast(dict[str, Any], reconciled["outputs"][0])

    assert reconciled["status"] == "partial"
    assert output["artifactEvidence"]["recoverySource"] == "DATASET_TRANSACTION"
    assert "artifactId" not in output["ref"]
    assert output["manifest"]["resourceRef"] == output_ref
    assert output["manifest"]["rowCount"] == 1
    assert output["manifest"]["manifestUri"]
    assert output["manifest"]["schemaHash"]
    assert output["manifest"]["geospatialSpec"]["coordinateReferenceSystem"] == "EPSG:4326"
    assert "metadata" not in output["manifest"]


def test_graph_v2_dataset_llm_run_pins_source_and_model_evidence_without_duplicates(
    tmp_path: Path,
) -> None:
    adapter = _StructuredLanguageModel({"category": "payment", "risk": 2})
    foundry = _foundry_with_language_model(tmp_path, adapter)
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_semantic_orders"
    output_ref = "clean.graph_v2_semantic_orders"
    source_version_id = _commit_dataset_source(foundry, ctx, tmp_path, source_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_semantic_orders",
        graph=_dataset_semantic_graph(source_ref, output_ref),
    )
    deployment = foundry.pipelines.deploy(
        "graph_v2_semantic_orders",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-semantic-orders",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_semantic_orders",
        idempotency_key="run-graph-v2-semantic-orders",
        ctx=ctx,
    )
    evidence_counts = _evidence_counts(foundry, ctx, str(run["id"]))
    output_version_ids = _dataset_version_ids(foundry, ctx, output_ref)
    replay = foundry.pipelines.run(
        "graph_v2_semantic_orders",
        idempotency_key="run-graph-v2-semantic-orders",
        ctx=ctx,
    )

    assert deployment["compiled"]["executionKind"] == "pipeline_graph_v2"
    assert run["status"] == "succeeded"
    assert run["outputDatasetRef"] == output_ref
    assert run["outputVersionId"] == output_version_ids[0]
    assert len(output_version_ids) == 1
    assert replay["id"] == run["id"]
    assert replay["outputs"] == run["outputs"]
    assert _dataset_version_ids(foundry, ctx, output_ref) == output_version_ids
    assert _evidence_counts(foundry, ctx, str(run["id"])) == evidence_counts
    assert len(adapter.requests) == 2
    _assert_dataset_semantic_evidence(run, source_version_id, output_version_ids[0])
    rows = foundry.datasets.preview(output_ref, ctx=ctx)
    assert len(rows) == 2
    assert all(isinstance(row["analysis"], Mapping) for row in rows)
    assert all(set(row) == {"analysis", "memo", "order_id"} for row in rows)
    assert rows[0]["analysis"] == {"category": "payment", "risk": 2}
    output_metadata = _artifacts_by_node(run)["output"]["manifest"]["metadata"]
    row_evidence = cast(list[dict[str, object]], output_metadata["rowEvidence"])
    internal = cast(Mapping[str, object], row_evidence[0]["internalEvidence"])
    assert cast(Mapping[str, object], internal["_pipelineModelEvidence"])["promptVersionId"] == "order-risk@1"
    transaction_metadata = _dataset_transaction_metadata(foundry, ctx, output_version_ids[0])
    assert transaction_metadata["outputContract"] == {
        "columns": [
            {"name": "order_id", "type": "string"},
            {"name": "memo", "type": "string"},
            {"name": "analysis", "type": "object"},
        ],
        "mode": "declared",
    }
    assert transaction_metadata["rowEvidence"] == row_evidence


def test_graph_v2_distinct_run_reuses_successful_semantic_rows(tmp_path: Path) -> None:
    adapter = _StructuredLanguageModel({"category": "payment", "risk": 2})
    foundry = _foundry_with_language_model(tmp_path, adapter)
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_cached_orders"
    output_ref = "clean.graph_v2_cached_orders"
    _commit_dataset_source(foundry, ctx, tmp_path, source_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_cached_orders",
        graph=_dataset_semantic_graph(source_ref, output_ref),
    )
    deployment = foundry.pipelines.deploy(
        "graph_v2_cached_orders",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-cached-orders",
        ctx=ctx,
    )

    first = foundry.pipelines.run(
        "graph_v2_cached_orders",
        idempotency_key="run-graph-v2-cached-orders-a",
        ctx=ctx,
    )
    second = foundry.pipelines.run(
        "graph_v2_cached_orders",
        idempotency_key="run-graph-v2-cached-orders-b",
        ctx=ctx,
    )

    rows = foundry.datasets.preview(output_ref, ctx=ctx)
    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert len(adapter.requests) == 2
    assert _semantic_cache_count(foundry, ctx) == 2
    assert all("_pipelineModelEvidence" not in row for row in rows)
    output_metadata = _artifacts_by_node(second)["output"]["manifest"]["metadata"]
    evidence = [
        cast(Mapping[str, object], cast(Mapping[str, object], row["internalEvidence"])["_pipelineModelEvidence"])
        for row in cast(list[dict[str, object]], output_metadata["rowEvidence"])
    ]
    assert all(item["cacheHit"] is True for item in evidence)
    assert all(item["cacheScopeKind"] == "deployment" for item in evidence)
    deployment_payload = cast(Mapping[str, object], deployment["deployment"])
    assert all(item["cacheScopeId"] == deployment_payload["id"] for item in evidence)
    assert all(item["cacheNodeId"] == "semantic" for item in evidence)
    assert all(item["cacheGeneration"] == 1 for item in evidence)
    assert all(isinstance(item["resourceSecurityPolicyFingerprint"], str) for item in evidence)


def test_graph_v2_llm_failure_marks_output_failed_and_preserves_only_source_evidence(
    tmp_path: Path,
) -> None:
    adapter = _StructuredLanguageModel("not-json")
    foundry = _foundry_with_language_model(tmp_path, adapter)
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_semantic_failure"
    output_ref = "clean.graph_v2_semantic_failure"
    _commit_dataset_source(foundry, ctx, tmp_path, source_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_semantic_failure",
        graph=_dataset_semantic_graph(source_ref, output_ref),
    )
    foundry.pipelines.deploy(
        "graph_v2_semantic_failure",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-semantic-failure",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_semantic_failure",
        idempotency_key="run-graph-v2-semantic-failure",
        ctx=ctx,
    )
    nodes = _nodes_by_id(run)
    artifacts = _artifacts_by_node(run)

    assert run["status"] == "failed"
    assert run["outputDatasetRef"] is None
    assert run["outputVersionId"] is None
    assert run["outputs"][0]["nodeId"] == "output"
    assert run["outputs"][0]["status"] == "FAILED"
    assert run["outputs"][0]["ref"] == {"datasetRef": output_ref}
    assert _dataset_version_ids(foundry, ctx, output_ref) == []
    assert nodes["source"]["status"] == "succeeded"
    assert nodes["semantic"]["status"] == "failed"
    assert nodes["semantic"]["attempts"][0]["status"] == "failed"
    assert nodes["output"]["status"] == "skipped"
    assert nodes["output"]["attemptCount"] == 0
    assert set(artifacts) == {"source"}
    assert len(adapter.requests) == 2


def test_graph_v2_dataset_llm_rejects_source_classification_downgrade_before_egress(
    tmp_path: Path,
) -> None:
    adapter = _StructuredLanguageModel({"category": "payment", "risk": 2})
    foundry = _foundry_with_language_model(tmp_path, adapter)
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_confidential_orders"
    output_ref = "clean.graph_v2_confidential_orders"
    _commit_dataset_source(
        foundry,
        ctx,
        tmp_path,
        source_ref,
        classification="confidential",
    )
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_confidential_orders",
        graph=_dataset_semantic_graph(source_ref, output_ref),
    )
    foundry.pipelines.deploy(
        "graph_v2_confidential_orders",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-confidential-orders",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_confidential_orders",
        idempotency_key="run-graph-v2-confidential-orders",
        ctx=ctx,
    )

    nodes = _nodes_by_id(run)
    assert run["status"] == "failed"
    assert nodes["semantic"]["status"] == "failed"
    assert nodes["output"]["status"] == "skipped"
    assert _dataset_version_ids(foundry, ctx, output_ref) == []
    assert adapter.requests == []


def test_graph_v2_confidential_dataset_output_denies_viewer_serving_access(
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'pipeline-confidential-output.db'}",
            storage_root=tmp_path / "confidential-output",
        )
    )
    ctx = demo_admin_context()
    source_ref = "raw.graph_v2_confidential_copy"
    output_ref = "clean.graph_v2_confidential_copy"
    _commit_dataset_source(foundry, ctx, tmp_path, source_ref, classification="confidential")
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_confidential_copy",
        graph=_dataset_copy_graph(source_ref, output_ref),
    )
    foundry.pipelines.deploy(
        "graph_v2_confidential_copy",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-confidential-copy",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_confidential_copy",
        idempotency_key="run-graph-v2-confidential-copy",
        ctx=ctx,
    )
    viewer = RequestContext(actor_user_id="viewer-confidential", roles=("viewer",))

    assert run["status"] == "succeeded"
    with pytest.raises(PermissionDenied):
        foundry.datasets.preview(output_ref, ctx=viewer)
    admin_rows = foundry.datasets.preview(output_ref, ctx=ctx)
    assert admin_rows[0]["order_id"] == "O-1"
    assert all(set(row) == {"memo", "order_id"} for row in admin_rows)


def test_graph_v2_pdf_run_commits_extract_chunks_and_dataset_with_exact_media_pin(
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'pipeline-graph-v2-pdf.db'}",
            storage_root=tmp_path / "pdf-flite",
        )
    )
    ctx = demo_admin_context()
    media_ref = "legal.graph_v2_contracts"
    output_ref = "clean.graph_v2_contract_chunks"
    media_version_id = _commit_pdf(foundry, ctx, media_ref)
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_contract_chunks",
        graph=_pdf_graph(media_ref, media_version_id, output_ref),
    )
    foundry.pipelines.deploy(
        "graph_v2_contract_chunks",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-contract-chunks",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_contract_chunks",
        idempotency_key="run-graph-v2-contract-chunks",
        ctx=ctx,
    )
    output_version_ids = _dataset_version_ids(foundry, ctx, output_ref)
    evidence_counts = _evidence_counts(foundry, ctx, str(run["id"]))
    media_counts = _media_serving_counts(foundry, ctx)
    replay = foundry.pipelines.run(
        "graph_v2_contract_chunks",
        idempotency_key="run-graph-v2-contract-chunks",
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    assert len(output_version_ids) == 1
    assert run["outputVersionId"] == output_version_ids[0]
    assert replay["id"] == run["id"]
    assert _evidence_counts(foundry, ctx, str(run["id"])) == evidence_counts
    assert _dataset_version_ids(foundry, ctx, output_ref) == output_version_ids
    assert _media_serving_counts(foundry, ctx) == media_counts
    _assert_pdf_runtime_evidence(run, media_version_id, output_version_ids[0])
    rows = foundry.datasets.preview(output_ref, ctx=ctx)
    extracted_text = " ".join(str(row["text"]) for row in rows)
    assert "payment due" in extracted_text
    assert "thirty days" in extracted_text
    transaction_metadata = _dataset_transaction_metadata(foundry, ctx, output_version_ids[0])
    security_contract = cast(Mapping[str, object], transaction_metadata["securityContract"])
    assert security_contract["policyVersions"] == ["legal-policy-v3"]
    assert security_contract["allowedPrincipalSetIds"] == ["legal-contract-readers"]
    assert security_contract["principalMembershipEnforcement"] == "admin_only_without_resolver"
    assert security_contract["hasLegalHold"] is True
    engineer = RequestContext(actor_user_id="engineer-no-membership-resolver", roles=("data_engineer",))
    with pytest.raises(PermissionDenied, match="principal-set membership resolver"):
        foundry.datasets.preview(output_ref, ctx=engineer)


def test_graph_v2_document_extract_media_reference_matches_preview_and_resolver_truth(
    tmp_path: Path,
) -> None:
    adapter = _StructuredLanguageModel({"summary": "payment terms"})
    dependencies = _dependencies_with_language_model(tmp_path, adapter)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_ref = "legal.graph_v2_vision_contract"
    output_ref = "clean.graph_v2_vision_contract"
    media_version_id = _commit_pdf(foundry, ctx, media_ref)
    graph = _pdf_vision_graph(media_ref, media_version_id, output_ref)
    preview_branch = foundry.pipelines.create_branch(
        pipeline_id="graph_v2_vision_preview",
        name="preview",
        idempotency_key="branch-graph-v2-vision-preview",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(preview_branch["id"]),
        graph=graph,
        target_node_id="rows",
        limits={"pdfPages": 3, "tableRows": 20},
        idempotency_key="preview-graph-v2-vision",
        ctx=ctx,
    )
    preview = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)
    preview_reference = cast(dict[str, object], preview["outputs"][0]["items"][0]["mediaReference"])
    version = _execute_graph_version(
        foundry,
        ctx,
        pipeline_id="graph_v2_vision_contract",
        graph=graph,
    )
    foundry.pipelines.deploy(
        "graph_v2_vision_contract",
        str(version["id"]),
        idempotency_key="deploy-graph-v2-vision-contract",
        ctx=ctx,
    )

    run = foundry.pipelines.run(
        "graph_v2_vision_contract",
        idempotency_key="run-graph-v2-vision-contract",
        ctx=ctx,
    )
    reference = adapter.requests[0].messages[-1].media_references[0]
    production_reference = {
        "mediaItemVersionId": reference.media_item_version_id,
        "mimeType": reference.mime_type,
        "contentHash": reference.content_hash,
        "sourceLocator": dict(reference.source_locator),
    }
    resolver = RepositoryModelMediaResolver(
        dependencies.engine,
        dependencies.media_repository,
        dependencies.media_storage,
    )
    content = resolver.read(
        tenant_id=ctx.tenant_id,
        reference=reference,
        expected_classification="public",
        allowed_classifications=None,
    )

    assert run["status"] == "succeeded"
    assert production_reference == preview_reference
    assert production_reference["mediaItemVersionId"] == media_version_id
    assert content.media_item_version_id == media_version_id
    assert content.content.startswith(b"%PDF")
    extract_metadata = _artifacts_by_node(run)["extract"]["manifest"]["metadata"]
    extract_reference = cast(Mapping[str, object], extract_metadata["sourceMediaReferences"][0])
    assert extract_reference["mediaItemVersionId"] == production_reference["mediaItemVersionId"]
    assert extract_reference["mimeType"] == production_reference["mimeType"]
    assert extract_reference["contentHash"] == production_reference["contentHash"]
    production_locator = cast(Mapping[str, object], production_reference["sourceLocator"])
    extract_locator = cast(Mapping[str, object], extract_reference["sourceLocator"])
    assert production_locator.items() >= extract_locator.items()


def _foundry_with_language_model(
    tmp_path: Path,
    adapter: _StructuredLanguageModel,
) -> FoundryLite:
    return FoundryLite(dependencies=_dependencies_with_language_model(tmp_path, adapter))


def _dependencies_with_language_model(
    tmp_path: Path,
    adapter: _StructuredLanguageModel,
) -> CoreDependencies:
    base = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-graph-v2-semantic.db'}",
        storage_root=tmp_path / "semantic-flite",
    )
    dependencies = CoreDependencies(
        paths=base.paths,
        security=base.security,
        action=base.action,
        data=base.data,
        object_store=base.object_store,
        runtime=base.runtime,
        aip=base.aip,
        media=base.media,
        source=base.source,
        profile=base.profile,
        language_model_adapter=adapter,
    )
    return dependencies


def _commit_dataset_source(
    foundry: FoundryLite,
    ctx: RequestContext,
    tmp_path: Path,
    source_ref: str,
    *,
    classification: str = "public",
) -> str:
    source = tmp_path / "semantic-orders.csv"
    source.write_text(
        "order_id,memo\nO-1,payment due in thirty days\nO-2,payment overdue\n",
        encoding="utf-8",
    )
    foundry.datasets.create(source_ref, classification=classification, ctx=ctx)
    committed = foundry.datasets.upload_csv(source_ref, source, ctx=ctx)
    return committed.version_id


def _commit_geospatial_source(
    foundry: FoundryLite,
    ctx: RequestContext,
    tmp_path: Path,
    source_ref: str,
) -> str:
    source = tmp_path / "asset-locations.csv"
    source.write_text(
        "asset_id,longitude,latitude,event_time\nA-1,127.0,37.5,2026-07-17T00:00:00Z\n",
        encoding="utf-8",
    )
    foundry.datasets.create(source_ref, classification="internal", ctx=ctx)
    return foundry.datasets.upload_csv(source_ref, source, ctx=ctx).version_id


def _commit_geojson_source(
    foundry: FoundryLite,
    ctx: RequestContext,
    source_ref: str,
) -> str:
    foundry.datasets.create(source_ref, classification="internal", ctx=ctx)
    result = foundry._services.dataset.ingest.sync_rows_batch(
        source_ref,
        (
            {
                "asset_id": "A-1",
                "geometry": {"type": "Point", "coordinates": [127.0, 37.5]},
                "event_time": "2026-07-17T00:00:00Z",
            },
        ),
        fieldnames=("asset_id", "geometry", "event_time"),
        ctx=ctx,
        tx_type="SNAPSHOT",
    )
    assert result is not None
    return result.version_id


def _register_stream_checkpoint(
    foundry: FoundryLite,
    ctx: RequestContext,
    dataset_ref: str,
    version_id: str,
) -> None:
    now = _now()
    sync = SourceSyncRecord(
        id="ss-graph-v2-orders",
        tenant_id=ctx.tenant_id,
        sync_name="orders_live",
        source_name="orders-kafka",
        display_name="Orders Kafka",
        source_type="kafka",
        capability="streaming",
        target_dataset_ref=dataset_ref,
        target_media_set_id=None,
        mode="APPEND",
        schedule={"mode": "continuous"},
        config_summary={"topic": "orders", "consumerGroup": "pipeline-tests"},
        config_fingerprint="stream-config-v1",
        status="active",
        last_run_id="ssr-graph-v2-orders",
        last_workflow_run_id=None,
        checkpoint={"partitionOffsets": {"0": 42}},
        created_at=now,
        updated_at=now,
    )
    run = _stream_run_record(ctx, dataset_ref, version_id, now)
    with foundry.engine.begin() as conn:
        foundry.source_management_repository.create_sync(transaction=conn, record=sync)
        foundry.source_management_repository.create_sync_run(transaction=conn, record=run)


def _stream_run_record(
    ctx: RequestContext,
    dataset_ref: str,
    version_id: str,
    now: str,
) -> SourceSyncRunRecord:
    return SourceSyncRunRecord(
        id="ssr-graph-v2-orders",
        tenant_id=ctx.tenant_id,
        sync_name="orders_live",
        source_name="orders-kafka",
        source_type="kafka",
        capability="streaming",
        workflow_run_id=None,
        dataset_version_id=version_id,
        status="succeeded",
        trigger_type="continuous",
        idempotency_key="stream-run-orders-42",
        batch_limit=100,
        checkpoint_start={"partitionOffsets": {"0": 40}},
        checkpoint_end={"partitionOffsets": {"0": 42}},
        result_summary={"targetDatasetRef": dataset_ref, "eventCount": 2},
        error=None,
        operations_path="/operations/source-syncs/orders_live/runs/ssr-graph-v2-orders",
        started_at=now,
        completed_at=now,
        created_at=now,
    )


def _commit_pdf(
    foundry: FoundryLite,
    ctx: RequestContext,
    media_ref: str,
) -> str:
    namespace, name = media_ref.split(".", 1)
    media_set = foundry.media.create_media_set(
        ctx,
        namespace=namespace,
        name=name,
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="public",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="graph-v2-pdf-upload",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/contracts/acme.pdf",
        source=io.BytesIO(_make_pdf("Acme contract payment due thirty days after invoice receipt")),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={
            "tenantId": ctx.tenant_id,
            "classification": "public",
            "policyVersion": "legal-policy-v3",
            "allowedPrincipalSetId": "legal-contract-readers",
            "hasLegalHold": True,
        },
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _execute_graph_version(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    pipeline_id: str,
    graph: Mapping[str, object],
) -> Mapping[str, object]:
    branch = foundry.pipelines.create_branch(
        pipeline_id=pipeline_id,
        name="graph-v2-runtime",
        idempotency_key=f"branch-{pipeline_id}",
        ctx=ctx,
    )
    foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    foundry.pipelines.run_tests(str(branch["id"]), ctx=ctx)
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy Graph v2 runtime",
        idempotency_key=f"proposal-{pipeline_id}",
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


def _dataset_semantic_graph(source_ref: str, output_ref: str) -> dict[str, object]:
    return _graph(
        [
            _node("source", "source", "source.dataset", {"datasetRef": source_ref}),
            _node("semantic", "transform", "transform.use_llm", _semantic_config()),
            _node("output", "output", "output.dataset", {"outputDatasetRef": output_ref}),
        ],
        [
            _edge("source-semantic", "source", "dataset", "semantic", "input"),
            _edge("semantic-output", "semantic", "dataset", "output", "input"),
        ],
        output_columns=(("order_id", "string"), ("memo", "string"), ("analysis", "object")),
    )


def _dataset_copy_graph(source_ref: str, output_ref: str) -> dict[str, object]:
    return _graph(
        [
            _node("source", "source", "source.dataset", {"datasetRef": source_ref}),
            _node("output", "output", "output.dataset", {"outputDatasetRef": output_ref}),
        ],
        [_edge("source-output", "source", "dataset", "output", "input")],
        output_columns=(("order_id", "string"), ("memo", "string")),
    )


def _stream_copy_graph(source_ref: str, output_ref: str) -> dict[str, object]:
    return _graph(
        [
            _node("source", "source", "source.stream", {"sourceRef": source_ref}),
            _node("bridge", "transform", "bridge.stream_to_dataset", {}),
            _node("output", "output", "output.dataset", {"outputDatasetRef": output_ref}),
        ],
        [
            _edge("source-bridge", "source", "stream", "bridge", "stream"),
            _edge("bridge-output", "bridge", "dataset", "output", "input"),
        ],
        output_columns=(("memo", "string"), ("order_id", "string")),
    )


def _geospatial_copy_graph(source_ref: str, output_ref: str) -> dict[str, object]:
    fields = {"longitudeField": "longitude", "latitudeField": "latitude", "timeField": "event_time"}
    return _graph(
        [
            _node("source", "source", "source.geospatial", {"resourceRef": source_ref, **fields}),
            _node("output", "output", "output.geospatial", {"resourceRef": output_ref, **fields}),
        ],
        [_edge("source-output", "source", "series", "output", "input")],
    )


def _geojson_copy_graph(source_ref: str, output_ref: str) -> dict[str, object]:
    fields = {"geometryField": "geometry", "timeField": "event_time"}
    return _graph(
        [
            _node("source", "source", "source.geospatial", {"resourceRef": source_ref, **fields}),
            _node("output", "output", "output.geospatial", {"resourceRef": output_ref, **fields}),
        ],
        [_edge("source-output", "source", "series", "output", "input")],
    )


def _pdf_graph(
    media_ref: str,
    media_version_id: str,
    output_ref: str,
) -> dict[str, object]:
    return _graph(
        [
            _node(
                "media",
                "source",
                "source.media_set",
                {"mediaSetRef": media_ref, "mediaItemVersionIds": [media_version_id]},
            ),
            _node(
                "extract",
                "transform",
                "transform.document_extract",
                {"processorId": "pdf_text_v1@1"},
            ),
            _node("chunk", "transform", "transform.chunk", {"chunkSize": 5, "overlap": 1}),
            _node("rows", "transform", "bridge.content_units_to_dataset", {}),
            _node("output", "output", "output.dataset", {"outputDatasetRef": output_ref}),
        ],
        [
            _edge("media-extract", "media", "media", "extract", "media"),
            _edge("extract-chunk", "extract", "content", "chunk", "content"),
            _edge("chunk-rows", "chunk", "content", "rows", "content"),
            _edge("rows-output", "rows", "dataset", "output", "input"),
        ],
        output_columns=(("contentUnitId", "string"), ("text", "string"), ("mediaReference", "object")),
    )


def _pdf_vision_graph(
    media_ref: str,
    media_version_id: str,
    output_ref: str,
) -> dict[str, object]:
    semantic = {
        "modelAlias": "default-completion",
        "promptVersionId": "contract-vision@1",
        "promptMode": "basic_vision",
        "promptTemplate": "Summarize {{text}} using {{mediaReference}}.",
        "inputFields": ["text", "mediaReference"],
        "mediaReferenceField": "mediaReference",
        "outputColumn": "analysis",
        "outputSchema": {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
            "additionalProperties": False,
        },
        "dataClassification": "public",
        "outputMode": "simple",
    }
    return _graph(
        [
            _node(
                "media",
                "source",
                "source.media_set",
                {"mediaSetRef": media_ref, "mediaItemVersionIds": [media_version_id]},
            ),
            _node(
                "extract",
                "transform",
                "transform.document_extract",
                {"processorId": "pdf_text_v1@1"},
            ),
            _node("rows", "transform", "bridge.content_units_to_dataset", {}),
            _node("semantic", "transform", "transform.use_llm", semantic),
            _node("output", "output", "output.dataset", {"outputDatasetRef": output_ref}),
        ],
        [
            _edge("media-extract", "media", "media", "extract", "media"),
            _edge("extract-rows", "extract", "content", "rows", "content"),
            _edge("rows-semantic", "rows", "dataset", "semantic", "input"),
            _edge("semantic-output", "semantic", "dataset", "output", "input"),
        ],
        output_columns=(
            ("contentUnitId", "string"),
            ("text", "string"),
            ("mediaReference", "object"),
            ("analysis", "object"),
        ),
    )


def _semantic_config() -> dict[str, object]:
    return {
        "modelAlias": "default-completion",
        "expectedModelId": "local-fake-model",
        "expectedModelRevision": "2026-06-25",
        "promptVersionId": "order-risk@1",
        "promptTemplate": "Classify {{order_id}} from this memo: {{memo}}",
        "inputFields": ["order_id", "memo"],
        "outputColumn": "analysis",
        "outputSchema": {
            "type": "object",
            "required": ["category", "risk"],
            "properties": {
                "category": {"type": "string"},
                "risk": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "dataClassification": "public",
        "outputMode": "simple",
        "skipRecomputingRows": True,
        "modelParameters": {"temperature": 0, "maxOutputTokens": 128},
    }


def _graph(
    nodes: list[dict[str, object]],
    edges: list[dict[str, object]],
    *,
    output_columns: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": [{"name": name, "type": data_type} for name, data_type in output_columns]},
        "tests": [],
        "schedule": None,
    }


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


def _assert_dataset_semantic_evidence(
    run: Mapping[str, object],
    source_version_id: str,
    output_version_id: str,
) -> None:
    nodes = _nodes_by_id(run)
    artifacts = _artifacts_by_node(run)
    assert set(nodes) == {"source", "semantic", "output"}
    assert all(node["status"] == "succeeded" for node in nodes.values())
    assert all(node["attemptCount"] == 1 for node in nodes.values())
    assert nodes["source"]["attempts"][0]["executorProfile"] == "tabular_v1_compiler"
    assert nodes["semantic"]["attempts"][0]["executorProfile"] == "governed_model_gateway_runtime"
    assert nodes["output"]["attempts"][0]["executorProfile"] == "tabular_v1_compiler"
    assert nodes["semantic"]["inputArtifacts"][0]["artifactRef"]["versionId"] == source_version_id
    assert nodes["semantic"]["inputArtifacts"][0]["sourcePortId"] == "dataset"
    assert nodes["semantic"]["inputArtifacts"][0]["targetPortId"] == "input"
    assert artifacts["source"]["artifactRef"]["versionId"] == source_version_id
    assert artifacts["source"]["manifest"]["metadata"]["pins"]["versionPins"][0]["versionId"] == source_version_id
    semantic_pins = artifacts["semantic"]["manifest"]["metadata"]["pins"]
    assert semantic_pins["modelAlias"] == "default-completion"
    assert semantic_pins["expectedModelId"] == "local-fake-model"
    assert semantic_pins["expectedModelRevision"] == "2026-06-25"
    assert semantic_pins["promptVersionId"] == "order-risk@1"
    assert artifacts["semantic"]["isServing"] is False
    assert artifacts["output"]["artifactRef"]["versionId"] == output_version_id
    assert artifacts["output"]["isServing"] is True
    assert run["outputs"][0]["status"] == "COMMITTED"
    assert run["outputs"][0]["ref"]["versionId"] == output_version_id


def _assert_pdf_runtime_evidence(
    run: Mapping[str, object],
    media_version_id: str,
    output_version_id: str,
) -> None:
    nodes = _nodes_by_id(run)
    artifacts = _artifacts_by_node(run)
    assert set(nodes) == {"media", "extract", "chunk", "rows", "output"}
    assert all(node["status"] == "succeeded" for node in nodes.values())
    assert nodes["media"]["attempts"][0]["executorProfile"] == "media_pipeline_runtime"
    assert nodes["extract"]["attempts"][0]["executorProfile"] == "media_processor_registry"
    assert nodes["chunk"]["attempts"][0]["executorProfile"] == "content_pipeline_runtime"
    assert nodes["rows"]["attempts"][0]["executorProfile"] == "multimodal_bridge_runtime"
    assert nodes["output"]["attempts"][0]["executorProfile"] == "tabular_v1_compiler"
    assert artifacts["media"]["artifactRef"]["mediaItemVersionIds"] == [media_version_id]
    source_pins = artifacts["media"]["manifest"]["metadata"]["pins"]["versionPins"]
    assert source_pins[0]["versionId"] == media_version_id
    extract_pins = artifacts["extract"]["manifest"]["metadata"]["pins"]
    assert extract_pins["processorId"] == "pdf_text_v1@1"
    assert extract_pins["model"]["name"] == "pypdf"
    chunk_pins = artifacts["chunk"]["manifest"]["metadata"]["pins"]
    assert chunk_pins["processorId"] == "content_chunk_v1@1.0.0"
    assert artifacts["extract"]["isServing"] is True
    assert artifacts["chunk"]["isServing"] is True
    assert artifacts["rows"]["isServing"] is False
    assert artifacts["output"]["artifactRef"]["versionId"] == output_version_id
    assert run["outputs"][0]["status"] == "COMMITTED"
    assert run["outputs"][0]["ref"]["versionId"] == output_version_id


def _nodes_by_id(run: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    return {str(row["nodeId"]): row for row in cast(list[dict[str, Any]], run["nodeRuns"])}


def _artifacts_by_node(run: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    return {str(row["nodeId"]): row for row in cast(list[dict[str, Any]], run["artifacts"])}


def _dataset_version_ids(
    foundry: FoundryLite,
    ctx: RequestContext,
    dataset_ref: str,
) -> list[str]:
    namespace, name = dataset_ref.split(".", 1)
    with foundry.engine.begin() as conn:
        rows = (
            cast(Any, conn)
            .execute(
                select(db.dataset_versions.c.id)
                .join(db.datasets, db.datasets.c.id == db.dataset_versions.c.dataset_id)
                .where(
                    db.dataset_versions.c.tenant_id == ctx.tenant_id,
                    db.datasets.c.namespace == namespace,
                    db.datasets.c.name == name,
                )
                .order_by(db.dataset_versions.c.version_number)
            )
            .scalars()
            .all()
        )
    return [str(row) for row in rows]


def _dataset_transaction_metadata(
    foundry: FoundryLite,
    ctx: RequestContext,
    version_id: str,
) -> dict[str, object]:
    with foundry.engine.begin() as conn:
        metadata = conn.execute(
            select(db.dataset_transactions.c.metadata)
            .join(
                db.dataset_versions,
                db.dataset_versions.c.transaction_id == db.dataset_transactions.c.id,
            )
            .where(
                db.dataset_versions.c.tenant_id == ctx.tenant_id,
                db.dataset_versions.c.id == version_id,
            )
        ).scalar_one()
    return dict(metadata)


def _evidence_counts(
    foundry: FoundryLite,
    ctx: RequestContext,
    run_id: str,
) -> tuple[int, int, int]:
    with foundry.engine.begin() as conn:
        sql = cast(Any, conn)
        nodes = sql.execute(
            select(func.count())
            .select_from(db.pipeline_node_runs)
            .where(
                db.pipeline_node_runs.c.tenant_id == ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        attempts = sql.execute(
            select(func.count())
            .select_from(db.pipeline_node_attempts)
            .join(db.pipeline_node_runs, db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id)
            .where(
                db.pipeline_node_runs.c.tenant_id == ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        artifacts = sql.execute(
            select(func.count())
            .select_from(db.pipeline_run_artifacts)
            .where(
                db.pipeline_run_artifacts.c.tenant_id == ctx.tenant_id,
                db.pipeline_run_artifacts.c.run_id == run_id,
            )
        ).scalar_one()
    return int(nodes), int(attempts), int(artifacts)


def _media_serving_counts(
    foundry: FoundryLite,
    ctx: RequestContext,
) -> tuple[int, int]:
    with foundry.engine.begin() as conn:
        sql = cast(Any, conn)
        derivatives = sql.execute(
            select(func.count())
            .select_from(db.media_derivatives)
            .where(
                db.media_derivatives.c.tenant_id == ctx.tenant_id,
                db.media_derivatives.c.status == "COMMITTED",
            )
        ).scalar_one()
        content_units = sql.execute(
            select(func.count())
            .select_from(db.content_units)
            .where(
                db.content_units.c.tenant_id == ctx.tenant_id,
            )
        ).scalar_one()
    return int(derivatives), int(content_units)


def _semantic_cache_count(foundry: FoundryLite, ctx: RequestContext) -> int:
    with foundry.engine.begin() as conn:
        count = (
            cast(Any, conn)
            .execute(
                select(func.count())
                .select_from(db.pipeline_semantic_row_cache)
                .where(db.pipeline_semantic_row_cache.c.tenant_id == ctx.tenant_id)
            )
            .scalar_one()
        )
    return int(count)


def _make_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    output = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return output + f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
