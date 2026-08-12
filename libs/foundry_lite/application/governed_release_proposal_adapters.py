"""Explicit proposal and branch adapters for Governed Release composition."""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_lite.application.services.aip.governed_release_service import (
    OntologyProposalBoundary,
    PipelineReleaseBoundary,
)
from foundry_lite.application.services.ontology_branch_service import OntologyBranchService
from foundry_lite.application.services.ontology_proposal_service import OntologyProposalService
from foundry_lite.domain.context import RequestContext


class GovernedReleaseProposalReader:
    """Read exact Ontology or Pipeline proposals with their source branch."""

    def __init__(
        self,
        ontology: OntologyProposalBoundary,
        pipelines: PipelineReleaseBoundary,
    ) -> None:
        self.ontology = ontology
        self.pipelines = pipelines

    def load_governed_release_proposal(
        self,
        ctx: RequestContext,
        release_kind: str,
        proposal_id: str,
    ) -> dict[str, object]:
        if release_kind == "ontology":
            proposal = self.ontology.get_proposal(proposal_id, ctx=ctx)
            return _with_ontology_source_branch(proposal)
        proposal = self.pipelines.get_proposal(proposal_id, ctx=ctx)
        return _with_pipeline_source_branch(self.pipelines, proposal, ctx)


class OntologyReleaseWorkflow:
    """Combine branch and proposal services without widening either service."""

    def __init__(self, branches: OntologyBranchService, proposals: OntologyProposalService) -> None:
        self.branches = branches
        self.proposals = proposals

    def create_branch(
        self,
        *,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.branches.create_branch(name=name, idempotency_key=idempotency_key, ctx=ctx)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.proposals.list_proposals(status=status, cursor=cursor, limit=limit, ctx=ctx)

    def get_proposal(
        self,
        proposal_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.proposals.get_proposal(proposal_id, ctx=ctx)

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_user_id: str,
        is_unassigned_only: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.proposals.assign_reviewer(
            proposal_id,
            reviewer_user_id=reviewer_user_id,
            is_unassigned_only=is_unassigned_only,
            ctx=ctx,
        )


def _with_pipeline_source_branch(
    pipelines: PipelineReleaseBoundary,
    proposal: Mapping[str, object],
    ctx: RequestContext,
) -> dict[str, object]:
    result = dict(proposal)
    branch_id = proposal.get("branchId")
    if not isinstance(branch_id, str) or not branch_id:
        return result
    branch = pipelines.get_branch(branch_id, ctx=ctx)
    branch_name = branch.get("name")
    if isinstance(branch_name, str) and branch_name:
        result["sourceBranch"] = {"branchId": branch_id, "branchName": branch_name}
    return result


def _with_ontology_source_branch(proposal: Mapping[str, object]) -> dict[str, object]:
    result = dict(proposal)
    identity = _ontology_branch_identity(proposal.get("description"))
    if identity is not None:
        result["sourceBranch"] = identity
    return result


def _ontology_branch_identity(description: object) -> dict[str, object] | None:
    marker = "[ontology-branch-diff] "
    if not isinstance(description, str) or marker not in description:
        return None
    try:
        payload = json.loads(description.rsplit(marker, 1)[1])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    branch_id, branch_name = payload.get("branchId"), payload.get("branchName")
    if not isinstance(branch_id, str) or not isinstance(branch_name, str):
        return None
    # Carry the per-resource changes through as well. A reviewer approves ontology resources one
    # by one, so the candidate has to name them; the migration plan only lists what threatens
    # compatibility, which leaves a pure addition looking like an empty change set.
    resources = payload.get("resources")
    identity: dict[str, object] = {"branchId": branch_id, "branchName": branch_name}
    if isinstance(resources, list):
        identity["resources"] = [dict(item) for item in resources if isinstance(item, Mapping)]
    return identity


__all__ = ["GovernedReleaseProposalReader", "OntologyReleaseWorkflow"]
