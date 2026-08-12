from __future__ import annotations

import json
from dataclasses import asdict
from io import StringIO
from pathlib import Path

import pytest
from foundry_lite.application.services.aip.governed_release_live_evidence import (
    assess_live_readiness,
    canonical_digest,
    verify_golden_evidence,
)

from scripts.operations.verify_governed_release_live_evidence import main

APP_ID = "release-app"
PUBLIC_BASE = "https://foundry.example.test"
ISSUER = "https://identity.example.test"
RESOURCE = f"{PUBLIC_BASE}/mcp/release/{APP_ID}"
CLIENT_ID = "https://chatgpt.com/oauth/release/client.json"
SUBMITTER = f"sha256:{'1' * 64}"
REVIEWER = f"sha256:{'2' * 64}"
SUBMITTER_SESSION = f"sha256:{'3' * 64}"
REVIEWER_SESSION = f"sha256:{'4' * 64}"
ONTOLOGY_BASE_SHA = "d" * 40
ONTOLOGY_HEAD_SHA = "a" * 40
ONTOLOGY_TREE_SHA = "e" * 40
ONTOLOGY_MERGE_SHA = "b" * 40
PIPELINE_BASE_SHA = "f" * 40
PIPELINE_HEAD_SHA = "7" * 40
PIPELINE_TREE_SHA = "8" * 40
PIPELINE_MERGE_SHA = "9" * 40
PRIOR_SHA = "c" * 40


def _manifest() -> dict[str, object]:
    return {
        "schemaVersion": "governed-release-golden-manifest/v2",
        "runId": "golden-run-1",
        "applicationId": APP_ID,
        "publicBaseUrl": PUBLIC_BASE,
        "authorizationServer": ISSUER,
        "hostedClientId": CLIENT_ID,
        "requiredScope": "osdk:connector:governed_release:execute",
        "resource": RESOURCE,
        "sourceControl": {
            "provider": "github",
            "repositoryId": 42,
            "owner": "acme",
            "repository": "foundry-lite",
            "baseRef": "main",
            "publications": [_source_binding("ontology", 17), _source_binding("pipeline", 18)],
        },
        "deployment": {
            "provider": "render",
            "serviceId": "srv-foundry-lite",
            "environment": "production",
        },
        "requiredScenarios": ["ontology", "pipeline"],
    }


def _preflight(origin: str = "local_or_injected") -> dict[str, object]:
    return {
        "schema_version": "governed-release-live-preflight/v1",
        "status": "ready",
        "is_ready": True,
        "network_mode": "read_only",
        "evidence_origin": origin,
        "checks": [
            {
                "name": "configuration",
                "status": "ready",
                "code": "exact_targets_configured",
                "evidence": {
                    "publicBaseUrl": PUBLIC_BASE,
                    "authorizationServer": ISSUER,
                    "resource": RESOURCE,
                    "repository": "acme/foundry-lite",
                    "repositoryId": 42,
                    "baseBranch": "main",
                    "serviceId": "srv-foundry-lite",
                },
            }
        ],
        "unverified": [],
    }


def _principal(subject: str, session: str) -> dict[str, object]:
    return {
        "subjectHash": subject,
        "oauthSessionHash": session,
        "applicationId": APP_ID,
        "clientId": CLIENT_ID,
        "issuer": ISSUER,
        "audience": RESOURCE,
        "grantType": "authorization_code",
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
    }


def _governance(proposal: str) -> dict[str, object]:
    return {
        "proposalId": proposal,
        "proposalFingerprint": f"sha256:{'5' * 64}",
        "validationPassed": True,
        "decision": "approve",
        "submitterSubjectHash": SUBMITTER,
        "reviewerSubjectHash": REVIEWER,
        "auditEventIds": [f"{proposal}-submitted", f"{proposal}-approved", f"{proposal}-executed"],
    }


