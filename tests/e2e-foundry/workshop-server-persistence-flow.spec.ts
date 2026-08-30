import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = (
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

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
  return page.getByRole("textbox", { name: "페이지 이름" });
}

test("Workshop builder stays editable while the active Ontology is missing", async ({
  page,
}) => {
  await page.route("**/api/ontology/catalog", async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: "NOT_FOUND",
          message: "active ontology not found",
          details: {},
          request_id: "workshop-missing-ontology-e2e",
        },
      }),
    });
  });

  await page.goto("/workshop");

  await expect(page.getByText("온톨로지 미연결")).toBeVisible();
  await expect(page.getByText("빌더 편집 가능 · 런타임은 Ontology 필요")).toBeVisible();
  await expect(await pageNameInput(page)).toBeEditable();

  await page.getByRole("button", { name: "런타임" }).click();
  await expect(page.getByText("활성 온톨로지가 없습니다")).toBeVisible();
  await page.getByRole("button", { name: "빌더" }).click();
  await expect(await pageNameInput(page)).toBeEditable();
});

test("Workshop exposes the rich template and full widget catalogs", async ({
  page,
}) => {
  await page.goto("/workshop");
  await waitForWorkshopReady(page);

  await page.getByRole("button", { name: "템플릿으로 시작" }).click();
  await expect(
    page.getByRole("heading", { name: "템플릿으로 시작" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /운영 대시보드/ }).click();

  await expect(await pageNameInput(page)).toHaveValue("대시보드");
  await expect(page.getByText("메트릭 카드", { exact: true })).toBeVisible();
  await expect(page.getByText("막대 차트", { exact: true })).toBeVisible();
  await expect(page.getByText("파이 차트", { exact: true })).toBeVisible();

  await page
    .getByRole("button", { name: "메트릭 카드 위젯 선택" })
    .first()
    .click();
  await expect(page.getByText("metricCard", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "위젯 추가" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("AIP 챗봇");
  await expect(page.getByRole("dialog")).toContainText("타임라인");
  await expect(page.getByRole("dialog")).toContainText("버튼 그룹");
});

test("Workshop v3 composes one responsive SaaS shell from governed components", async ({
  page,
}) => {
  await page.goto("/workshop");
  await waitForWorkshopReady(page);

  await page.getByRole("button", { name: "템플릿으로 시작" }).click();
  await page.getByRole("button", { name: /운영 대시보드/ }).click();
  await expect(page.getByText("상태 추적기", { exact: true })).toBeVisible();
  await expect(page.getByText("피벗 테이블", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "앱 디자인과 탐색" }).click();
  await page.getByRole("textbox", { name: "서비스 이름" }).fill("Northstar Operations");
  await page.getByRole("textbox", { name: "로고 문자" }).fill("NO");
  await page.getByRole("button", { name: "에메랄드" }).click();
  await page.getByRole("button", { name: "촘촘하게" }).click();
  await page.getByRole("button", { name: "집중형" }).click();
  await page.getByRole("button", { name: "런타임" }).click();

  const runtime = page.getByRole("main", { name: "Workshop runtime canvas" });
  await expect(runtime.getByText("Northstar Operations").first()).toBeVisible();
  await expect(runtime.getByText("Live work pulse")).toBeVisible();
  await expect(runtime.locator('[data-workshop-widget="statusTracker"]')).toBeVisible();
  await expect(runtime.locator('[data-workshop-widget="pivotTable"]')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "탐색 열기" })).toBeVisible();
  await expect(runtime.getByRole("heading", { name: "대시보드" })).toBeVisible();
  await page.getByRole("button", { name: "탐색 열기" }).click();
  await expect(page.getByRole("navigation", { name: "모바일 업무 앱 페이지" })).toBeVisible();
});

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
