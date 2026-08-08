import { expect, type Page, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

type OsdkApplication = {
  application?: OsdkApplication;
  id: string;
  app_api_name?: string;
  appApiName?: string;
  display_name?: string;
  displayName?: string;
  status: string;
  clients?: OsdkClient[];
};
type OsdkClient = {
  id: string;
  client_id?: string;
  clientId?: string;
  status: string;
  redirect_uris?: string[];
  redirectUris?: string[];
};

function uniqueName(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

function applicationRecord(app: OsdkApplication): OsdkApplication {
  return app.application ?? app;
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

test("Developer Console creates an OSDK application and OAuth client through backend APIs", async ({
  page,
}) => {
  const appApiName = uniqueName("e2eDeveloperApp");
  const displayName = `E2E Developer App ${appApiName}`;
  const clientId = uniqueName("e2e-client");
  const redirectUri = "http://127.0.0.1:4173/oauth/callback";

  await page.goto("/developer");
  await expect(page.locator("body")).toContainText("Developer Console");

  await page.getByRole("button", { name: /새 앱|새 애플리케이션/ }).first().click();
  await expect(page.getByRole("dialog")).toContainText("새 OSDK 애플리케이션");
  await page.getByLabel("애플리케이션 API name").fill(appApiName);
  await page.getByLabel("표시 이름").fill(displayName);

  const createAppResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/developer-console/osdk-applications") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "애플리케이션 생성" }).click();
  const createAppPayload = (await (await createAppResponse).json()) as {
    application: OsdkApplication;
  };
  const appId = createAppPayload.application.id;
  expect(createAppPayload.application.status).toBe("active");

  await expect(page.locator("body")).toContainText(displayName);
  await expect(page.locator("body")).toContainText(appApiName);
  const apps = await apiGet<OsdkApplication[]>(
    page,
    "/api/developer-console/osdk-applications",
  );
  expect(
    apps.some(
      (app) => {
        const record = applicationRecord(app);
        return (
          record.id === appId &&
          (record.app_api_name ?? record.appApiName) === appApiName
        );
      },
    ),
  ).toBe(true);

  await page.getByRole("button", { name: "클라이언트" }).click();
  await page.getByRole("button", { name: "새 클라이언트" }).click();
  await expect(page.getByRole("dialog")).toContainText("새 OAuth 클라이언트");
  await page.getByLabel("client id").fill(clientId);
  await page.getByLabel("redirect URIs").fill(redirectUri);

  const createClientResponse = page.waitForResponse(
    (response) =>
      response.url().includes(
        `/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/clients`,
      ) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "클라이언트 생성" }).click();
  const createdClient = (await (await createClientResponse).json()) as OsdkClient;
  expect(createdClient.status).toBe("active");
  expect(createdClient.client_id ?? createdClient.clientId).toBe(clientId);

  await expect(page.locator("body")).toContainText("클라이언트가 생성되었습니다");
  await expect(page.locator("body")).toContainText(clientId);
  await expect(page.locator("body")).toContainText("redirect URIs");
  await expect(page.locator("body")).toContainText("active");

  const clients = await apiGet<OsdkClient[]>(
    page,
    `/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/clients`,
  );
  expect(
    clients.some(
      (client) =>
        (client.client_id ?? client.clientId) === clientId &&
        (client.redirect_uris ?? client.redirectUris ?? []).includes(redirectUri),
    ),
  ).toBe(true);

  const appDetail = await apiGet<OsdkApplication>(
    page,
    `/api/developer-console/osdk-applications/${encodeURIComponent(appId)}`,
  );
  expect(applicationRecord(appDetail).id).toBe(appId);
  expect((appDetail.clients ?? []).some((client) => (client.client_id ?? client.clientId) === clientId)).toBe(true);

  const serviceClientId = uniqueName("e2e-service-client");
  await page.getByRole("button", { name: "새 클라이언트" }).click();
  await page.getByRole("button", { name: /서비스 · Client Secret/ }).click();
  await page.getByRole("textbox", { name: "client id", exact: true }).fill(serviceClientId);
  const rotateSecretResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/secrets/rotate") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "클라이언트 생성" }).click();
  const secretPayload = (await (await rotateSecretResponse).json()) as {
    clientSecret: string;
  };
  expect(secretPayload.clientSecret.length).toBeGreaterThan(32);
  await expect(page.locator("body")).toContainText("Client Secret");
  await expect(page.locator("body")).toContainText("다시 조회할 수 없습니다");

  await page.getByRole("button", { name: "Ontology MCP · Hub" }).click();
  await expect(page.locator("body")).toContainText("Ontology MCP 서버");
  await page.getByText("외부 에이전트에 공개", { exact: true }).click();
  await page.getByText("에이전트용 설명").locator("..").getByRole("textbox").fill("E2E governed Ontology MCP server");
  const configureMcpResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/developer-console/osdk-applications/${encodeURIComponent(appId)}/mcp-server`) &&
      response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "설정 저장" }).click();
  expect((await configureMcpResponse).ok()).toBe(true);
  await expect(page.locator("body")).toContainText("MCP Hub");
  await expect(page.locator("body")).toContainText("E2E governed Ontology MCP server");
});
