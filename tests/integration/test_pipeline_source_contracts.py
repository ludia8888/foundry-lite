from __future__ import annotations

import io
from pathlib import Path
from typing import cast

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext, demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


def test_deployment_pins_each_exact_dataset_version_and_ignores_draft_schema(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    first_version = _upload_orders(foundry, tmp_path, ctx, "first", "O-1,10\n")
    branch = foundry.pipelines.create_branch(
        pipeline_id="source-contract-pipeline",
        name="main",
        idempotency_key="source-contract-branch",
        ctx=ctx,
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_dataset_graph(),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )

    validation = foundry.pipelines.validate(str(branch["id"]), ctx=ctx)
    version = _approved_version(foundry, str(branch["id"]), ctx)
    first_deployment = foundry.pipelines.deploy(
        "source-contract-pipeline",
        str(version["id"]),
        idempotency_key="source-contract-deploy-v1",
        ctx=ctx,
    )
    second_version = _upload_orders(foundry, tmp_path, ctx, "second", "O-2,20\n")
    second_deployment = foundry.pipelines.deploy(
        "source-contract-pipeline",
        str(version["id"]),
        idempotency_key="source-contract-deploy-v2",
        ctx=ctx,
    )

    first_plan = _execution_plan(first_deployment)
    second_plan = _execution_plan(second_deployment)
    first_contract = _source_contract(first_plan)
    second_contract = _source_contract(second_plan)
    persisted = foundry.pipelines.list_deployments("source-contract-pipeline", ctx=ctx)
    persisted_plans = [
        cast(dict[str, object], item["executionPlan"]) for item in cast(list[dict[str, object]], persisted["items"])
    ]

    assert validation["valid"] is True
    assert validation["fingerprint"] == updated["graphFingerprint"]
    assert validation["warnings"][-1]["code"] == "source_schema_drift"
    assert validation["sourceContracts"][0]["versionPins"][0]["versionId"] == first_version.version_id
    assert first_contract["versionPins"][0]["versionId"] == first_version.version_id
    assert second_contract["versionPins"][0]["versionId"] == second_version.version_id
    assert first_contract["schemaContract"]["columns"][0]["name"] == "order_id"
    assert first_contract["securityEnvelope"]["classification"] == "INTERNAL"
    assert first_contract["accessEvidence"]["tenantId"] == ctx.tenant_id
    assert "schema" not in first_plan["nodes"][0]["config"]
    assert first_plan["planFingerprint"] != second_plan["planFingerprint"]
    assert {_source_contract(plan)["versionPins"][0]["versionId"] for plan in persisted_plans} == {
        first_version.version_id,
        second_version.version_id,
    }


def test_media_validation_pins_committed_version_and_rejects_staged_selection(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    committed_id = _upload_media(foundry, ctx, media_set.media_set_id, "committed", is_committed=True)
    staged_id = _upload_media(foundry, ctx, media_set.media_set_id, "staged", is_committed=False)
    committed_branch = _media_branch(foundry, ctx, "committed-media", committed_id)
    staged_branch = _media_branch(foundry, ctx, "staged-media", staged_id)

    committed = foundry.pipelines.validate(str(committed_branch["id"]), ctx=ctx)
    staged = foundry.pipelines.validate(str(staged_branch["id"]), ctx=ctx)
    contract = committed["sourceContracts"][0]

    assert committed["valid"] is True
    assert contract["versionPins"][0]["versionId"] == committed_id
    assert contract["securityEnvelope"]["classification"] == "CONFIDENTIAL"
    assert contract["schemaContract"]["schemaType"] == "document"
    assert staged["valid"] is False
    assert staged["errors"][-1]["code"] == "source_has_no_committed_version"
    assert staged["sourceContracts"] == []


def test_multimodal_deployment_returns_engine_neutral_plan_summary(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="deployment_contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    committed_id = _upload_media(foundry, ctx, media_set.media_set_id, "deployment", is_committed=True)
    branch = foundry.pipelines.create_branch(
        pipeline_id="multimodal-deployment",
        name="main",
        idempotency_key="multimodal-deployment-branch",
        ctx=ctx,
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_media_semantic_graph(committed_id),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    version = _approved_version(foundry, str(updated["id"]), ctx)

    deployed = foundry.pipelines.deploy(
        "multimodal-deployment",
        str(version["id"]),
        idempotency_key="multimodal-deployment-v1",
        ctx=ctx,
    )

    plan = _execution_plan(deployed)
    compiled = cast(dict[str, object], deployed["compiled"])
    contract = _source_contract(plan)
    assert contract["versionPins"][0]["versionId"] == committed_id
    assert compiled == {
        "pipelineId": "multimodal-deployment",
        "versionId": version["id"],
        "executionKind": "pipeline_graph_v2",
        "engineNeutral": True,
        "graphSchemaVersion": 2,
        "planFingerprint": plan["planFingerprint"],
        "runtimeCapabilities": [
            "governed_model_gateway_runtime",
            "media_pipeline_runtime",
            "multimodal_bridge_runtime",
            "tabular_v1_compiler",
        ],
        "nodeCount": 4,
        "edgeCount": 3,
        "artifactCount": 4,
        "resultArtifactIds": ["artifact:out:dataset"],
    }
    assert "transforms" not in compiled


def test_pipeline_source_validation_cannot_cross_tenant_by_logical_ref(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    owner_ctx = demo_admin_context()
    _upload_orders(foundry, tmp_path, owner_ctx, "owner", "O-1,10\n")
    other_ctx = RequestContext(
        tenant_id="tenant-other",
        actor_user_id="other-engineer",
        roles=DEMO_ADMIN_ROLES,
    )
    branch = foundry.pipelines.create_branch(
        pipeline_id="tenant-isolation-source-contract",
        name="main",
        idempotency_key="tenant-isolation-source-contract-branch",
        ctx=other_ctx,
    )
    foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_dataset_graph(),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=other_ctx,
    )

    validation = foundry.pipelines.validate(str(branch["id"]), ctx=other_ctx)

    assert validation["valid"] is False
    assert validation["errors"][-1]["code"] == "source_not_found"
    assert validation["errors"][-1]["resourceRef"] == "raw.source_contract_orders"
    assert validation["sourceContracts"] == []


def _foundry(tmp_path: Path) -> FoundryLite:
    return FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'pipeline-source-contracts.db'}",
            storage_root=tmp_path / "flite",
        )
    )


def _upload_orders(
    foundry: FoundryLite,
    tmp_path: Path,
    ctx: RequestContext,
    suffix: str,
    rows: str,
) -> CommitResult:
    csv_path = tmp_path / f"orders-{suffix}.csv"
    csv_path.write_text(f"order_id,amount\n{rows}", encoding="utf-8")
    if foundry.datasets.find("raw.source_contract_orders", ctx=ctx) is None:
        foundry.datasets.create(
            "raw.source_contract_orders",
            classification="internal",
            ctx=ctx,
        )
    return foundry.datasets.upload_csv("raw.source_contract_orders", csv_path, ctx=ctx)


def _approved_version(
    foundry: FoundryLite,
    branch_id: str,
    ctx: RequestContext,
) -> dict[str, object]:
    foundry.pipelines.run_tests(branch_id, ctx=ctx)
    proposal = foundry.pipelines.propose(
        branch_id,
        title="Pin committed source",
        idempotency_key="source-contract-proposal",
        ctx=ctx,
    )
    reviewer = RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id="source-contract-reviewer",
        request_id=f"{ctx.request_id}-review",
        roles=ctx.roles,
    )
    foundry.pipelines.assign(
        str(proposal["id"]),
        assignee_user_id=reviewer.actor_user_id,
        ctx=ctx,
    )
    foundry.pipelines.approve(str(proposal["id"]), ctx=reviewer)
    return foundry.pipelines.execute(str(proposal["id"]), ctx=reviewer)


