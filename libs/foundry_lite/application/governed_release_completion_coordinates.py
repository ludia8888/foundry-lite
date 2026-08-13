"""Resolve app-only completion coordinates from authoritative release ledgers."""

from __future__ import annotations

from typing import ClassVar

from foundry_lite.application.ports.ai_run_repository import AiRunRepository
from foundry_lite.application.ports.governed_release_live_attestation_repository import GovernedReleaseLiveAuthority
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryKind,
    ReleaseDeliveryRepository,
)
from foundry_lite.application.ports.runtime_repository import RuntimeRepository
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.governed_release_live_authority import (
    is_exact_live_reviewer_identity,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_loader import (
    GovernedReleaseLiveCollectionDatabaseLoader,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ServerLoadedDatabaseSnapshot,
)
from foundry_lite.application.services.aip.governed_release_status_projection import (
    unavailable_completion_coordinates,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_ROOT_LIMIT = 5
_PURPOSE = "rollback_rehearsal"
_RELEASE_KINDS: tuple[ReleaseDeliveryKind, ...] = ("ontology", "pipeline")


class GovernedReleaseCompletionReader(CoreService):
    """Read-only service used by release status without mutation collaborators."""

    required_dependencies = (
        "ai_run_repository",
        "engine",
        "governed_release_live_authority",
        "release_delivery_repository",
        "runtime_repository",
    )
    required_collaborators: ClassVar[tuple[str, ...]] = ()
    governed_release_live_authority: GovernedReleaseLiveAuthority

    def completion_coordinates(self, ctx: RequestContext, application_id: str) -> dict[str, object]:
        if not self.governed_release_live_authority.is_live_eligible_for(application_id):
            return unavailable_completion_coordinates("authentic_live_collector_not_eligible")
        resolver = GovernedReleaseCompletionCoordinateResolver(
            self.release_delivery_repository,
            self.ai_run_repository,
            self.runtime_repository,
        )
        with self.engine.begin() as transaction:
            return resolver.resolve(
                transaction=transaction,
                ctx=ctx,
                authority=self.governed_release_live_authority,
                tenant_id=ctx.tenant_id,
                application_id=application_id,
            )


class GovernedReleaseCompletionCoordinateResolver:
    """Select only a complete, server-owned two-scenario golden workflow pair."""

    def __init__(
        self,
        deliveries: ReleaseDeliveryRepository,
        ai_runs: AiRunRepository,
        runtime: RuntimeRepository,
    ) -> None:
        self._deliveries = deliveries
        self._loader = GovernedReleaseLiveCollectionDatabaseLoader(deliveries, ai_runs, runtime)

    def resolve(
        self,
        *,
        transaction: TransactionContext,
        ctx: RequestContext,
        authority: GovernedReleaseLiveAuthority,
        tenant_id: str,
        application_id: str,
    ) -> dict[str, object]:
        roots = {
            kind: self._deliveries.list_workflow_roots(
                transaction=transaction,
                tenant_id=tenant_id,
                application_id=application_id,
                release_kind=kind,
                limit=_ROOT_LIMIT,
            )
            for kind in _RELEASE_KINDS
        }
        for ontology in roots["ontology"]:
            for pipeline in roots["pipeline"]:
                snapshot = self._complete_pair(
                    transaction,
                    tenant_id,
                    application_id,
                    ontology.workflow_run_id,
                    pipeline.workflow_run_id,
                )
                if snapshot is not None and _is_current_reviewer(ctx, authority, snapshot):
                    return _eligible(ontology.workflow_run_id, pipeline.workflow_run_id)
        return _ineligible("current_reviewer_completed_golden_workflow_not_found")

    def _complete_pair(
        self,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> ServerLoadedDatabaseSnapshot | None:
        try:
            return self._loader.load(
                transaction=transaction,
                tenant_id=tenant_id,
                application_id=application_id,
                ontology_workflow_run_id=ontology_workflow_run_id,
                pipeline_workflow_run_id=pipeline_workflow_run_id,
            )
        except (ConflictDetected, ValidationFailed):
            return None


def _is_current_reviewer(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    snapshot: ServerLoadedDatabaseSnapshot,
) -> bool:
    return is_exact_live_reviewer_identity(
        ctx,
        authority,
        snapshot.application_id,
        snapshot.reviewer_subject_hash,
        snapshot.reviewer_oauth_session_hash,
    )


def _eligible(ontology_run_id: str, pipeline_run_id: str) -> dict[str, object]:
    return {
        "attestationPurpose": _PURPOSE,
        "ontologyWorkflowRunId": ontology_run_id,
        "pipelineWorkflowRunId": pipeline_run_id,
        "isEligible": True,
        "nextAction": "verify_release_completion",
        "reason": None,
    }


def _ineligible(reason: str) -> dict[str, object]:
    return {
        "attestationPurpose": _PURPOSE,
        "ontologyWorkflowRunId": None,
        "pipelineWorkflowRunId": None,
        "isEligible": False,
        "nextAction": None,
        "reason": reason,
    }


__all__ = ["GovernedReleaseCompletionCoordinateResolver", "GovernedReleaseCompletionReader"]
