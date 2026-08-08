import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  (process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
    /\/+$/,
    "",
  );
const TARGET_ORDER_ID = "O-3999";
const MCP_PROTOCOL_VERSION = "2025-06-18";

type OrderObject = {
  objectId: string;
  objectVersion: number;
  properties: { status?: string; operatorNote?: string };
};

type McpResult = Record<string, unknown>;

function uniqueName(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

async function createMcpApplication(page: Page) {
  const clientId = uniqueName("mcp-browser-client");
  const scopes = [
    "osdk:object:Order:read",
    "osdk:action:ApproveOrder:validate",
    "osdk:action:ApproveOrder:execute",
  ];
  const created = await page.request.post(
    `${API_BASE_URL}/api/developer-console/osdk-applications`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": uniqueName("create-mcp-browser-app"),
      },
      data: {
        appApiName: uniqueName("mcpBrowserApproval").replaceAll("-", "_"),
        displayName: "MCP browser approval E2E",
        clientId,
        resources: [
          {
            resourceType: "object",
            resourceApiName: "Order",
            scopes: [scopes[0]],
          },
          {
            resourceType: "action",
            resourceApiName: "ApproveOrder",
            scopes: scopes.slice(1),
          },
        ],
      },
    },
  );
  expect(created.ok()).toBe(true);
  const payload = (await created.json()) as { application: { id: string } };
  const appId = payload.application.id;
  const configured = await page.request.put(
    `${API_BASE_URL}/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/mcp-server`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": uniqueName("enable-mcp-browser-app"),
      },
      data: {
        status: "enabled",
        descriptionMarkdown:
          "Governed Order lookup and approval for an external AI agent.",
        allowedOrigins: [],
      },
    },
  );
  expect(configured.ok()).toBe(true);
  const oauthClient = await page.request.post(
    `${API_BASE_URL}/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/clients`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": uniqueName("create-mcp-oauth-client"),
      },
      data: {
        clientId,
        redirectUris: [],
        allowedScopes: scopes,
        accessTokenTtlSeconds: 300,
      },
    },
  );
  expect(oauthClient.ok()).toBe(true);
  const clientPayload = (await oauthClient.json()) as { id: string };
  const rotated = await page.request.post(
    `${API_BASE_URL}/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/clients/${encodeURIComponent(clientPayload.id)}/secrets/rotate`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": uniqueName("rotate-mcp-oauth-secret"),
      },
      data: { reason: "Ontology MCP browser E2E" },
    },
  );
  expect(rotated.ok()).toBe(true);
  const secretPayload = (await rotated.json()) as { clientSecret: string };
  const resource = `${API_BASE_URL}/mcp/ontology/${encodeURIComponent(appId)}`;
  const token = await page.request.post(`${API_BASE_URL}/api/auth/osdk/oauth/token`, {
    headers: {
      "X-Request-ID": uniqueName("mcp-client-credentials-token"),
    },
    form: {
      grant_type: "client_credentials",
      client_id: clientId,
      client_secret: secretPayload.clientSecret,
      scope: scopes.join(" "),
      resource,
    },
  });
  expect(token.ok()).toBe(true);
  const tokenPayload = (await token.json()) as {
    accessToken: string;
    token_type: string;
  };
  expect(tokenPayload.token_type).toBe("Bearer");
  expect(tokenPayload.accessToken).toBeTruthy();
  return {
    appId,
    headers: {
      Authorization: `Bearer ${tokenPayload.accessToken}`,
      "X-Request-ID": uniqueName("mcp-browser-request"),
    },
  };
}

async function initializeMcp(
  page: Page,
  appId: string,
  headers: Record<string, string>,
) {
  const response = await page.request.post(
    `${API_BASE_URL}/mcp/ontology/${encodeURIComponent(appId)}`,
    {
      headers,
      data: {
        jsonrpc: "2.0",
        id: "initialize",
        method: "initialize",
        params: {
          protocolVersion: MCP_PROTOCOL_VERSION,
          capabilities: {},
          clientInfo: { name: "foundry-lite-browser-e2e", version: "1.0.0" },
        },
      },
    },
  );
  expect(response.ok()).toBe(true);
  const initialized = (await response.json()) as {
    id: string;
    result: { protocolVersion: string };
  };
  expect(initialized.id).toBe("initialize");
  expect(initialized.result.protocolVersion).toBe(MCP_PROTOCOL_VERSION);
  const sessionId = response.headers()["mcp-session-id"];
  expect(sessionId).toBeTruthy();
  const sessionHeaders = {
    ...headers,
    "Mcp-Session-Id": sessionId,
    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
  };
  const acknowledged = await page.request.post(
    `${API_BASE_URL}/mcp/ontology/${encodeURIComponent(appId)}`,
    {
      headers: sessionHeaders,
      data: {
        jsonrpc: "2.0",
        method: "notifications/initialized",
        params: {},
      },
    },
  );
  expect(acknowledged.status()).toBe(202);
  return sessionHeaders;
}

async function callMcp(
  page: Page,
  appId: string,
  headers: Record<string, string>,
  id: string,
  name: string,
  argumentsPayload: Record<string, unknown>,
): Promise<McpResult> {
  const response = await page.request.post(
    `${API_BASE_URL}/mcp/ontology/${encodeURIComponent(appId)}`,
    {
      headers,
      data: {
        jsonrpc: "2.0",
        id,
        method: "tools/call",
        params: { name, arguments: argumentsPayload },
      },
    },
  );
  expect(response.ok()).toBe(true);
  const payload = (await response.json()) as {
    error?: unknown;
    result?: { structuredContent?: McpResult };
  };
  expect(payload.error).toBeUndefined();
  expect(payload.result?.structuredContent).toBeTruthy();
  return payload.result?.structuredContent ?? {};
}

async function getOrder(page: Page): Promise<OrderObject> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/objects/Order/${TARGET_ORDER_ID}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as OrderObject;
}

test("high-risk Ontology MCP Action is human-approved in UI and returns the same run", async ({
  page,
}) => {
  const order = await getOrder(page);
  expect(order.properties.status).toBe("REVIEW");
  const reason = `External agent request ${Date.now()}`;
  const mcp = await createMcpApplication(page);
  const mcpHeaders = await initializeMcp(page, mcp.appId, mcp.headers);

  const visibleOrder = await callMcp(
    page,
    mcp.appId,
    mcpHeaders,
    "read-order-before-approval",
    "object.Order.get",
    { objectId: TARGET_ORDER_ID },
  );
  expect(visibleOrder.objectVersion).toBe(order.objectVersion);

  const approval = await callMcp(
    page,
    mcp.appId,
    mcpHeaders,
    "request-high-risk-approval",
    "action.ApproveOrder.apply",
    {
      objectType: "Order",
      objectId: TARGET_ORDER_ID,
      expectedObjectVersion: order.objectVersion,
      params: { reason },
    },
  );
  expect(approval.status).toBe("approval_required");
  expect(String(approval.planHash)).toMatch(/^sha256:/);
  expect(String(approval.reviewId)).toMatch(/^insight_review_/);
  expect(String(approval.proposalFingerprint)).toMatch(/^sha256:/);
  const reviewId = String(approval.reviewId);

  const pending = await callMcp(
    page,
    mcp.appId,
    mcpHeaders,
    "read-pending-approval",
    "action_approval.get",
    { reviewId },
  );
  expect(pending.status).toBe("approval_pending");

  await page.goto(`/approvals?source=insights&proposalId=${encodeURIComponent(reviewId)}`);
  await expect(page.locator("body")).toContainText(reviewId);
  await expect(page.locator("body")).toContainText(String(approval.planHash));
  await page.getByRole("button", { name: "나에게 배정" }).click();
  await expect(page.getByRole("button", { name: "나에게 배정됨" })).toBeVisible();
  await page
    .getByPlaceholder("승인/거절 사유를 남기세요")
    .fill("MCP 요청의 계획과 증거를 검토했습니다.");
  await page.getByRole("button", { name: "승인" }).click();
  await expect(
    page.getByRole("button", { name: "executeApprovedAction 실행" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "executeApprovedAction 실행" }).click();
  await expect(page.getByText(/실행 완료\s*action_run_id=/)).toBeVisible();

  const reviewResponse = await page.request.get(
    `${API_BASE_URL}/api/insights/reviews/${encodeURIComponent(reviewId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(reviewResponse.ok()).toBe(true);
  const review = (await reviewResponse.json()) as {
    executionStatus: string;
    approvedActionRunId: string;
  };
  expect(review.executionStatus).toBe("executed");
  expect(review.approvedActionRunId).toMatch(/^action_run_/);

  const completed = await callMcp(
    page,
    mcp.appId,
    mcpHeaders,
    "read-completed-approval",
    "action_approval.get",
    { reviewId },
  );
  expect(completed.status).toBe("succeeded");
  expect(completed.actionRunId).toBe(review.approvedActionRunId);

  const run = await callMcp(
    page,
    mcp.appId,
    mcpHeaders,
    "read-approved-action-run",
    "action_run.get",
    { runId: review.approvedActionRunId },
  );
  expect(run.actionRunId).toBe(review.approvedActionRunId);
  expect(run.status).toBe("succeeded");

  const updatedOrder = await getOrder(page);
  expect(updatedOrder.properties.status).toBe("APPROVED");
  expect(updatedOrder.properties.operatorNote).toBe(reason);
  expect(updatedOrder.objectVersion).toBe(order.objectVersion + 1);
});
