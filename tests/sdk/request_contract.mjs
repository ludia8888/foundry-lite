import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const BASE_URL = "https://foundry.example";
const REQUEST_ID = "sdk-request-id";
const RESPONSE_REQUEST_ID = "api-response-id";
const REQUIRED_TEST_NAME = "test_sdk_request_contract_covers_all_frontend_surface_routes";
const REQUIRED_HELPER_TEST_NAME = "test_sdk_request_contract_covers_frontend_foundation_helpers";

const tempDir = mkdtempSync(join(tmpdir(), "foundry-lite-sdk-"));
const modulePath = join(tempDir, "generated-sdk.mjs");
copyFileSync(join(ROOT, "apps/web/generated-sdk.js"), modulePath);

const sdk = await import(pathToFileURL(modulePath).href);
const matrix = JSON.parse(readFileSync(join(ROOT, "docs/frontend-api-sdk-surface-matrix.json"), "utf8"));
const calls = [];
const telemetry = [];
const responseQueue = [];
const coveredSurfaceIds = new Set();
const coveredHelperIds = new Set();
const coveredMissingIdempotencySurfaceIds = new Set();

function responseHeaders(values) {
  const normalized = new Map(Object.entries(values).map(([key, value]) => [key.toLowerCase(), value]));
  return {
    get(name) {
      return normalized.get(name.toLowerCase()) ?? null;
    },
  };
}

function okResponse(body = {}) {
  return {
    ok: true,
    status: 200,
    headers: responseHeaders({ "X-Request-ID": RESPONSE_REQUEST_ID }),
    json: async () => body,
  };
}

function errorResponse(status, body, headers = {}) {
  return {
    ok: false,
    status,
    headers: responseHeaders(headers),
    json: async () => body,
  };
}

async function fetchImpl(url, init = {}) {
  calls.push({ url, init });
  return responseQueue.shift() ?? okResponse({ ok: true });
}

const client = sdk.createFoundryLiteClient({
  baseUrl: BASE_URL,
  token: "token-a",
  headers: { "X-Static-Header": "static-a" },
  context: {
    tenantId: "tenant-a",
    userId: "user-a",
    roles: ["operator", "admin"],
  },
  requestIdFactory: () => REQUEST_ID,
  fetchImpl,
  onResponse: (metadata) => telemetry.push(metadata),
});
coveredHelperIds.add("helpers.createFoundryLiteClient");

function assertBaseHeaders(headers, surfaceId) {
  assert.equal(headers["X-Tenant-ID"], "tenant-a", surfaceId);
  assert.equal(headers["X-User-ID"], "user-a", surfaceId);
  assert.equal(headers["X-Roles"], "operator,admin", surfaceId);
  assert.equal(headers["X-Request-ID"], REQUEST_ID, surfaceId);
  assert.equal(headers.Authorization, "Bearer token-a", surfaceId);
  assert.equal(headers["X-Static-Header"], "static-a", surfaceId);
}

function assertBody(call, expectedBody, surfaceId) {
  if (expectedBody === undefined) {
    assert.equal(call.init.body, undefined, surfaceId);
    return;
  }
  assert.deepEqual(JSON.parse(call.init.body), expectedBody, surfaceId);
}

function assertRequest(call, expectation) {
  const requestUrl = new URL(call.url);
  const headers = call.init.headers ?? {};
  assert.equal(requestUrl.origin, BASE_URL, expectation.surfaceId);
  assert.equal(`${requestUrl.pathname}${requestUrl.search}`, expectation.path, expectation.surfaceId);
  assert.equal(call.init.method ?? "GET", expectation.method ?? "GET", expectation.surfaceId);
  assertBaseHeaders(headers, expectation.surfaceId);
  for (const [name, value] of Object.entries(expectation.headers ?? {})) {
    assert.equal(headers[name], value, expectation.surfaceId);
  }
  assertBody(call, expectation.body, expectation.surfaceId);
}

