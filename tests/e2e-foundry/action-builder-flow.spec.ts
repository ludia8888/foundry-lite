import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
  "X-User-Attributes": JSON.stringify({ department: "sales", region: "apac" }),
};
const REVIEWER_HEADERS = {
  ...DEMO_HEADERS,
  "X-User-ID": "action-builder-reviewer",
};
const API_BASE_URL = process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type OntologyBranch = {
  id: string;
  name: string;
  contentFingerprint: string;
};

type BranchActionMutation = {
  actionType: {
    apiName: string;
    contractFingerprint: string;
    parameterSchema: Record<string, unknown>;
  };
  branch: OntologyBranch;
  isReplay: boolean;
};

type OntologyProposal = {
  id: string;
  status: string;
  fingerprint: string;
  executionStatus: string | null;
  appliedOntologyVersion: Record<string, unknown> | null;
};

type OsdkResource = {
  resourceType: "action" | "object" | "link";
  resourceApiName: string;
  scopes: string[];
};

async function createBranch(page: Page, name: string): Promise<OntologyBranch> {
  const response = await page.request.post(`${API_BASE_URL}/api/ontology/branches`, {
    headers: {
      ...DEMO_HEADERS,
      "Content-Type": "application/json",
      "Idempotency-Key": `action-builder-branch-${name}`,
    },
    data: { name },
  });
  expect(response.ok()).toBe(true);
  return response.json() as Promise<OntologyBranch>;
}

async function createMediaSet(page: Page, name: string): Promise<string> {
  const response = await page.request.post(`${API_BASE_URL}/api/media/sets`, {
    headers: { ...DEMO_HEADERS, "Content-Type": "application/json" },
    data: {
      namespace: "action_e2e",
      name,
      schemaType: "document",
      primaryFormat: "pdf",
      allowedInputFormats: ["pdf"],
      classification: "internal",
    },
  });
  expect(response.ok()).toBe(true);
  return `action_e2e.${name}`;
}

async function approveProposal(page: Page, proposal: OntologyProposal): Promise<void> {
  const assign = await page.request.post(
    `${API_BASE_URL}/api/ontology/proposals/${encodeURIComponent(proposal.id)}/assign`,
    {
      headers: { ...REVIEWER_HEADERS, "Content-Type": "application/json" },
      data: { reviewerUserId: REVIEWER_HEADERS["X-User-ID"] },
    },
  );
  expect(assign.ok()).toBe(true);
  const decision = await page.request.post(
    `${API_BASE_URL}/api/ontology/proposals/${encodeURIComponent(proposal.id)}/decide`,
    {
      headers: { ...REVIEWER_HEADERS, "Content-Type": "application/json" },
      data: {
        decision: "approve",
        expectedFingerprint: proposal.fingerprint,
        comment: "Independent Action Builder E2E review",
      },
    },
  );
  expect(decision.ok()).toBe(true);
}

