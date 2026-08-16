import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

function e2eSlug(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

async function startRestFixture(): Promise<{
  baseUrl: string;
  close: () => Promise<void>;
}> {
  const server = createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        items: [
          { id: "E2E-REST-1", name: "Verified order", amount: 125 },
          { id: "E2E-REST-2", name: "Second order", amount: 250 },
        ],
        nextCursor: null,
      }),
    );
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

test("Postgres wizard uses a database URL credential contract", async ({
  page,
}) => {
  const displayName = `Postgres credential E2E ${e2eSlug("postgres")}`;

  await page.goto("/data/connections");
  await page.getByRole("button", { name: "새 소스" }).first().click();
  await page.getByTestId("source-template-postgres_jdbc").click();
  await page.getByText("직접 연결", { exact: true }).click();
  await page.getByRole("button", { name: "계속" }).click();
  await page.getByPlaceholder("예: 주문 ERP 연결").fill(displayName);
  await page.getByRole("button", { name: "소스 생성 및 계속" }).click();

  await expect(page.getByText("Database URL", { exact: true })).toBeVisible();
  await expect(
    page.getByPlaceholder(
      "postgresql+psycopg://user:password@db.internal:5432/database",
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      "전체 database URL은 vault에 저장되고 이후 ***REDACTED*** 참조로만 노출됩니다.",
      { exact: true },
    ),
  ).toBeVisible();
});

test("connector catalog marks executable truth and SAP uses OData defaults", async ({
  page,
}) => {
  await page.route("**/api/sources/templates", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          sourceType: "sap_odata",
          displayName: "SAP OData",
          category: "protocol",
          description:
            "SAP OData 서비스의 엔터티를 탐색하고 Foundry로 동기화합니다.",
          isRecommended: false,
          executionStatus: "active",
          capabilities: ["batch", "exploration"],
          credentialModes: ["credential_ref", "oauth_future"],
          networkModes: ["direct", "agent_proxy"],
          supportsExploration: true,
          managedRunModes: ["manual", "scheduled"],
        },
        {
          sourceType: "sharepoint_graph",
          displayName: "SharePoint Graph",
          category: "protocol",
          description:
            "SharePoint Online의 파일과 미디어를 탐색해 안전하게 동기화합니다.",
          isRecommended: false,
          executionStatus: "definition_only",
          capabilities: ["batch_file", "media", "exploration"],
          credentialModes: ["credential_ref", "oauth_future"],
          networkModes: ["direct", "agent_proxy"],
          supportsExploration: true,
          managedRunModes: ["definition_only"],
        },
      ]),
    });
  });

  await page.goto("/data/connections");
  await page.getByRole("button", { name: "새 소스" }).first().click();

  const sapCard = page.getByTestId("source-template-sap_odata");
  const sharePointCard = page.getByTestId("source-template-sharepoint_graph");
  await expect(sapCard).toContainText("실행 가능");
  await expect(sharePointCard).toContainText("정의만");

  await sapCard.click();
  await page.getByText("직접 연결", { exact: true }).click();
  await page.getByRole("button", { name: "계속" }).click();
  await page
    .getByPlaceholder("예: 주문 ERP 연결")
    .fill(`SAP OData E2E ${e2eSlug("sap")}`);
  await page.getByRole("button", { name: "소스 생성 및 계속" }).click();

  await expect(page.getByText("Basic 인증", { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder("username:password")).toBeVisible();
  await page.getByPlaceholder("username:password").fill("e2e-user:e2e-pass");
  await page
    .getByPlaceholder("api.example.com, db.internal")
    .fill("sap.example.com");
  await page.getByRole("button", { name: "다음" }).click();

  await expect(page.getByText("SAP OData 서비스 연결", { exact: true })).toBeVisible();
  await expect(
    page.getByPlaceholder(
      "https://sap.example.com/sap/opu/odata/sap/ZORDERS_SRV",
    ),
  ).toBeVisible();
  await expect(page.getByPlaceholder("value")).toHaveValue("value");
  await expect(page.getByPlaceholder("@odata.nextLink")).toHaveValue(
    "@odata.nextLink",
  );
});

