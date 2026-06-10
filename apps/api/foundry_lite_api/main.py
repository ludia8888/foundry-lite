from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports.auth_provider import AuthProvider
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.infrastructure.auth import HeaderTrustAuthProvider
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
core = FoundryLiteCore(
    dependencies=create_local_core_dependencies(
        db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
        storage_root=os.getenv("FOUNDRY_LITE_HOME", ".foundry-lite"),
        adapter_profile=os.getenv("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
    )
)
# Sprint 02A: HTTP requests authenticate through an explicit AuthProvider.
# Today's adapter trusts X-Tenant-ID/X-User-ID/X-Roles headers verbatim - the
# same posture as before, but now visible at the composition root so a JWT
# adapter can swap in without touching any handler.
auth_provider: AuthProvider = HeaderTrustAuthProvider()
instrument_fastapi_app(app)
instrument_sqlalchemy_engine(core.engine)


class ActionApplyRequest(BaseModel):
    target: dict[str, str]
    expected_object_version: int = Field(alias="expectedObjectVersion")
    params: dict[str, Any]


class ObjectSetCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    object_type: str = Field(alias="objectType")
    set_type: str = Field(alias="setType")
    visibility: str = "private"
    ids: list[str] | None = None
    filter_ast: dict[str, Any] | None = Field(default=None, alias="filter")
    ttl_seconds: int | None = Field(default=None, alias="ttlSeconds")


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next: Any) -> Response:
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
def preview_dataset(request: Request, namespace: str, name: str, limit: int = 100) -> list[dict[str, Any]]:
    try:
        return core.preview_dataset(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/objects/{object_type}/{object_id}")
def get_object(request: Request, object_type: str, object_id: str) -> dict[str, Any]:
    try:
        return core.get_object(object_type, object_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/object-sets")
def query_object_sets(
    request: Request,
    object_type: str | None = Query(default=None, alias="objectType"),
) -> dict[str, list[dict[str, Any]]]:
    try:
        return core.query_object_sets(ctx=_ctx(request), object_type_api_name=object_type)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/object-sets")
def create_object_set(request: Request, payload: ObjectSetCreateRequest) -> dict[str, Any]:
    try:
        return core.create_object_set(
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
def get_object_set(request: Request, set_id: str) -> dict[str, Any]:
    try:
        return core.get_object_set(set_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/actions/{action_type}/apply")
def apply_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        return core.apply_action(
            action_type,
            object_type=payload.target["objectType"],
            object_id=payload.target["objectId"],
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
