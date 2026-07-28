"""Durable, idempotent, non-committing Pipeline Builder preview runs."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.language_model import GovernedSemanticModelPort
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelinePreviewRunRecord,
    PipelinePreviewRunRow,
)
from foundry_lite.application.ports.pipeline_repository import PipelineBranchRow, PipelineRepository
from foundry_lite.application.ports.semantic_row_cache_repository import SemanticRowCacheRepository
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_preview_executor import (
    PreviewExecutionResult,
    execute_pipeline_preview,
    normalize_preview_limits,
)
from foundry_lite.application.services.pipeline_preview_recovery import (
    PipelinePreviewExecutionLeaseLost,
    PipelinePreviewRecoveryCursor,
    pipeline_preview_execution_heartbeat,
    pipeline_preview_lease_claim_values,
    pipeline_preview_lease_reclaim_values,
    pipeline_preview_utc_now,
    recoverable_pipeline_previews,
    recovered_pipeline_preview_context,
)
from foundry_lite.application.services.pipeline_preview_runtime import (
    PipelinePreviewDatasetRegistry,
    PipelinePreviewRuntime,
)
from foundry_lite.application.services.pipeline_preview_values import (
    PIPELINE_PREVIEW_CANCELLED,
    PIPELINE_PREVIEW_FAILED,
    StatusTransition,
    _idempotent_preview_payload,
    _preview_payload,
    _preview_record,
    _preview_request_fingerprint,
    _require_valid_preview_graph,
)
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticRowCacheSession,
    semantic_resource_security_policy_fingerprint,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class PipelinePreviewService(CoreService):
    """Run an unsaved draft graph while making serving commits structurally impossible."""

    required_dependencies = (
        "engine",
        "metadata_repository",
        "policy",
        "pipeline_repository",
        "pipeline_execution_repository",
        "media_repository",
        "media_storage",
        "media_processor_registry",
        "embedding_model_adapter",
        "governed_semantic_model_port",
        "semantic_row_cache_repository",
        "source_management_repository",
    )
    required_collaborators = ("dataset_registry_service", "runtime_service")
    dataset_registry_service: PipelinePreviewDatasetRegistry
    governed_semantic_model_port: GovernedSemanticModelPort
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_repository: PipelineRepository
    semantic_row_cache_repository: SemanticRowCacheRepository
    runtime_service: RuntimeEvidenceBoundary

    def __init__(self, **dependencies: object) -> None:
        super().__init__(**dependencies)
        self._recovery_cursor = PipelinePreviewRecoveryCursor()

    def create_preview_run(
        self,
        branch_id: str,
        *,
        graph: Mapping[str, object],
        target_node_id: str | None,
        limits: Mapping[str, object] | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", "pipeline_branch", branch_id)
        self._require_write_open(ctx, branch_id)
        branch = self._branch(branch_id, ctx)
        pipeline_id = str(branch["pipeline_id"])
        _require_valid_preview_graph(graph)
        normalized_limits = normalize_preview_limits(limits)
        request_fingerprint = _preview_request_fingerprint(graph, target_node_id, normalized_limits)
        record = _preview_record(
            ctx,
            pipeline_id=pipeline_id,
            branch_id=branch_id,
            graph=graph,
            target_node_id=target_node_id,
            limits=normalized_limits,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        row = self._insert_or_replay_preview(ctx, record)
        return _idempotent_preview_payload(row, request_fingerprint)

    def execute_preview_run(
        self,
        preview_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:write")
        row, is_claimed = self._claim_preview(preview_run_id, ctx)
        if row["status"] == "CANCEL_REQUESTED":
            return self._complete_preview(
                ctx,
                row,
                PIPELINE_PREVIEW_CANCELLED,
                PreviewExecutionResult([], []),
                None,
                row["execution_lease_token"],
            )
        if not is_claimed:
            return _preview_payload(row)
        return self._execute_claimed_preview(ctx, row)

    def recover_preview_runs(self, *, limit: int = 10) -> dict[str, object]:
        rows = recoverable_pipeline_previews(
            self.engine,
            self.pipeline_execution_repository,
            self.metadata_repository,
            self._recovery_cursor,
            as_of=pipeline_preview_utc_now(),
            limit=limit,
        )
        recovered_ids: list[str] = []
        for row in rows:
            recovered_ids.append(self._recover_preview_row(row))
        return {"processed": len(recovered_ids), "previewRunIds": recovered_ids}

    def _recover_preview_row(self, row: PipelinePreviewRunRow) -> str:
        ctx = recovered_pipeline_preview_context(row)
        try:
            self.execute_preview_run(str(row["id"]), ctx=ctx)
        except Exception as exc:
            error = dict(self.runtime_service._error_payload(exc, ctx, run_id=str(row["id"])))
            with self.engine.begin() as conn:
                current = self._require_preview(conn, ctx, str(row["id"]))
            transition = (
                PIPELINE_PREVIEW_CANCELLED if current["status"] == "CANCEL_REQUESTED" else PIPELINE_PREVIEW_FAILED
            )
            self._complete_preview(
                ctx,
                current,
                transition,
                PreviewExecutionResult([], []),
                None if transition is PIPELINE_PREVIEW_CANCELLED else error,
                current["execution_lease_token"],
            )
        return str(row["id"])

    def _execute_claimed_preview(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
    ) -> dict[str, object]:
        result = PreviewExecutionResult([], [])
        error: dict[str, object] | None = None
        try:
            with pipeline_preview_execution_heartbeat(
                self.engine,
                self.pipeline_execution_repository,
                ctx,
                row,
            ) as guard:
                try:
                    result = self._execute_preview_graph(ctx, row)
                except Exception as exc:
                    error = dict(self.runtime_service._error_payload(exc, ctx, run_id=str(row["id"])))
                guard.require_active()
            return self._finish_claimed_execution(ctx, row, guard.token, result, error)
        except PipelinePreviewExecutionLeaseLost:
            return self.get_preview_run(str(row["id"]), ctx=ctx)

    def _execute_preview_graph(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
    ) -> PreviewExecutionResult:
        return execute_pipeline_preview(
            row["graph"],
            preview_run_id=str(row["id"]),
            target_node_id=row["target_node_id"],
            limits=row["limits"],
            runtime=self._preview_runtime(ctx, row),
        )

    def get_preview_run(
        self,
        preview_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            row = self.pipeline_execution_repository.preview_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=preview_run_id,
            )
        if row is None:
            raise NotFound("pipeline preview run not found", details={"preview_run_id": preview_run_id})
        return _preview_payload(row)

    def cancel_preview_run(
        self,
        preview_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", "pipeline_preview_run", preview_run_id)
        with self.engine.begin() as conn:
            current = self._require_preview(conn, ctx, preview_run_id)
            if current["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return _preview_payload(current)
            row = self.pipeline_execution_repository.request_preview_cancel(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=preview_run_id,
                requested_at=pipeline_preview_utc_now(),
            )
            if row is None:
                row = self._require_preview(conn, ctx, preview_run_id)
            else:
                self._audit_preview(conn, ctx, row, "cancel_requested")
        return _preview_payload(row)

    def _insert_or_replay_preview(
        self,
        ctx: RequestContext,
        record: PipelinePreviewRunRecord,
    ) -> PipelinePreviewRunRow:
        with self.engine.begin() as conn:
            existing = self.pipeline_execution_repository.preview_by_idempotency_key(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                idempotency_key=record.idempotency_key,
            )
            if existing is not None:
                return existing
            row = self.pipeline_execution_repository.insert_preview(transaction=conn, record=record)
            if row["id"] == record.preview_run_id:
                self._audit_preview(conn, ctx, row, "created")
            return row

    def _claim_preview(
        self,
        preview_run_id: str,
        ctx: RequestContext,
    ) -> tuple[PipelinePreviewRunRow, bool]:
        with self.engine.begin() as conn:
            claimed = self.pipeline_execution_repository.claim_preview(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=preview_run_id,
                **pipeline_preview_lease_claim_values(),
            )
            if claimed is not None:
                return claimed, True
            reclaimed = self.pipeline_execution_repository.reclaim_expired_preview(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=preview_run_id,
                **pipeline_preview_lease_reclaim_values(),
            )
            if reclaimed is not None:
                return reclaimed, True
            return self._require_preview(conn, ctx, preview_run_id), False

    def _finish_claimed_execution(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
        execution_lease_token: str,
        result: PreviewExecutionResult,
        error: dict[str, object] | None,
    ) -> dict[str, object]:
        if error is not None:
            return self._complete_preview_failure(ctx, row, error, execution_lease_token)
        return self._complete_preview_success(ctx, row, result, execution_lease_token)

    def _complete_preview_failure(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
        error: dict[str, object],
        execution_lease_token: str,
    ) -> dict[str, object]:
        with self.engine.begin() as conn:
            after = self.pipeline_execution_repository.complete_preview_failure(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=str(row["id"]),
                execution_lease_token=execution_lease_token,
                error=error,
                completed_at=pipeline_preview_utc_now(),
            )
            if after is None:
                return _preview_payload(self._require_preview(conn, ctx, str(row["id"])))
            self._audit_preview(conn, ctx, after, after["status"].lower())
        return _preview_payload(after)

    def _complete_preview_success(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
        result: PreviewExecutionResult,
        execution_lease_token: str,
    ) -> dict[str, object]:
        with self.engine.begin() as conn:
            after = self.pipeline_execution_repository.complete_preview_success(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=str(row["id"]),
                execution_lease_token=execution_lease_token,
                outputs=result.outputs,
                artifacts=result.artifacts,
                completed_at=pipeline_preview_utc_now(),
            )
            if after is None:
                return _preview_payload(self._require_preview(conn, ctx, str(row["id"])))
            self._audit_preview(conn, ctx, after, after["status"].lower())
        return _preview_payload(after)

    def _complete_preview(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
        transition: StatusTransition,
        result: PreviewExecutionResult,
        error: dict[str, object] | None,
        execution_lease_token: str | None,
    ) -> dict[str, object]:
        with self.engine.begin() as conn:
            after = self.pipeline_execution_repository.update_preview_terminal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                preview_run_id=str(row["id"]),
                transition=transition,
                outputs=result.outputs,
                artifacts=result.artifacts,
                error=error,
                completed_at=pipeline_preview_utc_now(),
                execution_lease_token=execution_lease_token,
            )
            if after is None:
                return _preview_payload(self._require_preview(conn, ctx, str(row["id"])))
            self._audit_preview(conn, ctx, after, transition.to_status.lower())
        return _preview_payload(after)

    def _preview_runtime(
        self,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
    ) -> PipelinePreviewRuntime:
        sensitive_fields = frozenset(self.policy.sensitive_column_names(ctx))
        decision = self.policy.decide(ctx, "pipeline:write")
        return PipelinePreviewRuntime(
            engine=self.engine,
            dataset_registry=self.dataset_registry_service,
            source_management_repository=self.source_management_repository,
            media_repository=self.media_repository,
            media_storage=self.media_storage,
            media_processor_registry=self.media_processor_registry,
            embedding_model_adapter=self.embedding_model_adapter,
            model_gateway=self.governed_semantic_model_port,
            semantic_cache=SemanticRowCacheSession(
                transaction_manager=self.engine,
                repository=self.semantic_row_cache_repository,
                model_gateway=self.governed_semantic_model_port,
            ),
            ctx=ctx,
            pipeline_id=str(row["pipeline_id"]),
            branch_id=str(row["branch_id"]),
            resource_security_policy_fingerprint=semantic_resource_security_policy_fingerprint(
                permission="pipeline:write",
                policy_reason=decision.reason,
                sensitive_fields=tuple(sensitive_fields),
                masked_fields=tuple(self.policy.masked_column_names(ctx)),
            ),
            sensitive_fields=sensitive_fields,
        )

    def _branch(self, branch_id: str, ctx: RequestContext) -> PipelineBranchRow:
        with self.engine.begin() as conn:
            row = self.pipeline_repository.branch_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
            )
        if row is None:
            raise NotFound("pipeline branch not found", details={"branch_id": branch_id})
        return row

    def _require_preview(self, conn: object, ctx: RequestContext, preview_run_id: str) -> PipelinePreviewRunRow:
        row = self.pipeline_execution_repository.preview_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            preview_run_id=preview_run_id,
        )
        if row is None:
            raise NotFound("pipeline preview run not found", details={"preview_run_id": preview_run_id})
        return row

    def _require_write_open(self, ctx: RequestContext, branch_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="create_pipeline_preview",
            resource_type="pipeline_branch",
            resource_id=branch_id,
        )

    def _audit_preview(
        self,
        conn: object,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
        event: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"pipeline.preview.{event}",
            resource_type="pipeline_preview_run",
            resource_id=str(row["id"]),
            action=event,
            after_ref={"status": row["status"], "commitForbidden": row["is_commit_forbidden"]},
        )