test("SAP Source detail preserves Basic auth and capability actions", async ({
  page,
}) => {
  const sourceName = e2eSlug("sap_source_detail_e2e");
  const connectorName = `${sourceName}_connector`;
  const tenantId = e2eSlug("tenant_sap_source_detail_e2e");
  const requestHeaders = { ...DEMO_HEADERS, "X-Tenant-ID": tenantId };

  await page.route("**/api/**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const targetUrl = new URL(
      `${requestUrl.pathname}${requestUrl.search}`,
      API_BASE_URL,
    );
    await route.continue({
      url: targetUrl.toString(),
      headers: {
        ...route.request().headers(),
        "x-tenant-id": tenantId,
      },
    });
  });
  const connectorResponse = await page.request.post(
    `${API_BASE_URL}/api/connectors/connections`,
    {
      headers: {
        ...requestHeaders,
        "Idempotency-Key": `connector-${connectorName}`,
      },
      data: {
        connectorName,
        displayName: "SAP Source Detail E2E connector",
        baseUrl: "https://sap.example.com/odata",
        auth: {
          mode: "basic",
          basicCredentialsSecretRef: "sap-e2e-basic",
        },
      },
    },
  );
  expect(connectorResponse.ok()).toBe(true);

  const resourceResponse = await page.request.put(
    `${API_BASE_URL}/api/connectors/connections/${connectorName}/resources/sales_orders`,
    {
      headers: {
        ...requestHeaders,
        "Idempotency-Key": `resource-${connectorName}`,
      },
      data: {
        datasetRef: `demo.${sourceName}`,
        resourcePath: "/SalesOrderSet?$format=json",
        pagination: {
          strategy: "next_link",
          itemsPath: "value",
          nextCursorPath: "@odata.nextLink",
          cursorKey: "nextLink",
          maxPagesPerSnapshot: 100,
        },
        schemaColumns: ["SalesOrder", "Status"],
        primaryKey: ["SalesOrder"],
      },
    },
  );
  expect(resourceResponse.ok()).toBe(true);

  const syncResponse = await page.request.post(
    `${API_BASE_URL}/api/sources/managed-syncs`,
    {
      headers: {
        ...requestHeaders,
        "Idempotency-Key": `sync-${sourceName}`,
      },
      data: {
        syncName: `${sourceName}_sync`,
        sourceName,
        displayName: `SAP Source Detail E2E ${sourceName}`,
        sourceType: "sap_odata",
        capability: "batch",
        targetDatasetRef: `demo.${sourceName}`,
        mode: "SNAPSHOT",
        schedule: { mode: "manual" },
        configSummary: {
          connectorName,
          resourceName: "sales_orders",
          connectionMode: "direct",
        },
      },
    },
  );
  expect(syncResponse.ok()).toBe(true);

  await page.goto("/data/connections");
  const sourceList = page.getByTestId("source-list");
  await sourceList.getByPlaceholder("소스 검색").fill(sourceName);
  await sourceList.getByRole("button").click();

  const capabilities = page.getByLabel("사용 가능한 기능");
  await expect(capabilities.getByText("Batch sync", { exact: true })).toBeVisible();
  await expect(
    capabilities.getByText("Source exploration", { exact: true }),
  ).toBeVisible();
  await expect(
    capabilities.getByText("Connection diagnostics", { exact: true }),
  ).toBeVisible();
  await expect(
    capabilities.getByText("사용 가능", { exact: true }),
  ).toHaveCount(3);

  await page.getByRole("button", { name: "연결 설정" }).click();
  await page.getByRole("button", { name: "자격 증명" }).click();
  await expect(page.getByText("Basic 인증", { exact: true })).toBeVisible();
  await expect(page.getByText("sap-e2e-basic", { exact: true })).toBeVisible();
});

