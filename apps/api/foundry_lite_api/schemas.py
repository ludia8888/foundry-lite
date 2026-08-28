"""Pydantic request models and shared request-parameter singletons for the API."""

from __future__ import annotations

from fastapi import File, Query
from pydantic import BaseModel, ConfigDict, Field

from foundry_lite_api.schema_types import JsonObject as JsonObject
from foundry_lite_api.schema_types import ValidationErrorPayload as ValidationErrorPayload
from foundry_lite_api.schemas_actions import (  # noqa: F401 - compatibility re-exports
    ActionApplyBatchRequest,
    ActionApplyRequest,
    ActionBatchTargetItemRequest,
    ActionEffectCancelRequest,
    ActionEffectReconcileRequest,
    ActionEffectReconciliationEvidenceRequest,
    ActionFunctionBatchItemRequest,
    ActionFunctionBatchRunRequest,
    ActionNotificationPolicyCreateRequest,
    ActionNotificationPolicyDisableRequest,
    ActionNotificationPolicyUpdateRequest,
    ActionNotificationRecipientRequest,
    ActionRunCancelRequest,
    ActionTargetRequest,
)
from foundry_lite_api.schemas_aip import (  # noqa: F401 - compatibility re-exports
    AipAgentRunRequest,
    AipBuilderContextSourceRequest,
    AipBuilderLogicBlockRequest,
    AipBuilderRunRequest,
    AipBuilderToolSpecRequest,
    AipBuilderValidateRequest,
    AipCitationNavigationResolveRequest,
    AipEvalCaseRequest,
    AipEvalRunRequest,
    AipFdeRunRequest,
    AipPilotActionRequest,
    AipPilotDomainBriefRequest,
    AipPilotFieldRequest,
    AipPilotGenerateRequest,
    AipPilotPlanRequest,
    AipPilotPolicyConditionRequest,
    AipPilotPolicyRequest,
    AipPilotRecordRequest,
    AipReleasePromotionRequest,
)
from foundry_lite_api.schemas_media import (  # noqa: F401 - compatibility re-exports
    MediaBindReferenceRequest,
    MediaContentPromoteRequest,
    MediaIndexDerivativeRequest,
    MediaOpenTransactionRequest,
    MediaProcessRequest,
    MediaSearchRequest,
    MediaSetCreateRequest,
    MediaVisualPromoteRequest,
    MediaVisualSearchRequest,
)
from foundry_lite_api.schemas_ontology import (  # noqa: F401 - compatibility re-exports
    FunctionExecuteRequest,
    OntologyApplyRequest,
    OntologyBranchActionTypeDeleteRequest,
    OntologyBranchActionTypeRequest,
    OntologyBranchCreateRequest,
    OntologyBranchProposeRequest,
    OntologyBranchRebaseRequest,
    OntologyBranchRebaseResolution,
    OntologyBranchUpdateRequest,
    OntologyProposalAssignRequest,
    OntologyProposalDecisionRequest,
    OntologyProposalExecuteRequest,
    OntologyProposalSubmitRequest,
    OntologyProposalUpdateRequest,
    OntologyProposalWithdrawRequest,
    OntologyRollbackRequest,
    OntologyValidateRequest,
)
from foundry_lite_api.schemas_osdk import (  # noqa: F401 - compatibility re-exports
    OsdkApplicationClientRequest,
    OsdkApplicationCreateRequest,
    OsdkApplicationResourceRequest,
    OsdkApplicationResourcesUpdateRequest,
    OsdkArtifactDownloadTokenRequest,
    OsdkClientSecretRotateRequest,
    OsdkMcpServerConfigureRequest,
    OsdkOAuthRefreshRequest,
    OsdkOAuthTokenRequest,
    OsdkSdkCompatibilityWindowCreateRequest,
    OsdkSdkVersionCreateRequest,
)
from foundry_lite_api.schemas_pipelines import (  # noqa: F401 - compatibility re-exports
    DeadLetterBulkRetryRequest,
    PipelineBranchCreateRequest,
    PipelineBranchProposeRequest,
    PipelineBranchRebaseRequest,
    PipelineDeployRequest,
    PipelineGraphUpdateRequest,
    PipelinePreviewLimitsRequest,
    PipelinePreviewNodeRequest,
    PipelinePreviewRunCreateRequest,
    PipelineProposalAssignRequest,
    PipelineProposalDecisionRequest,
    PipelineRunCancelRequest,
    PipelineRunStartRequest,
    PipelineScheduleSpecRequest,
    PipelineScheduleUpsertRequest,
    TransformSchedulerTickRequest,
    TransformSqlRegisterRequest,
)
from foundry_lite_api.schemas_sources import (  # noqa: F401 - compatibility re-exports
    ConnectorResourceSyncStartRequest,
    ConnectorSyncWorkflowStartRequest,
    RestConnectorAuthRequest,
    RestConnectorConnectionCreateRequest,
    RestConnectorConnectionUpdateRequest,
    RestConnectorPaginationRequest,
    RestConnectorResourceUpsertRequest,
    SourceAgentRegisterRequest,
    SourceBatchFileManifest,
    SourceBatchFileManifestItem,
    SourceConnectionTestRequest,
    SourceCredentialCreateRequest,
    SourceDebeziumCreateRequest,
    SourceDebeziumObjectIndexStartRequest,
    SourceDebeziumSyncStartRequest,
    SourceExploreRequest,
    SourceManagedStreamingSyncStateRequest,
    SourceManagedSyncCreateRequest,
    SourceManagedSyncRunStartRequest,
    SourceManagedSyncScheduleStateRequest,
    SourceManagedSyncScheduleUpdateRequest,
    SourceNetworkPolicyCreateRequest,
    SourceSchedulerTickRequest,
    SourceStatusUpdateRequest,
    SourceWebhookListenerCreateRequest,
)

