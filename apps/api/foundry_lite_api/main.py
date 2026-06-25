from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from foundry_lite.application.action_types import ActionApplyResponse, ActionWritebackReconciliationResult
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    BackupRestoreModeReport,
    BackupRestorePreflightReport,
    DatasetInspectionPayload,
    DatasetRow,
    DatasetVersionRow,
    DeadLetterRecordBulkRetryResult,
    DeadLetterRecordDiscardResult,
    DeadLetterRecordRetryResult,
    DeadLetterRecordRow,
    LineageEdgeRow,
    ObjectIndexRebuildResult,
    ObjectLinkPayload,
    ObjectPayload,
    ObjectQueryResult,
    ObjectSetPayload,
    ObjectSetQueryResult,
    ObservabilityDetectorConfig,
    ObservabilityReport,
    OntologyCatalogResult,
    OntologyValidationResult,
    ProductWorkflowRun,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    TabularRow,
    TransformRetryResult,
)
from foundry_lite.application.upload_limits import max_webhook_body_bytes
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from foundry_lite.infrastructure.auth import AUTHORIZATION_HEADER, AuthProvider, auth_provider_from_env
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.observability.metrics import prometheus_payload, record_http_request
from foundry_lite.observability.tracing import (
    configure_observability,
    instrument_fastapi_app,
    instrument_sqlalchemy_engine,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
ValidationErrorPayload = dict[str, object]


class ObservabilityDetectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    configs: list[JsonObject] = Field(default_factory=list)
    previous_incidents: list[JsonObject] = Field(default_factory=list, alias="previousIncidents")
    observed_at: str | None = Field(default=None, alias="observedAt")


class BackupRestorePreflightRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    backup_id: str | None = Field(default=None, alias="backupId")


class BackupRestoreModeStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    backup_id: str | None = Field(default=None, alias="backupId")
    restore_id: str | None = Field(default=None, alias="restoreId")


class BackupRestoreResumeApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    validation_id: str | None = Field(default=None, alias="validationId")


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


class OntologyValidateRequest(BaseModel):
    yaml_text: str = Field(alias="yaml")


class AipBuilderContextSourceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_id: str = Field(alias="sourceId")
    kind: str
    security_partition: str = Field(alias="securityPartition")
    selected_properties: list[str] = Field(default_factory=list, alias="selectedProperties")
    token_budget: int = Field(default=800, alias="tokenBudget")


class AipBuilderToolSpecRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(alias="toolId")
    version: str
    input_schema: JsonObject = Field(default_factory=dict, alias="inputSchema")
    output_schema: JsonObject = Field(default_factory=dict, alias="outputSchema")
    effect: str = "READ"
    required_permission: str = Field(default="object:read", alias="requiredPermission")
    confirmation_policy: str = Field(default="NONE", alias="confirmationPolicy")
    object_type_allowlist: list[str] = Field(default_factory=list, alias="objectTypeAllowlist")
    property_allowlist: list[str] = Field(default_factory=list, alias="propertyAllowlist")
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")
    max_result_items: int = Field(default=50, alias="maxResultItems")
    result_classification: str = Field(default="public", alias="resultClassification")
    status: str = "published"


class AipBuilderLogicBlockRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    block_id: str = Field(alias="blockId")
    kind: str
    inputs: JsonObject = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")


class AipBuilderValidateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_version_id: str = Field(alias="agentVersionId")
    release_channel: str = Field(alias="releaseChannel")
    model_alias_version: str = Field(alias="modelAliasVersion")
    prompt_version_id: str = Field(alias="promptVersionId")
    context_sources: list[AipBuilderContextSourceRequest] = Field(alias="contextSources")
    tool_manifest: list[AipBuilderToolSpecRequest] = Field(alias="toolManifest")
    logic_blocks: list[AipBuilderLogicBlockRequest] = Field(alias="logicBlocks")
    eval_axes: list[str] = Field(alias="evalAxes")
    agent_allowed_actions: list[str] = Field(default_factory=list, alias="agentAllowedActions")
    max_logic_blocks: int = Field(default=25, alias="maxLogicBlocks")


class AipBuilderRunRequest(AipBuilderValidateRequest):
    logic_run_id: str = Field(alias="logicRunId")
    ai_run_id: str | None = Field(default=None, alias="aiRunId")
    session_id: str | None = Field(default=None, alias="sessionId")
    input_json: JsonObject = Field(default_factory=dict, alias="inputJson")
    user_message: str = Field(default="", alias="userMessage")
    agent_allowed_tools: list[str] = Field(default_factory=list, alias="agentAllowedTools")
    model_allowed_classifications: list[str] = Field(
        default_factory=lambda: ["public", "internal"],
        alias="modelAllowedClassifications",
    )
    policy_version: str = Field(default="policy-v1", alias="policyVersion")


class AipAgentRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_run_id: str = Field(default="agent-run-default", alias="agentRunId")
    agent_version_id: str = Field(alias="agentVersionId")
    model_alias: str = Field(default="default-completion", alias="modelAlias")
    prompt_version_id: str = Field(alias="promptVersionId")
    user_message: str = Field(alias="userMessage")
    agent_instruction: str = Field(
        default="Answer the operator using cited context.",
        alias="agentInstruction",
    )
    security_partition: str = Field(alias="securityPartition")
    allowed_security_partitions: list[str] = Field(alias="allowedSecurityPartitions")
    state_json: JsonObject = Field(default_factory=dict, alias="stateJson")
    output_schema: JsonObject | None = Field(default=None, alias="outputSchema")
    ai_run_id: str | None = Field(default=None, alias="aiRunId")
    session_id: str | None = Field(default=None, alias="sessionId")
    ontology_version_id: str = Field(default="active-ontology", alias="ontologyVersionId")
    data_classification: str = Field(default="internal", alias="dataClassification")
    region_requirement: str | None = Field(default=None, alias="regionRequirement")
    max_context_items: int = Field(default=4, alias="maxContextItems")
    max_context_tokens: int = Field(default=1200, alias="maxContextTokens")
    max_model_calls: int = Field(default=1, alias="maxModelCalls")
    max_loop_iterations: int = Field(default=1, alias="maxLoopIterations")
    max_output_tokens: int = Field(default=512, alias="maxOutputTokens")
    policy_version: str = Field(default="policy-v1", alias="policyVersion")


class DeadLetterBulkRetryRequest(BaseModel):
    ids: list[str]


class ConnectorSyncWorkflowStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_ref: str = Field(alias="datasetRef")
    connector_name: str = Field(alias="connectorName")
    resource_name: str = Field(alias="resourceName")
    sync_name: str | None = Field(default=None, alias="syncName")


class ActionWritebackReconciliationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remote_status: str = Field(alias="remoteStatus")
    remote_resource_id: str = Field(alias="remoteResourceId")


class InsightReviewCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    claim_id: str = Field(alias="claimId")
    claim_text: str = Field(alias="claimText")
    evidence_object_ids: list[str] = Field(alias="evidenceObjectIds")
    evidence_refs: list[JsonObject] = Field(alias="evidenceRefs")
    priority: str = "normal"
    assignee_user_id: str | None = Field(default=None, alias="assigneeUserId")
    action_proposal: JsonObject | None = Field(default=None, alias="actionProposal")


class InsightReviewAssignRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignee_user_id: str = Field(alias="assigneeUserId")


class InsightReviewDecisionRequest(BaseModel):
    decision: str
    comment: str | None = None


WEBHOOK_SIGNING_KEY_ENV = "FOUNDRY_LITE_WEBHOOK_SIGNING_KEY"
WEBHOOK_SIGNING_KEY_NAME = "webhook_signing_key"


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


def _validation_errors(exc: RequestValidationError) -> list[ValidationErrorPayload]:
    return [
        {
            "type": str(error.get("type", "validation_error")),
            "loc": [str(item) for item in error.get("loc", ())],
            "msg": str(error.get("msg", "request validation failed")),
        }
        for error in exc.errors()
    ]


def _metrics_route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "__unmatched__"


def _ctx(
    request: Request | None = None,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
) -> RequestContext:
    defaults = RequestContext()
    credentials = _collect_credentials(
        request,
        authorization=authorization,
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
    authorization: str | None,
    x_tenant_id: str | None,
    x_user_id: str | None,
    x_roles: str | None,
) -> dict[str, str]:
    pairs = (
        ("Authorization", _header_or_request(authorization, request, AUTHORIZATION_HEADER)),
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


@app.get("/api/datasets")
def list_datasets(request: Request) -> list[DatasetRow]:
    try:
        return foundry.datasets.list_datasets(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/preview")
def preview_dataset(request: Request, namespace: str, name: str, limit: int = 100) -> list[TabularRow]:
    try:
        return foundry.datasets.preview(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/versions")
def list_dataset_versions(request: Request, namespace: str, name: str) -> list[DatasetVersionRow]:
    try:
        return foundry.datasets.list_versions(f"{namespace}.{name}", ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/inspect")
def inspect_dataset(request: Request, namespace: str, name: str, version: str = "latest") -> DatasetInspectionPayload:
    try:
        return foundry.datasets.inspect(f"{namespace}.{name}", version=version, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/ontology/catalog")
def ontology_catalog(request: Request) -> OntologyCatalogResult:
    try:
        return foundry.ontology.catalog(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/ontology/validate")
def validate_ontology(request: Request, payload: OntologyValidateRequest) -> OntologyValidationResult:
    try:
        return foundry.ontology.validate(payload.yaml_text, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/aip/builder/validate")
def validate_aip_builder(request: Request, payload: AipBuilderValidateRequest) -> JsonObject:
    result = foundry.aip.validate_builder_payload(
        payload=payload.model_dump(by_alias=True),
        ctx=_ctx(request),
    )
    return result.to_payload()


@app.post("/api/aip/builder/run")
def run_aip_builder(request: Request, payload: AipBuilderRunRequest) -> JsonObject:
    result = foundry.aip.run_builder_payload(
        payload=payload.model_dump(by_alias=True),
        ctx=_ctx(request),
    )
    return result.to_payload()


@app.post("/api/aip/agent/run")
def run_aip_agent(request: Request, payload: AipAgentRunRequest) -> JsonObject:
    result = foundry.aip.run_agent_payload(
        payload=payload.model_dump(by_alias=True),
        ctx=_ctx(request),
    )
    return result.to_payload()


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


@app.get("/api/objects/{object_type}/{object_id}/links/{link_type}")
def get_object_links(request: Request, object_type: str, object_id: str, link_type: str) -> list[ObjectLinkPayload]:
    try:
        return foundry.objects.links(object_type, object_id, link_type, ctx=_ctx(request))
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


@app.get("/api/insights/reviews")
def list_insight_reviews(
    request: Request,
    status: str | None = Query(default=None),
    assignee_user_id: str | None = Query(default=None, alias="assigneeUserId"),
    limit: int = Query(default=50),
) -> JsonObject:
    try:
        return foundry.insights.list(
            ctx=_ctx(request),
            status=status,
            assignee_user_id=assignee_user_id,
            limit=limit,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/insights/reviews")
def create_insight_review(
    request: Request,
    payload: InsightReviewCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.insights.create(
            claim_id=payload.claim_id,
            claim_text=payload.claim_text,
            evidence_object_ids=payload.evidence_object_ids,
            evidence_refs=payload.evidence_refs,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
            priority=payload.priority,
            assignee_user_id=payload.assignee_user_id,
            action_proposal=payload.action_proposal,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/insights/reviews/{review_id}")
def get_insight_review(request: Request, review_id: str) -> JsonObject:
    try:
        return foundry.insights.get(review_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/insights/reviews/{review_id}/assign")
def assign_insight_review(
    request: Request,
    review_id: str,
    payload: InsightReviewAssignRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.insights.assign(
            review_id,
            assignee_user_id=payload.assignee_user_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/insights/reviews/{review_id}/decision")
def decide_insight_review(
    request: Request,
    review_id: str,
    payload: InsightReviewDecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.insights.decide(
            review_id,
            decision=payload.decision,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
            comment=payload.comment,
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


@app.get("/api/operations/lineage")
def get_operation_lineage(
    request: Request,
    resource_id: str = Query(alias="resourceId"),
) -> list[LineageEdgeRow]:
    try:
        return foundry.operations.lineage(resource_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/observability/detect")
def detect_observability_incidents(request: Request, payload: ObservabilityDetectRequest) -> ObservabilityReport:
    try:
        return foundry.operations.observability_report(
            ctx=_ctx(request),
            configs=cast(list[ObservabilityDetectorConfig], payload.configs),
            previous_incidents=payload.previous_incidents,
            observed_at=payload.observed_at,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/backup-restore/preflight")
def backup_restore_preflight(
    request: Request,
    payload: BackupRestorePreflightRequest,
) -> BackupRestorePreflightReport:
    try:
        return foundry.operations.restore_preflight_report(ctx=_ctx(request), backup_id=payload.backup_id)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/backup-restore/restore-mode/start")
def start_backup_restore_mode(
    request: Request,
    payload: BackupRestoreModeStartRequest,
) -> BackupRestoreModeReport:
    try:
        return foundry.operations.start_restore_mode(
            ctx=_ctx(request),
            backup_id=payload.backup_id,
            restore_id=payload.restore_id,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/backup-restore/restore-mode/{restore_id}")
def get_backup_restore_mode_status(request: Request, restore_id: str) -> BackupRestoreModeReport:
    try:
        return foundry.operations.restore_mode_status(restore_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/backup-restore/restore-mode/{restore_id}/approve-resume")
def approve_backup_restore_resume(
    request: Request,
    restore_id: str,
    payload: BackupRestoreResumeApprovalRequest,
) -> BackupRestoreModeReport:
    try:
        return foundry.operations.approve_restore_resume(
            restore_id,
            ctx=_ctx(request),
            validation_id=payload.validation_id,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/runs/{run_type}/{run_id}")
def get_operation_run_detail(request: Request, run_type: str, run_id: str) -> RuntimeRunDetail:
    try:
        return foundry.operations.run_detail(run_type, run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/workflows/connector-sync/start")
def start_connector_sync_workflow(
    request: Request,
    payload: ConnectorSyncWorkflowStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ProductWorkflowRun:
    try:
        return foundry.operations.start_connector_sync_workflow(
            payload.dataset_ref,
            connector_name=payload.connector_name,
            resource_name=payload.resource_name,
            sync_name=payload.sync_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/workflows/{workflow_run_id}")
def get_product_workflow_run(request: Request, workflow_run_id: str) -> ProductWorkflowRun:
    try:
        return foundry.operations.product_workflow_run(workflow_run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/reconciliation/{writeback_id}/resolve")
def resolve_action_writeback_reconciliation(
    request: Request,
    writeback_id: str,
    payload: ActionWritebackReconciliationRequest,
) -> ActionWritebackReconciliationResult:
    try:
        return cast(
            ActionWritebackReconciliationResult,
            foundry.operations.reconcile_action_writeback(
                writeback_id,
                remote_status=payload.remote_status,
                remote_resource_id=payload.remote_resource_id,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/maintenance/iceberg")
def get_iceberg_maintenance_plan(
    request: Request,
    dataset_ref: str = Query(alias="datasetRef"),
    branch: str = Query(default="main"),
    small_file_threshold_bytes: int | None = Query(default=None, alias="smallFileThresholdBytes"),
    file_count_threshold: int | None = Query(default=None, alias="fileCountThreshold"),
    read_amplification_threshold: float | None = Query(default=None, alias="readAmplificationThreshold"),
    retention_min_snapshots: int | None = Query(default=None, alias="retentionMinSnapshots"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.operations.plan_iceberg_maintenance(
                dataset_ref,
                ctx=_ctx(request),
                branch=branch,
                small_file_threshold_bytes=small_file_threshold_bytes,
                file_count_threshold=file_count_threshold,
                read_amplification_threshold=read_amplification_threshold,
                retention_min_snapshots=retention_min_snapshots,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/maintenance/iceberg/{dataset_ref}/plan")
def plan_iceberg_maintenance(
    request: Request,
    dataset_ref: str,
    branch: str = Query(default="main"),
    small_file_threshold_bytes: int | None = Query(default=None, alias="smallFileThresholdBytes"),
    file_count_threshold: int | None = Query(default=None, alias="fileCountThreshold"),
    read_amplification_threshold: float | None = Query(default=None, alias="readAmplificationThreshold"),
    retention_min_snapshots: int | None = Query(default=None, alias="retentionMinSnapshots"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.operations.plan_iceberg_maintenance(
                dataset_ref,
                ctx=_ctx(request),
                branch=branch,
                small_file_threshold_bytes=small_file_threshold_bytes,
                file_count_threshold=file_count_threshold,
                read_amplification_threshold=read_amplification_threshold,
                retention_min_snapshots=retention_min_snapshots,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/dead-letter-records")
def list_dead_letter_records(
    request: Request,
    status: str | None = Query(default=None),
) -> list[DeadLetterRecordRow]:
    try:
        return foundry.operations.list_dead_letter_records(ctx=_ctx(request), status=status)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/dead-letter-records/bulk-retry")
def bulk_retry_dead_letter_records(
    request: Request,
    payload: DeadLetterBulkRetryRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> DeadLetterRecordBulkRetryResult:
    try:
        return foundry.operations.bulk_retry_dead_letter_records(
            payload.ids,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/dead-letter-records/{record_id}")
def get_dead_letter_record(request: Request, record_id: str) -> DeadLetterRecordRow:
    try:
        return foundry.operations.dead_letter_record(record_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/dead-letter-records/{record_id}/retry")
def retry_dead_letter_record(
    request: Request,
    record_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> DeadLetterRecordRetryResult:
    try:
        return foundry.operations.retry_dead_letter_record(
            record_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/dead-letter-records/{record_id}/discard")
def discard_dead_letter_record(request: Request, record_id: str) -> DeadLetterRecordDiscardResult:
    try:
        return foundry.operations.discard_dead_letter_record(record_id, ctx=_ctx(request))
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


@app.post("/api/materializations/{api_name}/run")
def run_materialization(request: Request, api_name: str) -> JsonObject:
    try:
        result = foundry.materialization.run(api_name, ctx=_ctx(request))
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


@app.post("/api/connectors/webhooks/{connector_name}/{resource_name}")
async def ingest_webhook(
    request: Request,
    connector_name: str,
    resource_name: str,
    dataset_ref: str = Query(alias="datasetRef"),
    signature: str = Header(alias="X-Foundry-Lite-Signature"),
    signature_timestamp: str = Header(alias="X-Foundry-Lite-Timestamp"),
    event_id: str | None = Header(default=None, alias="X-Foundry-Lite-Event-ID"),
):
    try:
        raw_body = await _bounded_webhook_body(request)
        payload = _webhook_payload_request(raw_body)
        ctx = _ctx(request)
        return foundry.datasets.ingest_webhook_event(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            payload=_webhook_payload(payload),
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            secret=_webhook_signing_key(ctx, dataset_ref, connector_name, resource_name),
            event_id=event_id,
            ctx=ctx,
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


async def _bounded_webhook_body(request: Request) -> bytes:
    max_bytes = max_webhook_body_bytes()
    content_length = _request_content_length(request)
    if content_length is not None and content_length > max_bytes:
        raise _webhook_body_too_large(content_length, max_bytes)
    chunks: list[bytes] = []
    size_bytes = 0
    async for chunk in request.stream():
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            raise _webhook_body_too_large(size_bytes, max_bytes)
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _request_content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        content_length = int(value)
    except ValueError as exc:
        raise ValidationFailed("invalid content-length header", details={"header": "content-length"}) from exc
    if content_length < 0:
        raise ValidationFailed("invalid content-length header", details={"header": "content-length"})
    return content_length


def _webhook_body_too_large(size_bytes: int, max_bytes: int) -> ValidationFailed:
    return ValidationFailed(
        "webhook body exceeds configured size limit",
        details={"size_bytes": size_bytes, "max_bytes": max_bytes},
    )


def _webhook_payload_request(raw_body: bytes) -> WebhookPayloadRequest:
    try:
        return WebhookPayloadRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _webhook_payload(value: WebhookPayloadRequest) -> JsonObject:
    return {str(key): item for key, item in (value.model_extra or {}).items()}


def _webhook_signing_key(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
) -> str:
    try:
        return foundry.secret_provider.get_secret(WEBHOOK_SIGNING_KEY_NAME).value
    except FoundryLiteError as exc:
        _audit_webhook_secret_failure(ctx, dataset_ref, connector_name, resource_name, exc)
        raise


def _audit_webhook_secret_failure(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    exc: FoundryLiteError,
) -> None:
    foundry.operations.record_failure_audit(
        ctx=ctx,
        event_type="webhook.secret_resolution_failed",
        resource_type="webhook",
        resource_id=f"{connector_name}:{resource_name}",
        action="webhook:ingest",
        exc=exc,
        decision="deny",
        before_ref={"dataset_ref": dataset_ref},
        after_ref={"secret_name": WEBHOOK_SIGNING_KEY_NAME, "env_var": WEBHOOK_SIGNING_KEY_ENV},
        adapter="secret_provider.get_secret",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