def _source_binding(kind: str, pull_number: int) -> dict[str, object]:
    proposal_id = f"{kind}-proposal"
    is_ontology = kind == "ontology"
    return {
        "kind": kind,
        "operation": "source_publish",
        "proposalId": proposal_id,
        "provider": "github",
        "repositoryId": 42,
        "owner": "acme",
        "repository": "foundry-lite",
        "baseRef": "main",
        "baseSha": ONTOLOGY_BASE_SHA if is_ontology else PIPELINE_BASE_SHA,
        "headRef": f"codex/{proposal_id}",
        "headSha": ONTOLOGY_HEAD_SHA if is_ontology else PIPELINE_HEAD_SHA,
        "treeSha": ONTOLOGY_TREE_SHA if is_ontology else PIPELINE_TREE_SHA,
        "pullNumber": pull_number,
        "manifestPath": f".foundry-lite/releases/{kind}/{proposal_id}.json",
        "manifestFingerprint": f"sha256:{'6' * 64}" if is_ontology else f"sha256:{'7' * 64}",
    }


def _source_publication(kind: str, pull_number: int) -> dict[str, object]:
    return {
        **_source_binding(kind, pull_number),
        "deliveryId": f"source-publication-{pull_number}",
        "status": "landed",
        "receiptStatus": "published",
        "providerResourceId": f"pull:{pull_number}",
        "providerRequestId": f"github-publication-request-{pull_number}",
        "completedAt": "2026-08-09T00:55:00Z",
    }


def _source_receipt(kind: str, pull_number: int) -> dict[str, object]:
    is_ontology = kind == "ontology"
    return {
        **_source_binding(kind, pull_number),
        "publicationDeliveryId": f"source-publication-{pull_number}",
        "mergeCommitSha": ONTOLOGY_MERGE_SHA if is_ontology else PIPELINE_MERGE_SHA,
        "isMerged": True,
        "requiredChecksPassed": True,
        "providerRequestId": f"github-request-{pull_number}",
        "mergedAt": "2026-08-09T01:00:00Z",
    }


def _evidence() -> dict[str, object]:
    return {
        "schemaVersion": "governed-release-golden-evidence/v2",
        "runId": "golden-run-1",
        "applicationId": APP_ID,
        "capture": {
            "host": "chatgpt.com",
            "publicEndpoint": RESOURCE,
            "transportProfile": "hosted_chatgpt_public_https",
            "verificationProfile": "provider_live_readback",
            "isSimulated": False,
            "completedAt": "2026-08-09T02:00:00Z",
        },
        "principals": {
            "submitter": _principal(SUBMITTER, SUBMITTER_SESSION),
            "reviewer": _principal(REVIEWER, REVIEWER_SESSION),
        },
        "scenarios": [
            {
                "kind": "ontology",
                "governance": _governance("ontology-proposal"),
                "sourcePublication": _source_publication("ontology", 17),
                "sourceControl": _source_receipt("ontology", 17),
                "internal": {
                    "status": "active",
                    "activeVersionNumber": 2,
                    "auditEventId": "ontology-activated",
                },
                "rollback": {
                    "status": "rolled_back",
                    "targetVersionNumber": 1,
                    "auditEventId": "ontology-rolled-back",
                },
            },
            {
                "kind": "pipeline",
                "governance": _governance("pipeline-proposal"),
                "sourcePublication": _source_publication("pipeline", 18),
                "sourceControl": _source_receipt("pipeline", 18),
                "internal": {
                    "status": "PROMOTED",
                    "deploymentId": "pipeline-deployment-18",
                    "auditEventId": "pipeline-promoted",
                },
                "deployment": {
                    "provider": "render",
                    "serviceId": "srv-foundry-lite",
                    "environment": "production",
                    "deployId": "dep-current-18",
                    "commitId": PIPELINE_MERGE_SHA,
                    "status": "live",
                    "isTerminal": True,
                    "isSuccessful": True,
                    "providerRequestId": "render-deploy-request",
                    "finishedAt": "2026-08-09T01:30:00Z",
                },
                "statusObservation": {
                    "deployId": "dep-current-18",
                    "commitId": PIPELINE_MERGE_SHA,
                    "status": "live",
                    "providerRequestId": "render-status-request",
                    "observedAt": "2026-08-09T01:31:00Z",
                },
                "rollback": {
                    "provider": "render",
                    "serviceId": "srv-foundry-lite",
                    "environment": "production",
                    "targetDeployId": "dep-prior-17",
                    "targetCommitId": PRIOR_SHA,
                    "rolledBackFromDeployId": "dep-current-18",
                    "rollbackDeployId": "dep-rollback-19",
                    "commitId": PRIOR_SHA,
                    "trigger": "rollback",
                    "status": "live",
                    "isTerminal": True,
                    "isSuccessful": True,
                    "providerRequestId": "render-rollback-request",
                    "finishedAt": "2026-08-09T01:45:00Z",
                },
            },
        ],
    }


