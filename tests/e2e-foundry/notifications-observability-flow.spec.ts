import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";

type StoredIncident = {
  id: string;
  detectorId: string;
  message: string;
  status: string;
};

type StoredReport = {
  storedIncidents: StoredIncident[];
};

async function recordIncident(page: Page, detectorId: string): Promise<StoredIncident> {
  const response = await page.request.post(
    `${API_BASE_URL}/api/operations/observability/detect-and-record`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Content-Type": "application/json",
      },
      data: {
        observedAt: "2035-01-01T00:00:00Z",
        configs: [
          {
            detectorId,
            detectorType: "flow_interruption",
            configVersion: "notifications-e2e",
            owner: "ops_manager",
            severity: "critical",
            runType: "transform",
            expectedCadenceSeconds: 1,
          },
        ],
      },
    },
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as StoredReport;
  expect(body.storedIncidents).toHaveLength(1);
  expect(body.storedIncidents[0].status).toBe("open");
  return body.storedIncidents[0];
}

async function listOpenIncidents(page: Page): Promise<StoredIncident[]> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/operations/observability/incidents?status=open&limit=10`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as StoredIncident[];
}

test("Shell notifications render backend observability incidents instead of a future placeholder", async ({
  page,
}) => {
  const detectorId = `notifications-e2e-${Date.now()}`;
  const incident = await recordIncident(page, detectorId);
  await expect
    .poll(async () =>
      (await listOpenIncidents(page)).some((item) => item.id === incident.id),
    )
    .toBe(true);

  await page.goto("/");
  await expect(page.locator("body")).toContainText(
    "Foundry에 오신 것을 환영합니다",
  );
  const notificationButton = page.getByRole("button", {
    name: "운영 알림 1건",
  });
  await expect(notificationButton).toBeVisible();
  await notificationButton.click();

  const popover = page.locator('[data-radix-popper-content-wrapper]');
  await expect(popover).toContainText("운영 알림");
  await expect(popover).toContainText("Operations observability open incidents");
  await expect(popover).toContainText(detectorId);
  await expect(popover).toContainText(incident.message);
  await expect(popover).not.toContainText("백엔드 미지원");
  await expect(popover).not.toContainText("예정");

  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "네비게이션 열기" }).click();
  const sidebar = page.locator("aside");
  await expect(sidebar).toBeVisible();
  const sidebarNotificationButton = sidebar.getByRole("button", {
    name: "운영 알림 1건",
  });
  await expect(sidebarNotificationButton).toBeVisible();
  await sidebarNotificationButton.click();

  const sidebarPopover = page
    .locator('[data-radix-popper-content-wrapper]')
    .last();
  await expect(sidebarPopover).toContainText(
    "Operations observability open incidents",
  );
  await expect(sidebarPopover).toContainText(detectorId);
  await expect(sidebarPopover).toContainText(incident.message);
  await expect(page.locator("body")).not.toContainText(
    "Notifications — 백엔드 미지원",
  );
});