MEDIA_UPLOAD_FILE = File(...)


SOURCE_BATCH_FILES = File(...)


MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY = Query(default=None, alias="allowedClassifications")


# DoS ceilings for the object query/subscription surface. Bounding these at the
# request boundary prevents an unbounded stream, a busy-loop poll, or a runaway
# page size from starving the process.
MAX_OBJECT_QUERY_LIMIT = 1_000
MAX_SUBSCRIPTION_EVENTS = 10_000
MIN_SUBSCRIPTION_POLL_INTERVAL_SECONDS = 0.1
MAX_SUBSCRIPTION_POLL_INTERVAL_SECONDS = 60.0


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
    definition: JsonObject | None = None
    ttl_seconds: int | None = Field(default=None, alias="ttlSeconds")


class ObjectQueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    order_by: list[dict[str, str]] | None = Field(default=None, alias="orderBy")
    limit: int = Field(default=50, ge=1, le=MAX_OBJECT_QUERY_LIMIT)
    cursor: str | None = None
    search_text: str | None = Field(default=None, alias="search")


class InterfaceQueryRequest(BaseModel):
    """Interface-scoped polymorphic query: single merged page, no cursor."""

    model_config = ConfigDict(populate_by_name=True)

    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    order_by: list[dict[str, str]] | None = Field(default=None, alias="orderBy")
    limit: int = 50


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


class DatasetAggregateMetricRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    function: str
    property: str | None = None
    name: str | None = None


class DatasetAggregateFilterRequest(BaseModel):
    column: str
    operator: str
    value: str


class DatasetAggregateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str = "latest"
    filter_conditions: list[DatasetAggregateFilterRequest] | None = Field(default=None, alias="filter")
    group_by: list[str] | None = Field(default=None, alias="groupBy")
    select: list[DatasetAggregateMetricRequest]


class ObjectSubscriptionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filter_ast: JsonObject | None = Field(default=None, alias="filter")
    order_by: list[dict[str, str]] | None = Field(default=None, alias="orderBy")
    properties: list[str] | None = None
    page_size: int = Field(default=50, ge=1, le=MAX_OBJECT_QUERY_LIMIT, alias="pageSize")
    last_seen_object_change_sequence: int | None = Field(default=None, alias="lastSeenObjectChangeSequence")
    # A finite ceiling (never None) so a subscription cannot stream forever.
    max_events: int = Field(default=MAX_SUBSCRIPTION_EVENTS, ge=1, le=MAX_SUBSCRIPTION_EVENTS, alias="maxEvents")
    # Floor prevents a 0/negative busy loop; ceiling keeps a stalled poll finite.
    poll_interval_seconds: float = Field(
        default=1.0,
        ge=MIN_SUBSCRIPTION_POLL_INTERVAL_SECONDS,
        le=MAX_SUBSCRIPTION_POLL_INTERVAL_SECONDS,
        alias="pollIntervalSeconds",
    )


class WebhookPayloadRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProductWorkflowCancelRequest(BaseModel):
    reason: str | None = None


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


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(alias="displayName")
    description: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ProjectGrantUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str


class ProjectFolderCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(alias="displayName")
    parent_folder_id: str | None = Field(default=None, alias="parentFolderId")
    metadata: JsonObject = Field(default_factory=dict)


class ProjectFolderMoveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    parent_folder_id: str | None = Field(default=None, alias="parentFolderId")


class ResourceRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource_type: str = Field(alias="resourceType")
    display_name: str = Field(alias="displayName")
    project_id: str | None = Field(default=None, alias="projectId")
    folder_id: str | None = Field(default=None, alias="folderId")
    source_surface: str = Field(alias="sourceSurface")
    source_ref: str = Field(alias="sourceRef")
    operations_path: str | None = Field(default=None, alias="operationsPath")
    metadata: JsonObject = Field(default_factory=dict)


class ResourceMoveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    folder_id: str | None = Field(default=None, alias="folderId")


class ResourceReconcileRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str | None = Field(default=None, alias="projectId")


class VirtualTableRegisterRequest(BaseModel):
    """Register a pointer to one external table.

    `config` is a mapping because the source decides what identifies a table: a SQL source uses
    schema plus name, object storage a path and format. It must carry `databaseUrlSecretRef` --
    a reference the vault resolves, never the connection URL itself.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    parent_rid: str = Field(alias="parentRid")
    config: dict[str, object]
    markings: list[str] = Field(default_factory=list)


class VirtualTableDiscoverRequest(BaseModel):
    """Ask a connection what tables it can reach."""

    model_config = ConfigDict(populate_by_name=True)

    config: dict[str, object]
    schema_names: list[str] = Field(default_factory=list, alias="schemaNames")


class ExternalTableRefRequest(BaseModel):
    """One table chosen from a discovery listing."""

    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    table_name: str = Field(alias="table")


class VirtualTableBulkRegisterRequest(BaseModel):
    """Register several pointers under one parent, mirroring the source's own hierarchy."""

    model_config = ConfigDict(populate_by_name=True)

    parent_rid: str = Field(alias="parentRid")
    config: dict[str, object]
    tables: list[ExternalTableRefRequest]
    markings: list[str] = Field(default_factory=list)


class VirtualTableAutoRegisterRequest(BaseModel):
    """One scheduled pass over a connection."""

    model_config = ConfigDict(populate_by_name=True)

    parent_rid: str = Field(alias="parentRid")
    config: dict[str, object]
    schema_names: list[str] = Field(default_factory=list, alias="schemaNames")
    markings: list[str] = Field(default_factory=list)
