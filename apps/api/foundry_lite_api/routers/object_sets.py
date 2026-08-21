"""Object set query and creation routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from foundry_lite.application.ports import ObjectSetPayload, ObjectSetQueryResult
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import ObjectSetCreateRequest

router = APIRouter()


@router.get("/api/object-sets")
def query_object_sets(
    request: Request,
    object_type: str | None = Query(default=None, alias="objectType"),
) -> ObjectSetQueryResult:
    try:
        return runtime.foundry.objects.query_sets(ctx=_ctx(request), object_type_api_name=object_type)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/object-sets")
def create_object_set(request: Request, payload: ObjectSetCreateRequest) -> ObjectSetPayload:
    try:
        return runtime.foundry.objects.create_set(
            payload.name,
            payload.object_type,
            set_type=payload.set_type,
            definition=payload.definition,
            object_ids=payload.ids,
            filter_ast=payload.filter_ast,
            visibility=payload.visibility,
            access_scope=payload.access_scope,
            lifecycle=payload.lifecycle,
            ttl_seconds=payload.ttl_seconds,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/object-sets/{set_id}")
def get_object_set(request: Request, set_id: str) -> ObjectSetPayload:
    try:
        return runtime.foundry.objects.get_set(set_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
