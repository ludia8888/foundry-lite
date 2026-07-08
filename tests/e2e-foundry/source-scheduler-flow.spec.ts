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

  return { datasetRef, syncName };
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

  const rows = await previewDataset(page, datasetRef);
  expect(rows).toHaveLength(1);
  expect(rows[0].order_id ?? rows[0].ORDER_ID).toBe("SCHED-1");
});
