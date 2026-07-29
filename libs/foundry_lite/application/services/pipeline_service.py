"""Stable public entrypoint for the Pipeline Builder bounded context."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_async_run_service import PipelineAsyncRunService
from foundry_lite.application.services.pipeline_catalog_service import PipelineCatalogService
from foundry_lite.application.services.pipeline_definition_service import PipelineDefinitionService
from foundry_lite.application.services.pipeline_deployment_service import PipelineDeploymentService
from foundry_lite.application.services.pipeline_governance_service import PipelineGovernanceService
from foundry_lite.application.services.pipeline_graph_validation_service import PipelineGraphValidationService
from foundry_lite.application.services.pipeline_preview_service import PipelinePreviewService
from foundry_lite.application.services.pipeline_run_service import PipelineRunService
from foundry_lite.application.services.pipeline_scheduler_service import PipelineSchedulerService
from foundry_lite.domain.context import RequestContext


class PipelineService(CoreService):
    """Compatibility facade used by API routers and local Python callers."""

    required_dependencies = ()
    required_collaborators = (
        "pipeline_async_run_service",
        "pipeline_catalog_service",
        "pipeline_definition_service",
        "pipeline_deployment_service",
        "pipeline_governance_service",
        "pipeline_graph_validation_service",
        "pipeline_preview_service",
        "pipeline_run_service",
        "pipeline_scheduler_service",
    )
    pipeline_async_run_service: PipelineAsyncRunService
    pipeline_catalog_service: PipelineCatalogService
    pipeline_definition_service: PipelineDefinitionService
    pipeline_deployment_service: PipelineDeploymentService
    pipeline_governance_service: PipelineGovernanceService
    pipeline_graph_validation_service: PipelineGraphValidationService
    pipeline_preview_service: PipelinePreviewService
    pipeline_run_service: PipelineRunService
    pipeline_scheduler_service: PipelineSchedulerService

    def node_types(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_catalog_service.node_types(ctx=ctx)

    def media_processors(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_catalog_service.media_processors(ctx=ctx)

    def trained_models(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_catalog_service.trained_models(ctx=ctx)

    def create_branch(
        self,
        *,
        pipeline_id: str,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_definition_service.create_branch(
            pipeline_id=pipeline_id,
            name=name,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_branches(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_definition_service.list_branches(status=status, limit=limit, ctx=ctx)

    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_definition_service.get_branch(branch_id, ctx=ctx)

    def update_graph(
        self,
        branch_id: str,
        *,
        graph: Mapping[str, object],
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_definition_service.update_graph(
            branch_id,
            graph=graph,
            expected_fingerprint=expected_fingerprint,
            ctx=ctx,
        )

    def diff_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_definition_service.diff_branch(branch_id, ctx=ctx)

    def rebase_branch(
        self,
        branch_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_definition_service.rebase_branch(
            branch_id,
            expected_fingerprint=expected_fingerprint,
            ctx=ctx,
        )

    def abandon_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_definition_service.abandon_branch(branch_id, ctx=ctx)

    def validate_graph(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_graph_validation_service.validate_branch(branch_id, ctx=ctx)

    def suggest_casts(self, branch_id: str, node_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_graph_validation_service.suggest_casts(branch_id, node_id, ctx=ctx)

    def preview_node(
        self,
        branch_id: str,
        node_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_graph_validation_service.preview_node(branch_id, node_id, options=options, ctx=ctx)

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
        return self.pipeline_preview_service.create_preview_run(
            branch_id,
            graph=graph,
            target_node_id=target_node_id,
            limits=limits,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def execute_preview_run(self, preview_run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_preview_service.execute_preview_run(preview_run_id, ctx=ctx)

    def get_preview_run(self, preview_run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_preview_service.get_preview_run(preview_run_id, ctx=ctx)

    def cancel_preview_run(self, preview_run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_preview_service.cancel_preview_run(preview_run_id, ctx=ctx)

    def recover_preview_runs(self, *, limit: int = 10) -> dict[str, object]:
        return self.pipeline_preview_service.recover_preview_runs(limit=limit)

    def node_stats(
        self,
        branch_id: str,
        node_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_graph_validation_service.node_stats(branch_id, node_id, options=options, ctx=ctx)

    def run_tests(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_graph_validation_service.run_tests(branch_id, ctx=ctx)

    def propose_branch(
        self,
        branch_id: str,
        *,
        title: str,
        idempotency_key: str,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_governance_service.propose_branch(
            branch_id,
            title=title,
            idempotency_key=idempotency_key,
            description=description,
            ctx=ctx,
        )

    def list_proposals(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_governance_service.list_proposals(status=status, limit=limit, ctx=ctx)

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_governance_service.get_proposal(proposal_id, ctx=ctx)

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        assignee_user_id: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_governance_service.assign_proposal(
            proposal_id,
            assignee_user_id=assignee_user_id,
            ctx=ctx,
        )

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_governance_service.decide_proposal(
            proposal_id,
            decision=decision,
            comment=comment,
            ctx=ctx,
        )

    def execute_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_governance_service.execute_proposal(proposal_id, ctx=ctx)

    def withdraw_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_governance_service.withdraw_proposal(proposal_id, ctx=ctx)

    def list_versions(
        self,
        pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_definition_service.list_versions(pipeline_id, limit=limit, ctx=ctx)

    def get_version(self, version_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_definition_service.get_version(version_id, ctx=ctx)

    def deploy(
        self,
        pipeline_id: str,
        version_id: str,
        *,
        idempotency_key: str,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_deployment_service.deploy(
            pipeline_id,
            version_id,
            idempotency_key=idempotency_key,
            options=options,
            ctx=ctx,
        )

    def list_deployments(
        self,
        pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_deployment_service.list_deployments(pipeline_id, limit=limit, ctx=ctx)

    def start_run(
        self,
        pipeline_id: str,
        *,
        version_id: str | None = None,
        idempotency_key: str | None = None,
        parameters: Mapping[str, object] | None = None,
        target_node_ids: list[str] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_run_service.start_run(
            pipeline_id,
            version_id=version_id,
            idempotency_key=idempotency_key,
            parameters=parameters,
            target_node_ids=target_node_ids,
            ctx=ctx,
        )

    def enqueue_run(
        self,
        pipeline_id: str,
        *,
        version_id: str | None,
        idempotency_key: str,
        parameters: Mapping[str, object] | None,
        target_node_ids: list[str] | None,
        wait_seconds: int,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self.pipeline_async_run_service.enqueue(
            pipeline_id,
            version_id=version_id,
            idempotency_key=idempotency_key,
            parameters=parameters,
            target_node_ids=target_node_ids,
            wait_seconds=wait_seconds,
            ctx=ctx,
        )

    def list_runs(
        self,
        pipeline_id: str,
        *,
        cursor: str | None,
        limit: int,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self.pipeline_async_run_service.list_runs(
            pipeline_id,
            cursor=cursor,
            limit=limit,
            ctx=ctx,
        )

    def get_run_events(
        self,
        run_id: str,
        *,
        after_sequence: int,
        limit: int,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self.pipeline_async_run_service.events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
            ctx=ctx,
        )

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_run_service.get_run(run_id, ctx=ctx)

    def get_run_timeline(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_run_service.get_run_timeline(run_id, ctx=ctx)

    def cancel_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_run_service.cancel_run(run_id, ctx=ctx)

    def request_run_cancel(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self.pipeline_async_run_service.cancel(
            run_id,
            idempotency_key=idempotency_key,
            reason=reason,
            ctx=ctx,
        )

    def upsert_schedule(
        self,
        pipeline_id: str,
        *,
        version_id: str,
        schedule: Mapping[str, object],
        enabled: bool = True,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_scheduler_service.upsert_schedule(
            pipeline_id,
            version_id=version_id,
            schedule=schedule,
            enabled=enabled,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def get_schedule(self, pipeline_id: str, *, ctx: RequestContext | None = None) -> dict[str, object] | None:
        return self.pipeline_scheduler_service.get_schedule(pipeline_id, ctx=ctx)

    def pause_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_scheduler_service.pause_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def resume_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_scheduler_service.resume_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def delete_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.pipeline_scheduler_service.delete_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def preview_due_schedules(self, *, max_runs: int = 50, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_scheduler_service.preview_due_schedules(max_runs=max_runs, ctx=ctx)

    def tick_schedules(self, *, max_runs: int = 50, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.pipeline_scheduler_service.tick_schedules(max_runs=max_runs, ctx=ctx)
