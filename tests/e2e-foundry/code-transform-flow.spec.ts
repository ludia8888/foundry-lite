import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type PreviewRow = Record<string, unknown>;

function e2eName(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function splitDatasetRef(datasetRef: string): { namespace: string; name: string } {
  const [namespace, ...rest] = datasetRef.split(".");
  return { namespace, name: rest.join(".") };
}

async function previewDataset(page: Page, datasetRef: string): Promise<PreviewRow[]> {
  const { namespace, name } = splitDatasetRef(datasetRef);
  const response = await page.request.get(
    `${API_BASE_URL}/api/datasets/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/preview?limit=10`,
    { headers: DEMO_HEADERS },
  );
  await expectOk(response, "dataset preview");
  return (await response.json()) as PreviewRow[];
}

async function expectOk(response: { ok(): boolean; text(): Promise<string> }, label: string) {
  expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

async function uploadCsvDataset(
  page: Page,
  {
    sourceName,
    datasetRef,
    csv,
  }: { sourceName: string; datasetRef: string; csv: string },
) {
  const response = await page.request.post(`${API_BASE_URL}/api/sources/csv/uploads`, {
    headers: {
      ...DEMO_HEADERS,
      "Idempotency-Key": `${sourceName}-upload`,
    },
    multipart: {
      sourceName,
      displayName: `Code scheduler input ${sourceName}`,
      datasetRef,
      primaryKey: JSON.stringify(["order_id"]),
      file: {
        name: "orders.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(csv),
      },
    },
  });
  await expectOk(response, "upload code scheduler input CSV");
}

test("Code screen registers and runs a SQL transform, then previews the backend output dataset", async ({
  page,
}) => {
  const apiName = e2eName("orders_over_500_e2e");
  const outputDatasetRef = `pipelines.${e2eName("code_transform_output")}`;

  await page.goto("/code");
  await expect(page.locator("body")).toContainText("Code Repositories");

  await page.getByLabel("API 이름").fill(apiName);
  await page.getByLabel("출력 데이터셋 ref").fill(outputDatasetRef);
  await page.getByRole("button", { name: /^빌드$/ }).click();

  await expect(page.getByText("빌드 성공 · 출력 데이터셋 생성됨")).toBeVisible();
  await expect(page.locator("body")).toContainText(outputDatasetRef);
  await expect(page.locator("body")).toContainText("row count");
  await expect(page.locator("body")).toContainText("2");
  await expect(page.locator("body")).toContainText(/request_id=(?!—)\S+/);

  const rowsFromApi = await previewDataset(page, outputDatasetRef);
  expect(rowsFromApi).toHaveLength(2);
  expect(rowsFromApi.map((row) => row.order_id ?? row.ORDER_ID)).toEqual([
    "O-1001",
    "O-1002",
  ]);

  await page.getByRole("button", { name: "출력 프리뷰" }).click();
  await expect(page.locator("body")).toContainText(`${outputDatasetRef} · 2행`);
  await expect(page.locator("table")).toContainText("O-1001");
  await expect(page.locator("table")).toContainText("O-1002");
});

test("Code screen scheduler tick rebuilds a due SQL transform from a changed input dataset", async ({
  page,
}) => {
  const slug = e2eName("code_scheduler_e2e");
  const apiName = `${slug}_transform`;
  const inputDatasetRef = `raw.${slug}_input`;
  const outputDatasetRef = `pipelines.${slug}_output`;
  const inputDatasetName = `${slug}_input`;

  await uploadCsvDataset(page, {
    sourceName: `${slug}_source_v1`,
    datasetRef: inputDatasetRef,
    csv: "order_id,amount,region\nSCHED-CODE-1,110,NA\nSCHED-CODE-2,220,EU\n",
  });

  await page.goto("/code");
  await expect(page.locator("body")).toContainText("Code Repositories");
  await page.getByRole("button", { name: inputDatasetName }).click();
  await page.getByLabel("API 이름").fill(apiName);
  await page.getByLabel("출력 데이터셋 ref").fill(outputDatasetRef);
  await page.locator("textarea").fill(`SELECT
  order_id,
  amount,
  region
FROM {{ input('${inputDatasetRef}') }}`);
  await page.getByRole("button", { name: /^빌드$/ }).click();

  await expect(page.getByText("빌드 성공 · 출력 데이터셋 생성됨")).toBeVisible();
  await expect(page.locator("body")).toContainText(outputDatasetRef);

  await uploadCsvDataset(page, {
    sourceName: `${slug}_source_v2`,
    datasetRef: inputDatasetRef,
    csv: "order_id,amount,region\nSCHED-CODE-3,330,APAC\n",
  });

  await page.getByRole("button", { name: /scheduler tick/ }).click();
  await expect(page.locator("body")).toContainText("tick started runs");
  await expect(page.locator("body")).toContainText(`transform=${apiName}`);
  await expect(page.locator("body")).toContainText("rows=1");
  await expect(page.locator("body")).toContainText(/request_id=(?!—)\S+/);

  const rowsFromApi = await previewDataset(page, outputDatasetRef);
  expect(rowsFromApi).toHaveLength(1);
  expect(rowsFromApi[0].order_id ?? rowsFromApi[0].ORDER_ID).toBe(
    "SCHED-CODE-3",
  );
});
