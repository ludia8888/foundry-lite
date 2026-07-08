import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";

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
  await page.getByRole("button", { name: /Order[\s\S]*속성/ }).click();
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
