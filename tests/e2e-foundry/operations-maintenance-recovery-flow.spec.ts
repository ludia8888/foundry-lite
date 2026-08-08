import { expect, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type RecoveryOverview = {
  tenantId: string;
  restoreTrafficGate: Record<string, unknown>;
  requiredOperatorActions: string[];
};

type PreflightReport = {
  backupId: string;
  status: "ready" | "blocked";
  datasetVersions: unknown[];
  issues: unknown[];
  activeIndexPointers: unknown[];
};

type ObservabilityReport = {
  configurationVersion: string;
  activeIncidents: unknown[];
  suppressedIncidents: unknown[];
  summary: Record<string, unknown>;
};

type ErrorEnvelope = {
  detail: {
    code: string;
    message: string;
    request_id: string;
  };
};

type ObjectIndexReplay = {
  index_run_id: string;
  object_type: string;
  rows_read: number;
  objects_upserted: number;
};

test("Operations maintenance and recovery controls call backend APIs and render evidence", async ({
  page,
}) => {
  const overviewResponse = await page.request.get(
    `${API_BASE_URL}/api/operations/recovery/overview`,
    { headers: DEMO_HEADERS },
  );
  expect(overviewResponse.ok()).toBe(true);
  const overview = (await overviewResponse.json()) as RecoveryOverview;
  expect(overview.tenantId).toBe("tenant-demo");
  expect(overview.restoreTrafficGate).toBeTruthy();
  expect(Array.isArray(overview.requiredOperatorActions)).toBe(true);

  await page.goto("/operations?tab=recovery");
  await expect(page.getByRole("heading", { name: "Platform Operations" })).toBeVisible();
  await expect(page.locator("body")).toContainText("복구 개요");
  await expect(page.locator("body")).toContainText("현재 위상");

  const preflightResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/operations/backup-restore/preflight") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: /preflight 실행/ }).click();
  const preflight = (await (await preflightResponse).json()) as PreflightReport;
  expect(["ready", "blocked"]).toContain(preflight.status);
  expect(preflight.datasetVersions.length).toBeGreaterThan(0);
  expect(preflight.activeIndexPointers.length).toBeGreaterThan(0);
  await expect(page.locator("body")).toContainText("preflight 상태");
  await expect(page.locator("body")).toContainText(`backup_id=${preflight.backupId}`);

  await page.goto("/operations?tab=maintenance");
  await expect(page.locator("body")).toContainText("Observability 탐지");

  const detectResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/operations/observability/detect") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: /detect 실행/ }).click();
  const observability = (await (await detectResponse).json()) as ObservabilityReport;
  expect(observability.configurationVersion).toBeTruthy();
  expect(
    observability.activeIncidents.length + observability.suppressedIncidents.length,
  ).toBeGreaterThanOrEqual(0);
  await expect(page.locator("body")).toContainText("config_version=");
  await expect(page.locator("body")).toContainText("활성 인시던트");

  const planResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/operations/maintenance/iceberg?") &&
      response.url().includes("datasetRef=clean.orders") &&
      response.request().method() === "GET",
  );
  await page.getByRole("button", { name: /계획 읽기/ }).click();
  const planResult = await planResponse;
  expect(planResult.status()).toBe(400);
  const planError = (await planResult.json()) as ErrorEnvelope;
  expect(planError.detail.code).toBe("VALIDATION_FAILED");
  expect(planError.detail.message).toContain("does not support Iceberg maintenance");
  await expect(page.locator("body")).toContainText(
    "active dataset storage profile does not support Iceberg maintenance",
  );
  await expect(page.locator("body")).toContainText("code=VALIDATION_FAILED");
  await expect(page.locator("body")).toContainText("재시도 불가");

  const replayResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/operations/index/Order/replay") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "재구축" }).click();
  const replay = (await (await replayResponse).json()) as ObjectIndexReplay;
  expect(replay.object_type).toBe("Order");
  expect(replay.index_run_id).toMatch(/^index_run_/);
  expect(replay.rows_read).toBeGreaterThanOrEqual(1);
  expect(replay.objects_upserted).toBeGreaterThanOrEqual(0);
  await expect(page.locator("body")).toContainText(`run=${replay.index_run_id}`);
  await expect(page.locator("body")).toContainText("upserted=");
});
