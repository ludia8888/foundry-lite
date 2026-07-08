import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";
const WRITEBACK_ID = "writeback_web_reconcile_o1003";
const TARGET_ORDER_ID = "O-1003";
const APPROVAL_IDEMPOTENCY_KEY = "operations-web-approval-release-o1999";
const APPROVAL_TARGET_ORDER_ID = "O-1999";

type OrderObject = {
  objectId: string;
  objectVersion: number;
  properties: {
    status?: string;
    operatorNote?: string;
    margin?: number;
  };
};

type WritebackQueue = {
  items: Array<{
    writebackId: string;
    actionRunId: string;
    status: string;
    idempotencyKey: string;
    lastObservedStatus: string | null;
    request: Record<string, unknown>;
  }>;
};

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

test("Operations resolves an outcome_unknown writeback through the reconciliation UI", async ({
  page,
}) => {
  const before = await apiGet<OrderObject>(
    page,
    `/api/objects/Order/${TARGET_ORDER_ID}`,
  );
  expect(before.objectVersion).toBe(1);
  expect(before.properties.status).toBe("APPROVED");

  const queuedBefore = await apiGet<WritebackQueue>(
    page,
    "/api/operations/reconciliation/writebacks?status=outcome_unknown&limit=20",
  );
  const seeded = queuedBefore.items.find(
    (item) => item.writebackId === WRITEBACK_ID,
  );
  expect(seeded).toBeTruthy();
  expect(seeded?.status).toBe("outcome_unknown");
  expect(seeded?.lastObservedStatus).toBe("unknown");
  expect(seeded?.request.objectId).toBe(TARGET_ORDER_ID);

  await page.goto("/operations?tab=reconciliation");
  await expect(page.getByRole("heading", { name: "Platform Operations" })).toBeVisible();
  await expect(page.locator("body")).toContainText("Writeback 조정");
  await expect(page.locator("body")).toContainText(WRITEBACK_ID);

  await page.getByRole("row").filter({ hasText: WRITEBACK_ID }).click();
  await expect(page.locator("body")).toContainText("writeback 상세");
  await expect(page.locator("body")).toContainText("결과 미확정");
  await expect(page.locator("body")).toContainText("Operations reconciliation seed");

  await page
    .getByPlaceholder("remote resource id (미입력 시 writeback id 사용)")
    .fill("erp-approval-o1003-e2e");

  const resolveResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/operations/reconciliation/${WRITEBACK_ID}/resolve`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "succeeded로 조정" }).click();
  const resolved = (await (await resolveResponse).json()) as {
    status: string;
    remoteStatus: string;
    remoteResourceId: string;
    objectEditId?: string;
    newObjectVersion?: number;
  };

  expect(resolved.status).toBe("reconciled");
  expect(resolved.remoteStatus).toBe("succeeded");
  expect(resolved.remoteResourceId).toBe("erp-approval-o1003-e2e");
  expect(resolved.objectEditId).toMatch(/^edit_/);
  expect(resolved.newObjectVersion).toBe(2);

  await expect(page.locator("body")).toContainText("status=reconciled");
  await expect(page.locator("body")).toContainText("remote_status=succeeded");
  await expect(page.locator("body")).toContainText("object_edit_applied=true");

  const after = await apiGet<OrderObject>(
    page,
    `/api/objects/Order/${TARGET_ORDER_ID}`,
  );
  expect(after.objectVersion).toBe(2);
  expect(after.properties.status).toBe("APPROVED");
  expect(after.properties.operatorNote).toBe("Operations reconciliation seed");

  const queuedAfter = await apiGet<WritebackQueue>(
    page,
    "/api/operations/reconciliation/writebacks?status=outcome_unknown&limit=20",
  );
  expect(
    queuedAfter.items.some((item) => item.writebackId === WRITEBACK_ID),
  ).toBe(false);
});

test("Operations approval-releases a sensitive writeback recovery through the UI", async ({
  page,
}) => {
  const before = await apiGet<OrderObject>(
    page,
    `/api/objects/Order/${APPROVAL_TARGET_ORDER_ID}`,
  );
  expect(before.objectVersion).toBe(1);
  expect(before.properties.margin).not.toBe(7777.77);

  const queuedBefore = await apiGet<WritebackQueue>(
    page,
    "/api/operations/reconciliation/writebacks?status=outcome_unknown&limit=20",
  );
  const seeded = queuedBefore.items.find(
    (item) => item.idempotencyKey === APPROVAL_IDEMPOTENCY_KEY,
  );
  expect(seeded).toBeTruthy();
  expect(seeded?.status).toBe("outcome_unknown");
  expect(seeded?.request.externalWritebackUri).toBe(
    "memory://erp/orders/O-1999/margin",
  );
  const approvalWritebackId = seeded?.writebackId ?? "";
  expect(approvalWritebackId).toMatch(/^writeback_/);

  await page.goto("/operations?tab=reconciliation");
  await expect(page.locator("body")).toContainText(approvalWritebackId);
  await page.getByRole("row").filter({ hasText: approvalWritebackId }).click();
  await expect(page.locator("body")).toContainText("승인 후 recovery");

  await page.getByPlaceholder("approval id").fill("e2e-approval-margin-o1001");
  await page
    .getByPlaceholder("approval reason")
    .fill("finance reviewed remote margin write");
  await page
    .getByPlaceholder("externalWritebackUri (저장값 사용 가능)")
    .fill("memory://erp/orders/O-1999/margin");

  const approvalResponse = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(
          `/api/operations/reconciliation/${approvalWritebackId}/approve-recovery`,
        ) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "승인 후 recovery 실행" }).click();
  const approved = (await (await approvalResponse).json()) as {
    decision: string;
    approvalId: string;
    approvalReason: string;
    remoteStatus?: string;
    remoteResourceId?: string;
  };

  expect(approved.decision).toBe("reconciled");
  expect(approved.approvalId).toBe("e2e-approval-margin-o1001");
  expect(approved.approvalReason).toBe("sensitive_action_parameter");
  expect(approved.remoteStatus).toBe("succeeded");
  expect(approved.remoteResourceId).toBe("remote-sensitive-writeback");

  await expect(page.locator("body")).toContainText("decision=reconciled");
  await expect(page.locator("body")).toContainText(
    "approval_reason=sensitive_action_parameter",
  );
  await expect(page.locator("body")).toContainText(
    "remote_resource=remote-sensitive-writeback",
  );

  const after = await apiGet<OrderObject>(
    page,
    `/api/objects/Order/${APPROVAL_TARGET_ORDER_ID}`,
  );
  expect(after.objectVersion).toBe(before.objectVersion + 1);
  expect(after.properties.margin).toBe(7777.77);

  const queuedAfter = await apiGet<WritebackQueue>(
    page,
    "/api/operations/reconciliation/writebacks?status=outcome_unknown&limit=20",
  );
  expect(
    queuedAfter.items.some((item) => item.writebackId === approvalWritebackId),
  ).toBe(false);
});
