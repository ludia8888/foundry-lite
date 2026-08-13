from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from foundry_lite.application.services.aip.governed_release_service import GovernedReleaseService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed


class _OntologyProposals:
    def __init__(self, proposal: Mapping[str, object]) -> None:
        self.proposal = dict(proposal)

    def get_proposal(self, _proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        del ctx
        return dict(self.proposal)

    def decide_proposal(
        self,
        _proposal_id: str,
        *,
        decision: str,
        expected_fingerprint: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del expected_fingerprint, comment, ctx
        self.proposal["status"] = "approved" if decision == "approve" else "rejected"
        return dict(self.proposal)

    def execute_proposal(
        self,
        _proposal_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del expected_fingerprint, ctx
        self.proposal["status"] = "applied"
        return dict(self.proposal)


class _Ontology:
    def __init__(self, active_version: int) -> None:
        self.active_version = active_version
        self.rollback_count = 0
        self.rollback_receipts: dict[str, Mapping[str, object]] = {}

    def release_active_version_summary(self, *, ctx: RequestContext | None = None) -> Mapping[str, object]:
        del ctx
        return {"ontologyVersionId": f"ontology-{self.active_version}", "versionNumber": self.active_version}

    def rollback_to_version(
        self,
        version_number: int,
        *,
        expected_active_version_number: int | None = None,
        idempotency_key: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        del ctx, expected_active_version_number
        self.rollback_count += 1
        self.active_version += 1
        result = {
            "version_number": self.active_version,
            "rolled_back_to_version_number": version_number,
        }
        if idempotency_key is not None:
            self.rollback_receipts[idempotency_key] = result
        return result

    def replay_rollback(
        self,
        _version_number: int,
        *,
        expected_active_version_number: int,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object] | None:
        del expected_active_version_number, ctx
        return self.rollback_receipts.get(idempotency_key)


class _Pipelines:
    def __init__(
        self,
        proposal: Mapping[str, object],
        versions: list[Mapping[str, object]],
        deployments: list[Mapping[str, object]],
    ) -> None:
        self.proposal = dict(proposal)
        self.versions = [dict(row) for row in versions]
        self.deployments = [dict(row) for row in deployments]
        self.last_deploy: dict[str, object] | None = None
        self.deploy_count = 0

    def get_proposal(self, _proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        del ctx
        return dict(self.proposal)

    def decide_proposal(
        self,
        _proposal_id: str,
        *,
        decision: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del comment, ctx
        self.proposal["status"] = "approved" if decision == "approve" else "rejected"
        return dict(self.proposal)

    def execute_proposal(self, _proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        del ctx
        self.proposal["status"] = "executed"
        return dict(self.versions[0])

    def list_versions(
        self,
        _pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del limit, ctx
        return {"items": list(self.versions)}

    def list_deployments(
        self,
        _pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del limit, ctx
        return {"items": list(self.deployments)}

    def deploy(
        self,
        pipeline_id: str,
        version_id: str,
        *,
        idempotency_key: str,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del ctx
        self.deploy_count += 1
        deployment = {
            "id": f"deployment-{len(self.deployments) + 1}",
            "pipelineId": pipeline_id,
            "versionId": version_id,
            "status": "PROMOTED",
            "rolledBackFromId": (options or {}).get("rolledBackFromId"),
            "idempotencyKey": idempotency_key,
        }
        self.deployments.insert(0, deployment)
        self.last_deploy = deployment
        return {"deployment": deployment}

    def replay_deployment(
        self,
        idempotency_key: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object] | None:
        del ctx
        deployment = next((row for row in self.deployments if row.get("idempotencyKey") == idempotency_key), None)
        return {"deployment": dict(deployment)} if deployment is not None else None


class _ProposalReader:
    def __init__(self, ontology: _OntologyProposals, pipelines: _Pipelines) -> None:
        self.ontology = ontology
        self.pipelines = pipelines

    def load_governed_release_proposal(
        self,
        ctx: RequestContext,
        release_kind: str,
        proposal_id: str,
    ) -> dict[str, object]:
        if release_kind == "ontology":
            return self.ontology.get_proposal(proposal_id, ctx=ctx)
        return self.pipelines.get_proposal(proposal_id, ctx=ctx)


class _ExternalDelivery:
    def __init__(self) -> None:
        self.target = {
            "targetDeployId": "provider-deploy-1",
            "targetCommitId": "a" * 40,
            "rolledBackFromDeployId": "provider-deploy-2",
        }
        self.rollback_targets: list[Mapping[str, object]] = []
        self.is_source_configured = False
        self.is_source_published = False
        self.publish_calls: list[str] = []
        self.application_status = "live"

    def is_application_delivery_enabled(self) -> bool:
        return True

    def application_rollback_target(
        self,
        _ctx: RequestContext,
        _proposal_id: str,
        _idempotency_key: str | None = None,
    ) -> dict[str, object]:
        return dict(self.target)

    def start_application_rollback(
        self,
        _ctx: RequestContext,
        _proposal_id: str,
        target: Mapping[str, object],
        _idempotency_key: str,
    ) -> dict[str, object]:
        self.rollback_targets.append(dict(target))
        return {"status": "landed", "providerResourceId": "provider-rollback-3"}

    def source_evidence(self, _ctx: RequestContext, _proposal: Mapping[str, object]) -> dict[str, object]:
        if not self.is_source_configured:
            return {"isConfigured": False, "isRequired": False, "status": "not_configured"}
        evidence: dict[str, object] = {
            "isConfigured": True,
            "isRequired": True,
            "status": "blocked" if self.is_source_published else "not_published",
        }
        if self.is_source_published:
            evidence["publication"] = {"receiptStatus": "published"}
        return evidence

    def publish_source_candidate(
        self,
        _ctx: RequestContext,
        _release_kind: str,
        _proposal: Mapping[str, object],
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        self.publish_calls.append(str(arguments["idempotencyKey"]))
        self.is_source_published = True
        return {"status": "landed", "receiptStatus": "published"}

    def delivery_evidence(self, _ctx: RequestContext, _proposal_id: str) -> dict[str, object]:
        return {
            "isConfigured": True,
            "isRequired": True,
            "applicationStatus": {
                "status": self.application_status,
                "isOperationallyComplete": self.application_status == "live",
            },
            "applicationRollbackTarget": dict(self.target),
            "deliveries": [],
            "deploymentObservations": [],
        }


def _service(
    *,
    ontology_proposal: Mapping[str, object],
    active_ontology_version: int,
    pipeline_proposal: Mapping[str, object] | None = None,
    versions: list[Mapping[str, object]] | None = None,
    deployments: list[Mapping[str, object]] | None = None,
    external_delivery: _ExternalDelivery | None = None,
) -> tuple[GovernedReleaseService, _Pipelines]:
    pipelines = _Pipelines(
        pipeline_proposal or {"id": "pp-1", "pipelineId": "pipe-1", "status": "approved"},
        versions or [],
        deployments or [],
    )
    ontology_proposals = _OntologyProposals(ontology_proposal)
    service = GovernedReleaseService(
        proposal_reader=_ProposalReader(ontology_proposals, pipelines),
        ontology_proposals=ontology_proposals,
        ontology=_Ontology(active_ontology_version),
        pipelines=pipelines,
        external_delivery=external_delivery,
    )
    return service, pipelines


def test_ontology_release_is_active_only_when_applied_version_matches_catalog() -> None:
    proposal = {
        "id": "op-1",
        "status": "applied",
        "fingerprint": "sha256:proposal",
        "appliedOntologyVersion": {"versionNumber": 4},
    }
    current, _ = _service(ontology_proposal=proposal, active_ontology_version=4)
    superseded, _ = _service(ontology_proposal=proposal, active_ontology_version=5)
    arguments = {"releaseKind": "ontology", "proposalId": "op-1"}

    current_view = current.get_status(RequestContext(roles=("admin",)), arguments)
    stale_view = superseded.get_status(RequestContext(roles=("admin",)), arguments)

    assert current_view["stage"] == "active"
    assert current_view["rollbackTarget"] == {
        "targetVersionNumber": 3,
        "expectedActiveVersionNumber": 4,
    }
    assert current_view["nextActions"] == ["rollback_release"]
    assert stale_view["stage"] == "superseded"
    assert stale_view["rollbackTarget"] is None
    assert stale_view["nextActions"] == []


def test_pipeline_view_exposes_only_safe_current_rollback_target() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    versions = [
        {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
        {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
    ]
    deployments = [
        {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
        {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
    ]
    service, _ = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=versions,
        deployments=deployments,
    )

    view = service.get_status(
        RequestContext(roles=("admin",)),
        {"releaseKind": "pipeline", "proposalId": "pp-2"},
    )

    assert view["stage"] == "deployed"
    assert view["rollbackTarget"] == {
        "pipelineId": "pipe-1",
        "targetVersionId": "version-1",
        "rolledBackFromId": "deployment-2",
    }
    assert view["nextActions"] == ["rollback_release"]


def test_pipeline_author_must_publish_exact_source_candidate_before_independent_decision() -> None:
    external = _ExternalDelivery()
    external.is_source_configured = True
    proposal = {
        "id": "pp-review",
        "pipelineId": "pipe-1",
        "status": "submitted",
        "createdBy": "author-1",
        "assignedTo": "reviewer-1",
    }
    service, _ = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        external_delivery=external,
    )
    arguments = {"releaseKind": "pipeline", "proposalId": "pp-review"}

    author_view = service.get_candidate(RequestContext(actor_user_id="author-1"), arguments)
    reviewer_view = service.get_candidate(RequestContext(actor_user_id="reviewer-1"), arguments)

    assert author_view["nextActions"] == ["publish_release_candidate"]
    assert reviewer_view["nextActions"] == []
    with pytest.raises(ConflictDetected, match="published source candidate"):
        service.submit_decision(
            RequestContext(actor_user_id="reviewer-1"),
            {**arguments, "decision": "approve", "idempotencyKey": "decision-before-publish"},
        )

    service.publish_candidate(
        RequestContext(actor_user_id="author-1"),
        {**arguments, "idempotencyKey": "publish-1"},
    )
    published_view = service.get_candidate(RequestContext(actor_user_id="reviewer-1"), arguments)
    assert external.publish_calls == ["publish-1"]
    assert published_view["nextActions"] == ["submit_release_decision"]


def test_pipeline_rollback_redeploys_explicit_prior_version_and_returns_snapshot() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    versions = [
        {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
        {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
    ]
    deployments = [
        {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
        {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
    ]
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=versions,
        deployments=deployments,
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "targetVersionId": "version-1",
        "rolledBackFromId": "deployment-2",
        "idempotencyKey": "rollback-1",
    }

    result = service.rollback(RequestContext(roles=("admin",)), arguments)

    assert pipelines.last_deploy == {
        "id": "deployment-3",
        "pipelineId": "pipe-1",
        "versionId": "version-1",
        "status": "PROMOTED",
        "rolledBackFromId": "deployment-2",
        "idempotencyKey": "rollback-1",
    }
    assert result["proposalId"] == "pp-2"
    assert result["lastOperation"]["operation"] == "rollback_deployed"


def test_pipeline_rollback_binds_widget_confirmation_to_exact_external_provider_target() -> None:
    external = _ExternalDelivery()
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal={"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"},
        versions=[
            {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
            {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
        ],
        deployments=[
            {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
            {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
        ],
        external_delivery=external,
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "targetVersionId": "version-1",
        "rolledBackFromId": "deployment-2",
        **external.target,
        "idempotencyKey": "rollback-external-1",
    }

    result = service.rollback(RequestContext(roles=("admin",)), arguments)

    assert pipelines.deploy_count == 1
    assert external.rollback_targets == [external.target]
    assert result["lastOperation"]["result"]["infrastructure"]["status"] == "landed"


def test_pipeline_status_does_not_offer_rollback_without_an_external_provider_target() -> None:
    external = _ExternalDelivery()
    external.target = {}
    service, _ = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal={"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"},
        versions=[
            {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
            {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
        ],
        deployments=[
            {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
            {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
        ],
        external_delivery=external,
    )

    view = service.get_status(
        RequestContext(roles=("admin",)),
        {"releaseKind": "pipeline", "proposalId": "pp-2"},
    )

    assert view["stage"] == "deployed"
    assert view["rollbackTarget"] is not None
    assert view["nextActions"] == []


def test_failed_application_deploy_offers_internal_only_recovery_without_provider_rollback() -> None:
    external = _ExternalDelivery()
    external.application_status = "failed"
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal={"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"},
        versions=[
            {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
            {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
        ],
        deployments=[
            {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
            {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
        ],
        external_delivery=external,
    )
    ctx = RequestContext(roles=("admin",))
    status = service.get_status(ctx, {"releaseKind": "pipeline", "proposalId": "pp-2"})

    assert status["stage"] == "deployment_failed"
    assert status["nextActions"] == ["rollback_release"]
    delivery = cast(Mapping[str, object], status["releaseEvidence"])["externalDelivery"]
    assert isinstance(delivery, Mapping)
    assert delivery["applicationRollbackTarget"] is None
    assert delivery["recoveryMode"] == "internal_only"

    result = service.rollback(
        ctx,
        {
            "releaseKind": "pipeline",
            "proposalId": "pp-2",
            "pipelineId": "pipe-1",
            "targetVersionId": "version-1",
            "rolledBackFromId": "deployment-2",
            "idempotencyKey": "internal-only-recovery-1",
        },
    )

    assert pipelines.last_deploy is not None
    assert pipelines.last_deploy["versionId"] == "version-1"
    assert external.rollback_targets == []
    infrastructure = result["lastOperation"]["result"]["infrastructure"]
    assert infrastructure == {
        "status": "skipped",
        "reason": "failed_candidate_never_became_live_existing_deploy_preserved",
    }


def test_pipeline_rollback_rejects_stale_external_provider_target_before_internal_change() -> None:
    external = _ExternalDelivery()
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal={"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"},
        versions=[
            {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
            {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
        ],
        deployments=[
            {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
            {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
        ],
        external_delivery=external,
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "targetVersionId": "version-1",
        "rolledBackFromId": "deployment-2",
        **external.target,
        "targetDeployId": "provider-attacker-selected",
        "idempotencyKey": "rollback-external-stale-1",
    }

    with pytest.raises(ConflictDetected, match="application rollback target"):
        service.rollback(RequestContext(roles=("admin",)), arguments)

    assert pipelines.deploy_count == 0
    assert external.rollback_targets == []


def test_pipeline_deploy_requires_the_version_merged_from_the_exact_proposal() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=[{"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"}],
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "versionId": "attacker-selected-version",
        "idempotencyKey": "deploy-1",
    }

    with pytest.raises(ConflictDetected, match="approved merged proposal"):
        service.deploy(RequestContext(roles=("admin",)), arguments)

    assert pipelines.last_deploy is None


def test_pipeline_deploy_replays_its_durable_receipt_after_the_gateway_crash_window() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=[{"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"}],
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-crash-1",
    }

    first = service.deploy(RequestContext(roles=("admin",)), arguments)
    recovered = service.deploy(RequestContext(roles=("admin",)), arguments)

    assert pipelines.deploy_count == 1
    assert len(pipelines.deployments) == 1
    assert recovered["lastOperation"] == first["lastOperation"]


def test_pipeline_deploy_receipt_cannot_be_rebound_to_different_arguments() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=[{"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"}],
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-binding-1",
    }
    service.deploy(RequestContext(roles=("admin",)), arguments)

    with pytest.raises(ConflictDetected, match="receipt does not match"):
        service.deploy(RequestContext(roles=("admin",)), {**arguments, "versionId": "version-attacker"})

    assert pipelines.deploy_count == 1


def test_pipeline_rollback_rejects_a_client_selected_prior_version() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=[
            {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
            {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
        ],
        deployments=[
            {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
            {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
        ],
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "targetVersionId": "attacker-selected-version",
        "rolledBackFromId": "deployment-2",
        "idempotencyKey": "rollback-1",
    }

    with pytest.raises(ConflictDetected, match="current server evidence"):
        service.rollback(RequestContext(roles=("admin",)), arguments)

    assert pipelines.last_deploy is None


def test_pipeline_rollback_replays_its_durable_receipt_after_the_gateway_crash_window() -> None:
    proposal = {"id": "pp-2", "pipelineId": "pipe-1", "status": "executed"}
    service, pipelines = _service(
        ontology_proposal={"id": "op-1", "status": "submitted"},
        active_ontology_version=1,
        pipeline_proposal=proposal,
        versions=[
            {"id": "version-2", "pipelineId": "pipe-1", "proposalId": "pp-2"},
            {"id": "version-1", "pipelineId": "pipe-1", "proposalId": "pp-1"},
        ],
        deployments=[
            {"id": "deployment-2", "versionId": "version-2", "status": "PROMOTED"},
            {"id": "deployment-1", "versionId": "version-1", "status": "PROMOTED"},
        ],
    )
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "pp-2",
        "pipelineId": "pipe-1",
        "targetVersionId": "version-1",
        "rolledBackFromId": "deployment-2",
        "idempotencyKey": "rollback-crash-1",
    }

    first = service.rollback(RequestContext(roles=("admin",)), arguments)
    recovered = service.rollback(RequestContext(roles=("admin",)), arguments)

    assert pipelines.deploy_count == 1
    assert len(pipelines.deployments) == 3
    assert recovered["lastOperation"] == first["lastOperation"]


def test_ontology_rollback_rejects_a_client_selected_version() -> None:
    proposal = {
        "id": "op-1",
        "status": "applied",
        "appliedOntologyVersion": {"versionNumber": 4},
    }
    service, _ = _service(ontology_proposal=proposal, active_ontology_version=4)
    arguments = {
        "releaseKind": "ontology",
        "proposalId": "op-1",
        "targetVersionNumber": 1,
        "expectedActiveVersionNumber": 4,
        "idempotencyKey": "rollback-1",
    }

    with pytest.raises(ConflictDetected, match="current server evidence"):
        service.rollback(RequestContext(roles=("admin",)), arguments)


def test_ontology_rollback_replays_its_durable_receipt_after_the_gateway_crash_window() -> None:
    proposal = {
        "id": "op-1",
        "status": "applied",
        "appliedOntologyVersion": {"versionNumber": 4},
    }
    service, _ = _service(ontology_proposal=proposal, active_ontology_version=4)
    ontology = cast(_Ontology, service.ontology)
    arguments = {
        "releaseKind": "ontology",
        "proposalId": "op-1",
        "targetVersionNumber": 3,
        "expectedActiveVersionNumber": 4,
        "idempotencyKey": "ontology-rollback-crash-1",
    }

    first = service.rollback(RequestContext(roles=("admin",)), arguments)
    recovered = service.rollback(RequestContext(roles=("admin",)), arguments)

    assert ontology.rollback_count == 1
    assert ontology.active_version == 5
    assert recovered["lastOperation"] == first["lastOperation"]


def test_ontology_decision_respects_assigned_human_reviewer() -> None:
    service, _ = _service(
        ontology_proposal={
            "id": "op-1",
            "status": "in_review",
            "fingerprint": "sha256:proposal",
            "assigneeUserId": "reviewer-2",
        },
        active_ontology_version=1,
    )

    with pytest.raises(PermissionDenied, match="assigned human reviewer"):
        service.submit_decision(
            RequestContext(actor_user_id="reviewer-1", roles=("admin",)),
            {
                "releaseKind": "ontology",
                "proposalId": "op-1",
                "decision": "approve",
                "expectedFingerprint": "sha256:proposal",
                "idempotencyKey": "decision-1",
            },
        )


def test_ontology_decision_rejects_an_unassigned_proposal() -> None:
    service, _ = _service(
        ontology_proposal={
            "id": "op-1",
            "status": "submitted",
            "fingerprint": "sha256:proposal",
            "assigneeUserId": None,
        },
        active_ontology_version=1,
    )

    with pytest.raises(ValidationFailed, match="must be assigned"):
        service.submit_decision(
            RequestContext(actor_user_id="reviewer-1", roles=("admin",)),
            {
                "releaseKind": "ontology",
                "proposalId": "op-1",
                "decision": "approve",
                "expectedFingerprint": "sha256:proposal",
                "idempotencyKey": "decision-1",
            },
        )