test("managed REST connection appears as one product-level Source", async ({
  page,
}) => {
  const sourceName = e2eSlug("rest_source_e2e");
  const connectorName = `${sourceName}_connector`;
  const syncName = `${sourceName}_sync`;
  const policyName = `${sourceName}_policy`;
  const displayName = `REST Source E2E ${sourceName}`;
  const tenantId = e2eSlug("tenant_rest_source_e2e");
  const requestHeaders = { ...DEMO_HEADERS, "X-Tenant-ID": tenantId };
  const restFixture = await startRestFixture();

  try {
    await page.route("**/api/**", async (route) => {
      const requestUrl = new URL(route.request().url());
      const targetUrl = new URL(
        `${requestUrl.pathname}${requestUrl.search}`,
        API_BASE_URL,
      );
      await route.continue({
        url: targetUrl.toString(),
        headers: {
          ...route.request().headers(),
          "x-tenant-id": tenantId,
        },
      });
    });
    const connectorResponse = await page.request.post(
      `${API_BASE_URL}/api/connectors/connections`,
      {
        headers: {
          ...requestHeaders,
          "Idempotency-Key": `connector-${connectorName}`,
        },
        data: {
          connectorName,
          displayName: `${displayName} REST connector`,
          baseUrl: restFixture.baseUrl,
          auth: { mode: "none" },
          allowPrivateNetwork: true,
        },
      },
    );
    expect(connectorResponse.ok()).toBe(true);
    const createdConnection = await connectorResponse.json();

    const resourceResponse = await page.request.put(
      `${API_BASE_URL}/api/connectors/connections/${connectorName}/resources/orders`,
      {
        headers: {
          ...requestHeaders,
          "Idempotency-Key": `resource-${connectorName}`,
        },
        data: {
          datasetRef: `demo.${sourceName}`,
          resourcePath: "/orders",
          pagination: {
            strategy: "cursor",
            itemsPath: "items",
            nextCursorPath: "nextCursor",
            cursorQueryParam: "cursor",
            cursorKey: "cursor",
          },
          schemaColumns: ["id", "name", "amount"],
          primaryKey: ["id"],
        },
      },
    );
    expect(resourceResponse.ok()).toBe(true);

    const policyResponse = await page.request.post(
      `${API_BASE_URL}/api/sources/network-policies`,
      {
        headers: {
          ...requestHeaders,
          "Idempotency-Key": `policy-${policyName}`,
        },
        data: {
          policyName,
          displayName: `${displayName} egress policy`,
          mode: "direct",
          allowedHosts: [new URL(restFixture.baseUrl).hostname],
        },
      },
    );
    expect(policyResponse.ok()).toBe(true);

    const syncResponse = await page.request.post(
      `${API_BASE_URL}/api/sources/managed-syncs`,
      {
        headers: {
          ...requestHeaders,
          "Idempotency-Key": `sync-${syncName}`,
        },
        data: {
          syncName,
          sourceName,
          displayName,
          sourceType: "rest_api",
          capability: "batch",
          targetDatasetRef: `demo.${sourceName}`,
          mode: "SNAPSHOT",
          schedule: { mode: "manual" },
          configSummary: {
            connectorName,
            resourceName: "orders",
            connectionMode: "direct",
            networkPolicyName: policyName,
          },
        },
      },
    );
    expect(syncResponse.ok()).toBe(true);

    await page.goto("/data/connections");
    const sourceList = page.getByTestId("source-list");
    await sourceList.getByPlaceholder("소스 검색").fill(sourceName);
    await expect(sourceList.getByRole("button")).toHaveCount(1);
    await expect(
      sourceList.getByText(displayName, { exact: true }),
    ).toBeVisible();
    await expect(
      sourceList.getByText(`${displayName} REST connector`, { exact: true }),
    ).toHaveCount(0);

    await sourceList.getByRole("button").click();
    await page.getByRole("button", { name: "연결 설정" }).click();
    await expect(
      page.getByRole("heading", { name: "Source setup" }),
    ).toBeVisible();
    await expect(
      page.getByText(restFixture.baseUrl, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(connectorName, { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "미리보기" }).click();
    await expect(page.getByText("연결 미리보기", { exact: true })).toBeVisible();
    await expect(page.getByText("E2E-REST-1", { exact: true })).toBeVisible();
    await expect(page.getByText("2 rows · orders", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "소스 탐색" }).click();
    const explorer = page.getByTestId("rest-source-explorer");
    await expect(explorer).toBeVisible();
    await expect(explorer.getByText("1 resources", { exact: true })).toBeVisible();
    await explorer.getByTestId("rest-resource-orders").click();
    await explorer.getByRole("button", { name: "미리보기" }).click();
    await expect(
      explorer.getByText("미리보기 성공", { exact: true }),
    ).toBeVisible();
    const operationsEvidenceLink = explorer.getByRole("link", {
      name: /Operations에서 실행 증거 보기/,
    });
    await expect(operationsEvidenceLink).toBeVisible();
    await expect(operationsEvidenceLink).toHaveAttribute(
      "href",
      /\/operations\/runs\/source_exploration\/source_explore_/,
    );
    await expect(explorer.getByText("E2E-REST-1", { exact: true })).toBeVisible();
    await expect(
      explorer.getByText("읽기 전용 · Dataset commit 없음", { exact: true }),
    ).toBeVisible();
    await explorer.getByRole("tab", { name: /스키마/ }).click();
    await expect(
      explorer.getByRole("cell", { name: "amount", exact: true }),
    ).toBeVisible();
    await expect(
      explorer.getByText("primary key", { exact: true }),
    ).toBeVisible();
    await explorer
      .getByRole("button", { name: "이 리소스로 동기화 만들기" })
      .click();
    await expect(page.getByText("새 동기화", { exact: true })).toBeVisible();
    await expect(page.getByTestId("new-sync-display-name")).toHaveValue(
      "orders",
    );
    await expect(page.getByTestId("new-sync-rest-resource")).toContainText(
      "orders",
    );
    await expect(page.getByTestId("new-sync-dataset-ref")).toHaveValue(
      `demo.${sourceName}`,
    );
    await page.getByRole("button", { name: "동기화 편집기 닫기" }).click();
    await page.getByRole("button", { name: "연결 설정" }).click();

    const updatedBaseUrl = `${restFixture.baseUrl}/v2`;
    await page.getByRole("button", { name: "편집" }).click();
    await page.getByTestId("source-connection-base-url").fill("ftp://invalid");
    await expect(
      page.getByText("유효한 HTTP(S) 주소가 필요합니다.", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "변경사항 저장" }),
    ).toBeDisabled();
    await page.getByTestId("source-connection-base-url").fill(updatedBaseUrl);
    await page.getByRole("button", { name: "변경사항 저장" }).click();
    await expect(page.getByText(updatedBaseUrl, { exact: true })).toBeVisible();

    const updatedResponse = await page.request.get(
      `${API_BASE_URL}/api/connectors/connections/${connectorName}`,
      { headers: requestHeaders },
    );
    expect(updatedResponse.ok()).toBe(true);
    const updatedConnection = await updatedResponse.json();
    expect(updatedConnection.baseUrl).toBe(updatedBaseUrl);
    expect(updatedConnection.configFingerprint).not.toBe(
      createdConnection.configFingerprint,
    );
    await page.getByRole("button", { name: "자격 증명" }).click();
    await expect(
      page.getByText(updatedConnection.configFingerprint, { exact: true }),
    ).toBeVisible();

    const credentialName = `${sourceName}_credential`;
    await page.getByRole("button", { name: "인증정보 변경" }).click();
    await page.getByRole("button", { name: "새 인증정보" }).click();
    await page.getByTestId("source-new-credential-name").fill(credentialName);
    await page
      .getByTestId("source-new-credential-secret")
      .fill("e2e-secret-never-rendered");
    await page.getByRole("button", { name: "인증정보 만들기" }).click();
    await expect(
      page.getByText(
        "인증정보를 만들었습니다. 적용을 눌러 Source에 연결하세요",
        { exact: true },
      ),
    ).toBeVisible();
    await page.getByRole("button", { name: "인증정보 적용" }).click();
    await expect(page.getByText("Bearer token", { exact: true })).toBeVisible();
    await expect(page.getByText("e2e-secret-never-rendered")).toHaveCount(0);

    const securedResponse = await page.request.get(
      `${API_BASE_URL}/api/connectors/connections/${connectorName}`,
      { headers: requestHeaders },
    );
    expect(securedResponse.ok()).toBe(true);
    const securedConnection = await securedResponse.json();
    expect(securedConnection.auth).toEqual({
      mode: "bearer",
      tokenSecretRef: `source_${credentialName}`,
    });

    await page.getByRole("button", { name: "네트워크 egress" }).click();
    await expect(
      page.getByText(`${displayName} egress policy`, { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "연결 진단 실행" }).click();
    await expect(
      page.getByText("2개 row를 읽었고 Dataset commit은 만들지 않았습니다.", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText("연결 준비됨", { exact: true })).toBeVisible();
    await expect(page.getByText("5/5 checks", { exact: true })).toBeVisible();
    await expect(
      page
        .getByRole("button")
        .filter({ hasText: "5/5 checks" })
        .getByText(/^request .+/),
    ).toBeVisible();

    const diagnosticHistory = await page.request.get(
      `${API_BASE_URL}/api/sources/${sourceName}/connection-tests?limit=5`,
      { headers: requestHeaders },
    );
    expect(diagnosticHistory.ok()).toBe(true);
    const diagnosticRows = await diagnosticHistory.json();
    expect(diagnosticRows).toHaveLength(1);
    expect(diagnosticRows[0]).toMatchObject({
      sourceName,
      status: "succeeded",
    });
    expect(diagnosticRows[0].checks.probe).toMatchObject({
      rowCount: 2,
      datasetCommitCreated: false,
    });

    await page.getByRole("button", { name: "연결 상세" }).click();
    await page.getByRole("button", { name: "네트워크 egress" }).click();
    await expect(page.getByText("5/5 checks", { exact: true })).toBeVisible();

    await page
      .getByRole("button", { name: "동기화", exact: true })
      .nth(1)
      .click();
    const operationalSummary = page.getByTestId("sync-operational-summary");
    await expect(operationalSummary).toBeVisible();
    await expect(operationalSummary).toContainText("수동 실행");

    const schedulePanel = page.getByTestId("sync-schedule-panel");
    await schedulePanel.getByRole("button", { name: "편집" }).click();
    await page.getByRole("combobox", { name: "실행 방식" }).click();
    await page.getByRole("option", { name: "일정 간격" }).click();
    await page.getByTestId("sync-schedule-interval").fill("3600");
    await page.getByTestId("sync-schedule-batch-limit").fill("2");
    await page.getByRole("button", { name: "일정 저장" }).click();
    await expect(schedulePanel).toContainText("3600초 간격");
    await expect(operationalSummary).toContainText("다음 실행");

    const scheduleState = page.getByTestId("sync-schedule-state");
    await scheduleState.getByRole("button", { name: "일시정지" }).click();
    await expect(scheduleState).toContainText("스케줄 일시정지됨");
    await expect(operationalSummary).toContainText("일시정지 · 3600초 간격");
    const pausedPreview = await page.request.get(
      `${API_BASE_URL}/api/sources/scheduler/due?maxRuns=500`,
      { headers: requestHeaders },
    );
    expect(pausedPreview.ok()).toBe(true);
    const pausedDecision = (await pausedPreview.json()).decisions.find(
      (decision: { syncName: string }) => decision.syncName === syncName,
    );
    expect(pausedDecision).toMatchObject({
      enabled: false,
      due: false,
      reason: "schedule_paused",
    });

    await scheduleState.getByRole("button", { name: "재개" }).click();
    await expect(scheduleState).toContainText("자동 실행 활성");
    await expect(operationalSummary).toContainText("다음 실행");

    await page.getByRole("button", { name: "빌드", exact: true }).click();
    await expect(page.getByText("성공", { exact: true }).first()).toBeVisible();
    const runRow = page.getByRole("row").filter({ hasText: "수동" }).first();
    await expect(runRow).toContainText("2");
    await runRow.click();
    await expect(page.getByTestId("sync-run-evidence")).toContainText(
      "데이터셋 버전",
    );
    const networkEvidence = page.getByTestId("sync-network-evidence");
    await expect(networkEvidence).toContainText("실제 빌드 네트워크 경로");
    await expect(networkEvidence).toContainText("Direct egress");
    await expect(networkEvidence).toContainText("전송 성공");
    await expect(networkEvidence).toContainText("TCP :");

    const syncTabs = page.getByRole("button", {
      name: "동기화",
      exact: true,
    });
    await expect(syncTabs).toHaveCount(2);
    await syncTabs.nth(0).click();
    const globalSyncRow = page.getByRole("row").filter({ hasText: syncName });
    await expect(globalSyncRow).toHaveCount(1);
    await globalSyncRow.click();
    const globalRunRow = page.getByRole("row").filter({ hasText: "manual" });
    await expect(globalRunRow).toHaveCount(1);
    await globalRunRow.click();
    const globalNetworkEvidence = page.getByTestId("sync-network-evidence");
    await expect(globalNetworkEvidence).toContainText(
      "실제 빌드 네트워크 경로",
    );
    await expect(globalNetworkEvidence).toContainText("Direct egress");

    const updatedSyncResponse = await page.request.get(
      `${API_BASE_URL}/api/sources/managed-syncs/${syncName}`,
      { headers: requestHeaders },
    );
    expect(updatedSyncResponse.ok()).toBe(true);
    const updatedSync = await updatedSyncResponse.json();
    expect(updatedSync.status).toBe("active");
    expect(updatedSync.schedule).toMatchObject({
      mode: "interval",
      everySeconds: 3600,
      batchLimit: 2,
    });
    expect(updatedSync.schedule.startAt).toMatch(/Z$/);
  } finally {
    await restFixture.close();
  }
});

test("managed REST wizard keeps one seven-step progress rail", async ({
  page,
}) => {
  const displayName = `REST Wizard E2E ${e2eSlug("source")}`;

  await page.goto("/data/connections");
  await page.getByRole("button", { name: "새 소스" }).first().click();
  await page.getByTestId("source-template-rest_api").click();
  await page.getByRole("button", { name: "계속" }).click();
  await page.getByPlaceholder("예: 주문 ERP 연결").fill(displayName);
  await page.getByRole("button", { name: "소스 생성 및 계속" }).click();

  const progress = page.getByRole("navigation");
  await expect(progress.getByRole("listitem")).toHaveCount(7);
  for (const label of [
    "소스 유형 선택",
    "연결 방식",
    "프로젝트에 저장",
    "자격 증명 & 네트워크",
    "동기화 설정",
    "실행 & 증거",
    "완료",
  ]) {
    await expect(progress.getByText(label, { exact: true })).toBeVisible();
  }
});
