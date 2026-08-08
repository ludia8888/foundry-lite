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
