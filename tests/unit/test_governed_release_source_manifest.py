from __future__ import annotations

import json

import pytest
from foundry_lite.application.ports.source_control_release import SourceRepositoryRef
from foundry_lite.application.services.aip.governed_release_source_manifest import (
    build_governed_release_source_manifest,
    verify_governed_release_source_manifest,
)
from foundry_lite.application.services.ontology_proposal_payloads import yaml_fingerprint
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_REPOSITORY = SourceRepositoryRef("github", 42, "example", "foundry-lite")
_SOURCE_COMMIT = "a" * 40


def test_ontology_manifest_binds_exact_content_without_copying_tenant_data() -> None:
    yaml_text = "objectTypes:\n  Order:\n    properties: {}\n"
    proposal = {
        "id": "ontprop_123",
        "fingerprint": yaml_fingerprint(yaml_text),
        "yamlText": yaml_text,
        "sourceBranch": {"branchId": "ontbranch_123", "branchName": "orders"},
        "validation": {"objectTypeCount": 1, "linkTypeCount": 0},
        "submittedMigrationPlan": {"status": "compatible", "changes": []},
        "title": "private title",
        "submittedByUserId": "private-user",
    }
    ctx = RequestContext(tenant_id="tenant-private", actor_user_id="private-user")

    manifest = build_governed_release_source_manifest(
        ctx,
        "ontology",
        proposal,
        _REPOSITORY,
        "main",
        "codex/orders",
    )

    body = manifest.canonical_bytes.decode("utf-8")
    payload = json.loads(body)
    assert manifest.artifact_path == ".foundry-lite/releases/ontology/ontprop_123.json"
    assert payload["proposalContentFingerprint"] == yaml_fingerprint(yaml_text)
    assert payload["source"]["internalBranchId"] == "ontbranch_123"
    assert manifest.manifest_fingerprint.startswith("sha256:")
    assert "tenantBinding" not in payload
    assert "tenant-private" not in body
    assert "private-user" not in body
    assert "private title" not in body
    assert yaml_text not in body


def test_pipeline_manifest_is_deterministic_and_hashes_review_evidence() -> None:
    graph = {"edges": [], "nodes": [], "schemaVersion": 2}
    proposal = {
        "id": "pipeprop_123",
        "graph": graph,
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchName": "orders", "branchId": "pipebranch_123"},
        "changeDiff": {"items": [], "summary": {"totalChangeCount": 0}},
        "diffCompleteness": "complete",
        "testReceipt": {"status": "passed", "proofKind": "pipeline_branch_test"},
    }
    reordered = {
        "testReceipt": {"proofKind": "pipeline_branch_test", "status": "passed"},
        "diffCompleteness": "complete",
        "changeDiff": {"summary": {"totalChangeCount": 0}, "items": []},
        "sourceBranch": {"branchId": "pipebranch_123", "branchName": "orders"},
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "graph": {"schemaVersion": 2, "nodes": [], "edges": []},
        "id": "pipeprop_123",
    }
    ctx = RequestContext(tenant_id="tenant-a")

    first = build_governed_release_source_manifest(
        ctx,
        "pipeline",
        proposal,
        _REPOSITORY,
        "main",
        "codex/orders",
    )
    second = build_governed_release_source_manifest(
        ctx,
        "pipeline",
        reordered,
        _REPOSITORY,
        "main",
        "codex/orders",
    )

    assert first == second
    payload = json.loads(first.canonical_bytes)
    assert payload["reviewEvidence"]["pipelineValidationFingerprint"].startswith("sha256:")
    assert "pipeline_branch_test" not in first.canonical_bytes.decode("utf-8")


def test_manifest_binds_passing_consumer_osdk_receipt_to_source_commit() -> None:
    proposal = _pipeline_proposal()

    manifest = build_governed_release_source_manifest(
        RequestContext(tenant_id="tenant-a"),
        "pipeline",
        proposal,
        _REPOSITORY,
        "main",
        "codex/orders",
        consumer_osdk_application_id="restaurant-reservation-customer",
        consumer_osdk_compliance=_consumer_osdk_receipt(),
        expected_source_commit=_SOURCE_COMMIT,
    )

    binding = json.loads(manifest.canonical_bytes)["consumerOsdkCompliance"]
    assert binding["applicationId"] == "restaurant-reservation-customer"
    assert binding["sourceCommit"] == _SOURCE_COMMIT
    assert binding["sourceTreeHash"].startswith("sha256:")
    assert binding["sdkArtifactHash"].startswith("sha256:")
    assert binding["ontologyFingerprint"].startswith("sha256:")
    assert binding["receiptFingerprint"].startswith("sha256:")
    assert "sourceFiles" not in binding


def test_manifest_blocks_strict_consumer_release_without_receipt() -> None:
    with pytest.raises(ValidationFailed, match="consumerOsdkCompliance"):
        build_governed_release_source_manifest(
            RequestContext(tenant_id="tenant-a"),
            "pipeline",
            _pipeline_proposal(),
            _REPOSITORY,
            "main",
            "codex/orders",
            consumer_osdk_application_id="restaurant-reservation-customer",
            expected_source_commit=_SOURCE_COMMIT,
        )