async function expectSdkCall(surfaceId, invoke, expectation) {
  responseQueue.push(okResponse({ surfaceId }));
  const beforeCalls = calls.length;
  const beforeTelemetry = telemetry.length;
  await invoke();
  assert.equal(calls.length, beforeCalls + 1, surfaceId);
  assert.equal(telemetry.length, beforeTelemetry + 1, surfaceId);
  const call = calls.at(-1);
  assertRequest(call, { surfaceId, ...expectation });
  assert.deepEqual(
    telemetry.at(-1),
    {
      path: expectation.path,
      status: 200,
      ok: true,
      requestId: RESPONSE_REQUEST_ID,
    },
    surfaceId,
  );
  coveredSurfaceIds.add(surfaceId);
}

function assertMissingIdempotencyFailFast(surfaceId, invoke, operationName) {
  const beforeCalls = calls.length;
  assert.throws(
    invoke,
    (error) => {
      assert.equal(error.name, "FoundryLiteApiError", surfaceId);
      assert.equal(error.code, "MISSING_IDEMPOTENCY_KEY", surfaceId);
      assert.equal(error.retryable, false, surfaceId);
      assert.deepEqual(error.details, { operationName }, surfaceId);
      return true;
    },
    surfaceId,
  );
  assert.equal(calls.length, beforeCalls, surfaceId);
  coveredMissingIdempotencySurfaceIds.add(surfaceId);
}

