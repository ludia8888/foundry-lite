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
  let planBody: Record<string, unknown> | null = null;
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
    planBody = route.request().postDataJSON() as Record<string, unknown>;
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
  await expect(page.getByText("내 업무를 앱으로 설계하기")).toBeVisible();
  await page.getByRole("button", { name: "업무 설계 검토하기" }).click();
  await expect(page.getByText("앱 생성 가능")).toBeVisible();
  await page.getByRole("button", { name: "검토한 설계로 테스트 앱 만들기" }).click();

  await expect(page.getByText("테스트 앱이 준비되었습니다")).toBeVisible();
  await expect(page.getByRole("link", { name: "생성된 업무 앱 확인하기" })).toHaveAttribute(
    "href",
    "/projects/project_pilot_1/pilot/dining-concierge",
  );
  expect(planBody).toMatchObject({
    applicationName: "Dining Concierge",
    domainBrief: {
      actors: ["고객", "매니저", "홀 직원"],
      lifecycleStates: ["요청됨", "확인중", "확정됨", "방문완료", "취소됨"],
    },
  });
  expect((planBody?.domainBrief as Record<string, unknown>).records).toHaveLength(2);
  expect((planBody?.domainBrief as Record<string, unknown>).actions).toHaveLength(3);
  expect((planBody?.domainBrief as Record<string, unknown>).policies).toHaveLength(2);
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
  const domainBrief = {
    actors: ["고객", "매니저", "홀 직원"],
    records: [
      { name: "예약", apiName: "Reservation", fields: [{ name: "고객명", type: "string", required: true }] },
      { name: "테이블", apiName: "DiningTable", fields: [{ name: "좌석수", type: "integer", required: true }] },
    ],
    lifecycleStates: ["요청됨", "확인중", "확정됨", "방문완료", "취소됨"],
    actions: [
      { name: "예약 접수", apiName: "RequestReservation", fromStates: ["요청됨"], toState: "확인중", allowedActors: ["고객", "홀 직원"] },
      { name: "예약 확정", apiName: "ConfirmReservation", fromStates: ["확인중"], toState: "확정됨", allowedActors: ["매니저", "홀 직원"] },
      { name: "예약 취소", apiName: "CancelReservation", fromStates: ["확정됨"], toState: "취소됨", allowedActors: ["고객", "매니저"], requiresApproval: true },
    ],
    policies: [
      { name: "운영 시간 중복", statement: "같은 테이블의 이용 시간이 겹치면 예약할 수 없습니다.", enforcement: "blocking" },
      { name: "큰 모임", statement: "8명 이상 예약은 매니저가 한 번 확인합니다.", enforcement: "manual_review" },
    ],
    evidence: ["요청 시각", "규칙 판정 결과", "담당자"],
    integrations: ["예약 DB", "결제 서비스", "문자 알림"],
    successMeasures: ["중복 예약 0건", "예약 처리 2분 이내"],
  };
  return {
    operationType: "pilot_application_plan",
    applicationName: "Dining Concierge",
    domainDescription: "외국인 여행자를 위한 예약 운영 앱",
    domainBrief,
    domainOsBlueprint: {
      actors: domainBrief.actors,
      records: domainBrief.records,
      policies: domainBrief.policies,
      workflow: { states: domainBrief.lifecycleStates, actions: domainBrief.actions },
      readiness: { isReady: true, status: "ready_for_review", missingCount: 0, questions: [] },
    },
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
  const plan = pilotPlan();
  return {
    applicationName: "Dining Concierge",
    applicationPath: "/projects/project_pilot_1/pilot/dining-concierge",
    status: "preview_ready",
    domainOsBlueprint: plan.domainOsBlueprint,
    seed: { datasetRef: "pilot.dining_concierge_seed" },
    ontologyBranch: { id: "ontbranch_pilot_1" },
    reactFiles: { "src/App.tsx": "export default function App() {}", "src/osdk.ts": "export {}" },
    ciWorkflow: "name: ci",
    isReplayed: false,
  };
}
