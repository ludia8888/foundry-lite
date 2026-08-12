"""Server-owned lineage rules for one Governed Release delivery workflow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryKind,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


@dataclass(frozen=True, slots=True)
class ReleaseDeliveryLineage:
    """Internal-only workflow coordinates persisted with a delivery intent."""

    application_id: str
    release_kind: ReleaseDeliveryKind
    workflow_run_id: str
    parent_delivery_id: str | None


class DeliveryLineageReader(Protocol):
    """Minimal tenant-scoped ledger reads needed for lineage resolution."""

    def get(self, ctx: RequestContext, delivery_id: str) -> ReleaseDeliveryRecord | None: ...

    def list_for_proposal(
        self,
        ctx: RequestContext,
        proposal_id: str,
    ) -> tuple[ReleaseDeliveryRecord, ...]: ...


class ExternalReleaseDeliveryLineageResolver:
    """Resolve predecessor rows without accepting lineage coordinates from callers."""

    def __init__(self, ledger: DeliveryLineageReader) -> None:
        self._ledger = ledger

    def for_parent(
        self,
        ctx: RequestContext,
        proposal_id: str,
        parent: ReleaseDeliveryRecord,
        expected_parent_operations: tuple[ReleaseDeliveryOperation, ...],
    ) -> ReleaseDeliveryLineage:
        return child_delivery_lineage(ctx, parent, proposal_id, expected_parent_operations)

    def for_replay(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        expected_parent_operations: tuple[ReleaseDeliveryOperation, ...],
    ) -> ReleaseDeliveryLineage:
        parent_id = row.parent_delivery_id
        parent = self._ledger.get(ctx, parent_id) if parent_id is not None else None
        if parent is None:
            raise ConflictDetected("release delivery replay parent is unavailable")
        lineage = self.for_parent(ctx, row.proposal_id, parent, expected_parent_operations)
        require_delivery_lineage(row, lineage)
        return lineage

    def for_application_deploy(
        self,
        ctx: RequestContext,
        proposal_id: str,
        commit_id: str,
        replay: ReleaseDeliveryRecord | None,
    ) -> ReleaseDeliveryLineage:
        if replay is not None:
            return self.for_replay(ctx, replay, ("source_merge",))
        rows = self._ledger.list_for_proposal(ctx, proposal_id)
        parent = landed_source_merge_for_commit(rows, commit_id)
        return self.for_parent(ctx, proposal_id, parent, ("source_merge",))

    def for_application_rollback(
        self,
        ctx: RequestContext,
        proposal_id: str,
        current_resource_id: str,
        replay: ReleaseDeliveryRecord | None,
    ) -> ReleaseDeliveryLineage:
        if replay is not None:
            return self.for_replay(ctx, replay, ("application_deploy", "application_rollback"))
        rows = self._ledger.list_for_proposal(ctx, proposal_id)
        parent = landed_application_delivery_for_resource(rows, current_resource_id)
        return self.for_parent(ctx, proposal_id, parent, ("application_deploy", "application_rollback"))


def root_delivery_lineage(
    ctx: RequestContext,
    release_kind: str,
    ai_run_id: str,
) -> ReleaseDeliveryLineage:
    """Create a source-publication root from authenticated server context."""

    return ReleaseDeliveryLineage(
        application_id=_application_id(ctx),
        release_kind=_release_kind(release_kind),
        workflow_run_id=ai_run_id,
        parent_delivery_id=None,
    )


def child_delivery_lineage(
    ctx: RequestContext,
    parent: ReleaseDeliveryRecord,
    proposal_id: str,
    expected_parent_operations: tuple[ReleaseDeliveryOperation, ...],
) -> ReleaseDeliveryLineage:
    """Inherit a root only from one exact landed predecessor row."""

    expected = (ctx.tenant_id, _application_id(ctx), proposal_id)
    observed = (parent.tenant_id, parent.application_id, parent.proposal_id)
    if observed != expected or parent.operation not in expected_parent_operations:
        raise ConflictDetected("release delivery parent does not match the server-owned workflow")
    if parent.status != "landed" or not parent.workflow_run_id:
        raise ConflictDetected("release delivery parent is not a landed workflow receipt")
    _require_parent_shape(parent)
    return ReleaseDeliveryLineage(
        application_id=parent.application_id,
        release_kind=parent.release_kind,
        workflow_run_id=parent.workflow_run_id,
        parent_delivery_id=parent.delivery_id,
    )


def require_delivery_lineage(
    row: ReleaseDeliveryRecord,
    lineage: ReleaseDeliveryLineage,
) -> None:
    """Reject a replay whose immutable stored lineage differs from its parent."""

    observed = (
        row.application_id,
        row.release_kind,
        row.workflow_run_id,
        row.parent_delivery_id,
    )
    expected = (
        lineage.application_id,
        lineage.release_kind,
        lineage.workflow_run_id,
        lineage.parent_delivery_id,
    )
    if observed != expected:
        raise ConflictDetected("release delivery replay lineage does not match its durable parent")


def landed_source_merge_for_commit(
    rows: Sequence[ReleaseDeliveryRecord],
    commit_id: str,
) -> ReleaseDeliveryRecord:
    """Resolve exactly one landed source merge for an application deployment."""

    matches = [
        row
        for row in rows
        if row.operation == "source_merge"
        and row.status == "landed"
        and _result_text(row, "mergeCommitSha") == commit_id
    ]
    return _one_parent(matches, "application deployment requires one exact landed source merge")


def landed_application_delivery_for_resource(
    rows: Sequence[ReleaseDeliveryRecord],
    provider_resource_id: str,
) -> ReleaseDeliveryRecord:
    """Resolve the exact current application receipt selected for rollback."""

    matches = [
        row
        for row in rows
        if row.operation in {"application_deploy", "application_rollback"}
        and row.status == "landed"
        and row.provider_resource_id == provider_resource_id
    ]
    return _one_parent(matches, "application rollback current delivery is missing or ambiguous")


def _one_parent(
    rows: Sequence[ReleaseDeliveryRecord],
    message: str,
) -> ReleaseDeliveryRecord:
    if len(rows) != 1:
        raise ConflictDetected(message, details={"matchingDeliveryCount": len(rows)})
    return rows[0]


def _require_parent_shape(parent: ReleaseDeliveryRecord) -> None:
    if parent.operation == "source_publish":
        if parent.parent_delivery_id is not None or parent.workflow_run_id != parent.ai_run_id:
            raise ConflictDetected("source publication is not a valid workflow root")
        return
    if parent.parent_delivery_id is None:
        raise ConflictDetected("release delivery parent is detached from its workflow root")


def _result_text(row: ReleaseDeliveryRecord, key: str) -> str | None:
    value = row.result_ref.get(key) if row.result_ref is not None else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _application_id(ctx: RequestContext) -> str:
    value = ctx.application_id
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("external release delivery requires an authenticated application identity")
    return value.strip()


def _release_kind(value: str) -> ReleaseDeliveryKind:
    if value == "ontology":
        return "ontology"
    if value == "pipeline":
        return "pipeline"
    raise ValidationFailed("release kind must be ontology or pipeline")


__all__ = [
    "ReleaseDeliveryLineage",
    "ExternalReleaseDeliveryLineageResolver",
    "child_delivery_lineage",
    "landed_application_delivery_for_resource",
    "landed_source_merge_for_commit",
    "root_delivery_lineage",
    "require_delivery_lineage",
]