await expectSdkCall("system.health", () => client.system.health(), {
  path: "/healthz",
});
await expectSdkCall("datasets.list", () => client.datasets.list(), {
  path: "/api/datasets",
});
await expectSdkCall("datasets.preview", () => client.datasets.preview("clean space", "orders/daily", { limit: 25 }), {
  path: "/api/datasets/clean%20space/orders%2Fdaily/preview?limit=25",
});
await expectSdkCall("datasets.versions", () => client.datasets.versions("clean", "orders"), {
  path: "/api/datasets/clean/orders/versions",
});
await expectSdkCall(
  "datasets.inspect",
  () => client.datasets.inspect("clean space", "orders/daily", { version: "version/1" }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/inspect?version=version%2F1",
  },
);
await expectSdkCall("ontology.catalog", () => client.ontology.catalog(), {
  path: "/api/ontology/catalog",
});
await expectSdkCall("ontology.validate", () => client.ontology.validate({ yaml: "objectTypes: []" }), {
  path: "/api/ontology/validate",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { yaml: "objectTypes: []" },
});
await expectSdkCall(
  "aip.builder.validate",
  () =>
    client.aip.builder.validate({
      agentVersionId: "agent-demo:v1",
      releaseChannel: "dev",
      modelAliasVersion: "support-assistant@1",
      promptVersionId: "prompt-v1",
      contextSources: [
        {
          sourceId: "ctx-orders",
          kind: "object_set",
          securityPartition: "tenant-a:orders",
          selectedProperties: ["status", "riskScore"],
          tokenBudget: 1200,
        },
      ],
      toolManifest: [
        {
          toolId: "read_order",
          version: "1",
          effect: "READ",
          confirmationPolicy: "NONE",
          status: "published",
          inputSchema: { type: "object" },
          outputSchema: { type: "object" },
        },
      ],
      logicBlocks: [
        { blockId: "load-order", kind: "CallFunction", inputs: { toolId: "read_order" } },
        { blockId: "answer", kind: "Output", inputs: { fromBlock: "load-order" }, dependsOn: ["load-order"] },
      ],
      evalAxes: ["Quality", "Security"],
      agentAllowedActions: ["ApproveOrder"],
      maxLogicBlocks: 8,
    }),
  {
    path: "/api/aip/builder/validate",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      agentVersionId: "agent-demo:v1",
      releaseChannel: "dev",
      modelAliasVersion: "support-assistant@1",
      promptVersionId: "prompt-v1",
      contextSources: [
        {
          sourceId: "ctx-orders",
          kind: "object_set",
          securityPartition: "tenant-a:orders",
          selectedProperties: ["status", "riskScore"],
          tokenBudget: 1200,
        },
      ],
      toolManifest: [
        {
          toolId: "read_order",
          version: "1",
          effect: "READ",
          confirmationPolicy: "NONE",
          status: "published",
          inputSchema: { type: "object" },
          outputSchema: { type: "object" },
        },
      ],
      logicBlocks: [
        { blockId: "load-order", kind: "CallFunction", inputs: { toolId: "read_order" } },
        { blockId: "answer", kind: "Output", inputs: { fromBlock: "load-order" }, dependsOn: ["load-order"] },
      ],
      evalAxes: ["Quality", "Security"],
      agentAllowedActions: ["ApproveOrder"],
      maxLogicBlocks: 8,
    },
  },
);
await expectSdkCall(
  "aip.builder.run",
  () =>
    client.aip.builder.run({
      logicRunId: "logic-web-1",
      agentVersionId: "agent-demo:v1",
      releaseChannel: "dev",
      modelAliasVersion: "support-assistant@1",
      promptVersionId: "prompt-v1",
      contextSources: [
        {
          sourceId: "ctx-orders",
          kind: "object_set",
          securityPartition: "tenant-a:orders",
          selectedProperties: ["status", "riskScore"],
          tokenBudget: 1200,
        },
      ],
      toolManifest: [
        {
          toolId: "read_order",
          version: "1",
          effect: "READ",
          confirmationPolicy: "NONE",
          status: "published",
          inputSchema: { type: "object" },
          outputSchema: { type: "object" },
        },
      ],
      logicBlocks: [
        { blockId: "load-order", kind: "CallFunction", inputs: { toolId: "read_order" } },
        { blockId: "answer", kind: "Output", inputs: { fromBlock: "load-order" }, dependsOn: ["load-order"] },
      ],
      evalAxes: ["Quality", "Security"],
      agentAllowedTools: ["read_order"],
      agentAllowedActions: ["ApproveOrder"],
      modelAllowedClassifications: ["public"],
      inputJson: { objectId: "O-1" },
      userMessage: "Run the draft",
      maxLogicBlocks: 8,
    }),
  {
    path: "/api/aip/builder/run",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      logicRunId: "logic-web-1",
      agentVersionId: "agent-demo:v1",
      releaseChannel: "dev",
      modelAliasVersion: "support-assistant@1",
      promptVersionId: "prompt-v1",
      contextSources: [
        {
          sourceId: "ctx-orders",
          kind: "object_set",
          securityPartition: "tenant-a:orders",
          selectedProperties: ["status", "riskScore"],
          tokenBudget: 1200,
        },
      ],
      toolManifest: [
        {
          toolId: "read_order",
          version: "1",
          effect: "READ",
          confirmationPolicy: "NONE",
          status: "published",
          inputSchema: { type: "object" },
          outputSchema: { type: "object" },
        },
      ],
      logicBlocks: [
        { blockId: "load-order", kind: "CallFunction", inputs: { toolId: "read_order" } },
        { blockId: "answer", kind: "Output", inputs: { fromBlock: "load-order" }, dependsOn: ["load-order"] },
      ],
      evalAxes: ["Quality", "Security"],
      agentAllowedTools: ["read_order"],
      agentAllowedActions: ["ApproveOrder"],
      modelAllowedClassifications: ["public"],
      inputJson: { objectId: "O-1" },
      userMessage: "Run the draft",
      maxLogicBlocks: 8,
    },
  },
);
await expectSdkCall(
  "insights.reviews.list",
  () => client.insights.reviews.list({ status: "pending", assigneeUserId: "ops/1", limit: 15 }),
  {
    path: "/api/insights/reviews?status=pending&assigneeUserId=ops%2F1&limit=15",
  },
);
await expectSdkCall(
  "insights.reviews.create",
  () =>
    client.insights.reviews.create(
      {
        claimId: "claim/1",
        claimText: "Supplier risk increased",
        evidenceObjectIds: ["evidence/1"],
        evidenceRefs: [{ evidenceId: "evidence/1", sourceObjectId: "O-1" }],
        priority: "high",
        assigneeUserId: "ops-user",
        actionProposal: {
          actionApiName: "ApproveOrder",
          targetObjectType: "Order",
          targetObjectId: "O-1",
          expectedObjectVersion: 3,
          params: { reason: "reviewed" },
        },
      },
      { idempotencyKey: "insight-create-key" },
    ),
  {
    path: "/api/insights/reviews",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "insight-create-key" },
    body: {
      claimId: "claim/1",
      claimText: "Supplier risk increased",
      evidenceObjectIds: ["evidence/1"],
      evidenceRefs: [{ evidenceId: "evidence/1", sourceObjectId: "O-1" }],
      priority: "high",
      assigneeUserId: "ops-user",
      actionProposal: {
        actionApiName: "ApproveOrder",
        targetObjectType: "Order",
        targetObjectId: "O-1",
        expectedObjectVersion: 3,
        params: { reason: "reviewed" },
      },
    },
  },
);
assertMissingIdempotencyFailFast(
  "insights.reviews.create",
  () =>
    client.insights.reviews.create({
      claimId: "claim/1",
      claimText: "Supplier risk increased",
      evidenceObjectIds: ["evidence/1"],
      evidenceRefs: [{ evidenceId: "evidence/1", sourceObjectId: "O-1" }],
    }),
  "insights.reviews.create",
);
await expectSdkCall("insights.reviews.get", () => client.insights.reviews.get("review/1"), {
  path: "/api/insights/reviews/review%2F1",
});
await expectSdkCall(
  "insights.reviews.assign",
  () =>
    client.insights.reviews.assign(
      "review/1",
      { assigneeUserId: "ops/2" },
      { idempotencyKey: "insight-assign-key" },
    ),
  {
    path: "/api/insights/reviews/review%2F1/assign",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "insight-assign-key" },
    body: { assigneeUserId: "ops/2" },
  },
);
assertMissingIdempotencyFailFast(
  "insights.reviews.assign",
  () => client.insights.reviews.assign("review/1", { assigneeUserId: "ops/2" }),
  "insights.reviews.assign",
);
await expectSdkCall(
  "insights.reviews.decide",
  () =>
    client.insights.reviews.decide(
      "review/1",
      { decision: "approved", comment: "Evidence checked" },
      { idempotencyKey: "insight-decision-key" },
    ),
  {
    path: "/api/insights/reviews/review%2F1/decision",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "insight-decision-key" },
    body: { decision: "approved", comment: "Evidence checked" },
  },
);
assertMissingIdempotencyFailFast(
  "insights.reviews.decide",
  () => client.insights.reviews.decide("review/1", { decision: "approved", comment: "Evidence checked" }),
  "insights.reviews.decide",
);
await expectSdkCall("objects.generic.get", () => client.objects.generic.get("Order Item", "order/1", { explain: true }), {
  path: "/api/objects/Order%20Item/order%2F1?explain=true",
});
await expectSdkCall("objects.generic.links", () => client.objects.generic.links("Order", "order/1", "customer/link"), {
  path: "/api/objects/Order/order%2F1/links/customer%2Flink",
});
await expectSdkCall("objects.generic.query", () => client.objects.generic.query("Order Item", { limit: 10, search: "rush" }), {
  path: "/api/objects/Order%20Item/query",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { limit: 10, search: "rush" },
});
await expectSdkCall("object-sets.list", () => client.objectSets.list({ objectType: "Order Item" }), {
  path: "/api/object-sets?objectType=Order%20Item",
});
await expectSdkCall(
  "object-sets.create",
  () =>
    client.objectSets.create({
      name: "Urgent orders",
      objectType: "Order",
      setType: "static",
      ids: ["order-1"],
      visibility: "private",
      ttlSeconds: 3600,
      filter: { eq: ["status", "OPEN"] },
    }),
  {
    path: "/api/object-sets",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      name: "Urgent orders",
      objectType: "Order",
      setType: "static",
      ids: ["order-1"],
      visibility: "private",
      ttlSeconds: 3600,
      filter: { eq: ["status", "OPEN"] },
    },
  },
);
await expectSdkCall("object-sets.get", () => client.objectSets.get("set/1"), {
  path: "/api/object-sets/set%2F1",
});
await expectSdkCall(
  "operations.runs.list",
  () =>
    client.operations.runs.list({
      runType: "transform",
      status: "failed",
      since: "2026-06-18T00:00:00Z",
      until: "2026-06-19T00:00:00Z",
      limit: 20,
      cursor: "cursor/1",
    }),
  {
    path:
      "/api/operations/runs?runType=transform&status=failed&since=2026-06-18T00%3A00%3A00Z" +
      "&until=2026-06-19T00%3A00%3A00Z&limit=20&cursor=cursor%2F1",
  },
);
await expectSdkCall("operations.observability.detect", () => client.operations.observability.detect({ configs: [] }), {
  path: "/api/operations/observability/detect",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { configs: [] },
});
await expectSdkCall("operations.backup-restore.preflight", () => client.operations.backupRestore.preflight({ backupId: "b1" }), {
  path: "/api/operations/backup-restore/preflight",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { backupId: "b1" },
});
await expectSdkCall(
  "operations.backup-restore.start",
  () => client.operations.backupRestore.startRestoreMode({ backupId: "b1", restoreId: "r1" }),
  {
    path: "/api/operations/backup-restore/restore-mode/start",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { backupId: "b1", restoreId: "r1" },
  },
);
await expectSdkCall("operations.backup-restore.status", () => client.operations.backupRestore.restoreModeStatus("restore/1"), {
  path: "/api/operations/backup-restore/restore-mode/restore%2F1",
});
await expectSdkCall(
  "operations.backup-restore.approve-resume",
  () => client.operations.backupRestore.approveResume("restore/1", { validationId: "validation-1" }),
  {
    path: "/api/operations/backup-restore/restore-mode/restore%2F1/approve-resume",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { validationId: "validation-1" },
  },
);
await expectSdkCall("operations.runs.detail", () => client.operations.runs.detail("index", "run/1"), {
  path: "/api/operations/runs/index/run%2F1",
});
await expectSdkCall("operations.lineage.get", () => client.operations.lineage.get("dataset-version/1"), {
  path: "/api/operations/lineage?resourceId=dataset-version%2F1",
});
await expectSdkCall(
  "operations.workflows.start-connector-sync",
  () =>
    client.operations.workflows.startConnectorSync(
      {
        datasetRef: "raw.orders",
        connectorName: "postgres",
        resourceName: "orders",
        syncName: "daily",
      },
      { idempotencyKey: "workflow-key" },
    ),
  {
    path: "/api/operations/workflows/connector-sync/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "workflow-key" },
    body: {
      datasetRef: "raw.orders",
      connectorName: "postgres",
      resourceName: "orders",
      syncName: "daily",
    },
  },
);
assertMissingIdempotencyFailFast(
  "operations.workflows.start-connector-sync",
  () =>
    client.operations.workflows.startConnectorSync({
      datasetRef: "raw.orders",
      connectorName: "postgres",
      resourceName: "orders",
    }),
  "operations.workflows.startConnectorSync",
);
await expectSdkCall("operations.workflows.get", () => client.operations.workflows.get("workflow/1"), {
  path: "/api/operations/workflows/workflow%2F1",
});
await expectSdkCall(
  "operations.reconciliation.resolve",
  () =>
    client.operations.reconciliation.resolve("writeback/1", {
      remoteStatus: "succeeded",
      remoteResourceId: "remote-1",
    }),
  {
    path: "/api/operations/reconciliation/writeback%2F1/resolve",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { remoteStatus: "succeeded", remoteResourceId: "remote-1" },
  },
);
await expectSdkCall(
  "operations.iceberg-maintenance.plan-get",
  () =>
    client.operations.icebergMaintenance.planReadOnly("raw.orders", {
      branch: "main",
      smallFileThresholdBytes: 128,
      fileCountThreshold: 5,
      readAmplificationThreshold: 2.5,
      retentionMinSnapshots: 3,
    }),
  {
    path:
      "/api/operations/maintenance/iceberg?datasetRef=raw.orders&branch=main&smallFileThresholdBytes=128" +
      "&fileCountThreshold=5&readAmplificationThreshold=2.5&retentionMinSnapshots=3",
  },
);
await expectSdkCall(
  "operations.iceberg-maintenance.plan-post",
  () =>
    client.operations.icebergMaintenance.plan("raw/orders", {
      branch: "dev",
      smallFileThresholdBytes: 256,
      fileCountThreshold: 6,
      readAmplificationThreshold: 3.5,
      retentionMinSnapshots: 4,
    }),
  {
    path:
      "/api/operations/maintenance/iceberg/raw%2Forders/plan?branch=dev&smallFileThresholdBytes=256" +
      "&fileCountThreshold=6&readAmplificationThreshold=3.5&retentionMinSnapshots=4",
    method: "POST",
  },
);
await expectSdkCall("operations.dead-letter-records.list", () => client.operations.deadLetterRecords.list({ status: "QUARANTINED" }), {
  path: "/api/operations/dead-letter-records?status=QUARANTINED",
});
await expectSdkCall("operations.dead-letter-records.bulk-retry", () => client.operations.deadLetterRecords.bulkRetry(["dlq/1"], { idempotencyKey: "bulk-key" }), {
  path: "/api/operations/dead-letter-records/bulk-retry",
  method: "POST",
  headers: { "Content-Type": "application/json", "Idempotency-Key": "bulk-key" },
  body: { ids: ["dlq/1"] },
});
assertMissingIdempotencyFailFast(
  "operations.dead-letter-records.bulk-retry",
  () => client.operations.deadLetterRecords.bulkRetry(["dlq/1"]),
  "operations.deadLetterRecords.bulkRetry",
);
await expectSdkCall("operations.dead-letter-records.get", () => client.operations.deadLetterRecords.get("dlq/1"), {
  path: "/api/operations/dead-letter-records/dlq%2F1",
});
await expectSdkCall("operations.dead-letter-records.retry", () => client.operations.deadLetterRecords.retry("dlq/1", { idempotencyKey: "retry-key" }), {
  path: "/api/operations/dead-letter-records/dlq%2F1/retry",
  method: "POST",
  headers: { "Idempotency-Key": "retry-key" },
});
assertMissingIdempotencyFailFast(
  "operations.dead-letter-records.retry",
  () => client.operations.deadLetterRecords.retry("dlq/1"),
  "operations.deadLetterRecords.retry",
);
await expectSdkCall("operations.dead-letter-records.discard", () => client.operations.deadLetterRecords.discard("dlq/1"), {
  path: "/api/operations/dead-letter-records/dlq%2F1/discard",
  method: "POST",
});
await expectSdkCall("operations.dead-letter-events.retry", () => client.operations.deadLetterEvents.retry("event/1"), {
  path: "/api/operations/dead-letter-events/event%2F1/retry",
  method: "POST",
});
await expectSdkCall("operations.index.replay-object-type", () => client.operations.index.replayObjectType("Order Item"), {
  path: "/api/operations/index/Order%20Item/replay",
  method: "POST",
});
await expectSdkCall("operations.index.replay-failed-run", () => client.operations.index.replayFailedRun("run/1"), {
  path: "/api/operations/runs/index/run%2F1/replay",
  method: "POST",
});
await expectSdkCall("operations.transforms.retry", () => client.operations.transforms.retry("transform/1"), {
  path: "/api/operations/runs/transform/transform%2F1/retry",
  method: "POST",
});
await expectSdkCall("materializations.run", () => client.materializations.run("Daily Orders"), {
  path: "/api/materializations/Daily%20Orders/run",
  method: "POST",
});
await expectSdkCall(
  "actions.generated.apply",
  () =>
    client.actions.ApproveOrder.apply({
      objectId: "order/1",
      expectedObjectVersion: 7,
      params: { reason: "approved" },
      idempotencyKey: "approve-key",
    }),
  {
    path: "/api/actions/ApproveOrder/apply",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "approve-key" },
    body: {
      target: { objectType: "Order", objectId: "order/1" },
      expectedObjectVersion: 7,
      params: { reason: "approved" },
    },
  },
);

