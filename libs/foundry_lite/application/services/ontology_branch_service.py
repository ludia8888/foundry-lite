"""Ontology branch service: isolated editing state derived from main.

A branch snapshots the ACTIVE ontology (rendered through the definition
snapshot black box) as both its base and its working copy. Edits happen only
on the branch's ``content_yaml_text`` and never touch the active catalog; the
only way branch content reaches main is through the existing proposal black
box (review-then-apply), so data/index state is never copied to a branch and
merge reindexing rides the normal activation migration plan.
"""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_branch_payloads import (
    ActiveOntologySnapshot,
    active_ontology_snapshot,
    bounded_branch_limit,
    branch_audit_ref,
    branch_detail_payload,
    branch_list_status_filter,
    branch_payload,
    branch_record,
    decode_branch_cursor,
    encode_branch_cursor,
    require_branch_fingerprint_match,
    require_open_branch,
    required_text,
)
from foundry_lite.application.services.ontology_branch_workflows import (
    abandon_ontology_branch,
    diff_ontology_branch,
    propose_ontology_branch,
    rebase_ontology_branch,
    reconcile_ontology_branch_merge,
)
from foundry_lite.application.services.ontology_proposal_payloads import yaml_fingerprint
from foundry_lite.application.services.ontology_proposal_service import OntologyProposalService
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.ontology_yaml import require_yaml_text_within_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

_RESOURCE_TYPE = "ontology_branch"


