"""Pydantic request models and shared request-parameter singletons for the API."""

from __future__ import annotations

from fastapi import File, Query
from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, object]


ValidationErrorPayload = dict[str, object]


MEDIA_UPLOAD_FILE = File(...)


SOURCE_BATCH_FILES = File(...)


MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY = Query(default=None, alias="allowedClassifications")


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


class ObjectAggregateMetricRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    function: str
    property: str | None = None
    name: str | None = None


class ObjectAggregateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    group_by: list[str] | None = Field(default=None, alias="groupBy")
    select: list[ObjectAggregateMetricRequest]


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


class OntologyApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yaml_text: str = Field(alias="yamlText")


class OntologyRollbackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_number: int = Field(alias="versionNumber", ge=1)


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