assertMissingIdempotencyFailFast(
  "actions.generated.apply",
  () =>
    client.actions.ApproveOrder.apply({
      objectId: "order/1",
      expectedObjectVersion: 7,
      params: { reason: "approved" },
    }),
  "ApproveOrder",
);

await expectSdkCall("objects.Order.query.generated", () => client.objects.Order.query({ limit: 3 }), {
  path: "/api/objects/Order/query",
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Foundry-Lite-Object": "Order" },
  body: { limit: 3 },
});
coveredSurfaceIds.delete("objects.Order.query.generated");

responseQueue.push(
  errorResponse(
    503,
    {
      detail: {
        code: "TEMPORAL_UNAVAILABLE",
        message: "Temporal service unavailable",
        details: { workflowRunId: "workflow/err" },
        request_id: "durable-error-id",
      },
    },
    { "X-Request-ID": "transport-error-id" },
  ),
);
await assert.rejects(
  () => client.operations.workflows.get("workflow/err"),
  (error) => {
    assert.equal(error.name, "FoundryLiteApiError");
    assert.equal(error.status, 503);
    assert.equal(error.code, "TEMPORAL_UNAVAILABLE");
    assert.equal(error.requestId, "durable-error-id");
    assert.equal(error.retryable, true);
    assert.deepEqual(error.details, { workflowRunId: "workflow/err" });
    return true;
  },
);
assert.deepEqual(telemetry.at(-1), {
  path: "/api/operations/workflows/workflow%2Ferr",
  status: 503,
  ok: false,
  requestId: "durable-error-id",
  errorCode: "TEMPORAL_UNAVAILABLE",
  retryable: true,
});

