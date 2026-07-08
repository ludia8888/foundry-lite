import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";

type PipelineBranch = Record<string, unknown> & {
  id: string;
  graphFingerprint: string;
};
type PipelineProposal = Record<string, unknown> & {
  id: string;
  status: string;
};
type PipelineVersion = Record<string, unknown> & {
  id: string;
  versionNumber?: number;
};
type PipelineRun = Record<string, unknown> & {
  id: string;
  status: string;
  outputDatasetRef?: string;
};
type PreviewRow = Record<string, unknown>;

function e2eSlug(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

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
  data: Record<string, unknown> = {},
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const response = await page.request.post(`${API_BASE_URL}${path}`, {
    headers: {
      ...DEMO_HEADERS,
      ...extraHeaders,
      "Content-Type": "application/json",
    },
    data,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function previewDataset(page: Page, datasetRef: string): Promise<PreviewRow[]> {
  const [namespace, ...rest] = datasetRef.split(".");
  const name = rest.join(".");
  return apiGet<PreviewRow[]>(
    page,
    `/api/datasets/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/preview?limit=10`,
  );
}

function ordersPipelineGraph({
  pipelineId,
  sqlOutputRef,
  outputRef,
}: {
  pipelineId: string;
  sqlOutputRef: string;
  outputRef: string;
}) {
  const columns = [
    column("order_id"),
    column("customer_id"),
    column("source_status"),
    column("amount", "double"),
  ];
  return {
    nodes: [
      {
        id: "raw_orders",
        type: "dataset",
        config: {
          datasetRef: "raw.erp_orders",
          label: "erp_orders",
          schema: columns,
        },
        schema: columns,
      },
      {
        id: "clean_sql",
        type: "sql",
        config: {
          label: "Clean seeded orders",
          sql:
            "select order_id, customer_id, source_status, amount " +
            "from {{ input('raw.erp_orders') }} order by order_id",
          outputDatasetRef: sqlOutputRef,
          schema: columns,
        },
        schema: columns,
      },
      {
        id: "out",
        type: "output_dataset",
        config: {
          label: "Orders UI output",
          outputDatasetRef: outputRef,
          schema: columns,
        },
        schema: columns,
      },
    ],
    edges: [
      { id: "edge_raw_orders__clean_sql_1", source: "raw_orders", target: "clean_sql" },
      { id: "edge_clean_sql__out_2", source: "clean_sql", target: "out" },
    ],
    layout: {
      positions: {
        raw_orders: { x: 40, y: 80 },
        clean_sql: { x: 340, y: 80 },
        out: { x: 640, y: 80 },
      },
    },
    outputContract: { columns },
    tests: [{ name: "schema contract", expected: { columns } }],
    schedule: { kind: "manual", pipelineId },
  };
}

function column(name: string, type = "string") {
  return { name, type, nullable: false };
}

test("Pipeline Builder edits, proposes, deploys, runs, and materializes a backend output dataset", async ({
  page,
}) => {
  const pipelineId = e2eSlug("pipeline_ui");
  const branchName = `ui-review-${pipelineId}`;
  const initialOutputRef = `pipelines.${pipelineId}_initial`;
  const finalOutputRef = `pipelines.${pipelineId}_final`;
  const sqlOutputRef = `pipelines.${pipelineId}_sql`;
  const proposalTitle = `Deploy ${pipelineId}`;

  const branch = await apiPost<PipelineBranch>(
    page,
    "/api/pipelines/branches",
    { pipelineId, name: branchName },
    { "Idempotency-Key": `e2e-pipeline-branch-${pipelineId}` },
  );
  const seededGraph = ordersPipelineGraph({
    pipelineId,
    sqlOutputRef,
    outputRef: initialOutputRef,
  });
  await apiPost<PipelineBranch>(
    page,
    `/api/pipelines/branches/${encodeURIComponent(branch.id)}/graph`,
    {
      graph: seededGraph,
      expectedFingerprint: branch.graphFingerprint,
    },
  );
  const validation = await apiGet<Record<string, unknown>>(
    page,
    `/api/pipelines/branches/${encodeURIComponent(branch.id)}/validate`,
  );
  expect(validation.valid).toBe(true);

  await page.goto("/pipelines");
  await expect(page.locator("body")).toContainText("Pipeline Builder");
  await expect(page.locator("body")).toContainText(branchName);

  await page.locator(".react-flow__node").filter({ hasText: "erp_orders" }).click();
  await page.getByRole("button", { name: "데이터 미리보기" }).click();
  await expect(page.locator("body")).toContainText("표시 3행 / limit 50");
  await expect(page.locator("body")).toContainText("O-1001");

  await page.getByTitle("파이프라인 출력 열기").click();
  await page.getByRole("button", { name: new RegExp(initialOutputRef) }).click();
  const inspector = page.locator("aside");
  await expect(inspector).toContainText("출력");
  const outputRefInput = inspector.locator("input").nth(1);
  await expect(outputRefInput).toHaveValue(initialOutputRef);
  await outputRefInput.fill(finalOutputRef);
  await inspector.getByRole("button", { name: "설정 적용" }).click();

  const saveButton = page.getByRole("button", { name: "저장" });
  await expect(saveButton).toBeEnabled();
  const saveResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/pipelines/branches/${branch.id}/graph`) &&
      response.request().method() === "POST",
  );
  await saveButton.click();
  const saved = (await (await saveResponse).json()) as PipelineBranch;
  const savedOutputNode = ((saved.graph as { nodes: Record<string, unknown>[] }).nodes).find(
    (node) => node.id === "out",
  );
  expect((savedOutputNode?.data as Record<string, unknown>)?.outputDatasetRef).toBe(
    finalOutputRef,
  );
  await expect(page.locator("body")).toContainText(finalOutputRef);

  await expect(page.getByRole("button", { name: "변경 제안" })).toBeEnabled();
  await page.getByRole("button", { name: "변경 제안" }).click();
  await page.getByPlaceholder("예: 주문-고객 조인 파이프라인 추가").fill(proposalTitle);
  await page
    .getByPlaceholder("변경 내용과 검증 근거를 적어주세요.")
    .fill("Playwright가 저장된 그래프를 proposal/deploy/run까지 검증합니다.");
  const proposeResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/pipelines/branches/${branch.id}/propose`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "제안 제출" }).click();
  const proposal = (await (await proposeResponse).json()) as PipelineProposal;
  expect(proposal.status).toBe("submitted");

  const proposalRow = page.getByRole("row").filter({ hasText: proposalTitle });
  await expect(proposalRow).toContainText("검토 대기");
  const approveResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/pipelines/proposals/${proposal.id}/decision`) &&
      response.request().method() === "POST",
  );
  await proposalRow.getByRole("button", { name: "승인" }).click();
  const approved = (await (await approveResponse).json()) as PipelineProposal;
  expect(approved.status).toBe("approved");
  await expect(proposalRow).toContainText("승인됨");

  const executeResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/pipelines/proposals/${proposal.id}/execute`) &&
      response.request().method() === "POST",
  );
  await proposalRow.getByRole("button", { name: "적용 (버전 생성)" }).click();
  const version = (await (await executeResponse).json()) as PipelineVersion;
  expect(version.versionNumber).toBe(1);
  await expect(page.locator("body")).toContainText("파이프라인 버전");
  await expect(page.locator("body")).toContainText(version.id);

  const versionRow = page.getByRole("row").filter({ hasText: version.id });
  const deployResponse = page.waitForResponse(
    (response) =>
      response.url().includes(
        `/api/pipelines/${pipelineId}/deploy/${encodeURIComponent(version.id)}`,
      ) && response.request().method() === "POST",
  );
  await versionRow.getByRole("button", { name: "배포" }).click();
  expect((await deployResponse).ok()).toBe(true);

  const runButton = versionRow.getByRole("button", { name: "실행" });
  await expect(runButton).toBeEnabled();
  const runResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/pipelines/${pipelineId}/runs`) &&
      response.request().method() === "POST",
  );
  await runButton.click();
  const run = (await (await runResponse).json()) as PipelineRun;
  expect(run.status).toBe("succeeded");
  expect(run.outputDatasetRef).toBe(finalOutputRef);

  await expect(page.locator("body")).toContainText("succeeded");
  await expect(page.locator("body")).toContainText(finalOutputRef);
  await expect(page.locator("body")).toContainText("pipeline.run.succeeded");

  const runDetail = await apiGet<PipelineRun>(
    page,
    `/api/pipelines/runs/${encodeURIComponent(run.id)}`,
  );
  expect(runDetail.status).toBe("succeeded");
  expect(runDetail.outputDatasetRef).toBe(finalOutputRef);
  const timeline = await apiGet<{ timeline: Record<string, unknown>[] }>(
    page,
    `/api/pipelines/runs/${encodeURIComponent(run.id)}/timeline`,
  );
  expect(timeline.timeline.some((item) => item.event === "pipeline.run.succeeded")).toBe(true);

  const outputRows = await previewDataset(page, finalOutputRef);
  expect(outputRows.map((row) => row.order_id ?? row.ORDER_ID)).toEqual([
    "O-1001",
    "O-1002",
    "O-1003",
  ]);
});
