import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, type APIResponse, type Page, test } from "@playwright/test";

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

async function expectOk(response: APIResponse, label: string): Promise<void> {
  expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

function createSchedulerSqliteSource(syncName: string): string {
  const directory = path.resolve("artifacts", "e2e-source-scheduler");
  mkdirSync(directory, { recursive: true });
  const dbPath = path.join(directory, `${syncName}.db`);
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      [
        "import pathlib, sqlite3, sys",
        "path = pathlib.Path(sys.argv[1])",
        "path.parent.mkdir(parents=True, exist_ok=True)",
        "conn = sqlite3.connect(path)",
        "conn.execute('drop table if exists orders')",
        "conn.execute('create table orders (id integer primary key, order_id text, amount real)')",
        "conn.executemany('insert into orders (id, order_id, amount) values (?, ?, ?)', [(1, 'SCHED-1', 120.0), (2, 'SCHED-2', 240.0)])",
        "conn.commit()",
        "conn.close()",
      ].join("; "),
      dbPath,
    ],
    { stdio: "pipe" },
  );
  return dbPath;
}

async function previewDataset(
  page: Page,
  datasetRef: string,
): Promise<PreviewRow[]> {
  const [namespace, ...rest] = datasetRef.split(".");
  const name = rest.join(".");
  const response = await page.request.get(
    `${API_BASE_URL}/api/datasets/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/preview?limit=10`,
    { headers: DEMO_HEADERS },
  );
  await expectOk(response, "dataset preview");
  return (await response.json()) as PreviewRow[];
}

async function createDueManagedSync(page: Page, syncName: string) {
  const dbPath = createSchedulerSqliteSource(syncName);
  const credentialName = `${syncName}_credential`;
  const sourceName = `${syncName}_source`;
  const datasetRef = `raw.${syncName}`;

  const credential = await page.request.post(
    `${API_BASE_URL}/api/sources/credentials`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": `${syncName}-credential`,
      },
      data: {
        credentialName,
        displayName: `Scheduler E2E credential ${syncName}`,
        kind: "postgres_jdbc",
        authScheme: "database_url",
        secretValue: `sqlite:///${dbPath}`,
      },
    },
  );
  await expectOk(credential, "create source credential");
  const secretRef = ((await credential.json()) as {
    secretRef: { name: string };
  }).secretRef.name;

  const managedSync = await page.request.post(
    `${API_BASE_URL}/api/sources/managed-syncs`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": `${syncName}-managed-sync`,
      },
      data: {
        syncName,
        sourceName,
        displayName: `Scheduler E2E ${syncName}`,
        sourceType: "postgres_jdbc",
        capability: "batch",
        mode: "APPEND",
        targetDatasetRef: datasetRef,
        schedule: {
          mode: "interval",
          everySeconds: 60,
          startAt: "2026-01-01T00:00:00Z",
          batchLimit: 1,
        },
        configSummary: {
          databaseUrlSecretRef: secretRef,
          tableName: "orders",
          checkpointColumn: "id",
          batchLimit: 1,
        },
      },
    },
  );
  await expectOk(managedSync, "create due managed sync");

  return { datasetRef, sourceName, syncName };
}

async function createRecoverableManagedSync(
  page: Page,
  syncName: string,
  requestHeaders: Record<string, string>,
) {
  const dbPath = createSchedulerSqliteSource(syncName);
  const credentialName = `${syncName}_credential`;
  const sourceName = `${syncName}_source`;
  const credential = await page.request.post(`${API_BASE_URL}/api/sources/credentials`, {
    headers: { ...requestHeaders, "Idempotency-Key": `${syncName}-credential` },
    data: {
      credentialName,
      displayName: `Recoverable scheduler credential ${syncName}`,
      kind: "postgres_jdbc",
      authScheme: "database_url",
      secretValue: `sqlite:///${dbPath}`,
    },
  });
  await expectOk(credential, "create recoverable source credential");
  const secretRef = ((await credential.json()) as { secretRef: { name: string } }).secretRef.name;
  const managedSync = await page.request.post(`${API_BASE_URL}/api/sources/managed-syncs`, {
    headers: { ...requestHeaders, "Idempotency-Key": `${syncName}-managed-sync` },
    data: {
      syncName,
      sourceName,
      displayName: `Recoverable Source ${syncName}`,
      sourceType: "postgres_jdbc",
      capability: "batch",
      mode: "APPEND",
      targetDatasetRef: `raw.${syncName}`,
      schedule: {
        mode: "interval",
        everySeconds: 60,
        startAt: "2026-01-01T00:00:00Z",
        autoPauseAfterFailures: 1,
      },
      configSummary: {
        databaseUrlSecretRef: secretRef,
        tableName: "recovery_orders",
      },
    },
  });
  await expectOk(managedSync, "create recoverable managed sync");
  return { dbPath, sourceName };
}