assert.match(sdk.idempotencyKey("ApproveOrder", "order/1"), /^ApproveOrder-order\/1-\d+-[0-9a-f]+$/);
coveredHelperIds.add("helpers.idempotencyKey");

assert.ok(sdk.createRequestId("screen").startsWith("screen-"));
coveredHelperIds.add("helpers.createRequestId");

assert.deepEqual(sdk.requestContextHeaders({ tenantId: "tenant-b", userId: "user-b", roles: ["operator"] }), {
  "X-Tenant-ID": "tenant-b",
  "X-User-ID": "user-b",
  "X-Roles": "operator",
});
coveredHelperIds.add("helpers.requestContextHeaders");

const normalizedNetworkError = sdk.normalizeFoundryLiteError(new Error("offline"));
assert.equal(normalizedNetworkError.code, "NETWORK_ERROR");
assert.equal(normalizedNetworkError.retryable, true);
coveredHelperIds.add("helpers.normalizeFoundryLiteError");
assert.equal(sdk.isRetryableFoundryLiteError(normalizedNetworkError), true);
coveredHelperIds.add("helpers.isRetryableFoundryLiteError");

assert.equal(sdk.expectedObjectVersion({ objectVersion: 9 }), 9);
coveredHelperIds.add("helpers.expectedObjectVersion");

assert.deepEqual(
  sdk.classifyFoundryLiteError(
    new sdk.FoundryLiteApiError(403, "PERMISSION_DENIED", "not allowed", {}, "permission-id", false),
  ),
  {
    kind: "permission_denied",
    status: 403,
    code: "PERMISSION_DENIED",
    message: "not allowed",
    details: {},
    requestId: "permission-id",
    retryable: false,
  },
);
coveredHelperIds.add("helpers.classifyFoundryLiteError");
assert.equal(
  sdk.classifyFoundryLiteError(
    new sdk.FoundryLiteApiError(
      409,
      "CONFLICT",
      "object version conflict",
      { expectedObjectVersion: 7, actualObjectVersion: 8 },
      "stale-id",
      false,
    ),
  ).kind,
  "stale_object_version",
);
assert.equal(
  sdk.classifyFoundryLiteError(new sdk.FoundryLiteApiError(409, "CONFLICT", "already exists", {}, "conflict-id", false))
    .kind,
  "conflict",
);
assert.equal(sdk.actionLockKey("ApproveOrder", "order/1"), "ApproveOrder:order/1");
coveredHelperIds.add("helpers.actionLockKey");

const lock = sdk.createInFlightActionLock();
let lockExecutions = 0;
const [firstLockResult, secondLockResult] = await Promise.all([
  lock.run("ApproveOrder:order/1", async () => {
    lockExecutions += 1;
    await Promise.resolve();
    return "approved";
  }),
  lock.run("ApproveOrder:order/1", async () => {
    lockExecutions += 1;
    return "duplicate";
  }),
]);
assert.equal(lockExecutions, 1);
assert.equal(firstLockResult, "approved");
assert.equal(secondLockResult, "approved");
assert.equal(lock.isLocked("ApproveOrder:order/1"), false);
coveredHelperIds.add("helpers.createInFlightActionLock");

let retryAttempts = 0;
const retryDelays = [];
const retryResult = await sdk.retryWithBackoff(
  async () => {
    retryAttempts += 1;
    if (retryAttempts < 3) {
      throw new sdk.FoundryLiteApiError(503, "TEMPORAL_UNAVAILABLE", "retry later", {}, "retry-id", true);
    }
    return "recovered";
  },
  {
    baseDelayMs: 0,
    jitterRatio: 0,
    sleep: async (delayMs) => retryDelays.push(delayMs),
  },
);
assert.equal(retryResult, "recovered");
assert.equal(retryAttempts, 3);
assert.deepEqual(retryDelays, [0, 0]);
coveredHelperIds.add("helpers.retryWithBackoff");

let deniedAttempts = 0;
await assert.rejects(
  () =>
    sdk.retryWithBackoff(async () => {
      deniedAttempts += 1;
      throw new sdk.FoundryLiteApiError(403, "PERMISSION_DENIED", "no", {}, "denied-id", false);
    }),
  (error) => {
    assert.equal(error.code, "PERMISSION_DENIED");
    return true;
  },
);
assert.equal(deniedAttempts, 1);

const seenCursors = [];
const collectedItems = await sdk.collectCursorPages(async (cursor) => {
  seenCursors.push(cursor);
  return cursor === null ? { items: ["first"], nextCursor: "cursor-2" } : { items: ["second"], nextCursor: null };
});
assert.deepEqual(seenCursors, [null, "cursor-2"]);
assert.deepEqual(collectedItems, ["first", "second"]);
coveredHelperIds.add("helpers.collectCursorPages");

