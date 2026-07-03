from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from scripts import generate_sdk_ts as sdk
from scripts.sdk_generator.surface import contract_fingerprint


def _type_block(source: str, type_name: str) -> str:
    prefix = f"export type {type_name} = {{"
    start = source.index(prefix)
    end = source.index("};", start)
    return source[start : end + 2]


def _type_alias_until(source: str, type_name: str, next_type_name: str) -> str:
    start = source.index(f"export type {type_name} =")
    end = source.index(f"export type {next_type_name}", start)
    return source[start:end]


def _client_surface_payload(source: str) -> dict[str, object]:
    prefix = "export const SDK_CLIENT_SURFACE = "
    start = source.index(prefix) + len(prefix)
    end = source.index(";", start)
    payload = json.loads(source[start:end])
    assert isinstance(payload, dict)
    return payload


def test_sdk_generator_emits_typed_order_and_action_contract() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    generated = sdk.render_typescript(ontology)

    approve_params = _type_block(generated, "ApproveOrderParams")
    assert 'export type Order = FoundryLiteObject<"Order", OrderProperties>;' in generated
    assert "get(id: string, options?: { explain?: boolean }): Promise<Order>;" in generated
    assert "propertyLineage?: Array<Record<string, unknown>>" in generated
    assert "lateDataBadge?: Record<string, unknown> | null" in generated
    assert "query(payload?: ObjectQueryRequest): Promise<ObjectQueryResult<Order>>;" in generated
    assert "validate(payload: ApproveOrderValidateRequest): Promise<ActionValidationResponse>;" in generated
    assert "apply(payload: ApproveOrderApplyRequest): Promise<ActionApplyResponse>;" in generated
    assert "export type ActionValidationResponse = {" in generated
    assert "export type ActionEditSummary = {" in generated
    assert "export type ActionCacheRefreshHint = {" in generated
    assert "export type OsdkObjectType<" in generated
    assert "export type FoundryLiteOsdkClient = {" in generated
    assert "readonly objects: typeof $Objects;" in generated
    assert "readonly actions: typeof $Actions;" in generated
    assert "export const Order = {" in generated
    assert "  readonly titleProperty: string | null;" in generated
    assert 'titleProperty: "orderId",' in generated
    assert 'titleProperty: "name",' in generated
    assert "titleProperty: string | null;" in _type_block(generated, "OntologyCatalogObject")
    assert "export const $Objects = { Order, Customer } as const;" in generated
    assert "export const OrderCustomer = {" in generated
    assert "export const $Links = { OrderCustomer } as const;" in generated
    assert "export interface OsdkGeneratedLinkRegistry" in generated
    assert "customer: OsdkLinkSet<typeof Customer>;" in generated
    assert "export interface OsdkGeneratedObjectActionRegistry" in generated
    assert "approveOrder: OsdkBoundAction<typeof ApproveOrder>;" in generated
    assert "function decorateOsdkInstance<TObject extends OsdkObjectType>" in generated
    assert "function createOsdkLinkSet<TTarget extends OsdkObjectType>" in generated
    assert "function createOsdkBoundAction<TAction extends OsdkActionType>" in generated
    assert "export const ApproveOrder = {" in generated
    assert "export const $Actions = { ApproveOrder } as const;" in generated
    assert "fetchOne(primaryKey: string, options?: { explain?: boolean })" in generated
    assert "export type OsdkWhere<TObject extends OsdkObjectType>" in generated
    assert "export type OsdkOrderBy<TObject extends OsdkObjectType>" in generated
    assert "export type OsdkAggregateInput<TObject extends OsdkObjectType>" in generated
    assert "export type OsdkAggregateResult = {" in generated
    assert "where(filter: OsdkFilterInput<TObject>): OsdkObjectSet<TObject>;" in generated
    assert "orderBy(orderBy: OsdkOrderByInput<TObject>): OsdkObjectSet<TObject>;" in generated
    assert "aggregate(request: OsdkAggregateInput<TObject>): Promise<OsdkAggregateResult>;" in generated
    assert "function normalizeOsdkFilterInput<TObject extends OsdkObjectType>" in generated
    assert "function normalizeOsdkOrderBy<TObject extends OsdkObjectType>" in generated
    assert "function normalizeOsdkAggregateInput<TObject extends OsdkObjectType>" in generated
    assert "async function aggregateOsdkObjectSet<TObject extends OsdkObjectType>" in generated
    assert "INVALID_OSDK_FILTER_OPERATOR" in generated
    assert "INVALID_OSDK_AGGREGATE_GROUPBY_PROPERTY" in generated
    assert "$pageSize?: number;" in generated
    assert "$orderBy?: OsdkOrderBy<TObject>;" in generated
    assert "validateAction(" in generated
    assert "onCacheRefresh?: (hint: ActionCacheRefreshHint) => void" in generated
    assert "applyAction(" in generated
    assert "uploadAndCommit(" in generated
    assert "export function createFoundryLiteOsdkClient" in generated
    assert "export type DeadLetterRecord = {" in generated
    assert "operations: {" in generated
    assert "export type BackupRestorePreflightReport = {" in generated
    assert "export type BackupRestoreModeReport = {" in generated
    assert "system: {" in generated
    assert "health(): Promise<SystemHealth>;" in generated
    assert "datasets: {" in generated
    assert "export type Dataset = {" in generated
    assert "export type DatasetInspectionPayload = {" in generated
    assert "export type DatasetQualityContractCheck = {" in generated
    assert "list(): Promise<Dataset[]>;" in generated
    assert "versions(namespace: string, name: string): Promise<DatasetVersion[]>;" in generated
    assert (
        "preview(namespace: string, name: string, options?: DatasetPreviewOptions): Promise<TabularRow[]>;" in generated
    )
    assert "inspect(namespace: string, name: string, options?: DatasetInspectOptions)" in generated
    assert "qualityChecks: {" in generated
    assert "list(namespace: string, name: string): Promise<DatasetQualityContractCheckList>;" in generated
    assert "create(namespace: string, name: string, payload: DatasetQualityContractCheckCreateRequest)" in generated
    assert "export type DatasetQualityContractCheckUpdateRequest = {" in generated
    assert "update(namespace: string, name: string, checkId: string" in generated
    assert "export type DatasetQualityResultHistory = {" in generated
    assert "export type DatasetQualityResultSummary = {" in generated
    assert "qualityResults: {" in generated
    assert "list(namespace: string, name: string, options?: DatasetQualityResultHistoryOptions)" in generated
    assert "summary(namespace: string, name: string, options?: DatasetQualityResultSummaryOptions)" in generated
    assert "ontology: {" in generated
    assert "export type OntologyCatalog = {" in generated
    assert "catalog(): Promise<OntologyCatalog>;" in generated
    assert "validate(payload: OntologyValidateRequest): Promise<OntologyValidationResult>;" in generated
    assert "export type AipBuilderValidateRequest = {" in generated
    assert "export type AipBuilderRunRequest = AipBuilderValidateRequest & {" in generated
    assert "export type AipBuilderValidationResult = {" in generated
    assert "export type AipBuilderRunResult = {" in generated
    assert "export type AipAgentRunRequest = {" in generated
    assert "export type AipAgentRunResult = {" in generated
    assert "export type AipEvalRunRequest = {" in generated
    assert "export type AipReleasePromotionRequest = {" in generated
    assert "export type MediaUploadRequest = {" in generated
    assert "export type TransformSqlRegisterRequest = {" in generated
    assert "export type TransformSchedulerTickResult = {" in generated
    assert "aip: {" in generated
    assert "validate(payload: AipBuilderValidateRequest): Promise<AipBuilderValidationResult>;" in generated
    assert "run(payload: AipBuilderRunRequest): Promise<AipBuilderRunResult>;" in generated
    assert "run(payload: AipAgentRunRequest): Promise<AipAgentRunResult>;" in generated
    assert "run(payload: AipEvalRunRequest): Promise<AipEvalRunResult>;" in generated
    assert "promote(payload: AipReleasePromotionRequest): Promise<AipReleasePromotionResult>;" in generated
    assert "media: {" in generated
    assert "sets: {" in generated
    assert "upload(mediaSetId: string, mediaTransactionId: string, payload: MediaUploadRequest):" in generated
    assert "function mediaUploadFormData(payload: MediaUploadRequest): FormData" in generated
    assert "registerSql(payload: TransformSqlRegisterRequest): Promise<TransformRow>;" in generated
    assert "run(apiName: string): Promise<TransformRunResult>;" in generated
    assert "sdkClient().aip.builder.validate" not in generated
    assert "insights: {" in generated
    assert "export type InsightReviewPayload = {" in generated
    assert "list(filters?: InsightReviewListFilters): Promise<InsightReviewQueryResult>;" in generated
    assert "create(payload: InsightReviewCreateRequest, options: { idempotencyKey: string })" in generated
    assert "decide(reviewId: string, payload: InsightReviewDecisionRequest" in generated
    assert "generic: {" in generated
    assert "links(objectType: string, id: string, linkType: string): Promise<ObjectLinkPayload[]>;" in generated
    assert "objectSets: {" in generated
    assert "materializations: {" in generated
    assert "backupRestore: {" in generated
    assert "export class FoundryLiteApiError extends Error" in generated
    assert "request<T = unknown>(path: string, init?: FoundryLiteRequestInit): Promise<T>;" in generated
    assert "export function requestContextHeaders(" in generated
    assert "context: FoundryLiteRequestContext = {}" in generated
    assert "tokenProvider?: FoundryLiteTokenProvider;" in generated
    assert "export function createSessionTokenProvider(" in generated
    assert "export const $Ontology = {" in generated
    assert "export type ObjectApiName = keyof ObjectTypeRegistry;" in generated
    assert "export type ActionApiName = keyof ActionTypeRegistry;" in generated
    assert "export function getObjectType<TName extends ObjectApiName>" in generated
    assert "export function getActionType<TName extends ActionApiName>" in generated
    assert 'createRequestId(scope: string = "sdk"): string' in generated
    assert "normalizeFoundryLiteError(error: unknown): FoundryLiteApiError" in generated
    assert "isRetryableFoundryLiteError(error: unknown): boolean" in generated
    assert "classifyFoundryLiteError(error: unknown): FoundryLiteErrorClassification" in generated
    assert "retryWithBackoff<T>" in generated
    assert "collectCursorPages<TPage, TItem = unknown>" in generated
    assert "pollFoundryLiteOperation<TSnapshot>" in generated
    assert "createInFlightActionLock(): InFlightActionLock" in generated
    assert "export function groupAdminCapabilities(" in generated
    assert "export function adminCapabilityView(" in generated
    assert "export function adminOperationsBoard(" in generated
    assert "export function adminReadinessScreen(" in generated
    assert "export function adminTaskView(" in generated
    assert "export function adminTaskPlanScreen(" in generated
    assert "AdminCapabilityActionKind" in generated
    assert "export type AdminTaskView = {" in generated
    assert "export type AdminTaskPlanScreen = {" in generated
    assert "export type AdminOperationsBoard = {" in generated
    assert "export type AdminTaskPlan = {" in generated
    assert "taskPlan(): Promise<AdminTaskPlan>;" in generated
    assert "canStartFromBrowser: boolean;" in generated
    assert "operatorChecklist: string[];" in generated
    assert "blockingReason: string | null;" in generated
    assert "actionLockKey(actionName: string, objectId: string): string" in generated
    assert '"X-Request-ID": requestId' in generated
    assert "clientOptions.onResponse?.({" in generated
    assert "preflight(payload?: BackupRestorePreflightRequest): Promise<BackupRestorePreflightReport>;" in generated
    assert (
        "createArtifact(payload?: BackupRestoreArtifactCreateRequest): Promise<BackupRestoreArtifactReceipt>;"
        in generated
    )
    assert (
        "restoreArtifact(payload: BackupRestoreArtifactRestoreRequest): "
        "Promise<BackupRestoreArtifactRestoreReport>;" in generated
    )
    assert (
        "executeArtifactRestore(payload: BackupRestoreArtifactRestoreRequest): "
        "Promise<BackupRestoreArtifactRestoreReport>;" in generated
    )
    assert "restoredDatasetVersions: BackupRestoreArtifactRestoredVersion[];" in generated
    assert "startRestoreMode(payload?: BackupRestoreModeStartRequest): Promise<BackupRestoreModeReport>;" in generated
    assert "restoreModeStatus(restoreId: string): Promise<BackupRestoreModeReport>;" in generated
    assert "postRestoreValidation(restoreId: string, payload?: BackupRestorePostRestoreValidationRequest)" in generated
    assert "approveResume(restoreId: string, payload?: BackupRestoreResumeApprovalRequest):" in generated
    assert "recoveryOverview(): Promise<BackupRestoreRecoveryOverview>;" in generated
    assert "export type BackupRestoreArtifactReceipt = {" in generated
    assert "export type BackupRestoreArtifactRestoreReport = {" in generated
    assert "publishPending(payload?: OutboxPublishRequest): Promise<OutboxPublishBatchResult>;" in generated
    assert "export type BackupRestorePostRestoreValidationReport = {" in generated
    assert "export type BackupRestoreRecoveryOverview = {" in generated
    assert "export type OutboxPublishBatchResult = {" in generated
    assert "export type ObservabilityDetectorConfig = {" in generated
    assert "detect(payload: ObservabilityDetectRequest): Promise<ObservabilityReport>;" in generated
    assert "export type StoredObservabilityIncident = Omit<ObservabilityIncident, 'status'> & {" in generated
    assert "detectAndRecord(payload: ObservabilityDetectRequest): Promise<ObservabilityStoredReport>;" in generated
    assert "deadLetterRecords: {" in generated
    assert "deadLetterEvents: {" in generated
    assert "retry(id: string): Promise<RuntimeRetryResult>;" in generated
    assert "runs: {" in generated
    assert "list(filters?: RuntimeRunQueryFilters): Promise<RuntimeRunQueryResult>;" in generated
    assert "detail(runType: string, runId: string): Promise<RuntimeRunDetail>;" in generated
    assert "promptArtifact(runId: string, artifactId: string): Promise<PromptArtifactReadResult>;" in generated
    assert "export type PromptArtifactReadResult = {" in generated
    assert '  | "ai"' in generated
    assert "  aiRuns: RuntimeRow[];" in generated
    assert "  ai?: Record<string, unknown> | null;" in generated
    assert "planReadOnly(datasetRef: string, options?: IcebergMaintenancePlanOptions)" in generated
    assert "export type LineageEdge = {" in generated
    assert "lineage: {" in generated
    assert "get(resourceId: string): Promise<LineageEdge[]>;" in generated
    assert "index: {" in generated
    assert "replayObjectType(objectType: string): Promise<ObjectIndexRebuildResult>;" in generated
    assert "replayFailedRun(runId: string): Promise<ObjectIndexRebuildResult>;" in generated
    assert "export type OntologyObjectReindexResult = ObjectIndexRebuildResult & {" in generated
    assert "replayOntologyReindex(objectType: string, payload: OntologyObjectReindexRequest)" in generated
    assert (
        "run(datasetRef: string, options?: IcebergMaintenancePlanOptions): Promise<IcebergMaintenanceRun>;" in generated
    )
    assert "export type IcebergMaintenanceRun = {" in generated
    assert "transforms: {" in generated
    assert "retry(runId: string): Promise<TransformRetryResult>;" in generated
    assert "connectors: {" in generated
    assert "export type RestConnectorAuthInput =" in generated
    assert "tokenSecretRef: string" in generated
    assert "headerValueSecretRef: string" in generated
    auth_input = _type_alias_until(generated, "RestConnectorAuthInput", "RestConnectorPaginationInput")
    assert "token: string" not in auth_input
    assert "headerValue: string" not in auth_input
    assert "create(payload: RestConnectorConnectionCreateRequest" in generated
    assert "list(): Promise<ConnectorConnection[]>;" in generated
    assert "get(connectorName: string): Promise<ConnectorConnection>;" in generated
    assert "update(connectorName: string, payload: RestConnectorConnectionUpdateRequest" in generated
    assert "upsert(connectorName: string, resourceName: string" in generated
    assert "test(connectorName: string, resourceName: string): Promise<ConnectorResourceTestResult>;" in generated
    assert "startSync(connectorName: string, resourceName: string" in generated
    assert "reconciliation: {" in generated
    assert "list(filters?: ActionWritebackQueueFilters): Promise<ActionWritebackQueueResult>;" in generated
    assert "resolve(writebackId: string, payload: ActionWritebackReconciliationRequest):" in generated
    assert "workflows: {" in generated
    assert "startConnectorSync(payload: ConnectorSyncWorkflowStartRequest" in generated
    assert "get(workflowRunId: string): Promise<ProductWorkflowRun>;" in generated
    assert "cancel(workflowRunId: string, payload?: ProductWorkflowCancelRequest):" in generated
    assert "list(filters?: { status?: DeadLetterRecordStatus }): Promise<DeadLetterRecord[]>;" in generated
    assert "retry(id: string, options: { idempotencyKey: string }):" in generated
    assert "retryTransform(id: string, options: { idempotencyKey: string }):" in generated
    assert "export type TransformRecordDlqRetryResult = {" in generated
    assert "bulkRetry(ids: string[], options: { idempotencyKey: string }):" in generated
    assert "discard(id: string): Promise<DeadLetterRecordDiscardResult>;" in generated
    assert "export type DeadLetterRecordBulkRetryFailure = {" in generated
    assert 'status: "SUCCEEDED" | "PARTIAL_SUCCESS" | "FAILED";' in generated
    assert "failedItems: DeadLetterRecordBulkRetryFailure[];" in generated
    assert "replayDatasetVersionId: string | null;" in generated
    assert "rowCount: number | null;" in generated
    assert "reason: string;" in approve_params
    assert "any" not in approve_params
    approve_validate_request = _type_block(generated, "ApproveOrderValidateRequest")
    assert "expectedObjectVersion: number;" in approve_validate_request
    assert "idempotencyKey" not in approve_validate_request
    approve_apply_start = generated.index("export type ApproveOrderApplyRequest =")
    approve_apply_end = generated.index("export const Order", approve_apply_start)
    approve_apply_request = generated[approve_apply_start:approve_apply_end]
    assert "export type ApproveOrderApplyRequest = ApproveOrderValidateRequest & {" in approve_apply_request
    assert "idempotencyKey: string;" in approve_apply_request
    assert "idempotencyKey?: string;" not in approve_apply_request
    assert "expectedObjectVersion(object: { objectVersion: number }): number" in generated
    assert "idempotencyKey(actionName: string, objectId: string): string" in generated
    assert "function requireIdempotencyKey(value: string | undefined" in generated
    assert '"MISSING_IDEMPOTENCY_KEY"' in generated
    assert "payload.idempotencyKey ?? idempotencyKey(" not in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.create")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.assign")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.decide")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.deadLetterRecords.retry")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.deadLetterRecords.retryTransform")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.deadLetterRecords.bulkRetry")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "connectors.connections.create")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "connectors.connections.update")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "connectors.resources.upsert")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "connectors.resources.startSync")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.workflows.startConnectorSync")' in generated
    assert "export const SDK_CLIENT_SURFACE" in generated
    assert "export const SDK_PACKAGE_MANIFEST" in generated
    assert "export type FoundryLiteSdkDriftReport = {" in generated
    assert "sdkPackageManifest(): FoundryLiteSdkPackageManifest" in generated
    assert "sdkOntologyDriftReport(" in generated
    assert "assertFoundryLiteSdkFresh(" in generated
    assert "OSDK_SDK_REGENERATION_REQUIRED" in generated


