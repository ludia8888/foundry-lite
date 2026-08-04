import { expect, test } from "@playwright/test";

test("AI FDE makes branch scope and write approvals explicit before a governed run", async ({ page }) => {
  let runBody: Record<string, unknown> | null = null;
  await page.route("**/api/aip/fde/catalog", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        modes: [
          {
            modeId: "ontology_editing",
            title: "Ontology editing",
            description: "Branch-only ontology authoring",
            availability: "current",
            capabilities: ["ontology.inspect", "ontology.validate", "ontology.edit", "ontology.propose"],
            scopePrefixes: ["ontology-branch:"],
          },
          {
            modeId: "data_integration",
            title: "Data integration",
            description: "Governed pipeline authoring",
            availability: "current",
            capabilities: ["pipeline.inspect"],
            scopePrefixes: ["pipeline-branch:"],
          },
        ],
        tools: [
          {
            toolId: "ontology.branch.apply_patch",
            version: "v1",
            description: "Edit the isolated Ontology branch",
            effect: "WRITE",
            confirmationPolicy: "USER",
            requiredPermission: "ontology:validate",
            inputSchema: {},
            modeIds: ["ontology_editing"],
          },
          {
            toolId: "ontology.branch.propose",
            version: "v1",
            description: "Submit for human review",
            effect: "PROPOSE_WRITE",
            confirmationPolicy: "HUMAN_REVIEW",
            requiredPermission: "ontology:validate",
            inputSchema: {},
            modeIds: ["ontology_editing"],
          },
        ],
        toolDiscovery: ["lazy", "eager"],
        safetyBoundary: {
          writes: "ontology_branch_only",
          productionMerge: "human_proposal_review_required",
          identity: "invoking_user",
        },
      }),
    });
  });
  await page.route("**/api/aip/fde/run", async (route) => {
    runBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "ontology_editing",
        workspaceRef: "ontology-branch:ontbranch_browser_1",
        branchId: "ontbranch_browser_1",
        capabilities: ["ontology.inspect", "ontology.validate", "ontology.edit", "ontology.propose"],
        approvedToolIds: ["ontology.branch.apply_patch", "ontology.branch.propose"],
        toolDiscovery: "lazy",
        structuredOperations: [
          { operationType: "plan", objective: "Create the booking ontology", status: "presented" },
        ],
        agentRunId: "aip-fde-browser-1",
        aiRunId: "ai-fde-browser-1",
        sessionId: "aip-fde-session-browser-1",
        runStatus: "succeeded",
        answer: "Restaurant and Booking were added to the branch and a review proposal was created.",
        contextIds: [],
        citations: [],
        operations: null,
      }),
    });
  });

  await page.goto("/aip");
  await expect(page.getByText("실행 안전선")).toBeVisible();
  await expect(page.getByText("production unchanged")).toBeVisible();
  await page.getByPlaceholder("ontology-branch:… / pipeline-branch:… / source:…").fill("ontology-branch:ontbranch_browser_1");
  await page.getByPlaceholder("dataset:clean.restaurants, ontology-branch:…").fill("dataset:clean.restaurants");
  await page.getByText("ontology.branch.apply_patch", { exact: true }).click();
  await page.getByText("ontology.branch.propose", { exact: true }).click();
  await page.getByRole("button", { name: "AI FDE 실행" }).click();

  await expect(page.getByText("설계 실행 결과")).toBeVisible();
  await expect(page.getByText("Restaurant and Booking were added to the branch")).toBeVisible();
  expect(runBody).toMatchObject({
    workspaceRef: "ontology-branch:ontbranch_browser_1",
    mode: "ontology_editing",
    toolDiscovery: "lazy",
    approvedToolIds: ["ontology.branch.apply_patch", "ontology.branch.propose"],
    attachedContextRefs: ["dataset:clean.restaurants"],
  });
});

