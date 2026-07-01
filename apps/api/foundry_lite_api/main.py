from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Protocol, cast

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionValidationResponse,
    ActionWritebackQueueResult,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
)
from foundry_lite.application.admin_overview import AdminReadinessOverview, AdminTaskPlan
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    BackupRestoreRecoveryOverview,
    DatasetInspectionPayload,
    DatasetQualityContractCheck,
    DatasetQualityContractCheckCreateResult,
    DatasetQualityContractCheckList,
    DatasetQualityContractVersion,
    DatasetQualityContractVersionCreateResult,
    DatasetQualityContractVersionList,
    DatasetQualityResultHistory,
    DatasetQualityResultSummary,
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
    ObservabilityStoredReport,
    OntologyCatalogResult,
    OntologyObjectReindexResult,
    OntologyValidationResult,
    ProductWorkflowRun,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    StoredObservabilityIncident,
    TabularRow,
    TransformRecordDlqRetryResult,
    TransformRetryResult,
    TransformRow,
)
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.aip.eval_types import AiEvalError, EvalCaseInput
from foundry_lite.application.services.source_onboarding_config import SourceUpload
from foundry_lite.application.upload_limits import max_webhook_body_bytes
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, RateLimited, ValidationFailed
from foundry_lite.infrastructure.auth import AUTHORIZATION_HEADER, AuthProvider, auth_provider_from_env
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.observability.metrics import prometheus_payload, record_http_request
from foundry_lite.observability.tracing import (
    configure_observability,
    instrument_fastapi_app,
    instrument_sqlalchemy_engine,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError


@dataclass
class _ApiWindowRateLimiter:
    buckets: dict[tuple[str, ...], list[float]]

    def __init__(self) -> None:
        self.buckets = {}

    def retry_after_seconds(self, key: tuple[str, ...], *, limit: int, window_seconds: float) -> int | None:
        now = time.monotonic()
        recent = [seen_at for seen_at in self.buckets.get(key, []) if now - seen_at < window_seconds]
        if len(recent) >= limit:
            return max(1, int(window_seconds - (now - recent[0])))
        recent.append(now)
        self.buckets[key] = recent
        return None


configure_observability("foundry-lite-api")
app = FastAPI(title="Foundry-lite API", version="0.1.0")
ALLOWED_BROWSER_ORIGINS = ("http://127.0.0.1:4173", "http://localhost:4173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_BROWSER_ORIGINS),
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

WEBSOCKET_SUBSCRIPTION_CONNECT_LIMIT = int(os.getenv("FOUNDRY_LITE_WS_SUBSCRIPTION_CONNECT_LIMIT", "100"))
WEBSOCKET_SUBSCRIPTION_CONNECT_WINDOW_SECONDS = float(
    os.getenv("FOUNDRY_LITE_WS_SUBSCRIPTION_CONNECT_WINDOW_SECONDS", "60")
)
websocket_subscription_rate_limiter = _ApiWindowRateLimiter()

JsonObject = dict[str, object]
ValidationErrorPayload = dict[str, object]
MEDIA_UPLOAD_FILE = File(...)
SOURCE_BATCH_FILES = File(...)
MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY = Query(default=None, alias="allowedClassifications")


class PromptArtifactPayload(Protocol):
    @property
    def artifact_id(self) -> str: ...

    @property
    def ai_run_id(self) -> str: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def export_marking(self) -> str: ...

    @property
    def plaintext(self) -> str: ...


class ObservabilityDetectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    configs: list[JsonObject] = Field(default_factory=list)
    previous_incidents: list[JsonObject] = Field(default_factory=list, alias="previousIncidents")
    observed_at: str | None = Field(default=None, alias="observedAt")


class ObservabilityResolveRequest(BaseModel):
    reason: str | None = None


class BackupRestorePreflightRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    backup_id: str | None = Field(default=None, alias="backupId")


class BackupRestoreArtifactCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    backup_id: str | None = Field(default=None, alias="backupId")


class BackupRestoreArtifactRestoreRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    artifact_ref: str = Field(alias="artifactRef")
    artifact_hash: str | None = Field(default=None, alias="artifactHash")
    restore_id: str | None = Field(default=None, alias="restoreId")
    validation_id: str | None = Field(default=None, alias="validationId")
    should_run_post_restore_validation: bool = Field(default=True, alias="runPostRestoreValidation")


class BackupRestoreModeStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    backup_id: str | None = Field(default=None, alias="backupId")
    restore_id: str | None = Field(default=None, alias="restoreId")


class OntologyObjectReindexRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reindex_key: str = Field(alias="reindexKey")


class DatasetQualityContractCheckCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    config: JsonObject
    severity: str | None = None
    enabled: bool = True


class DatasetQualityContractCheckUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    config: JsonObject | None = None
    severity: str | None = None
    enabled: bool | None = None


class DatasetQualityContractVersionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    contract_key: str = Field(default="default", alias="contractKey")
    owner_user_id: str | None = Field(default=None, alias="ownerUserId")
    description: str | None = None


class BackupRestoreResumeApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    validation_id: str | None = Field(default=None, alias="validationId")


class BackupRestorePostRestoreValidationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    validation_id: str | None = Field(default=None, alias="validationId")


class OutboxPublishRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stream_name: str = Field(default="foundry-lite-outbox", alias="streamName")
    limit: int = Field(default=100, ge=1, le=500)


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
    visibility: str | None = None
    access_scope: str | None = Field(default=None, alias="accessScope")
    lifecycle: str | None = None
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


class ObjectSubscriptionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    order_by: list[dict[str, str]] | None = Field(default=None, alias="orderBy")
    properties: list[str] | None = None
    page_size: int = Field(default=50, alias="pageSize")
    last_seen_object_change_sequence: int | None = Field(default=None, alias="lastSeenObjectChangeSequence")
    max_events: int | None = Field(default=None, alias="maxEvents")
    poll_interval_seconds: float = Field(default=1.0, alias="pollIntervalSeconds")


class OsdkApplicationResourceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource_type: str = Field(alias="resourceType")
    resource_api_name: str = Field(alias="resourceApiName")
    scopes: list[str]


class OsdkApplicationCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    app_api_name: str = Field(alias="appApiName")
    display_name: str = Field(alias="displayName")
    client_id: str | None = Field(default=None, alias="clientId")
    resources: list[OsdkApplicationResourceRequest] = Field(default_factory=list)


class OsdkApplicationResourcesUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resources: list[OsdkApplicationResourceRequest]


class OsdkApplicationClientRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="clientId")
    redirect_uris: list[str] = Field(default_factory=list, alias="redirectUris")
    allowed_scopes: list[str] = Field(default_factory=list, alias="allowedScopes")
    access_token_ttl_seconds: int = Field(default=900, alias="accessTokenTtlSeconds")
    refresh_token_ttl_seconds: int = Field(default=2_592_000, alias="refreshTokenTtlSeconds")
    status: str = "active"


class OsdkSdkVersionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    language: str
    package_name: str | None = Field(default=None, alias="packageName")
    requested_bump: str | None = Field(default=None, alias="requestedBump")


class OsdkSdkCompatibilityWindowCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_version_id: str = Field(alias="fromVersionId")
    to_version_id: str = Field(alias="toVersionId")
    supported_until: str | None = Field(default=None, alias="supportedUntil")


class OsdkArtifactDownloadTokenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ttl_seconds: int = Field(default=900, alias="ttlSeconds")


class OsdkOAuthTokenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="clientId")
    code: str
    redirect_uri: str = Field(alias="redirectUri")
    code_verifier: str = Field(alias="codeVerifier")


class OsdkOAuthRefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(alias="refreshToken")


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
    model_allowed_classifications: list[str] | None = Field(default=None, alias="modelAllowedClassifications")
    region_requirement: str | None = Field(default=None, alias="regionRequirement")
    max_context_items: int = Field(default=4, alias="maxContextItems")
    max_context_tokens: int = Field(default=1200, alias="maxContextTokens")
    max_model_calls: int = Field(default=1, alias="maxModelCalls")
    max_loop_iterations: int = Field(default=1, alias="maxLoopIterations")
    max_tool_calls: int = Field(default=0, alias="maxToolCalls")
    max_tool_output_bytes: int = Field(default=4096, alias="maxToolOutputBytes")
    max_output_tokens: int = Field(default=512, alias="maxOutputTokens")
    policy_version: str = Field(default="policy-v1", alias="policyVersion")
    tool_manifest: list[AipBuilderToolSpecRequest] = Field(default_factory=list, alias="toolManifest")
    agent_allowed_tools: list[str] = Field(default_factory=list, alias="agentAllowedTools")
    agent_allowed_actions: list[str] = Field(default_factory=list, alias="agentAllowedActions")


class AipEvalCaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_api_name: str = Field(alias="caseApiName")
    axis: str
    input_json: JsonObject = Field(default_factory=dict, alias="inputJson")
    expected_json: JsonObject = Field(default_factory=dict, alias="expectedJson")
    actual_json: JsonObject = Field(default_factory=dict, alias="actualJson")
    rubric_json: JsonObject = Field(default_factory=dict, alias="rubricJson")
    tags: list[str] = Field(default_factory=list)
    sample_index: int = Field(default=1, alias="sampleIndex")
    evaluator: str = "exact_subset_v1"
    weight: float = 1.0


class AipEvalRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    eval_run_id: str = Field(alias="evalRunId")
    suite_api_name: str = Field(alias="suiteApiName")
    suite_version: str = Field(alias="suiteVersion")
    suite_description: str = Field(default="", alias="suiteDescription")
    agent_version_id: str = Field(alias="agentVersionId")
    candidate_release_channel: str = Field(alias="candidateReleaseChannel")
    cases: list[AipEvalCaseRequest]
    min_score: float = Field(default=1.0, alias="minScore")
    required_axes: list[str] = Field(default_factory=list, alias="requiredAxes")


class AipReleasePromotionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_version_id: str = Field(alias="agentVersionId")
    target_release_channel: str = Field(alias="targetReleaseChannel")
    eval_run_id: str = Field(alias="evalRunId")
    policy_version: str = Field(default="release-policy-v1", alias="policyVersion")


class MediaSetCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    name: str
    schema_type: str = Field(alias="schemaType")
    primary_format: str = Field(alias="primaryFormat")
    allowed_input_formats: list[str] = Field(alias="allowedInputFormats")
    classification: str
    transaction_policy: str = Field(default="transactional", alias="transactionPolicy")
    storage_profile: str = Field(default="local", alias="storageProfile")
    processing_profile: str = Field(default="local", alias="processingProfile")
    retention_policy_id: str | None = Field(default=None, alias="retentionPolicyId")


class MediaOpenTransactionRequest(BaseModel):
    mode: str = "APPEND"


class MediaProcessRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    processor: str
    processor_version: str = Field(alias="processorVersion")
    model: str | None = None
    model_version: str | None = Field(default=None, alias="modelVersion")
    parameters: JsonObject = Field(default_factory=dict)


class MediaIndexDerivativeRequest(BaseModel):
    generation: str


class MediaVisualPromoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_active: str = Field(alias="expectedActive")
    generation: str


class MediaSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str | None = None
    top_k: int = Field(default=10, alias="topK")
    allowed_classifications: list[str] | None = Field(default=None, alias="allowedClassifications")


class MediaVisualSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    top_k: int = Field(default=10, alias="topK")


class MediaBindReferenceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    holder_type: str = Field(alias="holderType")
    holder_id: str = Field(alias="holderId")
    property_name: str = Field(alias="propertyName")
    media_item_version_id: str = Field(alias="mediaItemVersionId")


class TransformSqlRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_name: str = Field(alias="apiName")
    sql: str
    inputs: dict[str, str]
    output_dataset_ref: str = Field(alias="outputDatasetRef")
    checks: list[JsonObject] = Field(default_factory=list)
    mode: str = "snapshot"


class TransformSchedulerTickRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_runs: int = Field(default=50, ge=1, le=500, alias="maxRuns")


class DeadLetterBulkRetryRequest(BaseModel):
    ids: list[str]


class ConnectorSyncWorkflowStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_ref: str = Field(alias="datasetRef")
    connector_name: str = Field(alias="connectorName")
    resource_name: str = Field(alias="resourceName")
    sync_name: str | None = Field(default=None, alias="syncName")


class ProductWorkflowCancelRequest(BaseModel):
    reason: str | None = None


class RestConnectorAuthRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: str = "none"
    token_secret_ref: str | None = Field(default=None, alias="tokenSecretRef")
    header_name: str | None = Field(default=None, alias="headerName")
    header_value_secret_ref: str | None = Field(default=None, alias="headerValueSecretRef")
    token: str | None = None
    header_value: str | None = Field(default=None, alias="headerValue")


class RestConnectorPaginationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items_path: str = Field(default="items", alias="itemsPath")
    next_cursor_path: str = Field(default="nextCursor", alias="nextCursorPath")
    cursor_query_param: str = Field(default="cursor", alias="cursorQueryParam")
    cursor_key: str = Field(default="cursor", alias="cursorKey")
    strategy: str = "cursor"


class RestConnectorConnectionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connector_name: str = Field(alias="connectorName")
    display_name: str = Field(alias="displayName")
    base_url: str = Field(alias="baseUrl")
    auth: RestConnectorAuthRequest = Field(default_factory=RestConnectorAuthRequest)
    rate_limit_per_minute: int | None = Field(default=None, alias="rateLimitPerMinute")
    allow_private_network: bool = Field(default=False, alias="allowPrivateNetwork")


class RestConnectorConnectionUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = Field(default=None, alias="displayName")
    base_url: str | None = Field(default=None, alias="baseUrl")
    auth: RestConnectorAuthRequest | None = None
    rate_limit_per_minute: int | None = Field(default=None, alias="rateLimitPerMinute")
    allow_private_network: bool | None = Field(default=None, alias="allowPrivateNetwork")
    status: str | None = None


class RestConnectorResourceUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_ref: str = Field(alias="datasetRef")
    resource_path: str = Field(alias="resourcePath")
    pagination: RestConnectorPaginationRequest = Field(default_factory=RestConnectorPaginationRequest)
    schema_columns: list[str] = Field(default_factory=list, alias="schemaColumns")
    primary_key: list[str] = Field(default_factory=list, alias="primaryKey")


class ConnectorResourceSyncStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sync_name: str | None = Field(default=None, alias="syncName")


class SourceWebhookListenerCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    dataset_ref: str = Field(alias="datasetRef")
    connector_name: str = Field(alias="connectorName")
    resource_name: str = Field(alias="resourceName")
    signing_secret_ref: str = Field(alias="signingSecretRef")


class SourceDebeziumCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    dataset_ref: str = Field(alias="datasetRef")
    stream_name: str = Field(alias="streamName")
    topic: str
    consumer_group: str = Field(default="foundry-lite-cdc", alias="consumerGroup")
    secret_refs: JsonObject = Field(default_factory=dict, alias="secretRefs")


class SourceDebeziumSyncStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_config_fingerprint: str | None = Field(default=None, alias="expectedConfigFingerprint")
    after_offset: int | None = Field(default=None, alias="afterOffset")
    limit: int | None = None


class SourceCredentialCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    credential_name: str = Field(alias="credentialName")
    display_name: str = Field(alias="displayName")
    kind: str
    auth_scheme: str = Field(alias="authScheme")
    secret_value: str = Field(alias="secretValue")
    secret_name: str | None = Field(default=None, alias="secretName")


class SourceAgentRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias="agentId")
    display_name: str = Field(alias="displayName")
    mode: str
    capabilities: JsonObject = Field(default_factory=dict)
    network_summary: JsonObject = Field(default_factory=dict, alias="networkSummary")


class SourceNetworkPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    policy_name: str = Field(alias="policyName")
    display_name: str = Field(alias="displayName")
    mode: str
    agent_id: str | None = Field(default=None, alias="agentId")
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts")


class SourceExploreRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    source_type: str = Field(alias="sourceType")
    request: JsonObject = Field(default_factory=dict)


class SourceManagedSyncCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sync_name: str = Field(alias="syncName")
    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    source_type: str = Field(alias="sourceType")
    capability: str
    mode: str = "APPEND"
    target_dataset_ref: str | None = Field(default=None, alias="targetDatasetRef")
    target_media_set_id: str | None = Field(default=None, alias="targetMediaSetId")
    schedule: JsonObject = Field(default_factory=dict)
    config_summary: JsonObject = Field(default_factory=dict, alias="configSummary")


class SourceManagedSyncRunStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trigger_type: str = Field(default="manual", alias="triggerType")
    batch_limit: int | None = Field(default=None, alias="batchLimit")


class SourceSchedulerTickRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_runs: int = Field(default=50, ge=1, le=500, alias="maxRuns")


class SourceBatchFileManifestItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_name: str = Field(alias="fileName")
    dataset_ref: str = Field(alias="datasetRef")


class SourceBatchFileManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    sync_name: str | None = Field(default=None, alias="syncName")
    files: list[SourceBatchFileManifestItem]


class ActionWritebackReconciliationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remote_status: str | None = Field(default=None, alias="remoteStatus")
    remote_resource_id: str | None = Field(default=None, alias="remoteResourceId")
    external_writeback_uri: str | None = Field(default=None, alias="externalWritebackUri")


class ActionWritebackRecoveryApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    approval_id: str = Field(alias="approvalId")
    reason: str
    external_writeback_uri: str | None = Field(default=None, alias="externalWritebackUri")


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


class ApprovalExecutionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_proposal_fingerprint: str = Field(alias="expectedProposalFingerprint")


WEBHOOK_SIGNING_KEY_ENV = "FOUNDRY_LITE_WEBHOOK_SIGNING_KEY"
WEBHOOK_SIGNING_KEY_NAME = "webhook_signing_key"
WEBHOOK_SERVICE_PRINCIPAL_HEADER = "X-Foundry-Lite-Service-Principal"
WEBHOOK_SERVICE_TENANT_HEADER = "X-Foundry-Lite-Tenant-ID"
WEBHOOK_SERVICE_ROLE = "connector_ingest"
WEBHOOK_SERVICE_ACTOR_PREFIX = "service-principal:"


@dataclass(frozen=True)
class WebhookRequestContext:
    ctx: RequestContext
    require_service_principal_signature: bool


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
    x_foundry_lite_app_id: str | None = Header(default=None),
    x_foundry_lite_client_id: str | None = Header(default=None),
    x_foundry_lite_scopes: str | None = Header(default=None),
) -> RequestContext:
    defaults = RequestContext()
    credentials = _collect_credentials(
        request,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_user_id=x_user_id,
        x_roles=x_roles,
        x_foundry_lite_app_id=x_foundry_lite_app_id,
        x_foundry_lite_client_id=x_foundry_lite_client_id,
        x_foundry_lite_scopes=x_foundry_lite_scopes,
    )
    principal = auth_provider.authenticate(credentials) if credentials else auth_provider.anonymous()
    return RequestContext(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.actor_user_id,
        request_id=_request_id(request, defaults.request_id),
        roles=principal.roles,
        application_id=principal.application_id,
        client_id=principal.client_id,
        token_scopes=principal.token_scopes,
    )


def _websocket_ctx(websocket: WebSocket) -> RequestContext:
    defaults = RequestContext()
    credentials = _collect_websocket_credentials(websocket)
    principal = auth_provider.authenticate(credentials) if credentials else auth_provider.anonymous()
    return RequestContext(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.actor_user_id,
        request_id=websocket.headers.get("X-Request-ID", defaults.request_id),
        roles=principal.roles,
        application_id=principal.application_id,
        client_id=principal.client_id,
        token_scopes=principal.token_scopes,
    )


def _collect_websocket_credentials(websocket: WebSocket) -> dict[str, str]:
    authorization = websocket.headers.get(AUTHORIZATION_HEADER) or _websocket_subprotocol_bearer(websocket)
    pairs = (
        ("Authorization", authorization),
        ("X-Tenant-ID", websocket.headers.get("X-Tenant-ID")),
        ("X-User-ID", websocket.headers.get("X-User-ID")),
        ("X-Roles", websocket.headers.get("X-Roles")),
        ("X-Foundry-Lite-App-ID", websocket.headers.get("X-Foundry-Lite-App-ID")),
        ("X-Foundry-Lite-Client-ID", websocket.headers.get("X-Foundry-Lite-Client-ID")),
        ("X-Foundry-Lite-Scopes", websocket.headers.get("X-Foundry-Lite-Scopes")),
    )
    return {key: value for key, value in pairs if value}