async function proveLinkedCriteriaScopeIntersection(
  page: Page,
  actionApiName: string,
  expectedObjectVersion: number,
): Promise<void> {
  const clientId = `linked-criteria-client-${Date.now()}`;
  const scopes = {
    action: `osdk:action:${actionApiName}:validate`,
    target: "osdk:object:Order:read",
    link: "osdk:link:OrderCustomer:read",
    linkedObject: "osdk:object:Customer:read",
  };
  const baseResources: OsdkResource[] = [
    { resourceType: "action", resourceApiName: actionApiName, scopes: [scopes.action] },
    { resourceType: "object", resourceApiName: "Order", scopes: [scopes.target] },
    { resourceType: "object", resourceApiName: "Customer", scopes: [scopes.linkedObject] },
  ];
  const created = await page.request.post(
    `${API_BASE_URL}/api/developer-console/osdk-applications`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": `linked-criteria-app-${Date.now()}`,
      },
      data: {
        appApiName: `linkedCriteriaApp${Date.now()}`,
        displayName: "Linked criteria permission E2E",
        clientId,
        resources: baseResources,
      },
    },
  );
  expect(created.ok()).toBe(true);
  const appId = ((await created.json()) as { application: { id: string } }).application.id;
  const allTokenScopes = Object.values(scopes);
  const validate = (tokenScopes: string[]) =>
    page.request.post(`${API_BASE_URL}/api/actions/${encodeURIComponent(actionApiName)}/validate`, {
      headers: {
        ...DEMO_HEADERS,
        "X-Foundry-Lite-App-ID": appId,
        "X-Foundry-Lite-Client-ID": clientId,
        "X-Foundry-Lite-Scopes": tokenScopes.join(" "),
      },
      data: {
        target: { objectType: "Order", objectId: "O-1001" },
        expectedObjectVersion,
        params: {},
      },
    });

  const appDenied = await validate(allTokenScopes);
  expect(appDenied.status()).toBe(403);
  expect(await appDenied.json()).toMatchObject({
    detail: { details: { requiredScope: scopes.link } },
  });

  const updated = await page.request.put(
    `${API_BASE_URL}/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/resources`,
    {
      headers: {
        ...DEMO_HEADERS,
        "Idempotency-Key": `linked-criteria-resources-${Date.now()}`,
      },
      data: {
        resources: [
          ...baseResources,
          { resourceType: "link", resourceApiName: "OrderCustomer", scopes: [scopes.link] },
        ],
      },
    },
  );
  expect(updated.ok()).toBe(true);

  const tokenDenied = await validate(allTokenScopes.filter((scope) => scope !== scopes.linkedObject));
  expect(tokenDenied.status()).toBe(403);
  expect(await tokenDenied.json()).toMatchObject({
    detail: { details: { requiredScope: scopes.linkedObject } },
  });

  const allowed = await validate(allTokenScopes);
  expect(allowed.ok()).toBe(true);
  expect(await allowed.json()).toMatchObject({
    result: "VALID",
    submissionCriteriaEvaluation: { status: "PASSED" },
  });

  const attributeDenied = await page.request.post(
    `${API_BASE_URL}/api/actions/${encodeURIComponent(actionApiName)}/validate`,
    {
      headers: {
        ...DEMO_HEADERS,
        "X-User-Attributes": JSON.stringify({ department: "support" }),
        "X-Foundry-Lite-App-ID": appId,
        "X-Foundry-Lite-Client-ID": clientId,
        "X-Foundry-Lite-Scopes": allTokenScopes.join(" "),
      },
      data: {
        target: { objectType: "Order", objectId: "O-1001" },
        expectedObjectVersion,
        params: {},
      },
    },
  );
  expect(attributeDenied.ok()).toBe(true);
  expect(await attributeDenied.json()).toMatchObject({
    result: "INVALID",
    submissionCriteriaEvaluation: { status: "FAILED" },
  });
}