def test_injected_preflight_can_be_structurally_complete_but_never_live_verified() -> None:
    result = verify_golden_evidence(_manifest(), _evidence(), _preflight())

    assert result.status == "structurally_complete"
    assert result.is_structurally_complete is True
    assert result.is_live_verified is False
    assert "live_provider_preflight_required" in result.blockers
    assert "authentic_live_collector_required" in result.blockers


def test_crafted_live_fields_still_cannot_create_authentic_live_proof() -> None:
    result = verify_golden_evidence(_manifest(), _evidence(), _preflight("live_provider_readback"))

    assert result.status == "structurally_complete"
    assert result.is_live_verified is False
    assert result.blockers == ("authentic_live_collector_required",)


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("operation", "source_merge", "source_publication_operation_mismatch"),
        ("status", "dispatching", "source_publication_not_landed"),
        ("receiptStatus", "partial", "source_publication_receipt_not_published"),
        ("deliveryId", "", "deliveryId_text_required"),
        ("providerResourceId", "pull:999", "source_publication_resource_identity_mismatch"),
        ("providerRequestId", "", "providerRequestId_text_required"),
    ],
)
def test_publication_requires_exact_durable_landed_receipt(
    field: str,
    replacement: object,
    code: str,
) -> None:
    evidence = _evidence()
    publication = _scenario_by_kind(evidence, "ontology")["sourcePublication"]
    assert isinstance(publication, dict)
    publication[field] = replacement

    result = verify_golden_evidence(_manifest(), evidence, _preflight("live_provider_readback"))

    assert result.is_structurally_complete is False
    assert code in result.blockers


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("repositoryId", 84),
        ("baseRef", "release"),
        ("baseSha", "1" * 40),
        ("headRef", "codex/another-proposal"),
        ("headSha", "2" * 40),
        ("treeSha", "3" * 40),
        ("manifestPath", ".foundry-lite/releases/ontology/another-proposal.json"),
        ("manifestFingerprint", f"sha256:{'8' * 64}"),
    ],
)
def test_publication_artifact_must_match_golden_manifest(field: str, replacement: object) -> None:
    evidence = _evidence()
    publication = _scenario_by_kind(evidence, "ontology")["sourcePublication"]
    assert isinstance(publication, dict)
    publication[field] = replacement

    result = verify_golden_evidence(_manifest(), evidence, _preflight("live_provider_readback"))

    assert result.is_structurally_complete is False
    assert f"source_publication_{field}_mismatch" in result.blockers


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("provider", "gitlab"),
        ("repositoryId", 84),
        ("owner", "other"),
        ("repository", "other-repository"),
        ("baseRef", "release"),
        ("baseSha", "1" * 40),
        ("headRef", "codex/another-proposal"),
        ("headSha", "2" * 40),
        ("treeSha", "3" * 40),
        ("pullNumber", 99),
        ("manifestPath", ".foundry-lite/releases/ontology/another-proposal.json"),
        ("manifestFingerprint", f"sha256:{'8' * 64}"),
    ],
)
def test_merge_receipt_must_match_published_artifact(field: str, replacement: object) -> None:
    evidence = _evidence()
    merge = _scenario_by_kind(evidence, "ontology")["sourceControl"]
    assert isinstance(merge, dict)
    merge[field] = replacement

    result = verify_golden_evidence(_manifest(), evidence, _preflight("live_provider_readback"))

    assert result.is_structurally_complete is False
    assert f"source_merge_publication_{field}_mismatch" in result.blockers


