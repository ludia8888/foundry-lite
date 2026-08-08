import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const REVIEWER_HEADERS = {
  ...DEMO_HEADERS,
  "X-User-ID": "object-inline-reviewer",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type GenericObject = {
  objectType: string;
  objectId: string;
  objectVersion: number;
  properties: Record<string, unknown>;
  sourceDatasetVersionId?: string | null;
};
type QueryResult = { items: GenericObject[]; nextCursor: string | null };
type AggregateResult = {
  groups: Array<{
    key: Record<string, unknown>;
    metrics: Record<string, number | null>;
  }>;
  totalGroups: number;
};
type ObjectLinkPayload = {
  linkType: string;
  to: {
    objectType: string;
    objectId: string;
    properties: Record<string, unknown>;
  };
};

async function apiPost<T>(
  page: Page,
  path: string,
  data: Record<string, unknown>,
): Promise<T> {
  const response = await page.request.post(`${API_BASE_URL}${path}`, {
    headers: { ...DEMO_HEADERS, "Content-Type": "application/json" },
    data,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function activateInlineAction(page: Page, suffix: string): Promise<string> {
  const actionApiName = `InlineOperatorNote${suffix}`;
  const branchResponse = await page.request.post(`${API_BASE_URL}/api/ontology/branches`, {
    headers: {
      ...DEMO_HEADERS,
      "Content-Type": "application/json",
      "Idempotency-Key": `inline-branch-${suffix}`,
    },
    data: { name: `inline-edit-${suffix}` },
  });
  expect(branchResponse.ok()).toBe(true);
  const branch = (await branchResponse.json()) as { id: string; contentFingerprint: string };
  const actionResponse = await page.request.post(
    `${API_BASE_URL}/api/ontology/branches/${encodeURIComponent(branch.id)}/action-types`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Content-Type": "application/json",
        "Idempotency-Key": `inline-action-${suffix}`,
      },
      data: {
        expectedFingerprint: branch.contentFingerprint,
        definition: {
          contractVersion: 3,
          apiName: actionApiName,
          displayName: "운영 메모 인라인 편집",
          target: "Order",
          targetKind: "object",
          riskLevel: "low",
          agentExecutionPolicy: "approval_required",
          permissions: { allowedRoles: ["ops_manager", "data_engineer"] },
          parameters: [{ apiName: "note", type: "string", required: true }],
          rules: [
            {
              kind: "modifyObject",
              ruleId: "set-operator-note-inline",
              objectType: "Order",
              target: { kind: "parameter", parameter: "__target__" },
              assignments: [
                {
                  property: "operatorNote",
                  value: { kind: "parameter", parameter: "note" },
                },
              ],
            },
          ],
          actionLog: { enabled: true },
          revert: { enabled: true },
        },
      },
    },
  );
  expect(actionResponse.ok()).toBe(true);

  const proposeResponse = await page.request.post(
    `${API_BASE_URL}/api/ontology/branches/${encodeURIComponent(branch.id)}/propose`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Content-Type": "application/json",
        "Idempotency-Key": `inline-proposal-${suffix}`,
      },
      data: { title: `Activate ${actionApiName}` },
    },
  );
  expect(proposeResponse.ok()).toBe(true);
  const proposed = (await proposeResponse.json()) as {
    proposal: { id: string; fingerprint: string };
  };
  const proposal = proposed.proposal;
  const assignResponse = await page.request.post(
    `${API_BASE_URL}/api/ontology/proposals/${encodeURIComponent(proposal.id)}/assign`,
    {
      headers: { ...REVIEWER_HEADERS, "Content-Type": "application/json" },
      data: { reviewerUserId: REVIEWER_HEADERS["X-User-ID"] },
    },
  );
  expect(assignResponse.ok()).toBe(true);
  const decisionResponse = await page.request.post(
    `${API_BASE_URL}/api/ontology/proposals/${encodeURIComponent(proposal.id)}/decide`,
    {
      headers: { ...REVIEWER_HEADERS, "Content-Type": "application/json" },
      data: { decision: "approve", expectedFingerprint: proposal.fingerprint },
    },
  );
  expect(decisionResponse.ok()).toBe(true);
  const executeResponse = await page.request.post(
    `${API_BASE_URL}/api/ontology/proposals/${encodeURIComponent(proposal.id)}/execute`,
    {
      headers: { ...REVIEWER_HEADERS, "Content-Type": "application/json" },
      data: { expectedFingerprint: proposal.fingerprint },
    },
  );
  expect(executeResponse.ok()).toBe(true);
  return actionApiName;
}

