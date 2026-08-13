"""Typed collaborators used by the Governed Release application service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.domain.context import RequestContext


class GovernedReleaseProposalReader(Protocol):
    """Load normalized proposal data without exposing provider-specific services."""

    def load_governed_release_proposal(
        self,
        ctx: RequestContext,
        release_kind: str,
        proposal_id: str,
    ) -> dict[str, object]:
        """Return the proposal snapshot used for a governed release decision."""
        ...


class OntologyProposalBoundary(Protocol):
    """Review and execute ontology proposals through their owning service."""

    def get_proposal(
        self,
        proposal_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Return the current ontology proposal snapshot."""
        ...

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        expected_fingerprint: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Record an approve or reject decision against the expected proposal."""
        ...

    def execute_proposal(
        self,
        proposal_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Activate an approved ontology proposal with fingerprint protection."""
        ...


class OntologyReleaseBoundary(Protocol):
    """Read and restore versioned ontology release state."""

    def release_active_version_summary(
        self,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        """Return the active ontology version used to bind rollback checks."""
        ...

    def rollback_to_version(
        self,
        version_number: int,
        *,
        expected_active_version_number: int | None = None,
        idempotency_key: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        """Restore a prior ontology version with idempotency and CAS guards."""
        ...

    def replay_rollback(
        self,
        version_number: int,
        *,
        expected_active_version_number: int,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object] | None:
        """Return a completed rollback for the exact idempotency binding."""
        ...


class PipelineReleaseBoundary(Protocol):
    """Review, execute, deploy, and replay pipeline release operations."""

    def get_branch(
        self,
        branch_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Return the isolated pipeline branch bound to a proposal."""
        ...

    def get_proposal(
        self,
        proposal_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Return the current pipeline proposal snapshot."""
        ...

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Record an approve or reject decision for a pipeline proposal."""
        ...

    def execute_proposal(
        self,
        proposal_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Merge an approved pipeline proposal into its internal target."""
        ...

    def list_versions(
        self, pipeline_id: str, *, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        """List bounded pipeline versions for deployment and rollback selection."""
        ...

    def list_deployments(
        self, pipeline_id: str, *, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        """List bounded pipeline deployment receipts."""
        ...

    def replay_deployment(
        self,
        idempotency_key: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object] | None:
        """Return a completed deployment for an exact idempotency key."""
        ...

    def deploy(
        self,
        pipeline_id: str,
        version_id: str,
        *,
        idempotency_key: str,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Promote an exact pipeline version using idempotent deployment options."""
        ...


class GovernedReleaseStatusBoundary(Protocol):
    """Read durable audit evidence used to build release status timelines."""

    def list_release_audit_events(
        self,
        resource_refs: list[tuple[str, str]],
        event_types: tuple[str, ...],
        *,
        limit: int = 100,
        ctx: RequestContext | None = None,
    ) -> list[Mapping[str, object]]:
        """Return relevant audit events for the requested release resources."""
        ...


class GovernedReleaseCompletionBoundary(Protocol):
    """Resolve app-only completion inputs from server-owned golden ledgers."""

    def completion_coordinates(
        self,
        ctx: RequestContext,
        application_id: str,
    ) -> dict[str, object]:
        """Return only coordinates bound to the current OAuth reviewer."""
        ...


__all__ = [
    "GovernedReleaseCompletionBoundary",
    "GovernedReleaseProposalReader",
    "GovernedReleaseStatusBoundary",
    "OntologyProposalBoundary",
    "OntologyReleaseBoundary",
    "PipelineReleaseBoundary",
]