function repairSchedulerSource(dbPath: string): void {
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      [
        "import sqlite3, sys",
        "conn = sqlite3.connect(sys.argv[1])",
        "conn.execute('create table recovery_orders (id integer primary key, order_id text, amount real)')",
        "conn.execute(\"insert into recovery_orders (id, order_id, amount) values (1, 'RECOVERED-E2E', 420.0)\")",
        "conn.commit()",
        "conn.close()",
      ].join("; "),
      dbPath,
    ],
    { stdio: "pipe" },
  );
}

test("Data Connection source scheduler previews due syncs and starts scheduled run", async ({
  page,
}) => {
  const syncName = e2eSlug("scheduler_sync_e2e");
  const { datasetRef } = await createDueManagedSync(page, syncName);

  await page.goto("/data/connections");
  await expect(page.locator("body")).toContainText("Data Connection");
  await page.getByRole("button", { name: "동기화", exact: true }).first().click();

  await expect(page.getByText("Source scheduler")).toBeVisible();
  await page.getByRole("button", { name: /previewDue 새로고침/ }).click();
  await expect(page.locator("body")).toContainText(syncName);
  await expect(page.locator("body")).toContainText(/due [1-9]\d*건/);

  await page.getByRole("button", { name: /scheduler tick 실행/ }).click();
  await expect(page.locator("body")).toContainText("tick started runs");
  await expect(page.locator("body")).toContainText(`sync=${syncName}`);
  await expect(page.locator("body")).toContainText("status=succeeded");
  await expect(page.locator("body")).toContainText("scheduled");
  await expect(page.getByText("due 0건", { exact: true })).toBeVisible();
  await expect(
    page.getByText("현재 due인 managed sync가 없습니다.", { exact: true }),
  ).toBeVisible();

  const rows = await previewDataset(page, datasetRef);
  expect(rows).toHaveLength(1);
  expect(rows[0].order_id ?? rows[0].ORDER_ID).toBe("SCHED-1");
});