def _websocket_subprotocol_bearer(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("sec-websocket-protocol")
    if not header:
        return None
    for item in (part.strip() for part in header.split(",")):
        if item.startswith("bearer."):
            return f"Bearer {item.removeprefix('bearer.')}"
    return None


def _websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    return origin is None or origin in ALLOWED_BROWSER_ORIGINS


def _check_websocket_subscription_rate(ctx: RequestContext, object_type: str) -> None:
    key = (
        ctx.tenant_id,
        ctx.actor_user_id,
        ctx.application_id or "",
        ctx.client_id or "",
        object_type,
    )
    retry_after = websocket_subscription_rate_limiter.retry_after_seconds(
        key,
        limit=WEBSOCKET_SUBSCRIPTION_CONNECT_LIMIT,
        window_seconds=WEBSOCKET_SUBSCRIPTION_CONNECT_WINDOW_SECONDS,
    )
    if retry_after is not None:
        raise RateLimited(
            "WebSocket object subscription rate limit exceeded",
            details={"retryAfterSeconds": retry_after},
        )


def _collect_credentials(
    request: Request | None,
    *,
    authorization: str | None,
    x_tenant_id: str | None,
    x_user_id: str | None,
    x_roles: str | None,
    x_foundry_lite_app_id: str | None,
    x_foundry_lite_client_id: str | None,
    x_foundry_lite_scopes: str | None,
) -> dict[str, str]:
    pairs = (
        ("Authorization", _header_or_request(authorization, request, AUTHORIZATION_HEADER)),
        ("X-Tenant-ID", _header_or_request(x_tenant_id, request, "X-Tenant-ID")),
        ("X-User-ID", _header_or_request(x_user_id, request, "X-User-ID")),
        ("X-Roles", _header_or_request(x_roles, request, "X-Roles")),
        ("X-Foundry-Lite-App-ID", _header_or_request(x_foundry_lite_app_id, request, "X-Foundry-Lite-App-ID")),
        ("X-Foundry-Lite-Client-ID", _header_or_request(x_foundry_lite_client_id, request, "X-Foundry-Lite-Client-ID")),
        ("X-Foundry-Lite-Scopes", _header_or_request(x_foundry_lite_scopes, request, "X-Foundry-Lite-Scopes")),
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


def _resource_payloads(resources: list[OsdkApplicationResourceRequest]) -> list[JsonObject]:
    return [cast(JsonObject, resource.model_dump(by_alias=True)) for resource in resources]


def _scope_query(scope: str | None) -> tuple[str, ...]:
    if not scope:
        return ()
    return tuple(item.strip() for item in scope.replace(",", " ").split(" ") if item.strip())


def _sse_json_events(events: Iterable[JsonObject]) -> Iterator[str]:
    for payload in events:
        yield _sse_json_event(payload)


def _sse_json_event(payload: JsonObject) -> str:
    name = str(payload.get("event", "message"))
    return f"event: {name}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


def _with_first_event(first: JsonObject, events: Iterable[JsonObject]) -> Iterator[JsonObject]:
    yield first
    yield from events


def _handle_error(exc: FoundryLiteError, request: Request | None = None) -> HTTPException:
    status_by_code = {
        "NOT_FOUND": 404,
        "CONFLICT": 409,
        "PERMISSION_DENIED": 403,
        "RATE_LIMITED": 429,
    }
    status = _status_for_error(exc, status_by_code.get(exc.code, 400))
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return HTTPException(
        status_code=status,
        detail={"code": _code_for_error(exc), "message": exc.message, "details": exc.details, "request_id": request_id},
    )


def _websocket_error(exc: FoundryLiteError, request_id: str) -> JsonObject:
    return {"code": _code_for_error(exc), "message": exc.message, "details": exc.details, "request_id": request_id}


def _code_for_error(exc: FoundryLiteError) -> str:
    if exc.code != "APPROVAL_EXECUTION_ERROR":
        return exc.code
    reason = exc.details.get("reason")
    if isinstance(reason, str) and reason:
        return reason.upper()
    return exc.code


def _status_for_error(exc: FoundryLiteError, default_status: int) -> int:
    if exc.code != "APPROVAL_EXECUTION_ERROR":
        return default_status
    reason = exc.details.get("reason")
    if not isinstance(reason, str):
        return default_status
    not_found = {"action_not_found", "run_not_found", "target_not_found"}
    conflict = {
        "approval_object_version_conflict",
        "fingerprint_mismatch",
        "originating_tool_call_not_found",
        "review_expired",
        "review_not_approved",
    }
    denied = {"policy_denied", "source_access_denied"}
    if reason in not_found:
        return 404
    if reason in conflict:
        return 409
    if reason in denied:
        return 403
    return default_status


def _prompt_artifact_payload(result: PromptArtifactPayload) -> dict[str, object]:
    return {
        "artifactId": result.artifact_id,
        "aiRunId": result.ai_run_id,
        "contentHash": result.content_hash,
        "exportMarking": result.export_marking,
        "plaintext": result.plaintext,
    }


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


@app.get("/api/datasets/{namespace}/{name}/quality-contract/checks")
def list_dataset_quality_contract_checks(
    request: Request,
    namespace: str,
    name: str,
) -> DatasetQualityContractCheckList:
    try:
        return foundry.datasets.list_quality_contract_checks(f"{namespace}.{name}", ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/quality-contract/contracts")
def list_dataset_quality_contract_versions(
    request: Request,
    namespace: str,
    name: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetQualityContractVersionList:
    try:
        return foundry.datasets.list_data_contract_versions(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/datasets/{namespace}/{name}/quality-contract/contracts")
def create_dataset_quality_contract_version(
    request: Request,
    namespace: str,
    name: str,
    payload: DatasetQualityContractVersionCreateRequest,
) -> DatasetQualityContractVersionCreateResult:
    try:
        return foundry.datasets.create_data_contract_version(
            f"{namespace}.{name}",
            contract_key=payload.contract_key,
            owner_user_id=payload.owner_user_id,
            description=payload.description,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/quality-contract/contracts/{contract_version_id}")
def get_dataset_quality_contract_version(
    request: Request,
    namespace: str,
    name: str,
    contract_version_id: str,
) -> DatasetQualityContractVersion:
    try:
        return foundry.datasets.get_data_contract_version(
            f"{namespace}.{name}",
            contract_version_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/datasets/{namespace}/{name}/quality-contract/contracts/{contract_version_id}/activate")
def activate_dataset_quality_contract_version(
    request: Request,
    namespace: str,
    name: str,
    contract_version_id: str,
) -> DatasetQualityContractVersion:
    try:
        return foundry.datasets.activate_data_contract_version(
            f"{namespace}.{name}",
            contract_version_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/quality-contract/results")
def list_dataset_quality_results(
    request: Request,
    namespace: str,
    name: str,
    limit: int = Query(default=50, ge=1, le=100),
) -> DatasetQualityResultHistory:
    try:
        return foundry.datasets.list_quality_results(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/datasets/{namespace}/{name}/quality-contract/results/summary")
def get_dataset_quality_result_summary(
    request: Request,
    namespace: str,
    name: str,
    latest_limit: int = Query(default=5, ge=1, le=20),
) -> DatasetQualityResultSummary:
    try:
        return foundry.datasets.get_quality_result_summary(
            f"{namespace}.{name}",
            latest_limit=latest_limit,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/datasets/{namespace}/{name}/quality-contract/checks")
def create_dataset_quality_contract_check(
    request: Request,
    namespace: str,
    name: str,
    payload: DatasetQualityContractCheckCreateRequest,
) -> DatasetQualityContractCheckCreateResult:
    try:
        return foundry.datasets.create_quality_contract_check(
            f"{namespace}.{name}",
            config=payload.config,
            severity=payload.severity,
            enabled=payload.enabled,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.patch("/api/datasets/{namespace}/{name}/quality-contract/checks/{check_id}")
def update_dataset_quality_contract_check(
    request: Request,
    namespace: str,
    name: str,
    check_id: str,
    payload: DatasetQualityContractCheckUpdateRequest,
) -> DatasetQualityContractCheck:
    try:
        return foundry.datasets.update_quality_contract_check(
            f"{namespace}.{name}",
            check_id,
            config=payload.config,
            severity=payload.severity,
            enabled=payload.enabled,
            ctx=_ctx(request),
        )
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


@app.post("/api/aip/evals/run")
def run_aip_eval(request: Request, payload: AipEvalRunRequest) -> JsonObject:
    try:
        result = foundry.aip.run_eval(
            eval_run_id=payload.eval_run_id,
            suite_api_name=payload.suite_api_name,
            suite_version=payload.suite_version,
            suite_description=payload.suite_description,
            agent_version_id=payload.agent_version_id,
            candidate_release_channel=payload.candidate_release_channel,
            cases=tuple(_eval_case(case) for case in payload.cases),
            min_score=payload.min_score,
            required_axes=tuple(payload.required_axes),
            ctx=_ctx(request),
        )
        return cast(JsonObject, asdict(result))
    except AiEvalError as exc:
        raise _handle_error(_aip_eval_validation_error(exc), request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/aip/releases/promote")
def promote_aip_release(request: Request, payload: AipReleasePromotionRequest) -> JsonObject:
    try:
        result = foundry.aip.promote_agent_release(
            agent_version_id=payload.agent_version_id,
            target_release_channel=payload.target_release_channel,
            eval_run_id=payload.eval_run_id,
            policy_version=payload.policy_version,
            ctx=_ctx(request),
        )
        return cast(JsonObject, asdict(result))
    except AiEvalError as exc:
        raise _handle_error(_aip_eval_validation_error(exc), request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/sets")
def create_media_set(request: Request, payload: MediaSetCreateRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                foundry.media.create_media_set(
                    _ctx(request),
                    namespace=payload.namespace,
                    name=payload.name,
                    schema_type=payload.schema_type,
                    primary_format=payload.primary_format,
                    allowed_input_formats=tuple(payload.allowed_input_formats),
                    classification=payload.classification,
                    transaction_policy=payload.transaction_policy,
                    storage_profile=payload.storage_profile,
                    processing_profile=payload.processing_profile,
                    retention_policy_id=payload.retention_policy_id,
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/media/sets/{media_set_id}")
def get_media_set(request: Request, media_set_id: str) -> JsonObject:
    try:
        return cast(JsonObject, asdict(foundry.media.get_media_set(_ctx(request), media_set_id=media_set_id)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/sets/{media_set_id}/transactions")
def open_media_transaction(
    request: Request,
    media_set_id: str,
    payload: MediaOpenTransactionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        media_transaction_id = foundry.media.open_transaction(
            _ctx(request), media_set_id=media_set_id, idempotency_key=idempotency_key, mode=payload.mode
        )
        return {"mediaTransactionId": media_transaction_id}
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/sets/{media_set_id}/transactions/{media_transaction_id}/uploads")
def upload_media_file(
    request: Request,
    media_set_id: str,
    media_transaction_id: str,
    logical_path: str = Form(..., alias="logicalPath"),
    schema_type: str = Form(..., alias="schemaType"),
    format: str = Form(...),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    supplied_mime_type: str | None = Form(default=None, alias="suppliedMimeType"),
    security_envelope: str = Form(default="{}", alias="securityEnvelope"),
    probe_metadata: str | None = Form(default=None, alias="probeMetadata"),
) -> JsonObject:
    try:
        file.file.seek(0)
        staged = foundry.media.upload(
            _ctx(request),
            media_set_id=media_set_id,
            media_transaction_id=media_transaction_id,
            logical_path=logical_path,
            source=file.file,
            supplied_mime_type=supplied_mime_type or file.content_type or "application/octet-stream",
            schema_type=schema_type,
            format=format,
            security_envelope=_json_form_object(security_envelope, "securityEnvelope"),
            probe_metadata=_optional_json_form_object(probe_metadata, "probeMetadata"),
        )
        return cast(JsonObject, asdict(staged))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/transactions/{media_transaction_id}/commit")
def commit_media_transaction(request: Request, media_transaction_id: str) -> JsonObject:
    try:
        return cast(JsonObject, asdict(foundry.media.commit(_ctx(request), media_transaction_id=media_transaction_id)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/versions/{media_item_version_id}/process")
def process_media_version(request: Request, media_item_version_id: str, payload: MediaProcessRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                foundry.media.process(
                    _ctx(request), media_item_version_id=media_item_version_id, spec=_processor_spec(payload)
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/media/derivatives/{media_derivative_id}")
def get_media_derivative(request: Request, media_derivative_id: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(foundry.media.resolve_derivative(_ctx(request), media_derivative_id=media_derivative_id)),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/derivatives/{media_derivative_id}/index")
def index_media_derivative(
    request: Request, media_derivative_id: str, payload: MediaIndexDerivativeRequest
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                foundry.media.index_derivative(
                    _ctx(request), media_derivative_id=media_derivative_id, generation=payload.generation
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/content/search")
def search_media_content(request: Request, payload: MediaSearchRequest) -> list[JsonObject]:
    try:
        query = HybridContentQuery(
            tenant_id=_ctx(request).tenant_id,
            text=payload.text,
            top_k=payload.top_k,
            allowed_classifications=_optional_tuple(payload.allowed_classifications),
        )
        return [cast(JsonObject, asdict(hit)) for hit in foundry.media.search_content(_ctx(request), query=query)]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/visual/derivatives/{media_derivative_id}/index")
def index_media_visual_derivative(
    request: Request,
    media_derivative_id: str,
    payload: MediaIndexDerivativeRequest,
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                foundry.media.index_visual_derivative(
                    _ctx(request),
                    media_derivative_id=media_derivative_id,
                    generation=payload.generation,
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/visual/generations/promote")
def promote_media_visual_generation(request: Request, payload: MediaVisualPromoteRequest) -> JsonObject:
    try:
        foundry.media.promote_visual_generation(
            _ctx(request),
            expected_active=payload.expected_active,
            generation=payload.generation,
        )
        return {"status": "ok", "generation": payload.generation}
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/visual/search")
def search_media_visual(request: Request, payload: MediaVisualSearchRequest) -> list[JsonObject]:
    try:
        hits = foundry.media.search_visual(_ctx(request), text=payload.text, top_k=payload.top_k)
        return [cast(JsonObject, asdict(hit)) for hit in hits]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/media/processing-runs")
def list_media_processing_runs(
    request: Request,
    source_media_item_version_id: str | None = Query(default=None, alias="sourceMediaItemVersionId"),
    limit: int = Query(default=50),
) -> list[JsonObject]:
    try:
        runs = foundry.media.list_media_runs(
            _ctx(request), source_media_item_version_id=source_media_item_version_id, limit=limit
        )
        return [cast(JsonObject, asdict(run)) for run in runs]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/media/processing-runs/{media_processing_run_id}")
def get_media_processing_run(request: Request, media_processing_run_id: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(foundry.media.media_run_detail(_ctx(request), media_processing_run_id=media_processing_run_id)),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/media/references/bind")
def bind_media_reference(
    request: Request,
    payload: MediaBindReferenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                foundry.media.bind_reference(
                    _ctx(request),
                    holder_type=payload.holder_type,
                    holder_id=payload.holder_id,
                    property_name=payload.property_name,
                    media_item_version_id=payload.media_item_version_id,
                    idempotency_key=idempotency_key,
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/media/references/resolve")
def resolve_media_reference(
    request: Request,
    holder_type: str = Query(alias="holderType"),
    holder_id: str = Query(alias="holderId"),
    property_name: str = Query(alias="propertyName"),
    allowed_classifications: list[str] | None = MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY,
) -> JsonObject | None:
    try:
        resolved = foundry.media.resolve_bound_reference(
            _ctx(request),
            holder_type=holder_type,
            holder_id=holder_id,
            property_name=property_name,
            allowed_classifications=_optional_tuple(allowed_classifications),
        )
        return cast(JsonObject, asdict(resolved)) if resolved is not None else None
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


@app.post("/api/objects/{object_type}/subscriptions/stream")
def stream_object_subscription(
    request: Request,
    object_type: str,
    payload: ObjectSubscriptionRequest,
) -> StreamingResponse:
    try:
        events = foundry.objects.subscription_events(
            object_type,
            ctx=_ctx(request),
            filter_ast=payload.filter_ast,
            order_by=payload.order_by,
            properties=payload.properties,
            page_size=payload.page_size,
            last_seen_object_change_sequence=payload.last_seen_object_change_sequence,
            max_events=payload.max_events,
            poll_interval_seconds=payload.poll_interval_seconds,
        )
        first = cast(JsonObject, next(events))
        return StreamingResponse(
            _sse_json_events(_with_first_event(first, cast(Iterator[JsonObject], events))),
            media_type="text/event-stream",
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.websocket("/api/objects/{object_type}/subscriptions/ws")
async def websocket_object_subscription(websocket: WebSocket, object_type: str) -> None:
    if not _websocket_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ctx = _websocket_ctx(websocket)
    try:
        _check_websocket_subscription_rate(ctx, object_type)
        payload = await websocket.receive_json()
        request = ObjectSubscriptionRequest.model_validate(payload or {})
        events = foundry.objects.subscription_events(
            object_type,
            ctx=ctx,
            filter_ast=request.filter_ast,
            order_by=request.order_by,
            properties=request.properties,
            page_size=request.page_size,
            last_seen_object_change_sequence=request.last_seen_object_change_sequence,
            max_events=request.max_events,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        for event in events:
            await websocket.send_json(cast(JsonObject, event))
    except FoundryLiteError as exc:
        await websocket.send_json({"event": "error", "error": _websocket_error(exc, ctx.request_id)})
        await websocket.close(code=1008)
    except ValidationError as exc:
        await websocket.send_json({"event": "error", "error": {"code": "VALIDATION_ERROR", "message": str(exc)}})
        await websocket.close(code=1003)


@app.get("/api/auth/osdk/oauth/authorize")
def authorize_osdk_oauth(
    request: Request,
    client_id: str = Query(alias="clientId"),
    redirect_uri: str = Query(alias="redirectUri"),
    code_challenge: str = Query(alias="codeChallenge"),
    code_challenge_method: str = Query(default="S256", alias="codeChallengeMethod"),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.auth.osdk_oauth_authorize(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scopes=_scope_query(scope),
                state=state,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/auth/osdk/oauth/token")
def exchange_osdk_oauth_token(request: Request, payload: OsdkOAuthTokenRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.auth.osdk_oauth_token(
                client_id=payload.client_id,
                code=payload.code,
                redirect_uri=payload.redirect_uri,
                code_verifier=payload.code_verifier,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/auth/osdk/oauth/refresh")
def refresh_osdk_oauth_token(request: Request, payload: OsdkOAuthRefreshRequest) -> JsonObject:
    try:
        return cast(JsonObject, foundry.auth.osdk_oauth_refresh(refresh_token=payload.refresh_token, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/auth/osdk/oauth/revoke")
def revoke_osdk_oauth_token(request: Request, payload: OsdkOAuthRefreshRequest) -> JsonObject:
    try:
        return cast(JsonObject, foundry.auth.osdk_oauth_revoke(refresh_token=payload.refresh_token, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/developer-console/osdk-applications")
def create_osdk_application(
    request: Request,
    payload: OsdkApplicationCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.create_osdk_application(
                app_api_name=payload.app_api_name,
                display_name=payload.display_name,
                client_id=payload.client_id,
                resources=_resource_payloads(payload.resources),
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications")
def list_osdk_applications(request: Request) -> list[JsonObject]:
    try:
        return [cast(JsonObject, item) for item in foundry.developer_console.list_osdk_applications(ctx=_ctx(request))]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}")
def get_osdk_application(request: Request, app_id: str) -> JsonObject:
    try:
        return cast(JsonObject, foundry.developer_console.get_osdk_application(app_id, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.put("/api/developer-console/osdk-applications/{app_id}/resources")
def update_osdk_application_resources(
    request: Request,
    app_id: str,
    payload: OsdkApplicationResourcesUpdateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.update_osdk_application_resources(
                app_id,
                resources=_resource_payloads(payload.resources),
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/developer-console/osdk-applications/{app_id}/clients")
def create_osdk_application_client(
    request: Request,
    app_id: str,
    payload: OsdkApplicationClientRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.create_osdk_application_client(
                app_id,
                client_id=payload.client_id,
                redirect_uris=payload.redirect_uris,
                allowed_scopes=payload.allowed_scopes,
                access_token_ttl_seconds=payload.access_token_ttl_seconds,
                refresh_token_ttl_seconds=payload.refresh_token_ttl_seconds,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/clients")
def list_osdk_application_clients(request: Request, app_id: str) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in foundry.developer_console.list_osdk_application_clients(app_id, ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.put("/api/developer-console/osdk-applications/{app_id}/clients/{client_row_id}")
def update_osdk_application_client(
    request: Request,
    app_id: str,
    client_row_id: str,
    payload: OsdkApplicationClientRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.update_osdk_application_client(
                app_id,
                client_row_id,
                status=payload.status,
                redirect_uris=payload.redirect_uris,
                allowed_scopes=payload.allowed_scopes,
                access_token_ttl_seconds=payload.access_token_ttl_seconds,
                refresh_token_ttl_seconds=payload.refresh_token_ttl_seconds,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/developer-console/osdk-applications/{app_id}/clients/{client_row_id}/deactivate")
def deactivate_osdk_application_client(
    request: Request,
    app_id: str,
    client_row_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.deactivate_osdk_application_client(
                app_id, client_row_id, idempotency_key=idempotency_key, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/developer-console/osdk-applications/{app_id}/sdk-versions")
def create_osdk_sdk_version(
    request: Request,
    app_id: str,
    payload: OsdkSdkVersionCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.create_osdk_sdk_version(
                app_id,
                language=payload.language,
                package_name=payload.package_name,
                requested_bump=payload.requested_bump,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/sdk-versions")
def list_osdk_sdk_versions(request: Request, app_id: str) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in foundry.developer_console.list_osdk_sdk_versions(app_id, ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}")
def get_osdk_sdk_version(request: Request, app_id: str, version_id: str) -> JsonObject:
    try:
        return cast(JsonObject, foundry.developer_console.get_osdk_sdk_version(app_id, version_id, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/artifacts/{artifact_kind}")
def get_osdk_release_artifact(request: Request, app_id: str, version_id: str, artifact_kind: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.osdk_release_artifact(app_id, version_id, artifact_kind, ctx=_ctx(request)),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/channels/{channel}")
def promote_osdk_sdk_version(
    request: Request,
    app_id: str,
    version_id: str,
    channel: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.promote_osdk_sdk_version(
                app_id, version_id, channel=channel, idempotency_key=idempotency_key, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/sdk-release-channels")
def list_osdk_release_channels(
    request: Request, app_id: str, language: str | None = Query(default=None)
) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in foundry.developer_console.list_osdk_release_channels(
                app_id, language=language, ctx=_ctx(request)
            )
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/developer-console/osdk-applications/{app_id}/sdk-compatibility-windows")
def create_osdk_compatibility_window(
    request: Request,
    app_id: str,
    payload: OsdkSdkCompatibilityWindowCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.create_osdk_compatibility_window(
                app_id,
                from_version_id=payload.from_version_id,
                to_version_id=payload.to_version_id,
                supported_until=payload.supported_until,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/sdk-compatibility-windows")
def list_osdk_compatibility_windows(request: Request, app_id: str) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in foundry.developer_console.list_osdk_compatibility_windows(app_id, ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-applications/{app_id}/sdk-install-metadata")
def get_osdk_install_metadata(request: Request, app_id: str) -> JsonObject:
    try:
        return cast(JsonObject, foundry.developer_console.osdk_install_metadata(app_id, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post(
    "/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/artifacts/{artifact_kind}/download-token"
)
def create_osdk_artifact_download_token(
    request: Request,
    app_id: str,
    version_id: str,
    artifact_kind: str,
    payload: OsdkArtifactDownloadTokenRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.create_osdk_artifact_download_token(
                app_id,
                version_id,
                artifact_kind,
                ttl_seconds=payload.ttl_seconds,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/developer-console/osdk-release-artifacts/download/{download_token}")
def get_osdk_release_artifact_by_download_token(request: Request, download_token: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.developer_console.osdk_release_artifact_by_download_token(download_token, ctx=_ctx(request)),
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


@app.post("/api/insights/reviews/{review_id}/execute-action")
@app.post("/api/insights/reviews/{review_id}/execute-approved-action")
def execute_approved_action(
    request: Request,
    review_id: str,
    payload: ApprovalExecutionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_IDEMPOTENCY_KEY",
                "request_id": getattr(getattr(request, "state", None), "request_id", None),
            },
        )
    try:
        result = foundry.aip.execute_approved_action(
            review_id=review_id,
            expected_proposal_fingerprint=payload.expected_proposal_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
        return cast(JsonObject, asdict(result))
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
            access_scope=payload.access_scope,
            lifecycle=payload.lifecycle,
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


@app.post("/api/operations/observability/detect-and-record")
def record_observability_incidents(
    request: Request,
    payload: ObservabilityDetectRequest,
) -> ObservabilityStoredReport:
    try:
        return foundry.operations.record_observability_report(
            ctx=_ctx(request),
            configs=cast(list[ObservabilityDetectorConfig], payload.configs),
            observed_at=payload.observed_at,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/observability/incidents")
def list_observability_incidents(
    request: Request,
    status: str | None = None,
    limit: int = 50,
) -> list[StoredObservabilityIncident]:
    try:
        return foundry.operations.list_observability_incidents(ctx=_ctx(request), status=status, limit=limit)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/observability/incidents/{incident_id}/acknowledge")
def acknowledge_observability_incident(request: Request, incident_id: str) -> StoredObservabilityIncident:
    try:
        return foundry.operations.acknowledge_observability_incident(incident_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/observability/incidents/{incident_id}/resolve")
def resolve_observability_incident(
    request: Request,
    incident_id: str,
    payload: ObservabilityResolveRequest,
) -> StoredObservabilityIncident:
    try:
        return foundry.operations.resolve_observability_incident(incident_id, ctx=_ctx(request), reason=payload.reason)
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


@app.post("/api/operations/backup-restore/artifacts")
def create_backup_restore_artifact(
    request: Request,
    payload: BackupRestoreArtifactCreateRequest,
) -> BackupRestoreArtifactReceipt:
    try:
        return foundry.operations.create_backup_artifact(ctx=_ctx(request), backup_id=payload.backup_id)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/backup-restore/artifacts/restore")
def restore_from_backup_restore_artifact(
    request: Request,
    payload: BackupRestoreArtifactRestoreRequest,
) -> BackupRestoreArtifactRestoreReport:
    try:
        return foundry.operations.restore_from_backup_artifact(
            ctx=_ctx(request),
            artifact_ref=payload.artifact_ref,
            artifact_hash=payload.artifact_hash,
            restore_id=payload.restore_id,
            validation_id=payload.validation_id,
            should_run_post_restore_validation=payload.should_run_post_restore_validation,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/backup-restore/artifacts/restore/execute")
def execute_backup_restore_artifact_restore(
    request: Request,
    payload: BackupRestoreArtifactRestoreRequest,
) -> BackupRestoreArtifactRestoreReport:
    try:
        return foundry.operations.execute_backup_artifact_restore(
            ctx=_ctx(request),
            artifact_ref=payload.artifact_ref,
            artifact_hash=payload.artifact_hash,
            restore_id=payload.restore_id,
            validation_id=payload.validation_id,
            should_run_post_restore_validation=payload.should_run_post_restore_validation,
        )
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


@app.post("/api/operations/backup-restore/restore-mode/{restore_id}/post-restore-validation")
def run_backup_restore_post_restore_validation(
    request: Request,
    restore_id: str,
    payload: BackupRestorePostRestoreValidationRequest,
) -> BackupRestorePostRestoreValidationReport:
    try:
        return foundry.operations.run_post_restore_validation(
            restore_id,
            ctx=_ctx(request),
            validation_id=payload.validation_id,
        )
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


@app.post("/api/operations/outbox/publish")
def publish_pending_outbox(request: Request, payload: OutboxPublishRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            foundry.operations.publish_pending_outbox(
                ctx=_ctx(request),
                stream_name=payload.stream_name,
                limit=payload.limit,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/admin/overview")
def get_operations_admin_overview(request: Request) -> AdminReadinessOverview:
    try:
        return foundry.operations.admin_readiness_overview(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/admin/task-plan")
def get_operations_admin_task_plan(request: Request) -> AdminTaskPlan:
    try:
        return foundry.operations.admin_task_plan(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/recovery/overview")
def get_operations_recovery_overview(request: Request) -> BackupRestoreRecoveryOverview:
    try:
        return foundry.operations.recovery_overview(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/runs/{run_type}/{run_id}")
def get_operation_run_detail(request: Request, run_type: str, run_id: str) -> RuntimeRunDetail:
    try:
        return foundry.operations.run_detail(run_type, run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/runs/ai/{run_id}/prompt-artifacts/{artifact_id}")
def get_ai_prompt_artifact(request: Request, run_id: str, artifact_id: str) -> dict[str, object]:
    try:
        result = foundry.operations.read_prompt_artifact(run_id, artifact_id, ctx=_ctx(request))
        return _prompt_artifact_payload(result)
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


@app.post("/api/operations/workflows/{workflow_run_id}/cancel")
def cancel_product_workflow_run(
    request: Request,
    workflow_run_id: str,
    payload: ProductWorkflowCancelRequest,
) -> ProductWorkflowRun:
    try:
        return foundry.operations.cancel_product_workflow(workflow_run_id, reason=payload.reason, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources")
def list_sources(request: Request) -> list[JsonObject]:
    try:
        return foundry.sources.list_sources(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/templates")
def list_source_templates(request: Request) -> list[JsonObject]:
    try:
        return foundry.sources.list_templates(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/credentials")
def create_source_credential(
    request: Request,
    payload: SourceCredentialCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.create_credential(
            credential_name=payload.credential_name,
            display_name=payload.display_name,
            kind=payload.kind,
            auth_scheme=payload.auth_scheme,
            secret_value=payload.secret_value,
            secret_name=payload.secret_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/credentials")
def list_source_credentials(request: Request) -> list[JsonObject]:
    try:
        return foundry.sources.list_credentials(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/credentials/{credential_name}")
def get_source_credential(request: Request, credential_name: str) -> JsonObject:
    try:
        return foundry.sources.get_credential(credential_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/agents")
def register_source_agent(
    request: Request,
    payload: SourceAgentRegisterRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.register_agent(
            agent_id=payload.agent_id,
            display_name=payload.display_name,
            mode=payload.mode,
            capabilities=payload.capabilities,
            network_summary=payload.network_summary,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/agents")
def list_source_agents(request: Request) -> list[JsonObject]:
    try:
        return foundry.sources.list_agents(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/agents/{agent_id}/heartbeat")
def heartbeat_source_agent(request: Request, agent_id: str) -> JsonObject:
    try:
        return foundry.sources.heartbeat_agent(agent_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/network-policies")
def create_source_network_policy(
    request: Request,
    payload: SourceNetworkPolicyCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.create_network_policy(
            policy_name=payload.policy_name,
            display_name=payload.display_name,
            mode=payload.mode,
            agent_id=payload.agent_id,
            allowed_hosts=payload.allowed_hosts,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/network-policies")
def list_source_network_policies(request: Request) -> list[JsonObject]:
    try:
        return foundry.sources.list_network_policies(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/explore")
def explore_source(request: Request, payload: SourceExploreRequest) -> JsonObject:
    try:
        return foundry.sources.explore_source(
            source_name=payload.source_name,
            source_type=payload.source_type,
            request=payload.request,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/managed-syncs")
def create_source_managed_sync(
    request: Request,
    payload: SourceManagedSyncCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.create_managed_sync(
            sync_name=payload.sync_name,
            source_name=payload.source_name,
            display_name=payload.display_name,
            source_type=payload.source_type,
            capability=payload.capability,
            mode=payload.mode,
            target_dataset_ref=payload.target_dataset_ref,
            target_media_set_id=payload.target_media_set_id,
            schedule=payload.schedule,
            config_summary=payload.config_summary,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/managed-syncs")
def list_source_managed_syncs(request: Request) -> list[JsonObject]:
    try:
        return foundry.sources.list_managed_syncs(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/managed-syncs/{sync_name}")
def get_source_managed_sync(request: Request, sync_name: str) -> JsonObject:
    try:
        return foundry.sources.get_managed_sync(sync_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/managed-syncs/{sync_name}/runs/start")
def start_source_managed_sync_run(
    request: Request,
    sync_name: str,
    payload: SourceManagedSyncRunStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.start_managed_sync_run(
            sync_name,
            trigger_type=payload.trigger_type,
            batch_limit=payload.batch_limit,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/managed-syncs/{sync_name}/runs")
def list_source_managed_sync_runs(request: Request, sync_name: str) -> list[JsonObject]:
    try:
        return foundry.sources.list_managed_sync_runs(sync_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/managed-sync-runs/{run_id}")
def get_source_managed_sync_run(request: Request, run_id: str) -> JsonObject:
    try:
        return foundry.sources.get_managed_sync_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/scheduler/due")
def preview_source_scheduler_due(
    request: Request,
    max_runs: int = Query(default=50, ge=1, le=500, alias="maxRuns"),
) -> JsonObject:
    try:
        return foundry.sources.preview_due_managed_syncs(ctx=_ctx(request), max_runs=max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/scheduler/tick")
def run_source_scheduler_tick(request: Request, payload: SourceSchedulerTickRequest) -> JsonObject:
    try:
        return foundry.sources.run_due_managed_syncs(ctx=_ctx(request), max_runs=payload.max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/csv/uploads")
def upload_csv_source(
    request: Request,
    source_name: str = Form(..., alias="sourceName"),
    display_name: str = Form(..., alias="displayName"),
    dataset_ref: str = Form(..., alias="datasetRef"),
    sync_name: str | None = Form(default=None, alias="syncName"),
    primary_key: str = Form(default="[]", alias="primaryKey"),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        file.file.seek(0)
        return foundry.sources.upload_csv(
            source_name=source_name,
            display_name=display_name,
            dataset_ref=dataset_ref,
            file_name=file.filename or "upload.csv",
            source=file.file,
            sync_name=sync_name,
            primary_key=_json_form_string_list(primary_key, "primaryKey"),
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/batch-files/uploads")
def upload_batch_file_source(
    request: Request,
    manifest: str = Form(...),
    files: list[UploadFile] = SOURCE_BATCH_FILES,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        parsed = _source_batch_manifest(manifest)
        uploads = _source_batch_uploads(parsed, files)
        return foundry.sources.upload_batch_files(
            source_name=parsed.source_name,
            display_name=parsed.display_name,
            uploads=uploads,
            sync_name=parsed.sync_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/webhook-listeners")
def create_webhook_listener_source(
    request: Request,
    payload: SourceWebhookListenerCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        inbound_url = str(
            request.url_for(
                "ingest_source_webhook_listener_event",
                source_name=payload.source_name,
            )
        )
        return foundry.sources.create_webhook_listener(
            source_name=payload.source_name,
            display_name=payload.display_name,
            dataset_ref=payload.dataset_ref,
            connector_name=payload.connector_name,
            resource_name=payload.resource_name,
            signing_secret_ref=payload.signing_secret_ref,
            inbound_url=inbound_url,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/webhook-listeners/{source_name}")
def get_webhook_listener_source(request: Request, source_name: str) -> JsonObject:
    try:
        source = foundry.sources.get_source(source_name, ctx=_ctx(request))
        if source.get("kind") != "webhook_listener":
            raise ValidationFailed("source is not a webhook listener", details={"sourceName": source_name})
        return source
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/webhook-listeners/{source_name}/events")
async def ingest_source_webhook_listener_event(
    request: Request,
    source_name: str,
    signature: str = Header(alias="X-Foundry-Lite-Signature"),
    signature_timestamp: str = Header(alias="X-Foundry-Lite-Timestamp"),
    event_id: str | None = Header(default=None, alias="X-Foundry-Lite-Event-ID"),
    service_principal: str | None = Header(default=None, alias=WEBHOOK_SERVICE_PRINCIPAL_HEADER),
    service_tenant_id: str | None = Header(default=None, alias=WEBHOOK_SERVICE_TENANT_HEADER),
) -> JsonObject:
    try:
        raw_body = await _bounded_webhook_body(request)
        payload = _webhook_payload_request(raw_body)
        webhook_ctx = _webhook_request_context(
            request,
            service_principal=service_principal,
            service_tenant_id=service_tenant_id,
        )
        return foundry.sources.ingest_webhook_listener_event(
            source_name,
            payload=_webhook_payload(payload),
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            event_id=event_id,
            require_service_principal_signature=webhook_ctx.require_service_principal_signature,
            ctx=webhook_ctx.ctx,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/cdc/debezium")
def create_debezium_source(
    request: Request,
    payload: SourceDebeziumCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.create_debezium_source(
            source_name=payload.source_name,
            display_name=payload.display_name,
            dataset_ref=payload.dataset_ref,
            stream_name=payload.stream_name,
            topic=payload.topic,
            consumer_group=payload.consumer_group,
            secret_refs=payload.secret_refs,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/cdc/debezium/{source_name}/sync/start")
def start_debezium_source_sync(
    request: Request,
    source_name: str,
    payload: SourceDebeziumSyncStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.sources.start_debezium_sync(
            source_name,
            expected_config_fingerprint=payload.expected_config_fingerprint,
            after_offset=payload.after_offset,
            limit=payload.limit,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/sources/media/uploads")
def upload_media_source(
    request: Request,
    source_name: str = Form(..., alias="sourceName"),
    display_name: str = Form(..., alias="displayName"),
    media_set_id: str = Form(..., alias="mediaSetId"),
    logical_path: str = Form(..., alias="logicalPath"),
    schema_type: str = Form(..., alias="schemaType"),
    format: str = Form(...),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    supplied_mime_type: str | None = Form(default=None, alias="suppliedMimeType"),
    security_envelope: str = Form(default="{}", alias="securityEnvelope"),
    probe_metadata: str | None = Form(default=None, alias="probeMetadata"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        file.file.seek(0)
        return foundry.sources.upload_media(
            source_name=source_name,
            display_name=display_name,
            media_set_id=media_set_id,
            logical_path=logical_path,
            file_name=file.filename or logical_path,
            source=file.file,
            supplied_mime_type=supplied_mime_type or file.content_type or "application/octet-stream",
            schema_type=schema_type,
            format=format,
            security_envelope=_json_form_object(security_envelope, "securityEnvelope"),
            probe_metadata=_optional_json_form_object(probe_metadata, "probeMetadata"),
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/sources/{source_name}")
def get_source(request: Request, source_name: str) -> JsonObject:
    try:
        return foundry.sources.get_source(source_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/connectors/connections")
def create_connector_connection(
    request: Request,
    payload: RestConnectorConnectionCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.connectors.create_connection(
            connector_name=payload.connector_name,
            display_name=payload.display_name,
            base_url=payload.base_url,
            auth=_connector_auth_payload(payload.auth),
            rate_limit_per_minute=payload.rate_limit_per_minute,
            allow_private_network=payload.allow_private_network,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/connectors/connections")
def list_connector_connections(request: Request) -> list[JsonObject]:
    try:
        return foundry.connectors.list_connections(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/connectors/connections/{connector_name}")
def get_connector_connection(request: Request, connector_name: str) -> JsonObject:
    try:
        return foundry.connectors.get_connection(connector_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.patch("/api/connectors/connections/{connector_name}")
def update_connector_connection(
    request: Request,
    connector_name: str,
    payload: RestConnectorConnectionUpdateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.connectors.update_connection(
            connector_name,
            display_name=payload.display_name,
            base_url=payload.base_url,
            auth=_connector_auth_payload(payload.auth) if payload.auth is not None else None,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            allow_private_network=payload.allow_private_network,
            status=payload.status,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.put("/api/connectors/connections/{connector_name}/resources/{resource_name}")
def upsert_connector_resource(
    request: Request,
    connector_name: str,
    resource_name: str,
    payload: RestConnectorResourceUpsertRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return foundry.connectors.upsert_resource(
            connector_name,
            resource_name,
            dataset_ref=payload.dataset_ref,
            resource_path=payload.resource_path,
            pagination=_connector_pagination_payload(payload.pagination),
            schema_columns=payload.schema_columns,
            primary_key=payload.primary_key,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/connectors/connections/{connector_name}/resources/{resource_name}/test")
def test_connector_resource(request: Request, connector_name: str, resource_name: str) -> JsonObject:
    try:
        return foundry.connectors.test_resource(connector_name, resource_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/connectors/connections/{connector_name}/resources/{resource_name}/sync/start")
def start_connector_resource_sync(
    request: Request,
    connector_name: str,
    resource_name: str,
    payload: ConnectorResourceSyncStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ProductWorkflowRun:
    try:
        return foundry.connectors.start_resource_sync(
            connector_name,
            resource_name,
            sync_name=payload.sync_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.get("/api/operations/reconciliation/writebacks")
def list_action_writeback_reconciliation_queue(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> ActionWritebackQueueResult:
    try:
        return foundry.operations.list_unresolved_action_writebacks(status=status, limit=limit, ctx=_ctx(request))
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
                external_writeback_uri=payload.external_writeback_uri,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/operations/reconciliation/{writeback_id}/approve-recovery")
def approve_action_writeback_recovery(
    request: Request,
    writeback_id: str,
    payload: ActionWritebackRecoveryApprovalRequest,
) -> ActionWritebackRecoveryItem:
    try:
        return cast(
            ActionWritebackRecoveryItem,
            foundry.operations.approve_action_writeback_recovery(
                writeback_id,
                approval_id=payload.approval_id,
                reason=payload.reason,
                external_writeback_uri=payload.external_writeback_uri,
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


@app.post("/api/operations/maintenance/iceberg/{dataset_ref}/run")
def run_iceberg_maintenance(
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
            foundry.operations.run_iceberg_maintenance(
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


@app.post("/api/operations/dead-letter-records/{record_id}/retry-transform")
def retry_transform_dead_letter_record(
    request: Request,
    record_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> TransformRecordDlqRetryResult:
    try:
        return foundry.transforms.retry_dead_letter_record(
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


@app.post("/api/operations/index/{object_type}/ontology-reindex")
def replay_ontology_object_reindex(
    request: Request,
    object_type: str,
    payload: OntologyObjectReindexRequest,
) -> OntologyObjectReindexResult:
    try:
        return foundry.objects.reindex_ontology_migration(
            object_type,
            payload.reindex_key,
            ctx=_ctx(request),
        )
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


@app.post("/api/transforms/sql")
def register_sql_transform(request: Request, payload: TransformSqlRegisterRequest) -> TransformRow:
    try:
        return foundry.transforms.register_sql(
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


@app.get("/api/transforms/scheduler/due")
def preview_transform_scheduler_due(
    request: Request,
    max_runs: int = Query(default=50, ge=1, le=500, alias="maxRuns"),
) -> JsonObject:
    try:
        return foundry.transforms.preview_due(ctx=_ctx(request), max_runs=max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/transforms/scheduler/tick")
def run_transform_scheduler_tick(request: Request, payload: TransformSchedulerTickRequest) -> JsonObject:
    try:
        return foundry.transforms.run_due(ctx=_ctx(request), max_runs=payload.max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@app.post("/api/transforms/{api_name}/run")
def run_transform(request: Request, api_name: str) -> JsonObject:
    try:
        return _commit_result_payload(foundry.transforms.run(api_name, ctx=_ctx(request)))
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
    service_principal: str | None = Header(default=None, alias=WEBHOOK_SERVICE_PRINCIPAL_HEADER),
    service_tenant_id: str | None = Header(default=None, alias=WEBHOOK_SERVICE_TENANT_HEADER),
):
    try:
        raw_body = await _bounded_webhook_body(request)
        payload = _webhook_payload_request(raw_body)
        webhook_ctx = _webhook_request_context(
            request,
            service_principal=service_principal,
            service_tenant_id=service_tenant_id,
        )
        ctx = webhook_ctx.ctx
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
            require_service_principal_signature=webhook_ctx.require_service_principal_signature,
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


@app.post("/api/actions/{action_type}/validate")
def validate_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
) -> ActionValidationResponse:
    try:
        return foundry.actions.validate(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _eval_case(payload: AipEvalCaseRequest) -> EvalCaseInput:
    return EvalCaseInput(
        case_api_name=payload.case_api_name,
        axis=payload.axis,
        input_json=payload.input_json,
        expected_json=payload.expected_json,
        actual_json=payload.actual_json,
        rubric_json=payload.rubric_json,
        tags=tuple(payload.tags),
        sample_index=payload.sample_index,
        evaluator=payload.evaluator,
        weight=payload.weight,
    )


def _aip_eval_validation_error(exc: AiEvalError) -> ValidationFailed:
    return ValidationFailed("AIP eval request failed", details={"reason": exc.reason, "detail": exc.detail})


def _processor_spec(payload: MediaProcessRequest) -> ProcessorSpec:
    return ProcessorSpec(
        processor=payload.processor,
        processor_version=payload.processor_version,
        model=payload.model,
        model_version=payload.model_version,
        parameters=payload.parameters,
    )


def _json_form_object(value: str, field_name: str) -> JsonObject:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("form field must be a JSON object", details={"field": field_name}) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed("form field must be a JSON object", details={"field": field_name})
    return {str(key): item for key, item in parsed.items()}


def _optional_json_form_object(value: str | None, field_name: str) -> JsonObject | None:
    if value is None or not value.strip():
        return None
    return _json_form_object(value, field_name)


def _json_form_string_list(value: str, field_name: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("form field must be a JSON string array", details={"field": field_name}) from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValidationFailed("form field must be a JSON string array", details={"field": field_name})
    return [item for item in parsed if item]


def _source_batch_manifest(value: str) -> SourceBatchFileManifest:
    try:
        return SourceBatchFileManifest.model_validate_json(value)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _source_batch_uploads(manifest: SourceBatchFileManifest, files: list[UploadFile]) -> list[SourceUpload]:
    by_name = {file.filename or f"file-{index}": file for index, file in enumerate(files)}
    uploads: list[SourceUpload] = []
    for item in manifest.files:
        upload = by_name.get(item.file_name)
        if upload is None:
            raise ValidationFailed(
                "batch file manifest references a missing upload", details={"fileName": item.file_name}
            )
        upload.file.seek(0)
        uploads.append(SourceUpload(file_name=item.file_name, dataset_ref=item.dataset_ref, source=upload.file))
    return uploads


def _optional_tuple(values: list[str] | None) -> tuple[str, ...] | None:
    return tuple(values) if values is not None else None


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


def _webhook_request_context(
    request: Request,
    *,
    service_principal: str | None,
    service_tenant_id: str | None,
) -> WebhookRequestContext:
    principal_id = _webhook_identity_header(service_principal, WEBHOOK_SERVICE_PRINCIPAL_HEADER)
    tenant_id = _webhook_identity_header(service_tenant_id, WEBHOOK_SERVICE_TENANT_HEADER)
    if principal_id is None and tenant_id is None:
        return WebhookRequestContext(ctx=_ctx(request), require_service_principal_signature=False)
    if principal_id is None or tenant_id is None:
        raise ValidationFailed(
            "webhook service-principal auth requires tenant and principal headers",
            details={"required_headers": [WEBHOOK_SERVICE_TENANT_HEADER, WEBHOOK_SERVICE_PRINCIPAL_HEADER]},
        )
    ctx = RequestContext(
        tenant_id=tenant_id,
        actor_user_id=f"{WEBHOOK_SERVICE_ACTOR_PREFIX}{principal_id}",
        request_id=_request_id(request, f"api-{time.time_ns()}"),
        roles=(WEBHOOK_SERVICE_ROLE,),
    )
    return WebhookRequestContext(ctx=ctx, require_service_principal_signature=True)


def _webhook_identity_header(value: str | None, header_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "" or any(char.isspace() for char in normalized):
        raise ValidationFailed("invalid webhook service-principal header", details={"header": header_name})
    return normalized


def _connector_auth_payload(value: RestConnectorAuthRequest) -> JsonObject:
    return cast(JsonObject, value.model_dump(by_alias=True, exclude_none=True))


def _connector_pagination_payload(value: RestConnectorPaginationRequest) -> JsonObject:
    return cast(JsonObject, value.model_dump(by_alias=True))


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
