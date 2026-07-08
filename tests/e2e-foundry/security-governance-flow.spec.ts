import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL = "http://127.0.0.1:8000";

type ResourceProject = {
  id: string;
  rid: string;
  displayName: string;
};

type ProjectGrant = {
  id: string;
  principalId: string;
  principalType: string;
  role: string;
};

type OsdkApplication = {
  application: {
    id: string;
    app_api_name?: string;
    appApiName?: string;
    display_name?: string;
    displayName?: string;
    status: string;
  };
  clients: Array<{
    id: string;
    client_id?: string;
    clientId?: string;
    status: string;
    redirect_uris?: string[];
    redirectUris?: string[];
  }>;
  resources: Array<Record<string, unknown>>;
};

type AuditEvent = {
  event_type?: string;
  request_id?: string;
  correlation_id?: string;
};

type RuntimeRunQueryResult = {
  auditEvents: AuditEvent[];
};

function uniqueName(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function apiPost<T>(
  page: Page,
  path: string,
  data: Record<string, unknown>,
  idempotencyKey: string,
): Promise<T> {
  const response = await page.request.post(`${API_BASE_URL}${path}`, {
    headers: {
      ...DEMO_HEADERS,
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    data,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function createProject(
  page: Page,
  displayName: string,
): Promise<ResourceProject> {
  const body = await apiPost<{ project: ResourceProject }>(
    page,
    "/api/projects",
    { displayName },
    `e2e-security-project-${displayName}`,
  );
  return body.project;
}

async function listGrants(page: Page, projectId: string): Promise<ProjectGrant[]> {
  const body = await apiGet<{ grants: ProjectGrant[] }>(
    page,
    `/api/projects/${encodeURIComponent(projectId)}/grants`,
  );
  return body.grants;
}

async function createOsdkApplication(
  page: Page,
  appApiName: string,
  displayName: string,
): Promise<OsdkApplication> {
  return apiPost<OsdkApplication>(
    page,
    "/api/developer-console/osdk-applications",
    { appApiName, displayName },
    `e2e-security-app-${appApiName}`,
  );
}

async function createOsdkClient(
  page: Page,
  appId: string,
  clientId: string,
): Promise<void> {
  await apiPost<Record<string, unknown>>(
    page,
    `/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/clients`,
    {
      clientId,
      redirectUris: ["http://127.0.0.1:4173/oauth/callback"],
      allowedScopes: [],
    },
    `e2e-security-client-${clientId}`,
  );
}

function isMutationAudit(event: AuditEvent): boolean {
  return /\.(created|updated|upserted|deleted|promoted|approved|assigned|aborted|failed|deactivated|revoked)$/.test(
    event.event_type ?? "",
  );
}

test("Security screen manages project grants, proves denied mutation state, and renders auth/audit evidence", async ({
  page,
}) => {
  const projectName = uniqueName("Security E2E Project");
  const principalId = uniqueName("security-principal");
  const appApiName = uniqueName("securityE2EApp");
  const appDisplayName = `Security E2E App ${appApiName}`;
  const clientId = uniqueName("security-e2e-client");

  const project = await createProject(page, projectName);
  const app = await createOsdkApplication(page, appApiName, appDisplayName);
  await createOsdkClient(page, app.application.id, clientId);

  await page.goto("/security");
  await expect(page.locator("body")).toContainText("Security & Governance");
  await expect(page.locator("body")).toContainText("프로젝트 권한");

  await page.locator("section", { hasText: "프로젝트" }).getByRole("combobox").click();
  await page.getByRole("option", { name: projectName }).click();
  await expect(page.locator("body")).toContainText(project.rid);

  await page.getByPlaceholder("사용자 또는 그룹 추가…").fill(principalId);
  const grantResponse = page.waitForResponse(
    (response) =>
      response.url().includes(
        `/api/projects/${encodeURIComponent(project.id)}/grants/user/${encodeURIComponent(principalId)}`,
      ) && response.request().method() === "PUT",
  );
  await page.locator("section", { hasText: "역할 (Grants)" }).getByRole("button", { name: "추가" }).click();
  const grantPayload = (await (await grantResponse).json()) as {
    grant: ProjectGrant;
  };
  expect(grantPayload.grant.principalId).toBe(principalId);
  expect(grantPayload.grant.role).toBe("viewer");

  await expect(page.locator("body")).toContainText("마지막 upsert 성공");
  await expect(page.locator("body")).toContainText(principalId);
  await expect(page.locator("body")).toContainText("idempotency");
  expect(
    (await listGrants(page, project.id)).some(
      (grant) => grant.principalId === principalId && grant.role === "viewer",
    ),
  ).toBe(true);

  const deniedResponse = page.waitForResponse(
    (response) =>
      response.url().includes(
        `/api/projects/${encodeURIComponent(project.id)}/grants/user/security-probe-target`,
      ) && response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "viewer 롤로 upsert 시도" }).click();
  const deniedPayload = (await (await deniedResponse).json()) as {
    detail: { code: string; request_id?: string };
  };
  expect(deniedPayload.detail.code).toBe("PERMISSION_DENIED");
  await expect(page.locator("body")).toContainText("권한 거부됨");
  await expect(page.locator("body")).toContainText("PERMISSION_DENIED");
  await expect(page.locator("body")).toContainText("request id");

  await page.getByRole("tab", { name: "인증 · 세션" }).click();
  await expect(page.locator("body")).toContainText("현재 세션");
  await expect(page.locator("body")).toContainText("tenant-demo");
  await expect(page.locator("body")).toContainText("web-demo-operator");
  await expect(page.locator("body")).toContainText(appDisplayName);
  await expect(page.locator("body")).toContainText(appApiName);
  await expect(page.locator("body")).toContainText(clientId);

  await page.getByRole("tab", { name: "감사 관점" }).click();
  let mutationEventType: string | null = null;
  await expect
    .poll(async () => {
      const audit = await apiGet<RuntimeRunQueryResult>(
        page,
        "/api/operations/runs?runType=audit&limit=100",
      );
      mutationEventType = audit.auditEvents.find(isMutationAudit)?.event_type ?? null;
      return mutationEventType;
    })
    .not.toBeNull();

  await expect(page.locator("body")).toContainText("감사 이벤트");
  expect(mutationEventType).not.toBeNull();
  await expect(page.locator("body")).toContainText(mutationEventType as string);
  await expect(page.locator("body")).toContainText("request id");
});
