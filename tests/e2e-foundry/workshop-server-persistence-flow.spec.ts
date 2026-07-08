import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";

type WorkshopDefinition = {
  page: { name: string };
  version: number;
};

type WorkshopMetadata = {
  kind: string;
  schemaVersion: number;
  definition: WorkshopDefinition;
};

type ResourceItem = {
  rid: string;
  resourceType: string;
  displayName: string;
  sourceSurface: string;
  sourceRef: string;
  operationsPath: string | null;
  metadata: WorkshopMetadata;
};

async function listResources(page: Page): Promise<ResourceItem[]> {
  const response = await page.request.get(`${API_BASE_URL}/api/resources`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { items: ResourceItem[] };
  return body.items;
}

async function getResource(page: Page, rid: string): Promise<ResourceItem> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/resources/${encodeURIComponent(rid)}`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { resource: ResourceItem };
  return body.resource;
}

function workshopResources(resources: ResourceItem[]): ResourceItem[] {
  return resources.filter(
    (item) =>
      item.resourceType === "workshop_app" &&
      item.sourceSurface === "workshop" &&
      item.sourceRef === "default-workshop-app",
  );
}

async function waitForWorkshopReady(page: Page): Promise<void> {
  await expect(page.locator("body")).toContainText("Workshop");
  await expect(page.getByText("저장 상태 확인 중")).toHaveCount(0);
}

async function pageNameInput(page: Page) {
  return page.locator("input").first();
}

test("Workshop saves its app definition to the backend resource catalog and reloads it without localStorage", async ({
  page,
}) => {
  await page.addInitScript(() => window.localStorage.clear());
  const firstPageName = `Server persisted page ${Date.now()}`;
  const secondPageName = `${firstPageName} updated`;

  await page.goto("/workshop");
  await waitForWorkshopReady(page);
  await expect(page.locator("body")).toContainText(
    "앱 정의는 서버 Resources 카탈로그에 저장됩니다",
  );

  await (await pageNameInput(page)).fill(firstPageName);
  const firstSaveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/resources/register") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "저장 후 게시" }).click();
  const firstSave = (await (await firstSaveResponse).json()) as {
    resource: ResourceItem;
  };
  expect(firstSave.resource.metadata.definition.page.name).toBe(firstPageName);
  expect(firstSave.resource.metadata.definition.version).toBeGreaterThan(0);
  expect(firstSave.resource.operationsPath).toBe("/workshop");

  await expect
    .poll(async () => (await getResource(page, firstSave.resource.rid)).metadata.definition.page.name)
    .toBe(firstPageName);
  expect(workshopResources(await listResources(page))).toHaveLength(1);

  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await waitForWorkshopReady(page);
  await expect(await pageNameInput(page)).toHaveValue(firstPageName);

  await (await pageNameInput(page)).fill(secondPageName);
  const secondSaveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/resources/register") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "저장 후 게시" }).click();
  const secondSave = (await (await secondSaveResponse).json()) as {
    resource: ResourceItem;
  };
  expect(secondSave.resource.rid).toBe(firstSave.resource.rid);
  expect(secondSave.resource.metadata.definition.page.name).toBe(secondPageName);
  expect(secondSave.resource.metadata.definition.version).toBe(
    firstSave.resource.metadata.definition.version + 1,
  );

  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await waitForWorkshopReady(page);
  await expect(await pageNameInput(page)).toHaveValue(secondPageName);
  expect(workshopResources(await listResources(page))).toHaveLength(1);
});
