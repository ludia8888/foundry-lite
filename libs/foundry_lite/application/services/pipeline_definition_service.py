"""Pipeline Builder branch, graph, diff, rebase, and version reads."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRow,
    PipelineRepository,
    PipelineVersionRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_model import (
    pipeline_graph_fingerprint,
)
from foundry_lite.application.services.pipeline_graph_normalizer import (
    empty_pipeline_graph_v2,
    normalize_pipeline_graph,
)
from foundry_lite.application.services.pipeline_graph_release_diff import pipeline_graph_release_diff
from foundry_lite.application.services.pipeline_graph_three_way_merge import (
    merge_pipeline_graphs,
)
from foundry_lite.application.services.pipeline_payloads import (
    bounded_pipeline_limit,
    branch_payload,
    branch_record,
    require_open_branch,
    required_text,
    version_payload,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

_RESOURCE_TYPE = "pipeline_branch"


class PipelineDefinitionService(CoreService):
    """Create and edit Pipeline Builder graph branches."""

    required_dependencies = ("engine", "policy", "pipeline_repository")
    required_collaborators = ("runtime_service",)
    pipeline_repository: PipelineRepository
    runtime_service: RuntimeEvidenceBoundary

    def create_branch(
        self,
        *,
        pipeline_id: str,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", _RESOURCE_TYPE, "create")
        self._require_write_open(ctx, "create_pipeline_branch", "create")
        required_text(idempotency_key, "Idempotency-Key")
        clean_pipeline_id = required_text(pipeline_id, "pipelineId")
        clean_name = required_text(name, "name")
        with self.engine.begin() as conn:
            latest = self.pipeline_repository.latest_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=clean_pipeline_id,
            )
            record = branch_record(
                ctx,
                pipeline_id=clean_pipeline_id,
                name=clean_name,
                latest=latest,
                now=_now(),
            )
            row = self.pipeline_repository.insert_branch_if_name_free(transaction=conn, record=record)
            if row is None:
                return self._replay_or_reject_create(conn, ctx, clean_pipeline_id, clean_name)
            self._audit(conn, ctx, "created", row)
            return branch_payload(row)

    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            return branch_payload(self._require_branch(conn, ctx, branch_id))

    def list_branches(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            rows = self.pipeline_repository.list_branches(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                status=status,
                limit=bounded_pipeline_limit(limit),
            )
        return {"items": [branch_payload(row) for row in rows], "nextCursor": None}

    def update_graph(
        self,
        branch_id: str,
        *,
        graph: Mapping[str, object],
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", _RESOURCE_TYPE, branch_id)
        self._require_write_open(ctx, "update_pipeline_graph", branch_id)
        required_text(expected_fingerprint, "expectedFingerprint")
        canonical_graph = dict(normalize_pipeline_graph(graph))
        with self.engine.begin() as conn:
            before = self._require_branch(conn, ctx, branch_id)
            after = self.pipeline_repository.update_branch_graph(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
                expected_fingerprint=expected_fingerprint,
                graph=canonical_graph,
                graph_fingerprint=pipeline_graph_fingerprint(canonical_graph),
                graph_schema_version=2,
                updated_at=_now(),
            )
            if after is None:
                self._raise_graph_update_conflict(before, expected_fingerprint)
            assert after is not None
            self._audit(conn, ctx, "graph.updated", after, before=before)
            return branch_payload(after)

    def diff_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            branch = self._require_branch(conn, ctx, branch_id)
            latest = self.pipeline_repository.latest_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=str(branch["pipeline_id"]),
            )
        latest_graph = latest["graph"] if latest is not None else empty_pipeline_graph_v2()
        return {
            "branchId": branch["id"],
            "pipelineId": branch["pipeline_id"],
            "baseVersionId": branch["base_version_id"],
            "latestVersionId": latest["id"] if latest is not None else None,
            "baseStale": latest is not None and branch["base_version_id"] != latest["id"],
            "graph": _graph_diff(dict(branch["base_graph"]), dict(branch["graph"])),
            "againstLatest": _graph_diff(latest_graph, dict(branch["graph"])),
        }

    def rebase_branch(
        self,
        branch_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", _RESOURCE_TYPE, branch_id)
        self._require_write_open(ctx, "rebase_pipeline_branch", branch_id)
        required_text(expected_fingerprint, "expectedFingerprint")
        with self.engine.begin() as conn:
            before = self._require_branch(conn, ctx, branch_id)
            latest = self.pipeline_repository.latest_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=str(before["pipeline_id"]),
            )
            base_graph, canonical_graph = _three_way_rebase_graphs(latest, before)
            after = self.pipeline_repository.rebase_branch(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
                expected_fingerprint=expected_fingerprint,
                base_version_id=latest["id"] if latest is not None else None,
                base_graph=base_graph,
                graph=canonical_graph,
                graph_fingerprint=pipeline_graph_fingerprint(canonical_graph),
                graph_schema_version=2,
                rebased_at=_now(),
                updated_at=_now(),
            )
            if after is None:
                self._raise_graph_update_conflict(before, expected_fingerprint)
            assert after is not None
            self._audit(conn, ctx, "rebased", after, before=before)
            return branch_payload(after)

    def abandon_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", _RESOURCE_TYPE, branch_id)
        self._require_write_open(ctx, "abandon_pipeline_branch", branch_id)
        with self.engine.begin() as conn:
            before = self._require_branch(conn, ctx, branch_id)
            if before["status"] == "abandoned":
                return branch_payload(before)
            after = self.pipeline_repository.close_branch(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
                status="abandoned",
                merged_version_id=None,
                updated_at=_now(),
            )
            if after is None:
                raise ConflictDetected("pipeline branch can no longer be abandoned", details={"branch_id": branch_id})
            self._audit(conn, ctx, "abandoned", after, before=before)
            return branch_payload(after)

    def list_versions(
        self,
        pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            rows = self.pipeline_repository.list_versions(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
                limit=bounded_pipeline_limit(limit),
            )
        return {"items": [version_payload(row) for row in rows], "nextCursor": None}

    def get_version(self, version_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            row = self.pipeline_repository.version_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                version_id=version_id,
            )
        if row is None:
            raise NotFound("pipeline version not found", details={"version_id": version_id})
        return version_payload(row)

    def _require_branch(self, conn: TransactionContext, ctx: RequestContext, branch_id: str) -> PipelineBranchRow:
        row = self.pipeline_repository.branch_by_id(transaction=conn, tenant_id=ctx.tenant_id, branch_id=branch_id)
        if row is None:
            raise NotFound("pipeline branch not found", details={"branch_id": branch_id})
        return row

    def _replay_or_reject_create(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        pipeline_id: str,
        name: str,
    ) -> dict[str, object]:
        rows = self.pipeline_repository.list_branches(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            status="open",
            limit=100,
        )
        for row in rows:
            if row["pipeline_id"] == pipeline_id and row["name"] == name:
                return branch_payload(row)
        raise ConflictDetected(
            "open pipeline branch name already exists",
            details={"pipeline_id": pipeline_id, "name": name},
        )

    def _raise_graph_update_conflict(self, before: PipelineBranchRow, expected_fingerprint: str) -> None:
        require_open_branch(before)
        if before["graph_fingerprint"] != expected_fingerprint:
            raise ConflictDetected(
                "pipeline graph fingerprint mismatch",
                details={
                    "expectedFingerprint": expected_fingerprint,
                    "actualFingerprint": before["graph_fingerprint"],
                },
            )
        raise ConflictDetected("pipeline branch changed concurrently", details={"branch_id": before["id"]})

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation=operation,
            resource_type=_RESOURCE_TYPE,
            resource_id=resource_id,
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        row: PipelineBranchRow,
        *,
        before: PipelineBranchRow | None = None,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"pipeline.branch.{event}",
            resource_type=_RESOURCE_TYPE,
            resource_id=str(row["id"]),
            action=event,
            before_ref=_branch_audit_ref(before) if before is not None else None,
            after_ref=_branch_audit_ref(row),
        )


def _graph_diff(base: Mapping[str, object], graph: Mapping[str, object]) -> dict[str, object]:
    return pipeline_graph_release_diff(base, graph)


def _three_way_rebase_graphs(
    latest: PipelineVersionRow | None,
    branch: PipelineBranchRow,
) -> tuple[dict[str, object], dict[str, object]]:
    latest_graph = normalize_pipeline_graph(latest["graph"]) if latest is not None else empty_pipeline_graph_v2()
    base_graph = normalize_pipeline_graph(branch["base_graph"])
    branch_graph = normalize_pipeline_graph(branch["graph"])
    result = merge_pipeline_graphs(base_graph, branch_graph, latest_graph)
    if result.conflicts:
        raise ConflictDetected(
            "pipeline branch has three-way rebase conflicts",
            details={
                "branchId": branch["id"],
                "baseVersionId": branch["base_version_id"],
                "latestVersionId": latest["id"] if latest is not None else None,
                "conflicts": list(result.conflicts),
            },
        )
    return dict(latest_graph), dict(normalize_pipeline_graph(result.graph))


def _branch_audit_ref(row: PipelineBranchRow | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "pipeline_id": row["pipeline_id"],
        "name": row["name"],
        "status": row["status"],
        "graph_fingerprint": row["graph_fingerprint"],
    }
