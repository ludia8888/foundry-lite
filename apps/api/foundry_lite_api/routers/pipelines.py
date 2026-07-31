"""Pipeline Builder graph, review, deploy, run, schedule, and lineage routes."""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncIterator

import anyio.to_thread
from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    JsonObject,
    PipelineBranchCreateRequest,
    PipelineBranchProposeRequest,
    PipelineBranchRebaseRequest,
    PipelineDeployRequest,
    PipelineGraphUpdateRequest,
    PipelinePreviewNodeRequest,
    PipelinePreviewRunCreateRequest,
    PipelineProposalAssignRequest,
    PipelineProposalDecisionRequest,
    PipelineRunCancelRequest,
    PipelineRunStartRequest,
    PipelineScheduleUpsertRequest,
)

router = APIRouter()


@router.get("/api/pipelines/node-types")
def list_pipeline_node_types(request: Request) -> JsonObject:
    try:
        return runtime.foundry.pipelines.node_types(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/media/processors")
def list_media_processors(request: Request) -> JsonObject:
    try:
        return runtime.foundry.pipelines.media_processors(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/trained-models")
def list_pipeline_trained_models(request: Request) -> JsonObject:
    try:
        return runtime.foundry.pipelines.trained_models(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches")
def create_pipeline_branch(
    request: Request,
    payload: PipelineBranchCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.create_branch(
            pipeline_id=payload.pipeline_id,
            name=payload.name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/branches")
def list_pipeline_branches(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.list_branches(status=status, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/branches/{branch_id}")
def get_pipeline_branch(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.get_branch(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/graph")
def update_pipeline_graph(request: Request, branch_id: str, payload: PipelineGraphUpdateRequest) -> JsonObject:
    try:
        return runtime.foundry.pipelines.update_graph(
            branch_id,
            graph=payload.graph,
            expected_fingerprint=payload.expected_fingerprint,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/branches/{branch_id}/diff")
def diff_pipeline_branch(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.diff(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/rebase")
def rebase_pipeline_branch(request: Request, branch_id: str, payload: PipelineBranchRebaseRequest) -> JsonObject:
    try:
        return runtime.foundry.pipelines.rebase(
            branch_id,
            expected_fingerprint=payload.expected_fingerprint,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/propose")
def propose_pipeline_branch(
    request: Request,
    branch_id: str,
    payload: PipelineBranchProposeRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.propose(
            branch_id,
            title=payload.title,
            idempotency_key=idempotency_key,
            description=payload.description,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/abandon")
def abandon_pipeline_branch(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.abandon(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/branches/{branch_id}/validate")
def validate_pipeline_graph(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.validate(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/branches/{branch_id}/nodes/{node_id}/casts")
def suggest_pipeline_casts(request: Request, branch_id: str, node_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.suggest_casts(branch_id, node_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/nodes/{node_id}/preview")
def preview_pipeline_node(
    request: Request,
    branch_id: str,
    node_id: str,
    payload: PipelinePreviewNodeRequest,
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.preview_node(
            branch_id,
            node_id,
            options={"limit": payload.limit},
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post(
    "/api/pipelines/branches/{branch_id}/preview-runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_pipeline_preview_run(
    request: Request,
    branch_id: str,
    payload: PipelinePreviewRunCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        ctx = _ctx(request)
        preview = runtime.foundry.pipelines.create_preview_run(
            branch_id,
            graph=payload.graph,
            target_node_id=payload.target_node_id,
            limits=payload.limits.model_dump(by_alias=True),
            idempotency_key=idempotency_key,
            ctx=ctx,
        )
        return preview
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/preview-runs/{preview_run_id}")
def get_pipeline_preview_run(request: Request, preview_run_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.get_preview_run(preview_run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/preview-runs/{preview_run_id}/cancel")
def cancel_pipeline_preview_run(request: Request, preview_run_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.cancel_preview_run(preview_run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/nodes/{node_id}/stats")
def stats_pipeline_node(
    request: Request,
    branch_id: str,
    node_id: str,
    payload: PipelinePreviewNodeRequest,
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.node_stats(
            branch_id,
            node_id,
            options={"limit": payload.limit},
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/branches/{branch_id}/tests/run")
def run_pipeline_tests(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.run_tests(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/proposals")
def list_pipeline_proposals(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.list_proposals(status=status, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/proposals/{proposal_id}")
def get_pipeline_proposal(request: Request, proposal_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.get_proposal(proposal_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/proposals/{proposal_id}/assign")
def assign_pipeline_proposal(
    request: Request,
    proposal_id: str,
    payload: PipelineProposalAssignRequest,
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.assign(
            proposal_id,
            assignee_user_id=payload.assignee_user_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/proposals/{proposal_id}/decision")
def decide_pipeline_proposal(
    request: Request,
    proposal_id: str,
    payload: PipelineProposalDecisionRequest,
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.decide(
            proposal_id,
            decision=payload.decision,
            comment=payload.comment,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/proposals/{proposal_id}/execute")
def execute_pipeline_proposal(request: Request, proposal_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.execute(proposal_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/proposals/{proposal_id}/withdraw")
def withdraw_pipeline_proposal(request: Request, proposal_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.withdraw(proposal_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/versions/{version_id}")
def get_pipeline_version(request: Request, version_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.get_version(version_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/runs/{run_id}")
def get_pipeline_run(request: Request, run_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.get_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/runs/{run_id}/timeline")
def get_pipeline_run_timeline(request: Request, run_id: str) -> JsonObject:
    try:
        return runtime.foundry.pipelines.timeline(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/runs/{run_id}/events")
async def stream_pipeline_run_events(
    request: Request,
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    ctx = _ctx(request)
    after_sequence = _event_sequence(last_event_id)
    return StreamingResponse(
        _pipeline_event_stream(request, run_id, after_sequence, ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/pipelines/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_pipeline_run(
    request: Request,
    run_id: str,
    payload: PipelineRunCancelRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.request_cancel(
            run_id,
            idempotency_key=idempotency_key,
            reason=payload.reason,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/scheduler/due")
def preview_pipeline_scheduler_due(
    request: Request,
    max_runs: int = Query(default=50, ge=1, le=100, alias="maxRuns"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.preview_due(max_runs=max_runs, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/scheduler/tick")
def run_pipeline_scheduler_tick(
    request: Request,
    max_runs: int = Query(default=50, ge=1, le=100, alias="maxRuns"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.tick(max_runs=max_runs, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/{pipeline_id}/versions")
def list_pipeline_versions(
    request: Request,
    pipeline_id: str,
    limit: int = Query(default=50, ge=1, le=100),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.list_versions(pipeline_id, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/{pipeline_id}/deploy/{version_id}")
def deploy_pipeline_version(
    request: Request,
    pipeline_id: str,
    version_id: str,
    payload: PipelineDeployRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.deploy(
            pipeline_id,
            version_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
            options=payload.options,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/{pipeline_id}/deployments")
def list_pipeline_deployments(
    request: Request,
    pipeline_id: str,
    limit: int = Query(default=50, ge=1, le=100),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.list_deployments(pipeline_id, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/{pipeline_id}/runs")
def list_pipeline_runs(
    request: Request,
    pipeline_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.list_runs(
            pipeline_id,
            cursor=cursor,
            limit=limit,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/{pipeline_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_pipeline_run(
    request: Request,
    response: Response,
    pipeline_id: str,
    payload: PipelineRunStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    wait_seconds: int = Query(default=0, ge=0, le=30, alias="waitSeconds"),
) -> JsonObject:
    try:
        snapshot = runtime.foundry.pipelines.start_async(
            pipeline_id,
            version_id=payload.version_id,
            idempotency_key=idempotency_key,
            parameters=payload.parameters,
            target_node_ids=payload.target_node_ids,
            wait_seconds=wait_seconds,
            ctx=_ctx(request),
        )
        if snapshot["status"] in {"succeeded", "partial", "failed", "cancelled"}:
            response.status_code = status.HTTP_200_OK
        return snapshot
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _event_sequence(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


async def _pipeline_event_stream(
    request: Request,
    run_id: str,
    after_sequence: int,
    ctx: RequestContext,
) -> AsyncIterator[str]:
    cursor = after_sequence
    heartbeat_count = 0
    while not await request.is_disconnected():
        # These are synchronous SQLAlchemy calls; running them directly on the
        # event loop (once per second per open stream) would block every other
        # async request. Offload to the threadpool like the object-subscription
        # SSE route does.
        page = await anyio.to_thread.run_sync(
            functools.partial(runtime.foundry.pipelines.events, run_id, after_sequence=cursor, limit=100, ctx=ctx)
        )
        events = page["events"]
        if isinstance(events, list) and events:
            for event in events:
                if not isinstance(event, dict):
                    continue
                cursor = int(event["id"])
                yield _sse_event(event)
            heartbeat_count = 0
            continue
        snapshot = await anyio.to_thread.run_sync(functools.partial(runtime.foundry.pipelines.get_run, run_id, ctx=ctx))
        if snapshot["status"] in {"succeeded", "partial", "failed", "cancelled"}:
            return
        heartbeat_count += 1
        if heartbeat_count >= 15:
            yield ": heartbeat\n\n"
            heartbeat_count = 0
        await asyncio.sleep(1)


def _sse_event(event: JsonObject) -> str:
    data = json.dumps(event["data"], separators=(",", ":"), ensure_ascii=False)
    return f"id: {event['id']}\nevent: {event['event']}\ndata: {data}\n\n"


@router.put("/api/pipelines/{pipeline_id}/schedule")
def upsert_pipeline_schedule(
    request: Request,
    pipeline_id: str,
    payload: PipelineScheduleUpsertRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.upsert_schedule(
            pipeline_id,
            version_id=payload.version_id,
            schedule=payload.schedule.model_dump(by_alias=True, exclude_none=True),
            enabled=payload.enabled,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/pipelines/{pipeline_id}/schedule")
def get_pipeline_schedule(request: Request, pipeline_id: str) -> JsonObject | None:
    try:
        return runtime.foundry.pipelines.get_schedule(pipeline_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/{pipeline_id}/schedule/pause")
def pause_pipeline_schedule(
    request: Request,
    pipeline_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.pause_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/pipelines/{pipeline_id}/schedule/resume")
def resume_pipeline_schedule(
    request: Request,
    pipeline_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.resume_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.delete("/api/pipelines/{pipeline_id}/schedule")
def delete_pipeline_schedule(
    request: Request,
    pipeline_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.pipelines.delete_schedule(
            pipeline_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
