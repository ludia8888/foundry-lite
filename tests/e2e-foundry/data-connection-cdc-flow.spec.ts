import { expect, type Page, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";
const CDC_OBJECT_TYPE = "CdcOrderE2E";

type OperationPlan = {
  readiness?: { status?: string };
  workerCommands?: { streamArchive?: string; objectIndexer?: string };
  objectIndexing?: { cdcPrimaryKey?: string[] };
  objectIndexingStatus?: {
    workflowRunId?: string | null;
    workflowOperationPath?: string | null;
    lastIndexedVersionNumber?: number | null;
    lastIndexRunId?: string | null;
    backlog?: {
      latestSourceDatasetVersionNumber?: number | null;
      remainingVersionCount?: number;
      hasMoreVersions?: boolean;
    };
    nextAction?: string;
  };
};
type SourceConnection = {
  lastRunId?: string | null;
  operationsPath?: string | null;
  lastCommitRef?: { status?: string; runId?: string };
};
type CdcObjectIndexResult = {
  status: "INDEXED" | "NO_VERSIONS";
  sourceDatasetVersionId: string | null;
  sourceDatasetVersionNumber: number | null;
  indexRunId: string | null;
  operationsPath: string | null;
  workflowRunId: string;
  workflowOperationPath: string;
  eventCount: number;
  objectsUpserted: number;
  objectsDeleted: number;
  eventsSkipped: number;
  backlog: {
    latestSourceDatasetVersionNumber: number | null;
    nextSourceDatasetVersionNumber: number | null;
    remainingVersionCount: number;
    hasMoreVersions: boolean;
  };
  nextAction: string;
};
type GenericObject = {
  objectId: string;
  properties: Record<string, unknown>;
};

function e2eSlug(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function getOperationPlan(
  page: Page,
  sourceName: string,
  objectTypeApiName = "Order",
): Promise<OperationPlan> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/sources/cdc/debezium/${encodeURIComponent(sourceName)}/operation-plan?objectTypeApiName=${encodeURIComponent(objectTypeApiName)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as OperationPlan;
}

async function getSource(page: Page, sourceName: string): Promise<SourceConnection> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/sources/${encodeURIComponent(sourceName)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as SourceConnection;
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function applyCdcOntology(page: Page, datasetRef: string): Promise<void> {
  const response = await page.request.post(`${API_BASE_URL}/api/ontology/apply`, {
    headers: { ...DEMO_HEADERS, "Content-Type": "application/json" },
    data: { yamlText: ontologyWithCdcObject(datasetRef) },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

async function uploadCdcEventDatasetVersion(
  page: Page,
  sourceName: string,
  datasetRef: string,
  objectId: string,
): Promise<void> {
  const csv = cdcEventCsv(sourceName, objectId);
  const response = await page.request.post(`${API_BASE_URL}/api/sources/csv/uploads`, {
    headers: {
      ...DEMO_HEADERS,
      "Idempotency-Key": `source-cdc-event-upload-${sourceName}`,
    },
    multipart: {
      sourceName: `cdc_event_upload_${sourceName}`,
      displayName: `CDC event upload ${sourceName}`,
      datasetRef,
      primaryKey: JSON.stringify(["event_id"]),
      file: {
        name: `${sourceName}.csv`,
        mimeType: "text/csv",
        buffer: Buffer.from(csv, "utf-8"),
      },
    },
  });
  expect(response.ok(), "CDC event CSV upload should return ok").toBe(true);
}

function ontologyWithCdcObject(datasetRef: string): string {
  const base = readFileSync(
    "examples/supply-chain-demo/ontology/order-customer.yaml",
    "utf-8",
  ).replace(
    "      - apiName: margin\n        column: margin\n        type: float\n        classification: finance",
    "      - apiName: margin\n        column: margin\n        type: float\n        classification: finance\n        editable: true\n        editPolicy: edit_wins",
  );
  const cdcObject = `
  - apiName: ${CDC_OBJECT_TYPE}
    displayName: CDC Order E2E
    primaryKey: orderId
    titleProperty: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
      cdc:
        dataset: ${datasetRef}
        primaryKeyColumns: [order_id]
        deletePolicy: tombstone
    properties:
      - apiName: orderId
        column: order_id
        type: string
        indexed: true
        nullable: false
      - apiName: customerId
        column: customer_id
        type: string
      - apiName: status
        column: source_status
        type: string
        indexed: true
      - apiName: amount
        column: amount
        type: float
      - apiName: riskScore
        column: risk_score
        type: float
        indexed: true
`;
  const adjustMarginAction = `
  - apiName: AdjustMargin
    displayName: Adjust margin
    target: Order
    parameters:
      - apiName: margin
        type: float
        required: true
    mutations:
      - type: setProperty
        property: margin
        valueFrom: params.margin
`;
  return base
    .replace("\nlinkTypes:\n", `\n${cdcObject}\nlinkTypes:\n`)
    .concat(adjustMarginAction);
}

function cdcEventCsv(sourceName: string, objectId: string): string {
  const eventId = `${sourceName}:topic:0:1`;
  const row = [
    eventId,
    "u",
    JSON.stringify({ order_id: objectId }, null, 0),
    JSON.stringify(
      {
        order_id: objectId,
        customer_id: "C-100",
        source_status: "SHIPPED",
        amount: 987.65,
        risk_score: 0.17,
      },
      null,
      0,
    ),
    JSON.stringify(
      {
        lsn: 1,
        offset: 1,
        source_ts_ms: 1700000000001,
        table: "orders",
      },
      null,
      0,
    ),
  ];
  return `event_id,op,pk_json,after_json,ordering_json\n${row.map(csvCell).join(",")}\n`;
}

function csvCell(value: string): string {
  return `"${value.replaceAll('"', '""')}"`;
}

test("Data Connection CDC wizard creates a Debezium source and opens the operation plan", async ({
  page,
}) => {
  const sourceName = e2eSlug("cdc_orders_e2e");
  const displayName = `CDC Orders E2E ${sourceName}`;
  const datasetRef = `demo.${sourceName}`;
  const streamName = `${sourceName}_stream`;
  const topic = `dbserver1.public.${sourceName}`;
  const consumerGroup = `foundry-lite-${sourceName}`;
  const cdcObjectId = `O-${sourceName.slice(-8)}`;

  await page.goto("/data/connections");
  await expect(page.locator("body")).toContainText("Data Connection");
  await page.getByRole("button", { name: "새 소스" }).first().click();

  await expect(page.getByText("소스 유형 선택")).toBeVisible();
  await page.getByRole("button", { name: /Debezium CDC/ }).click();
  await expect(page.getByText("데이터 소스 연결 방식을 선택하세요")).toBeVisible();
  await page.getByRole("button", { name: "계속" }).click();

  await expect(page.getByText("프로젝트에 소스 저장")).toBeVisible();
  await page.getByPlaceholder("예: 주문 ERP 연결").fill(displayName);
  await page.getByRole("button", { name: "소스 생성 및 계속" }).click();

  await expect(page.getByRole("heading", { name: "CDC 스트림 구성" })).toBeVisible();
  await page.getByPlaceholder("orders_cdc", { exact: true }).fill(sourceName);
  await page
    .getByPlaceholder("demo.orders_cdc", { exact: true })
    .fill(datasetRef);
  await page
    .getByPlaceholder("orders_stream", { exact: true })
    .fill(streamName);
  await page
    .getByPlaceholder("dbserver1.public.orders", { exact: true })
    .fill(topic);
  await page
    .getByPlaceholder("foundry-lite-cdc", { exact: true })
    .fill(consumerGroup);
  await page.getByPlaceholder("order_id", { exact: true }).fill("order_id");

  const startSyncCheckbox = page.getByRole("checkbox", {
    name: /생성 직후 CDC sync/,
  });
  await expect(startSyncCheckbox).toBeChecked();
  await startSyncCheckbox.click();
  await expect(startSyncCheckbox).not.toBeChecked();

  await page.getByRole("button", { name: "다음" }).click();
  await page.getByRole("button", { name: /CDC 소스 생성/ }).click();

  await expect(page.getByText("생성 결과 증거")).toBeVisible();
  await expect(page.locator("body")).toContainText(sourceName);
  await expect(page.locator("body")).toContainText(datasetRef);
  await expect(page.locator("body")).toContainText("order_id");
  await page.getByRole("button", { name: "완료 보기" }).click();
  await expect(page.getByRole("heading", { name: "소스 설정 완료" })).toBeVisible();
  await page.getByRole("button", { name: "소스 상세로 이동" }).click();

  await expect(page.locator("body")).toContainText(displayName);
  await page.getByRole("button", { name: "CDC 운영 계획" }).click();
  await expect(page.getByText("CDC operation plan")).toBeVisible();
  await expect(page.locator("body")).toContainText("worker ready");
  await expect(page.locator("body")).toContainText(datasetRef);
  await expect(page.locator("body")).toContainText(topic);
  await expect(page.locator("body")).toContainText(consumerGroup);
  await expect(page.locator("body")).toContainText("worker:stream-archive");
  await expect(page.locator("body")).toContainText("--consumer-group");
  await expect(page.locator("body")).toContainText("worker:cdc-object-indexer");
  await expect(page.locator("body")).toContainText("--cdc-primary-key");
  await expect(page.locator("body")).toContainText("order_id");
  await expect(page.locator("body")).toContainText("no index cursor");
  await expect(page.locator("body")).toContainText("archive first");
  await page.getByRole("button", { name: "Archive run 시작" }).click();
  await expect(page.locator("body")).toContainText("이벤트 없음");
  await expect(page.locator("body")).toContainText("limit=10");
  await expect(page.locator("body")).toContainText("source_debezium_archive_run");
  await expect(page.locator("body")).toContainText("/api/operations/runs/sync/");

  const sourceAfterRun = await getSource(page, sourceName);
  expect(sourceAfterRun.lastRunId).toMatch(/^sync_run_/);
  expect(sourceAfterRun.operationsPath).toBe(
    `/api/operations/runs/sync/${sourceAfterRun.lastRunId}`,
  );
  expect(sourceAfterRun.lastCommitRef?.status).toBe("no_events");
  expect(sourceAfterRun.lastCommitRef?.runId).toBe(sourceAfterRun.lastRunId);

  await uploadCdcEventDatasetVersion(page, sourceName, datasetRef, cdcObjectId);
  await applyCdcOntology(page, datasetRef);
  await page.getByLabel("CDC object type").fill(CDC_OBJECT_TYPE);
  await expect(page.locator("body")).toContainText(`--object-type ${CDC_OBJECT_TYPE}`);

  const objectIndexResponse = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(
          `/api/sources/cdc/debezium/${encodeURIComponent(sourceName)}/object-index/start`,
        ) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "CDC Object index 적용" }).click();
  const objectIndex = (await (await objectIndexResponse).json()) as CdcObjectIndexResult;
  expect(objectIndex.status).toBe("INDEXED");
  expect(objectIndex.indexRunId).toMatch(/^index_run_/);
  expect(objectIndex.operationsPath).toMatch(/^\/api\/operations\/runs\/index\/index_run_/);
  expect(objectIndex.workflowOperationPath).toMatch(/^\/api\/operations\/runs\/workflow\//);
  expect(objectIndex.eventCount).toBe(1);
  expect(objectIndex.sourceDatasetVersionId).toMatch(/^dsv_/);
  expect(objectIndex.sourceDatasetVersionNumber).toBe(1);
  expect(objectIndex.objectsUpserted).toBe(1);
  expect(objectIndex.objectsDeleted).toBe(0);
  expect(objectIndex.eventsSkipped).toBe(0);
  expect(objectIndex.backlog.remainingVersionCount).toBe(0);
  expect(objectIndex.backlog.hasMoreVersions).toBe(false);
  expect(objectIndex.nextAction).toBe("monitor_operations");
  await expect(page.locator("body")).toContainText("indexed");
  await expect(page.locator("body")).toContainText("1 / 1 / 0 / 0");
  await expect(page.locator("body")).toContainText(datasetRef);
  await expect(page.locator("body")).toContainText("version=1");
  await expect(page.locator("body")).toContainText(
    `id=${objectIndex.sourceDatasetVersionId}`,
  );
  await expect(page.locator("body")).toContainText("remaining=0");
  await expect(page.locator("body")).toContainText("caught up");
  await expect(page.locator("body")).toContainText(objectIndex.workflowOperationPath);
  const indexedObject = await apiGet<GenericObject>(
    page,
    `/api/objects/${CDC_OBJECT_TYPE}/${encodeURIComponent(cdcObjectId)}`,
  );
  expect(indexedObject.objectId).toBe(cdcObjectId);
  expect(indexedObject.properties.status).toBe("SHIPPED");
  await page.getByRole("link", { name: objectIndex.workflowOperationPath }).click();
  await expect(page.locator("body")).toContainText("Platform Operations");
  await expect(page.locator("body")).toContainText("Run 조사");
  await expect(page.locator("body")).toContainText("선택한 run 상세");
  await expect(page.locator("body")).toContainText(objectIndex.workflowRunId);
  await page.goBack();
  await page
    .getByRole("button", {
      name: new RegExp(`${escapeRegExp(displayName)}[\\s\\S]*Debezium CDC`),
    })
    .click();
  await expect(page.locator("body")).toContainText(displayName);

  await page.getByRole("button", { name: "개요" }).click();
  await expect(page.locator("body")).toContainText("마지막 run");
  await expect(page.locator("body")).toContainText(sourceAfterRun.lastRunId ?? "");
  await page
    .getByRole("link", { name: sourceAfterRun.lastRunId ?? "" })
    .click();
  await expect(page.locator("body")).toContainText("Platform Operations");
  await expect(page.locator("body")).toContainText("Run 조사");
  await expect(page.locator("body")).toContainText("선택한 run 상세");
  await expect(page.locator("body")).toContainText(sourceAfterRun.lastRunId ?? "");
  await expect(page.locator("body")).toContainText(
    `/api/operations/runs/sync/${sourceAfterRun.lastRunId}`,
  );

  const plan = await getOperationPlan(page, sourceName, CDC_OBJECT_TYPE);
  expect(plan.readiness?.status).toBe("ready_for_cdc_workers");
  expect(plan.workerCommands?.streamArchive).toContain(
    `--consumer-group ${consumerGroup}`,
  );
  expect(plan.workerCommands?.streamArchive).toContain(
    "--cdc-primary-key order_id",
  );
  expect(plan.workerCommands?.objectIndexer).toContain(
    `--source-dataset ${datasetRef}`,
  );
  expect(plan.workerCommands?.objectIndexer).toContain(
    `--object-type ${CDC_OBJECT_TYPE}`,
  );
  expect(plan.objectIndexing?.cdcPrimaryKey).toEqual(["order_id"]);
  expect(plan.objectIndexingStatus?.workflowRunId).toBe(objectIndex.workflowRunId);
  expect(plan.objectIndexingStatus?.workflowOperationPath).toBe(
    objectIndex.workflowOperationPath,
  );
  expect(plan.objectIndexingStatus?.lastIndexedVersionNumber).toBe(1);
  expect(plan.objectIndexingStatus?.lastIndexRunId).toBe(objectIndex.indexRunId);
  expect(plan.objectIndexingStatus?.backlog?.latestSourceDatasetVersionNumber).toBe(1);
  expect(plan.objectIndexingStatus?.backlog?.remainingVersionCount).toBe(0);
  expect(plan.objectIndexingStatus?.backlog?.hasMoreVersions).toBe(false);
  expect(plan.objectIndexingStatus?.nextAction).toBe("monitor_operations");
});
