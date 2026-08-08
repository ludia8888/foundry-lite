import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type PreviewRow = Record<string, unknown>;
type QualitySummary = {
  totalResults: number;
  latestResults: Record<string, unknown>[];
};

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

test("Datasets screen opens clean.orders with preview, version, quality, and lineage evidence", async ({
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

  const qualitySummary = await apiGet<QualitySummary>(
    page,
    "/api/datasets/clean/orders/quality-contract/results/summary",
  );
  expect(qualitySummary.totalResults).toBeGreaterThan(0);
  expect(qualitySummary.latestResults.length).toBeGreaterThan(0);

  await page.goto("/datasets?ref=clean.orders");
  await expect(page.locator("body")).toContainText("Dataset / Media Set");
  await expect(page.locator("body")).toContainText("clean.orders");
  await expect(page.locator("body")).toContainText("8개 컬럼");
  await expect(page.locator("body")).toContainText("3행");
  await expect(page.locator("body")).toContainText("O-1001");
  await expect(page.locator("body")).toContainText("risk_score");

  await page.getByRole("button", { name: "시스템 컬럼 표시" }).click();
  await expect(page.locator("body")).toContainText("version=");
  await page.getByRole("button", { name: /Manifest evidence/ }).click();
  await expect(page.locator("body")).toContainText("schema_hash=");
  await expect(page.locator("body")).toContainText("version_id=");

  await page.getByRole("button", { name: "버전" }).click();
  await expect(page.locator("body")).toContainText("트랜잭션");
  await expect(page.locator("body")).toContainText("active");
  await expect(page.locator("body")).toContainText("schema_v");

  await page.getByRole("button", { name: "품질" }).click();
  await expect(page.locator("body")).toContainText("결과 요약");
  await expect(page.locator("body")).toContainText("품질 체크");
  await expect(page.locator("body")).toContainText("unique");
  await expect(page.locator("body")).toContainText("not_null");
  await expect(page.locator("body")).toContainText("PASS");

  await page.getByRole("button", { name: "리니지" }).click();
  await expect(page.locator("body")).toContainText("resource=clean.orders");
  await expect(page.locator("body")).toContainText("전체 그래프 보기");
});
