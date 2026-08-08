import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type DatasetVersion = {
  id: string;
  version_number: number;
  row_count: number;
  transaction_id: string;
};
type PreviewRow = Record<string, unknown>;
type QualitySummary = {
  datasetRef: string;
  totalResults: number;
  latestResults: Record<string, unknown>[];
};
type LineageEdge = {
  id: string;
  from_resource_id: string;
  to_resource_id: string;
  relation: string;
  created_by_run_id: string;
};
type RuntimeRow = Record<string, unknown>;
type RuntimeRunQueryResult = {
  syncRuns: RuntimeRow[];
  transformRuns: RuntimeRow[];
  indexRuns: RuntimeRow[];
};

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

test("Lineage screen ties graph selection to preview, quality, run history, and timeline evidence", async ({
  page,
}) => {
  const versions = await apiGet<DatasetVersion[]>(
    page,
    "/api/datasets/clean/orders/versions",
  );
  expect(versions.length).toBeGreaterThan(0);
  const latestVersion = versions[0]!;

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

  const lineageEdges = await apiGet<LineageEdge[]>(
    page,
    `/api/operations/lineage?resourceId=${encodeURIComponent(latestVersion.id)}`,
  );
  expect(
    lineageEdges.some((edge) => edge.to_resource_id === latestVersion.id),
    "clean.orders latest version should have incoming lineage evidence",
  ).toBe(true);

  const runs = await apiGet<RuntimeRunQueryResult>(
    page,
    "/api/operations/runs?limit=100",
  );
  expect(runs.transformRuns.length + runs.syncRuns.length + runs.indexRuns.length).toBeGreaterThan(0);

  await page.goto("/lineage?ref=clean.orders");
  await expect(page.locator("body")).toContainText("데이터 리니지");
  await expect(page.locator("body")).toContainText("clean.orders");
  await expect(page.locator("body")).toContainText(`v${latestVersion.version_number}`);
  await expect(page.locator("body")).toContainText(
    latestVersion.row_count.toLocaleString("ko-KR"),
  );
  await expect(page.locator("body")).toContainText(latestVersion.transaction_id);
  await expect(page.locator("body")).toContainText("업스트림 출처");

  await page.getByRole("button", { name: "미리보기" }).click();
  await expect(page.locator("body")).toContainText("전체 3행 중 3행 표시");
  await expect(page.locator("body")).toContainText("order_id");
  await expect(page.locator("body")).toContainText("O-1001");

  await page.getByPlaceholder("컬럼 검색…").fill("risk");
  await expect(page.locator("body")).toContainText("risk_score");

  await page.getByRole("button", { name: "데이터 상태" }).click();
  await expect(page.locator("body")).toContainText("검증 결과");
  await expect(page.locator("body")).toContainText("PASS");
  await expect(page.locator("body")).toContainText("unique");
  await expect(page.locator("body")).toContainText("not_null");

  await page.getByRole("button", { name: "히스토리" }).click();
  await expect(page.locator("body")).toContainText(/총 \d+건/);
  await expect(page.locator("body")).toContainText(/transform|sync|index/);

  await page.getByRole("button", { name: "빌드 타임라인" }).click();
  await expect(page.locator("body")).toContainText("기간");
  await expect(page.locator("body")).toContainText("색상 기준: 상태");
  await expect(page.locator("body")).toContainText("성공");
});
