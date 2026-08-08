import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type ResourceProject = {
  id: string;
  rid: string;
  displayName: string;
};

type ProjectFolder = {
  id: string;
  rid: string;
  displayName: string;
  status: string;
};

type ResourceItem = {
  rid: string;
  displayName: string;
  status: string;
  folderId: string | null;
  isFavorite: boolean;
};

function e2eName(prefix: string): string {
  return `${prefix} ${Date.now()} ${Math.random().toString(16).slice(2, 8)}`;
}

async function listProjects(page: Page): Promise<ResourceProject[]> {
  const response = await page.request.get(`${API_BASE_URL}/api/projects`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { projects: ResourceProject[] };
  return body.projects;
}

async function listFolders(page: Page, projectId: string): Promise<ProjectFolder[]> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/folders?includeTrashed=true`,
    { headers: DEMO_HEADERS },
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { folders: ProjectFolder[] };
  return body.folders;
}

async function listResources(page: Page, projectId: string): Promise<ResourceItem[]> {
  const response = await page.request.get(
    `${API_BASE_URL}/api/resources?projectId=${encodeURIComponent(projectId)}&includeTrashed=true`,
    { headers: DEMO_HEADERS },
  );
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

async function createCatalogEntity(
  page: Page,
  triggerName: "새 프로젝트" | "새 폴더",
  entityName: string,
): Promise<void> {
  await page.getByRole("button", { name: triggerName }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: triggerName })).toBeVisible();
  await dialog.getByLabel("이름").fill(entityName);
  await dialog.getByRole("button", { name: "생성" }).click();
  await expect(dialog).toContainText("생성 완료");
  await expect(dialog).toContainText(`name=${entityName}`);
  await expect(dialog).toContainText(/idempotency_key=/);
  await expect(dialog).toContainText(/request_id=(?!—)\S+/);
  await dialog.getByRole("button", { name: "닫기" }).click();
}

test("Projects screen creates a project and folder, then manages a synced resource through favorite, folder move, trash, and restore", async ({
  page,
}) => {
  const projectName = e2eName("Foundry E2E Project");
  const folderName = e2eName("Foundry E2E Folder");

  await page.goto("/projects");
  await expect(page.locator("body")).toContainText("프로젝트");

  await createCatalogEntity(page, "새 프로젝트", projectName);
  await expect
    .poll(async () =>
      (await listProjects(page)).find((project) => project.displayName === projectName) ?? null,
    )
    .not.toBeNull();
  const project = (await listProjects(page)).find(
    (candidate) => candidate.displayName === projectName,
  ) as ResourceProject;

  await expect(page.getByText(projectName).first()).toBeVisible();
  await page.locator("aside").getByRole("combobox").first().click();
  await page.getByRole("option", { name: projectName }).click();

  await createCatalogEntity(page, "새 폴더", folderName);
  await expect
    .poll(async () =>
      (await listFolders(page, project.id)).find((folder) => folder.displayName === folderName) ?? null,
    )
    .not.toBeNull();
  const folder = (await listFolders(page, project.id)).find(
    (candidate) => candidate.displayName === folderName,
  ) as ProjectFolder;
  expect(folder.status).toBe("active");
  await expect(page.getByText(folderName).first()).toBeVisible();

  await page.getByRole("button", { name: /카탈로그 동기화/ }).first().click();
  await expect
    .poll(async () =>
      (await listResources(page, project.id)).filter((item) => item.status === "active").length,
    )
    .toBeGreaterThan(0);

  const resource = (await listResources(page, project.id)).find(
    (item) => item.status === "active",
  ) as ResourceItem;
  const catalogButton = page.locator("aside").getByRole("button", {
    name: /프로젝트 카탈로그/,
  });
  await expect(catalogButton).toBeEnabled();
  await expect(catalogButton).not.toContainText("future");
  await page
    .locator("aside")
    .getByRole("button", { name: new RegExp(folderName) })
    .click();
  await expect(
    page.locator("tbody tr", { hasText: resource.displayName }),
  ).toHaveCount(0);
  await catalogButton.click();

  await page.getByPlaceholder("이름 또는 RID 검색").fill(resource.rid);
  const row = page.locator("tbody tr", { hasText: resource.displayName }).first();
  await expect(row).toBeVisible();
  await row.hover();
  await row.getByLabel("즐겨찾기 추가").click();
  await expect.poll(async () => (await getResource(page, resource.rid)).isFavorite).toBe(true);

  await page.getByPlaceholder("이름 또는 RID 검색").fill(resource.rid);
  await row.click();
  const drawer = page.locator("aside", { hasText: resource.displayName }).last();
  await expect(drawer).toContainText(resource.rid);
  await drawer.getByRole("combobox").click();
  await page.getByRole("option", { name: folderName }).click();
  await drawer.getByRole("button", { name: "이동" }).click();
  await expect.poll(async () => (await getResource(page, resource.rid)).folderId).toBe(folder.id);

  await drawer.getByRole("button", { name: "휴지통으로" }).click();
  await expect.poll(async () => (await getResource(page, resource.rid)).status).toBe("trashed");

  await page.locator("aside").getByRole("button", { name: /휴지통/ }).click();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "복원" }).click();
  await expect.poll(async () => (await getResource(page, resource.rid)).status).toBe("active");
});
