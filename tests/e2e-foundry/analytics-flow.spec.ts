import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type PreviewRow = Record<string, unknown>;
type ObjectAggregationResult = {
  groups: Array<{
    key: Record<string, unknown>;
    metrics: Record<string, number | null>;
  }>;
  totalGroups: number;
};
type DatasetAggregationResult = ObjectAggregationResult & {
  datasetRef: string;
  versionId: string;
  rowCount: number;
  filteredRowCount: number;
};

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

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

test("Analytics screen previews clean.orders, filters rows, and renders server aggregate evidence", async ({
  page,
}) => {
  const previewRows = await apiGet<PreviewRow[]>(
    page,
    "/api/datasets/clean/orders/preview?limit=10",
  );
  expect(previewRows.map((row) => row.order_id ?? row.ORDER_ID)).toEqual([
    "O-1001",
    "O-1002",
    "O-1003",
  ]);

  const orderAggregate = await apiPost<ObjectAggregationResult>(
    page,
    "/api/objects/Order/aggregate",
    {
      select: [{ function: "count", name: "count" }],
      groupBy: ["status"],
    },
  );
  expect(orderAggregate.totalGroups).toBeGreaterThan(0);
  const datasetAggregate = await apiPost<DatasetAggregationResult>(
    page,
    "/api/datasets/clean/orders/aggregate",
    {
      select: [{ function: "count", name: "value" }],
      groupBy: ["source_status"],
    },
  );
  expect(datasetAggregate.filteredRowCount).toBe(3);
  expect(datasetAggregate.totalGroups).toBeGreaterThan(0);

  await page.goto("/analytics");
  await expect(page.locator("body")).toContainText("Analytics");
  await expect(page.locator("body")).toContainText("reference_only");
  await expect(page.locator("body")).toContainText(
    "datasets.list · datasets.preview · datasets.aggregate",
  );
  await expect(page.locator("body")).toContainText("clean.orders");
  await expect(page.locator("body")).toContainText("3/3행");
  await expect(page.locator("body")).toContainText("TABLE · 데이터 프리뷰");
  await expect(page.locator("body")).toContainText("O-1001");
  await expect(page.locator("body")).toContainText("risk_score");

  await expect(page.locator("body")).toContainText("FILTER · Keep rows");
  await expect(page.locator("body")).toContainText("3/3행 유지");
  await page.getByRole("button", { name: "조건 추가" }).click();
  await page.getByPlaceholder("값").fill("O-1001");
  await expect(page.locator("body")).toContainText("1/3행 유지");
  await expect(page.locator("body")).toContainText("2행 제외됨");

  await expect(page.locator("body")).toContainText("DISTRIBUTION · 집계 차트");
  await expect(page.locator("body")).toContainText("datasets.aggregate");
  await expect(page.locator("body")).toContainText("POST /api/datasets/clean/orders/aggregate");
  await expect(page.locator("body")).toContainText("1개 그룹");
  await expect(page.locator("body")).toContainText("SUMMARY");

  await expect(page.locator("body")).toContainText("QUIVER · 오브젝트 집계 차트");
  await expect(page.locator("body")).toContainText("서버 집계");
  await expect(page.locator("body")).toContainText("POST /api/objects/Order/aggregate");
  await expect(page.locator("body")).toContainText(
    `totalGroups=${orderAggregate.totalGroups}`,
  );
});