test("Action Builder creates a governed v3 contract on an isolated Ontology branch", async ({ page }) => {
  const suffix = `${Date.now()}${Math.random().toString(16).slice(2, 6)}`;
  const branchName = `action-builder-${suffix}`;
  const actionApiName = `SetOperatorNote${suffix}`;
  const notificationPolicyName = `operations${suffix}`;
  const notificationTargetRef = `notification-policy:${notificationPolicyName}`;
  const branch = await createBranch(page, branchName);
  const mediaSet = await createMediaSet(page, `receipts-${suffix}`);

  await page.setExtraHTTPHeaders({ "X-User-Attributes": DEMO_HEADERS["X-User-Attributes"] });
  await page.goto("/actions");
  await page.getByRole("tab", { name: "알림 정책" }).click();
  await page.getByRole("textbox", { name: "알림 정책 API name" }).fill(notificationPolicyName);
  await page.getByRole("textbox", { name: "알림 정책 표시 이름" }).fill("예약 운영팀");
  await page.getByRole("textbox", { name: "알림 수신자 1 user ID" }).fill("web-demo-operator");
  await page.getByRole("textbox", { name: "알림 수신자 1 roles" }).fill("ops_manager");
  const policyCreateResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/actions/notification-policies")
      && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "정책 생성" }).click();
  expect((await policyCreateResponse).status()).toBe(201);
  await expect(page.getByText(notificationTargetRef)).toBeVisible();
  await page.getByRole("textbox", { name: "알림 정책 표시 이름" }).fill("예약·식당 운영팀");
  const policyUpdateResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/api/actions/notification-policies/${notificationPolicyName}`)
      && response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "정책 갱신" }).click();
  expect((await policyUpdateResponse).ok()).toBe(true);
  await expect(page.getByText(/v2 · 1명/)).toBeVisible();
  await page.getByRole("tab", { name: "Action Builder" }).click();
  await expect(page.getByText("계약 정의")).toBeVisible();
  await expect(page.getByText("active Ontology 직접 편집 금지")).toBeVisible();

  await page.getByRole("combobox", { name: "작업 브랜치" }).click();
  await page.getByRole("option", { name: branchName }).click();
  await page.getByRole("button", { name: "새 Action" }).click();
  await page.getByRole("textbox", { name: "Action API name" }).fill(actionApiName);
  await page.getByRole("textbox", { name: "Action 표시 이름" }).fill("운영 메모 설정");
  await page.getByRole("textbox", { name: "Action AI tool 설명" }).fill(
    "검토 중인 주문의 운영 메모를 한 번에 변경합니다.",
  );
  await page.getByRole("combobox", { name: "Action 대상 객체" }).click();
  await page.getByRole("option", { name: "Order · Order" }).click();
  await page.getByRole("textbox", { name: "Action 조회 역할" }).fill("viewer, ops_manager, data_engineer");
  await page.getByRole("textbox", { name: "Action 편집 역할" }).fill("data_engineer");
  await page.getByRole("textbox", { name: "Action 실행 역할" }).fill("ops_manager");

  await page.getByRole("combobox", { name: "Action 실행 방식" }).click();
  await page.getByRole("option", { name: "Version-pinned function" }).click();
  await expect(page.getByRole("combobox", { name: "Action function batch execution mode" })).toBeVisible();
  await page.getByRole("combobox", { name: "Action function batch execution mode" }).click();
  await page.getByRole("option", { name: /List of structs 한 번 실행/ }).click();
  await expect(page.getByRole("textbox", { name: "Action function batch input name" })).toHaveValue("requests");
  await expect(page.getByRole("spinbutton", { name: "Action function maximum batch size" })).toHaveValue("10000");
  await expect(page.getByText(/어느 계산이나 OCC 검증이 실패해도 전체 Ontology 편집은 커밋되지 않습니다/)).toBeVisible();
  await page.getByRole("combobox", { name: "Action 실행 방식" }).click();
  await page.getByRole("option", { name: "선언형 편집 규칙" }).click();

  await page.getByRole("button", { name: "파라미터", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 API name" }).fill("mode");
  await page.getByText("기본값 · 조건부 override").first().click();
  await page.getByRole("textbox", { name: "mode 제약조건 enum" }).fill("routine, urgent");
  await page.getByText("기본값 · 조건부 override").first().click();
  await page.getByRole("button", { name: "파라미터", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 API name" }).nth(1).fill("reason");
  await page.getByText("기본값 · 조건부 override").nth(1).click();
  await page.getByRole("spinbutton", { name: "reason 제약조건 최소 길이" }).fill("5");
  await page.getByRole("spinbutton", { name: "reason 제약조건 최대 길이" }).fill("30");
  await page.getByRole("combobox", { name: "reason 기본값 종류" }).click();
  await page.getByRole("option", { name: "고정 값" }).click();
  await page.getByRole("textbox", { name: "reason 기본값 값" }).fill("Standard handling");
  await page.getByRole("button", { name: "override", exact: true }).click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값", exact: true }).click();
  await page.getByRole("option", { name: "mode", exact: true }).click();
  await page.getByRole("textbox", { name: "오른쪽 조건 값", exact: true }).fill("urgent");
  await page.getByRole("combobox", { name: "override 필수" }).click();
  await page.getByRole("option", { name: "예", exact: true }).click();
  await page.getByRole("combobox", { name: "일치 시 기본값 종류" }).click();
  await page.getByRole("option", { name: "고정 값" }).click();
  await page.getByRole("textbox", { name: "일치 시 기본값 값" }).fill("Urgent handling");
  await page.getByRole("checkbox", { name: "override 1 제약조건 변경" }).click();
  await page.getByRole("spinbutton", { name: "override 1 제약조건 최소 길이" }).fill("20");
  await page.getByRole("spinbutton", { name: "override 1 제약조건 최대 길이" }).fill("40");
  await page.getByRole("button", { name: "override", exact: true }).click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값", exact: true }).nth(1).click();
  await page.getByRole("option", { name: "mode", exact: true }).click();
  await page.getByRole("textbox", { name: "오른쪽 조건 값", exact: true }).nth(1).fill("urgent");
  await page.getByRole("combobox", { name: "override 표시" }).nth(1).click();
  await page.getByRole("option", { name: "아니요", exact: true }).click();

  await page.getByRole("button", { name: "파라미터", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 API name" }).nth(2).fill("guest");
  await page.getByRole("combobox", { name: "파라미터 타입" }).nth(2).click();
  await page.getByRole("option", { name: "struct", exact: true }).click();
  await page.getByRole("button", { name: "필드", exact: true }).last().click();
  await page.getByRole("textbox", { name: "guest struct 필드 API name" }).fill("name");

  await page.getByRole("button", { name: "파라미터", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 API name" }).nth(3).fill("receipt");
  await page.getByRole("combobox", { name: "파라미터 타입" }).nth(3).click();
  await page.getByRole("option", { name: "attachment", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 Media Set" }).fill(mediaSet);
  await page.getByRole("textbox", { name: "허용 MIME 타입" }).fill("application/pdf");
  await page.getByRole("spinbutton", { name: "미디어 최대 byte" }).fill("209715200");
  await page.getByText("기본값 · 조건부 override").nth(3).click();
  await page.getByRole("button", { name: "override", exact: true }).last().click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값", exact: true }).last().click();
  await page.getByRole("option", { name: "mode", exact: true }).click();
  await page.getByRole("textbox", { name: "오른쪽 조건 값", exact: true }).last().fill("urgent");
  await page.getByRole("combobox", { name: "override 필수" }).last().click();
  await page.getByRole("option", { name: "예", exact: true }).click();

  await page.getByRole("button", { name: "섹션", exact: true }).click();
  await page.getByRole("textbox", { name: "폼 섹션 2 ID" }).fill("urgent-details");
  await page.getByRole("textbox", { name: "폼 섹션 2 제목" }).fill("긴급 처리");
  await page.getByRole("checkbox", { name: "reason", exact: true }).last().click();
  await page.getByRole("button", { name: "조건", exact: true }).nth(1).click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값", exact: true }).last().click();
  await page.getByRole("option", { name: "mode", exact: true }).click();
  await page.getByRole("textbox", { name: "오른쪽 조건 값", exact: true }).last().fill("urgent");

  await page.getByRole("button", { name: "조건 추가" }).click();
  await page.getByRole("combobox", { name: "조건 노드 타입" }).last().click();
  await page.getByRole("option", { name: "all / any" }).click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값 출처" }).last().click();
  await page.getByRole("option", { name: "연결 객체 속성" }).click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값 연결 속성" }).click();
  await page.getByRole("option", { name: /Customer\.segment/ }).click();
  await page.getByRole("combobox", { name: "조건 연산자" }).last().click();
  await page.getByRole("option", { name: "contains", exact: true }).click();
  await page.getByRole("textbox", { name: "오른쪽 조건 값", exact: true }).last().fill("enterprise");
  await page.getByRole("button", { name: "하위 조건", exact: true }).click();
  await page.getByRole("combobox", { name: "왼쪽 조건 값 출처" }).last().click();
  await page.getByRole("option", { name: "현재 사용자" }).click();
  await page.getByRole("textbox", { name: "왼쪽 조건 값", exact: true }).last().fill("department");
  await page.getByRole("textbox", { name: "오른쪽 조건 값", exact: true }).last().fill("sales");
  await page.getByRole("textbox", { name: "제출 조건 불충족 안내" }).fill(
    "엔터프라이즈 고객의 주문만 변경할 수 있습니다.",
  );
  await page.getByRole("button", { name: "외부효과" }).click();
  await page.getByRole("combobox", { name: "외부효과 1 target reference" }).click();
  await page.getByRole("option", { name: /예약·식당 운영팀 · 1명/ }).click();
  await page.getByRole("button", { name: "필드", exact: true }).last().click();
  await page.getByRole("textbox", { name: "외부효과 payload field" }).fill("template");
  await page.getByRole("textbox", { name: "외부효과 payload value" }).fill("order-note-updated");
  await page.getByRole("checkbox", { name: "Action 되돌리기 허용" }).click();
  await page.getByRole("combobox", { name: "외부효과 보상 Action" }).click();
  await page.getByRole("option", { name: "ApproveOrder", exact: true }).click();
  await page.getByRole("button", { name: "속성", exact: true }).click();
  await page.getByRole("combobox", { name: "속성 값", exact: true }).click();
  await page.getByRole("option", { name: "reason" }).click();
  await page.getByRole("combobox", { name: "매핑 Ontology 속성" }).click();
  await page.getByRole("option", { name: /operatorNote/i }).click();
  const saveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/ontology/branches/${encodeURIComponent(branch.id)}/action-types`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "브랜치에 생성" }).click();
  const saved = (await (await saveResponse).json()) as BranchActionMutation;

  expect(saved.actionType.apiName).toBe(actionApiName);
  expect(saved.actionType.contractFingerprint).toMatch(/^sha256:/);
  expect(saved.actionType.parameterSchema["x-foundry-form-layout"]).toMatchObject({
    sections: [
      { id: "primary", parameterNames: ["mode", "guest", "receipt"] },
      {
        id: "urgent-details",
        parameterNames: ["reason"],
        visibleWhen: {
          op: "eq",
          left: { kind: "parameter", parameter: "mode" },
          right: { kind: "literal", value: "urgent" },
        },
      },
    ],
  });
  expect(saved.actionType.definition).toMatchObject({
    permissions: {
      viewRoles: ["viewer", "ops_manager", "data_engineer"],
      editRoles: ["data_engineer"],
      applyRoles: ["ops_manager"],
    },
    parameters: [
      { apiName: "mode", constraints: { enum: ["routine", "urgent"] } },
      {
        apiName: "reason",
        constraints: { minLength: 5, maxLength: 30 },
        default: { kind: "literal", value: "Standard handling" },
        overrides: [
          {
            when: {
              op: "eq",
              left: { kind: "parameter", parameter: "mode" },
              right: { kind: "literal", value: "urgent" },
            },
            config: {
              required: true,
              default: { kind: "literal", value: "Urgent handling" },
              constraints: { minLength: 20, maxLength: 40 },
            },
          },
          {
            when: {
              op: "eq",
              left: { kind: "parameter", parameter: "mode" },
              right: { kind: "literal", value: "urgent" },
            },
            config: { visible: false },
          },
        ],
      },
      {
        apiName: "guest",
        type: "struct",
        fields: [{ apiName: "name", type: "string" }],
      },
      {
        apiName: "receipt",
        type: "attachment",
        mediaSet,
        allowedMimeTypes: ["application/pdf"],
        maxBytes: 209715200,
        render: "filePicker",
        overrides: [
          {
            when: {
              op: "eq",
              left: { kind: "parameter", parameter: "mode" },
              right: { kind: "literal", value: "urgent" },
            },
            config: { required: true },
          },
        ],
      },
    ],
    submissionCriteria: {
      all: [
        {
          op: "contains",
          left: {
            kind: "linkedObjectProperty",
            linkType: "OrderCustomer",
            direction: "outgoing",
            property: "segment",
            aggregation: "values",
          },
          right: { kind: "literal", value: "enterprise" },
        },
        {
          op: "eq",
          left: { kind: "currentUser", attribute: "department" },
          right: { kind: "literal", value: "sales" },
        },
      ],
      message: "엔터프라이즈 고객의 주문만 변경할 수 있습니다.",
    },
    rules: [
      {
        kind: "modifyObject",
        assignments: [
          {
            property: "operatorNote",
            value: { kind: "parameter", parameter: "reason" },
          },
        ],
      },
    ],
    effects: [
      {
        effectId: "effect-1",
        kind: "notification",
        phase: "after_commit",
        targetRef: notificationTargetRef,
        maxAttempts: 3,
        timeoutSeconds: 30,
        payload: { template: "order-note-updated" },
      },
    ],
    actionLog: { enabled: true },
    revert: { enabled: true, compensationAction: "ApproveOrder" },
    branchPolicy: { allowExternalEffects: false },
  });
  expect(saved.branch.contentFingerprint).not.toBe(branch.contentFingerprint);
  expect(saved.isReplay).toBe(false);
  await expect(page.getByText("운영 메모 설정")).toBeVisible();
  await expect(page.getByText("Action 정의를 브랜치에 만들었습니다")).toBeVisible();

  const proposeResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/ontology/branches/${encodeURIComponent(branch.id)}/propose`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "변경 제안 만들기" }).click();
  const proposedBranch = (await (await proposeResponse).json()) as {
    proposalId: string;
    proposal: OntologyProposal;
  };
  expect(proposedBranch.proposalId).toMatch(/^ontprop_/);
  await expect(page).toHaveURL(new RegExp(`/approvals\\?source=ontology&proposalId=${proposedBranch.proposalId}`));
  await expect(page.getByText("운영 메모 설정 Action 활성화", { exact: true }).last()).toBeVisible();

  await approveProposal(page, proposedBranch.proposal);
  await page.reload();
  await expect(page.getByRole("button", { name: "제안 실행 (execute)" })).toBeVisible();
  const executeResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/ontology/proposals/${encodeURIComponent(proposedBranch.proposalId)}/execute`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "제안 실행 (execute)" }).click();
  const executed = (await (await executeResponse).json()) as OntologyProposal;
  expect(executed.executionStatus).toBe("executed");
  expect(executed.appliedOntologyVersion).not.toBeNull();

  const activeAction = await page.request.get(
    `${API_BASE_URL}/api/actions/${encodeURIComponent(actionApiName)}`,
    { headers: DEMO_HEADERS },
  );
  expect(activeAction.ok()).toBe(true);
  const engineerHeaders = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "action-engineer-without-apply",
    "X-Roles": "data_engineer",
  };
  const engineerAction = await page.request.get(
    `${API_BASE_URL}/api/actions/${encodeURIComponent(actionApiName)}`,
    { headers: engineerHeaders },
  );
  expect(engineerAction.ok()).toBe(true);
  expect(((await engineerAction.json()) as { access: Record<string, boolean> }).access).toEqual({
    canView: true,
    canEdit: true,
    canApply: false,
  });

  const orderBeforePermissionProof = await page.request.get(
    `${API_BASE_URL}/api/objects/Order/O-1001`,
    { headers: DEMO_HEADERS },
  );
  expect(orderBeforePermissionProof.ok()).toBe(true);
  const orderBeforePermission = (await orderBeforePermissionProof.json()) as { objectVersion: number };
  const engineerPlan = await page.request.post(
    `${API_BASE_URL}/api/actions/${encodeURIComponent(actionApiName)}/plan`,
    {
      headers: { ...engineerHeaders, "Content-Type": "application/json" },
      data: {
        target: { objectType: "Order", objectId: "O-1001" },
        expectedObjectVersion: orderBeforePermission.objectVersion,
        params: { mode: "routine", reason: "not authorized" },
      },
    },
  );
  expect(engineerPlan.status()).toBe(403);
  await proveLinkedCriteriaScopeIntersection(
    page,
    actionApiName,
    orderBeforePermission.objectVersion,
  );

  await page.goto("/actions");
  await page.getByRole("button", { name: new RegExp(`운영 메모 설정[\\s\\S]*${actionApiName}`) }).click();
  await page.getByRole("button", { name: /O-1001[\s\S]*PENDING/ }).click();
  await page.getByRole("combobox", { name: "mode" }).click();
  await page.getByRole("option", { name: "urgent", exact: true }).click();
  await expect(page.getByPlaceholder("reason")).toHaveValue("Urgent handling");
  await expect(page.getByText("override 1 적용")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "긴급 처리" })).toBeVisible();
  await expect(page.getByText("필수 파라미터 미입력")).toBeVisible();
  await expect(page.getByText("파라미터 제약조건 불충족")).toBeVisible();
  await expect(page.getByText(/제약조건 불충족: reason: minLength/)).toBeVisible();
  await expect(page.getByRole("button", { name: "액션 실행" })).toBeDisabled();
  await page.getByRole("combobox", { name: "mode" }).click();
  await page.getByRole("option", { name: "routine", exact: true }).click();
  await expect(page.getByText("override 1 적용")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "긴급 처리" })).toHaveCount(0);
  await expect(page.getByText("필수 파라미터 미입력")).toHaveCount(0);
  await expect(page.getByText("파라미터 제약조건 불충족")).toHaveCount(0);
  await page.locator('input[type="file"]').setInputFiles({
    name: "receipt.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\nFoundry Lite governed attachment evidence\n%%EOF"),
  });
  await expect(page.getByText(/연결됨:.*receipt\.pdf/)).toBeVisible();

  const validateResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/actions/${encodeURIComponent(actionApiName)}/validate`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "사전 검증" }).click();
  expect((await validateResponse).ok()).toBe(true);
  await expect(page.getByText("검증 통과")).toBeVisible();
  await expect(page.getByRole("region", { name: "제출 기준 판정" })).toContainText(
    "OrderCustomer",
  );

  const applyResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/actions/${encodeURIComponent(actionApiName)}/apply`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "액션 실행" }).click();
  const applied = await applyResponse;
  expect(applied.ok()).toBe(true);
  const appliedRun = (await applied.json()) as { actionRunId: string };
  await expect(page.getByText("succeeded", { exact: true })).toBeVisible();

  const updatedOrder = await page.request.get(`${API_BASE_URL}/api/objects/Order/O-1001`, {
    headers: DEMO_HEADERS,
  });
  expect(updatedOrder.ok()).toBe(true);
  expect(((await updatedOrder.json()) as { properties: Record<string, unknown> }).properties.operatorNote).toBe(
    "Standard handling",
  );

  await page.getByRole("tab", { name: "실행·로그" }).click();
  await expect(page.getByRole("region", { name: "Action Log" })).toContainText(actionApiName);
  await page.getByRole("button", { name: new RegExp(actionApiName) }).first().click();
  await expect(page.getByText("ApproveOrder", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "되돌리기" })).toBeVisible();
  const revertResponse = page.waitForResponse(
    (response) =>
      /\/api\/actions\/runs\/[^/]+\/revert$/.test(new URL(response.url()).pathname) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "되돌리기" }).click();
  expect((await revertResponse).ok()).toBe(true);

  const revertedOrder = await page.request.get(`${API_BASE_URL}/api/objects/Order/O-1001`, {
    headers: DEMO_HEADERS,
  });
  expect(revertedOrder.ok()).toBe(true);
  expect((await revertedOrder.json()) as { properties: Record<string, unknown> }).not.toHaveProperty(
    "properties.operatorNote",
  );

  const editedObjectLink = page
    .getByRole("region", { name: "Action Log" })
    .getByRole("button", { name: "객체 열기: Order/O-1001" })
    .first();
  await expect(editedObjectLink).toBeVisible();
  await editedObjectLink.click();
  await expect(page).toHaveURL(/\/objects\?objectType=Order&objectId=O-1001$/);
  await expect(page.getByText(/Order › O-1001 · v\d+/)).toBeVisible();

  await page.goto("/workshop");
  await expect(page.getByText("저장 상태 확인 중")).toHaveCount(0);
  await page.getByRole("button", { name: "위젯 추가" }).first().click();
  await page.getByRole("dialog").getByText("타임라인", { exact: true }).click();
  const objectField = page.locator("label").filter({ hasText: "객체 타입" }).last();
  await objectField.getByRole("combobox").click();
  await page.getByRole("option", { name: `[LOG] ${actionApiName}`, exact: true }).click();
  const dateField = page.locator("label").filter({ hasText: "날짜 속성" }).last();
  await dateField.getByRole("combobox").click();
  await page.getByRole("option", { name: "createdAt", exact: true }).click();
  await page.getByRole("button", { name: "보기" }).click();
  const actionLogTimeline = page.locator('[data-workshop-widget="timeline"]').last();
  await expect(actionLogTimeline).toContainText(appliedRun.actionRunId);
});