def _upload_media(
    foundry: FoundryLite,
    ctx: RequestContext,
    media_set_id: str,
    suffix: str,
    *,
    is_committed: bool,
) -> str:
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set_id,
        idempotency_key=f"source-contract-media-{suffix}",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set_id,
        media_transaction_id=transaction_id,
        logical_path=f"/contracts/{suffix}.pdf",
        source=io.BytesIO(b"%PDF-1.7\nsource contract\n%%EOF"),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={
            "tenantId": ctx.tenant_id,
            "classification": "confidential",
            "policyVersion": "policy-v1",
        },
    )
    if is_committed:
        foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _media_branch(
    foundry: FoundryLite,
    ctx: RequestContext,
    pipeline_id: str,
    version_id: str,
) -> dict[str, object]:
    branch = foundry.pipelines.create_branch(
        pipeline_id=pipeline_id,
        name="main",
        idempotency_key=f"{pipeline_id}-branch",
        ctx=ctx,
    )
    return foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_media_graph(version_id),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )


def _execution_plan(deployment: dict[str, object]) -> dict[str, object]:
    row = cast(dict[str, object], deployment["deployment"])
    return cast(dict[str, object], row["executionPlan"])


def _source_contract(plan: dict[str, object]) -> dict[str, object]:
    contracts = cast(list[dict[str, object]], plan["sourceContracts"])
    return contracts[0]


