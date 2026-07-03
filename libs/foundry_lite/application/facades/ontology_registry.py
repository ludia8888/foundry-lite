"""Thin facade entrypoints for ontology registry workflows."""

from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports import (
    OntologyApplyResult,
    OntologyCatalogResult,
    OntologyRollbackResult,
    OntologyValidationResult,
)
from foundry_lite.application.services.ontology_insights import (
    DEFAULT_USAGE_WINDOW_DAYS,
    OntologyResourceDependentsResult,
    OntologyResourceUsageResult,
)
from foundry_lite.application.services.ontology_insights_service import OntologyInsightsService
from foundry_lite.application.services.ontology_proposal_service import OntologyProposalService
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class OntologyRegistry:
    """Ontology bounded context: import, validate, activate, propose, and roll back ontology versions."""

    def __init__(
        self,
        ontology: OntologyService,
        insights: OntologyInsightsService,
        proposals: OntologyProposalService,
    ) -> None:
        self._ontology = ontology
        self._insights = insights
        self._proposals = proposals

    def apply(self, yaml_path: str | Path, *, ctx: RequestContext | None = None) -> OntologyApplyResult:
        return self._ontology.apply_ontology(yaml_path, ctx=ctx)

    def apply_text(self, yaml_text: str, *, ctx: RequestContext | None = None) -> OntologyApplyResult:
        return self._ontology.apply_ontology_text(yaml_text, ctx=ctx)

    def validate(self, yaml_text: str, *, ctx: RequestContext | None = None) -> OntologyValidationResult:
        return self._ontology.validate_yaml_text(yaml_text, ctx=ctx)

    def rollback(self, version_number: int, *, ctx: RequestContext | None = None) -> OntologyRollbackResult:
        return self._ontology.rollback_to_version(version_number, ctx=ctx)

    def catalog(self, *, ctx: RequestContext | None = None) -> OntologyCatalogResult:
        return self._ontology.active_catalog(ctx=ctx)

    def resource_usage(
        self,
        resource_type: str,
        api_name: str,
        *,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
        ctx: RequestContext | None = None,
    ) -> OntologyResourceUsageResult:
        return self._insights.resource_usage(resource_type, api_name, window_days=window_days, ctx=ctx)

    def resource_dependents(
        self,
        resource_type: str,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyResourceDependentsResult:
        return self._insights.resource_dependents(resource_type, api_name, ctx=ctx)

    def submit_proposal(
        self,
        *,
        yaml_text: str,
        title: str,
        idempotency_key: str,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.submit_proposal(
            yaml_text=yaml_text,
            title=title,
            idempotency_key=idempotency_key,
            description=description,
            ctx=ctx,
        )

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_user_id: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.assign_reviewer(proposal_id, reviewer_user_id=reviewer_user_id, ctx=ctx)

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        expected_fingerprint: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.decide_proposal(
            proposal_id,
            decision=decision,
            expected_fingerprint=expected_fingerprint,
            comment=comment,
            ctx=ctx,
        )

    def update_proposal(
        self,
        proposal_id: str,
        *,
        yaml_text: str,
        expected_fingerprint: str,
        title: str | None = None,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.update_proposal(
            proposal_id,
            yaml_text=yaml_text,
            expected_fingerprint=expected_fingerprint,
            title=title,
            description=description,
            ctx=ctx,
        )

    def execute_proposal(
        self,
        proposal_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.execute_proposal(proposal_id, expected_fingerprint=expected_fingerprint, ctx=ctx)

    def withdraw_proposal(
        self,
        proposal_id: str,
        *,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.withdraw_proposal(proposal_id, reason=reason, ctx=ctx)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._proposals.list_proposals(status=status, cursor=cursor, limit=limit, ctx=ctx)

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._proposals.get_proposal(proposal_id, ctx=ctx)
