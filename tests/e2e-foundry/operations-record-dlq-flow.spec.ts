import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type DeadLetterRecord = {
  id: string;
  status: string;
  replay_status: string;
  replay_run_id: string | null;
};

async function getDeadLetterRecord(page: Page, recordId: string): Promise<DeadLetterRecord> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/operations/dead-letter-records/${encodeURIComponent(recordId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as DeadLetterRecord;
}

test("Operations Record DLQ retries and discards records through the backend queue", async ({
  page,
}) => {
  await page.goto("/operations?tab=dlq");
  await expect(page.getByRole("heading", { name: "Platform Operations" })).toBeVisible();
  await expect(page.locator("body")).toContainText("Record DLQ");
  await expect(page.locator("body")).toContainText("dlqr_web_retry");
  await expect(page.locator("body")).toContainText("dlqr_web_discard");

  await page.getByRole("row").filter({ hasText: "dlqr_web_retry" }).click();
  await expect(page.locator("body")).toContainText("record 상세");
  await expect(page.locator("body")).toContainText("payload-hash-web-retry");

  const retryResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/operations/dead-letter-records/dlqr_web_retry/retry") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "재시도" }).click();
  const retryPayload = (await (await retryResponse).json()) as {
    status: string;
    replayStatus: string;
    replayRunId: string;
  };
  expect(retryPayload.status).toBe("RESOLVED");
  expect(retryPayload.replayStatus).toBe("SUCCEEDED");
  expect(retryPayload.replayRunId).toMatch(/^sync_run_/);
  await expect(page.locator("body")).toContainText("retry 결과=RESOLVED");
  await expect(page.locator("body")).toContainText(/replay_run=sync_run_/);

  const retried = await getDeadLetterRecord(page, "dlqr_web_retry");
  expect(retried.status).toBe("RESOLVED");
  expect(retried.replay_status).toBe("SUCCEEDED");
  expect(retried.replay_run_id).toBe(retryPayload.replayRunId);

  await page.getByRole("row").filter({ hasText: "dlqr_web_discard" }).click();
  await expect(page.locator("body")).toContainText("payload-hash-web-discard");

  const discardResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/operations/dead-letter-records/dlqr_web_discard/discard") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "폐기" }).click();
  const discardPayload = (await (await discardResponse).json()) as {
    status: string;
    replayStatus: string;
    discardedAt: string;
  };
  expect(discardPayload.status).toBe("DISCARDED");
  expect(discardPayload.replayStatus).toBe("DISCARDED");
  expect(discardPayload.discardedAt).toBeTruthy();
  await expect(page.locator("body")).toContainText("discard 결과=DISCARDED");

  const discarded = await getDeadLetterRecord(page, "dlqr_web_discard");
  expect(discarded.status).toBe("DISCARDED");
  expect(discarded.replay_status).toBe("DISCARDED");
  expect(discarded.replay_run_id).toBeNull();
  await expect(page.locator("body")).toContainText("2건 · 미해결 0건");
});