def test_manifest_blocks_stale_or_bypassed_consumer_osdk_receipt() -> None:
    with pytest.raises(ConflictDetected, match="stale"):
        build_governed_release_source_manifest(
            RequestContext(tenant_id="tenant-a"),
            "pipeline",
            _pipeline_proposal(),
            _REPOSITORY,
            "main",
            "codex/orders",
            consumer_osdk_application_id="restaurant-reservation-customer",
            consumer_osdk_compliance=_consumer_osdk_receipt(source_commit="b" * 40),
            expected_source_commit=_SOURCE_COMMIT,
        )
    receipt = _consumer_osdk_receipt()
    receipt["applications"][0]["exceptionCount"] = 1
    with pytest.raises(ConflictDetected, match="bypasses"):
        build_governed_release_source_manifest(
            RequestContext(tenant_id="tenant-a"),
            "pipeline",
            _pipeline_proposal(),
            _REPOSITORY,
            "main",
            "codex/orders",
            consumer_osdk_application_id="restaurant-reservation-customer",
            consumer_osdk_compliance=receipt,
            expected_source_commit=_SOURCE_COMMIT,
        )


def test_manifest_rejects_proposal_content_drift() -> None:
    graph = {"schemaVersion": 2, "nodes": [], "edges": []}
    proposal = {
        "id": "pipeprop_123",
        "graph": {**graph, "nodes": [{"id": "changed"}]},
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchId": "pipebranch_123", "branchName": "orders"},
    }

    with pytest.raises(ConflictDetected, match="stored fingerprint"):
        build_governed_release_source_manifest(
            RequestContext(tenant_id="tenant-a"),
            "pipeline",
            proposal,
            _REPOSITORY,
            "main",
            "codex/orders",
        )


def _pipeline_proposal() -> dict[str, object]:
    graph = {"edges": [], "nodes": [], "schemaVersion": 2}
    return {
        "id": "pipeprop_123",
        "graph": graph,
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchName": "orders", "branchId": "pipebranch_123"},
        "changeDiff": {"items": [], "summary": {"totalChangeCount": 0}},
        "diffCompleteness": "complete",
        "testReceipt": {"status": "passed", "proofKind": "pipeline_branch_test"},
    }


def _consumer_osdk_receipt(source_commit: str = _SOURCE_COMMIT) -> dict[str, object]:
    fingerprint = f"sha256:{'1' * 64}"
    return {
        "schemaVersion": "foundry-lite-consumer-osdk-compliance/v1",
        "sourceCommit": source_commit,
        "inventoryHash": fingerprint,
        "isCompliant": True,
        "applications": [
            {
                "applicationId": "restaurant-reservation-customer",
                "profile": "consumer_osdk_strict",
                "ontologyFingerprint": fingerprint,
                "sourceTreeHash": fingerprint,
                "sdkPackageTreeHash": fingerprint,
                "sdkArtifactHash": fingerprint,
                "exceptionCount": 0,
                "violationCount": 0,
                "isCompliant": True,
                "sourceFiles": [{"path": "private/source.ts", "hash": fingerprint}],
            }
        ],
    }


def test_manifest_rejects_unbound_source_head() -> None:
    graph = {"schemaVersion": 2, "nodes": [], "edges": []}
    proposal = {
        "id": "pipeprop_123",
        "graph": graph,
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchId": "pipebranch_123", "branchName": "orders"},
    }

    with pytest.raises(ConflictDetected, match="not derived"):
        build_governed_release_source_manifest(
            RequestContext(tenant_id="tenant-a"),
            "pipeline",
            proposal,
            _REPOSITORY,
            "main",
            "codex/other",
        )


def test_manifest_verifier_rejects_a_different_git_blob() -> None:
    graph = {"schemaVersion": 2, "nodes": [], "edges": []}
    proposal = {
        "id": "pipeprop_123",
        "graph": graph,
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchId": "pipebranch_123", "branchName": "orders"},
        "changeDiff": {"items": [], "summary": {"totalChangeCount": 0}},
        "diffCompleteness": "complete",
        "testReceipt": {"status": "passed", "proofKind": "pipeline_branch_test"},
    }
    manifest = build_governed_release_source_manifest(
        RequestContext(tenant_id="tenant-a"),
        "pipeline",
        proposal,
        _REPOSITORY,
        "main",
        "codex/orders",
    )

    with pytest.raises(ConflictDetected, match="does not match") as error:
        verify_governed_release_source_manifest(manifest, b"{}\n")

    assert error.value.details["expectedFingerprint"] == manifest.manifest_fingerprint
    observed = error.value.details["observedFingerprint"]
    assert isinstance(observed, str)
    assert observed.startswith("sha256:")


@pytest.mark.parametrize("missing_key", ["changeDiff", "diffCompleteness", "testReceipt"])
def test_pipeline_manifest_rejects_missing_review_evidence(missing_key: str) -> None:
    graph = {"schemaVersion": 2, "nodes": [], "edges": []}
    proposal = {
        "id": "pipeprop_123",
        "graph": graph,
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchId": "pipebranch_123", "branchName": "orders"},
        "changeDiff": {"items": [], "summary": {"totalChangeCount": 0}},
        "diffCompleteness": "complete",
        "testReceipt": {"status": "passed", "proofKind": "pipeline_branch_test"},
    }
    del proposal[missing_key]

    with pytest.raises(ValidationFailed, match=missing_key):
        build_governed_release_source_manifest(
            RequestContext(tenant_id="tenant-a"),
            "pipeline",
            proposal,
            _REPOSITORY,
            "main",
            "codex/orders",
        )