def test_merge_receipt_must_reference_durable_publication_delivery() -> None:
    evidence = _evidence()
    merge = _scenario_by_kind(evidence, "pipeline")["sourceControl"]
    assert isinstance(merge, dict)
    merge["publicationDeliveryId"] = "source-publication-other"

    result = verify_golden_evidence(_manifest(), evidence, _preflight("live_provider_readback"))

    assert result.is_structurally_complete is False
    assert "source_merge_publication_receipt_mismatch" in result.blockers


def test_readiness_blocks_manifest_without_exact_source_publications() -> None:
    manifest = _manifest()
    source = manifest["sourceControl"]
    assert isinstance(source, dict)
    del source["publications"]

    readiness = assess_live_readiness(APP_ID, manifest, _preflight("live_provider_readback"), None)

    assert readiness.status == "blocked"
    assert readiness.is_ready_for_live_run is False
    assert "publications_list_required" in readiness.blockers


def test_staging_manifest_binds_every_render_receipt_without_claiming_production() -> None:
    manifest = _manifest()
    deployment_target = manifest["deployment"]
    assert isinstance(deployment_target, dict)
    deployment_target["environment"] = "staging"
    evidence = _evidence()
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, list)
    pipeline = scenarios[1]
    assert isinstance(pipeline, dict)
    for key in ("deployment", "rollback"):
        receipt = pipeline[key]
        assert isinstance(receipt, dict)
        receipt["environment"] = "staging"

    result = verify_golden_evidence(manifest, evidence, _preflight("live_provider_readback"))

    assert result.is_structurally_complete is True
    assert result.is_live_verified is False


def test_same_human_is_allowed_but_inexact_rollback_breaks_structural_completion() -> None:
    evidence = _evidence()
    principals = evidence["principals"]
    assert isinstance(principals, dict)
    principals["reviewer"] = _principal(SUBMITTER, SUBMITTER_SESSION)
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        governance = scenario["governance"]
        assert isinstance(governance, dict)
        governance["reviewerSubjectHash"] = SUBMITTER
    result = verify_golden_evidence(_manifest(), evidence, _preflight("live_provider_readback"))
    assert result.is_structurally_complete is True

    evidence = _evidence()
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, list)
    pipeline = scenarios[1]
    assert isinstance(pipeline, dict)
    rollback = pipeline["rollback"]
    assert isinstance(rollback, dict)
    rollback["rollbackDeployId"] = "dep-current-18"
    result = verify_golden_evidence(_manifest(), evidence, _preflight("live_provider_readback"))
    assert result.is_structurally_complete is False
    assert "rollback_deploy_identity_reused" in result.blockers


def test_readiness_can_reach_ready_for_live_run_but_not_live_verified() -> None:
    manifest = _manifest()
    structural = verify_golden_evidence(manifest, _evidence(), _preflight("live_provider_readback"))
    forged = {
        **asdict(structural),
        "status": "live_verified",
        "is_live_verified": True,
        "blockers": [],
        "manifest_digest": canonical_digest(manifest),
    }

    readiness = assess_live_readiness(APP_ID, manifest, _preflight("live_provider_readback"), forged)

    assert readiness.status == "ready_for_live_run"
    assert readiness.is_ready_for_live_run is True
    assert readiness.is_live_verified is False
    assert "authentic_live_collector_required" in readiness.blockers


def test_verifier_cli_writes_blocked_report_and_nonzero_exit(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    evidence_path = tmp_path / "evidence.json"
    preflight_path = tmp_path / "preflight.json"
    output_path = tmp_path / "verification.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
    preflight_path.write_text(json.dumps(_preflight("live_provider_readback")), encoding="utf-8")
    stdout = StringIO()

    exit_code = main(
        [
            "--manifest",
            str(manifest_path),
            "--evidence",
            str(evidence_path),
            "--preflight",
            str(preflight_path),
            "--output",
            str(output_path),
        ],
        stdout=stdout,
    )
    payload = json.loads(output_path.read_text())

    assert exit_code == 1
    assert payload["status"] == "structurally_complete"
    assert payload["is_live_verified"] is False
    assert json.loads(stdout.getvalue()) == payload


def _scenario_by_kind(evidence: dict[str, object], kind: str) -> dict[str, object]:
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, list)
    matching = [item for item in scenarios if isinstance(item, dict) and item.get("kind") == kind]
    assert len(matching) == 1
    return matching[0]