const requestContractRows = matrix.surfaces.filter((row) => row.proofClass === "sdk-request-contract");
for (const row of requestContractRows) {
  assert.ok(row.proofTests.includes(REQUIRED_TEST_NAME), row.id);
  assert.ok(coveredSurfaceIds.has(row.id), row.id);
}
assert.equal(coveredSurfaceIds.size, requestContractRows.length);

const idempotencyRows = matrix.surfaces.filter((row) => row.requiresIdempotencyKey === true);
for (const row of idempotencyRows) {
  assert.ok(coveredMissingIdempotencySurfaceIds.has(row.id), row.id);
}
assert.equal(coveredMissingIdempotencySurfaceIds.size, idempotencyRows.length);

const helperContractRows = matrix.sdkHelpers.filter((row) => row.proofClass === "sdk-helper-contract");
for (const row of helperContractRows) {
  assert.ok(row.proofTests.includes(REQUIRED_HELPER_TEST_NAME), row.id);
  assert.ok(coveredHelperIds.has(row.id), row.id);
}
assert.equal(coveredHelperIds.size, helperContractRows.length);

rmSync(tempDir, { recursive: true, force: true });
console.log(
  `SDK request contract covered ${coveredSurfaceIds.size} frontend surfaces and ${coveredHelperIds.size} SDK helpers.`,
);