def test_sdk_package_and_browser_outputs_share_client_surface() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    expected_surface = json.loads(sdk.render_client_surface_json(sdk.client_surface(ontology)))
    ts_surface = _client_surface_payload(sdk.DEFAULT_TS_OUTPUT.read_text(encoding="utf-8"))
    browser_surface = _client_surface_payload(sdk.DEFAULT_WEB_OUTPUT.read_text(encoding="utf-8"))

    assert ts_surface == expected_surface
    assert browser_surface == expected_surface
    assert ts_surface == browser_surface
    assert ts_surface["system"] == ["health"]
    assert ts_surface["datasets"] == {
        "_self": ["list", "versions", "preview", "inspect"],
        "qualityContracts": ["list", "create", "get", "activate"],
        "qualityChecks": ["list", "create", "update"],
        "qualityResults": ["list", "summary"],
    }
    assert ts_surface["connectors"] == {
        "connections": ["create", "list", "get", "update"],
        "resources": ["upsert", "test", "startSync"],
    }
    assert ts_surface["ontology"] == ["catalog", "validate"]
    assert ts_surface["aip"] == {
        "builder": ["validate", "run"],
        "agent": ["run"],
        "evals": ["run"],
        "releases": ["promote"],
    }
    assert ts_surface["media"] == {
        "sets": ["create", "get"],
        "transactions": ["open", "upload", "commit"],
        "versions": ["process"],
        "derivatives": ["get", "index"],
        "content": ["search"],
        "visual": ["indexDerivative", "promoteGeneration", "search"],
        "processingRuns": ["list", "get"],
        "references": ["bind", "resolve"],
    }
    assert ts_surface["insights"] == {"reviews": ["list", "create", "get", "assign", "decide", "executeApprovedAction"]}
    objects_surface = cast(dict[str, object], ts_surface["objects"])
    assert objects_surface["generic"] == ["get", "query", "links", "subscribe"]
    assert ts_surface["auth"] == {"osdkOAuth": ["authorize", "token", "refresh", "revoke"]}
    assert ts_surface["developerConsole"] == {
        "osdkApplications": [
            "create",
            "list",
            "get",
            "updateResources",
            "createClient",
            "listClients",
            "updateClient",
            "deactivateClient",
        ],
        "sdkVersions": [
            "create",
            "list",
            "get",
            "artifact",
            "promote",
            "channels",
            "createCompatibilityWindow",
            "compatibilityWindows",
            "installMetadata",
            "downloadToken",
            "artifactByDownloadToken",
        ],
    }
    assert ts_surface["objectSets"] == ["list", "create", "get"]
    assert ts_surface["actions"] == {"ApproveOrder": ["apply", "validate"]}
    assert ts_surface["materializations"] == ["run"]
    assert ts_surface["transforms"] == ["registerSql", "run", "previewDue", "tick"]
    assert ts_surface["operations"] == {
        "admin": ["overview", "taskPlan"],
        "backupRestore": [
            "preflight",
            "createArtifact",
            "restoreArtifact",
            "executeArtifactRestore",
            "startRestoreMode",
            "restoreModeStatus",
            "postRestoreValidation",
            "approveResume",
            "recoveryOverview",
        ],
        "deadLetterEvents": ["retry"],
        "deadLetterRecords": ["list", "get", "retry", "retryTransform", "bulkRetry", "discard"],
        "icebergMaintenance": ["planReadOnly", "plan", "run"],
        "index": ["replayObjectType", "replayFailedRun", "replayOntologyReindex"],
        "lineage": ["get"],
        "observability": ["detect", "detectAndRecord", "listIncidents", "acknowledgeIncident", "resolveIncident"],
        "outbox": ["publishPending"],
        "reconciliation": ["list", "resolve", "approveRecovery"],
        "runs": ["list", "detail", "promptArtifact"],
        "transforms": ["retry"],
        "workflows": ["startConnectorSync", "get", "cancel"],
    }
    assert ts_surface["helpers"] == [
        "adminCapabilityView",
        "adminOperationsBoard",
        "adminReadinessScreen",
        "adminTaskPlanScreen",
        "adminTaskView",
        "actionLockKey",
        "classifyFoundryLiteError",
        "collectCursorPages",
        "createInFlightActionLock",
        "createFoundryLiteClient",
        "createFoundryLiteOsdkClient",
        "createFoundryLiteOntologyIndex",
        "createSessionTokenProvider",
        "createRequestId",
        "expectedObjectVersion",
        "getActionType",
        "getObjectType",
        "groupAdminCapabilities",
        "idempotencyKey",
        "isRetryableFoundryLiteError",
        "normalizeFoundryLiteError",
        "pollFoundryLiteOperation",
        "requestContextHeaders",
        "retryWithBackoff",
        "sdkOntologyDriftReport",
        "sdkPackageManifest",
        "assertFoundryLiteSdkFresh",
        "streamFoundryLiteOperationEvents",
    ]


def test_browser_sdk_exposes_frontend_foundation_helpers() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    browser_sdk = sdk.render_web_javascript(ontology)

    expected_fragments = [
        "export class FoundryLiteApiError extends Error",
        'export function createRequestId(scope = "sdk")',
        "export function requestContextHeaders(context = {})",
        "export function createSessionTokenProvider(sessionProvider)",
        "export function normalizeFoundryLiteError(error)",
        "export function isRetryableFoundryLiteError(error)",
        "insights: {",
        "reviews: {",
        "aip: {",
        "builder: {",
        "validate: (payload) => request(`/api/aip/builder/validate`, {",
        "run: (payload) => request(`/api/aip/builder/run`, {",
        "agent: {",
        "run: (payload) => request(`/api/aip/agent/run`, {",
        "evals: {",
        "releases: {",
        "mediaUploadFormData(payload)",
        "export const Order = Object.freeze({",
        'titleProperty: "orderId",',
        "export const $Objects = Object.freeze({ Order, Customer });",
        "export const OrderCustomer = Object.freeze({",
        "export const $Links = Object.freeze({ OrderCustomer });",
        "export const ApproveOrder = Object.freeze({",
        "function createOsdkObjectSet(client, objectType, base = {})",
        "function decorateOsdkInstance(client, objectType, item)",
        "function createOsdkLinkSet(client, sourceObjectType, source, entry)",
        "function createOsdkBoundAction(client, actionType, source)",
        "function normalizeOsdkFilterInput(objectType, filter)",
        "function normalizeOsdkOrderBy(objectType, orderBy)",
        "function normalizeOsdkAggregateInput(objectType, request)",
        "async function aggregateOsdkObjectSet(client, objectType, base, request)",
        "orderBy(orderBy) {",
        "aggregate(request) {",
        "INVALID_OSDK_FILTER_OPERATOR",
        "INVALID_OSDK_AGGREGATE_GROUPBY_PROPERTY",
        "$pageSize",
        "function createOsdkActionInvoker(client, actionType)",
        "validateAction(payload) {",
        "/api/actions/ApproveOrder/validate",
        "onCacheRefresh",
        "uploadAndCommit(mediaSetId, payload, options)",
        "export function createFoundryLiteOsdkClient(client)",
        "export function createFoundryLiteOntologyIndex(catalog = null)",
        "export const SDK_PACKAGE_MANIFEST =",
        "export function sdkPackageManifest()",
        "export function sdkOntologyDriftReport(catalog = null)",
        "export function assertFoundryLiteSdkFresh(catalog = null)",
        "OSDK_SDK_REGENERATION_REQUIRED",
        "media: {",
        "upload: (mediaSetId, mediaTransactionId, payload) => request(",
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "media.transactions.open")',
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "media.references.bind")',
        "transforms: {",
        "registerSql: (payload) => request(`/api/transforms/sql`, {",
        "previewDue: (options = {}) => {",
        "request(`/api/transforms/scheduler/due${suffix}`)",
        "tick: (payload = {}) => request(`/api/transforms/scheduler/tick`, {",
        "export function classifyFoundryLiteError(error)",
        "export function actionLockKey(actionName, objectId)",
        "export function createInFlightActionLock()",
        "function requireIdempotencyKey(value, operationName)",
        'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.create")',
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "operations.deadLetterRecords.retry")',
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "operations.deadLetterRecords.retryTransform")',
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "operations.workflows.startConnectorSync")',
        "cancel: (workflowRunId, payload = {}) =>",
        "encodeURIComponent(workflowRunId)}/cancel",
        '"MISSING_IDEMPOTENCY_KEY"',
        "export function groupAdminCapabilities(overview)",
        "export function adminCapabilityView(capability)",
        "export function adminOperationsBoard(overview, plan)",
        "capability.canStartFromBrowser !== false",
        "operatorChecklist: capability.operatorChecklist ?? []",
        "export function adminReadinessScreen(overview)",
        "export function adminTaskView(task)",
        "export function adminTaskPlanScreen(plan)",
        "taskPlan: () => request(`/api/operations/admin/task-plan`)",
        "export async function retryWithBackoff(operation, options = {})",
        "export async function collectCursorPages(fetchPage, options = {})",
        "export async function* streamFoundryLiteOperationEvents(path, options = {})",
        'Accept: "text/event-stream"',
        "parseFoundryLiteEventFrames",
        "requestIdFactory",
        "onResponse",
        '"X-Request-ID": requestId',
        "await authorizationHeader(options)",
        "export const $Ontology = Object.freeze({",
        "export function getObjectType(apiName)",
        "export function getActionType(apiName)",
        "export function createFoundryLiteOntologyIndex(catalog = null)",
        "options.onResponse?.({",
        "ok: false",
        "errorCode: error.code",
        "return {",
        "    request,",
    ]

    for fragment in expected_fragments:
        assert fragment in browser_sdk

    react_helpers = (sdk.DEFAULT_TS_OUTPUT.parent / "react.ts").read_text(encoding="utf-8")
    assert "export function useFoundryLiteQuery" in react_helpers
    assert "export function useFoundryLiteSessionClient" in react_helpers
    assert "export type FoundryLiteSessionStatusView" in react_helpers
    assert "export function foundryLiteSessionStatus" in react_helpers
    assert "export function useFoundryLiteSessionStatus" in react_helpers
    assert "export type FoundryLiteScreenStatusView" in react_helpers
    assert "export function foundryLiteScreenStatus" in react_helpers
    assert "export function useFoundryLiteScreenStatus" in react_helpers
    assert "shouldShowSkeleton: boolean" in react_helpers
    assert "shouldShowInlineRefresh: boolean" in react_helpers
    assert "shouldRenderContent: boolean" in react_helpers
    assert "canRetry: boolean" in react_helpers
    assert "createSessionTokenProvider(options.sessionProvider)" in react_helpers
    assert "operationStreamOptions" in react_helpers
    assert "lastRequestId: lastResponse?.requestId ?? null" in react_helpers
    assert "isLastResponseRetryable" in react_helpers
    assert 'needsAuthentication: kind === "authentication_required"' in react_helpers
    assert 'isPermissionDenied: kind === "permission_denied"' in react_helpers
    assert 'canRetryLastRequest: kind === "retryable_error"' in react_helpers
    assert "foundryLiteSessionStatusKind(response, code, session.isLastResponseRetryable)" in react_helpers
    assert "createContext<FoundryLiteSessionClientState | null>(null)" in react_helpers
    assert "export function FoundryLiteProvider" in react_helpers
    assert "export function FoundryLiteClientProvider" in react_helpers
    assert "export function useFoundryLiteSession" in react_helpers
    assert "export function useFoundryLiteClient" in react_helpers
    assert "export function useFoundryLiteOsdkClient" in react_helpers
    assert "createFoundryLiteOsdkClient(client)" in react_helpers
    assert "createElement(FoundryLiteContext.Provider, { value }, children)" in react_helpers
    assert "useContext(FoundryLiteContext)" in react_helpers
    assert "export type FoundryLiteCursorPaginationState" in react_helpers
    assert "export function useFoundryLiteCursorPagination" in react_helpers
    assert "loadMore(): Promise<TPage | null>" in react_helpers
    assert "isLoadingMore" in react_helpers
    assert "nextCursorRef.current" in react_helpers
    assert "loadPageRef.current = loadPage" in react_helpers
    assert "retryWithBackoff(() => loadPageRef.current(cursor), retryRef.current)" in react_helpers
    assert "readFoundryLiteCursorItems(page, getItemsRef.current)" in react_helpers
    assert "export function useFoundryLiteMutation" in react_helpers
    assert "export type FoundryLiteConnectorOnboardingState" in react_helpers
    assert "export function useFoundryLiteConnectorOnboarding" in react_helpers
    assert "export function useFoundryLiteProvidedConnectorOnboarding" in react_helpers
    assert "createConnectorOnboardingRecipe(client)" in react_helpers
    assert "recipe.runFirstSync(input" in react_helpers
    assert "state.requestId ?? session.lastRequestId" in react_helpers
    assert "export type FoundryLiteDatasetExplorerView" in react_helpers
    assert "export function foundryLiteDatasetExplorerResourceId" in react_helpers
    assert "export function foundryLiteDatasetExplorerView" in react_helpers
    assert "export function useFoundryLiteDatasetExplorer" in react_helpers
    assert "export function useFoundryLiteProvidedDatasetExplorer" in react_helpers
    assert "loadFoundryLiteDatasetExplorerData(client, selection)" in react_helpers
    assert "client.datasets.versions(selection.namespace, selection.name)" in react_helpers
    assert "client.datasets.inspect(selection.namespace, selection.name, inspectOptions)" in react_helpers
    assert "client.datasets.preview(selection.namespace, selection.name" in react_helpers
    assert "client.datasets.qualityResults.summary(selection.namespace, selection.name)" in react_helpers
    assert "client.operations.lineage.get(lineageResourceId)" in react_helpers
    assert "previewRows" in react_helpers
    assert "qualitySummary" in react_helpers
    assert "export function foundryLiteObjectQueryKey" in react_helpers
    assert "export function useFoundryLiteObjectQuery" in react_helpers
    assert "export function useFoundryLiteGenericObjectQuery" in react_helpers
    assert "export function foundryLiteGenericObjectQueryRequest" in react_helpers
    assert "export function foundryLiteGenericObjectQueryKey" in react_helpers
    assert "client.objects.generic.query(objectApiName, request)" in react_helpers
    assert "export type FoundryLiteObjectActionWorkspaceState" in react_helpers
    assert "export function useFoundryLiteObjectActionWorkspace" in react_helpers
    assert "export function useFoundryLiteProvidedObjectActionWorkspace" in react_helpers
    assert "objectQuery: FoundryLiteGenericObjectQueryState" in react_helpers
    assert "selectedObject: GenericObject | null" in react_helpers
    assert "recommendedObject: GenericObject | null" in react_helpers
    assert "actionForm: FoundryLiteActionFormState" in react_helpers
    assert "const actionForm = useFoundryLiteActionForm(osdk, shell.explorer.selectedActionView" in react_helpers
    assert "stableOsdkFetchOptionsKey" in react_helpers
    assert "objectSet.fetchPage(fetchOptions)" in react_helpers
    assert "export type FoundryLiteActionFormView" in react_helpers
    assert "export type FoundryLiteActionParameterField" in react_helpers
    assert "export type FoundryLiteActionFormState" in react_helpers
    assert "export type FoundryLiteActionFormSubmitRequest" in react_helpers
    assert "export function foundryLiteActionFormView" in react_helpers
    assert "export function useFoundryLiteActionForm" in react_helpers
    assert "export function useFoundryLiteProvidedActionForm" in react_helpers
    assert "export function foundryLiteActionFormSubmitRequest" in react_helpers
    assert "parameterFields" in react_helpers
    assert "requiredParameterNames" in react_helpers
    assert "missingParameterNames" in react_helpers
    assert "canSubmitAction" in react_helpers
    assert "setParam(name: string, value: unknown): void" in react_helpers
    assert "resetForm(options?: FoundryLiteActionFormResetOptions): void" in react_helpers
    assert "submit(): Promise<OsdkActionResult<OsdkActionType> | null>" in react_helpers
    assert "const osdk = useFoundryLiteOsdkClient()" in react_helpers
    assert "selectedActionForm: FoundryLiteActionFormView | null" in react_helpers
    assert "recommendedActionForm: FoundryLiteActionFormView | null" in react_helpers
    assert "foundryLiteActionParameterFields(actionView.action.parameterSchema, params)" in react_helpers
    assert "requireIdempotencyKey" in react_helpers
    assert "export type FoundryLiteOntologyWorkspace" in react_helpers
    assert "export type FoundryLiteOntologyExplorerView" in react_helpers
    assert "export type FoundryLiteOntologyWorkspaceShellView" in react_helpers
    assert "export function foundryLiteOntologyObjectCard" in react_helpers
    assert "export function foundryLiteOntologyActionPaletteItem" in react_helpers
    assert "export function foundryLiteOntologyWorkspace" in react_helpers
    assert "export function foundryLiteOntologyExplorer" in react_helpers
    assert "export function foundryLiteOntologyWorkspaceShell" in react_helpers
    assert "index: FoundryLiteOntologyIndex" in react_helpers
    assert "createFoundryLiteOntologyIndex(catalog)" in react_helpers
    assert "export function useFoundryLiteOntologyCatalog" in react_helpers
    assert "export function useFoundryLiteOntologyExplorer" in react_helpers
    assert "export function useFoundryLiteOntologyWorkspaceShell" in react_helpers
    assert "export function useFoundryLiteProvidedOntologyExplorer" in react_helpers
    assert "export function useFoundryLiteProvidedOntologyWorkspaceShell" in react_helpers
    assert "objectSearchResults" in react_helpers
    assert "selectedActionPalette" in react_helpers
    assert "selectedActionPaletteItems" in react_helpers
    assert "canQuerySelectedObject" in react_helpers
    assert "canSubmitSelectedAction" in react_helpers
    assert "Regenerate the SDK before rendering this action as a static OSDK action button." in react_helpers
    assert "sdkRegenerationHint" in react_helpers
    assert "hasSelectedActionOutsideObject" in react_helpers
    assert "export type FoundryLiteScreenRecipesState" in react_helpers
    assert "export type FoundryLiteOperatorWorkspaceHomeState" in react_helpers
    assert "export type FoundryLiteOperatorWorkspaceShellState" in react_helpers
    assert "export type FoundryLiteOperatorAppShellState" in react_helpers
    assert "OperatorWorkspaceNavigationView" in react_helpers
    assert "OperatorWorkspaceShellView" in react_helpers
    assert "selectedAreaId?: OperatorWorkspaceAreaId" in react_helpers
    assert "navigation: OperatorWorkspaceNavigationView" in react_helpers
    assert "sessionStatus: FoundryLiteSessionStatusView" in react_helpers
    assert "screenStatus: FoundryLiteScreenStatusView" in react_helpers
    assert "canRenderWorkspace: boolean" in react_helpers
    assert "reloadWorkspace(): Promise<OperatorWorkspaceHomeView | null>" in react_helpers
    assert "export function useFoundryLiteScreenRecipes" in react_helpers
    assert "export function useFoundryLiteProvidedScreenRecipes" in react_helpers
    assert "export function foundryLiteOperatorWorkspaceShell" in react_helpers
    assert "export function useFoundryLiteOperatorWorkspaceHome" in react_helpers
    assert "export function useFoundryLiteProvidedOperatorWorkspaceHome" in react_helpers
    assert "export function useFoundryLiteOperatorWorkspaceShell" in react_helpers
    assert "export function useFoundryLiteProvidedOperatorWorkspaceShell" in react_helpers
    assert "export function useFoundryLiteProvidedOperatorAppShell" in react_helpers
    assert "createFoundryLiteScreenRecipes(client, query.data)" in react_helpers
    assert "createFoundryLiteScreenRecipes(client, shell.home.catalog)" in react_helpers
    assert "createOperatorWorkspaceRecipe(client).loadHome({ runFilters: options.runFilters })" in react_helpers
    assert 'options.key ?? ["operator-workspace", "shell"]' in react_helpers
    assert "operatorWorkspaceShell(home, selectedAreaId)" in react_helpers
    assert "operatorWorkspaceNavigation(home, options.selectedAreaId)" in react_helpers
    assert 'options.key ?? ["operator-workspace", "home"]' in react_helpers
    assert "shouldShowSignIn = screenStatus.needsAuthentication" in react_helpers
    assert "shouldShowPermissionDenied = screenStatus.isPermissionDenied" in react_helpers
    assert "const screenStatus = useMemo(" in react_helpers
    assert "foundryLiteScreenStatus({" in react_helpers
    assert "canRenderWorkspace: screenStatus.shouldRenderContent" in react_helpers
    assert "reloadWorkspace: shell.reload" in react_helpers
    assert "emptyFoundryLiteRuntimeRunQueryResult()" in react_helpers
    assert 'options.key ?? ["screen-recipes", "ontology", "catalog"]' in react_helpers
    assert "client.ontology.catalog()" in react_helpers
    assert "dynamicOnlyObjectViews" in react_helpers
    assert "dynamicOnlyActionViews" in react_helpers
    assert "actionsByTargetObjectApiName" in react_helpers
    assert "readGeneratedObjectType(objectType.apiName)" in react_helpers
    assert "readGeneratedActionType(action.apiName)" in react_helpers
    assert "export function foundryLiteActionPayloadObjectId" in react_helpers
    assert "export function foundryLiteActionSubmitLockKey" in react_helpers
    assert "export function useFoundryLiteActionSubmit" in react_helpers
    assert "export type FoundryLiteActionProposalView" in react_helpers
    assert "export function foundryLiteActionProposalView" in react_helpers
    assert "export function foundryLiteActionProposalApplyRequest" in react_helpers
    assert "export function useFoundryLiteActionProposalSubmit" in react_helpers
    assert "export function foundryLiteActionProposalSubmitLockKey" in react_helpers
    assert "readGeneratedActionType(actionApiName)" in react_helpers
    assert "canSubmitActionProposal" in react_helpers
    assert "missingFields" in react_helpers
    assert "INVALID_ACTION_PROPOSAL" in react_helpers
    assert "actionLockKey(actionType.apiName, objectId)" in react_helpers
    assert "actionInvoker.applyAction(payload" in react_helpers
    assert "export function foundryLiteMediaUploadLockKey" in react_helpers
    assert "export function useFoundryLiteMediaUpload" in react_helpers
    assert "export function useFoundryLiteProvidedMediaUpload" in react_helpers
    assert "client.media.transactions.open" in react_helpers
    assert "client.media.transactions.upload" in react_helpers
    assert "client.media.transactions.commit" in react_helpers
    assert "export function foundryLiteMediaProcessingLockKey" in react_helpers
    assert "export function useFoundryLiteMediaProcessing" in react_helpers
    assert "export function useFoundryLiteProvidedMediaProcessing" in react_helpers
    assert "client.media.versions.process" in react_helpers
    assert "client.media.derivatives.index" in react_helpers
    assert "export function useFoundryLiteMediaSearch" in react_helpers
    assert "export function useFoundryLiteProvidedMediaSearch" in react_helpers
    assert "stableMediaSearchPayloadKey" in react_helpers
    assert "export type FoundryLiteMediaPipelineState" in react_helpers
    assert "export function foundryLiteMediaPipelineLockKey" in react_helpers
    assert "export function useFoundryLiteMediaPipeline" in react_helpers
    assert "export function useFoundryLiteProvidedMediaPipeline" in react_helpers
    assert "servingTruthMediaItemVersionId" in react_helpers
    assert "phase: FoundryLiteMediaPipelinePhase" in react_helpers
    assert 'setPhase("searching")' in react_helpers
    assert "export function foundryLiteAipPhase" in react_helpers
    assert "export function foundryLiteAipRunView" in react_helpers
    assert "export async function loadFoundryLiteAipOperationsDetail" in react_helpers
    assert "export function useFoundryLiteAipAgentRun" in react_helpers
    assert "export function useFoundryLiteProvidedAipAgentRun" in react_helpers
    assert "export type FoundryLiteAipRunWithOperationsDetailState" in react_helpers
    assert "export function useFoundryLiteAipAgentRunWithOperationsDetail" in react_helpers
    assert "export function useFoundryLiteProvidedAipAgentRunWithOperationsDetail" in react_helpers
    assert "export function useFoundryLiteAipBuilderRun" in react_helpers
    assert "export function useFoundryLiteProvidedAipBuilderRun" in react_helpers
    assert "export function useFoundryLiteAipBuilderRunWithOperationsDetail" in react_helpers
    assert "export function useFoundryLiteProvidedAipBuilderRunWithOperationsDetail" in react_helpers
    assert "client.aip.agent.run(payload)" in react_helpers
    assert "client.aip.builder.run(payload)" in react_helpers
    assert "client.operations.runs.detail(result.operations.runType, result.operations.runId)" in react_helpers
    assert "operationsDetail" in react_helpers
    assert "hasOperationsDetail" in react_helpers
    assert "export type FoundryLiteInsightReviewQueueView" in react_helpers
    assert "export function foundryLiteInsightReviewQueueView" in react_helpers
    assert "export function useFoundryLiteInsightReviewQueue" in react_helpers
    assert "export function useFoundryLiteProvidedInsightReviewQueue" in react_helpers
    assert "export function useFoundryLiteInsightReviewCreate" in react_helpers
    assert "export function useFoundryLiteInsightReviewAssignment" in react_helpers
    assert "export function useFoundryLiteInsightReviewDecision" in react_helpers
    assert "client.insights.reviews.list(filters)" in react_helpers
    assert "client.insights.reviews.create(foundryLiteInsightReviewCreateRequest(payload)" in react_helpers
    assert "client.insights.reviews.assign(" in react_helpers
    assert "client.insights.reviews.decide(" in react_helpers
    assert "foundryLiteInsightReviewDecisionLockKey" in react_helpers
    assert "currentUserAssignedReviews" in react_helpers
    assert "selectedActionProposal" in react_helpers
    assert "export function foundryLiteSqlTransformLockKey" in react_helpers
    assert "export type FoundryLiteSqlTransformSubmitView" in react_helpers
    assert "export function foundryLiteSqlTransformSubmitView" in react_helpers
    assert "export function useFoundryLiteSqlTransformSubmit" in react_helpers
    assert "export function useFoundryLiteProvidedSqlTransformSubmit" in react_helpers
    assert "client.transforms.registerSql(payload.definition)" in react_helpers
    assert "client.transforms.run(payload.definition.apiName)" in react_helpers
    assert "outputDatasetVersionId" in react_helpers
    assert "outputDatasetRowCount" in react_helpers
    assert "outputManifestUri" in react_helpers
    assert "export function useFoundryLiteOperation" in react_helpers
    assert "export type FoundryLiteLongRunningJobPhase" in react_helpers
    assert "export function useFoundryLiteLongRunningJob" in react_helpers
    assert "export type FoundryLiteOperationTimelineItem" in react_helpers
    assert "export type FoundryLiteLiveOperationTimelineState" in react_helpers
    assert "export function useFoundryLiteLiveOperationTimeline" in react_helpers
    assert "export function useFoundryLiteProvidedLiveOperationTimeline" in react_helpers
    assert "timelineItems" in react_helpers
    assert "startStreamOnStart" in react_helpers
    assert "streamRequestId" in react_helpers
    assert "export function useFoundryLiteOperationEventStream" in react_helpers
    assert "export function useFoundryLiteProvidedOperationEventStream" in react_helpers
    assert "streamFoundryLiteOperationEvents<TEvent>" in react_helpers
    assert "session.operationStreamOptions.onOpen?.(metadata)" in react_helpers
    assert "latestEvent" in react_helpers
    assert "stop(): void" in react_helpers
    assert "pollFoundryLiteOperation(" in react_helpers
    assert "fetchSnapshotRef.current({ startResult: currentStartResult, attempt })" in react_helpers
    assert "foundryLiteLongRunningSnapshotSucceeded(" in react_helpers
    assert "readFoundryLiteLongRunningJobStatus(options.getStatus, startResult, snapshot)" in react_helpers
    assert "readFoundryLiteLongRunningJobRunId(options.getRunId, startResult, snapshot)" in react_helpers
    assert "Cannot poll a Foundry Lite long-running job before start() succeeds" in react_helpers
    assert "export function foundryLiteWorkflowPhase" in react_helpers
    assert "export function foundryLiteWorkflowRunView" in react_helpers
    assert "export function isFoundryLiteWorkflowTerminal" in react_helpers
    assert "export function useFoundryLiteWorkflowRun" in react_helpers
    assert "export type FoundryLiteOperationsRunRow" in react_helpers
    assert "export function foundryLiteOperationsRunPhase" in react_helpers
    assert "export function foundryLiteOperationsRunListView" in react_helpers
    assert "export function foundryLiteOperationsRunDetailView" in react_helpers
    assert "export function useFoundryLiteOperationsRunList" in react_helpers
    assert "export function useFoundryLiteProvidedOperationsRunList" in react_helpers
    assert "export function useFoundryLiteOperationsRunDetail" in react_helpers
    assert "export function useFoundryLitePromptArtifact" in react_helpers
    assert "export function useFoundryLiteOperationsInvestigation" in react_helpers
    assert "client.operations.runs.list(filters)" in react_helpers
    assert "client.operations.runs.detail(runType, runId)" in react_helpers
    assert "client.operations.runs.promptArtifact(runId, artifactId)" in react_helpers
    assert "failedRuns" in react_helpers
    assert "promptArtifactRefs" in react_helpers
    assert "export function foundryLiteRecordDlqQueueView" in react_helpers
    assert "export function useFoundryLiteRecordDlqQueue" in react_helpers
    assert "export function useFoundryLiteProvidedRecordDlqQueue" in react_helpers
    assert "export function useFoundryLiteRecordDlqControls" in react_helpers
    assert "export function foundryLiteRecordDlqRetryLockKey" in react_helpers
    assert "client.operations.deadLetterRecords.list" in react_helpers
    assert "client.operations.deadLetterRecords.retry" in react_helpers
    assert "client.operations.deadLetterRecords.bulkRetry" in react_helpers
    assert "client.operations.deadLetterRecords.discard" in react_helpers
    assert "export function foundryLiteWritebackReconciliationView" in react_helpers
    assert "export function useFoundryLiteWritebackReconciliationQueue" in react_helpers
    assert "export function useFoundryLiteProvidedWritebackReconciliationQueue" in react_helpers
    assert "export function useFoundryLiteWritebackResolve" in react_helpers
    assert "export function foundryLiteWritebackResolveLockKey" in react_helpers
    assert "client.operations.reconciliation.list(filters)" in react_helpers
    assert "client.operations.reconciliation.resolve(payload.writebackId, payload.payload)" in react_helpers
    assert "export function foundryLiteObservabilityReportView" in react_helpers
    assert "export function useFoundryLiteObservabilityDetect" in react_helpers
    assert "export function foundryLiteIcebergMaintenancePlanView" in react_helpers
    assert "export function useFoundryLiteIcebergMaintenancePlan" in react_helpers
    assert "export function useFoundryLiteMaintenanceControls" in react_helpers
    assert "export function foundryLiteIcebergMaintenancePlanLockKey" in react_helpers
    assert "export function foundryLiteIndexReplayObjectTypeLockKey" in react_helpers
    assert "export function foundryLiteTransformRetryLockKey" in react_helpers
    assert "client.operations.observability.detect(payload)" in react_helpers
    assert "client.operations.icebergMaintenance.planReadOnly(datasetRef, stablePlanOptions)" in react_helpers
    assert "client.operations.index.replayObjectType(payload.objectType)" in react_helpers
    assert "client.operations.transforms.retry(payload.runId)" in react_helpers
    assert "export function foundryLiteAdminCapabilityView" in react_helpers
    assert "export function groupFoundryLiteAdminCapabilities" in react_helpers
    assert "export type FoundryLiteAdminLaunchAction" in react_helpers
    assert "export function foundryLiteAdminLaunchAction" in react_helpers
    assert "export function foundryLiteAdminConsoleScreen" in react_helpers
    assert "export function foundryLiteAdminTaskPlanScreen" in react_helpers
    assert "export function foundryLiteAdminOperationsBoard" in react_helpers
    assert "export function foundryLiteAdminLaunchModel" in react_helpers
    assert "export function foundryLiteAdminLaunchpad" in react_helpers
    assert "export function foundryLiteAdminLaunchSections" in react_helpers
    assert "export type FoundryLiteAdminCommandCenterState" in react_helpers
    assert "export function foundryLiteAdminCommandCenter" in react_helpers
    assert "export type FoundryLiteAdminInternalOperationsWorkbenchState" in react_helpers
    assert "export function foundryLiteAdminInternalOperationsWorkbench" in react_helpers
    assert "export function useFoundryLiteAdminOverview" in react_helpers
    assert "export function useFoundryLiteAdminConsole" in react_helpers
    assert "export function useFoundryLiteAdminTaskPlan" in react_helpers
    assert "export function useFoundryLiteAdminOperationsBoard" in react_helpers
    assert "export function useFoundryLiteAdminLaunchModel" in react_helpers
    assert "export function useFoundryLiteProvidedAdminLaunchModel" in react_helpers
    assert "export function useFoundryLiteAdminLaunchpad" in react_helpers
    assert "export function useFoundryLiteProvidedAdminLaunchpad" in react_helpers
    assert "export function useFoundryLiteAdminCommandCenter" in react_helpers
    assert "export function useFoundryLiteProvidedAdminCommandCenter" in react_helpers
    assert "export function useFoundryLiteAdminInternalOperationsWorkbench" in react_helpers
    assert "export function useFoundryLiteProvidedAdminInternalOperationsWorkbench" in react_helpers
    assert "adminReadinessScreen(query.data)" in react_helpers
    assert "client.operations.admin.taskPlan()" in react_helpers
    assert "client.operations.admin.overview()" in react_helpers
    assert "return adminOperationsBoard(overview, plan)" in react_helpers
    assert "adminOperationsLaunchModel(foundryLiteAdminOperationsBoard(overview, plan))" in react_helpers
    assert "adminOperationsLaunchpad(foundryLiteAdminOperationsBoard(overview, plan))" in react_helpers
    assert "adminCommandCenter(model, selection)" in react_helpers
    assert "return adminInternalOperationsWorkbench(board, selection)" in react_helpers
    assert "commandCenter: AdminCommandCenterView" in react_helpers
    assert "internalOperationsWorkbench: AdminInternalOperationsWorkbenchView" in react_helpers
    assert "AdminCommandCenterView &" in react_helpers
    assert "...commandCenter" in react_helpers
    assert "...internalOperationsWorkbench" in react_helpers
    assert "return adminTaskPlanScreen(plan)" in react_helpers
    assert "futureBackendSurfaceTaskViews" in react_helpers
    assert "return groupAdminCapabilities(overview)" in react_helpers
    assert "label: foundryLiteAdminLaunchLabel(view.actionKind)" in react_helpers
    assert "command: capability.command ?? null" in react_helpers
    assert "executionSurface: view.executionSurface" in react_helpers
    assert "operatorChecklist: view.operatorChecklist" in react_helpers
    assert 'action.actionKind === "future"' in react_helpers
    assert "operatorCommandActions" in react_helpers
    assert "browserActions" in react_helpers
    assert "migrationCommands" in react_helpers
    assert "workerCommands" in react_helpers
    assert "bootstrapCommands" in react_helpers
    assert "operatorCommandCards" in react_helpers
    assert "migrationCommandCards" in react_helpers
    assert "workerCommandCards" in react_helpers
    assert "bootstrapCommandCards" in react_helpers
    assert "privilegedCommandCards" in react_helpers
    assert "visibleCommandCards" in react_helpers
    assert "selectedAreaId" in react_helpers
    assert "readiness" in react_helpers
    assert "futureActions" in react_helpers
    assert "export function foundryLiteRecoveryOverviewView" in react_helpers
    assert "export function foundryLiteRecoveryPhase" in react_helpers
    assert "export function useFoundryLiteRecoveryOverview" in react_helpers
    assert "client.operations.backupRestore.recoveryOverview()" in react_helpers
    assert "export function useFoundryLiteBackupRestorePreflight" in react_helpers
    assert "client.operations.backupRestore.preflight(payload)" in react_helpers
    assert "export function useFoundryLiteRestoreModeControls" in react_helpers
    assert "client.operations.backupRestore.startRestoreMode(payload)" in react_helpers
    assert "client.operations.backupRestore.postRestoreValidation" in react_helpers
    assert "client.operations.backupRestore.approveResume" in react_helpers
    assert "export function useFoundryLiteOutboxPublish" in react_helpers
    assert "export function foundryLiteOutboxPublishLockKey" in react_helpers
    assert "client.operations.outbox.publishPending(payload)" in react_helpers
    assert "autoStart" in react_helpers
    assert "terminalStatuses" in react_helpers
    assert "retryWithBackoff" in react_helpers
    assert "pollFoundryLiteOperation" in react_helpers
    assert "createInFlightActionLock" in react_helpers

    screen_recipes = (sdk.DEFAULT_TS_OUTPUT.parent / "screen-recipes.ts").read_text(encoding="utf-8")
    assert "export type OperatorWorkspaceHomeView" in screen_recipes
    assert "export type OperatorWorkspaceNavigationView" in screen_recipes
    assert "export type OperatorWorkspaceAreaSurface" in screen_recipes
    assert "export type OperatorWorkspaceShellView" in screen_recipes
    assert "areaSurfaces: OperatorWorkspaceAreaSurface[]" in screen_recipes
    assert "selectedSurface: OperatorWorkspaceAreaSurface" in screen_recipes
    assert "primaryQuickAction: OperatorWorkspaceQuickAction | null" in screen_recipes
    assert "export function operatorWorkspaceHomeView" in screen_recipes
    assert "export function operatorWorkspaceNavigation" in screen_recipes
    assert "export function operatorWorkspaceShell" in screen_recipes
    assert "export function createOperatorWorkspaceRecipe" in screen_recipes
    assert "export type AdminInternalOperationsWorkbenchView" in screen_recipes
    assert "export function adminInternalOperationsWorkbench" in screen_recipes
    assert "loadInternalOperationsWorkbench" in screen_recipes
    assert "operatorWorkspace: createOperatorWorkspaceRecipe(client, catalog ?? null)" in screen_recipes
    assert "const loadHome = async (" in screen_recipes
    assert "loadShell: async (" in screen_recipes
    assert "operatorWorkspaceShell(await loadHome(options), options.selectedAreaId)" in screen_recipes
    assert "client.datasets.list()" in screen_recipes
    assert "client.operations.runs.list(options.runFilters ?? {})" in screen_recipes
    assert "adminOperationsLaunchpad(adminBoard)" in screen_recipes
    assert "operatorWorkspaceRunRows(input.runs)" in screen_recipes
    assert "quickActions" in screen_recipes
    assert "attentionItems" in screen_recipes
    assert "recipeEntry: operatorWorkspaceRecipeEntry(item.id)" in screen_recipes
    assert "reactHook: operatorWorkspaceReactHook(item.id)" in screen_recipes
    assert "primarySdkSurface: operatorWorkspacePrimarySdkSurface(item.id)" in screen_recipes
    assert "useFoundryLiteProvidedAdminCommandCenter" in screen_recipes
    assert "investigate-failed-runs" in screen_recipes
    assert "recommendedAdminSectionId" in screen_recipes
    assert "export function createInsightReviewWorkspaceRecipe" in screen_recipes
    assert "export function createConnectorOnboardingRecipe" in screen_recipes
    assert "export type ConnectorOnboardingRecipeState" in screen_recipes
    assert "runFirstSync" in screen_recipes
    assert "client.connectors.connections.create" in screen_recipes
    assert "client.connectors.resources.upsert" in screen_recipes
    assert "client.connectors.resources.test" in screen_recipes
    assert "client.connectors.resources.startSync" in screen_recipes
    assert "connectorOnboarding: createConnectorOnboardingRecipe(client)" in screen_recipes
    assert "export function insightReviewQueueView" in screen_recipes
    assert "insightReviewWorkspace: createInsightReviewWorkspaceRecipe(client)" in screen_recipes
    assert "client.insights.reviews.list(filters)" in screen_recipes
    assert "client.insights.reviews.create(payload, options)" in screen_recipes
    assert "client.insights.reviews.assign(reviewId, payload, options)" in screen_recipes
    assert "client.insights.reviews.decide(reviewId, payload, options)" in screen_recipes
    assert "approveReview" in screen_recipes
    assert "rejectReview" in screen_recipes
    assert "export function createOperationsEvidenceRecipe" in screen_recipes
    assert "operationsEvidence: createOperationsEvidenceRecipe(client)" in screen_recipes
    assert "loadRunInvestigation" in screen_recipes
    assert "client.operations.runs.list(filters)" in screen_recipes
    assert "client.operations.runs.promptArtifact(runId, artifactId)" in screen_recipes
    assert "export function createRecordDlqOperationsRecipe" in screen_recipes
    assert "recordDlqOperations: createRecordDlqOperationsRecipe(client)" in screen_recipes
    assert "client.operations.deadLetterRecords.bulkRetry(ids, options)" in screen_recipes
    assert "export function createWritebackReconciliationRecipe" in screen_recipes
    assert "writebackReconciliation: createWritebackReconciliationRecipe(client)" in screen_recipes
    assert "client.operations.reconciliation.resolve(writebackId, payload)" in screen_recipes
    assert "client.operations.reconciliation.approveRecovery(writebackId, payload)" in screen_recipes
    assert "export function createMaintenanceOperationsRecipe" in screen_recipes
    assert "maintenanceOperations: createMaintenanceOperationsRecipe(client)" in screen_recipes
    assert "client.operations.observability.detect(payload)" in screen_recipes
    assert "client.operations.icebergMaintenance.planReadOnly(datasetRef, options)" in screen_recipes
    assert "client.operations.index.replayObjectType(objectType)" in screen_recipes
    assert "client.operations.transforms.retry(runId)" in screen_recipes
    assert "export type AdminOperationsLaunchpadModel" in screen_recipes
    assert "export type AdminOperatorCommandCard" in screen_recipes
    assert "export type AdminCommandCenterView" in screen_recipes
    assert "visibleCards: AdminOperatorCommandCard[]" in screen_recipes
    assert "selectedCard: AdminOperatorCommandCard | null" in screen_recipes
    assert "sectionCounts: Record<AdminCommandCardSectionId, number>" in screen_recipes
    assert "export function adminOperationsLaunchpad" in screen_recipes
    assert "export function adminOperatorCommandCards" in screen_recipes
    assert "export function adminCommandCenter" in screen_recipes
    assert "loadLaunchpad: async () => adminOperationsLaunchpad(await loadOperationsBoard())" in screen_recipes
    assert "loadOperatorCommandCards" in screen_recipes
    assert "copyLabel" in screen_recipes
    assert "canCopyCommand" in screen_recipes
    assert "visibleSectionCounts" in screen_recipes
    assert "selectedCommandId" in screen_recipes
    assert "includeBlocked" in screen_recipes
    assert "adminCommandCardCounts" in screen_recipes
    assert "migrationCommandCards" in screen_recipes
    assert "workerCommandCards" in screen_recipes
    assert "bootstrapCommandCards" in screen_recipes
    assert "riskLevel: AdminLaunchRiskLevel" in screen_recipes
    assert "visibleSections: sections.filter((section) => section.hasItems)" in screen_recipes
    assert "recommendedSection:" in screen_recipes

    package_json = json.loads((sdk.DEFAULT_TS_OUTPUT.parents[1] / "package.json").read_text(encoding="utf-8"))
    assert package_json["name"] == "@foundry-lite/sdk"
    assert package_json["scripts"]["typecheck"] == "tsc -p tsconfig.json --noEmit"
    assert package_json["exports"]["."] == "./src/client.ts"
    assert package_json["exports"]["./react"] == "./src/react.ts"
    assert package_json["exports"]["./screen-recipes"] == "./src/screen-recipes.ts"
    assert package_json["peerDependenciesMeta"]["react"]["optional"] is True


def test_sdk_generator_check_detects_api_name_drift(tmp_path: Path) -> None:
    ontology_path = tmp_path / "ontology.yaml"
    ts_output = tmp_path / "generated.ts"
    web_output = tmp_path / "generated-sdk.js"
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")

    ontology_path.write_text(ontology_text, encoding="utf-8")
    assert (
        sdk.write_or_check_outputs(
            ontology_path=ontology_path,
            ts_output=ts_output,
            web_output=web_output,
            should_check=False,
        )
        == 0
    )

    ontology_path.write_text(ontology_text.replace("apiName: Order", "apiName: PurchaseOrder", 1), encoding="utf-8")
    assert (
        sdk.write_or_check_outputs(
            ontology_path=ontology_path,
            ts_output=ts_output,
            web_output=web_output,
            should_check=True,
        )
        == 1
    )


def test_generated_sdk_files_match_active_ontology() -> None:
    assert (
        sdk.write_or_check_outputs(
            ontology_path=sdk.DEFAULT_ONTOLOGY,
            ts_output=sdk.DEFAULT_TS_OUTPUT,
            web_output=sdk.DEFAULT_WEB_OUTPUT,
            should_check=True,
        )
        == 0
    )


def test_sdk_generator_emits_interface_constants() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    generated = sdk.render_typescript(ontology)
    browser_sdk = sdk.render_web_javascript(ontology)

    assert "export type OsdkInterfaceType = {" in generated
    assert "export const Asset = {" in generated
    assert '  apiName: "Asset",' in generated
    assert '  properties: ["riskScore"],' in generated
    assert '  implementers: ["Order", "Customer"],' in generated
    assert "export const $Interfaces = { Asset } as const;" in generated
    assert "interfaces?: OntologyCatalogInterface[];" in _type_block(generated, "OntologyCatalog")
    assert "implements?: string[];" in _type_block(generated, "OntologyCatalogObject")
    assert "export const Asset = Object.freeze({" in browser_sdk
    assert 'properties: Object.freeze(["riskScore"]),' in browser_sdk
    assert 'implementers: Object.freeze(["Order", "Customer"]),' in browser_sdk
    assert "export const $Interfaces = Object.freeze({ Asset });" in browser_sdk


def test_interface_changes_the_ontology_contract_fingerprint(tmp_path: Path) -> None:
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")
    original_path = tmp_path / "original.yaml"
    original_path.write_text(ontology_text, encoding="utf-8")
    renamed_path = tmp_path / "renamed.yaml"
    renamed_path.write_text(
        ontology_text.replace("apiName: Asset", "apiName: Resource").replace(
            "implements: [Asset]", "implements: [Resource]"
        ),
        "utf-8",
    )

    original = contract_fingerprint(sdk.load_ontology(original_path))
    renamed = contract_fingerprint(sdk.load_ontology(renamed_path))

    assert original != renamed


def test_sdk_generator_rejects_dangling_or_non_conforming_implements(tmp_path: Path) -> None:
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")
    dangling_path = tmp_path / "dangling.yaml"
    dangling_path.write_text(ontology_text.replace("implements: [Asset]", "implements: [Ghost]", 1), "utf-8")
    with pytest.raises(ValueError, match="implements must reference a declared interface"):
        sdk.load_ontology(dangling_path)

    non_conforming_path = tmp_path / "non-conforming.yaml"
    non_conforming_path.write_text(ontology_text.replace("apiName: riskScore", "apiName: riskiness", 1), "utf-8")
    with pytest.raises(ValueError, match="missing riskiness"):
        sdk.load_ontology(non_conforming_path)


