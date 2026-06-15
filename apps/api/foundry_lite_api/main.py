from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    ObjectIndexRebuildResult,
    ObjectPayload,
    ObjectQueryResult,
    ObjectSetPayload,
    ObjectSetQueryResult,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    TabularRow,
    TransformRetryResult,
)
from foundry_lite.application.ports.auth_provider import AuthProvider
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.infrastructure.auth import auth_provider_from_env
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.observability.metrics import prometheus_payload, record_http_request
from foundry_lite.observability.tracing import (
    configure_observability,
    instrument_fastapi_app,
    instrument_sqlalchemy_engine,
)
from pydantic import BaseModel, ConfigDict, Field

configure_observability("foundry-lite-api")
app = FastAPI(title="Foundry-lite API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
foundry = FoundryLite(
    dependencies=create_local_core_dependencies(
        db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
        storage_root=os.getenv("FOUNDRY_LITE_HOME", ".foundry-lite"),
        adapter_profile=os.getenv("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
    )
)
# Sprint 36A: choose auth through a profile guard so production startup cannot
# accidentally use the local header-trust adapter.
auth_provider: AuthProvider = auth_provider_from_env()
instrument_fastapi_app(app)
instrument_sqlalchemy_engine(foundry.engine)

JsonObject = dict[str, object]


class ActionTargetRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_type: str = Field(alias="objectType")
    object_id: str = Field(alias="objectId")


class ActionApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target: ActionTargetRequest
    expected_object_version: int = Field(alias="expectedObjectVersion")
    params: JsonObject


class ObjectSetCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    object_type: str = Field(alias="objectType")
    set_type: str = Field(alias="setType")
    visibility: str = "private"
    ids: list[str] | None = None
    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    ttl_seconds: int | None = Field(default=None, alias="ttlSeconds")


class ObjectQueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    order_by: list[dict[str, str]] | None = Field(default=None, alias="orderBy")
    limit: int = 50
    cursor: str | None = None
    search_text: str | None = Field(default=None, alias="search")


class WebhookPayloadRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


WEBHOOK_SIGNING_KEY_ENV = "FOUNDRY_LITE_WEBHOOK_SIGNING_KEY"


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    started_at = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or f"api-{time.time_ns()}"
    request.state.request_id = request_id
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        record_http_request(
            request.method,
            request.url.path,
            status_code,
            time.perf_counter() - started_at,
        )


def _ctx(
    request: Request | None = None,
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
) -> RequestContext:
    defaults = RequestContext()
    credentials = _collect_credentials(
        request,
        x_tenant_id=x_tenant_id,
        x_user_id=x_user_id,
        x_roles=x_roles,
    )
    principal = auth_provider.authenticate(credentials) if credentials else auth_provider.anonymous()
    return RequestContext(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.actor_user_id,
        request_id=_request_id(request, defaults.request_id),
        roles=principal.roles,
    )


def _collect_credentials(
    request: Request | None,
    *,
    x_tenant_id: str | None,
    x_user_id: str | None,
    x_roles: str | None,
) -> dict[str, str]:
    pairs = (
        ("X-Tenant-ID", _header_or_request(x_tenant_id, request, "X-Tenant-ID")),
        ("X-User-ID", _header_or_request(x_user_id, request, "X-User-ID")),
        ("X-Roles", _header_or_request(x_roles, request, "X-Roles")),
    )
    return {key: value for key, value in pairs if value}


def _header_or_request(value: str | None, request: Request | None, header_name: str) -> str | None:
    normalized = value if isinstance(value, str) else None
    if normalized or request is None:
        return normalized
    return request.headers.get(header_name)


def _request_id(request: Request | None, default_request_id: str) -> str:
    state = getattr(request, "state", None)
    return getattr(state, "request_id", default_request_id)


def _handle_error(exc: FoundryLiteError, request: Request | None = None) -> HTTPException:
    status_by_code = {
        "NOT_FOUND": 404,
        "CONFLICT": 409,
        "PERMISSION_DENIED": 403,
    }
    status = status_by_code.get(exc.code, 400)
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.message, "details": exc.details, "request_id": request_id},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    payload, media_type = prometheus_payload()
    return Response(content=payload, media_type=media_type)


@app.get("/api/datasets/{namespace}/{name}/preview")
def preview_dataset(request: Request, namespace: str, name: str, limit: int = 100) -> list[TabularRow]:
    try:
        return foundry.datasets.preview(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/objects/{object_type}/{object_id}")
def get_object(
    request: Request,
    object_type: str,
    object_id: str,
    include_explain: bool = Query(default=False, alias="explain"),
) -> ObjectPayload:
    try:
        return foundry.objects.get(object_type, object_id, ctx=_ctx(request), include_explain=include_explain)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/objects/{object_type}/query")
def query_objects(request: Request, object_type: str, payload: ObjectQueryRequest) -> ObjectQueryResult:
    try:
        return foundry.objects.query(
            object_type,
            ctx=_ctx(request),
            filter_ast=payload.filter_ast,
            order_by=payload.order_by,
            limit=payload.limit,
            cursor=payload.cursor,
            search_text=payload.search_text,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/object-sets")
def query_object_sets(
    request: Request,
    object_type: str | None = Query(default=None, alias="objectType"),
) -> ObjectSetQueryResult:
    try:
        return foundry.objects.query_sets(ctx=_ctx(request), object_type_api_name=object_type)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/object-sets")
def create_object_set(request: Request, payload: ObjectSetCreateRequest) -> ObjectSetPayload:
    try:
        return foundry.objects.create_set(
            payload.name,
            payload.object_type,
            set_type=payload.set_type,
            object_ids=payload.ids,
            filter_ast=payload.filter_ast,
            visibility=payload.visibility,
            ttl_seconds=payload.ttl_seconds,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/object-sets/{set_id}")
def get_object_set(request: Request, set_id: str) -> ObjectSetPayload:
    try:
        return foundry.objects.get_set(set_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/runs")
def list_operation_runs(
    request: Request,
    run_type: str | None = Query(default=None, alias="runType"),
    status: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
) -> RuntimeRunQueryResult:
    try:
        return foundry.operations.query_runs(
            ctx=_ctx(request),
            run_type=run_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/runs/{run_type}/{run_id}")
def get_operation_run_detail(request: Request, run_type: str, run_id: str) -> RuntimeRunDetail:
    try:
        return foundry.operations.run_detail(run_type, run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/dead-letter-events/{event_id}/retry")
def retry_dead_letter_event(request: Request, event_id: str) -> RuntimeRetryResult:
    try:
        return foundry.operations.retry_dead_letter_event(event_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/index/{object_type}/replay")
def replay_object_index(request: Request, object_type: str) -> ObjectIndexRebuildResult:
    try:
        return foundry.objects.reindex(object_type, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/runs/index/{run_id}/replay")
def replay_failed_index_run(request: Request, run_id: str) -> ObjectIndexRebuildResult:
    try:
        return foundry.objects.replay_index_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/runs/transform/{run_id}/retry")
def retry_failed_transform_run(request: Request, run_id: str) -> TransformRetryResult:
    try:
        return foundry.transforms.retry_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/connectors/webhooks/{connector_name}/{resource_name}")
async def ingest_webhook(
    request: Request,
    connector_name: str,
    resource_name: str,
    payload: WebhookPayloadRequest,
    dataset_ref: str = Query(alias="datasetRef"),
    signature: str = Header(alias="X-Foundry-Lite-Signature"),
    signature_timestamp: str = Header(alias="X-Foundry-Lite-Timestamp"),
    event_id: str | None = Header(default=None, alias="X-Foundry-Lite-Event-ID"),
):
    try:
        raw_body = await request.body()
        return foundry.datasets.ingest_webhook_event(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            payload=_webhook_payload(payload),
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            secret=_webhook_signing_key(),
            event_id=event_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/actions/{action_type}/apply")
def apply_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ActionApplyResponse:
    try:
        return foundry.actions.apply(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _webhook_payload(value: WebhookPayloadRequest) -> JsonObject:
    return {str(key): item for key, item in (value.model_extra or {}).items()}


def _webhook_signing_key() -> str:
    return os.getenv(WEBHOOK_SIGNING_KEY_ENV, "")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
