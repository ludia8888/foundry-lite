import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type OrderObject = {
  objectId: string;
  objectVersion: number;
  properties: {
    orderId?: string;
    status?: string;
    operatorNote?: string;
  };
};

type ReviewPayload = {
  id: string;
  claimText: string;
  status: string;
  proposalFingerprint: string | null;
  executionStatus: string | null;
  approvedActionRunId: string | null;
  actionProposal: {
    actionApiName?: string;
    targetObjectType?: string;
    targetObjectId?: string;
    expectedObjectVersion?: number;
    params?: Record<string, unknown>;
  } | null;
};

function e2eId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function currentOrder(page: Page): Promise<OrderObject> {
  const response = await page.request.post(
    `${API_BASE_URL}/api/objects/Order/query`,
    {
      headers: { ...DEMO_HEADERS, "Content-Type": "application/json" },
      data: { limit: 10 },
    },
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { items: OrderObject[] };
  const order = body.items.find(
    (item) =>
      item.objectId === "O-1001" &&
      ["PENDING", "REVIEW"].includes(item.properties.status ?? ""),
  ) ?? body.items.find((item) =>
    ["PENDING", "REVIEW"].includes(item.properties.status ?? ""),
  );
  expect(
    order,
    "fresh demo seed should include an order that still needs approval",
  ).toBeTruthy();
  return order as OrderObject;
}

function actionProposalGraph(order: OrderObject, reason: string, claimText: string) {
  const objectId = order.objectId;
  const expectedObjectVersion = order.objectVersion;
  return {
    logicRunId: e2eId("logic-foundry-e2e"),
    agentVersionId: "agent.order-ops.v1",
    releaseChannel: "dev",
    modelAliasVersion: "default-completion@v1",
    promptVersionId: "prompt-order-copilot@v1",
    contextSources: [
      {
        sourceId: "ctx-order-ops",
        kind: "ontology.object_set",
        securityPartition: "tenant-demo:internal",
        selectedProperties: ["orderId", "status", "priority", "customerId"],
        tokenBudget: 800,
      },
    ],
    toolManifest: [
      {
        toolId: "ontology.get_object",
        version: "2026-06-25",
        effect: "READ",
        requiredPermission: "object:read",
        confirmationPolicy: "NONE",
        objectTypeAllowlist: ["Order"],
        propertyAllowlist: ["orderId", "status", "priority", "customerId"],
        inputSchema: {
          type: "object",
          required: ["object_type", "object_id"],
          properties: {
            object_type: { type: "string" },
            object_id: { type: "string" },
            property_names: { type: "array" },
          },
        },
        outputSchema: { type: "object" },
        resultClassification: "internal",
        status: "published",
      },
    ],
    logicBlocks: [
      { blockId: "input", kind: "Input", inputs: { objectId } },
      {
        blockId: "retrieve",
        kind: "CallFunction",
        inputs: {
          toolId: "ontology.get_object",
          version: "2026-06-25",
          toolCallId: "foundry-e2e-retrieve",
          arguments: {
            object_type: "Order",
            object_id: objectId,
            property_names: ["orderId", "status", "priority", "customerId"],
          },
          occurredAt: "2026-07-07T00:00:00Z",
        },
        dependsOn: ["input"],
      },
      {
        blockId: "proposal",
        kind: "CreateActionProposal",
        inputs: {
          actionType: "ApproveOrder",
          targetObjectType: "Order",
          targetObjectId: objectId,
          expectedObjectVersion,
          parameters: { reason },
          evidenceContextIds: ["ctx-order-ops"],
          expiresAt: "2026-08-01T00:10:00Z",
          claimText,
        },
        dependsOn: ["retrieve"],
      },
      {
        blockId: "output",
        kind: "Output",
        inputs: { fromBlock: "proposal" },
        dependsOn: ["proposal"],
      },
    ],
    evalAxes: ["retrieval", "answer", "citation", "tool", "action", "security"],
    agentAllowedTools: ["ontology.get_object"],
    agentAllowedActions: ["ApproveOrder"],
    modelAllowedClassifications: ["public", "internal"],
    inputJson: { objectType: "Order", objectId },
    userMessage: `Approve ${objectId} after reviewing selected AIP evidence.`,
    maxLogicBlocks: 25,
  };
}

async function findReview(page: Page, objectId: string, claimText: string) {
  const response = await page.request.get(
    `${API_BASE_URL}/api/insights/reviews?limit=100`,
    {
      headers: DEMO_HEADERS,
    },
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as {
    items?: ReviewPayload[];
    reviews?: ReviewPayload[];
  };
  const reviews = body.items ?? body.reviews ?? [];
  return reviews.find(
    (review) =>
      review.claimText === claimText &&
      review.actionProposal?.actionApiName === "ApproveOrder" &&
      review.actionProposal.targetObjectId === objectId,
  );
}

test("AIP action proposal can be approved and executed from the Foundry UI", async ({ page }) => {
  const order = await currentOrder(page);
  const reason = `Foundry UI approval e2e ${Date.now()}`;
  const claimText = `Approve ${order.objectId} based on selected AIP evidence.`;
  const graph = actionProposalGraph(order, reason, claimText);

  await page.goto("/");
  await page.getByRole("button", { name: "네비게이션 열기" }).click();
  const sidebar = page.locator("aside");
  await expect(sidebar).toBeVisible();
  await sidebar.getByRole("button", { name: "AIP Assist" }).click();
  await expect(page).toHaveURL(/\/aip$/);
  await expect(page.locator("body")).not.toContainText("AIP Assist — 백엔드 미지원");
  await page.getByRole("tab", { name: "빌더" }).click();
  await page.locator("textarea").fill(JSON.stringify(graph, null, 2));

  await page.getByRole("button", { name: "검증" }).click();
  await expect(page.getByText("검증 결과")).toBeVisible();
  await expect(page.locator("body")).toContainText("ready");
  await expect(page.locator("body")).toContainText("릴리스 준비 완료");

  await page.getByRole("button", { name: "실행" }).click();
  await expect(page.getByText("그래프 실행 결과")).toBeVisible();
  await expect(page.locator("body")).toContainText("succeeded");

  await expect
    .poll(
      async () => (await findReview(page, order.objectId, claimText))?.id ?? null,
      {
        message:
          "AIP run should create an approval review with an action proposal",
      },
    )
    .not.toBeNull();
  const reviewPayload = await findReview(page, order.objectId, claimText);
  expect(reviewPayload).toBeTruthy();
  const createdReview = reviewPayload as ReviewPayload;
  expect(createdReview.proposalFingerprint).toMatch(/^sha256:/);

  await page.goto("/actions");
  await expect(page.getByText("승인 필요 액션 · proposal")).toBeVisible();
  await expect(
    page.getByText(`ApproveOrder · Order/${order.objectId} · v${order.objectVersion}`),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approvals 열기" }).click();

  await page.getByRole("button", { name: "전체" }).click();
  await page.getByRole("button", { name: claimText }).click();
  await expect(page.getByText("proposal_fingerprint")).toBeVisible();
  await expect(page.locator("body")).toContainText(
    createdReview.proposalFingerprint as string,
  );

  await page.getByRole("button", { name: "나에게 배정" }).click();
  await expect(page.getByRole("button", { name: "나에게 배정됨" })).toBeVisible();
  await page
    .getByPlaceholder("승인/거절 사유를 남기세요")
    .fill("E2E approval after AIP evidence review");
  await page.getByRole("button", { name: "승인" }).click();

  await expect(page.getByRole("button", { name: "executeApprovedAction 실행" })).toBeVisible();
  await page.getByRole("button", { name: "executeApprovedAction 실행" }).click();
  await expect(page.getByText(/실행 완료\s*action_run_id=/)).toBeVisible();
  await expect(page.getByRole("button", { name: "executeApprovedAction 실행" })).toHaveCount(0);

  const updatedReviewResponse = await page.request.get(
    `${API_BASE_URL}/api/insights/reviews/${encodeURIComponent(createdReview.id)}`,
    { headers: DEMO_HEADERS },
  );
  expect(updatedReviewResponse.ok()).toBe(true);
  const updatedReview = (await updatedReviewResponse.json()) as ReviewPayload;
  expect(updatedReview.executionStatus).toBe("executed");
  expect(updatedReview.approvedActionRunId).toMatch(/^action_run_/);

  const orderResponse = await page.request.get(
    `${API_BASE_URL}/api/objects/Order/${encodeURIComponent(order.objectId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(orderResponse.ok()).toBe(true);
  const updatedOrder = (await orderResponse.json()) as OrderObject;
  expect(updatedOrder.properties.status).toBe("APPROVED");
  expect(updatedOrder.properties.operatorNote).toBe(reason);
  expect(updatedOrder.objectVersion).toBe(order.objectVersion + 1);
});
