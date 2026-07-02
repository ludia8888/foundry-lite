"""Transform registration, scheduling, run, and materialization routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from foundry_lite.application.ports import TransformRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import JsonObject, TransformSchedulerTickRequest, TransformSqlRegisterRequest

router = APIRouter()


@router.post("/api/transforms/sql")
def register_sql_transform(request: Request, payload: TransformSqlRegisterRequest) -> TransformRow:
    try:
        return runtime.foundry.transforms.register_sql(
            payload.api_name,
            sql=payload.sql,
            inputs=payload.inputs,
            output_dataset_ref=payload.output_dataset_ref,
            checks=payload.checks,
            mode=payload.mode,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/transforms/scheduler/due")
def preview_transform_scheduler_due(
    request: Request,
    max_runs: int = Query(default=50, ge=1, le=500, alias="maxRuns"),
) -> JsonObject:
    try:
        return runtime.foundry.transforms.preview_due(ctx=_ctx(request), max_runs=max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/transforms/scheduler/tick")
def run_transform_scheduler_tick(request: Request, payload: TransformSchedulerTickRequest) -> JsonObject:
    try:
        return runtime.foundry.transforms.run_due(ctx=_ctx(request), max_runs=payload.max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/transforms/{api_name}/run")
def run_transform(request: Request, api_name: str) -> JsonObject:
    try:
        return _commit_result_payload(runtime.foundry.transforms.run(api_name, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/materializations/{api_name}/run")
def run_materialization(request: Request, api_name: str) -> JsonObject:
    try:
        result = runtime.foundry.materialization.run(api_name, ctx=_ctx(request))
        return {
            "dataset_id": result.dataset_id,
            "dataset_ref": result.dataset_ref,
            "transaction_id": result.transaction_id,
            "version_id": result.version_id,
            "version_number": result.version_number,
            "row_count": result.row_count,
            "manifest_uri": result.manifest_uri,
            "schema_hash": result.schema_hash,
        }
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _commit_result_payload(result: CommitResult) -> JsonObject:
    return {
        "dataset_id": result.dataset_id,
        "dataset_ref": result.dataset_ref,
        "transaction_id": result.transaction_id,
        "version_id": result.version_id,
        "version_number": result.version_number,
        "row_count": result.row_count,
        "manifest_uri": result.manifest_uri,
        "schema_hash": result.schema_hash,
    }