test("Object Explorer filters, aggregates, opens detail, and traverses linked objects", async ({
  page,
}) => {
  const queried = await apiPost<QueryResult>(page, "/api/objects/Order/query", {
    filter: { property: "orderId", op: "eq", value: "O-1001" },
    limit: 10,
  });
  const order = queried.items.find((item) => item.objectId === "O-1001");
  expect(order, "seeded Order O-1001 should be filterable").toBeTruthy();
  expect(order?.properties.status).toBeTruthy();

  const aggregate = await apiPost<AggregateResult>(
    page,
    "/api/objects/Order/aggregate",
    {
      select: [{ function: "count" }],
      groupBy: ["status"],
      filter: null,
    },
  );
  expect(aggregate.totalGroups).toBeGreaterThan(0);

  const links = await apiGet<ObjectLinkPayload[]>(
    page,
    "/api/objects/Order/O-1001/links/OrderCustomer",
  );
  const linkedCustomer = links.find((link) => link.to.objectType === "Customer");
  expect(linkedCustomer, "Order O-1001 should link to a Customer").toBeTruthy();

  await page.goto("/objects");
  await expect(page.locator("body")).toContainText("Object Explorer");
  await page.getByRole("button", { name: "새 탐색" }).click();
  await page.getByRole("button", { name: /^O Order 속성 \d+$/ }).click();
  await expect(page.locator("body")).toContainText("Order 탐색");
  await expect(page.locator("body")).toContainText("objects.generic.aggregate");

  await page.getByRole("button", { name: "필터 추가" }).click();
  await page.getByRole("combobox").filter({ hasText: "속성 선택" }).click();
  await page.getByRole("option", { name: /orderId/ }).click();
  await page.getByPlaceholder("비교 값").fill("O-1001");
  await page.getByRole("button", { name: "조건 추가" }).click();
  await expect(page.locator("body")).toContainText("orderId이(가) O-1001");
  await expect(page.locator("body")).toContainText("O-1001");

  await page
    .getByRole("button", { name: /객체 열기: Order[\s\S]*O-1001/ })
    .click();
  await expect(page.locator("body")).toContainText("Order › O-1001");
  await expect(page.getByText(/Order › O-1001 · v\d+/)).toBeVisible();
  await page.getByRole("button", { name: "속성" }).click();
  await expect(page.locator("body")).toContainText("전체 속성");
  await expect(page.locator("body")).toContainText("status");
  await expect(page.locator("body")).toContainText("riskScore");
  await expect(page.locator("body")).toContainText("근거 (lineage)");
  if (order!.sourceDatasetVersionId) {
    await expect(page.locator("body")).toContainText("source_dataset_version=");
  }

  await page.getByRole("button", { name: "링크" }).click();
  await expect(page.locator("body")).toContainText("OrderCustomer");
  await expect(page.locator("body")).toContainText(
    String(linkedCustomer!.to.properties.name ?? linkedCustomer!.to.objectId),
  );

  await page
    .getByRole("button", {
      name: String(linkedCustomer!.to.properties.name ?? linkedCustomer!.to.objectId),
    })
    .click();
  await expect(page.locator("body")).toContainText(
    `Customer › ${linkedCustomer!.to.objectId}`,
  );

  await page.getByRole("button", { name: "목록으로" }).click();
  await expect(page.locator("body")).toContainText("Order 탐색");
  await expect(page.locator("body")).toContainText("O-1001");
});

test("Object Explorer inline edit executes only a compiler-approved Action", async ({ page }) => {
  const suffix = `${Date.now()}${Math.random().toString(16).slice(2, 6)}`;
  const actionApiName = await activateInlineAction(page, suffix);
  const target = await apiGet<GenericObject>(page, "/api/objects/Order/O-1001");
  const nextNote = `inline-${suffix}`;
  const catalog = await apiGet<{
    objectTypes: Array<{ apiName: string; actions: Array<Record<string, unknown>> }>;
  }>(page, "/api/ontology/catalog");
  const activeAction = catalog.objectTypes
    .find((item) => item.apiName === "Order")
    ?.actions.find((item) => item.apiName === actionApiName);
  expect(activeAction).toMatchObject({
    enabled: true,
    definition: {
      contractVersion: 3,
      rules: [
        {
          kind: "modifyObject",
          assignments: [
            {
              property: "operatorNote",
              value: { kind: "parameter", parameter: "note" },
            },
          ],
        },
      ],
    },
    parameterSchema: {
      "x-foundry-inline-eligibility": {
        isEligible: true,
        propertyApiName: "operatorNote",
        parameterApiName: "note",
        parameterType: "string",
      },
    },
  });

  await page.goto("/objects");
  await page.getByRole("button", { name: "새 탐색" }).click();
  await page.getByRole("button", { name: /^O Order 속성 \d+$/ }).click();
  await page.getByRole("button", { name: "결과", exact: true }).click();
  const editButton = page.getByRole("button", {
    name: `인라인 편집: operatorNote · ${target.objectId}`,
  });
  await expect(editButton).toBeVisible();
  await expect(editButton).toHaveAttribute("title", "운영 메모 인라인 편집 Action으로 편집");
  await editButton.click();
  await page.getByRole("textbox", { name: "operatorNote 새 값" }).fill(nextNote);

  const applyResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/actions/${encodeURIComponent(actionApiName)}/apply`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: `인라인 저장: operatorNote · ${target.objectId}` }).click();
  expect((await applyResponse).ok()).toBe(true);

  await expect
    .poll(async () => {
      const object = await apiGet<GenericObject>(page, `/api/objects/Order/${target.objectId}`);
      return object.properties.operatorNote;
    })
    .toBe(nextNote);
  await expect(page.getByText(nextNote, { exact: true })).toBeVisible();
});