def _dataset_graph() -> dict[str, object]:
    return _graph(
        source={
            "id": "source",
            "kind": "source",
            "descriptorId": "source.dataset",
            "specVersion": 1,
            "config": {
                "datasetRef": "raw.source_contract_orders",
                "schema": [{"name": "stale_column", "type": "string"}],
            },
        },
        output={
            "id": "out",
            "kind": "output",
            "descriptorId": "output.dataset",
            "specVersion": 1,
            "config": {"outputDatasetRef": "clean.source_contract_orders"},
        },
        source_port="dataset",
        target_port="input",
    )


def _media_graph(version_id: str) -> dict[str, object]:
    return _graph(
        source={
            "id": "source",
            "kind": "source",
            "descriptorId": "source.media_set",
            "specVersion": 1,
            "config": {
                "mediaSetRef": "legal.contracts",
                "mediaItemVersionIds": [version_id],
            },
        },
        output={
            "id": "out",
            "kind": "output",
            "descriptorId": "output.media_set",
            "specVersion": 1,
            "config": {"mediaSetRef": "legal.processed"},
        },
        source_port="media",
        target_port="media",
    )


def _media_semantic_graph(version_id: str) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "source",
                "kind": "source",
                "descriptorId": "source.media_set",
                "specVersion": 1,
                "config": {
                    "mediaSetRef": "legal.deployment_contracts",
                    "mediaItemVersionIds": [version_id],
                },
            },
            {
                "id": "rows",
                "kind": "transform",
                "descriptorId": "bridge.media_to_table_rows",
                "specVersion": 1,
                "config": {},
            },
            {
                "id": "semantic",
                "kind": "transform",
                "descriptorId": "transform.use_llm",
                "specVersion": 1,
                "config": {
                    "modelAlias": "claude-structured",
                    "promptVersionId": "contract-v1",
                    "outputColumn": "interpretation",
                    "inputFields": ["mediaReference"],
                    "outputSchema": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                    },
                    "dataClassification": "CONFIDENTIAL",
                },
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "analytics.deployment_contracts"},
            },
        ],
        "edges": [
            {
                "id": "source-rows",
                "sourceNodeId": "source",
                "sourcePortId": "media",
                "targetNodeId": "rows",
                "targetPortId": "media",
            },
            {
                "id": "rows-semantic",
                "sourceNodeId": "rows",
                "sourcePortId": "dataset",
                "targetNodeId": "semantic",
                "targetPortId": "input",
            },
            {
                "id": "semantic-out",
                "sourceNodeId": "semantic",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            },
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _graph(
    *,
    source: dict[str, object],
    output: dict[str, object],
    source_port: str,
    target_port: str,
) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [source, output],
        "edges": [
            {
                "id": "source-out",
                "sourceNodeId": "source",
                "sourcePortId": source_port,
                "targetNodeId": "out",
                "targetPortId": target_port,
            }
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }
