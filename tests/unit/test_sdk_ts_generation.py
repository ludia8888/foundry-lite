from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from scripts import generate_sdk_ts as sdk


def _type_block(source: str, type_name: str) -> str:
    prefix = f"export type {type_name} = {{"
    start = source.index(prefix)
    end = source.index("};", start)
    return source[start : end + 2]


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
    assert "apply(payload: ApproveOrderApplyRequest): Promise<ActionApplyResponse>;" in generated
    assert "export type DeadLetterRecord = {" in generated
    assert "operations: {" in generated
    assert "export type BackupRestorePreflightReport = {" in generated
    assert "export type BackupRestoreModeReport = {" in generated
    assert "system: {" in generated
    assert "health(): Promise<SystemHealth>;" in generated
    assert "datasets: {" in generated
    assert "export type Dataset = {" in generated
    assert "export type DatasetInspectionPayload = {" in generated
    assert "list(): Promise<Dataset[]>;" in generated
    assert "versions(namespace: string, name: string): Promise<DatasetVersion[]>;" in generated
    assert (
        "preview(namespace: string, name: string, options?: DatasetPreviewOptions): Promise<TabularRow[]>;" in generated
    )
    assert "inspect(namespace: string, name: string, options?: DatasetInspectOptions)" in generated
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
    assert "aip: {" in generated
    assert "validate(payload: AipBuilderValidateRequest): Promise<AipBuilderValidationResult>;" in generated
    assert "run(payload: AipBuilderRunRequest): Promise<AipBuilderRunResult>;" in generated
    assert "run(payload: AipAgentRunRequest): Promise<AipAgentRunResult>;" in generated
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
    assert 'createRequestId(scope: string = "sdk"): string' in generated
    assert "normalizeFoundryLiteError(error: unknown): FoundryLiteApiError" in generated
    assert "isRetryableFoundryLiteError(error: unknown): boolean" in generated
    assert "classifyFoundryLiteError(error: unknown): FoundryLiteErrorClassification" in generated
    assert "retryWithBackoff<T>" in generated
    assert "collectCursorPages<TPage, TItem = unknown>" in generated
    assert "createInFlightActionLock(): InFlightActionLock" in generated
    assert "actionLockKey(actionName: string, objectId: string): string" in generated
    assert '"X-Request-ID": requestId' in generated
    assert "clientOptions.onResponse?.({" in generated
    assert "preflight(payload?: BackupRestorePreflightRequest): Promise<BackupRestorePreflightReport>;" in generated
    assert "startRestoreMode(payload?: BackupRestoreModeStartRequest): Promise<BackupRestoreModeReport>;" in generated
    assert "restoreModeStatus(restoreId: string): Promise<BackupRestoreModeReport>;" in generated
    assert "approveResume(restoreId: string, payload?: BackupRestoreResumeApprovalRequest):" in generated
    assert "export type ObservabilityDetectorConfig = {" in generated
    assert "detect(payload: ObservabilityDetectRequest): Promise<ObservabilityReport>;" in generated
    assert "deadLetterRecords: {" in generated
    assert "deadLetterEvents: {" in generated
    assert "retry(id: string): Promise<RuntimeRetryResult>;" in generated
    assert "runs: {" in generated
    assert "list(filters?: RuntimeRunQueryFilters): Promise<RuntimeRunQueryResult>;" in generated
    assert "detail(runType: string, runId: string): Promise<RuntimeRunDetail>;" in generated
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
    assert "transforms: {" in generated
    assert "retry(runId: string): Promise<TransformRetryResult>;" in generated
    assert "reconciliation: {" in generated
    assert "resolve(writebackId: string, payload: ActionWritebackReconciliationRequest):" in generated
    assert "workflows: {" in generated
    assert "startConnectorSync(payload: ConnectorSyncWorkflowStartRequest" in generated
    assert "get(workflowRunId: string): Promise<ProductWorkflowRun>;" in generated
    assert "list(filters?: { status?: DeadLetterRecordStatus }): Promise<DeadLetterRecord[]>;" in generated
    assert "retry(id: string, options: { idempotencyKey: string }):" in generated
    assert "bulkRetry(ids: string[], options: { idempotencyKey: string }):" in generated
    assert "discard(id: string): Promise<DeadLetterRecordDiscardResult>;" in generated
    assert "export type DeadLetterRecordBulkRetryFailure = {" in generated
    assert 'status: "SUCCEEDED" | "PARTIAL_SUCCESS" | "FAILED";' in generated
    assert "failedItems: DeadLetterRecordBulkRetryFailure[];" in generated
    assert "replayDatasetVersionId: string | null;" in generated
    assert "rowCount: number | null;" in generated
    assert "reason: string;" in approve_params
    assert "any" not in approve_params
    approve_request = _type_block(generated, "ApproveOrderApplyRequest")
    assert "idempotencyKey: string;" in approve_request
    assert "idempotencyKey?: string;" not in approve_request
    assert "expectedObjectVersion(object: { objectVersion: number }): number" in generated
    assert "idempotencyKey(actionName: string, objectId: string): string" in generated
    assert "function requireIdempotencyKey(value: string | undefined" in generated
    assert '"MISSING_IDEMPOTENCY_KEY"' in generated
    assert "payload.idempotencyKey ?? idempotencyKey(" not in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.create")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.assign")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.decide")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.deadLetterRecords.retry")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.deadLetterRecords.bulkRetry")' in generated
    assert 'requireIdempotencyKey(options?.idempotencyKey, "operations.workflows.startConnectorSync")' in generated
    assert "export const SDK_CLIENT_SURFACE" in generated


def test_sdk_package_and_browser_outputs_share_client_surface() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    expected_surface = json.loads(sdk.render_client_surface_json(sdk.client_surface(ontology)))
    ts_surface = _client_surface_payload(sdk.DEFAULT_TS_OUTPUT.read_text(encoding="utf-8"))
    browser_surface = _client_surface_payload(sdk.DEFAULT_WEB_OUTPUT.read_text(encoding="utf-8"))

    assert ts_surface == expected_surface
    assert browser_surface == expected_surface
    assert ts_surface == browser_surface
    assert ts_surface["system"] == ["health"]
    assert ts_surface["datasets"] == ["list", "versions", "preview", "inspect"]
    assert ts_surface["ontology"] == ["catalog", "validate"]
    assert ts_surface["aip"] == {"builder": ["validate", "run"], "agent": ["run"]}
    assert ts_surface["insights"] == {"reviews": ["list", "create", "get", "assign", "decide"]}
    objects_surface = cast(dict[str, object], ts_surface["objects"])
    assert objects_surface["generic"] == ["get", "query", "links"]
    assert ts_surface["objectSets"] == ["list", "create", "get"]
    assert ts_surface["materializations"] == ["run"]
    assert ts_surface["operations"] == {
        "backupRestore": ["preflight", "startRestoreMode", "restoreModeStatus", "approveResume"],
        "deadLetterEvents": ["retry"],
        "deadLetterRecords": ["list", "get", "retry", "bulkRetry", "discard"],
        "icebergMaintenance": ["planReadOnly", "plan"],
        "index": ["replayObjectType", "replayFailedRun"],
        "lineage": ["get"],
        "observability": ["detect"],
        "reconciliation": ["resolve"],
        "runs": ["list", "detail"],
        "transforms": ["retry"],
        "workflows": ["startConnectorSync", "get"],
    }
    assert ts_surface["helpers"] == [
        "actionLockKey",
        "classifyFoundryLiteError",
        "collectCursorPages",
        "createInFlightActionLock",
        "createFoundryLiteClient",
        "createRequestId",
        "expectedObjectVersion",
        "idempotencyKey",
        "isRetryableFoundryLiteError",
        "normalizeFoundryLiteError",
        "requestContextHeaders",
        "retryWithBackoff",
    ]


def test_browser_sdk_exposes_frontend_foundation_helpers() -> None:
    ontology = sdk.load_ontology(sdk.DEFAULT_ONTOLOGY)
    browser_sdk = sdk.render_web_javascript(ontology)

    expected_fragments = [
        "export class FoundryLiteApiError extends Error",
        'export function createRequestId(scope = "sdk")',
        "export function requestContextHeaders(context = {})",
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
        "export function classifyFoundryLiteError(error)",
        "export function actionLockKey(actionName, objectId)",
        "export function createInFlightActionLock()",
        "function requireIdempotencyKey(value, operationName)",
        'requireIdempotencyKey(options?.idempotencyKey, "insights.reviews.create")',
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "operations.deadLetterRecords.retry")',
        'requireIdempotencyKey(requestOptions?.idempotencyKey, "operations.workflows.startConnectorSync")',
        '"MISSING_IDEMPOTENCY_KEY"',
        "export async function retryWithBackoff(operation, options = {})",
        "export async function collectCursorPages(fetchPage, options = {})",
        "requestIdFactory",
        "onResponse",
        '"X-Request-ID": requestId',
        "options.onResponse?.({",
        "ok: false",
        "errorCode: error.code",
        "return {",
        "    request,",
    ]

    for fragment in expected_fragments:
        assert fragment in browser_sdk


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
