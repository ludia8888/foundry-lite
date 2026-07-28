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

function streamResponse(chunks, headers = {}) {
  return {
    ok: true,
    status: 200,
    headers: responseHeaders({ "X-Request-ID": "stream-response-id", ...headers }),
    body: new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    json: async () => ({}),
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
    applicationId: "osdk-app-orders",
    clientId: "client-orders",
    scopes: ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
  },
  requestIdFactory: () => REQUEST_ID,
  fetchImpl,
  onResponse: (metadata) => telemetry.push(metadata),
});
coveredHelperIds.add("helpers.createFoundryLiteClient");
const osdk = sdk.createFoundryLiteOsdkClient(client);
assert.equal(sdk.Order.kind, "object");
assert.equal(sdk.Order.apiName, "Order");
assert.equal(sdk.Order.primaryKey, "orderId");
assert.deepEqual(sdk.$Ontology.objectApiNames, ["Order", "Customer"]);
assert.deepEqual(sdk.$Ontology.actionApiNames, ["ApproveOrder"]);
assert.deepEqual(sdk.$Ontology.linkApiNames, ["OrderCustomer"]);
assert.equal(osdk.objects.Order, sdk.Order);
assert.equal(osdk.actions.ApproveOrder, sdk.ApproveOrder);
assert.equal(sdk.getObjectType("Order"), sdk.Order);
assert.equal(sdk.getActionType("ApproveOrder"), sdk.ApproveOrder);
const sdkManifest = sdk.sdkPackageManifest();
assert.equal(sdkManifest.packageName, "@foundry-lite/sdk");
assert.equal(sdkManifest.packageVersion, "0.1.0");
assert.equal(sdkManifest.ontologyFingerprint, sdk.ONTOLOGY_CONTRACT_FINGERPRINT);
assert.deepEqual(sdkManifest.objectApiNames, ["Order", "Customer"]);
assert.deepEqual(sdkManifest.linkApiNames, ["OrderCustomer"]);
const staticOntologyIndex = sdk.createFoundryLiteOntologyIndex();
assert.equal(staticOntologyIndex.objectCount, 2);
assert.equal(staticOntologyIndex.getObjectView("Order").generatedObjectType, sdk.Order);
assert.equal(staticOntologyIndex.actionsForObjectType("Order")[0].apiName, "ApproveOrder");
assert.deepEqual(staticOntologyIndex.findObjectTypes("customer").map((view) => view.apiName), [
  "Customer",
  "Order",
]);
const liveOntologyCatalog = {
  ontologyVersionId: "ontology/1",
  versionNumber: 7,
  status: "active",
  createdAt: "2026-06-28T00:00:00Z",
  activatedAt: "2026-06-28T00:01:00Z",
  objectTypes: [
    {
      apiName: "Order",
      displayName: "Order",
      description: "Supply chain order",
      primaryKeyProperty: "orderId",
      backing: {},
      config: {},
      properties: [
        {
          apiName: "orderId",
          displayName: "Order ID",
          dataType: "string",
          nullable: false,
          indexed: true,
          searchable: true,
          editable: false,
          classification: null,
          source: "column",
          columnName: "order_id",
          editPolicy: "read_only",
          derivation: null,
        },
      ],
      actions: [
        {
          apiName: "ApproveOrder",
          displayName: "Approve order",
          target: "Order",
          parameterSchema: {},
          definition: {},
          enabled: true,
        },
      ],
    },
    {
      apiName: "SupplierRisk",
      displayName: "Supplier Risk",
      description: "Dynamic supplier risk object activated after SDK generation",
      primaryKeyProperty: "supplierId",
      backing: {},
      config: {},
      properties: [
        {
          apiName: "supplierId",
          displayName: "Supplier ID",
          dataType: "string",
          nullable: false,
          indexed: true,
          searchable: true,
          editable: false,
          classification: null,
          source: "column",
          columnName: "supplier_id",
          editPolicy: "read_only",
          derivation: null,
        },
        {
          apiName: "riskScore",
          displayName: "Risk Score",
          dataType: "number",
          nullable: true,
          indexed: true,
          searchable: false,
          editable: true,
          classification: "finance",
          source: "column",
          columnName: "risk_score",
          editPolicy: "editable",
          derivation: null,
        },
      ],
      actions: [
        {
          apiName: "EscalateSupplier",
          displayName: "Escalate supplier",
          target: "SupplierRisk",
          parameterSchema: {},
          definition: {},
          enabled: true,
        },
      ],
    },
  ],
  linkTypes: [],
};
const matchingOntologyCatalog = {
  ontologyVersionId: "ontology/1",
  versionNumber: 7,
  status: "active",
  createdAt: "2026-06-28T00:00:00Z",
  activatedAt: "2026-06-28T00:01:00Z",
  ontologyContractFingerprint: sdk.ONTOLOGY_CONTRACT_FINGERPRINT,
  objectTypes: [
    liveOntologyCatalog.objectTypes[0],
    {
      apiName: "Customer",
      displayName: "Customer",
      description: "Supply chain customer",
      primaryKeyProperty: "customerId",
      backing: {},
      config: {},
      properties: [
        {
          apiName: "customerId",
          displayName: "Customer ID",
          dataType: "string",
          nullable: false,
          indexed: true,
          searchable: true,
          editable: false,
          classification: null,
          source: "column",
          columnName: "customer_id",
          editPolicy: "read_only",
          derivation: null,
        },
      ],
      actions: [],
    },
  ],
  linkTypes: [
    {
      apiName: "OrderCustomer",
      displayName: "Order belongs to Customer",
      fromObjectType: "Order",
      toObjectType: "Customer",
      cardinality: "many_to_one",
      backing: {},
    },
  ],
};
const freshSdkReport = sdk.sdkOntologyDriftReport(matchingOntologyCatalog);
assert.equal(freshSdkReport.requiresSdkRegeneration, false);
assert.equal(freshSdkReport.isFingerprintMatched, true);
assert.deepEqual(freshSdkReport.reasonCodes, []);
assert.deepEqual(freshSdkReport.liveObjectApiNames, ["Customer", "Order"]);
assert.equal(sdk.assertFoundryLiteSdkFresh(matchingOntologyCatalog).requiresSdkRegeneration, false);
const liveOntologyIndex = sdk.createFoundryLiteOntologyIndex(liveOntologyCatalog);
assert.equal(liveOntologyIndex.objectCount, 3);
assert.equal(liveOntologyIndex.actionCount, 2);
assert.equal(liveOntologyIndex.hasDynamicOnlyTypes, true);
assert.equal(liveOntologyIndex.hasStaticOnlyTypes, true);
assert.deepEqual(liveOntologyIndex.dynamicOnlyObjectViews.map((view) => view.apiName), ["SupplierRisk"]);
assert.deepEqual(liveOntologyIndex.staticOnlyObjectViews.map((view) => view.apiName), ["Customer"]);
assert.deepEqual(liveOntologyIndex.dynamicOnlyActionViews.map((view) => view.apiName), ["EscalateSupplier"]);
assert.equal(liveOntologyIndex.getObjectView("SupplierRisk").propertyCount, 2);
assert.deepEqual(liveOntologyIndex.getObjectView("SupplierRisk").classifiedPropertyNames, ["riskScore"]);
assert.deepEqual(liveOntologyIndex.actionsForObjectType("SupplierRisk").map((view) => view.apiName), [
  "EscalateSupplier",
]);
assert.deepEqual(liveOntologyIndex.findObjectTypes("supplier risk").map((view) => view.apiName), [
  "SupplierRisk",
]);
assert.deepEqual(liveOntologyIndex.findActionTypes("approve").map((view) => view.apiName), ["ApproveOrder"]);
assert.throws(() => liveOntologyIndex.getActionView("MissingAction"), {
  code: "UNKNOWN_ONTOLOGY_ACTION_VIEW",
});
const staleSdkReport = sdk.sdkOntologyDriftReport(liveOntologyCatalog);
assert.equal(staleSdkReport.requiresSdkRegeneration, true);
assert.equal(staleSdkReport.isFingerprintMatched, null);
assert.deepEqual(staleSdkReport.dynamicOnlyObjectApiNames, ["SupplierRisk"]);
assert.deepEqual(staleSdkReport.staticOnlyObjectApiNames, ["Customer"]);
assert.deepEqual(staleSdkReport.dynamicOnlyActionApiNames, ["EscalateSupplier"]);
assert.deepEqual(staleSdkReport.staticOnlyLinkApiNames, ["OrderCustomer"]);
assert.ok(staleSdkReport.reasonCodes.includes("DYNAMIC_OBJECT_TYPES"));
assert.ok(staleSdkReport.reasonCodes.includes("STATIC_LINK_TYPES"));
assert.throws(() => sdk.assertFoundryLiteSdkFresh(liveOntologyCatalog), {
  code: "OSDK_SDK_REGENERATION_REQUIRED",
});
coveredHelperIds.add("helpers.createFoundryLiteOsdkClient");
coveredHelperIds.add("helpers.createFoundryLiteOntologyIndex");
coveredHelperIds.add("helpers.getObjectType");
coveredHelperIds.add("helpers.getActionType");
coveredHelperIds.add("helpers.sdkPackageManifest");
coveredHelperIds.add("helpers.sdkOntologyDriftReport");
coveredHelperIds.add("helpers.assertFoundryLiteSdkFresh");

let sessionTokenCalls = 0;
const sessionClient = sdk.createFoundryLiteClient({
  baseUrl: BASE_URL,
  tokenProvider: sdk.createSessionTokenProvider(async () => {
    sessionTokenCalls += 1;
    return { accessToken: `session-token-${sessionTokenCalls}` };
  }),
  fetchImpl,
  requestIdFactory: () => REQUEST_ID,
});
responseQueue.push(okResponse({ status: "ok" }));
const beforeSessionCalls = calls.length;
await sessionClient.system.health();
assert.equal(calls.length, beforeSessionCalls + 1, "helpers.createSessionTokenProvider");
assert.equal(calls.at(-1).init.headers.Authorization, "Bearer session-token-1");
assert.equal(sessionTokenCalls, 1);
coveredHelperIds.add("helpers.createSessionTokenProvider");

function assertBaseHeaders(headers, surfaceId) {
  assert.equal(headers["X-Tenant-ID"], "tenant-a", surfaceId);
  assert.equal(headers["X-User-ID"], "user-a", surfaceId);
  assert.equal(headers["X-Roles"], "operator,admin", surfaceId);
  assert.equal(headers["X-Foundry-Lite-App-ID"], "osdk-app-orders", surfaceId);
  assert.equal(headers["X-Foundry-Lite-Client-ID"], "client-orders", surfaceId);
  assert.equal(
    headers["X-Foundry-Lite-Scopes"],
    "osdk:object:Order:read osdk:object:Order:subscribe",
    surfaceId,
  );
  assert.equal(headers["X-Request-ID"], REQUEST_ID, surfaceId);
  assert.equal(headers.Authorization, "Bearer token-a", surfaceId);
  assert.equal(headers["X-Static-Header"], "static-a", surfaceId);
}

function assertBody(call, expectedBody, surfaceId) {
  if (expectedBody === undefined) {
    assert.equal(call.init.body, undefined, surfaceId);
    return;
  }
  if (expectedBody.kind === "formData") {
    assertFormData(call.init.body, expectedBody, surfaceId);
    return;
  }
  assert.deepEqual(JSON.parse(call.init.body), expectedBody, surfaceId);
}

function assertFormData(body, expectedBody, surfaceId) {
  assert.ok(body instanceof FormData, surfaceId);
  const entries = Object.fromEntries(body.entries());
  for (const [name, value] of Object.entries(expectedBody.fields ?? {})) {
    assert.equal(entries[name], value, `${surfaceId}:${name}`);
  }
  const uploadedFile = entries.file;
  if (expectedBody.file) {
    assert.ok(uploadedFile, `${surfaceId}:file`);
    assert.equal(uploadedFile.name, expectedBody.file.name, surfaceId);
    assert.equal(uploadedFile.type, expectedBody.file.type, surfaceId);
    assert.equal(uploadedFile.size, expectedBody.file.size, surfaceId);
  }
  if (expectedBody.files) {
    const uploadedFiles = body.getAll("files");
    assert.equal(uploadedFiles.length, expectedBody.files.length, surfaceId);
    expectedBody.files.forEach((expectedFile, index) => {
      assert.equal(uploadedFiles[index].name, expectedFile.name, `${surfaceId}:files:${index}:name`);
      assert.equal(uploadedFiles[index].type, expectedFile.type, `${surfaceId}:files:${index}:type`);
      assert.equal(uploadedFiles[index].size, expectedFile.size, `${surfaceId}:files:${index}:size`);
    });
  }
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

async function assertOsdkFailFast(surfaceId, invoke, expectedCode) {
  const beforeCalls = calls.length;
  await assert.rejects(
    invoke,
    (error) => {
      assert.equal(error.name, "FoundryLiteApiError", surfaceId);
      assert.equal(error.code, expectedCode, surfaceId);
      assert.equal(error.retryable, false, surfaceId);
      return true;
    },
    surfaceId,
  );
  assert.equal(calls.length, beforeCalls, surfaceId);
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
await expectSdkCall(
  "datasets.aggregate",
  () =>
    client.datasets.aggregate("clean space", "orders/daily", {
      version: "version/1",
      filter: [{ column: "status", operator: "eq", value: "PENDING" }],
      groupBy: ["region"],
      select: [{ function: "count", name: "value" }],
    }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/aggregate",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      version: "version/1",
      filter: [{ column: "status", operator: "eq", value: "PENDING" }],
      groupBy: ["region"],
      select: [{ function: "count", name: "value" }],
    },
  },
);
await expectSdkCall("datasets.qualityChecks.list", () => client.datasets.qualityChecks.list("clean", "orders"), {
  path: "/api/datasets/clean/orders/quality-contract/checks",
});
await expectSdkCall(
  "datasets.qualityChecks.create",
  () =>
    client.datasets.qualityChecks.create("clean space", "orders/daily", {
      config: { type: "row_count_min", min: 3 },
      severity: "warn",
    }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/checks",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { config: { type: "row_count_min", min: 3 }, severity: "warn" },
  },
);
await expectSdkCall(
  "datasets.qualityChecks.update",
  () =>
    client.datasets.qualityChecks.update("clean space", "orders/daily", "check/1", {
      enabled: false,
    }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/checks/check%2F1",
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: { enabled: false },
  },
);
await expectSdkCall(
  "datasets.qualityResults.list",
  () => client.datasets.qualityResults.list("clean space", "orders/daily", { limit: 5 }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/results?limit=5",
  },
);
await expectSdkCall(
  "datasets.qualityResults.summary",
  () => client.datasets.qualityResults.summary("clean space", "orders/daily", { latestLimit: 3 }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/results/summary?latest_limit=3",
  },
);
await expectSdkCall(
  "datasets.qualityContracts.list",
  () => client.datasets.qualityContracts.list("clean space", "orders/daily", { limit: 5 }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/contracts?limit=5",
  },
);
await expectSdkCall(
  "datasets.qualityContracts.create",
  () =>
    client.datasets.qualityContracts.create("clean space", "orders/daily", {
      ownerUserId: "data-owner",
      description: "orders quality contract",
    }),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/contracts",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { ownerUserId: "data-owner", description: "orders quality contract" },
  },
);
await expectSdkCall(
  "datasets.qualityContracts.get",
  () => client.datasets.qualityContracts.get("clean space", "orders/daily", "dqcv/1"),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/contracts/dqcv%2F1",
  },
);
await expectSdkCall(
  "datasets.qualityContracts.activate",
  () => client.datasets.qualityContracts.activate("clean space", "orders/daily", "dqcv/1"),
  {
    path: "/api/datasets/clean%20space/orders%2Fdaily/quality-contract/contracts/dqcv%2F1/activate",
    method: "POST",
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
await expectSdkCall("ontology.apply", () => client.ontology.apply({ yamlText: "objectTypes: []" }), {
  path: "/api/ontology/apply",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { yamlText: "objectTypes: []" },
});
await expectSdkCall("ontology.rollback", () => client.ontology.rollback({ versionNumber: 6 }), {
  path: "/api/ontology/rollback",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { versionNumber: 6 },
});
await expectSdkCall(
  "ontology.resources.usage",
  () => client.ontology.resources.usage("object_type", "Order Item", { windowDays: 14 }),
  {
    path: "/api/ontology/resources/object_type/Order%20Item/usage?windowDays=14",
  },
);
await expectSdkCall(
  "ontology.resources.dependents",
  () => client.ontology.resources.dependents("object_type", "Order Item"),
  {
    path: "/api/ontology/resources/object_type/Order%20Item/dependents",
  },
);
await expectSdkCall(
  "ontology.proposals.submit",
  () =>
    client.ontology.proposals.submit(
      {
        yamlText: "objectTypes: []",
        title: "Add supplier risk object",
        description: "Governance proposal from the ontology workspace",
      },
      { idempotencyKey: "proposal-submit-key" },
    ),
  {
    path: "/api/ontology/proposals",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "proposal-submit-key" },
    body: {
      yamlText: "objectTypes: []",
      title: "Add supplier risk object",
      description: "Governance proposal from the ontology workspace",
    },
  },
);
assertMissingIdempotencyFailFast(
  "ontology.proposals.submit",
  () =>
    client.ontology.proposals.submit({
      yamlText: "objectTypes: []",
      title: "Add supplier risk object",
    }),
  "ontology.proposals.submit",
);
await expectSdkCall(
  "ontology.proposals.list",
  () => client.ontology.proposals.list({ status: "pending", cursor: "cursor/1", limit: 25 }),
  {
    path: "/api/ontology/proposals?status=pending&cursor=cursor%2F1&limit=25",
  },
);
await expectSdkCall("ontology.proposals.get", () => client.ontology.proposals.get("proposal/1"), {
  path: "/api/ontology/proposals/proposal%2F1",
});
await expectSdkCall(
  "ontology.proposals.update",
  () =>
    client.ontology.proposals.update("proposal/1", {
      yamlText: "objectTypes: []",
      expectedFingerprint: "sha256:proposal",
      title: "Revised proposal",
    }),
  {
    path: "/api/ontology/proposals/proposal%2F1/update",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      yamlText: "objectTypes: []",
      expectedFingerprint: "sha256:proposal",
      title: "Revised proposal",
    },
  },
);
await expectSdkCall(
  "ontology.proposals.assign",
  () => client.ontology.proposals.assign("proposal/1", { reviewerUserId: "ontology/reviewer" }),
  {
    path: "/api/ontology/proposals/proposal%2F1/assign",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { reviewerUserId: "ontology/reviewer" },
  },
);
await expectSdkCall(
  "ontology.proposals.decide",
  () =>
    client.ontology.proposals.decide("proposal/1", {
      decision: "approved",
      expectedFingerprint: "sha256:proposal",
      comment: "Migration plan reviewed",
    }),
  {
    path: "/api/ontology/proposals/proposal%2F1/decide",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      decision: "approved",
      expectedFingerprint: "sha256:proposal",
      comment: "Migration plan reviewed",
    },
  },
);
await expectSdkCall(
  "ontology.proposals.execute",
  () => client.ontology.proposals.execute("proposal/1", { expectedFingerprint: "sha256:proposal" }),
  {
    path: "/api/ontology/proposals/proposal%2F1/execute",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { expectedFingerprint: "sha256:proposal" },
  },
);
await expectSdkCall(
  "ontology.proposals.withdraw",
  () => client.ontology.proposals.withdraw("proposal/1", { reason: "superseded by a newer proposal" }),
  {
    path: "/api/ontology/proposals/proposal%2F1/withdraw",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { reason: "superseded by a newer proposal" },
  },
);
await expectSdkCall(
  "ontology.branches.create",
  () =>
    client.ontology.branches.create(
      { name: "supplier-risk-redesign" },
      { idempotencyKey: "branch-create-key" },
    ),
  {
    path: "/api/ontology/branches",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "branch-create-key" },
    body: { name: "supplier-risk-redesign" },
  },
);
assertMissingIdempotencyFailFast(
  "ontology.branches.create",
  () => client.ontology.branches.create({ name: "supplier-risk-redesign" }),
  "ontology.branches.create",
);
await expectSdkCall(
  "ontology.branches.list",
  () => client.ontology.branches.list({ status: "open", cursor: "cursor/1", limit: 25 }),
  {
    path: "/api/ontology/branches?status=open&cursor=cursor%2F1&limit=25",
  },
);
await expectSdkCall("ontology.branches.get", () => client.ontology.branches.get("branch/1"), {
  path: "/api/ontology/branches/branch%2F1",
});
await expectSdkCall(
  "ontology.branches.update",
  () =>
    client.ontology.branches.update("branch/1", {
      yamlText: "objectTypes: []",
      expectedFingerprint: "sha256:branch",
    }),
  {
    path: "/api/ontology/branches/branch%2F1/update",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { yamlText: "objectTypes: []", expectedFingerprint: "sha256:branch" },
  },
);
await expectSdkCall("ontology.branches.diff", () => client.ontology.branches.diff("branch/1"), {
  path: "/api/ontology/branches/branch%2F1/diff",
});
await expectSdkCall(
  "ontology.branches.rebase",
  () =>
    client.ontology.branches.rebase("branch/1", {
      expectedFingerprint: "sha256:branch",
      resolutions: [{ kind: "objectType", apiName: "Order", use: "branch" }],
    }),
  {
    path: "/api/ontology/branches/branch%2F1/rebase",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      expectedFingerprint: "sha256:branch",
      resolutions: [{ kind: "objectType", apiName: "Order", use: "branch" }],
    },
  },
);
await expectSdkCall(
  "ontology.branches.propose",
  () =>
    client.ontology.branches.propose(
      "branch/1",
      { title: "Merge supplier risk redesign", description: "Branch merge request" },
      { idempotencyKey: "branch-propose-key" },
    ),
  {
    path: "/api/ontology/branches/branch%2F1/propose",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "branch-propose-key" },
    body: { title: "Merge supplier risk redesign", description: "Branch merge request" },
  },
);
assertMissingIdempotencyFailFast(
  "ontology.branches.propose",
  () => client.ontology.branches.propose("branch/1", { title: "Merge supplier risk redesign" }),
  "ontology.branches.propose",
);
await expectSdkCall(
  "ontology.branches.abandon",
  () => client.ontology.branches.abandon("branch/1"),
  {
    path: "/api/ontology/branches/branch%2F1/abandon",
    method: "POST",
  },
);
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
  "aip.agent.run",
  () =>
    client.aip.agent.run({
      agentRunId: "agent-web-1",
      agentVersionId: "agent-demo:v1",
      modelAlias: "support-assistant",
      promptVersionId: "prompt-v1",
      userMessage: "Explain O-1",
      agentInstruction: "Answer with authorized context only.",
      securityPartition: "tenant-a:orders",
      allowedSecurityPartitions: ["tenant-a:orders"],
      stateJson: { objectId: "O-1" },
      outputSchema: { type: "object" },
      dataClassification: "internal",
      maxContextItems: 4,
      maxContextTokens: 1200,
      maxModelCalls: 1,
      maxLoopIterations: 1,
    }),
  {
    path: "/api/aip/agent/run",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      agentRunId: "agent-web-1",
      agentVersionId: "agent-demo:v1",
      modelAlias: "support-assistant",
      promptVersionId: "prompt-v1",
      userMessage: "Explain O-1",
      agentInstruction: "Answer with authorized context only.",
      securityPartition: "tenant-a:orders",
      allowedSecurityPartitions: ["tenant-a:orders"],
      stateJson: { objectId: "O-1" },
      outputSchema: { type: "object" },
      dataClassification: "internal",
      maxContextItems: 4,
      maxContextTokens: 1200,
      maxModelCalls: 1,
      maxLoopIterations: 1,
    },
  },
);
await expectSdkCall(
  "aip.citations.resolveNavigation",
  () =>
    client.aip.citations.resolveNavigation({
      navigationRef: "flite-citation-nav.v1.signed",
    }),
  {
    path: "/api/aip/citations/navigation/resolve",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { navigationRef: "flite-citation-nav.v1.signed" },
  },
);
await expectSdkCall(
  "aip.evals.run",
  () =>
    client.aip.evals.run({
      evalRunId: "eval-web-1",
      suiteApiName: "release-gate",
      suiteVersion: "v1",
      suiteDescription: "Web release gate",
      agentVersionId: "agent-demo:v1",
      candidateReleaseChannel: "dev",
      cases: [
        {
          caseApiName: "answer-ok",
          axis: "answer",
          inputJson: { question: "What is the status?" },
          expectedJson: { answer: "ok" },
          actualJson: { answer: "ok" },
          rubricJson: { mode: "exact_subset" },
          tags: ["smoke"],
          sampleIndex: 1,
          evaluator: "exact_subset_v1",
          weight: 1,
        },
      ],
      minScore: 1,
      requiredAxes: ["answer"],
    }),
  {
    path: "/api/aip/evals/run",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      evalRunId: "eval-web-1",
      suiteApiName: "release-gate",
      suiteVersion: "v1",
      suiteDescription: "Web release gate",
      agentVersionId: "agent-demo:v1",
      candidateReleaseChannel: "dev",
      cases: [
        {
          caseApiName: "answer-ok",
          axis: "answer",
          inputJson: { question: "What is the status?" },
          expectedJson: { answer: "ok" },
          actualJson: { answer: "ok" },
          rubricJson: { mode: "exact_subset" },
          tags: ["smoke"],
          sampleIndex: 1,
          evaluator: "exact_subset_v1",
          weight: 1,
        },
      ],
      minScore: 1,
      requiredAxes: ["answer"],
    },
  },
);
await expectSdkCall(
  "aip.releases.promote",
  () =>
    client.aip.releases.promote({
      agentVersionId: "agent-demo:v1",
      targetReleaseChannel: "dev",
      evalRunId: "eval-web-1",
      policyVersion: "release-policy-v1",
    }),
  {
    path: "/api/aip/releases/promote",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      agentVersionId: "agent-demo:v1",
      targetReleaseChannel: "dev",
      evalRunId: "eval-web-1",
      policyVersion: "release-policy-v1",
    },
  },
);
await expectSdkCall(
  "media.sets.create",
  () =>
    client.media.sets.create({
      namespace: "raw",
      name: "source-images",
      schemaType: "image",
      primaryFormat: "png",
      allowedInputFormats: ["png", "jpeg"],
      classification: "internal",
      transactionPolicy: "transactional",
      storageProfile: "local",
      processingProfile: "local",
      retentionPolicyId: "retention-30d",
    }),
  {
    path: "/api/media/sets",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      namespace: "raw",
      name: "source-images",
      schemaType: "image",
      primaryFormat: "png",
      allowedInputFormats: ["png", "jpeg"],
      classification: "internal",
      transactionPolicy: "transactional",
      storageProfile: "local",
      processingProfile: "local",
      retentionPolicyId: "retention-30d",
    },
  },
);
await expectSdkCall("media.sets.get", () => client.media.sets.get("media-set/1"), {
  path: "/api/media/sets/media-set%2F1",
});
await expectSdkCall(
  "media.transactions.open",
  () => client.media.transactions.open("media-set/1", { mode: "APPEND" }, { idempotencyKey: "media-open-key" }),
  {
    path: "/api/media/sets/media-set%2F1/transactions",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "media-open-key" },
    body: { mode: "APPEND" },
  },
);
assertMissingIdempotencyFailFast(
  "media.transactions.open",
  () => client.media.transactions.open("media-set/1", { mode: "APPEND" }),
  "media.transactions.open",
);
await expectSdkCall(
  "media.transactions.upload",
  () =>
    client.media.transactions.upload("media-set/1", "media-tx/1", {
      logicalPath: "/invoices/scan.png",
      schemaType: "image",
      format: "png",
      file: new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" }),
      fileName: "scan.png",
      suppliedMimeType: "image/png",
      securityEnvelope: { tenantId: "tenant-a", classification: "internal" },
      probeMetadata: { width: 1, height: 1 },
    }),
  {
    path: "/api/media/sets/media-set%2F1/transactions/media-tx%2F1/uploads",
    method: "POST",
    body: {
      kind: "formData",
      fields: {
        logicalPath: "/invoices/scan.png",
        schemaType: "image",
        format: "png",
        suppliedMimeType: "image/png",
        securityEnvelope: JSON.stringify({ tenantId: "tenant-a", classification: "internal" }),
        probeMetadata: JSON.stringify({ width: 1, height: 1 }),
      },
      file: { name: "scan.png", type: "image/png", size: 3 },
    },
  },
);
await expectSdkCall("media.transactions.commit", () => client.media.transactions.commit("media-tx/1"), {
  path: "/api/media/transactions/media-tx%2F1/commit",
  method: "POST",
});
responseQueue.push(
  okResponse({ mediaTransactionId: "osdk-media-tx" }),
  okResponse({
    media_item_version_id: "mver_osdk",
    media_item_id: "mitem_osdk",
    version_number: 1,
    blob_key: "blob-osdk",
    is_duplicate: false,
  }),
  okResponse({
    media_transaction_id: "osdk-media-tx",
    committed_version_ids: ["mver_osdk"],
    head_version_id_by_item: {},
  }),
);
const beforeOsdkMediaCalls = calls.length;
const beforeOsdkMediaTelemetry = telemetry.length;
await osdk.media.uploadAndCommit(
  "media-set/1",
  {
    logicalPath: "/invoices/osdk-scan.png",
    schemaType: "image",
    format: "png",
    file: new Blob([new Uint8Array([4, 5, 6])], { type: "image/png" }),
    fileName: "osdk-scan.png",
    suppliedMimeType: "image/png",
    securityEnvelope: { tenantId: "tenant-a", classification: "internal" },
  },
  { idempotencyKey: "osdk-media-key" },
);
assert.equal(calls.length, beforeOsdkMediaCalls + 3, "osdk.media.uploadAndCommit");
assert.equal(telemetry.length, beforeOsdkMediaTelemetry + 3, "osdk.media.uploadAndCommit");
assertRequest(calls.at(-3), {
  surfaceId: "media.transactions.open",
  path: "/api/media/sets/media-set%2F1/transactions",
  method: "POST",
  headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-media-key" },
  body: { mode: "APPEND" },
});
assertRequest(calls.at(-2), {
  surfaceId: "media.transactions.upload",
  path: "/api/media/sets/media-set%2F1/transactions/osdk-media-tx/uploads",
  method: "POST",
  body: {
    kind: "formData",
    fields: {
      logicalPath: "/invoices/osdk-scan.png",
      schemaType: "image",
      format: "png",
      suppliedMimeType: "image/png",
      securityEnvelope: JSON.stringify({ tenantId: "tenant-a", classification: "internal" }),
    },
    file: { name: "osdk-scan.png", type: "image/png", size: 3 },
  },
});
assertRequest(calls.at(-1), {
  surfaceId: "media.transactions.commit",
  path: "/api/media/transactions/osdk-media-tx/commit",
  method: "POST",
});
await expectSdkCall(
  "media.versions.process",
  () =>
    client.media.versions.process("media-version/1", {
      processor: "ocr_v1",
      processorVersion: "1.0",
      model: "tesseract",
      modelVersion: "5.5.2",
      parameters: { maxPages: 2 },
    }),
  {
    path: "/api/media/versions/media-version%2F1/process",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      processor: "ocr_v1",
      processorVersion: "1.0",
      model: "tesseract",
      modelVersion: "5.5.2",
      parameters: { maxPages: 2 },
    },
  },
);
assert.equal(
  client.media.versions.sourceUrl("media-version/1"),
  `${BASE_URL}/api/media/versions/media-version%2F1/content`,
);
coveredSurfaceIds.add("media.versions.sourceUrl");
await expectSdkCall("media.derivatives.get", () => client.media.derivatives.get("media-derivative/1"), {
  path: "/api/media/derivatives/media-derivative%2F1",
});
await expectSdkCall(
  "media.derivatives.contentUnits",
  () =>
    client.media.derivatives.contentUnits("media-derivative/1", {
      afterOrdinal: 4,
      pageNumber: 2,
      limit: 100,
    }),
  {
    path: "/api/media/derivatives/media-derivative%2F1/content-units?afterOrdinal=4&pageNumber=2&limit=100",
  },
);
await expectSdkCall(
  "media.derivatives.index",
  () => client.media.derivatives.index("media-derivative/1", { generation: "media-g1" }),
  {
    path: "/api/media/derivatives/media-derivative%2F1/index",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { generation: "media-g1" },
  },
);
await expectSdkCall(
  "media.content.search",
  () => client.media.content.search({ text: "invoice", topK: 5, allowedClassifications: ["internal"] }),
  {
    path: "/api/media/content/search",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { text: "invoice", topK: 5, allowedClassifications: ["internal"] },
  },
);
await expectSdkCall(
  "media.visual.indexDerivative",
  () => client.media.visual.indexDerivative("media-derivative/1", { generation: "clip-g1" }),
  {
    path: "/api/media/visual/derivatives/media-derivative%2F1/index",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { generation: "clip-g1" },
  },
);
await expectSdkCall(
  "media.visual.promoteGeneration",
  () => client.media.visual.promoteGeneration({ expectedActive: "", generation: "clip-g1" }),
  {
    path: "/api/media/visual/generations/promote",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { expectedActive: "", generation: "clip-g1" },
  },
);
await expectSdkCall(
  "media.visual.search",
  () => client.media.visual.search({ text: "a photo of a car", topK: 3 }),
  {
    path: "/api/media/visual/search",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { text: "a photo of a car", topK: 3 },
  },
);
await expectSdkCall(
  "media.processing-runs.list",
  () => client.media.processingRuns.list({ sourceMediaItemVersionId: "media-version/1", limit: 5 }),
  {
    path: "/api/media/processing-runs?sourceMediaItemVersionId=media-version%2F1&limit=5",
  },
);
await expectSdkCall("media.processing-runs.get", () => client.media.processingRuns.get("media-run/1"), {
  path: "/api/media/processing-runs/media-run%2F1",
});
await expectSdkCall("media.processors.list", () => client.media.processors.list(), {
  path: "/api/media/processors",
});
await expectSdkCall(
  "media.references.bind",
  () =>
    client.media.references.bind(
      {
        holderType: "Order",
        holderId: "O-1",
        propertyName: "sourceDocument",
        mediaItemVersionId: "media-version/1",
      },
      { idempotencyKey: "media-bind-key" },
    ),
  {
    path: "/api/media/references/bind",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "media-bind-key" },
    body: {
      holderType: "Order",
      holderId: "O-1",
      propertyName: "sourceDocument",
      mediaItemVersionId: "media-version/1",
    },
  },
);
assertMissingIdempotencyFailFast(
  "media.references.bind",
  () =>
    client.media.references.bind({
      holderType: "Order",
      holderId: "O-1",
      propertyName: "sourceDocument",
      mediaItemVersionId: "media-version/1",
    }),
  "media.references.bind",
);
await expectSdkCall(
  "media.references.resolve",
  () =>
    client.media.references.resolve({
      holderType: "Order",
      holderId: "O-1",
      propertyName: "sourceDocument",
      allowedClassifications: ["internal", "confidential"],
    }),
  {
    path:
      "/api/media/references/resolve?holderType=Order&holderId=O-1&propertyName=sourceDocument" +
      "&allowedClassifications=internal&allowedClassifications=confidential",
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
await expectSdkCall(
  "insights.reviews.executeApprovedAction",
  () =>
    client.insights.reviews.executeApprovedAction(
      "review/1",
      { expectedProposalFingerprint: "sha256:abc" },
      { idempotencyKey: "approval-execute-key" },
    ),
  {
    path: "/api/insights/reviews/review%2F1/execute-approved-action",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "approval-execute-key" },
    body: { expectedProposalFingerprint: "sha256:abc" },
  },
);
assertMissingIdempotencyFailFast(
  "insights.reviews.executeApprovedAction",
  () =>
    client.insights.reviews.executeApprovedAction("review/1", {
      expectedProposalFingerprint: "sha256:abc",
    }),
  "insights.reviews.executeApprovedAction",
);
await expectSdkCall("objects.generic.get", () => client.objects.generic.get("Order Item", "order/1", { explain: true }), {
  path: "/api/objects/Order%20Item/order%2F1?explain=true",
});
await expectSdkCall("objects.generic.links", () => client.objects.generic.links("Order", "order/1", "customer/edge"), {
  path: "/api/objects/Order/order%2F1/links/customer%2Fedge",
});
await expectSdkCall("objects.generic.query", () => client.objects.generic.query("Order Item", { limit: 10, search: "rush" }), {
  path: "/api/objects/Order%20Item/query",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { limit: 10, search: "rush" },
});
await expectSdkCall(
  "objects.generic.aggregate",
  () =>
    client.objects.generic.aggregate("Order Item", {
      select: [{ function: "count", name: "orders" }, { function: "sum", property: "amount" }],
      groupBy: ["status"],
      filter: { property: "status", op: "eq", value: "PENDING" },
    }),
  {
    path: "/api/objects/Order%20Item/aggregate",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      select: [{ function: "count", name: "orders" }, { function: "sum", property: "amount" }],
      groupBy: ["status"],
      filter: { property: "status", op: "eq", value: "PENDING" },
    },
  },
);
await expectSdkCall(
  "objects.Order.aggregate.generated",
  () => client.objects.Order.aggregate({ select: [{ function: "count" }] }),
  {
    path: "/api/objects/Order/aggregate",
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Foundry-Lite-Object": "Order" },
    body: { select: [{ function: "count" }] },
  },
);
coveredSurfaceIds.delete("objects.Order.aggregate.generated");
await expectSdkCall(
  "interfaces.generic.query",
  () =>
    client.interfaces.generic.query("Asset Kind", {
      filter: { property: "riskScore", op: "gte", value: 0.5 },
      orderBy: [{ property: "riskScore", direction: "desc" }],
      limit: 10,
    }),
  {
    path: "/api/interfaces/Asset%20Kind/query",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      filter: { property: "riskScore", op: "gte", value: 0.5 },
      orderBy: [{ property: "riskScore", direction: "desc" }],
      limit: 10,
    },
  },
);
await expectSdkCall(
  "interfaces.Asset.query.generated",
  () => client.interfaces.Asset.query({ limit: 5 }),
  {
    path: "/api/interfaces/Asset/query",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { limit: 5 },
  },
);
coveredSurfaceIds.delete("interfaces.Asset.query.generated");
await expectSdkCall(
  "functions.generic.execute",
  () => client.functions.generic.execute("order Risk", { inputs: { objectId: "O-1001" } }),
  {
    path: "/api/functions/order%20Risk/execute",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { inputs: { objectId: "O-1001" } },
  },
);
await expectSdkCall(
  "functions.orderRiskSummary.execute.generated",
  () => client.functions.orderRiskSummary.execute({ inputs: { objectId: "O-1001" } }),
  {
    path: "/api/functions/orderRiskSummary/execute",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { inputs: { objectId: "O-1001" } },
  },
);
coveredSurfaceIds.delete("functions.orderRiskSummary.execute.generated");
await expectSdkCall("objects.generic.get", () => osdk(sdk.Order).fetchOne("order/osdk", { explain: true }), {
  path: "/api/objects/Order/order%2Fosdk?explain=true",
});
await expectSdkCall(
  "objects.generic.query",
  () => osdk(sdk.Order).where({ property: "status", op: "eq", value: "PENDING" }).fetchPage({ pageSize: 2 }),
  {
    path: "/api/objects/Order/query",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { filter: { property: "status", op: "eq", value: "PENDING" }, limit: 2 },
  },
);
await expectSdkCall(
  "objects.generic.query",
  () =>
    osdk(sdk.Order)
      .where({ status: { $eq: "PENDING" } })
      .orderBy({ amount: "desc" })
      .fetchPage({ $pageSize: 2, $pageToken: "cursor/next" }),
  {
    path: "/api/objects/Order/query",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      filter: { property: "status", op: "eq", value: "PENDING" },
      orderBy: [{ property: "amount", direction: "desc" }],
      limit: 2,
      cursor: "cursor/next",
    },
  },
);
responseQueue.push(
  streamResponse([
    'event: snapshot\ndata: {"event":"snapshot","items":[],"watermark":11}\n\n',
    (
      'event: object_changed\ndata: {"event":"object_changed","state":"ADDED_OR_UPDATED",' +
      '"object":{"objectType":"Order","objectId":"O-200","objectVersion":2,' +
      '"properties":{"orderId":"O-200","status":"PENDING"}},"watermark":12}\n\n'
    ),
  ]),
);
{
  const beforeCalls = calls.length;
  const beforeTelemetry = telemetry.length;
  const snapshots = [];
  const changes = [];
  const handle = osdk(sdk.Order)
    .where({ status: { $eq: "PENDING" } })
    .subscribe(
      {
        onSuccessfulSubscription: (event) => snapshots.push(event),
        onChange: (event) => changes.push(event),
      },
      { pageSize: 3, maxEvents: 2, transport: "sse" },
    );
  await handle.closed;
  assert.equal(calls.length, beforeCalls + 1, "objects.generic.subscribe");
  assert.equal(telemetry.length, beforeTelemetry, "objects.generic.subscribe");
  assert.equal(snapshots[0].watermark, 11);
  assert.equal(changes[0].object.objectId, "O-200");
  assertRequest(calls.at(-1), {
    surfaceId: "objects.generic.subscribe",
    path: "/api/objects/Order/subscriptions/stream",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      filter: { property: "status", op: "eq", value: "PENDING" },
      orderBy: null,
      properties: null,
      pageSize: 3,
      lastSeenObjectChangeSequence: null,
      maxEvents: 2,
    },
  });
  coveredSurfaceIds.add("objects.generic.subscribe");
}
{
  class ResumeWebSocket {
    static instances = [];

    constructor(url, protocols) {
      this.url = url;
      this.protocols = protocols;
      this.sentBodies = [];
      ResumeWebSocket.instances.push(this);
      queueMicrotask(() => this.onopen?.());
    }

    send(body) {
      const parsed = JSON.parse(body);
      this.sentBodies.push(parsed);
      if (ResumeWebSocket.instances.length === 1) {
        this.onmessage?.({ data: JSON.stringify({ event: "snapshot", items: [], watermark: 7 }) });
        this.onerror?.(new Error("network drop"));
        return;
      }
      this.onmessage?.({
        data: JSON.stringify({
          event: "object_changed",
          state: "ADDED_OR_UPDATED",
          object: { objectType: "Order", objectId: "O-201", objectVersion: 3, properties: {} },
          watermark: 9,
        }),
      });
      this.onclose?.({ code: 1000 });
    }

    close() {
      this.onclose?.({ code: 1000 });
    }
  }

  const events = [];
  for await (const event of client.objects.generic.subscribe(
    "Order",
    { filter: { property: "status", op: "eq", value: "PENDING" } },
    { transport: "websocket", webSocketImpl: ResumeWebSocket, maxReconnectAttempts: 1 },
  )) {
    events.push(event.payload);
  }

  assert.equal(ResumeWebSocket.instances.length, 2, "subscription websocket reconnect count");
  assert.equal(ResumeWebSocket.instances[0].url, "wss://foundry.example/api/objects/Order/subscriptions/ws");
  assert.deepEqual(ResumeWebSocket.instances[0].protocols, ["foundry-lite", "bearer.token-a"]);
  assert.equal(ResumeWebSocket.instances[0].sentBodies[0].lastSeenObjectChangeSequence, undefined);
  assert.equal(ResumeWebSocket.instances[1].sentBodies[0].lastSeenObjectChangeSequence, 7);
  assert.equal(JSON.stringify(ResumeWebSocket.instances[1].sentBodies).includes("token-a"), false);
  assert.deepEqual(events.map((event) => event.event), ["snapshot", "object_changed"]);
}
{
  class BackpressureWebSocket {
    constructor() {
      queueMicrotask(() => this.onopen?.());
    }

    send() {
      this.onmessage?.({ data: JSON.stringify({ event: "snapshot", items: [], watermark: 1 }) });
      this.onmessage?.({ data: JSON.stringify({ event: "heartbeat", watermark: 1 }) });
    }

    close() {
      this.onclose?.({ code: 1008 });
    }
  }

  await assert.rejects(
    async () => {
      for await (const _event of client.objects.generic.subscribe("Order", {}, {
        transport: "websocket",
        webSocketImpl: BackpressureWebSocket,
        backpressureLimit: 1,
        reconnect: false,
      })) {
        // The first event may be delivered, but the second queued event must trip backpressure.
      }
    },
    (error) => {
      assert.equal(error.name, "FoundryLiteApiError", "subscription backpressure error type");
      assert.equal(error.code, "BACKPRESSURE_LIMIT_EXCEEDED", "subscription backpressure code");
      assert.equal(error.retryable, false, "subscription backpressure retryable");
      return true;
    },
  );
}
{
  class RateLimitedWebSocket {
    constructor() {
      queueMicrotask(() => this.onopen?.());
    }

    send() {
      this.onmessage?.({
        data: JSON.stringify({
          event: "error",
          error: { code: "RATE_LIMITED", message: "slow down", details: { retryAfterSeconds: 5 } },
        }),
      });
      this.onclose?.({ code: 1008 });
    }

    close() {
      this.onclose?.({ code: 1008 });
    }
  }

  const beforeCalls = calls.length;
  await assert.rejects(
    async () => {
      for await (const _event of client.objects.generic.subscribe("Order", {}, {
        transport: "auto",
        webSocketImpl: RateLimitedWebSocket,
      })) {
        // RATE_LIMITED is terminal and must not hide behind SSE fallback.
      }
    },
    (error) => {
      assert.equal(error.name, "FoundryLiteApiError", "subscription rate-limit error type");
      assert.equal(error.code, "RATE_LIMITED", "subscription rate-limit code");
      assert.equal(error.status, 429, "subscription rate-limit status");
      assert.equal(error.retryable, true, "subscription rate-limit retryable");
      return true;
    },
  );
  assert.equal(calls.length, beforeCalls, "subscription rate-limit no SSE fallback");
}
await assertOsdkFailFast(
  "osdk.objectset.invalid-property",
  async () => osdk(sdk.Order).where({ missing: { $eq: "x" } }).fetchPage(),
  "INVALID_OSDK_FILTER_PROPERTY",
);
await assertOsdkFailFast(
  "osdk.objectset.invalid-operator",
  async () => osdk(sdk.Order).where({ status: { $startsWith: "P" } }).fetchPage(),
  "INVALID_OSDK_FILTER_OPERATOR",
);
await assertOsdkFailFast(
  "osdk.objectset.invalid-order-property",
  async () => osdk(sdk.Order).orderBy({ missing: "asc" }).fetchPage(),
  "INVALID_OSDK_ORDER_PROPERTY",
);
responseQueue.push(
  okResponse({
    items: [
      {
        objectType: "Customer",
        objectId: "C-100",
        objectVersion: 3,
        properties: { customerId: "C-100", region: "NA" },
      },
    ],
    nextCursor: "customers/page/2",
  }),
  okResponse({
    items: [
      {
        objectType: "Customer",
        objectId: "C-101",
        objectVersion: 4,
        properties: { customerId: "C-101", region: "EU" },
      },
    ],
    nextCursor: null,
  }),
);
{
  const beforeCalls = calls.length;
  const aggregateResult = await osdk(sdk.Customer)
    .where({ region: { $in: ["NA", "EU"] } })
    .aggregate({ select: { count: { $count: "unordered" } }, groupBy: { region: "exact" } });
  assert.equal(calls.length, beforeCalls + 2);
  assert.deepEqual(
    Object.fromEntries(
      aggregateResult.data.map((bucket) => [bucket.group.region, bucket.metrics[0].value]),
    ),
    { NA: 1, EU: 1 },
  );
  assertRequest(calls[beforeCalls], {
    surfaceId: "osdk.objectset.aggregate.page1",
    path: "/api/objects/Customer/query",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      filter: { property: "region", op: "in", value: ["NA", "EU"] },
      orderBy: [{ property: "region", direction: "asc" }],
      limit: 500,
    },
  });
  assertRequest(calls[beforeCalls + 1], {
    surfaceId: "osdk.objectset.aggregate.page2",
    path: "/api/objects/Customer/query",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      filter: { property: "region", op: "in", value: ["NA", "EU"] },
      orderBy: [{ property: "region", direction: "asc" }],
      limit: 500,
      cursor: "customers/page/2",
    },
  });
}
await assertOsdkFailFast(
  "osdk.objectset.aggregate.invalid-groupby-property",
  async () =>
    osdk(sdk.Order).aggregate({ select: { count: { $count: "unordered" } }, groupBy: { region: "exact" } }),
  "INVALID_OSDK_AGGREGATE_GROUPBY_PROPERTY",
);
await assertOsdkFailFast(
  "osdk.objectset.aggregate.unsupported-operator",
  async () => osdk(sdk.Order).aggregate({ select: { sumAmount: { amount: { $sum: "unordered" } } } }),
  "INVALID_OSDK_AGGREGATE_SELECT",
);
responseQueue.push(
  okResponse({
    objectType: "Order",
    objectId: "O-1001",
    objectVersion: 7,
    properties: { orderId: "O-1001", customerId: "C-100", status: "PENDING" },
  }),
  okResponse([
    {
      linkType: "OrderCustomer",
      from: { objectType: "Order", objectId: "O-1001" },
      to: { objectType: "Customer", objectId: "C-100", properties: { customerId: "C-100" } },
    },
  ]),
  okResponse({
    objectType: "Customer",
    objectId: "C-100",
    objectVersion: 3,
    properties: { customerId: "C-100", region: "EMEA" },
  }),
);
{
  const beforeCalls = calls.length;
  const order = await osdk(sdk.Order).fetchOne("O-1001");
  const customers = await order.$link.customer.fetchPage({ $pageSize: 1 });
  assert.equal(customers.data[0].objectId, "C-100");
  assert.equal(customers.data[0].$link.order !== undefined, true);
  assert.equal(calls.length, beforeCalls + 3);
  assertRequest(calls[beforeCalls], {
    surfaceId: "osdk.object-instance.fetchOne",
    path: "/api/objects/Order/O-1001",
  });
  assertRequest(calls[beforeCalls + 1], {
    surfaceId: "osdk.object-instance.link.customer",
    path: "/api/objects/Order/O-1001/links/OrderCustomer",
  });
  assertRequest(calls[beforeCalls + 2], {
    surfaceId: "osdk.object-instance.link.customer.target",
    path: "/api/objects/Customer/C-100",
  });
  responseQueue.push(okResponse({
    actionApiName: "ApproveOrder",
    result: "VALID",
    target: {
      result: "VALID",
      objectType: "Order",
      objectId: "O-1001",
      expectedObjectVersion: 7,
      currentObjectVersion: 7,
      issues: [],
    },
    parameters: { reason: { result: "VALID", required: true, issues: [] } },
    submissionCriteria: [],
  }));
  const actionValidation = await order.$actions.approveOrder.validateAction(
    { reason: "approved through bound action" },
  );
  assert.equal(actionValidation.result, "VALID");
  assertRequest(calls.at(-1), {
    surfaceId: "osdk.object-instance.actions.approveOrder.validate",
    path: "/api/actions/ApproveOrder/validate",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      target: { objectType: "Order", objectId: "O-1001" },
      expectedObjectVersion: 7,
      params: { reason: "approved through bound action" },
    },
  });
  let boundActionCacheRefresh = null;
  responseQueue.push(okResponse({
    actionRunId: "action-run/osdk-bound",
    status: "succeeded",
    target: { objectType: "Order", objectId: "O-1001" },
    cacheRefresh: {
      objectKeys: ["objects:Order:O-1001"],
      objectTypeKeys: ["objects:Order:query"],
      actionRunKeys: ["actions:runs:action-run/osdk-bound"],
    },
  }));
  await order.$actions.approveOrder.applyAction(
    { reason: "approved through bound action" },
    { idempotencyKey: "bound-action-key", onCacheRefresh: (hint) => { boundActionCacheRefresh = hint; } },
  );
  assert.deepEqual(boundActionCacheRefresh.objectKeys, ["objects:Order:O-1001"]);
  assertRequest(calls.at(-1), {
    surfaceId: "osdk.object-instance.actions.approveOrder",
    path: "/api/actions/ApproveOrder/apply",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "bound-action-key" },
    body: {
      target: { objectType: "Order", objectId: "O-1001" },
      expectedObjectVersion: 7,
      params: { reason: "approved through bound action" },
    },
  });
  await assertOsdkFailFast(
    "osdk.object-instance.actions.missing-idempotency",
    async () => order.$actions.approveOrder.applyAction({ reason: "missing key" }),
    "MISSING_IDEMPOTENCY_KEY",
  );
}
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
  "transforms.register-sql",
  () =>
    client.transforms.registerSql({
      apiName: "clean_orders_from_web",
      sql: "select * from {{ input('raw.orders') }}",
      inputs: { "raw.orders": "raw.orders" },
      outputDatasetRef: "clean.orders_web",
      checks: [{ type: "row_count_min", min: 1 }],
      mode: "snapshot",
    }),
  {
    path: "/api/transforms/sql",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      apiName: "clean_orders_from_web",
      sql: "select * from {{ input('raw.orders') }}",
      inputs: { "raw.orders": "raw.orders" },
      outputDatasetRef: "clean.orders_web",
      checks: [{ type: "row_count_min", min: 1 }],
      mode: "snapshot",
    },
  },
);
await expectSdkCall("transforms.run", () => client.transforms.run("clean_orders_from_web"), {
  path: "/api/transforms/clean_orders_from_web/run",
  method: "POST",
});
await expectSdkCall("transforms.scheduler.previewDue", () => client.transforms.previewDue({ maxRuns: 10 }), {
  path: "/api/transforms/scheduler/due?maxRuns=10",
});
await expectSdkCall("transforms.scheduler.tick", () => client.transforms.tick({ maxRuns: 10 }), {
  path: "/api/transforms/scheduler/tick",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { maxRuns: 10 },
});
const pipelineGraph = {
  nodes: [
    { id: "raw", type: "dataset", data: { datasetRef: "raw.orders" } },
    {
      id: "sql",
      type: "sql",
      data: {
        sql: "select * from {{ input('raw.orders') }}",
        outputDatasetRef: "clean.orders",
      },
    },
    { id: "out", type: "output_dataset", data: { outputDatasetRef: "clean.orders_final" } },
  ],
  edges: [
    { source: "raw", target: "sql" },
    { source: "sql", target: "out" },
  ],
  layout: {},
  outputContract: { columns: [{ name: "order_id", type: "string", nullable: false }] },
  tests: [],
  schedule: null,
};
const pipelineGraphV2 = {
  schemaVersion: 2,
  nodes: [
    {
      id: "raw",
      kind: "source",
      descriptorId: "source.dataset",
      specVersion: 1,
      config: { datasetRef: "raw.orders" },
    },
    {
      id: "out",
      kind: "output",
      descriptorId: "output.dataset",
      specVersion: 1,
      config: { outputDatasetRef: "preview.orders" },
    },
  ],
  edges: [
    {
      id: "raw-to-out",
      sourceNodeId: "raw",
      sourcePortId: "dataset",
      targetNodeId: "out",
      targetPortId: "input",
    },
  ],
  layout: {},
  outputContract: {},
  tests: [],
  schedule: null,
};
await expectSdkCall("pipelines.nodeTypes", () => client.pipelines.nodeTypes(), {
  path: "/api/pipelines/node-types",
});
await expectSdkCall(
  "pipelines.trainedModels",
  () => client.pipelines.trainedModels(),
  { path: "/api/pipelines/trained-models" },
);
await expectSdkCall(
  "pipelines.branches.create",
  () => client.pipelines.branches.create({ pipelineId: "supply-chain", name: "join-orders" }, { idempotencyKey: "idem-pb" }),
  {
    path: "/api/pipelines/branches",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "idem-pb" },
    body: { pipelineId: "supply-chain", name: "join-orders" },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.branches.create",
  () => client.pipelines.branches.create({ pipelineId: "supply-chain", name: "join-orders" }),
  "pipelines.branches.create",
);
await expectSdkCall("pipelines.branches.list", () => client.pipelines.branches.list({ status: "open", limit: 10 }), {
  path: "/api/pipelines/branches?status=open&limit=10",
});
await expectSdkCall("pipelines.branches.get", () => client.pipelines.branches.get("branch/1"), {
  path: "/api/pipelines/branches/branch%2F1",
});
await expectSdkCall(
  "pipelines.branches.updateGraph",
  () => client.pipelines.branches.updateGraph("branch/1", { graph: pipelineGraph, expectedFingerprint: "fp-a" }),
  {
    path: "/api/pipelines/branches/branch%2F1/graph",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { graph: pipelineGraph, expectedFingerprint: "fp-a" },
  },
);
await expectSdkCall("pipelines.branches.diff", () => client.pipelines.branches.diff("branch/1"), {
  path: "/api/pipelines/branches/branch%2F1/diff",
});
await expectSdkCall(
  "pipelines.branches.rebase",
  () => client.pipelines.branches.rebase("branch/1", { expectedFingerprint: "fp-a" }),
  {
    path: "/api/pipelines/branches/branch%2F1/rebase",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { expectedFingerprint: "fp-a" },
  },
);
await expectSdkCall(
  "pipelines.branches.propose",
  () =>
    client.pipelines.branches.propose(
      "branch/1",
      { title: "Review graph", description: "Dataset output v1" },
      { idempotencyKey: "idem-propose" },
    ),
  {
    path: "/api/pipelines/branches/branch%2F1/propose",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "idem-propose" },
    body: { title: "Review graph", description: "Dataset output v1" },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.branches.propose",
  () => client.pipelines.branches.propose("branch/1", { title: "Review graph" }),
  "pipelines.branches.propose",
);
await expectSdkCall("pipelines.branches.abandon", () => client.pipelines.branches.abandon("branch/1"), {
  path: "/api/pipelines/branches/branch%2F1/abandon",
  method: "POST",
});
await expectSdkCall("pipelines.graph.validate", () => client.pipelines.graph.validate("branch/1"), {
  path: "/api/pipelines/branches/branch%2F1/validate",
});
await expectSdkCall("pipelines.graph.suggestCasts", () => client.pipelines.graph.suggestCasts("branch/1", "sql"), {
  path: "/api/pipelines/branches/branch%2F1/nodes/sql/casts",
});
await expectSdkCall(
  "pipelines.graph.previewNode",
  () => client.pipelines.graph.previewNode("branch/1", "sql", { limit: 25 }),
  {
    path: "/api/pipelines/branches/branch%2F1/nodes/sql/preview",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { limit: 25 },
  },
);
await expectSdkCall("pipelines.graph.stats", () => client.pipelines.graph.stats("branch/1", "sql", { limit: 25 }), {
  path: "/api/pipelines/branches/branch%2F1/nodes/sql/stats",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { limit: 25 },
});
await expectSdkCall("pipelines.graph.runTests", () => client.pipelines.graph.runTests("branch/1"), {
  path: "/api/pipelines/branches/branch%2F1/tests/run",
  method: "POST",
});
await expectSdkCall(
  "pipelines.previewRuns.create",
  () =>
    client.pipelines.previewRuns.create(
      "branch/1",
      { graph: pipelineGraphV2, targetNodeId: "out", limits: { tableRows: 50 } },
      { idempotencyKey: "idem-preview" },
    ),
  {
    path: "/api/pipelines/branches/branch%2F1/preview-runs",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "idem-preview" },
    body: { graph: pipelineGraphV2, targetNodeId: "out", limits: { tableRows: 50 } },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.previewRuns.create",
  () => client.pipelines.previewRuns.create("branch/1", { graph: pipelineGraphV2 }),
  "pipelines.previewRuns.create",
);
await expectSdkCall("pipelines.previewRuns.get", () => client.pipelines.previewRuns.get("preview/1"), {
  path: "/api/pipelines/preview-runs/preview%2F1",
});
await expectSdkCall("pipelines.previewRuns.cancel", () => client.pipelines.previewRuns.cancel("preview/1"), {
  path: "/api/pipelines/preview-runs/preview%2F1/cancel",
  method: "POST",
});
await expectSdkCall("pipelines.proposals.list", () => client.pipelines.proposals.list({ status: "approved", limit: 5 }), {
  path: "/api/pipelines/proposals?status=approved&limit=5",
});
await expectSdkCall("pipelines.proposals.get", () => client.pipelines.proposals.get("proposal/1"), {
  path: "/api/pipelines/proposals/proposal%2F1",
});
await expectSdkCall(
  "pipelines.proposals.assign",
  () => client.pipelines.proposals.assign("proposal/1", { assigneeUserId: "ops-1" }),
  {
    path: "/api/pipelines/proposals/proposal%2F1/assign",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { assigneeUserId: "ops-1" },
  },
);
await expectSdkCall(
  "pipelines.proposals.decide",
  () => client.pipelines.proposals.decide("proposal/1", { decision: "approve", comment: "ship it" }),
  {
    path: "/api/pipelines/proposals/proposal%2F1/decision",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { decision: "approve", comment: "ship it" },
  },
);
await expectSdkCall("pipelines.proposals.execute", () => client.pipelines.proposals.execute("proposal/1"), {
  path: "/api/pipelines/proposals/proposal%2F1/execute",
  method: "POST",
});
await expectSdkCall("pipelines.proposals.withdraw", () => client.pipelines.proposals.withdraw("proposal/1"), {
  path: "/api/pipelines/proposals/proposal%2F1/withdraw",
  method: "POST",
});
await expectSdkCall("pipelines.versions.list", () => client.pipelines.versions.list("supply-chain", { limit: 7 }), {
  path: "/api/pipelines/supply-chain/versions?limit=7",
});
await expectSdkCall("pipelines.versions.get", () => client.pipelines.versions.get("version/1"), {
  path: "/api/pipelines/versions/version%2F1",
});
await expectSdkCall(
  "pipelines.deployments.list",
  () => client.pipelines.deployments.list("supply-chain", { limit: 7 }),
  {
    path: "/api/pipelines/supply-chain/deployments?limit=7",
  },
);
await expectSdkCall(
  "pipelines.deploy",
  () =>
    client.pipelines.deploy(
      "supply-chain",
      "version/1",
      { options: { mode: "manual" } },
      { idempotencyKey: "idem-deploy" },
    ),
  {
    path: "/api/pipelines/supply-chain/deploy/version%2F1",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "idem-deploy" },
    body: { options: { mode: "manual" } },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.deploy",
  () => client.pipelines.deploy("supply-chain", "version/1", { options: { mode: "manual" } }),
  "pipelines.deploy",
);
await expectSdkCall(
  "pipelines.runs.start",
  () =>
    client.pipelines.runs.start(
      "supply-chain",
      { versionId: "version/1", parameters: { mode: "full" }, targetNodeIds: ["out"] },
      { idempotencyKey: "idem-pipeline-run" },
    ),
  {
    path: "/api/pipelines/supply-chain/runs",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "idem-pipeline-run" },
    body: { versionId: "version/1", parameters: { mode: "full" }, targetNodeIds: ["out"] },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.runs.start",
  () => client.pipelines.runs.start("supply-chain", { versionId: "version/1" }),
  "pipelines.runs.start",
);
await expectSdkCall("pipelines.runs.get", () => client.pipelines.runs.get("run/1"), {
  path: "/api/pipelines/runs/run%2F1",
});
await expectSdkCall("pipelines.runs.timeline", () => client.pipelines.runs.timeline("run/1"), {
  path: "/api/pipelines/runs/run%2F1/timeline",
});
await expectSdkCall("pipelines.runs.cancel", () => client.pipelines.runs.cancel("run/1"), {
  path: "/api/pipelines/runs/run%2F1/cancel",
  method: "POST",
});
await expectSdkCall(
  "pipelines.schedules.upsert",
  () =>
    client.pipelines.schedules.upsert(
      "supply-chain",
      {
        versionId: "version/1",
        schedule: {
          triggerType: "cron",
          timezone: "Asia/Seoul",
          cronExpression: "*/5 * * * *",
          autoPauseAfterFailures: 3,
        },
        enabled: true,
      },
      { idempotencyKey: "idem-pipeline-schedule" },
    ),
  {
    path: "/api/pipelines/supply-chain/schedule",
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "idem-pipeline-schedule" },
    body: {
      versionId: "version/1",
      schedule: {
        triggerType: "cron",
        timezone: "Asia/Seoul",
        cronExpression: "*/5 * * * *",
        autoPauseAfterFailures: 3,
      },
      enabled: true,
    },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.schedules.upsert",
  () =>
    client.pipelines.schedules.upsert("supply-chain", {
      versionId: "version/1",
      schedule: { triggerType: "interval", timezone: "UTC", intervalSeconds: 60 },
    }),
  "pipelines.schedules.upsert",
);
await expectSdkCall("pipelines.schedules.get", () => client.pipelines.schedules.get("supply-chain"), {
  path: "/api/pipelines/supply-chain/schedule",
});
await expectSdkCall(
  "pipelines.schedules.pause",
  () => client.pipelines.schedules.pause("supply-chain", { idempotencyKey: "idem-pipeline-schedule-pause" }),
  {
    path: "/api/pipelines/supply-chain/schedule/pause",
    method: "POST",
    headers: { "Idempotency-Key": "idem-pipeline-schedule-pause" },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.schedules.pause",
  () => client.pipelines.schedules.pause("supply-chain"),
  "pipelines.schedules.pause",
);
await expectSdkCall(
  "pipelines.schedules.resume",
  () => client.pipelines.schedules.resume("supply-chain", { idempotencyKey: "idem-pipeline-schedule-resume" }),
  {
    path: "/api/pipelines/supply-chain/schedule/resume",
    method: "POST",
    headers: { "Idempotency-Key": "idem-pipeline-schedule-resume" },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.schedules.resume",
  () => client.pipelines.schedules.resume("supply-chain"),
  "pipelines.schedules.resume",
);
await expectSdkCall(
  "pipelines.schedules.delete",
  () => client.pipelines.schedules.delete("supply-chain", { idempotencyKey: "idem-pipeline-schedule-delete" }),
  {
    path: "/api/pipelines/supply-chain/schedule",
    method: "DELETE",
    headers: { "Idempotency-Key": "idem-pipeline-schedule-delete" },
  },
);
assertMissingIdempotencyFailFast(
  "pipelines.schedules.delete",
  () => client.pipelines.schedules.delete("supply-chain"),
  "pipelines.schedules.delete",
);
await expectSdkCall("pipelines.schedules.previewDue", () => client.pipelines.schedules.previewDue({ maxRuns: 3 }), {
  path: "/api/pipelines/scheduler/due?maxRuns=3",
});
await expectSdkCall("pipelines.schedules.tick", () => client.pipelines.schedules.tick({ maxRuns: 3 }), {
  path: "/api/pipelines/scheduler/tick?maxRuns=3",
  method: "POST",
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
await expectSdkCall(
  "operations.observability.detect-and-record",
  () => client.operations.observability.detectAndRecord({ configs: [] }),
  {
    path: "/api/operations/observability/detect-and-record",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { configs: [] },
  },
);
await expectSdkCall(
  "operations.observability.list-incidents",
  () => client.operations.observability.listIncidents({ status: "open", limit: 25 }),
  {
    path: "/api/operations/observability/incidents?status=open&limit=25",
  },
);
await expectSdkCall(
  "operations.observability.acknowledge-incident",
  () => client.operations.observability.acknowledgeIncident("incident/raw orders"),
  {
    path: "/api/operations/observability/incidents/incident%2Fraw%20orders/acknowledge",
    method: "POST",
  },
);
await expectSdkCall(
  "operations.observability.resolve-incident",
  () => client.operations.observability.resolveIncident("incident/raw orders", { reason: "source recovered" }),
  {
    path: "/api/operations/observability/incidents/incident%2Fraw%20orders/resolve",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { reason: "source recovered" },
  },
);
await expectSdkCall("operations.backup-restore.preflight", () => client.operations.backupRestore.preflight({ backupId: "b1" }), {
  path: "/api/operations/backup-restore/preflight",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { backupId: "b1" },
});
await expectSdkCall(
  "operations.backup-restore.create-artifact",
  () => client.operations.backupRestore.createArtifact({ backupId: "b1" }),
  {
    path: "/api/operations/backup-restore/artifacts",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { backupId: "b1" },
  },
);
await expectSdkCall(
  "operations.backup-restore.restore-artifact",
  () =>
    client.operations.backupRestore.restoreArtifact({
      artifactRef: "/tmp/foundry-lite/backup.json",
      artifactHash: "sha256:backup-json",
      restoreId: "restore/1",
      validationId: "validation-1",
    }),
  {
    path: "/api/operations/backup-restore/artifacts/restore",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      artifactRef: "/tmp/foundry-lite/backup.json",
      artifactHash: "sha256:backup-json",
      restoreId: "restore/1",
      validationId: "validation-1",
    },
  },
);
await expectSdkCall(
  "operations.backup-restore.execute-artifact-restore",
  () =>
    client.operations.backupRestore.executeArtifactRestore({
      artifactRef: "/tmp/foundry-lite/backup.json",
      artifactHash: "sha256:backup-json",
      restoreId: "restore/1",
      validationId: "validation-1",
      runPostRestoreValidation: false,
    }),
  {
    path: "/api/operations/backup-restore/artifacts/restore/execute",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      artifactRef: "/tmp/foundry-lite/backup.json",
      artifactHash: "sha256:backup-json",
      restoreId: "restore/1",
      validationId: "validation-1",
      runPostRestoreValidation: false,
    },
  },
);
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
  "operations.backup-restore.post-restore-validation",
  () => client.operations.backupRestore.postRestoreValidation("restore/1", { validationId: "validation-1" }),
  {
    path: "/api/operations/backup-restore/restore-mode/restore%2F1/post-restore-validation",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { validationId: "validation-1" },
  },
);
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
await expectSdkCall("operations.recovery.overview", () => client.operations.backupRestore.recoveryOverview(), {
  path: "/api/operations/recovery/overview",
});
await expectSdkCall("operations.admin.overview", () => client.operations.admin.overview(), {
  path: "/api/operations/admin/overview",
});
await expectSdkCall("operations.admin.task-plan", () => client.operations.admin.taskPlan(), {
  path: "/api/operations/admin/task-plan",
});
await expectSdkCall(
  "operations.outbox.publish-pending",
  () => client.operations.outbox.publishPending({ streamName: "ops-outbox", limit: 2 }),
  {
    path: "/api/operations/outbox/publish",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { streamName: "ops-outbox", limit: 2 },
  },
);
await expectSdkCall("operations.runs.detail", () => client.operations.runs.detail("index", "run/1"), {
  path: "/api/operations/runs/index/run%2F1",
});
await expectSdkCall(
  "operations.runs.promptArtifact",
  () => client.operations.runs.promptArtifact("ai/run-1", "ai/run-1-compiled-prompt"),
  {
    path: "/api/operations/runs/ai/ai%2Frun-1/prompt-artifacts/ai%2Frun-1-compiled-prompt",
  },
);
await expectSdkCall("operations.lineage.get", () => client.operations.lineage.get("dataset-version/1"), {
  path: "/api/operations/lineage?resourceId=dataset-version%2F1",
});
await expectSdkCall("sources.list", () => client.sources.list(), {
  path: "/api/sources",
});
await expectSdkCall("sources.get", () => client.sources.get("erp/source"), {
  path: "/api/sources/erp%2Fsource",
});
await expectSdkCall(
  "sources.updateStatus",
  () =>
    client.sources.updateStatus(
      "erp/source",
      { status: "disabled", expectedConfigFingerprint: "sha256:source-active" },
      { idempotencyKey: "source-disable-key" },
    ),
  {
    path: "/api/sources/erp%2Fsource/status",
    method: "PATCH",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-disable-key" },
    body: { status: "disabled", expectedConfigFingerprint: "sha256:source-active" },
  },
);
await expectSdkCall(
  "sources.testConnection",
  () =>
    client.sources.testConnection(
      "erp/source",
      { expectedConfigFingerprint: "sha256:source-active" },
      { idempotencyKey: "source-connection-test-key" },
    ),
  {
    path: "/api/sources/erp%2Fsource/connection-tests",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-connection-test-key" },
    body: { expectedConfigFingerprint: "sha256:source-active" },
  },
);
await expectSdkCall(
  "sources.listConnectionTests",
  () => client.sources.listConnectionTests("erp/source", { limit: 5 }),
  { path: "/api/sources/erp%2Fsource/connection-tests?limit=5" },
);
await expectSdkCall(
  "sources.listEgressAttempts",
  () => client.sources.listEgressAttempts("erp/source", { limit: 5 }),
  { path: "/api/sources/erp%2Fsource/egress-attempts?limit=5" },
);
await expectSdkCall("sources.templates.list", () => client.sources.templates.list(), {
  path: "/api/sources/templates",
});
await expectSdkCall(
  "sources.credentials.create",
  () =>
    client.sources.credentials.create(
      {
        credentialName: "erp_db",
        displayName: "ERP DB",
        kind: "postgres_jdbc",
        authScheme: "database_url",
        secretValue: "sqlite:///customer-erp.db",
      },
      { idempotencyKey: "source-credential-key" },
    ),
  {
    path: "/api/sources/credentials",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-credential-key" },
    body: {
      credentialName: "erp_db",
      displayName: "ERP DB",
      kind: "postgres_jdbc",
      authScheme: "database_url",
      secretValue: "sqlite:///customer-erp.db",
    },
  },
);
await expectSdkCall("sources.credentials.list", () => client.sources.credentials.list(), {
  path: "/api/sources/credentials",
});
await expectSdkCall("sources.credentials.get", () => client.sources.credentials.get("erp/db"), {
  path: "/api/sources/credentials/erp%2Fdb",
});
await expectSdkCall(
  "sources.agents.register",
  () =>
    client.sources.agents.register(
      {
        agentId: "customer_vpn_agent",
        displayName: "Customer VPN Agent",
        mode: "agent_proxy",
        capabilities: { jdbc: true, rest: true },
        networkSummary: { region: "customer-dmz" },
      },
      { idempotencyKey: "source-agent-key" },
    ),
  {
    path: "/api/sources/agents",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-agent-key" },
    body: {
      agentId: "customer_vpn_agent",
      displayName: "Customer VPN Agent",
      mode: "agent_proxy",
      capabilities: { jdbc: true, rest: true },
      networkSummary: { region: "customer-dmz" },
    },
  },
);
await expectSdkCall("sources.agents.list", () => client.sources.agents.list(), {
  path: "/api/sources/agents",
});
await expectSdkCall("sources.agents.heartbeat", () => client.sources.agents.heartbeat("customer_vpn_agent"), {
  path: "/api/sources/agents/customer_vpn_agent/heartbeat",
  method: "POST",
});
await expectSdkCall(
  "sources.networkPolicies.create",
  () =>
    client.sources.networkPolicies.create(
      {
        policyName: "customer_erp_vpn",
        displayName: "Customer ERP VPN",
        mode: "agent_proxy",
        agentId: "customer_vpn_agent",
        allowedHosts: ["erp.customer.local"],
      },
      { idempotencyKey: "source-network-key" },
    ),
  {
    path: "/api/sources/network-policies",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-network-key" },
    body: {
      policyName: "customer_erp_vpn",
      displayName: "Customer ERP VPN",
      mode: "agent_proxy",
      agentId: "customer_vpn_agent",
      allowedHosts: ["erp.customer.local"],
    },
  },
);
await expectSdkCall("sources.networkPolicies.list", () => client.sources.networkPolicies.list(), {
  path: "/api/sources/network-policies",
});
await expectSdkCall(
  "sources.exploration.run",
  () =>
    client.sources.exploration.run({
      sourceName: "customer_erp",
      sourceType: "postgres_jdbc",
      request: {
        databaseUrlSecretRef: "erp_db",
        tableName: "orders",
        checkpointColumn: "id",
        sampleLimit: 25,
      },
    }),
  {
    path: "/api/sources/explore",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      sourceName: "customer_erp",
      sourceType: "postgres_jdbc",
      request: {
        databaseUrlSecretRef: "erp_db",
        tableName: "orders",
        checkpointColumn: "id",
        sampleLimit: 25,
      },
    },
  },
);
await expectSdkCall(
  "sources.managedSyncs.create",
  () =>
    client.sources.managedSyncs.create(
      {
        syncName: "orders_incremental",
        sourceName: "customer_erp",
        displayName: "Orders incremental",
        sourceType: "postgres_jdbc",
        capability: "batch",
        targetDatasetRef: "raw.orders",
        mode: "APPEND",
        schedule: { mode: "manual" },
        configSummary: {
          databaseUrlSecretRef: "erp_db",
          tableName: "orders",
          checkpointColumn: "id",
          batchLimit: 5000,
        },
      },
      { idempotencyKey: "source-sync-create-key" },
    ),
  {
    path: "/api/sources/managed-syncs",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-sync-create-key" },
    body: {
      syncName: "orders_incremental",
      sourceName: "customer_erp",
      displayName: "Orders incremental",
      sourceType: "postgres_jdbc",
      capability: "batch",
      targetDatasetRef: "raw.orders",
      mode: "APPEND",
      schedule: { mode: "manual" },
      configSummary: {
        databaseUrlSecretRef: "erp_db",
        tableName: "orders",
        checkpointColumn: "id",
        batchLimit: 5000,
      },
    },
  },
);
await expectSdkCall("sources.managedSyncs.list", () => client.sources.managedSyncs.list(), {
  path: "/api/sources/managed-syncs",
});
await expectSdkCall("sources.managedSyncs.get", () => client.sources.managedSyncs.get("orders/incremental"), {
  path: "/api/sources/managed-syncs/orders%2Fincremental",
});
await expectSdkCall(
  "sources.managedSyncs.updateSchedule",
  () =>
    client.sources.managedSyncs.updateSchedule(
      "orders/incremental",
      {
        schedule: { mode: "interval", everySeconds: 3600, batchLimit: 100 },
        expectedConfigFingerprint: "sha256:sync-before",
      },
      { idempotencyKey: "sync-schedule-update-1" },
    ),
  {
    path: "/api/sources/managed-syncs/orders%2Fincremental/schedule",
    method: "PATCH",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "sync-schedule-update-1" },
    body: {
      schedule: { mode: "interval", everySeconds: 3600, batchLimit: 100 },
      expectedConfigFingerprint: "sha256:sync-before",
    },
  },
);
await expectSdkCall(
  "sources.managedSyncs.pauseSchedule",
  () =>
    client.sources.managedSyncs.pauseSchedule(
      "orders/incremental",
      { expectedConfigFingerprint: "sha256:sync-active" },
      { idempotencyKey: "sync-schedule-pause-1" },
    ),
  {
    path: "/api/sources/managed-syncs/orders%2Fincremental/schedule/pause",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "sync-schedule-pause-1" },
    body: { expectedConfigFingerprint: "sha256:sync-active" },
  },
);
await expectSdkCall(
  "sources.managedSyncs.resumeSchedule",
  () =>
    client.sources.managedSyncs.resumeSchedule(
      "orders/incremental",
      { expectedConfigFingerprint: "sha256:sync-paused" },
      { idempotencyKey: "sync-schedule-resume-1" },
    ),
  {
    path: "/api/sources/managed-syncs/orders%2Fincremental/schedule/resume",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "sync-schedule-resume-1" },
    body: { expectedConfigFingerprint: "sha256:sync-paused" },
  },
);
await expectSdkCall(
  "sources.managedSyncs.startRun",
  () =>
    client.sources.managedSyncs.startRun(
      "orders_incremental",
      { triggerType: "manual", batchLimit: 5000 },
      { idempotencyKey: "source-sync-run-key" },
    ),
  {
    path: "/api/sources/managed-syncs/orders_incremental/runs/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-sync-run-key" },
    body: { triggerType: "manual", batchLimit: 5000 },
  },
);
await expectSdkCall("sources.managedSyncs.listRuns", () => client.sources.managedSyncs.listRuns("orders_incremental"), {
  path: "/api/sources/managed-syncs/orders_incremental/runs",
});
await expectSdkCall("sources.managedSyncs.getRun", () => client.sources.managedSyncs.getRun("run/source/1"), {
  path: "/api/sources/managed-sync-runs/run%2Fsource%2F1",
});
await expectSdkCall(
  "sources.managedSyncs.startStream",
  () =>
    client.sources.managedSyncs.startStream(
      "kraken/live",
      { expectedConfigFingerprint: "sha256:kraken-stream" },
      { idempotencyKey: "source-stream-start-key" },
    ),
  {
    path: "/api/sources/managed-syncs/kraken%2Flive/stream/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-stream-start-key" },
    body: { expectedConfigFingerprint: "sha256:kraken-stream" },
  },
);
await expectSdkCall(
  "sources.managedSyncs.stopStream",
  () =>
    client.sources.managedSyncs.stopStream(
      "kraken/live",
      { expectedConfigFingerprint: "sha256:kraken-stream" },
      { idempotencyKey: "source-stream-stop-key" },
    ),
  {
    path: "/api/sources/managed-syncs/kraken%2Flive/stream/stop",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-stream-stop-key" },
    body: { expectedConfigFingerprint: "sha256:kraken-stream" },
  },
);
await expectSdkCall(
  "sources.managedSyncs.streamStatus",
  () => client.sources.managedSyncs.streamStatus("kraken/live"),
  { path: "/api/sources/managed-syncs/kraken%2Flive/stream/status" },
);
await expectSdkCall("sources.scheduler.previewDue", () => client.sources.scheduler.previewDue({ maxRuns: 25 }), {
  path: "/api/sources/scheduler/due?maxRuns=25",
});
await expectSdkCall("sources.scheduler.tick", () => client.sources.scheduler.tick({ maxRuns: 25 }), {
  path: "/api/sources/scheduler/tick",
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: { maxRuns: 25 },
});
await expectSdkCall(
  "sources.csv.upload",
  () =>
    client.sources.csv.upload(
      {
        sourceName: "orders_csv",
        displayName: "Orders CSV",
        datasetRef: "raw.orders",
        file: new Blob(["order_id,amount\n1,10\n"], { type: "text/csv" }),
        fileName: "orders.csv",
        syncName: "first-orders-csv",
        primaryKey: ["order_id"],
      },
      { idempotencyKey: "source-csv-key" },
    ),
  {
    path: "/api/sources/csv/uploads",
    method: "POST",
    headers: { "Idempotency-Key": "source-csv-key" },
    body: {
      kind: "formData",
      fields: {
        sourceName: "orders_csv",
        displayName: "Orders CSV",
        datasetRef: "raw.orders",
        syncName: "first-orders-csv",
        primaryKey: JSON.stringify(["order_id"]),
      },
      file: { name: "orders.csv", type: "text/csv", size: 21 },
    },
  },
);
await expectSdkCall(
  "sources.batchFiles.upload",
  () =>
    client.sources.batchFiles.upload(
      {
        sourceName: "erp_batch",
        displayName: "ERP Batch",
        syncName: "erp-batch-1",
        files: [
          {
            fileName: "orders.csv",
            datasetRef: "raw.orders",
            file: new Blob(["order_id\n1\n"], { type: "text/csv" }),
          },
          {
            fileName: "customers.csv",
            datasetRef: "raw.customers",
            file: new Blob(["customer_id\nc1\n"], { type: "text/csv" }),
          },
        ],
      },
      { idempotencyKey: "source-batch-key" },
    ),
  {
    path: "/api/sources/batch-files/uploads",
    method: "POST",
    headers: { "Idempotency-Key": "source-batch-key" },
    body: {
      kind: "formData",
      fields: {
        manifest: JSON.stringify({
          sourceName: "erp_batch",
          displayName: "ERP Batch",
          syncName: "erp-batch-1",
          files: [
            { fileName: "orders.csv", datasetRef: "raw.orders" },
            { fileName: "customers.csv", datasetRef: "raw.customers" },
          ],
        }),
      },
      files: [
        { name: "orders.csv", type: "text/csv", size: 11 },
        { name: "customers.csv", type: "text/csv", size: 15 },
      ],
    },
  },
);
await expectSdkCall(
  "sources.webhookListeners.create",
  () =>
    client.sources.webhookListeners.create(
      {
        sourceName: "orders_webhook",
        displayName: "Orders Listener",
        datasetRef: "raw.webhook_orders",
        connectorName: "erp",
        resourceName: "orders",
        signingSecretRef: "erp-webhook-secret",
      },
      { idempotencyKey: "source-webhook-key" },
    ),
  {
    path: "/api/sources/webhook-listeners",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-webhook-key" },
    body: {
      sourceName: "orders_webhook",
      displayName: "Orders Listener",
      datasetRef: "raw.webhook_orders",
      connectorName: "erp",
      resourceName: "orders",
      signingSecretRef: "erp-webhook-secret",
    },
  },
);
await expectSdkCall("sources.webhookListeners.get", () => client.sources.webhookListeners.get("orders_webhook"), {
  path: "/api/sources/webhook-listeners/orders_webhook",
});
await expectSdkCall(
  "sources.cdc.debezium.create",
  () =>
    client.sources.cdc.debezium.create(
      {
        sourceName: "orders_cdc",
        displayName: "Orders CDC",
        datasetRef: "raw.orders_cdc",
        streamName: "debezium.orders",
        topic: "db.public.orders",
        consumerGroup: "foundry-orders",
        secretRefs: { databasePasswordSecretRef: "orders-db-password" },
        primaryKey: ["order_id"],
      },
      { idempotencyKey: "source-cdc-create-key" },
    ),
  {
    path: "/api/sources/cdc/debezium",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-cdc-create-key" },
    body: {
      sourceName: "orders_cdc",
      displayName: "Orders CDC",
      datasetRef: "raw.orders_cdc",
      streamName: "debezium.orders",
      topic: "db.public.orders",
      consumerGroup: "foundry-orders",
      secretRefs: { databasePasswordSecretRef: "orders-db-password" },
      primaryKey: ["order_id"],
    },
  },
);
await expectSdkCall(
  "sources.cdc.debezium.operationPlan",
  () => client.sources.cdc.debezium.operationPlan("orders_cdc", { objectTypeApiName: "Order" }),
  {
    path: "/api/sources/cdc/debezium/orders_cdc/operation-plan?objectTypeApiName=Order",
  },
);
await expectSdkCall(
  "sources.cdc.debezium.startSync",
  () =>
    client.sources.cdc.debezium.startSync(
      "orders_cdc",
      { expectedConfigFingerprint: "sha256:abc", afterOffset: 4, limit: 10 },
      { idempotencyKey: "source-cdc-start-key" },
    ),
  {
    path: "/api/sources/cdc/debezium/orders_cdc/sync/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-cdc-start-key" },
    body: { expectedConfigFingerprint: "sha256:abc", afterOffset: 4, limit: 10 },
  },
);
await expectSdkCall(
  "sources.cdc.debezium.startObjectIndex",
  () =>
    client.sources.cdc.debezium.startObjectIndex(
      "orders_cdc",
      { objectTypeApiName: "Order", expectedConfigFingerprint: "sha256:abc", maxRowsPerVersion: 25 },
      { idempotencyKey: "source-cdc-index-key" },
    ),
  {
    path: "/api/sources/cdc/debezium/orders_cdc/object-index/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-cdc-index-key" },
    body: { objectTypeApiName: "Order", expectedConfigFingerprint: "sha256:abc", maxRowsPerVersion: 25 },
  },
);
await expectSdkCall(
  "sources.media.uploadAndCommit",
  () =>
    client.sources.media.uploadAndCommit(
      {
        sourceName: "invoice_media",
        displayName: "Invoice Media",
        mediaSetId: "media-set-1",
        logicalPath: "invoices/1.pdf",
        schemaType: "document",
        format: "pdf",
        file: new Blob(["PDF"], { type: "application/pdf" }),
        fileName: "invoice.pdf",
        securityEnvelope: { classification: "internal" },
      },
      { idempotencyKey: "source-media-key" },
    ),
  {
    path: "/api/sources/media/uploads",
    method: "POST",
    headers: { "Idempotency-Key": "source-media-key" },
    body: {
      kind: "formData",
      fields: {
        logicalPath: "invoices/1.pdf",
        schemaType: "document",
        format: "pdf",
        suppliedMimeType: "application/pdf",
        securityEnvelope: JSON.stringify({ classification: "internal" }),
        sourceName: "invoice_media",
        displayName: "Invoice Media",
        mediaSetId: "media-set-1",
      },
      file: { name: "invoice.pdf", type: "application/pdf", size: 3 },
    },
  },
);
await expectSdkCall(
  "sources.rest.createConnection",
  () =>
    client.sources.rest.createConnection(
      {
        connectorName: "erp_source",
        displayName: "ERP Source",
        baseUrl: "https://erp.example/api",
        auth: { mode: "none" },
      },
      { idempotencyKey: "source-rest-create-key" },
    ),
  {
    path: "/api/connectors/connections",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-rest-create-key" },
    body: {
      connectorName: "erp_source",
      displayName: "ERP Source",
      baseUrl: "https://erp.example/api",
      auth: { mode: "none" },
    },
  },
);
await expectSdkCall(
  "sources.rest.upsertResource",
  () =>
    client.sources.rest.upsertResource(
      "erp_source",
      "orders",
      { datasetRef: "raw.source_orders", resourcePath: "/orders", primaryKey: ["order_id"] },
      { idempotencyKey: "source-rest-upsert-key" },
    ),
  {
    path: "/api/connectors/connections/erp_source/resources/orders",
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-rest-upsert-key" },
    body: { datasetRef: "raw.source_orders", resourcePath: "/orders", primaryKey: ["order_id"] },
  },
);
await expectSdkCall("sources.rest.test", () => client.sources.rest.test("erp_source", "orders"), {
  path: "/api/connectors/connections/erp_source/resources/orders/test",
  method: "POST",
});
await expectSdkCall(
  "sources.rest.startSync",
  () =>
    client.sources.rest.startSync(
      "erp_source",
      "orders",
      { syncName: "source-rest-orders" },
      { idempotencyKey: "source-rest-start-key" },
    ),
  {
    path: "/api/connectors/connections/erp_source/resources/orders/sync/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "source-rest-start-key" },
    body: { syncName: "source-rest-orders" },
  },
);
assertMissingIdempotencyFailFast(
  "sources.credentials.create",
  () =>
    client.sources.credentials.create({
      credentialName: "erp_db",
      displayName: "ERP DB",
      kind: "postgres_jdbc",
      authScheme: "database_url",
      secretValue: "sqlite:///customer-erp.db",
    }),
  "sources.credentials.create",
);
assertMissingIdempotencyFailFast(
  "sources.agents.register",
  () =>
    client.sources.agents.register({
      agentId: "customer_vpn_agent",
      displayName: "Customer VPN Agent",
      mode: "agent_proxy",
    }),
  "sources.agents.register",
);
assertMissingIdempotencyFailFast(
  "sources.networkPolicies.create",
  () =>
    client.sources.networkPolicies.create({
      policyName: "customer_erp_vpn",
      displayName: "Customer ERP VPN",
      mode: "agent_proxy",
      agentId: "customer_vpn_agent",
    }),
  "sources.networkPolicies.create",
);
assertMissingIdempotencyFailFast(
  "sources.updateStatus",
  () =>
    client.sources.updateStatus("erp/source", {
      status: "disabled",
      expectedConfigFingerprint: "sha256:source-active",
    }),
  "sources.updateStatus",
);
assertMissingIdempotencyFailFast(
  "sources.testConnection",
  () =>
    client.sources.testConnection("erp/source", {
      expectedConfigFingerprint: "sha256:source-active",
    }),
  "sources.testConnection",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.create",
  () =>
    client.sources.managedSyncs.create({
      syncName: "orders_incremental",
      sourceName: "customer_erp",
      displayName: "Orders incremental",
      sourceType: "postgres_jdbc",
      capability: "batch",
      targetDatasetRef: "raw.orders",
      configSummary: { databaseUrlSecretRef: "erp_db", tableName: "orders" },
    }),
  "sources.managedSyncs.create",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.startRun",
  () => client.sources.managedSyncs.startRun("orders_incremental"),
  "sources.managedSyncs.startRun",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.startStream",
  () =>
    client.sources.managedSyncs.startStream("kraken_live", {
      expectedConfigFingerprint: "sha256:kraken-stream",
    }),
  "sources.managedSyncs.startStream",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.stopStream",
  () =>
    client.sources.managedSyncs.stopStream("kraken_live", {
      expectedConfigFingerprint: "sha256:kraken-stream",
    }),
  "sources.managedSyncs.stopStream",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.updateSchedule",
  () =>
    client.sources.managedSyncs.updateSchedule("orders_incremental", {
      schedule: { mode: "manual" },
      expectedConfigFingerprint: "sha256:sync-before",
    }),
  "sources.managedSyncs.updateSchedule",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.pauseSchedule",
  () =>
    client.sources.managedSyncs.pauseSchedule("orders_incremental", {
      expectedConfigFingerprint: "sha256:sync-active",
    }),
  "sources.managedSyncs.pauseSchedule",
);
assertMissingIdempotencyFailFast(
  "sources.managedSyncs.resumeSchedule",
  () =>
    client.sources.managedSyncs.resumeSchedule("orders_incremental", {
      expectedConfigFingerprint: "sha256:sync-paused",
    }),
  "sources.managedSyncs.resumeSchedule",
);
assertMissingIdempotencyFailFast(
  "sources.csv.upload",
  () =>
    client.sources.csv.upload({
      sourceName: "orders_csv",
      displayName: "Orders CSV",
      datasetRef: "raw.orders",
      file: new Blob(["a"], { type: "text/csv" }),
    }),
  "sources.csv.upload",
);
assertMissingIdempotencyFailFast(
  "sources.batchFiles.upload",
  () =>
    client.sources.batchFiles.upload({
      sourceName: "erp_batch",
      displayName: "ERP Batch",
      files: [{ fileName: "orders.csv", datasetRef: "raw.orders", file: new Blob(["a"], { type: "text/csv" }) }],
    }),
  "sources.batchFiles.upload",
);
assertMissingIdempotencyFailFast(
  "sources.webhookListeners.create",
  () =>
    client.sources.webhookListeners.create({
      sourceName: "orders_webhook",
      displayName: "Orders Listener",
      datasetRef: "raw.webhook_orders",
      connectorName: "erp",
      resourceName: "orders",
      signingSecretRef: "erp-webhook-secret",
    }),
  "sources.webhookListeners.create",
);
assertMissingIdempotencyFailFast(
  "sources.cdc.debezium.create",
  () =>
    client.sources.cdc.debezium.create({
      sourceName: "orders_cdc",
      displayName: "Orders CDC",
      datasetRef: "raw.orders_cdc",
      streamName: "debezium.orders",
      topic: "db.public.orders",
    }),
  "sources.cdc.debezium.create",
);
assertMissingIdempotencyFailFast(
  "sources.cdc.debezium.startSync",
  () => client.sources.cdc.debezium.startSync("orders_cdc"),
  "sources.cdc.debezium.startSync",
);
assertMissingIdempotencyFailFast(
  "sources.cdc.debezium.startObjectIndex",
  () => client.sources.cdc.debezium.startObjectIndex("orders_cdc"),
  "sources.cdc.debezium.startObjectIndex",
);
assertMissingIdempotencyFailFast(
  "sources.media.uploadAndCommit",
  () =>
    client.sources.media.uploadAndCommit({
      sourceName: "invoice_media",
      displayName: "Invoice Media",
      mediaSetId: "media-set-1",
      logicalPath: "invoices/1.pdf",
      schemaType: "document",
      format: "pdf",
      file: new Blob(["PDF"], { type: "application/pdf" }),
    }),
  "sources.media.uploadAndCommit",
);
assertMissingIdempotencyFailFast(
  "sources.rest.createConnection",
  () =>
    client.sources.rest.createConnection({
      connectorName: "erp_source",
      displayName: "ERP Source",
      baseUrl: "https://erp.example/api",
      auth: { mode: "none" },
    }),
  "sources.rest.createConnection",
);
assertMissingIdempotencyFailFast(
  "sources.rest.upsertResource",
  () => client.sources.rest.upsertResource("erp_source", "orders", {
    datasetRef: "raw.source_orders",
    resourcePath: "/orders",
  }),
  "sources.rest.upsertResource",
);
assertMissingIdempotencyFailFast(
  "sources.rest.startSync",
  () => client.sources.rest.startSync("erp_source", "orders"),
  "sources.rest.startSync",
);
await expectSdkCall(
  "connectors.connections.create",
  () =>
    client.connectors.connections.create(
      {
        connectorName: "erp",
        displayName: "ERP",
        baseUrl: "https://erp.example/api",
        auth: { mode: "bearer", tokenSecretRef: "erp-token" },
        rateLimitPerMinute: 60,
        allowPrivateNetwork: false,
      },
      { idempotencyKey: "connector-create-key" },
    ),
  {
    path: "/api/connectors/connections",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "connector-create-key" },
    body: {
      connectorName: "erp",
      displayName: "ERP",
      baseUrl: "https://erp.example/api",
      auth: { mode: "bearer", tokenSecretRef: "erp-token" },
      rateLimitPerMinute: 60,
      allowPrivateNetwork: false,
    },
  },
);
await expectSdkCall("connectors.connections.list", () => client.connectors.connections.list(), {
  path: "/api/connectors/connections",
});
await expectSdkCall("connectors.connections.get", () => client.connectors.connections.get("erp/main"), {
  path: "/api/connectors/connections/erp%2Fmain",
});
await expectSdkCall(
  "connectors.connections.update",
  () =>
    client.connectors.connections.update(
      "erp/main",
      { status: "disabled" },
      { idempotencyKey: "connector-update-key" },
    ),
  {
    path: "/api/connectors/connections/erp%2Fmain",
    method: "PATCH",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "connector-update-key" },
    body: { status: "disabled" },
  },
);
await expectSdkCall(
  "connectors.resources.upsert",
  () =>
    client.connectors.resources.upsert(
      "erp",
      "orders",
      {
        datasetRef: "raw.erp_orders",
        resourcePath: "/orders",
        pagination: { strategy: "cursor", itemsPath: "items" },
        schemaColumns: ["order_id", "amount"],
        primaryKey: ["order_id"],
      },
      { idempotencyKey: "connector-resource-upsert-key" },
    ),
  {
    path: "/api/connectors/connections/erp/resources/orders",
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "connector-resource-upsert-key" },
    body: {
      datasetRef: "raw.erp_orders",
      resourcePath: "/orders",
      pagination: { strategy: "cursor", itemsPath: "items" },
      schemaColumns: ["order_id", "amount"],
      primaryKey: ["order_id"],
    },
  },
);
await expectSdkCall("connectors.resources.test", () => client.connectors.resources.test("erp", "orders"), {
  path: "/api/connectors/connections/erp/resources/orders/test",
  method: "POST",
});
await expectSdkCall(
  "connectors.resources.start-sync",
  () =>
    client.connectors.resources.startSync(
      "erp",
      "orders",
      { syncName: "erp-orders-first-sync" },
      { idempotencyKey: "connector-start-sync-key" },
    ),
  {
    path: "/api/connectors/connections/erp/resources/orders/sync/start",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "connector-start-sync-key" },
    body: { syncName: "erp-orders-first-sync" },
  },
);
assertMissingIdempotencyFailFast(
  "connectors.connections.create",
  () =>
    client.connectors.connections.create({
      connectorName: "erp",
      displayName: "ERP",
      baseUrl: "https://erp.example/api",
      auth: { mode: "bearer", tokenSecretRef: "erp-token" },
    }),
  "connectors.connections.create",
);
assertMissingIdempotencyFailFast(
  "connectors.connections.update",
  () => client.connectors.connections.update("erp", { status: "disabled" }),
  "connectors.connections.update",
);
assertMissingIdempotencyFailFast(
  "connectors.resources.upsert",
  () =>
    client.connectors.resources.upsert("erp", "orders", {
      datasetRef: "raw.erp_orders",
      resourcePath: "/orders",
    }),
  "connectors.resources.upsert",
);
assertMissingIdempotencyFailFast(
  "connectors.resources.start-sync",
  () => client.connectors.resources.startSync("erp", "orders"),
  "connectors.resources.startSync",
);
await expectSdkCall("resources.projects.list", () => client.resources.projects.list(), {
  path: "/api/projects",
});
await expectSdkCall(
  "resources.projects.create",
  () =>
    client.resources.projects.create(
      {
        displayName: "Operations",
        description: "Operator-owned project",
        metadata: { costCenter: "ops" },
      },
      { idempotencyKey: "project-create-key" },
    ),
  {
    path: "/api/projects",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "project-create-key" },
    body: {
      displayName: "Operations",
      description: "Operator-owned project",
      metadata: { costCenter: "ops" },
    },
  },
);
await expectSdkCall("resources.projects.get", () => client.resources.projects.get("project_1"), {
  path: "/api/projects/project_1",
});
await expectSdkCall(
  "resources.projects.listGrants",
  () => client.resources.projects.listGrants("project_1"),
  {
    path: "/api/projects/project_1/grants",
  },
);
await expectSdkCall(
  "resources.projects.upsertGrant",
  () =>
    client.resources.projects.upsertGrant(
      "project_1",
      "role",
      "viewer",
      { role: "viewer" },
      { idempotencyKey: "grant-upsert-key" },
    ),
  {
    path: "/api/projects/project_1/grants/role/viewer",
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "grant-upsert-key" },
    body: { role: "viewer" },
  },
);
await expectSdkCall(
  "resources.folders.list",
  () => client.resources.folders.list("project_1", { includeTrashed: true }),
  {
    path: "/api/projects/project_1/folders?includeTrashed=true",
  },
);
await expectSdkCall(
  "resources.folders.create",
  () =>
    client.resources.folders.create(
      "project_1",
      { displayName: "Raw", parentFolderId: null, metadata: { zone: "landing" } },
      { idempotencyKey: "folder-create-key" },
    ),
  {
    path: "/api/projects/project_1/folders",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "folder-create-key" },
    body: { displayName: "Raw", parentFolderId: null, metadata: { zone: "landing" } },
  },
);
await expectSdkCall(
  "resources.folders.move",
  () =>
    client.resources.folders.move(
      "project_1",
      "folder_1",
      { parentFolderId: "folder_parent" },
      { idempotencyKey: "folder-move-key" },
    ),
  {
    path: "/api/projects/project_1/folders/folder_1/move",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "folder-move-key" },
    body: { parentFolderId: "folder_parent" },
  },
);
await expectSdkCall(
  "resources.folders.trash",
  () => client.resources.folders.trash("project_1", "folder_1", { idempotencyKey: "folder-trash-key" }),
  {
    path: "/api/projects/project_1/folders/folder_1/trash",
    method: "POST",
    headers: { "Idempotency-Key": "folder-trash-key" },
  },
);
await expectSdkCall(
  "resources.folders.restore",
  () => client.resources.folders.restore("project_1", "folder_1", { idempotencyKey: "folder-restore-key" }),
  {
    path: "/api/projects/project_1/folders/folder_1/restore",
    method: "POST",
    headers: { "Idempotency-Key": "folder-restore-key" },
  },
);
await expectSdkCall(
  "resources.items.search",
  () => client.resources.items.search({ projectId: "project_1", folderId: "folder_1", includeTrashed: true }),
  {
    path: "/api/resources?projectId=project_1&folderId=folder_1&includeTrashed=true",
  },
);
await expectSdkCall(
  "resources.items.get",
  () => client.resources.items.get("ri.foundry-lite.dataset.abc"),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc",
  },
);
await expectSdkCall(
  "resources.items.register",
  () =>
    client.resources.items.register(
      {
        resourceType: "dataset",
        displayName: "orders",
        projectId: "project_1",
        folderId: "folder_1",
        sourceSurface: "dataset",
        sourceRef: "raw.orders",
        operationsPath: "/operations/datasets/raw.orders",
        metadata: { owner: "data" },
      },
      { idempotencyKey: "resource-register-key" },
    ),
  {
    path: "/api/resources/register",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "resource-register-key" },
    body: {
      resourceType: "dataset",
      displayName: "orders",
      projectId: "project_1",
      folderId: "folder_1",
      sourceSurface: "dataset",
      sourceRef: "raw.orders",
      operationsPath: "/operations/datasets/raw.orders",
      metadata: { owner: "data" },
    },
  },
);
await expectSdkCall(
  "resources.items.move",
  () =>
    client.resources.items.move(
      "ri.foundry-lite.dataset.abc",
      { projectId: "project_2", folderId: null },
      { idempotencyKey: "resource-move-key" },
    ),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc/move",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "resource-move-key" },
    body: { projectId: "project_2", folderId: null },
  },
);
await expectSdkCall(
  "resources.items.trash",
  () => client.resources.items.trash("ri.foundry-lite.dataset.abc", { idempotencyKey: "resource-trash-key" }),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc/trash",
    method: "POST",
    headers: { "Idempotency-Key": "resource-trash-key" },
  },
);
await expectSdkCall(
  "resources.items.restore",
  () => client.resources.items.restore("ri.foundry-lite.dataset.abc", { idempotencyKey: "resource-restore-key" }),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc/restore",
    method: "POST",
    headers: { "Idempotency-Key": "resource-restore-key" },
  },
);
await expectSdkCall(
  "resources.favorites.set",
  () => client.resources.favorites.set("ri.foundry-lite.dataset.abc", { idempotencyKey: "favorite-set-key" }),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc/favorite",
    method: "PUT",
    headers: { "Idempotency-Key": "favorite-set-key" },
  },
);
await expectSdkCall(
  "resources.favorites.delete",
  () => client.resources.favorites.delete("ri.foundry-lite.dataset.abc", { idempotencyKey: "favorite-delete-key" }),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc/favorite",
    method: "DELETE",
    headers: { "Idempotency-Key": "favorite-delete-key" },
  },
);
await expectSdkCall(
  "resources.trash.list",
  () => client.resources.trash.list({ projectId: "project_1" }),
  {
    path: "/api/resources?projectId=project_1&includeTrashed=true",
  },
);
await expectSdkCall(
  "resources.trash.restore",
  () => client.resources.trash.restore("ri.foundry-lite.dataset.abc", { idempotencyKey: "trash-restore-key" }),
  {
    path: "/api/resources/ri.foundry-lite.dataset.abc/restore",
    method: "POST",
    headers: { "Idempotency-Key": "trash-restore-key" },
  },
);
await expectSdkCall(
  "resources.admin.reconcile",
  () =>
    client.resources.admin.reconcile(
      { projectId: "project_1" },
      { idempotencyKey: "resources-reconcile-key" },
    ),
  {
    path: "/api/resources/reconcile",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "resources-reconcile-key" },
    body: { projectId: "project_1" },
  },
);
assertMissingIdempotencyFailFast(
  "resources.projects.create",
  () => client.resources.projects.create({ displayName: "Operations" }),
  "resources.projects.create",
);
assertMissingIdempotencyFailFast(
  "resources.projects.upsertGrant",
  () => client.resources.projects.upsertGrant("project_1", "role", "viewer", { role: "viewer" }),
  "resources.projects.upsertGrant",
);
assertMissingIdempotencyFailFast(
  "resources.folders.create",
  () => client.resources.folders.create("project_1", { displayName: "Raw" }),
  "resources.folders.create",
);
assertMissingIdempotencyFailFast(
  "resources.folders.move",
  () => client.resources.folders.move("project_1", "folder_1", { parentFolderId: null }),
  "resources.folders.move",
);
assertMissingIdempotencyFailFast(
  "resources.folders.trash",
  () => client.resources.folders.trash("project_1", "folder_1"),
  "resources.folders.trash",
);
assertMissingIdempotencyFailFast(
  "resources.folders.restore",
  () => client.resources.folders.restore("project_1", "folder_1"),
  "resources.folders.restore",
);
assertMissingIdempotencyFailFast(
  "resources.items.register",
  () =>
    client.resources.items.register({
      resourceType: "dataset",
      displayName: "orders",
      sourceSurface: "dataset",
      sourceRef: "raw.orders",
    }),
  "resources.items.register",
);
assertMissingIdempotencyFailFast(
  "resources.items.move",
  () => client.resources.items.move("ri.foundry-lite.dataset.abc", { projectId: "project_2" }),
  "resources.items.move",
);
assertMissingIdempotencyFailFast(
  "resources.items.trash",
  () => client.resources.items.trash("ri.foundry-lite.dataset.abc"),
  "resources.items.trash",
);
assertMissingIdempotencyFailFast(
  "resources.items.restore",
  () => client.resources.items.restore("ri.foundry-lite.dataset.abc"),
  "resources.items.restore",
);
assertMissingIdempotencyFailFast(
  "resources.favorites.set",
  () => client.resources.favorites.set("ri.foundry-lite.dataset.abc"),
  "resources.favorites.set",
);
assertMissingIdempotencyFailFast(
  "resources.favorites.delete",
  () => client.resources.favorites.delete("ri.foundry-lite.dataset.abc"),
  "resources.favorites.delete",
);
assertMissingIdempotencyFailFast(
  "resources.trash.restore",
  () => client.resources.trash.restore("ri.foundry-lite.dataset.abc"),
  "resources.trash.restore",
);
assertMissingIdempotencyFailFast(
  "resources.admin.reconcile",
  () => client.resources.admin.reconcile({ projectId: "project_1" }),
  "resources.admin.reconcile",
);
await expectSdkCall(
  "auth.osdkOAuth.authorize",
  () =>
    client.auth.osdkOAuth.authorize({
      clientId: "orders-web",
      redirectUri: "https://orders.example.test/callback",
      codeChallenge: "challenge",
      scopes: ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
      state: "abc",
    }),
  {
    path:
      "/api/auth/osdk/oauth/authorize?clientId=orders-web&redirectUri=https%3A%2F%2Forders.example.test%2Fcallback" +
      "&codeChallenge=challenge&codeChallengeMethod=S256" +
      "&scope=osdk%3Aobject%3AOrder%3Aread+osdk%3Aobject%3AOrder%3Asubscribe&state=abc",
  },
);
await expectSdkCall(
  "auth.osdkOAuth.token",
  () =>
    client.auth.osdkOAuth.token({
      clientId: "orders-web",
      code: "code-1",
      redirectUri: "https://orders.example.test/callback",
      codeVerifier: "verifier",
    }),
  {
    path: "/api/auth/osdk/oauth/token",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      clientId: "orders-web",
      code: "code-1",
      redirectUri: "https://orders.example.test/callback",
      codeVerifier: "verifier",
    },
  },
);
await expectSdkCall(
  "auth.osdkOAuth.refresh",
  () => client.auth.osdkOAuth.refresh({ refreshToken: "refresh-1" }),
  {
    path: "/api/auth/osdk/oauth/refresh",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { refreshToken: "refresh-1" },
  },
);
await expectSdkCall(
  "auth.osdkOAuth.revoke",
  () => client.auth.osdkOAuth.revoke({ refreshToken: "refresh-2" }),
  {
    path: "/api/auth/osdk/oauth/revoke",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { refreshToken: "refresh-2" },
  },
);
await expectSdkCall(
  "developerConsole.osdkApplications.create",
  () =>
    client.developerConsole.osdkApplications.create(
      {
        appId: "app/orders",
        displayName: "Orders App",
        description: "Order workflow OSDK app",
        clientId: "orders-web-client",
        resources: [
          {
            resourceType: "object",
            resourceApiName: "Order",
            scopes: ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
          },
        ],
      },
      { idempotencyKey: "osdk-app-create-key" },
    ),
  {
    path: "/api/developer-console/osdk-applications",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-app-create-key" },
    body: {
      appId: "app/orders",
      displayName: "Orders App",
      description: "Order workflow OSDK app",
      clientId: "orders-web-client",
      resources: [
        {
          resourceType: "object",
          resourceApiName: "Order",
          scopes: ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
        },
      ],
    },
  },
);
await expectSdkCall("developerConsole.osdkApplications.list", () => client.developerConsole.osdkApplications.list(), {
  path: "/api/developer-console/osdk-applications",
});
await expectSdkCall(
  "developerConsole.osdkApplications.get",
  () => client.developerConsole.osdkApplications.get("app/orders"),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders",
  },
);
await expectSdkCall(
  "developerConsole.osdkApplications.updateResources",
  () =>
    client.developerConsole.osdkApplications.updateResources(
      "app/orders",
      {
        resources: [
          { resourceType: "object", resourceApiName: "Order", scopes: ["osdk:object:Order:read"] },
          { resourceType: "action", resourceApiName: "ApproveOrder", scopes: ["osdk:action:ApproveOrder:execute"] },
        ],
      },
      { idempotencyKey: "osdk-app-resources-key" },
    ),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/resources",
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-app-resources-key" },
    body: {
      resources: [
        { resourceType: "object", resourceApiName: "Order", scopes: ["osdk:object:Order:read"] },
        { resourceType: "action", resourceApiName: "ApproveOrder", scopes: ["osdk:action:ApproveOrder:execute"] },
      ],
    },
  },
);
await expectSdkCall(
  "developerConsole.osdkApplications.createClient",
  () =>
    client.developerConsole.osdkApplications.createClient(
      "app/orders",
      {
        clientId: "orders-web",
        redirectUris: ["https://orders.example.test/callback"],
        allowedScopes: ["osdk:object:Order:read"],
        accessTokenTtlSeconds: 600,
        refreshTokenTtlSeconds: 3600,
      },
      { idempotencyKey: "osdk-client-create-key" },
    ),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/clients",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-client-create-key" },
    body: {
      clientId: "orders-web",
      redirectUris: ["https://orders.example.test/callback"],
      allowedScopes: ["osdk:object:Order:read"],
      accessTokenTtlSeconds: 600,
      refreshTokenTtlSeconds: 3600,
    },
  },
);
await expectSdkCall(
  "developerConsole.osdkApplications.listClients",
  () => client.developerConsole.osdkApplications.listClients("app/orders"),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/clients",
  },
);
await expectSdkCall(
  "developerConsole.osdkApplications.updateClient",
  () =>
    client.developerConsole.osdkApplications.updateClient(
      "app/orders",
      "client-row/1",
      {
        clientId: "orders-web",
        redirectUris: ["https://orders.example.test/new-callback"],
        allowedScopes: ["osdk:object:Order:read"],
        status: "active",
      },
      { idempotencyKey: "osdk-client-update-key" },
    ),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/clients/client-row%2F1",
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-client-update-key" },
    body: {
      clientId: "orders-web",
      redirectUris: ["https://orders.example.test/new-callback"],
      allowedScopes: ["osdk:object:Order:read"],
      status: "active",
    },
  },
);
await expectSdkCall(
  "developerConsole.osdkApplications.deactivateClient",
  () =>
    client.developerConsole.osdkApplications.deactivateClient("app/orders", "client-row/1", {
      idempotencyKey: "osdk-client-deactivate-key",
    }),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/clients/client-row%2F1/deactivate",
    method: "POST",
    headers: { "Idempotency-Key": "osdk-client-deactivate-key" },
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.create",
  () =>
    client.developerConsole.sdkVersions.create(
      "app/orders",
      {
        language: "typescript",
        packageName: "@acme/orders-osdk",
        semver: "0.2.0",
        requestedBump: "minor",
      },
      { idempotencyKey: "osdk-sdk-version-key" },
    ),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-versions",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-sdk-version-key" },
    body: {
      language: "typescript",
      packageName: "@acme/orders-osdk",
      semver: "0.2.0",
      requestedBump: "minor",
    },
  },
);
await expectSdkCall("developerConsole.sdkVersions.list", () => client.developerConsole.sdkVersions.list("app/orders"), {
  path: "/api/developer-console/osdk-applications/app%2Forders/sdk-versions",
});
await expectSdkCall(
  "developerConsole.sdkVersions.get",
  () => client.developerConsole.sdkVersions.get("app/orders", "sdk-version/1"),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-versions/sdk-version%2F1",
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.artifact",
  () => client.developerConsole.sdkVersions.artifact("app/orders", "sdk-version/1", "typescript"),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-versions/sdk-version%2F1/artifacts/typescript",
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.promote",
  () =>
    client.developerConsole.sdkVersions.promote("app/orders", "sdk-version/1", "stable", {
      idempotencyKey: "osdk-sdk-promote-key",
    }),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-versions/sdk-version%2F1/channels/stable",
    method: "POST",
    headers: { "Idempotency-Key": "osdk-sdk-promote-key" },
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.channels",
  () => client.developerConsole.sdkVersions.channels("app/orders", { language: "typescript" }),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-release-channels?language=typescript",
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.createCompatibilityWindow",
  () =>
    client.developerConsole.sdkVersions.createCompatibilityWindow(
      "app/orders",
      {
        fromVersionId: "sdk-version/1",
        toVersionId: "sdk-version/2",
        supportedUntil: "2099-01-01T00:00:00+00:00",
      },
      { idempotencyKey: "osdk-sdk-window-key" },
    ),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-compatibility-windows",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-sdk-window-key" },
    body: {
      fromVersionId: "sdk-version/1",
      toVersionId: "sdk-version/2",
      supportedUntil: "2099-01-01T00:00:00+00:00",
    },
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.compatibilityWindows",
  () => client.developerConsole.sdkVersions.compatibilityWindows("app/orders"),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-compatibility-windows",
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.installMetadata",
  () => client.developerConsole.sdkVersions.installMetadata("app/orders"),
  {
    path: "/api/developer-console/osdk-applications/app%2Forders/sdk-install-metadata",
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.downloadToken",
  () =>
    client.developerConsole.sdkVersions.downloadToken(
      "app/orders",
      "sdk-version/1",
      "typescript",
      { ttlSeconds: 60 },
      { idempotencyKey: "osdk-sdk-download-key" },
    ),
  {
    path:
      "/api/developer-console/osdk-applications/app%2Forders/sdk-versions/" +
      "sdk-version%2F1/artifacts/typescript/download-token",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-sdk-download-key" },
    body: { ttlSeconds: 60 },
  },
);
await expectSdkCall(
  "developerConsole.sdkVersions.artifactByDownloadToken",
  () => client.developerConsole.sdkVersions.artifactByDownloadToken("download/token"),
  {
    path: "/api/developer-console/osdk-release-artifacts/download/download%2Ftoken",
  },
);
assertMissingIdempotencyFailFast(
  "developerConsole.osdkApplications.create",
  () =>
    client.developerConsole.osdkApplications.create({
      appId: "app/orders",
      displayName: "Orders App",
      resources: [],
    }),
  "developerConsole.osdkApplications.create",
);
assertMissingIdempotencyFailFast(
  "developerConsole.osdkApplications.updateResources",
  () =>
    client.developerConsole.osdkApplications.updateResources("app/orders", {
      resources: [{ resourceType: "object", resourceApiName: "Order", scopes: ["osdk:object:Order:read"] }],
    }),
  "developerConsole.osdkApplications.updateResources",
);
assertMissingIdempotencyFailFast(
  "developerConsole.sdkVersions.create",
  () => client.developerConsole.sdkVersions.create("app/orders", { language: "python", requestedBump: "minor" }),
  "developerConsole.sdkVersions.create",
);
assertMissingIdempotencyFailFast(
  "developerConsole.osdkApplications.createClient",
  () =>
    client.developerConsole.osdkApplications.createClient("app/orders", {
      clientId: "orders-web",
      redirectUris: ["https://orders.example.test/callback"],
      allowedScopes: ["osdk:object:Order:read"],
    }),
  "developerConsole.osdkApplications.createClient",
);
assertMissingIdempotencyFailFast(
  "developerConsole.osdkApplications.updateClient",
  () =>
    client.developerConsole.osdkApplications.updateClient("app/orders", "client-row/1", {
      clientId: "orders-web",
      redirectUris: ["https://orders.example.test/callback"],
      allowedScopes: ["osdk:object:Order:read"],
    }),
  "developerConsole.osdkApplications.updateClient",
);
assertMissingIdempotencyFailFast(
  "developerConsole.osdkApplications.deactivateClient",
  () => client.developerConsole.osdkApplications.deactivateClient("app/orders", "client-row/1"),
  "developerConsole.osdkApplications.deactivateClient",
);
assertMissingIdempotencyFailFast(
  "developerConsole.sdkVersions.promote",
  () => client.developerConsole.sdkVersions.promote("app/orders", "sdk-version/1", "stable"),
  "developerConsole.sdkVersions.promote",
);
assertMissingIdempotencyFailFast(
  "developerConsole.sdkVersions.createCompatibilityWindow",
  () =>
    client.developerConsole.sdkVersions.createCompatibilityWindow("app/orders", {
      fromVersionId: "sdk-version/1",
      toVersionId: "sdk-version/2",
    }),
  "developerConsole.sdkVersions.createCompatibilityWindow",
);
assertMissingIdempotencyFailFast(
  "developerConsole.sdkVersions.downloadToken",
  () => client.developerConsole.sdkVersions.downloadToken("app/orders", "sdk-version/1", "typescript"),
  "developerConsole.sdkVersions.downloadToken",
);
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
  "operations.workflows.cancel",
  () => client.operations.workflows.cancel("workflow/1", { reason: "operator requested stop" }),
  {
    path: "/api/operations/workflows/workflow%2F1/cancel",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { reason: "operator requested stop" },
  },
);
await expectSdkCall(
  "operations.reconciliation.list",
  () => client.operations.reconciliation.list({ status: "outcome_unknown", limit: 25 }),
  {
    path: "/api/operations/reconciliation/writebacks?status=outcome_unknown&limit=25",
  },
);
await expectSdkCall(
  "operations.reconciliation.list",
  () => client.operations.reconciliation.list({ status: "retryable", limit: 10 }),
  {
    path: "/api/operations/reconciliation/writebacks?status=retryable&limit=10",
  },
);
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
  "operations.reconciliation.resolve-external-uri",
  () =>
    client.operations.reconciliation.resolve("writeback/2", {
      externalWritebackUri: "s3://erp-writebacks/order/O-1001",
    }),
  {
    path: "/api/operations/reconciliation/writeback%2F2/resolve",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { externalWritebackUri: "s3://erp-writebacks/order/O-1001" },
  },
);
await expectSdkCall(
  "operations.reconciliation.approveRecovery",
  () =>
    client.operations.reconciliation.approveRecovery("writeback/3", {
      approvalId: "approval-1",
      reason: "operator reviewed sensitive writeback",
      externalWritebackUri: "s3://erp-writebacks/order/O-1001",
    }),
  {
    path: "/api/operations/reconciliation/writeback%2F3/approve-recovery",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      approvalId: "approval-1",
      reason: "operator reviewed sensitive writeback",
      externalWritebackUri: "s3://erp-writebacks/order/O-1001",
    },
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
await expectSdkCall(
  "operations.iceberg-maintenance.run",
  () =>
    client.operations.icebergMaintenance.run("raw/orders", {
      branch: "dev",
      smallFileThresholdBytes: 512,
      fileCountThreshold: 7,
      readAmplificationThreshold: 4.5,
      retentionMinSnapshots: 5,
    }),
  {
    path:
      "/api/operations/maintenance/iceberg/raw%2Forders/run?branch=dev&smallFileThresholdBytes=512" +
      "&fileCountThreshold=7&readAmplificationThreshold=4.5&retentionMinSnapshots=5",
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
await expectSdkCall(
  "operations.dead-letter-records.retry-transform",
  () => client.operations.deadLetterRecords.retryTransform("dlq/1", { idempotencyKey: "retry-transform-key" }),
  {
    path: "/api/operations/dead-letter-records/dlq%2F1/retry-transform",
    method: "POST",
    headers: { "Idempotency-Key": "retry-transform-key" },
  },
);
assertMissingIdempotencyFailFast(
  "operations.dead-letter-records.retry-transform",
  () => client.operations.deadLetterRecords.retryTransform("dlq/1"),
  "operations.deadLetterRecords.retryTransform",
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
await expectSdkCall(
  "operations.index.replay-ontology-reindex",
  () => client.operations.index.replayOntologyReindex("Order Item", { reindexKey: "object_reindex:Order:abc123" }),
  {
    path: "/api/operations/index/Order%20Item/ontology-reindex",
    method: "POST",
    body: { reindexKey: "object_reindex:Order:abc123" },
  },
);
await expectSdkCall("operations.transforms.retry", () => client.operations.transforms.retry("transform/1"), {
  path: "/api/operations/runs/transform/transform%2F1/retry",
  method: "POST",
});
await expectSdkCall("materializations.run", () => client.materializations.run("Daily Orders"), {
  path: "/api/materializations/Daily%20Orders/run",
  method: "POST",
});
await expectSdkCall("materializations.list", () => client.materializations.list(), {
  path: "/api/materializations",
});
await expectSdkCall(
  "actions.generated.validate",
  () =>
    client.actions.ApproveOrder.validate({
      objectId: "order/1",
      expectedObjectVersion: 7,
      params: { reason: "approved" },
    }),
  {
    path: "/api/actions/ApproveOrder/validate",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: {
      target: { objectType: "Order", objectId: "order/1" },
      expectedObjectVersion: 7,
      params: { reason: "approved" },
    },
  },
);
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
await expectSdkCall(
  "actions.generated.apply",
  () =>
    osdk(sdk.ApproveOrder).applyAction({
      objectId: "order/osdk",
      expectedObjectVersion: 8,
      params: { reason: "osdk-approved" },
      idempotencyKey: "osdk-approve-key",
    }),
  {
    path: "/api/actions/ApproveOrder/apply",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "osdk-approve-key" },
    body: {
      target: { objectType: "Order", objectId: "order/osdk" },
      expectedObjectVersion: 8,
      params: { reason: "osdk-approved" },
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

await expectSdkCall(
  "actions.generated.applyBatch",
  () =>
    client.actions.ApproveOrder.applyBatch(
      {
        targets: [
          { objectId: "order/1", expectedObjectVersion: 7 },
          { objectId: "order/2", expectedObjectVersion: 3 },
        ],
        params: { reason: "batch approved" },
      },
      { idempotencyKey: "approve-batch-key" },
    ),
  {
    path: "/api/actions/ApproveOrder/apply-batch",
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "approve-batch-key" },
    body: {
      objectType: "Order",
      targets: [
        { objectId: "order/1", expectedObjectVersion: 7 },
        { objectId: "order/2", expectedObjectVersion: 3 },
      ],
      params: { reason: "batch approved" },
    },
  },
);

assertMissingIdempotencyFailFast(
  "actions.generated.applyBatch",
  () =>
    client.actions.ApproveOrder.applyBatch({
      targets: [{ objectId: "order/1", expectedObjectVersion: 7 }],
      params: { reason: "batch approved" },
    }),
  "ApproveOrder.applyBatch",
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
responseQueue.push(
  errorResponse(
    429,
    {
      detail: {
        code: "RATE_LIMITED",
        message: "Too many OAuth refresh requests",
        details: { retryAfterSeconds: 7 },
        request_id: "rate-limit-error-id",
      },
    },
    { "X-Request-ID": "transport-rate-id" },
  ),
);
await assert.rejects(
  () => client.auth.osdkOAuth.refresh({ refreshToken: "refresh-rate-limited" }),
  (error) => {
    assert.equal(error.name, "FoundryLiteApiError");
    assert.equal(error.status, 429);
    assert.equal(error.code, "RATE_LIMITED");
    assert.equal(error.requestId, "rate-limit-error-id");
    assert.equal(error.retryable, true);
    assert.deepEqual(error.details, { retryAfterSeconds: 7 });
    return true;
  },
);

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
const adminCapability = (fields) => ({
  executionSurface: "future",
  canStartFromBrowser: false,
  requiresApproval: true,
  operatorChecklist: ["Show operator evidence before marking the task complete."],
  blockingReason: null,
  ...fields,
});
const adminOverview = {
  generatedAt: "2026-06-28T00:00:00Z",
  summary: { apiAvailable: 1, workerEntrypoints: 1, cliOnly: 1, runbookOnly: 1, future: 1 },
  capabilities: [
    adminCapability({
      id: "bounded-outbox-publish",
      title: "Bounded outbox publish batch",
      status: "api_available",
      summary: "Publish one bounded batch.",
      sdkSurface: "operations.outbox.publishPending",
      apiRoute: "POST /api/operations/outbox/publish",
      command: "pnpm worker:outbox-publisher",
      evidencePath: "artifacts/operations/outbox_publisher_summary.json",
      safetyBoundary: "Bounded batch only.",
      nextSurface: null,
      executionSurface: "browser_sdk",
      canStartFromBrowser: true,
      requiresApproval: false,
      blockingReason: null,
    }),
    adminCapability({
      id: "outbox-publisher-worker",
      title: "Outbox publisher worker",
      status: "worker_entrypoint",
      summary: "Drain pending rows.",
      sdkSurface: null,
      apiRoute: null,
      command: "pnpm worker:outbox-publisher",
      evidencePath: "artifacts/operations/outbox_publisher_summary.json",
      safetyBoundary: "Daemon lifecycle is future.",
      nextSurface: "operations.admin.workers",
      executionSurface: "worker_entrypoint",
      blockingReason: "This worker entrypoint has no browser-safe daemon control yet.",
    }),
    adminCapability({
      id: "schema-migration-runner",
      title: "Schema migration runner",
      status: "cli_only",
      summary: "Run singleton-lock migrations.",
      sdkSurface: null,
      apiRoute: null,
      command: "pnpm db:migrate",
      evidencePath: "artifacts/operations/migration_run.json",
      safetyBoundary: "Privileged CLI-only operation.",
      nextSurface: "operations.admin.migrations",
      executionSurface: "cli",
      blockingReason: "Migration execution is CLI-only.",
    }),
    adminCapability({
      id: "infra-bootstrap",
      title: "Infrastructure bootstrap",
      status: "runbook_only",
      summary: "Bootstrap is runbook-driven.",
      sdkSurface: null,
      apiRoute: null,
      command: "pnpm dev",
      evidencePath: null,
      safetyBoundary: "Creates or connects infrastructure.",
      nextSurface: "operations.admin.bootstrap",
      executionSurface: "runbook",
      blockingReason: "Bootstrap is a runbook step.",
    }),
    adminCapability({
      id: "full-visual-operations-workspace",
      title: "Full visual operations workspace",
      status: "future",
      summary: "Full control room is future.",
      sdkSurface: null,
      apiRoute: null,
      command: null,
      evidencePath: null,
      safetyBoundary: "Needs screen-level authorization.",
      nextSurface: "operations.workspace",
      executionSurface: "future",
      blockingReason: "The full visual workspace is future work.",
    }),
  ],
};
const adminGroups = sdk.groupAdminCapabilities(adminOverview);
assert.equal(adminGroups.apiAvailable.length, 1);
assert.equal(adminGroups.workerEntrypoints.length, 1);
assert.equal(adminGroups.cliOnly[0].id, "schema-migration-runner");
coveredHelperIds.add("helpers.groupAdminCapabilities");
const outboxCapability = sdk.adminCapabilityView(adminOverview.capabilities[0]);
assert.equal(outboxCapability.actionKind, "browser_action");
assert.equal(outboxCapability.canRenderActionButton, true);
assert.equal(outboxCapability.primarySurface, "operations.outbox.publishPending");
assert.equal(outboxCapability.executionSurface, "browser_sdk");
assert.equal(outboxCapability.requiresApproval, false);
assert.equal(outboxCapability.operatorChecklist.length, 1);
assert.equal(outboxCapability.disabledReason, null);
const workerCapability = sdk.adminCapabilityView(adminOverview.capabilities[1]);
assert.equal(workerCapability.actionKind, "worker_entrypoint");
assert.equal(workerCapability.canRenderActionButton, false);
assert.match(workerCapability.disabledReason, /worker entrypoint/);
assert.equal(workerCapability.blockingReason, "This worker entrypoint has no browser-safe daemon control yet.");
assert.equal(
  sdk.adminCapabilityView({
    ...adminOverview.capabilities[0],
    id: "api-without-sdk",
    sdkSurface: null,
  }).actionKind,
  "api_without_sdk",
);
coveredHelperIds.add("helpers.adminCapabilityView");
const adminScreen = sdk.adminReadinessScreen(adminOverview);
assert.equal(adminScreen.canPublishOutbox, true);
assert.equal(adminScreen.browserRunnableCapabilityViews.length, 1);
assert.equal(adminScreen.workerEntrypointCapabilityViews[0].capability.id, "outbox-publisher-worker");
assert.equal(adminScreen.workerEntrypointCapabilityViews[0].actionKind, "worker_entrypoint");
assert.equal(adminScreen.cliOnlyCapabilityViews[0].capability.command, "pnpm db:migrate");
assert.equal(adminScreen.runbookOnlyCapabilityViews[0].capability.id, "infra-bootstrap");
assert.equal(adminScreen.futureCapabilityViews[0].capability.nextSurface, "operations.workspace");
coveredHelperIds.add("helpers.adminReadinessScreen");
const adminTaskPlan = {
  generatedAt: "2026-06-28T00:00:00Z",
  summary: { browserRunnable: 1, operatorRequired: 4, futureBackendSurfaces: 4 },
  tasks: [
    {
      id: "bounded-outbox-publish-task",
      capabilityId: "bounded-outbox-publish",
      title: "Bounded outbox publish batch",
      area: "event_plane",
      status: "api_available",
      executionSurface: "browser_sdk",
      canStartFromBrowser: true,
      requiresApproval: false,
      primarySurface: "operations.outbox.publishPending",
      sdkSurface: "operations.outbox.publishPending",
      apiRoute: "POST /api/operations/outbox/publish",
      command: "pnpm worker:outbox-publisher",
      evidencePath: "artifacts/operations/outbox_publisher_summary.json",
      operatorChecklist: ["Display DLQ evidence after the call."],
      requiredBackendSurface: null,
      blockingReason: null,
    },
    {
      id: "schema-migration-runner-task",
      capabilityId: "schema-migration-runner",
      title: "Schema migration runner",
      area: "migration",
      status: "cli_only",
      executionSurface: "cli",
      canStartFromBrowser: false,
      requiresApproval: true,
      primarySurface: "pnpm db:migrate",
      sdkSurface: null,
      apiRoute: null,
      command: "pnpm db:migrate",
      evidencePath: "artifacts/operations/migration_run.json",
      operatorChecklist: ["Show singleton-lock evidence."],
      requiredBackendSurface: "operations.admin.migrations",
      blockingReason: "Migration execution is CLI-only.",
    },
    {
      id: "infra-bootstrap-task",
      capabilityId: "infra-bootstrap",
      title: "Infrastructure bootstrap",
      area: "bootstrap",
      status: "runbook_only",
      executionSurface: "runbook",
      canStartFromBrowser: false,
      requiresApproval: true,
      primarySurface: "pnpm dev",
      sdkSurface: null,
      apiRoute: null,
      command: "pnpm dev",
      evidencePath: null,
      operatorChecklist: ["Keep credentials outside frontend code."],
      requiredBackendSurface: "operations.admin.bootstrap",
      blockingReason: "Bootstrap is a runbook step.",
    },
  ],
};
const outboxTaskView = sdk.adminTaskView(adminTaskPlan.tasks[0]);
assert.equal(outboxTaskView.badge, "Browser SDK");
assert.equal(outboxTaskView.canRenderActionButton, true);
assert.equal(outboxTaskView.disabledReason, null);
const migrationTaskView = sdk.adminTaskView(adminTaskPlan.tasks[1]);
assert.equal(migrationTaskView.badge, "CLI command");
assert.equal(migrationTaskView.canShowCommand, true);
assert.equal(migrationTaskView.requiresBackendSurface, true);
assert.match(migrationTaskView.disabledReason, /CLI-only/);
coveredHelperIds.add("helpers.adminTaskView");
const taskPlanScreen = sdk.adminTaskPlanScreen(adminTaskPlan);
assert.equal(taskPlanScreen.browserRunnableTaskViews.length, 1);
assert.equal(taskPlanScreen.operatorRequiredTaskViews.length, 2);
assert.equal(taskPlanScreen.futureBackendSurfaceTaskViews.length, 2);
assert.equal(taskPlanScreen.migrationTaskViews[0].task.command, "pnpm db:migrate");
assert.equal(taskPlanScreen.bootstrapTaskViews[0].task.command, "pnpm dev");
coveredHelperIds.add("helpers.adminTaskPlanScreen");
const adminBoard = sdk.adminOperationsBoard(adminOverview, adminTaskPlan);
assert.equal(adminBoard.readiness.canPublishOutbox, true);
assert.equal(adminBoard.browserRunnableTaskViews[0].task.id, "bounded-outbox-publish-task");
assert.deepEqual(adminBoard.commandRows.map((row) => row.command), ["pnpm db:migrate", "pnpm dev"]);
assert.deepEqual(adminBoard.requiredBackendSurfaces.map((item) => item.surface), [
  "operations.admin.migrations",
  "operations.admin.bootstrap",
]);
assert.equal(adminBoard.evidenceTaskViews.length, 2);
assert.equal(adminBoard.hasOperatorCommands, true);
assert.equal(adminBoard.hasRequiredBackendSurfaces, true);
coveredHelperIds.add("helpers.adminOperationsBoard");
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

const pollSnapshots = [];
const pollDelays = [];
const finalPollSnapshot = await sdk.pollFoundryLiteOperation(
  async ({ attempt }) => {
    const snapshot = attempt < 3 ? { status: "running", attempt } : { status: "succeeded", attempt };
    pollSnapshots.push(snapshot);
    return snapshot;
  },
  {
    intervalMs: 25,
    sleep: async (delayMs) => pollDelays.push(delayMs),
  },
);
assert.deepEqual(finalPollSnapshot, { status: "succeeded", attempt: 3 });
assert.deepEqual(pollDelays, [25, 25]);
assert.deepEqual(
  pollSnapshots.map((snapshot) => snapshot.status),
  ["running", "running", "succeeded"],
);
await assert.rejects(
  () =>
    sdk.pollFoundryLiteOperation(async () => ({ status: "running" }), {
      maxAttempts: 1,
      sleep: async () => undefined,
    }),
  (error) => {
    assert.equal(error.code, "POLLING_LIMIT_EXCEEDED");
    return true;
  },
);
coveredHelperIds.add("helpers.pollFoundryLiteOperation");

const streamOpenEvents = [];
const streamObservedEvents = [];
responseQueue.push(
  streamResponse([
    'id: evt-1\nevent: progress\nretry: 1500\ndata: {"status":"running","percent":50}\n\n',
    ': keepalive\n\n',
    'id: evt-2\nevent: done\ndata: {"status":"succeeded"}\n\n',
  ]),
);
const streamedEvents = [];
for await (const event of sdk.streamFoundryLiteOperationEvents("/api/operations/workflows/workflow%2F1/events", {
  baseUrl: BASE_URL,
  token: "stream-token",
  headers: { "X-Stream-Header": "stream-a" },
  context: { tenantId: "tenant-stream", userId: "user-stream", roles: ["viewer"] },
  requestIdFactory: () => "stream-request-id",
  fetchImpl,
  onOpen: (metadata) => streamOpenEvents.push(metadata),
  onEvent: (metadata) => streamObservedEvents.push(metadata),
})) {
  streamedEvents.push(event);
}
assert.equal(calls.at(-1).url, `${BASE_URL}/api/operations/workflows/workflow%2F1/events`);
assert.equal(calls.at(-1).init.method, "GET");
assert.equal(calls.at(-1).init.headers.Accept, "text/event-stream");
assert.equal(calls.at(-1).init.headers.Authorization, "Bearer stream-token");
assert.equal(calls.at(-1).init.headers["X-Tenant-ID"], "tenant-stream");
assert.equal(calls.at(-1).init.headers["X-User-ID"], "user-stream");
assert.equal(calls.at(-1).init.headers["X-Roles"], "viewer");
assert.equal(calls.at(-1).init.headers["X-Request-ID"], "stream-request-id");
assert.equal(calls.at(-1).init.headers["X-Stream-Header"], "stream-a");
assert.deepEqual(streamOpenEvents, [
  {
    path: "/api/operations/workflows/workflow%2F1/events",
    status: 200,
    ok: true,
    requestId: "stream-response-id",
  },
]);
assert.deepEqual(
  streamedEvents.map((event) => [event.id, event.eventType, event.retryMs, event.payload.status]),
  [
    ["evt-1", "progress", 1500, "running"],
    ["evt-2", "done", null, "succeeded"],
  ],
);
assert.deepEqual(
  streamObservedEvents.map((event) => event.event.eventType),
  ["progress", "done"],
);
responseQueue.push(streamResponse(["event: custom\ndata: raw-payload\n\n"]));
const parsedStreamEvents = [];
for await (const event of sdk.streamFoundryLiteOperationEvents("/events/custom", {
  baseUrl: BASE_URL,
  fetchImpl,
  parseEvent: (raw, metadata) => ({
    ...metadata,
    raw,
    payload: { upper: raw.toUpperCase() },
  }),
})) {
  parsedStreamEvents.push(event);
}
assert.deepEqual(parsedStreamEvents[0].payload, { upper: "RAW-PAYLOAD" });
coveredHelperIds.add("helpers.streamFoundryLiteOperationEvents");

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
