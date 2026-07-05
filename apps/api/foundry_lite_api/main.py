"""Foundry-lite API composition root.

Assembles the FastAPI app from the per-resource routers and keeps the
historical ``foundry_lite_api.main`` import surface (request models, route
handlers, helpers) as re-exports so existing tests and tooling keep working.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from foundry_lite.observability.metrics import prometheus_payload, record_http_request
from foundry_lite.observability.tracing import instrument_fastapi_app, instrument_sqlalchemy_engine

from foundry_lite_api import runtime
from foundry_lite_api.errors import (  # noqa: F401
    _code_for_error,
    _handle_error,
    _status_for_error,
    _validation_errors,
)
from foundry_lite_api.request_context import (  # noqa: F401
    _header_or_request,
    _request_id,
    _websocket_subprotocol_bearer,
)
from foundry_lite_api.routers import (
    actions,
    aip,
    auth,
    connectors,
    datasets,
    developer_console,
    functions,
    insights,
    media,
    object_sets,
    objects,
    ontology,
    operations,
    sources,
    transforms,
)
from foundry_lite_api.routers.aip import (  # noqa: F401
    promote_aip_release,
    run_aip_eval,
)
from foundry_lite_api.routers.auth import (  # noqa: F401
    _scope_query,
)
from foundry_lite_api.routers.connectors import (  # noqa: F401
    create_connector_connection,
    get_connector_connection,
    list_connector_connections,
    start_connector_resource_sync,
    test_connector_resource,
    update_connector_connection,
    upsert_connector_resource,
)
from foundry_lite_api.routers.datasets import (  # noqa: F401
    create_dataset_quality_contract_check,
    get_dataset_quality_result_summary,
    list_dataset_quality_contract_checks,
    list_dataset_quality_results,
    update_dataset_quality_contract_check,
)
from foundry_lite_api.routers.insights import (  # noqa: F401
    execute_approved_action,
)
from foundry_lite_api.routers.media import (  # noqa: F401
    bind_media_reference,
    commit_media_transaction,
    create_media_set,
    get_media_derivative,
    get_media_processing_run,
    get_media_set,
    index_media_derivative,
    index_media_visual_derivative,
    list_media_processing_runs,
    open_media_transaction,
    process_media_version,
    promote_media_visual_generation,
    resolve_media_reference,
    search_media_content,
    search_media_visual,
    upload_media_file,
)
from foundry_lite_api.routers.operations import (  # noqa: F401
    list_action_writeback_reconciliation_queue,
    resolve_action_writeback_reconciliation,
)
from foundry_lite_api.routers.sources import (  # noqa: F401
    _source_batch_uploads,
    create_debezium_source,
    create_source_credential,
    create_source_managed_sync,
    create_source_network_policy,
    create_webhook_listener_source,
    explore_source,
    get_source,
    get_source_credential,
    get_source_managed_sync,
    get_source_managed_sync_run,
    get_webhook_listener_source,
    heartbeat_source_agent,
    list_source_agents,
    list_source_credentials,
    list_source_managed_sync_runs,
    list_source_managed_syncs,
    list_source_network_policies,
    list_source_templates,
    list_sources,
    register_source_agent,
    start_debezium_source_sync,
    start_source_managed_sync_run,
    upload_batch_file_source,
    upload_media_source,
)
from foundry_lite_api.routers.transforms import (  # noqa: F401
    register_sql_transform,
    run_transform,
)
from foundry_lite_api.runtime import (  # noqa: F401
    ALLOWED_BROWSER_ORIGINS,
    _ApiWindowRateLimiter,
)
from foundry_lite_api.schemas import (  # noqa: F401
    ActionWritebackReconciliationRequest,
    AipEvalCaseRequest,
    AipEvalRunRequest,
    AipReleasePromotionRequest,
    ApprovalExecutionRequest,
    ConnectorResourceSyncStartRequest,
    DatasetQualityContractCheckCreateRequest,
    DatasetQualityContractCheckUpdateRequest,
    MediaBindReferenceRequest,
    MediaIndexDerivativeRequest,
    MediaOpenTransactionRequest,
    MediaProcessRequest,
    MediaSearchRequest,
    MediaSetCreateRequest,
    MediaVisualPromoteRequest,
    MediaVisualSearchRequest,
    RestConnectorConnectionCreateRequest,
    RestConnectorConnectionUpdateRequest,
    RestConnectorResourceUpsertRequest,
    SourceAgentRegisterRequest,
    SourceBatchFileManifest,
    SourceCredentialCreateRequest,
    SourceDebeziumCreateRequest,
    SourceDebeziumSyncStartRequest,
    SourceExploreRequest,
    SourceManagedSyncCreateRequest,
    SourceManagedSyncRunStartRequest,
    SourceNetworkPolicyCreateRequest,
    SourceWebhookListenerCreateRequest,
    TransformSqlRegisterRequest,
)
from foundry_lite_api.serializers import (  # noqa: F401
    _json_form_object,
    _json_form_string_list,
    _optional_json_form_object,
    _sse_json_events,
    _with_first_event,
)
from foundry_lite_api.webhooks import (  # noqa: F401
    WEBHOOK_SERVICE_ACTOR_PREFIX,
    WEBHOOK_SERVICE_PRINCIPAL_HEADER,
    WEBHOOK_SERVICE_TENANT_HEADER,
    WEBHOOK_SIGNING_KEY_ENV,
    _bounded_webhook_body,
    _request_content_length,
    _webhook_identity_header,
    _webhook_request_context,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    api_runtime = runtime.initialize_api_runtime()
    instrument_sqlalchemy_engine(api_runtime.foundry.engine)
    yield


app = FastAPI(title="Foundry-lite API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_BROWSER_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)
instrument_fastapi_app(app)


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
            _metrics_route_path(request),
            status_code,
            time.perf_counter() - started_at,
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = _request_id(request, f"api-{time.time_ns()}")
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "VALIDATION_FAILED",
                "message": "request validation failed",
                "details": {"validation_errors": _validation_errors(exc)},
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )


def _metrics_route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "__unmatched__"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    payload, media_type = prometheus_payload()
    return Response(content=payload, media_type=media_type)


# Routers are included in the original single-module registration order so
# path-matching precedence is unchanged.
for resource_router in (
    datasets.router,
    ontology.router,
    aip.router,
    media.router,
    objects.router,
    auth.router,
    developer_console.router,
    insights.router,
    object_sets.router,
    operations.router,
    sources.router,
    connectors.router,
    transforms.router,
    actions.router,
    functions.router,
):
    app.include_router(resource_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