test("AI FDE Pilot creates one governed application bundle from an approved plan", async ({ page }) => {
  const plan = pilotPlan();
  let generationBody: Record<string, unknown> | null = null;
  let idempotencyKey = "";
  await page.route("**/api/aip/fde/catalog", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ modes: [], tools: [], toolDiscovery: ["lazy", "eager"], safetyBoundary: {} }),
    });
  });
  await page.route("**/api/aip/pilot/plan", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(plan) });
  });
  await page.route("**/api/aip/pilot/applications", async (route) => {
    generationBody = route.request().postDataJSON() as Record<string, unknown>;
    idempotencyKey = route.request().headers()["idempotency-key"] ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(pilotBundle()),
    });
  });

  await page.goto("/aip");
  await expect(page.getByText("실행 가능한 앱 생성")).toBeVisible();
  await page.getByRole("button", { name: "생성 계획 만들기" }).click();
  await expect(page.getByRole("button", { name: "Branch-first 앱 생성 승인" })).toBeVisible();
  await page.getByRole("button", { name: "Branch-first 앱 생성 승인" }).click();

  await expect(page.getByText("Dining Concierge 생성 완료")).toBeVisible();
  await expect(page.getByText("/projects/project_pilot_1/pilot/dining-concierge")).toBeVisible();
  expect(generationBody).toEqual({ plan });
  expect(idempotencyKey).toMatch(/^aip-pilot-generate-dining-concierge-/);
});

test("generated Pilot application exposes durable resource, branch, files, and seed evidence", async ({ page }) => {
  await page.route("**/api/resources?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "resource_pilot_1",
            rid: "ri.foundry-lite.resource.pilot-application-1",
            resourceType: "pilot_application",
            displayName: "Dining Concierge",
            projectId: "project_pilot_1",
            folderId: null,
            sourceSurface: "aip_pilot",
            sourceRef: "pilot-application-1",
            operationsPath: null,
            status: "active",
            metadata: { slug: "dining-concierge" },
            isFavorite: false,
            createdAt: "2026-08-04T00:00:00Z",
            updatedAt: "2026-08-04T00:00:00Z",
          },
        ],
        nextCursor: null,
      }),
    });
  });
  await page.route("**/api/aip/pilot/applications/ri.foundry-lite.resource.pilot-application-1", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(pilotBundle()),
    });
  });
  await page.route("**/api/datasets/pilot/dining_concierge_seed/preview?limit=25", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ restaurantId: "restaurant_1", name: "Seoul Table" }]),
    });
  });

  await page.goto("/projects/project_pilot_1/pilot/dining-concierge");
  await expect(page.getByRole("heading", { name: "Dining Concierge" })).toBeVisible();
  await expect(page.getByText("pilot.dining_concierge_seed")).toBeVisible();
  await expect(page.getByText("ontbranch_pilot_1")).toBeVisible();
  await expect(page.getByText("2 files + CI")).toBeVisible();
  await expect(page.getByText("Seoul Table")).toBeVisible();
  await expect(page.getByText("production 객체나 Action을 사용하지 않습니다")).toBeVisible();
});

function pilotPlan() {
  return {
    operationType: "pilot_application_plan",
    applicationName: "Dining Concierge",
    domainDescription: "외국인 여행자를 위한 예약 운영 앱",
    slug: "dining-concierge",
    projectDisplayName: "Dining Concierge",
    seed: { namespace: "pilot", name: "dining_concierge_seed" },
    ontologyResources: [{ kind: "object_type", apiName: "Restaurant" }],
    applicationResources: [{ kind: "osdk_application", apiName: "DiningConcierge" }],
    react: { entrypoint: "src/App.tsx" },
    ci: { workflow: ".github/workflows/ci.yml" },
    requiredApprovals: ["ontology_proposal_activation"],
  };
}

function pilotBundle() {
  return {
    applicationName: "Dining Concierge",
    applicationPath: "/projects/project_pilot_1/pilot/dining-concierge",
    status: "preview_ready",
    seed: { datasetRef: "pilot.dining_concierge_seed" },
    ontologyBranch: { id: "ontbranch_pilot_1" },
    reactFiles: { "src/App.tsx": "export default function App() {}", "src/osdk.ts": "export {}" },
    ciWorkflow: "name: ci",
    isReplayed: false,
  };
}
