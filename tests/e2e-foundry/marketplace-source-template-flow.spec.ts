import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type PreviewRow = Record<string, unknown>;
type SourceDetail = {
  lastRunId?: string | null;
  operationsPath?: string | null;
};
type RuntimeRunDetail = {
  sourceEvidence?: Record<string, unknown> | null;
};

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
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as PreviewRow[];
}

async function getSource(page: Page, sourceName: string): Promise<SourceDetail> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/sources/${encodeURIComponent(sourceName)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as SourceDetail;
}

async function getSyncRunDetail(page: Page, runId: string): Promise<RuntimeRunDetail> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/operations/runs/sync/${encodeURIComponent(runId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as RuntimeRunDetail;
}

test("Marketplace blocks definition-only connector execution", async ({
  page,
}) => {
  await page.route("**/api/sources/templates", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          sourceType: "sharepoint_graph",
          displayName: "SharePoint Graph",
          category: "protocol",
          description:
            "SharePoint Online connector metadata without an execution adapter.",
          isRecommended: false,
          executionStatus: "definition_only",
          capabilities: ["batch_file", "media", "exploration"],
          credentialModes: ["credential_ref", "oauth_future"],
          networkModes: ["direct", "agent_proxy"],
          supportsExploration: true,
          managedRunModes: ["definition_only"],
        },
      ]),
    });
  });

  await page.goto("/marketplace");
  await page.getByRole("button", { name: "데이터 연결 제품" }).click();
  await page.getByRole("button", { name: /SharePoint Graph/ }).click();

  await expect(page.getByText("정의만", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "실행 준비 중" }),
  ).toBeDisabled();
  await expect(
    page.getByText("실행형 adapter·탐색·sync가 연결되기 전에는"),
  ).toBeVisible();
});

test("Marketplace CSV product deep-links into Data Connection and commits a backend dataset", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const sourceName = e2eSlug("market_csv_e2e");
  const displayName = `Marketplace CSV E2E ${sourceName}`;
  const datasetRef = `demo.${sourceName}`;

  await page.goto("/marketplace");
  await expect(page.locator("body")).toContainText("Marketplace");
  await expect(page.locator("body")).toContainText("sources.templates.list");

  await page.getByRole("button", { name: "데이터 연결 제품" }).click();
  await page.getByRole("button", { name: /CSV 업로드/ }).click();
  await expect(page.locator("body")).toContainText("csv_upload");
  await expect(page.locator("body")).toContainText("Dataset transaction");
  await expect(page.locator("body")).toContainText("내장된 Data Connection source flow");

  await page.getByRole("link", { name: /새 소스에서 사용/ }).click();
  await expect(page).toHaveURL(/\/data\/connections/);
  await expect(page.locator("body")).toContainText("CSV 업로드 · 연결 방식");

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
    buffer: Buffer.from("order_id,amount,region\nMKT-1,410,NA\nMKT-2,520,EU\n"),
  });
  await expect(page.getByText("MKT-1,410,NA")).toBeVisible();
  await page.getByRole("button", { name: "업로드 & 커밋 실행" }).click();

  await expect(page.getByRole("heading", { name: "업로드 증거 확인" })).toBeVisible();
  await expect(page.getByText("커밋 완료")).toBeVisible();
  await expect(page.locator("body")).toContainText(datasetRef);
  await expect(page.locator("body")).toContainText("row count");
  await expect(page.locator("body")).toContainText("2");

  const rows = await previewDataset(page, datasetRef);
  expect(rows.map((row) => row.order_id ?? row.ORDER_ID)).toEqual(["MKT-1", "MKT-2"]);
  const source = await getSource(page, sourceName);
  expect(source.lastRunId).toBeTruthy();
  const runId = String(source.lastRunId);
  const detail = await getSyncRunDetail(page, runId);
  expect(detail.sourceEvidence?.syncName).toBe(`upload:${datasetRef}`);
  expect(detail.sourceEvidence?.sourceType).toBe("file.csv");
  expect(detail.sourceEvidence?.operationPath).toBe(`/api/operations/runs/sync/${runId}`);
  expect(detail.sourceEvidence?.committedVersionId).toBeTruthy();
  expect(browserErrors, "browser console/page errors").toEqual([]);
});
