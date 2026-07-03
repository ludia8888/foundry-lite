"""Ontology branch diff/rebase/propose/abandon/reconcile workflows.

These module functions keep ``OntologyBranchService`` within the application
module size cap, mirroring how proposal revision lives beside its service.
Rebase merges the branch's changes onto the CURRENT active definition in the
pure diff module and serializes the result as JSON text (a strict subset of
YAML 1.2 that the ontology loader accepts). Merging main happens exclusively
through the proposal black box: propose enforces Palantir's up-to-date rule
(a stale base must rebase first) and reconcile flips open -> merged only once
the proposal reports it was executed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.ontology_branch_repository import (
    OntologyBranchRebase,
    OntologyBranchRepository,
    OntologyBranchRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.ontology_branch_diff import (
    BranchDiffResult,
    ResourceKey,
    ResourceMap,
    evaluate_branch_diff,
    merge_branch_onto_main,
    parse_resource_map,
    serialize_definition_yaml,
    unknown_resolutions,
    unresolved_conflicts,
)
from foundry_lite.application.services.ontology_branch_payloads import (
    ActiveOntologySnapshot,
    branch_payload,
    require_branch_fingerprint_match,
    require_open_branch,
    required_text,
)
from foundry_lite.application.services.ontology_proposal_payloads import yaml_fingerprint
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.state_transitions import ONTOLOGY_BRANCH_ABANDONED, ONTOLOGY_BRANCH_MERGED
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.security.policy import PolicyService

_RESOURCE_TYPE = "ontology_branch"
_RESOLUTION_CHOICES = ("main", "branch")
_LIVE_PROPOSAL_STATUSES = ("submitted", "in_review", "approved", "applied")


class OntologyBranchBoundary(Protocol):
    """Surface of ``OntologyBranchService`` these workflows use: the dependency-graph
    gate forbids importing the service module back into this helper module."""

    engine: TransactionManager
    policy: PolicyService
    runtime_service: OntologyRuntimeBoundary
    ontology_service: OntologyService
    ontology_branch_repository: OntologyBranchRepository

    def _active_version_id(self, conn: TransactionContext, ctx: RequestContext) -> str | None: ...

    def _audit_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        row: OntologyBranchRow,
        *,
        before: OntologyBranchRow | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None: ...

    def _is_base_stale(self, row: OntologyBranchRow, active_version_id: str | None) -> bool: ...

    def _proposal_by_id(self, proposal_id: str, ctx: RequestContext) -> dict[str, object] | None: ...

    def _require_active_snapshot(self, conn: TransactionContext, ctx: RequestContext) -> ActiveOntologySnapshot: ...

    def _require_branch_row(
        self, conn: TransactionContext, ctx: RequestContext, branch_id: str
    ) -> OntologyBranchRow: ...

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None: ...

    def _submit_branch_proposal(
        self,
        *,
        yaml_text: str,
        title: str,
        idempotency_key: str,
        description: str,
        ctx: RequestContext,
    ) -> dict[str, object]: ...


def diff_ontology_branch(
    service: OntologyBranchBoundary,
    branch_id: str,
    *,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    """Three-way resource-level diff plus the merge-time migration plan."""
    ctx = ctx or RequestContext()
    service.policy.require(ctx, "ontology:validate")
    with service.engine.begin() as conn:
        row = service._require_branch_row(conn, ctx, branch_id)
        snapshot = service._require_active_snapshot(conn, ctx)
    diff = _three_way_diff(row, snapshot)
    # Merge impact via the validate black box: content vs CURRENT active
    # (blocked changes, warnings, and reindex operations for the UI).
    validation = service.ontology_service.validate_yaml_text(row["content_yaml_text"], ctx=ctx)
    return {
        "branchId": row["id"],
        "baseStale": snapshot.version["id"] != row["base_version_id"],
        "resources": list(diff["resources"]),
        "conflicts": list(diff["conflicts"]),
        "migrationPlan": dict(validation["migration_plan"]),
    }


def rebase_ontology_branch(
    service: OntologyBranchBoundary,
    branch_id: str,
    *,
    resolutions: Sequence[Mapping[str, object]],
    expected_fingerprint: str,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    """Re-anchor the branch on the current active definition (CAS-guarded)."""
    ctx = ctx or RequestContext()
    service.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, branch_id)
    service._require_write_open(ctx, "rebase_ontology_branch", branch_id)
    parsed = _parse_resolutions(resolutions)
    with service.engine.begin() as conn:
        row = service._require_branch_row(conn, ctx, branch_id)
        require_open_branch(row)
        require_branch_fingerprint_match(row, expected_fingerprint)
        snapshot = service._require_active_snapshot(conn, ctx)
    if snapshot.version["id"] == row["base_version_id"]:
        raise ConflictDetected(
            "ontology branch is already based on the active ontology version",
            details={"branch_id": branch_id, "baseVersionNumber": row["base_version_number"]},
        )
    merged_yaml = _merged_content(branch_id, row, snapshot, parsed)
    # The merged working copy must still be structurally valid (a blocked
    # migration plan is fine on a branch; structural errors are not).
    service.ontology_service.validate_yaml_text(merged_yaml, ctx=ctx)
    return _store_rebase(service, ctx, branch_id, expected_fingerprint, snapshot, merged_yaml, parsed)


def propose_ontology_branch(
    service: OntologyBranchBoundary,
    branch_id: str,
    *,
    title: str,
    idempotency_key: str,
    description: str | None,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    """Submit the branch working copy through the proposal black box."""
    ctx = ctx or RequestContext()
    service.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, branch_id)
    service._require_write_open(ctx, "propose_ontology_branch", branch_id)
    required_text(title, "title")
    required_text(idempotency_key, "Idempotency-Key")
    with service.engine.begin() as conn:
        row = service._require_branch_row(conn, ctx, branch_id)
        require_open_branch(row)
        snapshot = service._require_active_snapshot(conn, ctx)
    _require_not_base_stale(row, snapshot)
    replay = _propose_replay(service, ctx, row)
    if replay is not None:
        return replay
    proposal = service._submit_branch_proposal(
        yaml_text=row["content_yaml_text"],
        title=title,
        idempotency_key=idempotency_key,
        description=_description_with_diff(description, row),
        ctx=ctx,
    )
    return _store_proposal(service, ctx, branch_id, proposal)


def abandon_ontology_branch(
    service: OntologyBranchBoundary,
    branch_id: str,
    *,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    """Terminally close an open branch; its name becomes reusable."""
    ctx = ctx or RequestContext()
    service.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, branch_id)
    service._require_write_open(ctx, "abandon_ontology_branch", branch_id)
    with service.engine.begin() as conn:
        before = service._require_branch_row(conn, ctx, branch_id)
        if before["status"] == "abandoned":
            return branch_payload(before, is_base_stale=False)
        if before["created_by"] != ctx.actor_user_id:
            # Not the creator: abandoning someone else's branch is a
            # governance action, so it demands the activate permission.
            service.runtime_service._require_or_audit(ctx, "ontology:activate", _RESOURCE_TYPE, branch_id)
        result = service.ontology_branch_repository.transition_branch_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            transition=ONTOLOGY_BRANCH_ABANDONED,
            merged_version_number=None,
            updated_at=_now(),
        )
        if not result.updated:
            raise ConflictDetected(
                "ontology branch can no longer be abandoned",
                details={"branch_id": branch_id, "status": result.current_status},
            )
        after = service._require_branch_row(conn, ctx, branch_id)
        service._audit_event(conn, ctx, "abandoned", after, before=before)
        return branch_payload(after, is_base_stale=False)


def reconcile_ontology_branch_merge(
    service: OntologyBranchBoundary,
    branch_id: str,
    *,
    ctx: RequestContext | None = None,
) -> None:
    """CAS open -> merged once the branch's proposal reports execution.

    This is derived-state convergence of an already-governed merge (the
    proposal execution went through permission, traffic, and audit gates), so
    it deliberately runs without a separate write-traffic gate: reads must be
    able to converge branch status without being writable themselves.
    """
    ctx = ctx or RequestContext()
    service.policy.require(ctx, "ontology:validate")
    with service.engine.begin() as conn:
        row = service.ontology_branch_repository.branch_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, branch_id=branch_id
        )
    if row is None or row["status"] != "open" or row["merged_proposal_id"] is None:
        return
    merged_version_number = _applied_version_number(service, ctx, row["merged_proposal_id"])
    if merged_version_number is None:
        return
    with service.engine.begin() as conn:
        result = service.ontology_branch_repository.transition_branch_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            transition=ONTOLOGY_BRANCH_MERGED,
            merged_version_number=merged_version_number,
            updated_at=_now(),
        )
        if result.updated:
            after = service._require_branch_row(conn, ctx, branch_id)
            service._audit_event(conn, ctx, "merged", after, before=row)


def _three_way_diff(row: OntologyBranchRow, snapshot: ActiveOntologySnapshot) -> BranchDiffResult:
    base = parse_resource_map(row["base_yaml_text"])
    branch = parse_resource_map(row["content_yaml_text"])
    main = parse_resource_map(snapshot.yaml_text)
    return evaluate_branch_diff(base, branch, main)


def _maps_for_merge(
    row: OntologyBranchRow, snapshot: ActiveOntologySnapshot
) -> tuple[ResourceMap, ResourceMap, ResourceMap]:
    return (
        parse_resource_map(row["base_yaml_text"]),
        parse_resource_map(row["content_yaml_text"]),
        parse_resource_map(snapshot.yaml_text),
    )


def _merged_content(
    branch_id: str,
    row: OntologyBranchRow,
    snapshot: ActiveOntologySnapshot,
    resolutions: dict[ResourceKey, str],
) -> str:
    base, branch, main = _maps_for_merge(row, snapshot)
    diff = evaluate_branch_diff(base, branch, main)
    unresolved = unresolved_conflicts(diff, resolutions)
    if unresolved:
        raise ValidationFailed(
            "every rebase conflict needs a resolution",
            details={"branch_id": branch_id, "unresolvedConflicts": [dict(entry) for entry in unresolved]},
        )
    unknown = unknown_resolutions(diff, resolutions)
    if unknown:
        raise ValidationFailed(
            "rebase resolutions reference resources that are not in conflict",
            details={"branch_id": branch_id, "unknownResolutions": [list(key) for key in unknown]},
        )
    merged = merge_branch_onto_main(base, branch, main, diff, resolutions)
    return serialize_definition_yaml(merged)


def _store_rebase(
    service: OntologyBranchBoundary,
    ctx: RequestContext,
    branch_id: str,
    expected_fingerprint: str,
    snapshot: ActiveOntologySnapshot,
    merged_yaml: str,
    resolutions: dict[ResourceKey, str],
) -> dict[str, object]:
    now = _now()
    with service.engine.begin() as conn:
        before = service._require_branch_row(conn, ctx, branch_id)
        after = service.ontology_branch_repository.rebase_branch(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            expected_fingerprint=expected_fingerprint,
            rebase=OntologyBranchRebase(
                base_version_id=snapshot.version["id"],
                base_version_number=snapshot.version["version_number"],
                base_yaml_text=snapshot.yaml_text,
                content_yaml_text=merged_yaml,
                content_fingerprint=yaml_fingerprint(merged_yaml),
                rebased_at=now,
                updated_at=now,
            ),
        )
        if after is None:
            require_open_branch(before)
            require_branch_fingerprint_match(before, expected_fingerprint)
            raise ConflictDetected(
                "ontology branch changed concurrently during rebase", details={"branch_id": branch_id}
            )
        summary = {
            "resolutions": [{"kind": k, "apiName": a, "use": use} for (k, a), use in sorted(resolutions.items())]
        }
        service._audit_event(conn, ctx, "rebased", after, before=before, extra=summary)
        active_version_id = service._active_version_id(conn, ctx)
        return branch_payload(after, is_base_stale=service._is_base_stale(after, active_version_id))


def _propose_replay(
    service: OntologyBranchBoundary,
    ctx: RequestContext,
    row: OntologyBranchRow,
) -> dict[str, object] | None:
    """Replay when a live proposal already carries this exact branch content."""
    if row["merged_proposal_id"] is None:
        return None
    proposal = service._proposal_by_id(row["merged_proposal_id"], ctx)
    if proposal is None:
        return None
    if proposal["status"] in _LIVE_PROPOSAL_STATUSES and proposal["fingerprint"] == row["content_fingerprint"]:
        payload = branch_payload(row, is_base_stale=False)
        payload["proposal"] = proposal
        return payload
    return None


def _store_proposal(
    service: OntologyBranchBoundary,
    ctx: RequestContext,
    branch_id: str,
    proposal: dict[str, object],
) -> dict[str, object]:
    with service.engine.begin() as conn:
        before = service._require_branch_row(conn, ctx, branch_id)
        after = service.ontology_branch_repository.set_branch_proposal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            proposal_id=str(proposal["id"]),
            updated_at=_now(),
        )
        if after is None:
            require_open_branch(before)
            raise ConflictDetected(
                "ontology branch changed concurrently during propose", details={"branch_id": branch_id}
            )
        service._audit_event(conn, ctx, "proposed", after, before=before, extra={"proposalId": proposal["id"]})
        payload = branch_payload(after, is_base_stale=False)
        payload["proposal"] = proposal
        return payload


def _require_not_base_stale(row: OntologyBranchRow, snapshot: ActiveOntologySnapshot) -> None:
    if snapshot.version["id"] != row["base_version_id"]:
        # Palantir requires an up-to-date branch before merging: main moved
        # since this branch's base, so the branch must rebase first.
        raise ConflictDetected(
            "ontology branch must be rebased before it can be proposed",
            details={
                "branch_id": row["id"],
                "baseVersionNumber": row["base_version_number"],
                "activeVersionNumber": snapshot.version["version_number"],
            },
        )


def _description_with_diff(description: str | None, row: OntologyBranchRow) -> str:
    """Embed the branch-vs-base resource diff so reviewers see per-resource scope."""
    base = parse_resource_map(row["base_yaml_text"])
    branch = parse_resource_map(row["content_yaml_text"])
    diff = evaluate_branch_diff(base, branch, base)
    changes = [
        {"kind": entry["kind"], "apiName": entry["apiName"], "change": entry["branchChange"]}
        for entry in diff["resources"]
    ]
    summary = json.dumps({"branchId": row["id"], "branchName": row["name"], "resources": changes}, sort_keys=True)
    prefix = f"{description}\n\n" if description else ""
    return f"{prefix}[ontology-branch-diff] {summary}"


def _applied_version_number(service: OntologyBranchBoundary, ctx: RequestContext, proposal_id: str) -> int | None:
    proposal = service._proposal_by_id(proposal_id, ctx)
    if proposal is None or proposal["status"] != "applied":
        return None
    applied = proposal.get("appliedOntologyVersion")
    if not isinstance(applied, Mapping):
        return None
    version_number = applied.get("versionNumber")
    return version_number if isinstance(version_number, int) else None


def _parse_resolutions(resolutions: Sequence[Mapping[str, object]]) -> dict[ResourceKey, str]:
    parsed: dict[ResourceKey, str] = {}
    for item in resolutions:
        kind = item.get("kind")
        api_name = item.get("apiName")
        use = item.get("use")
        if not isinstance(kind, str) or not isinstance(api_name, str) or use not in _RESOLUTION_CHOICES:
            raise ValidationFailed(
                "each rebase resolution needs kind, apiName, and use of main|branch",
                details={"resolution": dict(item)},
            )
        key = (kind, api_name)
        if key in parsed and parsed[key] != use:
            raise ValidationFailed(
                "conflicting rebase resolutions for the same resource",
                details={"kind": kind, "apiName": api_name},
            )
        parsed[key] = use
    return parsed
