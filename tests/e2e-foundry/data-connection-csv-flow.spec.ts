import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";

type PreviewRow = Record<string, unknown>;

function e2eSlug(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

async function previewDataset(page: Page, datasetRef: string): Promise<PreviewRow[]> {
  const [namespace, ...rest] = datasetRef.split(".");
  const name = rest.join(".");
  const response = await page.request.get(
    `${API_BASE_URL}/api/datasets/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/preview?limit=10`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as PreviewRow[];
}

test("Data Connection CSV wizard uploads a file and commits a previewable dataset", async ({
  page,
}) => {
  const sourceName = e2eSlug("csv_orders_e2e");
  const displayName = `CSV Orders E2E ${sourceName}`;
  const datasetRef = `demo.${sourceName}`;

  await page.goto("/data/connections");
  await expect(page.locator("body")).toContainText("Data Connection");
  await page.getByRole("button", { name: "새 소스" }).first().click();

  await expect(page.getByText("소스 유형 선택")).toBeVisible();
  await page.getByRole("button", { name: /CSV 업로드/ }).click();
  await expect(page.getByText("데이터 소스 연결 방식을 선택하세요")).toBeVisible();
  await page.getByRole("button", { name: "계속" }).click();

  await expect(page.getByText("프로젝트에 소스 저장")).toBeVisible();
  await page.getByPlaceholder("예: 주문 ERP 연결").fill(displayName);
  await page.getByRole("button", { name: "소스 생성 및 계속" }).click();

  await expect(page.getByRole("heading", { name: "소스 구성" })).toBeVisible();
  await page.getByPlaceholder("orders_raw_csv").fill(sourceName);
  await page.getByPlaceholder("demo.orders_raw").fill(datasetRef);
  await page.getByPlaceholder("order_id").fill("order_id");
  await page.getByRole("button", { name: "다음" }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: "orders.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("order_id,amount,region\nCSV-1,150,NA\nCSV-2,275,EU\n"),
  });
  await expect(page.getByText("CSV-1,150,NA")).toBeVisible();
  await page.getByRole("button", { name: "업로드 & 커밋 실행" }).click();

  await expect(page.getByRole("heading", { name: "업로드 증거 확인" })).toBeVisible();
  await expect(page.getByText("커밋 완료")).toBeVisible();
  await expect(page.locator("body")).toContainText(datasetRef);
  await expect(page.locator("body")).toContainText("row count");
  await expect(page.locator("body")).toContainText("2");
  await expect(page.locator("body")).toContainText(/source_csv_upload/);

  const rows = await previewDataset(page, datasetRef);
  expect(rows).toHaveLength(2);
  expect(rows.map((row) => row.order_id ?? row.ORDER_ID)).toEqual([
    "CSV-1",
    "CSV-2",
  ]);
});
