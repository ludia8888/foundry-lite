import { expect, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type RuntimeRow = Record<string, unknown> & {
  id?: string;
  status?: string;
  error?: {
    message?: string;
  };
};

type RuntimeRunQueryResult = {
  actionRuns: RuntimeRow[];
  sourceExplorationRuns: RuntimeRow[];
};

type RuntimeRunDetail = {
  runType: string;
  runId: string;
  status: string | null;
  errorMessage: string | null;
  correlationId: string | null;
  investigation: {
    summary?: string;
    references?: Record<string, unknown>;
  };
  relatedActionWritebacks: RuntimeRow[];
  runRelations: RuntimeRow[];
};

test("Operations Run investigation deep-links to backend detail evidence", async ({
  page,
}) => {
  const listResponse = await page.request.get(
    `${API_BASE_URL}/api/operations/runs?runType=action&limit=20`,
    { headers: DEMO_HEADERS },
  );
  expect(listResponse.ok()).toBe(true);
  const runs = (await listResponse.json()) as RuntimeRunQueryResult;
  const targetRun = runs.actionRuns.find(
    (run) =>
      run.status === "outcome_unknown" &&
      run.error?.message === "writeback outcome is unknown",
  );
  expect(targetRun?.id).toBeTruthy();
  const runId = String(targetRun?.id);

  const detailResponse = await page.request.get(
    `${API_BASE_URL}/api/operations/runs/action/${encodeURIComponent(runId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(detailResponse.ok()).toBe(true);
  const detail = (await detailResponse.json()) as RuntimeRunDetail;
  expect(detail.runType).toBe("action");
  expect(detail.runId).toBe(runId);
  expect(detail.errorMessage).toBe("writeback outcome is unknown");
  expect(detail.investigation.summary).toContain(runId);
  expect(detail.relatedActionWritebacks.length).toBeGreaterThanOrEqual(1);
  expect(detail.runRelations.length).toBeGreaterThanOrEqual(1);

  await page.goto(
    `/operations?tab=runs&runType=action&runId=${encodeURIComponent(runId)}`,
  );
  await expect(
    page.getByRole("heading", { name: "Platform Operations" }),
  ).toBeVisible();
  await expect(page.locator("body")).toContainText("Run 조사");
  await expect(page.locator("body")).toContainText(runId);
  await expect(page.locator("body")).toContainText("선택한 run 상세");
  await expect(page.locator("body")).toContainText("에러 상세");
  await expect(page.locator("body")).toContainText(
    "writeback outcome is unknown",
  );
  await expect(page.locator("body")).toContainText("조사 요약");
  await expect(page.locator("body")).toContainText(
    `action run ${runId} is outcome_unknown`,
  );
  await expect(page.locator("body")).toContainText("target_object_id");
  await expect(page.locator("body")).toContainText("O-1999");
  await expect(page.locator("body")).toContainText("관련 writeback");
  await expect(page.locator("body")).toContainText("run 관계");
});

test("failed Source Explorer run is durable, redacted, and opens in Operations", async ({
  page,
}) => {
  const sourceName = `unsupported_source_${Date.now()}`;
  const failedResponse = await page.request.post(
    `${API_BASE_URL}/api/sources/explore`,
    {
      headers: DEMO_HEADERS,
      data: {
        sourceName,
        sourceType: "unsupported_e2e",
        request: {
          password: "must-never-appear-in-operations",
          sample: [{ customerEmail: "private@example.test" }],
        },
      },
    },
  );
  expect(failedResponse.status()).toBe(400);
  const failure = (await failedResponse.json()) as {
    detail?: {
      details?: {
        operationsEvidence?: {
          runType?: string;
          runId?: string;
          operationsPath?: string;
        };
      };
    };
  };
  const evidence = failure.detail?.details?.operationsEvidence;
  expect(evidence?.runType).toBe("source_exploration");
  expect(evidence?.runId).toMatch(/^source_explore_/);
  expect(evidence?.operationsPath).toBe(
    `/api/operations/runs/source_exploration/${evidence?.runId}`,
  );

  const listResponse = await page.request.get(
    `${API_BASE_URL}/api/operations/runs?runType=source_exploration&status=failed&limit=20`,
    { headers: DEMO_HEADERS },
  );
  expect(listResponse.ok()).toBe(true);
  const runs = (await listResponse.json()) as RuntimeRunQueryResult;
  const run = runs.sourceExplorationRuns.find(
    (candidate) => candidate.id === evidence?.runId,
  );
  expect(run).toBeTruthy();
  expect(run).not.toHaveProperty("request");
  expect(JSON.stringify(run)).not.toContain("must-never-appear");
  expect(JSON.stringify(run)).not.toContain("private@example.test");

  const detailResponse = await page.request.get(
    `${API_BASE_URL}${evidence?.operationsPath}`,
    { headers: DEMO_HEADERS },
  );
  expect(detailResponse.ok()).toBe(true);
  const detail = (await detailResponse.json()) as RuntimeRunDetail;
  expect(detail.runType).toBe("source_exploration");
  expect(detail.runId).toBe(evidence?.runId);
  expect(detail.status).toBe("failed");
  expect(detail.errorMessage).toBe("source type does not support exploration");
  expect(JSON.stringify(detail)).not.toContain("must-never-appear");
  expect(JSON.stringify(detail)).not.toContain("private@example.test");

  await page.goto(
    `/operations?path=${encodeURIComponent(String(evidence?.operationsPath))}`,
  );
  await expect(
    page.getByRole("heading", { name: "Platform Operations" }),
  ).toBeVisible();
  await expect(page.locator("body")).toContainText("Source Explorer");
  await expect(page.locator("body")).toContainText(String(evidence?.runId));
  await expect(page.locator("body")).toContainText("선택한 run 상세");
  await expect(page.locator("body")).toContainText(
    "source type does not support exploration",
  );
});