def test_title_property_changes_the_ontology_contract_fingerprint(tmp_path: Path) -> None:
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")
    original_path = tmp_path / "original.yaml"
    original_path.write_text(ontology_text, encoding="utf-8")
    retitled_path = tmp_path / "retitled.yaml"
    retitled_path.write_text(ontology_text.replace("titleProperty: orderId", "titleProperty: status", 1), "utf-8")

    original = contract_fingerprint(sdk.load_ontology(original_path))
    retitled = contract_fingerprint(sdk.load_ontology(retitled_path))

    assert original != retitled


def test_sdk_generator_rejects_dangling_title_property(tmp_path: Path) -> None:
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")
    ontology_path = tmp_path / "ontology.yaml"
    ontology_path.write_text(ontology_text.replace("titleProperty: orderId", "titleProperty: missing", 1), "utf-8")

    with pytest.raises(ValueError, match="titleProperty must reference a declared property"):
        sdk.load_ontology(ontology_path)


def test_sdk_generator_emits_function_constants() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    generated = sdk.render_typescript(ontology)
    browser_sdk = sdk.render_web_javascript(ontology)

    assert "export type OsdkFunctionType = {" in generated
    assert "export const orderRiskSummary = {" in generated
    assert '  apiName: "orderRiskSummary",' in generated
    assert '  inputs: [{"apiName": "objectId", "type": "string", "required": true}],' in generated
    assert '  output: "string",' in generated
    assert "export const $Functions = { orderRiskSummary } as const;" in generated
    assert 'functionApiNames: ["orderRiskSummary"],' in generated
    assert "functionTypes?: OntologyCatalogFunction[];" in _type_block(generated, "OntologyCatalog")
    assert "export const orderRiskSummary = Object.freeze({" in browser_sdk
    assert "export const $Functions = Object.freeze({ orderRiskSummary });" in browser_sdk
    assert 'functionApiNames: Object.freeze(["orderRiskSummary"]),' in browser_sdk


def test_function_signature_changes_the_ontology_contract_fingerprint(tmp_path: Path) -> None:
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")
    original_path = tmp_path / "original.yaml"
    original_path.write_text(ontology_text, encoding="utf-8")
    renamed_path = tmp_path / "renamed.yaml"
    renamed_path.write_text(ontology_text.replace("apiName: orderRiskSummary", "apiName: orderRiskDigest"), "utf-8")
    retyped_input_path = tmp_path / "retyped-input.yaml"
    retyped_input_path.write_text(
        ontology_text.replace(
            "- apiName: objectId\n        type: string", "- apiName: objectId\n        type: integer"
        ),
        "utf-8",
    )

    original = contract_fingerprint(sdk.load_ontology(original_path))
    renamed = contract_fingerprint(sdk.load_ontology(renamed_path))
    retyped = contract_fingerprint(sdk.load_ontology(retyped_input_path))

    assert original != renamed
    assert original != retyped


def test_sdk_generator_rejects_invalid_function_signatures(tmp_path: Path) -> None:
    ontology_text = sdk.DEFAULT_ONTOLOGY.read_text(encoding="utf-8")
    missing_output_path = tmp_path / "missing-output.yaml"
    missing_output_path.write_text(ontology_text.replace("    output:\n      type: string\n", ""), "utf-8")

    with pytest.raises(ValueError, match="function output must be an object"):
        sdk.load_ontology(missing_output_path)
