"""Composition helper that keeps the FoundryLite root thin."""

from __future__ import annotations

from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.facades.governed_release_workspace import GovernedReleaseWorkspace
from foundry_lite.application.governed_release_completion_coordinates import (
    GovernedReleaseCompletionReader,
)
from foundry_lite.application.governed_release_proposal_adapters import (
    GovernedReleaseProposalReader,
    OntologyReleaseWorkflow,
)
from foundry_lite.application.services.aip.fde_mcp_service import FdeMcpGateway
from foundry_lite.application.services.aip.fde_mcp_sessions import FdeMcpSessionLedger
from foundry_lite.application.services.aip.governed_release_mcp import GovernedReleaseMcpGateway
from foundry_lite.application.services.aip.governed_release_security import GovernedReleaseSecurityLedger
from foundry_lite.application.services.aip.governed_release_service import GovernedReleaseService
from foundry_lite.application.services.aip.governed_release_workflow import GovernedReleaseWorkflowService


def _release_sessions(builder_mcp: FdeMcpGateway) -> FdeMcpSessionLedger:
    return FdeMcpSessionLedger(
        builder_mcp.engine,
        builder_mcp.osdk_application_repository,
        plane="release",
    )


def _completion_reader(services: CoreServices) -> GovernedReleaseCompletionReader:
    live_attestations = services.governed_release_live_attestation
    return GovernedReleaseCompletionReader(
        ai_run_repository=live_attestations.ai_run_repository,
        engine=live_attestations.engine,
        governed_release_live_authority=live_attestations.governed_release_live_authority,
        release_delivery_repository=live_attestations.release_delivery_repository,
        runtime_repository=live_attestations.runtime_repository,
    )


def build_governed_release_workspace(
    services: CoreServices,
    builder_mcp: FdeMcpGateway,
) -> GovernedReleaseWorkspace:
    proposal_reader = GovernedReleaseProposalReader(
        services.ontology.proposals,
        services.pipelines.entrypoint,
    )
    release_service = GovernedReleaseService(
        proposal_reader=proposal_reader,
        ontology_proposals=services.ontology.proposals,
        ontology=services.ontology.entrypoint,
        pipelines=services.pipelines.entrypoint,
        status_reader=services.runtime,
        external_delivery=services.external_release_delivery,
        completion_reader=_completion_reader(services),
    )
    workflow_service = GovernedReleaseWorkflowService(
        ontology=OntologyReleaseWorkflow(services.ontology.branches, services.ontology.proposals),
        pipelines=services.pipelines.entrypoint,
    )
    security = GovernedReleaseSecurityLedger(
        builder_mcp.engine,
        builder_mcp.ai_run_repository,
        builder_mcp.policy,
        services.runtime,
    )
    gateway = GovernedReleaseMcpGateway(
        release_service=release_service,
        workflow_service=workflow_service,
        application_reader=services.osdk_applications.entrypoint,
        access_session_validator=services.osdk_access_sessions,
        sessions=_release_sessions(builder_mcp),
        rate_limits=services.mcp_rate_limits,
        security=security,
        live_attestation_service=services.governed_release_live_attestation,
    )
    return GovernedReleaseWorkspace(gateway, services.governed_release_live_attestation)


__all__ = ["build_governed_release_workspace"]
