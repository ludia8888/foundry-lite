"""Thin facade entrypoint for Pipeline Builder workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.pipeline_service import PipelineService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class PipelineWorkspace:
    """Dataset Pipeline Builder: graph branches, review, deploy, run, and schedule."""

    def __init__(self, pipeline: PipelineService) -> None:
        self._pipeline = pipeline

    def node_types(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.node_types(ctx=ctx)

    def media_processors(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.media_processors(ctx=ctx)

    def trained_models(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.trained_models(ctx=ctx)

    def create_branch(
        self,
        *,
        pipeline_id: str,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.create_branch(
            pipeline_id=pipeline_id,
            name=name,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def update_graph(
        self,
        branch_id: str,
        *,
        graph: Mapping[str, object],
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.update_graph(
            branch_id,
            graph=graph,
            expected_fingerprint=expected_fingerprint,
            ctx=ctx,
        )

    def list_branches(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.list_branches(status=status, limit=limit, ctx=ctx)

    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.get_branch(branch_id, ctx=ctx)

    def diff(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.diff_branch(branch_id, ctx=ctx)

    def rebase(
        self,
        branch_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.rebase_branch(branch_id, expected_fingerprint=expected_fingerprint, ctx=ctx)

    def abandon(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.abandon_branch(branch_id, ctx=ctx)

    def validate(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.validate_graph(branch_id, ctx=ctx)

    def suggest_casts(
        self,
        branch_id: str,
        node_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.suggest_casts(branch_id, node_id, ctx=ctx)

    def preview_node(
        self,
        branch_id: str,
        node_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.preview_node(branch_id, node_id, options=options, ctx=ctx)

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
        return self._pipeline.create_preview_run(
            branch_id,
            graph=graph,
            target_node_id=target_node_id,
            limits=limits,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def execute_preview_run(self, preview_run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.execute_preview_run(preview_run_id, ctx=ctx)

    def get_preview_run(self, preview_run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.get_preview_run(preview_run_id, ctx=ctx)

    def cancel_preview_run(self, preview_run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.cancel_preview_run(preview_run_id, ctx=ctx)

    def recover_preview_runs(self, *, limit: int = 10) -> dict[str, object]:
        return self._pipeline.recover_preview_runs(limit=limit)

    def node_stats(
        self,
        branch_id: str,
        node_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.node_stats(branch_id, node_id, options=options, ctx=ctx)

    def run_tests(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.run_tests(branch_id, ctx=ctx)

    def propose(
        self,
        branch_id: str,
        *,
        title: str,
        idempotency_key: str,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.propose_branch(
            branch_id,
            title=title,
            idempotency_key=idempotency_key,
            description=description,
            ctx=ctx,
        )

    def approve(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.decide_proposal(proposal_id, decision="approve", ctx=ctx)

    def execute(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.execute_proposal(proposal_id, ctx=ctx)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.list_proposals(status=status, limit=limit, ctx=ctx)

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.get_proposal(proposal_id, ctx=ctx)

    def assign(
        self,
        proposal_id: str,
        *,
        assignee_user_id: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.assign_proposal(proposal_id, assignee_user_id=assignee_user_id, ctx=ctx)

    def decide(
        self,
        proposal_id: str,
        *,
        decision: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.decide_proposal(proposal_id, decision=decision, comment=comment, ctx=ctx)

    def withdraw(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.withdraw_proposal(proposal_id, ctx=ctx)

    def list_versions(
        self,
        pipeline_id: str,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.list_versions(pipeline_id, limit=limit, ctx=ctx)

    def get_version(self, version_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.get_version(version_id, ctx=ctx)

    def deploy(
        self,
        pipeline_id: str,
        version_id: str,
        *,
        idempotency_key: str,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.deploy(
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
        return self._pipeline.list_deployments(pipeline_id, limit=limit, ctx=ctx)

    def run(
        self,
        pipeline_id: str,
        *,
        version_id: str | None = None,
        idempotency_key: str | None = None,
        parameters: Mapping[str, object] | None = None,
        target_node_ids: list[str] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.start_run(
            pipeline_id,
            version_id=version_id,
            idempotency_key=idempotency_key,
            parameters=parameters,
            target_node_ids=target_node_ids,
            ctx=ctx,
        )

    def start_async(
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
        return self._pipeline.enqueue_run(
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
        return self._pipeline.list_runs(pipeline_id, cursor=cursor, limit=limit, ctx=ctx)

    def events(
        self,
        run_id: str,
        *,
        after_sequence: int,
        limit: int,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self._pipeline.get_run_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
            ctx=ctx,
        )

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.get_run(run_id, ctx=ctx)

    def timeline(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.get_run_timeline(run_id, ctx=ctx)

    def cancel(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.cancel_run(run_id, ctx=ctx)

    def request_cancel(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self._pipeline.request_run_cancel(
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
        return self._pipeline.upsert_schedule(
            pipeline_id,
            version_id=version_id,
            schedule=schedule,
            enabled=enabled,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def get_schedule(self, pipeline_id: str, *, ctx: RequestContext | None = None) -> dict[str, object] | None:
        return self._pipeline.get_schedule(pipeline_id, ctx=ctx)

    def pause_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._pipeline.pause_schedule(
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
        return self._pipeline.resume_schedule(
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
        return self._pipeline.delete_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def preview_due(self, *, max_runs: int = 50, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.preview_due_schedules(max_runs=max_runs, ctx=ctx)

    def tick(self, *, max_runs: int = 50, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._pipeline.tick_schedules(max_runs=max_runs, ctx=ctx)