test("Data Connection database Source runs and reloads a persisted live diagnostic", async ({
  page,
}) => {
  const syncName = e2eSlug("database_connection_test_e2e");
  const { datasetRef, sourceName } = await createDueManagedSync(page, syncName);

  await page.goto("/data/connections");
  const sourceList = page.getByTestId("source-list");
  await sourceList.getByPlaceholder("소스 검색").fill(sourceName);
  await sourceList.getByRole("button").click();
  await page.getByRole("button", { name: "연결 설정" }).click();
  await page.getByRole("button", { name: "네트워크 egress" }).click();

  await expect(page.getByText(/database URL · secretRef/)).toBeVisible();
  await page.getByRole("button", { name: "연결 진단 실행" }).click();
  await expect(
    page.getByText("1개 resource를 볼 수 있고 Dataset commit은 만들지 않았습니다.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText("5/5 checks", { exact: true })).toBeVisible();
  await expect(page.getByText("연결 준비됨", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "연결 상세" }).click();
  await page.getByRole("button", { name: "네트워크 egress" }).click();
  await expect(page.getByText("5/5 checks", { exact: true })).toBeVisible();

  const [namespace, name] = datasetRef.split(".");
  const versions = await page.request.get(
    `${API_BASE_URL}/api/datasets/${namespace}/${name}/versions`,
    { headers: DEMO_HEADERS },
  );
  await expectOk(versions, "dataset versions after connection test");
  expect(await versions.json()).toEqual([]);
});

test("Data Connection auto-pauses failed schedule and requires a successful recovery build", async ({ page }) => {
  const syncName = e2eSlug("scheduler_recovery_e2e");
  const tenantId = e2eSlug("tenant_scheduler_recovery");
  const requestHeaders = { ...DEMO_HEADERS, "X-Tenant-ID": tenantId };
  await page.route("**/api/**", async (route) => {
    await route.continue({
      headers: { ...route.request().headers(), "x-tenant-id": tenantId },
    });
  });
  const { dbPath, sourceName } = await createRecoverableManagedSync(page, syncName, requestHeaders);
  const failedTick = await page.request.post(`${API_BASE_URL}/api/sources/scheduler/tick`, {
    headers: requestHeaders,
    data: { maxRuns: 10 },
  });
  await expectOk(failedTick, "run failing scheduled sync");

  await page.goto("/data/connections");
  const sourceList = page.getByTestId("source-list");
  await sourceList.getByPlaceholder("소스 검색").fill(sourceName);
  await sourceList.getByRole("button").click();
  await page.getByRole("button", { name: "동기화", exact: true }).nth(1).click();

  const recorder = page.getByTestId("sync-failure-flight-recorder");
  await expect(recorder).toBeVisible();
  await expect(recorder).toContainText("자동 일시정지");
  await expect(recorder).toContainText("source database table batch could not be read");
  await expect(page.getByTestId("sync-schedule-state")).toContainText("복구 필요");

  await page.getByRole("button", { name: /실패 1/ }).click();
  await expect(page.getByRole("row").filter({ hasText: "실패" })).toHaveCount(1);
  await recorder.getByRole("button", { name: "증거 열기" }).click();
  await expect(page.getByTestId("sync-run-evidence")).toContainText("requestId");

  repairSchedulerSource(dbPath);
  await recorder.getByRole("button", { name: "복구 빌드" }).click();
  await expect(page.getByText("recovery_run_id=")).toBeVisible();
  await expect(page.getByTestId("sync-failure-flight-recorder")).toHaveCount(0);
  await expect(page.getByTestId("sync-schedule-state")).toContainText("자동 실행 활성");
  await expect(sourceList).toContainText("활성");
  await page.getByRole("button", { name: "전체 2", exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: "복구" })).toHaveCount(1);
  await page.getByRole("button", { name: "개요", exact: true }).click();
  await expect(page.getByTestId("source-latest-run-evidence")).toContainText("row count");
  await expect(page.getByTestId("source-latest-run-evidence")).toContainText("1");

  await page.getByRole("button", { name: "소스 비활성화", exact: true }).click();
  await expect(page.getByRole("button", { name: "소스 다시 활성화", exact: true })).toBeVisible();
  await expect(sourceList).toContainText("비활성");
  await page.getByRole("button", { name: "동기화", exact: true }).nth(1).click();
  await expect(page.getByTestId("source-disabled-run-guard")).toContainText(
    "새 빌드와 예약 실행은 차단됩니다",
  );
  await expect(page.getByRole("button", { name: "빌드", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "빌드 옵션", exact: true })).toBeDisabled();
  const blockedRun = await page.request.post(
    `${API_BASE_URL}/api/sources/managed-syncs/${syncName}/runs/start`,
    {
      headers: { ...requestHeaders, "Idempotency-Key": `${syncName}-blocked-manual-run` },
      data: { triggerType: "manual" },
    },
  );
  expect(blockedRun.status()).toBe(400);
  await expect(blockedRun.json()).resolves.toMatchObject({
    detail: { message: "source is disabled" },
  });

  await page.getByRole("button", { name: "개요", exact: true }).click();
  await page.getByRole("button", { name: "소스 다시 활성화", exact: true }).click();
  await expect(page.getByRole("button", { name: "소스 비활성화", exact: true })).toBeVisible();
  await expect(sourceList).toContainText("활성");
  await page.getByRole("button", { name: "동기화", exact: true }).nth(1).click();
  await expect(page.getByRole("button", { name: "빌드", exact: true })).toBeEnabled();

  const syncResponse = await page.request.get(
    `${API_BASE_URL}/api/sources/managed-syncs/${syncName}`,
    { headers: requestHeaders },
  );
  await expectOk(syncResponse, "read recovered managed sync");
  expect((await syncResponse.json()).status).toBe("active");
});