class OntologyBranchService(CoreService):
    """Create, edit, diff, rebase, propose, and abandon ontology branches."""

    required_dependencies = ("engine", "policy", "ontology_repository", "ontology_branch_repository")
    required_collaborators = ("ontology_service", "ontology_proposal_service", "runtime_service")
    ontology_service: OntologyService
    ontology_proposal_service: OntologyProposalService
    runtime_service: OntologyRuntimeBoundary

    def create_branch(
        self,
        *,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, "create")
        self._require_write_open(ctx, "create_ontology_branch", "create")
        required_text(idempotency_key, "Idempotency-Key")
        branch_name = required_text(name, "name")
        with self.engine.begin() as conn:
            snapshot = self._require_active_snapshot(conn, ctx)
            record = branch_record(ctx, name=branch_name, snapshot=snapshot, now=_now())
            row = self.ontology_branch_repository.insert_branch_if_name_free(transaction=conn, record=record)
            if row is None:
                return self._replay_or_reject_create(conn, ctx, branch_name)
            self._audit_event(conn, ctx, "created", row)
            return branch_payload(row, is_base_stale=False)

    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "ontology:validate")
        with self.engine.begin() as conn:
            row = self._require_branch_row(conn, ctx, branch_id)
            active_version_id = self._active_version_id(conn, ctx)
        return branch_detail_payload(row, is_base_stale=self._is_base_stale(row, active_version_id))

    def list_branches(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "ontology:validate")
        status_filter = branch_list_status_filter(status)
        page = decode_branch_cursor(cursor)
        bounded = bounded_branch_limit(limit)
        with self.engine.begin() as conn:
            rows = self.ontology_branch_repository.list_branches(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                status=status_filter,
                created_before=page.created_before,
                before_id=page.before_id,
                limit=bounded,
            )
            active_version_id = self._active_version_id(conn, ctx)
        items = [branch_payload(row, is_base_stale=self._is_base_stale(row, active_version_id)) for row in rows]
        next_cursor = encode_branch_cursor(rows[-1]) if len(rows) == bounded else None
        return {"items": items, "nextCursor": next_cursor}

    def update_branch_content(
        self,
        branch_id: str,
        *,
        yaml_text: str,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Replace the branch working copy; any ontology:validate holder may edit (collaborative)."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, branch_id)
        self._require_write_open(ctx, "update_ontology_branch", branch_id)
        required_text(expected_fingerprint, "expectedFingerprint")
        require_yaml_text_within_limit(yaml_text)
        replay = self._update_replay(ctx, branch_id, yaml_text)
        if replay is not None:
            return replay
        # Structural validity is required; a blocked migration plan is fine on
        # a branch (Palantir allows blocked edits until merge time).
        self.ontology_service.validate_yaml_text(yaml_text, ctx=ctx)
        return self._store_content(ctx, branch_id, yaml_text, expected_fingerprint)

    def branch_diff(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        """Three-way resource diff (base vs branch vs current active) plus merge impact."""
        return diff_ontology_branch(self, branch_id, ctx=ctx)

    def rebase_branch(
        self,
        branch_id: str,
        *,
        resolutions: list[dict[str, object]] | None = None,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Re-anchor the branch on the current active ontology, settling conflicts per resource."""
        return rebase_ontology_branch(
            self, branch_id, resolutions=resolutions or [], expected_fingerprint=expected_fingerprint, ctx=ctx
        )

    def propose_branch(
        self,
        branch_id: str,
        *,
        title: str,
        idempotency_key: str,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Submit the branch content as an ontology change proposal (merge request)."""
        return propose_ontology_branch(
            self, branch_id, title=title, idempotency_key=idempotency_key, description=description, ctx=ctx
        )

    def abandon_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        """Terminally close an open branch (creator or any ontology:activate holder)."""
        return abandon_ontology_branch(self, branch_id, ctx=ctx)

    def reconcile_branch_merge(self, branch_id: str, *, ctx: RequestContext | None = None) -> None:
        """Converge an open branch to merged once its proposal has been executed."""
        reconcile_ontology_branch_merge(self, branch_id, ctx=ctx)

    def reconcile_proposed_branches(self, *, ctx: RequestContext | None = None) -> None:
        """Converge every open proposed branch whose proposal has been executed."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "ontology:validate")
        with self.engine.begin() as conn:
            rows = self.ontology_branch_repository.list_branches(
                transaction=conn, tenant_id=ctx.tenant_id, status="open", created_before=None, before_id=None, limit=200
            )
        for row in rows:
            if row["merged_proposal_id"] is not None:
                reconcile_ontology_branch_merge(self, row["id"], ctx=ctx)

    def _update_replay(self, ctx: RequestContext, branch_id: str, yaml_text: str) -> dict[str, object] | None:
        with self.engine.begin() as conn:
            row = self._require_branch_row(conn, ctx, branch_id)
            require_open_branch(row)
            if row["content_fingerprint"] == yaml_fingerprint(yaml_text):
                # Idempotent replay: this exact content is already stored.
                active_version_id = self._active_version_id(conn, ctx)
                return branch_payload(row, is_base_stale=self._is_base_stale(row, active_version_id))
        return None

    def _store_content(
        self,
        ctx: RequestContext,
        branch_id: str,
        yaml_text: str,
        expected_fingerprint: str,
    ) -> dict[str, object]:
        with self.engine.begin() as conn:
            before = self._require_branch_row(conn, ctx, branch_id)
            after = self.ontology_branch_repository.update_branch_content(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
                expected_fingerprint=expected_fingerprint,
                content_yaml_text=yaml_text,
                content_fingerprint=yaml_fingerprint(yaml_text),
                updated_at=_now(),
            )
            if after is None:
                require_open_branch(before)
                require_branch_fingerprint_match(before, expected_fingerprint)
                raise ConflictDetected(
                    "ontology branch changed concurrently during update", details={"branch_id": branch_id}
                )
            self._audit_event(conn, ctx, "updated", after, before=before)
            active_version_id = self._active_version_id(conn, ctx)
            return branch_payload(after, is_base_stale=self._is_base_stale(after, active_version_id))

    def _replay_or_reject_create(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        branch_name: str,
    ) -> dict[str, object]:
        existing = self.ontology_branch_repository.open_branch_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, name=branch_name
        )
        if existing is None:
            raise ConflictDetected("ontology branch creation raced a concurrent create", details={"name": branch_name})
        if existing["created_by"] == ctx.actor_user_id:
            # Idempotent replay: the same user re-creating the same open branch
            # name gets the existing branch back (branch names are the create
            # replay identity because a branch has no client-supplied content).
            active_version_id = self._active_version_id(conn, ctx)
            return branch_payload(existing, is_base_stale=self._is_base_stale(existing, active_version_id))
        raise ConflictDetected(
            "an open ontology branch with this name already exists",
            details={"name": branch_name, "branch_id": existing["id"], "createdBy": existing["created_by"]},
        )

    def _proposal_by_id(self, proposal_id: str, ctx: RequestContext) -> dict[str, object] | None:
        """Read one proposal through the black box; None when it disappeared."""
        try:
            return self.ontology_proposal_service.get_proposal(proposal_id, ctx=ctx)
        except NotFound:
            return None

    def _submit_branch_proposal(
        self,
        *,
        yaml_text: str,
        title: str,
        idempotency_key: str,
        description: str,
        ctx: RequestContext,
    ) -> dict[str, object]:
        """Submit branch content through the proposal black box (merge request)."""
        return self.ontology_proposal_service.submit_proposal(
            yaml_text=yaml_text,
            title=title,
            idempotency_key=idempotency_key,
            description=description,
            ctx=ctx,
        )

    def _require_branch_row(self, conn: TransactionContext, ctx: RequestContext, branch_id: str) -> OntologyBranchRow:
        row = self.ontology_branch_repository.branch_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, branch_id=branch_id
        )
        if row is None:
            raise NotFound("ontology branch not found", details={"branch_id": branch_id})
        return row

    def _require_active_snapshot(self, conn: TransactionContext, ctx: RequestContext) -> ActiveOntologySnapshot:
        snapshot = active_ontology_snapshot(conn, ctx, self.ontology_repository)
        if snapshot is None:
            raise ValidationFailed("no active ontology version to branch from")
        return snapshot

    def _active_version_id(self, conn: TransactionContext, ctx: RequestContext) -> str | None:
        version = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
        return version["id"] if version is not None else None

    def _is_base_stale(self, row: OntologyBranchRow, active_version_id: str | None) -> bool:
        return active_version_id is not None and active_version_id != row["base_version_id"]

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx, operation=operation, resource_type=_RESOURCE_TYPE, resource_id=resource_id
        )

    def _audit_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        row: OntologyBranchRow,
        *,
        before: OntologyBranchRow | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        after_ref = branch_audit_ref(row)
        if extra:
            after_ref.update(extra)
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"ontology.branch.{event}",
            resource_type=_RESOURCE_TYPE,
            resource_id=row["id"],
            action="ontology:validate",
            before_ref=branch_audit_ref(before) if before is not None else None,
            after_ref=after_ref,
        )
