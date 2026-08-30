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

  await expect(page.getByText("업무 데이터 미연결")).toBeVisible();
  await expect(page.getByText("화면 검토 가능 · 실제 업무 사용 전 데이터 연결 필요")).toBeVisible();
  await expect(await pageNameInput(page)).toBeEditable();

  await page.getByRole("button", { name: "사용자 미리보기" }).click();
  await expect(page.getByText("활성 온톨로지가 없습니다")).toBeVisible();
  await page.getByRole("button", { name: "AI FDE 검토" }).click();
  await expect(await pageNameInput(page)).toBeEditable();
});

test("Workshop exposes the rich template and full widget catalogs", async ({
  page,
}) => {
  await page.goto("/workshop");
  await waitForWorkshopReady(page);

  await page.getByRole("button", { name: "AI FDE 추천 구성 적용" }).click();
  await expect(
    page.getByRole("heading", { name: "템플릿으로 시작" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /운영 대시보드/ }).click();

  await expect(await pageNameInput(page)).toHaveValue("대시보드");
  await expect(page.getByText("핵심 숫자", { exact: true })).toBeVisible();
  await expect(page.getByText("비교 차트", { exact: true })).toBeVisible();
  await expect(page.getByText("비중 차트", { exact: true })).toBeVisible();

  await page
    .getByRole("button", { name: "핵심 숫자 설정 열기" })
    .first()
    .click();
  await expect(page.getByText("핵심 숫자 설정", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "보여줄 정보 추가" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("AI 업무 도우미");
  await expect(page.getByRole("dialog")).toContainText("진행 기록");
  await expect(page.getByRole("dialog")).toContainText("다음 업무");
});

test("Workshop v4 composes one responsive SaaS shell from governed components", async ({
  page,
}) => {
  await page.goto("/workshop");
  await waitForWorkshopReady(page);

  await page.getByRole("button", { name: "AI FDE 추천 구성 적용" }).click();
  await page.getByRole("button", { name: /운영 대시보드/ }).click();
  await expect(page.getByText("업무 흐름", { exact: true })).toBeVisible();
  await expect(page.getByText("교차 분석표", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "브랜드와 메뉴" }).click();
  await page.getByRole("textbox", { name: "서비스 이름" }).fill("Northstar Operations");
  await page.getByRole("textbox", { name: "로고 문자" }).fill("NO");
  await page.getByRole("button", { name: "에메랄드" }).click();
  await page.getByRole("button", { name: "촘촘하게" }).click();
  await page.getByRole("button", { name: "집중형" }).click();
  await page.getByRole("button", { name: "사용자 미리보기" }).click();

  const runtime = page.locator('main[aria-label$="업무 앱"]');
  await expect(runtime.getByText("Northstar Operations").first()).toBeVisible();
  await expect(runtime.getByText("오늘의 업무 흐름")).toBeVisible();
  await expect(runtime.locator('[data-workshop-widget="statusTracker"]')).toBeVisible();
  await expect(runtime.locator('[data-workshop-widget="pivotTable"]')).toBeVisible();

  const runtimeSearch = runtime.getByPlaceholder("고객, 업무, 담당자 검색");
  await page.keyboard.press("Meta+K");
  await expect(runtimeSearch).toBeFocused();
  await expect(page.getByRole("combobox", { name: "리소스 및 화면 검색" })).toBeHidden();

  await page.getByRole("button", { name: "도움말" }).click();
  await expect(page.getByRole("heading", { name: "업무 도움말" })).toBeVisible();
  await page.getByRole("button", { name: "닫기" }).click();
  await runtime.getByRole("button", { name: "알림", exact: true }).click();
  await expect(page.getByRole("heading", { name: "알림" })).toBeVisible();
  await page.getByRole("button", { name: "닫기" }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "메뉴 열기" })).toBeVisible();
  await expect(runtime.getByRole("heading", { name: "대시보드" })).toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await expect(page.getByRole("navigation", { name: "전체 업무 메뉴" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "빠른 업무 메뉴" })).toBeVisible();
  expect(
    await runtime.evaluate((element) => element.scrollWidth <= element.clientWidth),
  ).toBe(true);
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
    "GPT 안의 화면과 외부 앱에 동일하게 적용됩니다",
  );

  await (await pageNameInput(page)).fill(firstPageName);
  const firstSaveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/resources/register") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "변경사항 게시" }).click();
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
  await page.getByRole("button", { name: "변경사항 게시" }).click();
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
