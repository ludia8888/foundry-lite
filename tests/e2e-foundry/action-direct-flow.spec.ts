import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type OrderObject = {
  objectId: string;
  objectVersion: number;
  properties: {
    status?: string;
    operatorNote?: string;
  };
};

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function queryActionableOrder(page: Page): Promise<OrderObject> {
  const response = await page.request.post(
    `${API_BASE_URL}/api/objects/Order/query`,
    {
      headers: { ...DEMO_HEADERS, "Content-Type": "application/json" },
      data: { limit: 20 },
    },
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { items: OrderObject[] };
  const order = body.items.find(
    (item) =>
      item.objectId === "O-1002" &&
      ["PENDING", "REVIEW"].includes(item.properties.status ?? ""),
  ) ?? body.items.find((item) =>
    ["PENDING", "REVIEW"].includes(item.properties.status ?? ""),
  );
  expect(
    order,
    "fresh Foundry UI seed should include an Order that can still run ApproveOrder",
  ).toBeTruthy();
  return order as OrderObject;
}

async function getOrder(page: Page, objectId: string): Promise<OrderObject> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/objects/Order/${encodeURIComponent(objectId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as OrderObject;
}

async function applyApproveOrder(
  page: Page,
  order: OrderObject,
  reason: string,
  idempotencyKey: string,
) {
  const response = await page.request.post(
    `${API_BASE_URL}/api/actions/ApproveOrder/apply`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      data: {
        target: { objectType: "Order", objectId: order.objectId },
        expectedObjectVersion: order.objectVersion,
        params: { reason },
      },
    },
  );
  expect(response.ok()).toBe(true);
  return response.json();
}

test("Actions screen directly validates, applies, and idempotently replays ApproveOrder", async ({
  page,
}) => {
  const order = await queryActionableOrder(page);
  const reason = `Actions direct flow e2e ${Date.now()}`;

  await page.goto("/actions");
  await expect(page.locator("body")).toContainText("Action Types / Functions");

  await page.getByRole("button", { name: /ApproveOrder\s+→\s+Order/ }).click();
  await expect(page.locator("body")).toContainText(`대상 객체 · Order`);
  await page
    .getByRole("button", {
      name: new RegExp(`${escapeRegExp(order.objectId)}[\\s\\S]*v${order.objectVersion}`),
    })
    .click();

  await expect(page.locator("body")).toContainText(
    `대상: Order / ${order.objectId}`,
  );
  await expect(page.locator("body")).toContainText(
    `expectedObjectVersion=v${order.objectVersion}`,
  );
  await page.getByPlaceholder("reason").fill(reason);

  await page.getByRole("button", { name: "사전 검증" }).click();
  await expect(page.getByText("검증 통과")).toBeVisible();
  await expect(page.locator("body")).toContainText(/request_id=(?!—)\S+/);

  await page.getByRole("button", { name: "액션 실행" }).click();
  await expect(page.locator("body")).toContainText("succeeded");
  await expect(page.locator("body")).toContainText(/run_id=action_run_/);
  await expect(page.locator("body")).toContainText(/edit_id=edit_/);
  await expect(page.locator("body")).toContainText(
    `new_version=v${order.objectVersion + 1}`,
  );
  await expect(page.locator("body")).toContainText(/request_id=(?!—)\S+/);

  const updatedOrder = await getOrder(page, order.objectId);
  expect(updatedOrder.properties.status).toBe("APPROVED");
  expect(updatedOrder.properties.operatorNote).toBe(reason);
  expect(updatedOrder.objectVersion).toBe(order.objectVersion + 1);

  await page.getByRole("button", { name: "동일 요청 재전송" }).click();
  await expect(page.getByText("idempotent replay")).toBeVisible();

  const replayedOrder = await getOrder(page, order.objectId);
  expect(replayedOrder.properties.status).toBe("APPROVED");
  expect(replayedOrder.properties.operatorNote).toBe(reason);
  expect(replayedOrder.objectVersion).toBe(order.objectVersion + 1);
});

test("Actions screen recovers from a stale object-version conflict", async ({
  page,
}) => {
  const order = await getOrder(page, "O-2999");
  expect(order.objectVersion).toBe(1);
  expect(order.properties.status).toBe("PENDING");
  const uiReason = `Stale UI apply ${Date.now()}`;
  const concurrentReason = `Concurrent apply ${Date.now()}`;

  await page.goto("/actions");
  await expect(page.locator("body")).toContainText("Action Types / Functions");

  await page.getByRole("button", { name: /ApproveOrder\s+→\s+Order/ }).click();
  await page
    .getByRole("button", {
      name: new RegExp(`${escapeRegExp(order.objectId)}[\\s\\S]*v${order.objectVersion}`),
    })
    .click();
  await expect(page.locator("body")).toContainText(
    `대상: Order / ${order.objectId}`,
  );
  await expect(page.locator("body")).toContainText("expectedObjectVersion=v1");
  await page.getByPlaceholder("reason").fill(uiReason);

  await applyApproveOrder(
    page,
    order,
    concurrentReason,
    `actions-stale-conflict-${Date.now()}`,
  );
  const concurrentlyUpdated = await getOrder(page, order.objectId);
  expect(concurrentlyUpdated.objectVersion).toBe(2);
  expect(concurrentlyUpdated.properties.operatorNote).toBe(concurrentReason);

  await page.getByRole("button", { name: "액션 실행" }).click();
  await expect(page.locator("body")).toContainText("객체 버전이 변경되었습니다");
  await expect(page.locator("body")).toContainText("stale object version");
  await expect(page.locator("body")).toContainText("expected=v1 / current=v2");

  await page.getByRole("button", { name: "현재 버전 새로고침" }).click();
  await expect(page.locator("body")).toContainText("expectedObjectVersion=v2");
  await expect(page.getByText("stale object version")).toHaveCount(0);
});
