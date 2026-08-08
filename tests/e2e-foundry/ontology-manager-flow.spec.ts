import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type OntologyCatalog = {
  versionNumber: number;
  objectTypes: Array<{ apiName: string }>;
};

type OntologyBranchPayload = {
  id: string;
  name: string;
  status: string;
  proposalId: string | null;
};

type OntologyBranchListResult = {
  items: OntologyBranchPayload[];
};

type OntologyBranchDiffResult = {
  resources: Array<{
    kind: string;
    apiName: string;
    branchChange: string;
  }>;
};

type OntologyProposalPayload = {
  id: string;
  title: string;
  status: string;
};

type OntologyProposalListResult = {
  items: OntologyProposalPayload[];
};

function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now()} ${Math.random().toString(16).slice(2, 8)}`;
}

function pascalCaseApiName(value: string): string {
  return value
    .trim()
    .split(/[\s_-]+/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function selectOption(page: Page, label: string): Promise<void> {
  await page.getByRole("option", { name: label }).click();
}

test("Ontology Manager creates a branch, maps clean.orders into a new object type, and opens a proposal", async ({
  page,
}) => {
  const catalog = await apiGet<OntologyCatalog>(page, "/api/ontology/catalog");
  expect(catalog.objectTypes.some((item) => item.apiName === "Order")).toBe(true);

  const branchName = uniqueName("ontology-e2e-branch");
  const objectDisplayName = uniqueName("Ontology E2E Order View");
  const objectApiName = pascalCaseApiName(objectDisplayName);
  const proposalTitle = `${branchName} proposal`;

  await page.goto("/ontology");
  await expect(page.locator("body")).toContainText("Ontology Manager");
  await expect(page.locator("body")).toContainText("객체 타입");
  await expect(page.locator("body")).toContainText("Order");

  await page.getByRole("button", { name: "브랜치 만들기" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("브랜치 만들기");
  await page.getByLabel("브랜치 이름").fill(branchName);
  const createBranchResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/ontology/branches") &&
      response.request().method() === "POST",
  );
  await page.getByRole("dialog").getByRole("button", { name: "브랜치 만들기" }).click();
  const branch = (await (await createBranchResponse).json()) as OntologyBranchPayload;
  expect(branch.name).toBe(branchName);
  expect(branch.status).toBe("open");
  await expect(page.locator('[data-testid="ontology-branch-topbar"]')).toContainText(branchName);
  expect(
    (await apiGet<OntologyBranchListResult>(page, "/api/ontology/branches?limit=50")).items.some(
      (item) => item.id === branch.id,
    ),
  ).toBe(true);

  await page.getByRole("button", { name: "새 객체 타입" }).click();
  const newObjectDialog = page.getByRole("dialog");
  await expect(newObjectDialog).toContainText("새 객체 타입");
  await newObjectDialog.getByPlaceholder("예: Supplier").fill(objectDisplayName);
  await expect(newObjectDialog).toContainText(`API name: ${objectApiName}`);
  await newObjectDialog.getByRole("button", { name: "다음" }).click();

  await newObjectDialog.getByRole("combobox").first().click();
  await selectOption(page, "clean.orders");
  await newObjectDialog.getByRole("combobox").nth(1).click();
  await selectOption(page, "order_id");
  await expect(newObjectDialog).toContainText(/컬럼 \d+개 감지됨/);
  await newObjectDialog.getByRole("button", { name: "다음" }).click();
  await expect(newObjectDialog).toContainText(/columns →/);

  const validationResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/ontology/validate") &&
      response.request().method() === "POST",
  );
  await newObjectDialog.getByRole("button", { name: "드래프트 생성 · 검증" }).click();
  expect((await (await validationResponse).json()) as { status: string }).toMatchObject({
    status: "valid",
  });
  await expect(newObjectDialog).toContainText("검증 통과");

  const updateBranchResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/ontology/branches/${encodeURIComponent(branch.id)}/update`) &&
      response.request().method() === "POST",
  );
  await newObjectDialog.getByRole("button", { name: "브랜치에 저장" }).click();
  const updatedBranch = (await (await updateBranchResponse).json()) as OntologyBranchPayload;
  expect(updatedBranch.id).toBe(branch.id);
  await expect(newObjectDialog).toHaveCount(0);

  await expect
    .poll(async () => {
      const diff = await apiGet<OntologyBranchDiffResult>(
        page,
        `/api/ontology/branches/${encodeURIComponent(branch.id)}/diff`,
      );
      return (
        diff.resources.find(
          (resource) =>
            resource.kind === "objectType" &&
            resource.apiName === objectApiName &&
            resource.branchChange === "added",
        )?.apiName ?? null
      );
    })
    .toBe(objectApiName);
  await expect(page.locator("body")).toContainText(objectApiName);
  await expect(page.locator("body")).toContainText(/변경 \d+건/);

  await page.getByRole("button", { name: "변경 제안 만들기" }).click();
  await expect(page.getByRole("dialog")).toContainText("변경 제안 만들기");
  await page.getByLabel("제목 (필수)").fill(proposalTitle);
  await page.getByLabel("설명 (선택)").fill("Created by Foundry UI E2E from clean.orders column mapping.");
  const proposeResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/ontology/branches/${encodeURIComponent(branch.id)}/propose`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("dialog").getByRole("button", { name: "변경 제안" }).click();
  const proposedBranch = (await (await proposeResponse).json()) as OntologyBranchPayload;
  expect(proposedBranch.proposalId).toMatch(/^ontprop_/);

  await expect(page.locator("body")).toContainText("제안");
  await expect(page.locator("body")).toContainText(proposalTitle);
  const proposals = await apiGet<OntologyProposalListResult>(
    page,
    "/api/ontology/proposals?limit=50",
  );
  const proposal = proposals.items.find((item) => item.id === proposedBranch.proposalId);
  expect(proposal?.title).toBe(proposalTitle);
  expect(proposal?.status).toBe("submitted");
});