test("Action Builder authors interface create and link-constraint rules", async ({ page }) => {
  const suffix = `${Date.now()}${Math.random().toString(16).slice(2, 6)}`;
  const branchName = `interface-action-${suffix}`;
  const actionApiName = `SetAssetCustomer${suffix}`;
  const branch = await createBranch(page, branchName);

  await page.goto("/actions");
  await page.getByRole("tab", { name: "Action Builder" }).click();
  await page.getByRole("combobox", { name: "작업 브랜치" }).click();
  await page.getByRole("option", { name: branchName }).click();
  await page.getByRole("button", { name: "새 Action" }).click();
  await page.getByRole("textbox", { name: "Action API name" }).fill(actionApiName);
  await page.getByRole("textbox", { name: "Action 표시 이름" }).fill("Asset 고객 연결");

  await page.getByRole("combobox", { name: "Action 대상 종류" }).click();
  await page.getByRole("option", { name: "Interface", exact: true }).click();
  await page.getByRole("combobox", { name: "Action 대상 객체" }).click();
  await page.getByRole("option", { name: "Asset · Asset" }).click();
  await expect(page.getByText("Interface 공유 계약 · Asset")).toBeVisible();
  await page.getByRole("combobox", { name: "규칙 1 종류" }).click();
  await page.getByRole("option", { name: "객체 생성", exact: true }).click();

  await page.getByRole("button", { name: "파라미터", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 API name" }).fill("riskScore");
  await page.getByRole("combobox", { name: "파라미터 타입" }).click();
  await page.getByRole("option", { name: "float", exact: true }).click();
  await page.getByRole("button", { name: "속성", exact: true }).click();
  await page.getByRole("combobox", { name: "매핑 Ontology 속성" }).click();
  await page.getByRole("option", { name: /riskScore/i }).click();
  await page.getByRole("combobox", { name: "속성 값", exact: true }).click();
  await page.getByRole("option", { name: "riskScore", exact: true }).click();

  await page.getByRole("button", { name: "파라미터", exact: true }).click();
  await page.getByRole("textbox", { name: "파라미터 API name" }).nth(1).fill("customer");
  await page.getByRole("combobox", { name: "파라미터 타입" }).nth(1).click();
  await page.getByRole("option", { name: "object", exact: true }).click();
  await page.getByRole("textbox", { name: "customer 참조 타입" }).fill("Customer");

  await page.getByRole("combobox", { name: "추가할 규칙 종류" }).click();
  await page.getByRole("option", { name: "링크 생성", exact: true }).click();
  await page.getByRole("button", { name: "규칙", exact: true }).click();
  await page.getByRole("combobox", { name: "규칙 2 Interface Link Constraint" }).click();
  await page.getByRole("option", { name: /Customer · object Customer · one/ }).click();
  await page.getByRole("combobox", { name: "링크 도착 객체", exact: true }).click();
  await page.getByRole("option", { name: "customer", exact: true }).click();

  await page.getByRole("button", { name: "외부효과" }).click();
  await page.getByRole("combobox", { name: "외부효과 1 단계" }).click();
  await page.getByRole("option", { name: "커밋 전 writeback" }).click();
  await page.getByRole("textbox", { name: "외부효과 1 target reference" }).fill("connector:erp/orders");
  await page.getByRole("button", { name: "응답 필드" }).click();
  await page.getByRole("textbox", { name: "외부효과 response field" }).fill("riskScore");
  await page.getByRole("combobox", { name: "riskScore response type" }).click();
  await page.getByRole("option", { name: "float", exact: true }).click();
  await page.getByRole("combobox", { name: "속성 값 출처" }).click();
  await page.getByRole("option", { name: "writeback 응답" }).click();
  await page.getByRole("textbox", { name: "속성 값", exact: true }).fill("riskScore");

  const saveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/ontology/branches/${encodeURIComponent(branch.id)}/action-types`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "브랜치에 생성" }).click();
  const saved = (await (await saveResponse).json()) as BranchActionMutation;

  expect(saved.actionType.definition).toMatchObject({
    target: "Asset",
    targetKind: "interface",
    rules: [
      {
        kind: "createObject",
        objectType: "Asset",
        onInterface: "Asset",
        primaryKey: { kind: "parameter", parameter: "__target__" },
        assignments: [
          {
            property: "riskScore",
            value: { kind: "webhookResponse", field: "riskScore" },
          },
        ],
      },
      {
        kind: "createLink",
        onInterface: "Asset",
        interfaceLinkConstraint: "customer",
        source: { kind: "parameter", parameter: "__target__" },
        target: { kind: "parameter", parameter: "customer" },
      },
    ],
    effects: [
      {
        effectId: "effect-1",
        kind: "webhook",
        phase: "before_commit",
        targetRef: "connector:erp/orders",
        responseFields: { riskScore: "float" },
      },
    ],
  });
  await expect(page.getByText("Asset 고객 연결")).toBeVisible();
});
