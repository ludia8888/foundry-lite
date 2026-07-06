# Foundry-lite SDK Frontend Cookbook

**Status:** Example cookbook for current S61-S64 SDK/frontend foundation
**Audience:** Frontend developers building Foundry-lite product screens

이 문서는 프론트 개발자가 SDK만 보고 첫 화면들을 만들기 시작할 수 있도록 정리한
사용 예제 모음이다. 비개발자식으로 말하면, `docs/frontend-backend-surface-contract.md`가
"프론트와 백엔드 사이의 공식 메뉴판"이라면, 이 문서는 그 메뉴로 실제 화면을 조립하는
"요리 순서"다.

이 문서는 current implementation claim의 원본이 아니다. 실제 현재/미래 경계는
`docs/implementation-status.md`, 증거 명령은 `docs/sprint-evidence-ledger.md`, SDK/API 계약은
`docs/frontend-backend-surface-contract.md`와 `docs/frontend-api-sdk-surface-matrix.json`을 따른다.

## Ground Rules

- 화면 코드는 `@foundry-lite/sdk`, `@foundry-lite/sdk/react`, `@foundry-lite/sdk/screen-recipes`를 우선 사용한다.
- 제품 화면에서 raw request path를 직접 조립하지 않는다.
- mutation은 사용자 의도마다 하나의 `idempotencyKey`를 만들고 retry 동안 재사용한다.
- 오류 UI에는 최소한 request id, error code, retryable 여부를 보여줄 수 있게 상태를 보관한다.
- 장시간 작업은 무한 polling이 아니라 bounded polling 또는 SDK stream helper로 다룬다.
- migration, worker daemon, infra bootstrap처럼 위험한 내부 운영 작업은 browser-safe action과
  operator command/future backend surface를 화면에서 구분한다.

## One Client, One Recipe Set

대부분의 화면은 먼저 session-aware client를 만들고, 그 client로 screen recipe 묶음을 만든다.

```ts
import { createFoundryLiteClient, createSessionTokenProvider, idempotencyKey } from "@foundry-lite/sdk";
import {
  createOperatorWorkspaceRecipe,
  createResourceBrowserRecipe,
  loadFoundryLiteScreenRecipes,
  operatorWorkspaceNavigation,
  operatorWorkspaceShell,
} from "@foundry-lite/sdk/screen-recipes";

const client = createFoundryLiteClient({
  baseUrl: window.location.origin,
  tokenProvider: createSessionTokenProvider(() => auth.currentSession()),
  context: {
    tenantId: activeTenantId,
    userId: currentUser.id,
    roles: currentUser.roles,
  },
  onResponse: (metadata) => requestStore.setLastRequest(metadata),
});

const recipes = await loadFoundryLiteScreenRecipes(client);
const operatorWorkspace = createOperatorWorkspaceRecipe(client);
const resourceBrowser = createResourceBrowserRecipe(client);
const home = await operatorWorkspace.loadHome({ runFilters: { limit: 25 } });
const resources = await resourceBrowser.loadResources({ includeTrashed: false });
const navigationItems = operatorWorkspaceNavigation(home).navItems;
const quickActions = operatorWorkspaceNavigation(home).quickActions;
const shell = await operatorWorkspace.loadShell({
  runFilters: { limit: 25 },
  selectedAreaId: "operations",
});
const shellFromHome = operatorWorkspaceShell(home, "operations");

renderWorkspaceHome({
  datasets: home.datasets,
  failedRunCount: home.failedRunCount,
  navigationItems,
  quickActions,
  resourceItems: resources.activeItems,
  favoriteResourceItems: resources.favoriteItems,
  selectedSurface: shell.selectedSurface,
  areaSurfaces: shell.areaSurfaces,
  primaryQuickAction: shellFromHome.primaryQuickAction,
  adminSections: home.adminLaunchpad.visibleSections,
  recoveryActions: home.recoveryOverview?.requiredOperatorActions ?? [],
});
```

React 앱에서는 `FoundryLiteProvider`를 앱 root에 한 번 붙이고, 화면 안에서는
`useFoundryLiteClient(...)`와 `useFoundryLiteProvidedScreenRecipes(...)`로 같은 session-aware client와 live
ontology recipe bundle을 꺼낸다. 개별 화면이 token refresh, request id, tenant header를 매번 직접 조립하지
않게 하는 기본 패턴이다. 테스트 harness나 이미 만들어진 client를 직접 주입하는 화면은 lower-level
`useFoundryLiteScreenRecipes(client)`를 그대로 쓸 수 있다.

```tsx
import {
  FoundryLiteProvider,
  useFoundryLiteClient,
  useFoundryLiteProvidedOperatorAppShell,
  useFoundryLiteProvidedOperatorWorkspaceHome,
  useFoundryLiteProvidedOperatorWorkspaceShell,
  useFoundryLiteProvidedScreenRecipes,
  useFoundryLiteScreenStatus,
  useFoundryLiteSession,
  useFoundryLiteSessionStatus,
} from "@foundry-lite/sdk/react";

export function AppShell() {
  return (
    <FoundryLiteProvider
      baseUrl={window.location.origin}
      sessionProvider={() => auth.currentSession()}
      context={{ tenantId: activeTenantId, userId: currentUser.id, roles: currentUser.roles }}
    >
      <DatasetExplorerScreen />
    </FoundryLiteProvider>
  );
}

function DatasetExplorerScreen() {
  const client = useFoundryLiteClient();
  const session = useFoundryLiteSession();
  const sessionStatus = useFoundryLiteSessionStatus();
  const screen = useFoundryLiteProvidedScreenRecipes();
  const appShell = useFoundryLiteProvidedOperatorAppShell({
    runFilters: { limit: 25 },
    selectedAreaId: "data",
  });
  const screenStatus = useFoundryLiteScreenStatus({
    error: screen.error ?? appShell.bootError,
    hasData: screen.hasCatalog && appShell.hasCatalog,
    isLoading: screen.isLoading || appShell.isBooting,
    isRefreshing: screen.isRefreshing,
    requestId: screen.requestId ?? appShell.bootRequestId,
    retryable: screen.retryable || appShell.retryable,
  });
  const operatorHome = useFoundryLiteProvidedOperatorWorkspaceHome({ runFilters: { limit: 25 } });
  const workspaceShell = useFoundryLiteProvidedOperatorWorkspaceShell({
    runFilters: { limit: 25 },
    selectedAreaId: "data",
  });

  if (screenStatus.shouldShowSkeleton || operatorHome.isLoading || workspaceShell.isLoading) {
    return renderSkeleton();
  }
  if (screenStatus.needsAuthentication) {
    return renderSignInPrompt(screenStatus.requestId, screenStatus.title);
  }
  if (screenStatus.isPermissionDenied) {
    return renderPermissionDenied(screenStatus.requestId, screenStatus.description);
  }
  if (!screenStatus.shouldRenderContent && screenStatus.error) {
    return renderRequestError(screenStatus.requestId, screenStatus.errorCode, screenStatus.canRetry);
  }
  if (screen.error) return renderRequestError(session.lastRequestId, screen.error.code, screen.retryable);
  if (operatorHome.error) {
    return renderRequestError(operatorHome.requestId, operatorHome.error.code, operatorHome.retryable);
  }

  async function refreshDatasets() {
    return screen.recipes.datasetExplorer.listDatasets();
  }

  return renderDatasetExplorer({
    client,
    refreshDatasets,
    recipes: appShell.recipes,
    reloadWorkspace: appShell.reloadWorkspace,
    canRenderWorkspace: screenStatus.shouldRenderContent,
    screenStatus,
    workspaceHome: operatorHome,
    fallbackNavigationItems: operatorHome.navigation.navItems,
    fallbackQuickActions: operatorHome.navigation.quickActions,
    navigationItems: appShell.navigation.navItems,
    quickActions: appShell.navigation.quickActions,
    selectedSurface: appShell.selectedSurface,
    areaSurfaces: appShell.areaSurfaces,
    sessionStatus: appShell.sessionStatus,
  });
}
```

`useFoundryLiteSessionStatus(...)` turns the provider's latest response metadata into screen-ready auth/error state:
`needsAuthentication`, `isPermissionDenied`, `canRetryLastRequest`, `lastRequestId`, `lastErrorCode`, `tone`, `title`,
and `description`. It is intentionally not a full login UI. It gives screens a consistent way to show sign-in,
permission, and retry banners while the actual auth product remains app-owned.

`useFoundryLiteScreenStatus(...)` is the generic screen-state layer on top of query/mutation errors and session status.
Pass the screen's `error`, `isLoading`, `isRefreshing`, `requestId`, `retryable`, and `hasData`; it returns
`shouldShowSkeleton`, `shouldShowInlineRefresh`, `shouldRenderContent`, `needsAuthentication`, `isPermissionDenied`,
`canRetry`, `requestId`, and `errorCode`. It is not a visual alert component; it is the shared decision model that lets
Dataset, Ontology, AIP, Operations, and Admin screens render consistent loading/auth/error states.

`useFoundryLiteProvidedOperatorAppShell(...)` is the fastest React app-shell path. It reuses the operator workspace
home catalog to create `recipes`, then returns `sessionStatus`, `canRenderWorkspace`, `shouldShowSignIn`,
`shouldShowPermissionDenied`, `bootError`, `bootRequestId`, `reloadWorkspace`, `navigation`, `selectedSurface`, and
`areaSurfaces` from one hook. That keeps a product shell from issuing a second ontology-catalog request just to build
recipe helpers, and it gives auth/permission/error gates one common state object.

`operatorWorkspaceShell(...)`, `operatorWorkspace.loadShell(...)`, and
`useFoundryLiteProvidedOperatorWorkspaceShell(...)` are the app-shell layer above the home summary. They expose
`areaSurfaces`, `selectedSurface`, `selectedRoute`, `selectedTitle`, `primaryQuickAction`, and `attentionCount`, plus
the recommended recipe entry and React hook for each workspace area. A frontend app can build the left navigation and
top-level route switch without hard-coding which SDK helper belongs to Data, Ontology, Operations, AIP, Admin, or
Recovery.

`createOperatorWorkspaceRecipe(client).loadHome(...)` and
`useFoundryLiteProvidedOperatorWorkspaceHome(...)` are the fastest "first screen" path. They load the current ontology
catalog, dataset list, recent Operations runs, admin launchpad, and recovery overview through named SDK methods, then
return screen-ready counts such as `datasetCount`, `objectTypeCount`, `failedRunCount`,
`recommendedAdminSectionId`, and `hasRecoveryActions`. The standalone `operatorWorkspaceNavigation(home)` helper and
the React hook's `home.navigation` field derive `navItems`, `attentionItems`, and `quickActions` from the same loaded
home model, so the app shell can show "where should I go next?" without inventing another backend contract. This keeps
the app shell from rebuilding a dashboard data loader out of raw REST paths.

## Project And Resource Browser

Palantir Compass에 가까운 화면은 `client.resources`와 `createResourceBrowserRecipe(client)`로 시작한다.
비개발자식으로 말하면, 프로젝트는 단순 폴더가 아니라 "누가 볼 수 있는가"를 정하는 경계이고, 폴더는 그 안의 정리 구조다.
데이터셋, 소스, 온톨로지 작업물은 stable RID가 있는 resource item으로 보인다.
파이프라인 작업물도 같은 resource hook을 쓸 수 있지만, 현재 `main`의 파이프라인 전용 DB surface는 별도 후속 범위다.

```ts
const browser = createResourceBrowserRecipe(client);
const view = await browser.loadResources({
  projectId: selectedProjectId,
  folderId: selectedFolderId,
  includeTrashed: false,
});

renderResourceBrowser({
  projects: view.projects,
  folders: view.folders,
  items: view.activeItems,
  favorites: view.favoriteItems,
  selectedProjectId: view.selectedProjectId,
  selectedFolderId: view.selectedFolderId,
});
```

프로젝트/폴더/리소스 변경은 named SDK method로 호출한다. mutation은 모두 사용자 의도마다 하나의
`idempotencyKey`를 만든다.

```ts
const project = await client.resources.projects.create(
  { displayName: "Supply Chain Ops" },
  { idempotencyKey: idempotencyKey("project", "supply-chain-ops") },
);

const folder = await client.resources.folders.create(
  project.id,
  { displayName: "Raw sources" },
  { idempotencyKey: idempotencyKey("folder", project.id, "raw-sources") },
);

await client.resources.items.move(
  "ri.foundry-lite.dataset.orders",
  { projectId: project.id, folderId: folder.id },
  { idempotencyKey: idempotencyKey("resource-move", "orders", folder.id) },
);
```

`favoriteIds`와 `trashedIds` 입력은 이제 backend state가 없던 테스트/오프라인 fallback 용도다. 실제 화면은
`client.resources.favorites.*`, `client.resources.items.trash/restore(...)`, `client.resources.trash.list(...)`에서
오는 서버 상태를 우선 사용한다. v1 권한은 프로젝트 grant 중심이다. 폴더/리소스 직접 grant override는 future다.

## First Source Connection

새 데이터가 처음 들어오는 화면은 `client.sources`로 시작한다. 비개발자식으로 말하면, Source는 "데이터가 들어오는 문"이고
Sync는 "그 문을 통해 반복해서 가져오는 일", Upload는 "브라우저에서 고른 파일을 바로 넣는 일", Listener는 "외부 시스템이
우리에게 이벤트를 밀어 넣는 입구"다. 기존 `client.connectors`와 `client.media`는 그대로 있지만, 고객 시스템을 처음
연결하고 탐색한 뒤 관리형 sync까지 보여주는 화면은 `createSourceWizardRecipe(...)`와
`useFoundryLiteProvidedSourceWizard(...)`를 먼저 사용한다. CSV/media/CDC처럼 더 짧은 루프는 기존
`createSourceOnboardingRecipe(...)`와 `useFoundryLiteProvidedSourceOnboarding(...)`을 그대로 쓴다.

### Source Wizard for ERP-style demos

고객사 회의실에서 "처음 ERP 붙이기"를 보여주는 화면은 `createSourceWizardRecipe(...)`를 쓴다. 이 recipe는 Palantir
Data Connection처럼 template 선택, credential vault 저장, agent/network 준비, source exploration, managed sync 생성,
첫 run 시작을 한 화면 상태로 묶는다. v1은 local vault에 secret을 저장하고 응답에는 redacted `secretRef`만 돌려준다.
managed sync schedule의 due preview/tick은 API/SDK/worker로 가능하지만, cloud/Vault secret manager, OAuth authorization
flow, SAP/NetSuite 전용 wizard packaging, visual scheduler UI는 아직 future다.

```tsx
import { idempotencyKey } from "@foundry-lite/sdk";
import { createSourceWizardRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useFoundryLiteProvidedSourceWizard } from "@foundry-lite/sdk/react";

const sourceWizard = createSourceWizardRecipe(client);

await sourceWizard.run(
  {
    sourceType: "postgres_jdbc",
    credential: {
      payload: {
        credentialName: "erp_db",
        displayName: "ERP DB",
        kind: "postgres_jdbc",
        authScheme: "database_url",
        secretValue: form.databaseUrl,
      },
      idempotencyKey: idempotencyKey("source-credential", "erp_db"),
    },
    agent: {
      payload: {
        agentId: "customer_agent",
        displayName: "Customer Agent",
        mode: "agent_proxy",
        capabilities: { jdbc: true },
        networkSummary: { region: "customer-dmz" },
      },
      idempotencyKey: idempotencyKey("source-agent", "customer_agent"),
    },
    networkPolicy: {
      payload: {
        policyName: "customer_erp_network",
        displayName: "Customer ERP Network",
        mode: "agent_proxy",
        agentId: "customer_agent",
        allowedHosts: ["erp.customer.local"],
      },
      idempotencyKey: idempotencyKey("source-network", "customer_erp_network"),
    },
    exploration: {
      sourceName: "erp_source",
      sourceType: "postgres_jdbc",
      request: { databaseUrlSecretRef: "erp_db", tableName: "orders", checkpointColumn: "id" },
    },
    sync: {
      payload: {
        syncName: "orders_incremental",
        sourceName: "erp_source",
        displayName: "Orders incremental",
        sourceType: "postgres_jdbc",
        capability: "batch",
        mode: "APPEND",
        targetDatasetRef: "raw.orders",
        schedule: { mode: "manual" },
        configSummary: { databaseUrlSecretRef: "erp_db", tableName: "orders", checkpointColumn: "id" },
      },
      idempotencyKey: idempotencyKey("source-sync", "orders_incremental"),
    },
    startRun: {
      payload: { triggerType: "manual", batchLimit: 5000 },
      idempotencyKey: idempotencyKey("source-sync-run", "orders_incremental"),
    },
  },
  { onState: (state) => renderWizardProgress(state.phase, state.operationsPath) },
);

function SourceWizardScreen() {
  const wizard = useFoundryLiteProvidedSourceWizard({
    onSuccess: (state) => navigateToDatasetOrOperations(state.datasetRef, state.operationsPath),
  });

  return renderSourceWizard({
    phase: wizard.phase,
    selectedTemplate: wizard.selectedTemplate,
    credential: wizard.credential,
    agent: wizard.agent,
    networkPolicy: wizard.networkPolicy,
    exploration: wizard.exploration,
    sync: wizard.sync,
    syncRun: wizard.syncRun,
    datasetRef: wizard.datasetRef,
    operationsPath: wizard.operationsPath,
    requestId: wizard.requestId,
    error: wizard.error,
    retryable: wizard.retryable,
    run: wizard.run,
    reset: wizard.reset,
  });
}
```

Wizard state의 핵심은 `phase`, `selectedTemplate`, `credential`, `agent`, `networkPolicy`, `exploration`, `sync`,
`syncRun`, `datasetRef`, `mediaSetId`, `operationsPath`, `requestId`, `error`, `retryable`이다. Exploration은
schema/sample evidence만 만들고 dataset commit은 만들지 않는다. Managed sync run은 `Idempotency-Key`로 재시도해도 같은
run을 돌려주고, checkpoint와 dataset version 또는 workflow pointer를 남긴다.

### Upload, webhook, CDC, media source loop

```tsx
import { idempotencyKey } from "@foundry-lite/sdk";
import { createSourceOnboardingRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useFoundryLiteProvidedSourceOnboarding } from "@foundry-lite/sdk/react";

const sourceOnboarding = createSourceOnboardingRecipe(client);

await sourceOnboarding.run(
  {
    kind: "csv_upload",
    payload: {
      sourceName: "orders_csv",
      displayName: "Orders CSV",
      datasetRef: "raw.orders",
      file: form.ordersCsvFile,
    },
    idempotencyKey: idempotencyKey("source-csv-upload", "orders_csv"),
  },
  {
    onState: (state) => renderSourceProgress(state.phase, state.operationsPath),
  },
);

function FirstSourceScreen() {
  const onboarding = useFoundryLiteProvidedSourceOnboarding({
    onSuccess: (state) => navigateToDatasetOrMedia(state.datasetRef, state.mediaSetId),
  });

  async function uploadOrdersCsv() {
    await onboarding.run({
      kind: "csv_upload",
      payload: {
        sourceName: "orders_csv",
        displayName: "Orders CSV",
        datasetRef: "raw.orders",
        file: form.ordersCsvFile,
      },
      idempotencyKey: form.idempotencyKeys.uploadOrdersCsv,
    });
  }

  async function createDebeziumSource() {
    await onboarding.run({
      kind: "debezium_cdc",
      payload: {
        sourceName: "orders_cdc",
        displayName: "Orders CDC",
        datasetRef: "raw.orders_cdc",
        streamName: "erp-orders",
        topic: "erp.public.orders",
        consumerGroup: "foundry-lite-orders",
        secretRefs: { connectorConfig: "debezium-orders-config" },
      },
      createIdempotencyKey: form.idempotencyKeys.createCdc,
      startSync: { limit: 100 },
      startSyncIdempotencyKey: form.idempotencyKeys.startCdc,
    });
  }

  async function uploadInvoiceMedia() {
    await onboarding.run({
      kind: "media_upload",
      payload: {
        sourceName: "invoice_media",
        displayName: "Invoice PDF",
        mediaSetId: "invoices",
        logicalPath: "uploads/invoice-123.pdf",
        contentType: "application/pdf",
        file: form.invoicePdfFile,
      },
      idempotencyKey: form.idempotencyKeys.uploadInvoiceMedia,
    });
  }

  return renderFirstSourceConnection({
    phase: onboarding.phase,
    source: onboarding.source,
    datasetRef: onboarding.datasetRef,
    mediaSetId: onboarding.mediaSetId,
    workflowRun: onboarding.workflowRun,
    commitResult: onboarding.commitResult,
    commitResults: onboarding.commitResults,
    mediaCommitResult: onboarding.mediaCommitResult,
    testResult: onboarding.testResult,
    operationsPath: onboarding.operationsPath,
    requestId: onboarding.requestId,
    error: onboarding.error,
    retryable: onboarding.retryable,
    isRunning: onboarding.isRunning,
    uploadOrdersCsv,
    createDebeziumSource,
    uploadInvoiceMedia,
    reset: onboarding.reset,
  });
}
```

REST ERP도 첫 화면에서는 Source 언어로 감쌀 수 있다. 아래 호출은 내부적으로 기존 Generic REST connector onboarding
surface를 사용하지만, 화면 코드는 "connector registry detail" 대신 "REST source sync"로 읽힌다.

```ts
await client.sources.rest.createConnection(
  {
    connectorName: "erp",
    displayName: "ERP REST",
    baseUrl: "https://erp.example.com/api",
    auth: { mode: "bearer", tokenSecretRef: "erp-api-token" },
    rateLimitPerMinute: 120,
    allowPrivateNetwork: false,
  },
  { idempotencyKey: idempotencyKey("source-rest-create", "erp") },
);

await client.sources.rest.upsertResource(
  "erp",
  "orders",
  {
    datasetRef: "raw.orders",
    resourcePath: "/orders",
    schemaColumns: ["order_id", "customer_id", "status", "total"],
    primaryKey: ["order_id"],
  },
  { idempotencyKey: idempotencyKey("source-rest-resource", "erp-orders") },
);

const testResult = await client.sources.rest.test("erp", "orders");
const workflowRun = await client.sources.rest.startSync(
  "erp",
  "orders",
  { syncName: "first-orders-sync" },
  { idempotencyKey: idempotencyKey("source-rest-sync", "erp-orders") },
);
```

Source recipe state는 한 화면에서 바로 렌더링할 수 있게 `phase`, `source`, `datasetRef`, `mediaSetId`,
`workflowRun`, `commitResult`, `commitResults`, `mediaCommitResult`, `testResult`, `operationsPath`, `requestId`,
`error`, `retryable`을 유지한다. Source managed sync schedule은 API/SDK/worker에서 due preview/tick으로 실행할 수
있고, v1의 의도적인 future scope는 remote directory crawler, visual scheduler UI, managed Debezium Connect
operations, cloud secret manager, OAuth authorization flow, vendor-specific SAP/NetSuite packaged source wizards다.

## ERP REST Connector Onboarding

ERP onboarding v1은 SAP/NetSuite 전용 마법사가 아니라 Generic REST JSON connector를 처음 연결하는 화면이다. 화면은
raw token 값을 받거나 저장하지 않는다. 사용자는 이미 SecretProvider에 등록된 secretRef 이름만 입력하고, SDK는
`tokenSecretRef` 또는 `headerValueSecretRef`만 API로 보낸다.

```tsx
import { idempotencyKey } from "@foundry-lite/sdk";
import { createConnectorOnboardingRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useFoundryLiteProvidedConnectorOnboarding } from "@foundry-lite/sdk/react";

const connectorOnboarding = createConnectorOnboardingRecipe(client);

await connectorOnboarding.runFirstSync(
  {
    connection: {
      connectorName: "erp",
      displayName: "ERP REST",
      baseUrl: "https://erp.example.com/api",
      auth: { mode: "bearer", tokenSecretRef: "erp-api-token" },
      rateLimitPerMinute: 120,
      allowPrivateNetwork: false,
    },
    resourceName: "orders",
    resource: {
      datasetRef: "raw.orders",
      resourcePath: "/orders",
      pagination: {
        strategy: "cursor",
        itemsPath: "data",
        nextCursorPath: "nextCursor",
        cursorQueryParam: "cursor",
      },
      schemaColumns: ["order_id", "customer_id", "status", "total"],
      primaryKey: ["order_id"],
    },
    sync: { syncName: "first-orders-sync" },
    idempotencyKeys: {
      createConnection: idempotencyKey("connector-create", "erp"),
      upsertResource: idempotencyKey("connector-resource", "erp-orders"),
      startFirstSync: idempotencyKey("connector-sync", "erp-orders"),
    },
  },
  {
    intervalMs: 1000,
    maxAttempts: 120,
    onState: (state) => renderOnboardingState(state),
  },
);

function ConnectorOnboardingScreen() {
  const onboarding = useFoundryLiteProvidedConnectorOnboarding({
    intervalMs: 1000,
    maxAttempts: 120,
  });

  async function connectOrders() {
    await onboarding.run({
      connection: {
        connectorName: "erp",
        displayName: "ERP REST",
        baseUrl: form.baseUrl,
        auth: { mode: "bearer", tokenSecretRef: form.tokenSecretRef },
        rateLimitPerMinute: form.rateLimitPerMinute,
        allowPrivateNetwork: false,
      },
      resourceName: "orders",
      resource: {
        datasetRef: "raw.orders",
        resourcePath: form.ordersPath,
        schemaColumns: form.schemaColumns,
        primaryKey: ["order_id"],
      },
      idempotencyKeys: form.idempotencyKeys,
    });
  }

  return renderConnectorOnboarding({
    phase: onboarding.phase,
    requestId: onboarding.requestId,
    testResult: onboarding.testResult,
    workflowRun: onboarding.workflowRun,
    operationsPath: onboarding.operationsPath,
    error: onboarding.error,
    retryable: onboarding.retryable,
    isRunning: onboarding.isRunning,
    connectOrders,
    reset: onboarding.reset,
  });
}
```

`testResource`는 외부 REST source를 읽어서 schema/sample/error evidence만 반환하고 dataset commit을 만들지 않는다. 첫
commit은 `startFirstSync`가 시작한 `ConnectorSyncWorkflow` data-plane에서만 생긴다. Source managed sync schedule은
API/SDK/worker에서 current이고, 현재 v1 future scope는 vendor-specific SAP/NetSuite adapter packaging, OAuth
authorization flow, connector-specific visual scheduler UI, cloud/Vault secret manager, cloud/Vault secret manager
and secret rotation API, CDC/Debezium onboarding이다.

## Dataset Explorer

Dataset Explorer는 "파일 미리보기"가 아니라 committed dataset version, schema, manifest, quality,
lineage evidence를 보는 화면이다.

```ts
import { createDatasetExplorerRecipe } from "@foundry-lite/sdk/screen-recipes";

const datasetExplorer = createDatasetExplorerRecipe(client);
const datasets = await datasetExplorer.listDatasets();
const selected = { namespace: "raw", name: "orders", version: "v1" };

const [versions, inspection, preview, quality, lineage] = await Promise.all([
  datasetExplorer.listVersions(selected),
  datasetExplorer.inspect(selected),
  datasetExplorer.preview({ ...selected, previewLimit: 100 }),
  datasetExplorer.qualitySummary(selected),
  datasetExplorer.lineage(selected),
]);

renderDatasetExplorer({ datasets, versions, inspection, preview, quality, lineage });
```

React Dataset Explorer screens should use `useFoundryLiteDatasetExplorer(...)` or the provider-backed
`useFoundryLiteProvidedDatasetExplorer(...)` to load the same evidence bundle as one screen state.

```tsx
const explorer = useFoundryLiteProvidedDatasetExplorer({
  namespace: selected?.namespace,
  name: selected?.name,
  version: selectedVersion,
  previewLimit: 100,
});

renderDatasetExplorer({
  datasets: explorer.datasets,
  versions: explorer.versions,
  selectedDataset: explorer.selectedDataset,
  selectedVersion: explorer.inspectedVersion,
  manifest: explorer.manifest,
  previewRows: explorer.previewRows,
  qualitySummary: explorer.qualitySummary,
  lineage: explorer.lineage,
  requestId: explorer.requestId,
  retryable: explorer.retryable,
});
```

React list screens that have cursor state should use `useFoundryLiteCursorPagination(...)` instead of rebuilding page
accumulation, request id, retryability, reload, reset, and `nextCursor` handling per screen.

## Object And Action Workspace

Object/action screens should use generated object/action types when they exist, and live ontology metadata when the
workspace is catalog-driven.

```ts
import { idempotencyKey } from "@foundry-lite/sdk";
import { loadObjectActionWorkspaceRecipe } from "@foundry-lite/sdk/screen-recipes";

const workspace = await loadObjectActionWorkspaceRecipe(client);

const orderPage = await workspace.fetchObjectPage("Order", {
  pageSize: 50,
  search: "pending",
});

const order = orderPage.items[0];
const result = await workspace.applyAction("ApproveOrder", {
  objectId: order.objectId,
  expectedObjectVersion: order.version,
  params: { reason: "approved in workspace" },
  idempotencyKey: idempotencyKey("ApproveOrder", order.objectId),
});

const actionPalette = workspace.actionsForObjectType("Order");
renderObjectWorkspace({ orderPage, result, actionPalette });
```

For React, `useFoundryLiteObjectQuery(...)`, `useFoundryLiteActionSubmit(...)`, and
`useFoundryLiteProvidedOntologyExplorer(...)` provide screen state for object pages, action submit status,
generated/dynamic ontology hints, search, selected object/action, and action grouping.
For larger catalogs, `useFoundryLiteOntologyWorkspaceShell(...)` and
`useFoundryLiteProvidedOntologyWorkspaceShell(...)` return object cards, selected action palette items, generated vs
dynamic-only badges, disabled reasons, and `canQuerySelectedObject` / `canSubmitSelectedAction` booleans so screens do
not rebuild catalog safety rules locally.
Action forms should use `foundryLiteActionFormView(...)` instead of parsing `parameterSchema` in the component. The
helper turns the selected catalog action plus the selected object, form params, expected object version, and stable
idempotency key into `parameterFields`, `missingFields`, `disabledReason`, `payload`, and `canSubmitAction`.
`useFoundryLiteActionForm(...)` and `useFoundryLiteProvidedActionForm(...)` add the screen-level state layer:
`setParam(...)`, `replaceParams(...)`, `resetForm(...)`, stable idempotency-key state, duplicate-submit locking, and
`submit()` through the generated OSDK action. `useFoundryLiteProvidedOntologyWorkspaceShell(...)` also exposes
`selectedActionForm` and `recommendedActionForm` for the catalog-only state.
For a full object/action screen, `useFoundryLiteObjectActionWorkspace(...)` and
`useFoundryLiteProvidedObjectActionWorkspace(...)` combine the live ontology shell, generic object query,
`selectedObject`, `recommendedObject`, and the managed action form. This is the ergonomic path for large catalogs
because object lists continue to use named SDK methods even when an ontology type is dynamic-only until the SDK is
regenerated.

```tsx
const ontologyShell = useFoundryLiteProvidedOntologyWorkspaceShell({
  selectedObjectApiName: "Order",
  selectedActionApiName: "ApproveOrder",
});

const actionForm = foundryLiteActionFormView(ontologyShell.explorer.selectedActionView, {
  targetObject: order,
  params: { reason },
  idempotencyKey: stableSubmitKey,
  requireIdempotencyKey: true,
});

renderActionForm(actionForm.parameterFields, {
  missingFields: actionForm.missingFields,
  disabledReason: actionForm.disabledReason,
  canSubmitAction: actionForm.canSubmitAction,
});
```

```tsx
const selectedOrder = orderPage.items[0] ?? null;
const managedActionForm = useFoundryLiteProvidedActionForm(ontologyShell.explorer.selectedActionView, {
  targetObject: selectedOrder,
  initialIdempotencyKey: selectedOrder ? idempotencyKey("ApproveOrder", selectedOrder.objectId) : null,
});

function onReasonChange(reason: string) {
  managedActionForm.setParam("reason", reason);
}

async function onSubmit() {
  if (!managedActionForm.canSubmitAction) return;
  await managedActionForm.submit();
}
```

```tsx
const objectActionWorkspace = useFoundryLiteProvidedObjectActionWorkspace({
  selectedObjectApiName: "Order",
  selectedActionApiName: "ApproveOrder",
  selectedObjectId,
  objectQuery: { pageSize: 50, search: searchText },
});

renderObjectRows(objectActionWorkspace.objects, {
  recommendedObject: objectActionWorkspace.recommendedObject,
  reload: objectActionWorkspace.reloadObjects,
});

renderActionForm(objectActionWorkspace.actionForm.parameterFields, {
  selectedObject: objectActionWorkspace.selectedObject,
  canSubmitAction: objectActionWorkspace.actionForm.canSubmitAction,
});
```

When an Insight Review has an `actionProposal`, React screens can use `foundryLiteActionProposalView(...)` to show
whether it is executable and `useFoundryLiteActionProposalSubmit(...)` to submit it through the generated OSDK action
surface.

```tsx
const proposalView = foundryLiteActionProposalView(review.actionProposal);
const proposalSubmit = useFoundryLiteActionProposalSubmit(osdk, review.actionProposal);

if (proposalView.disabledReason) {
  showProposalIssue(proposalView.missingFields, proposalView.disabledReason);
}

if (proposalView.canSubmitActionProposal) {
  await proposalSubmit.execute({
    idempotencyKey: `proposal-apply-${review.id}`,
  });
}
```

```tsx
const ontologyShell = useFoundryLiteProvidedOntologyWorkspaceShell({
  objectSearch: searchText,
  selectedObjectApiName: selectedObjectApiName,
  selectedActionApiName: selectedActionApiName,
});
const ontologyExplorer = ontologyShell.explorer;

renderObjectTypeList(ontologyExplorer.objectSearchResults);
renderActionPalette(ontologyExplorer.selectedActionPalette);
renderActionFormSummary(ontologyShell.selectedActionForm);

if (ontologyExplorer.hasSelectedActionOutsideObject) {
  showActionTargetMismatch(ontologyExplorer.selectedActionView);
}

if (ontologyExplorer.sdkRegenerationHint) {
  showSdkRefreshNotice(ontologyExplorer.sdkRegenerationHint);
}
```

## Media Workspace

Media screens should treat committed media item versions as serving truth. The recipe keeps upload, process, index,
and search in the SDK layer.

```ts
import { createMediaWorkspaceRecipe } from "@foundry-lite/sdk/screen-recipes";

const media = createMediaWorkspaceRecipe(client);

const committed = await media.uploadProcessIndexAndSearch(
  mediaSetId,
  {
    logicalPath: "/invoices/scan.png",
    schemaType: "image",
    format: "png",
    file,
    securityEnvelope: { tenantId: activeTenantId, classification: "internal" },
  },
  {
    idempotencyKey: `media-upload-${invoiceId}`,
    process: { processor: "ocr_v1", processorVersion: "1" },
    index: { generation: "invoice-search-v1" },
    search: { text: "invoice total", topK: 5, allowedClassifications: ["internal"] },
  },
);

renderMediaEvidence(committed.processing, committed.indexed, committed.hits);
```

React media screens can use the provider-backed pipeline hook when the screen flow is "upload bytes, commit the media
version, process it, index it, then optionally search".

```tsx
const mediaPipeline = useFoundryLiteProvidedMediaPipeline();

await mediaPipeline.execute({
  mediaSetId,
  logicalPath: "/invoices/scan.png",
  schemaType: "image",
  format: "png",
  file,
  securityEnvelope: { tenantId: activeTenantId, classification: "internal" },
  idempotencyKey: `media-pipeline-${invoiceId}`,
  process: { processor: "ocr_v1", processorVersion: "1" },
  indexGeneration: "invoice-search-v1",
  search: { text: "invoice total", topK: 5, allowedClassifications: ["internal"] },
});

renderMediaPipeline({
  phase: mediaPipeline.phase,
  servingTruthMediaItemVersionId: mediaPipeline.servingTruthMediaItemVersionId,
  mediaDerivativeId: mediaPipeline.mediaDerivativeId,
  hits: mediaPipeline.hits,
  requestId: mediaPipeline.requestId,
  retryable: mediaPipeline.retryable,
});
```

The pipeline hook still treats the committed media item version as serving truth. Processing, indexing, and search are
derived evidence/projections after that commit. More custom screens can still use `useFoundryLiteMediaUpload(...)`,
`useFoundryLiteMediaProcessing(...)`, `useFoundryLiteMediaSearch(...)`, or their provider-backed variants when they need
separate controls for each phase.

## AIP Workspace

AIP screens should not call a model provider directly. They submit through the governed SDK surface and then link to
Operations evidence.

```ts
import { createAipWorkspaceRecipe } from "@foundry-lite/sdk/screen-recipes";

const aip = createAipWorkspaceRecipe(client);

const { result, detail } = await aip.runAgentAndLoadOperationsDetail({
  agentVersionId: "ops-agent-v1",
  promptVersionId: "prompt-v4",
  userMessage: "Summarize blocked orders with citations",
  securityPartition: "tenant-main",
  allowedSecurityPartitions: ["tenant-main"],
});

renderAipRun({ result, detailPath: result.operations?.detailPath ?? null, detail });
```

For React, `useFoundryLiteAipAgentRun(...)` and `useFoundryLiteAipBuilderRun(...)` normalize run status into
screen phases. Under `FoundryLiteProvider`, `useFoundryLiteProvidedAipAgentRunWithOperationsDetail(...)` and
`useFoundryLiteProvidedAipBuilderRunWithOperationsDetail(...)` also load the linked Operations run detail so evidence
panels can render from `operationsDetail` without hand-wiring the run id.

## Insight Review Workspace

Insight Review는 AI가 만든 claim이나 action proposal을 곧바로 실행하지 않고, 사람이 evidence를 보고
승인/반려하는 작업대다. 화면은 raw API path를 알 필요 없이 review queue와 decision helper를 사용한다.

```ts
import { createInsightReviewWorkspaceRecipe } from "@foundry-lite/sdk/screen-recipes";

const insights = createInsightReviewWorkspaceRecipe(client);
const queue = await insights.loadQueue(
  { status: "pending", limit: 50 },
  { currentUserId: currentUser.id, selectedReviewId },
);

renderInsightReviewQueue({
  pending: queue.pendingReviews,
  assignedToMe: queue.currentUserAssignedReviews,
  highPriority: queue.highPriorityReviews,
  selectedReview: queue.selectedReview,
  selectedActionProposal: queue.selectedActionProposal,
});

await insights.decideReview(
  queue.selectedReview.id,
  { decision: "approved", comment: "Evidence checked" },
  { idempotencyKey: `insight-approve-${queue.selectedReview.id}` },
);
```

React screens can use `useFoundryLiteProvidedInsightReviewQueue(...)` and
`useFoundryLiteInsightReviewDecision(...)` for the same queue lanes, selected proposal, request id, retryability,
loading state, and idempotent decision submit state. If the user approves the proposal and wants to execute the
suggested action, `useFoundryLiteActionProposalSubmit(...)` bridges that selected proposal to the OSDK action path.

```tsx
const queue = useFoundryLiteProvidedInsightReviewQueue({
  status: "pending",
  currentUserId: currentUser.id,
  selectedReviewId,
});
const decision = useFoundryLiteInsightReviewDecision(client);
const proposalSubmit = useFoundryLiteActionProposalSubmit(osdk, queue.selectedActionProposal);

if (queue.selectedReview && queue.canReviewSelectedActionProposal) {
  await decision.execute({
    reviewId: queue.selectedReview.id,
    decision: "rejected",
    comment: "Action proposal needs a safer target object version",
    idempotencyKey: `insight-reject-${queue.selectedReview.id}`,
  });

  await proposalSubmit.execute({
    idempotencyKey: `insight-action-${queue.selectedReview.id}`,
  });
}
```

Current scope: durable review rows, assignment, idempotent approve/reject, selected action proposal metadata, and
SDK-side proposal-to-OSDK payload bridging. Future scope: rich evidence viewer, model-diff UI, approval policy editor,
and autonomous approved-action execution orchestration.

## Pipeline Builder

Pipeline Builder screens can now work graph-first: create a branch, save nodes/edges/layout/output contracts with a
CAS fingerprint, validate the graph, preview/stats a node, run tests, send the branch to review, deploy an approved
version, start/cancel runs, and manage schedules through `client.pipelines.*`.

```ts
import { createPipelineBuilderRecipe } from "@foundry-lite/sdk/screen-recipes";

const pipeline = createPipelineBuilderRecipe(client);

const branch = await pipeline.createBranch({
  pipelineId: "orders-readiness",
  name: "join-orders-customers",
  idempotencyKey: "pipeline-branch-orders-readiness",
});

await pipeline.updateGraph(branch.id, {
  graph: {
    nodes: [
      { id: "orders", type: "dataset", config: { datasetRef: "raw.orders" } },
      { id: "customers", type: "dataset", config: { datasetRef: "raw.customers" } },
      { id: "join", type: "join", config: { leftKey: "customer_id", rightKey: "id" } },
      { id: "python-score", type: "python", config: { functionName: "score_rows", sourceCode: "def score_rows(rows): return rows" } },
      { id: "out", type: "output_dataset", config: { outputDatasetRef: "clean.orders_readiness" } },
    ],
    edges: [
      { source: "orders", target: "join", targetHandle: "left" },
      { source: "customers", target: "join", targetHandle: "right" },
      { source: "join", target: "python-score" },
      { source: "python-score", target: "out" },
    ],
    outputContract: { columns: [{ name: "id", type: "string", nullable: false }] },
    tests: [],
  },
  expectedFingerprint: branch.graphFingerprint,
});

const validation = await pipeline.validate(branch.id);
const preview = await pipeline.previewNode(branch.id, "python-score", { limit: 50 });
const proposal = await pipeline.propose(branch.id, {
  title: "Deploy orders readiness pipeline",
  idempotencyKey: "pipeline-proposal-orders-readiness",
});

renderPipelineCanvas({ branch, validation, preview, proposal });
```

React graph screens can use `useFoundryLitePipelineBuilder(...)`, `useFoundryLitePipelineGraph(...)`,
`useFoundryLitePipelinePreview(...)`, `useFoundryLitePipelineReview(...)`, or provider-backed pipeline hooks for branch,
canvas, preview, and review state. Python nodes send `sourceCode`, `functionName`, and `inputs`; the backend stores the
managed artifact and the frontend never handles raw server file paths.

The older SQL transform builder path is still supported when a screen only needs one SQL definition and an optional
run:

```ts
const { transform, run } = await pipeline.registerAndRunSql({
  apiName: "clean_invoice_totals",
  sql: "select * from {{ input('raw.invoices') }}",
  inputs: { invoices: "raw.invoices" },
  outputDatasetRef: "clean.invoice_totals",
});

renderPipelineRun(transform, run);
```

React screens can use `useFoundryLiteSqlTransformSubmit(...)` or the provider-backed
`useFoundryLiteProvidedSqlTransformSubmit(...)` for submit status, retryability, request id, and optional run behavior.
The hook also exposes screen-ready output evidence: `hasOutputDatasetVersion`, `outputDatasetRef`,
`outputDatasetVersionId`, `outputDatasetVersionNumber`, `outputDatasetRowCount`, `outputManifestUri`, and
`outputSchemaHash`.

## Long-Running Operations

Long-running work should expose a run id/status to the user. Polling must be bounded and terminal-state aware.

```ts
import { createLongRunningOperationRecipe } from "@foundry-lite/sdk/screen-recipes";

const jobs = createLongRunningOperationRecipe(client);

const finalRun = await jobs.startAndPollConnectorSync(
  { datasetRef: "raw.orders", connectorName: "erp", resourceName: "orders" },
  {
    idempotencyKey: "connector-sync-raw.orders",
    intervalMs: 1000,
    maxAttempts: 120,
    onSnapshot: ({ snapshot }) => renderWorkflowStatus(snapshot),
  },
);

renderWorkflowDone(finalRun);
```

When a backend route exposes event frames, use the SDK stream helper instead of custom fetch loops. Under
`FoundryLiteProvider`, React screens should prefer the provider-based hook so auth/session/request context stays in one
place.

```ts
for await (const event of jobs.streamOperationEvents(run.eventsPath, {
  baseUrl: window.location.origin,
  tokenProvider: sessionTokenProvider,
  context: { tenantId: activeTenantId, userId: currentUser.id, roles: currentUser.roles },
})) {
  appendTimelineEvent(event);
}
```

React screens can use `useFoundryLiteLongRunningJob(...)`, `useFoundryLiteWorkflowRun(...)`,
`useFoundryLiteProvidedOperationEventStream(...)`, and `useFoundryLiteProvidedLiveOperationTimeline(...)` for
start/poll/stream screen state.

```tsx
const stream = useFoundryLiteProvidedOperationEventStream(run.eventsPath ?? null, {
  autoStart: true,
  onEvent: ({ event }) => appendTimelineEvent(event),
});

if (stream.isStreaming) renderLiveTimeline(stream.events);
if (stream.error) renderRequestError(stream.requestId, stream.error.code, stream.retryable);

const timeline = useFoundryLiteProvidedLiveOperationTimeline(
  (payload: { datasetRef: string; connectorName: string; resourceName: string }) =>
    client.operations.workflows.startConnectorSync(payload, {
      idempotencyKey: `connector-sync-${payload.datasetRef}`,
    }),
  ({ startResult }) => client.operations.workflows.get(startResult.workflowRunId),
  {
    isTerminal: isFoundryLiteWorkflowTerminal,
    getEventStreamPath: ({ snapshot }) => snapshot?.eventsPath ?? null,
  },
);

renderLiveTimeline(timeline.timelineItems);
if (timeline.streamError) renderRequestError(timeline.streamRequestId, timeline.streamError.code, timeline.retryable);
```

## Operations Evidence Investigation

Use the Operations evidence recipe when a screen needs to answer "what failed, what evidence was written, and what
should an operator inspect next?" without rebuilding run-row normalization in each frontend.

```ts
import { createOperationsEvidenceRecipe } from "@foundry-lite/sdk/screen-recipes";

const evidence = createOperationsEvidenceRecipe(client);
const investigation = await evidence.loadRunInvestigation(
  { runType: "ai", runId: selectedRunId, promptArtifactId },
  { status: "failed", limit: 50 },
);

renderRuns(investigation.runs);
renderRunDetail(investigation.detail);
renderPromptArtifact(investigation.promptArtifact?.plaintext ?? null);
```

React screens under `FoundryLiteProvider` can use one composite hook for the same flow.

```tsx
const investigation = useFoundryLiteOperationsInvestigation(client, {
  status: "failed",
  limit: 50,
  selectedRunType,
  selectedRunId,
});

renderRunRows(investigation.list.failedRuns, investigation.list.runningRuns);
renderEvidenceCounts(investigation.detail.relatedAuditEventCount, investigation.detail.lineageEdgeCount);

if (investigation.promptArtifact.plaintext) {
  renderPromptArtifact(investigation.promptArtifact.exportMarking, investigation.promptArtifact.plaintext);
}
```

For simpler screens, use `useFoundryLiteOperationsRunList(...)`, `useFoundryLiteOperationsRunDetail(...)`, and
`useFoundryLitePromptArtifact(...)` separately. These helpers classify run state, expose request id/retryability, and
load AI prompt artifacts only through `client.operations.runs.promptArtifact(...)`.

## Record DLQ And Writeback Reconciliation

Record DLQ screens handle bad source records that were quarantined instead of silently poisoning a dataset. Writeback
reconciliation screens handle external side effects whose outcome was unknown or requires compensation.

```ts
import {
  createRecordDlqOperationsRecipe,
  createWritebackReconciliationRecipe,
} from "@foundry-lite/sdk/screen-recipes";

const recordDlq = createRecordDlqOperationsRecipe(client);
const writebacks = createWritebackReconciliationRecipe(client);

const quarantined = await recordDlq.listRecords({ status: "QUARANTINED" });
const selectedRecord = await recordDlq.loadRecord(selectedRecordId);
await recordDlq.retryRecord(selectedRecord.id, { idempotencyKey: `record-dlq-${selectedRecord.id}` });
await recordDlq.bulkRetryRecords(quarantined.map((record) => record.id), {
  idempotencyKey: "record-dlq-bulk-retry-open",
});
await recordDlq.discardRecord(selectedRecord.id);

const unresolved = await writebacks.listWritebacks({ status: "outcome_unknown", limit: 50 });
await writebacks.resolveWriteback(unresolved.items[0].writebackId, {
  remoteStatus: "succeeded",
  remoteResourceId: "erp-order-123",
});
```

React screens can keep the same remediation state under the provider.

```tsx
const dlqQueue = useFoundryLiteRecordDlqQueue(client, {
  status: "QUARANTINED",
  selectedRecordId,
});
const dlqControls = useFoundryLiteRecordDlqControls(client);
const writebackQueue = useFoundryLiteWritebackReconciliationQueue(client, {
  status: "compensation_required",
  selectedWritebackId,
  limit: 50,
});
const writebackResolve = useFoundryLiteWritebackResolve(client);

renderDlqRows(dlqQueue.openRecords, dlqQueue.failedReplayRecords);
renderWritebackRows(writebackQueue.outcomeUnknownItems, writebackQueue.compensationRequiredItems);

if (dlqQueue.canRetrySelected && dlqQueue.selectedRecord) {
  await dlqControls.retry.execute({
    id: dlqQueue.selectedRecord.id,
    idempotencyKey: `record-dlq-${dlqQueue.selectedRecord.id}`,
  });
}

if (writebackQueue.canResolveSelected && writebackQueue.selectedWriteback) {
  await writebackResolve.execute({
    writebackId: writebackQueue.selectedWriteback.writebackId,
    payload: { remoteStatus: "succeeded", remoteResourceId },
  });
}
```

These helpers are current browser-safe operator workbench helpers. They do not run autonomous compensation workers or
privileged migration/daemon/bootstrap operations.

## Maintenance And Observability Workbench

Use this recipe when an operations screen needs to inspect detector incidents, preview Iceberg maintenance, or run
bounded replay/retry controls through named SDK methods.

```ts
import { createMaintenanceOperationsRecipe } from "@foundry-lite/sdk/screen-recipes";

const maintenance = createMaintenanceOperationsRecipe(client);

const report = await maintenance.detectObservability({
  configs: detectorConfigs,
  previousIncidents,
});
const plan = await maintenance.planIcebergMaintenanceReadOnly("clean.orders", {
  branch: "main",
  retentionMinSnapshots: 3,
});
await maintenance.requestIcebergMaintenancePlan("clean.orders", { branch: "main" });
await client.operations.icebergMaintenance.run("clean.orders", {
  branch: "main",
  retentionMinSnapshots: 3,
});
await maintenance.replayObjectTypeIndex("Order");
await maintenance.replayFailedIndexRun(indexRunId);
await maintenance.retryTransformRun(transformRunId);

renderObservability(report.activeIncidents, report.suppressedIncidents);
renderIcebergPlan(plan.compaction_candidates, plan.orphan_snapshots, plan.protected_snapshot_ids);
```

React screens can keep those controls in one operations state bundle.

```tsx
const observability = useFoundryLiteObservabilityDetect(client);
const icebergPlan = useFoundryLiteIcebergMaintenancePlan(client, {
  datasetRef: "clean.orders",
  planOptions: { branch: "main", retentionMinSnapshots: 3 },
});
const maintenanceControls = useFoundryLiteMaintenanceControls(client);

await observability.execute({ configs: detectorConfigs });

if (observability.hasCriticalIncidents) {
  renderCriticalIncidents(observability.criticalIncidents);
}

if (icebergPlan.hasDeletableSnapshots) {
  renderProtectedSnapshotWarning(icebergPlan.protectedSnapshotIds);
}

await maintenanceControls.replayObjectTypeIndex.execute({ objectType: "Order" });
await maintenanceControls.replayFailedIndexRun.execute({ runId: indexRunId });
await maintenanceControls.retryTransformRun.execute({ runId: transformRunId });
```

Current boundary: this is observability detection, maintenance planning/run evidence, index replay, and transform retry.
It does not provide a full Iceberg retention policy console, run database migrations, manage long-running worker
daemons, or bootstrap infrastructure from a browser.

## Admin And Recovery Console

Admin screens must not render every backend-adjacent task as a normal browser button. The recipe separates
browser-safe controls from operator command rows and future privileged backend surfaces.

```ts
import {
  createAdminOperationsRecipe,
  createRecoveryOperationsRecipe,
} from "@foundry-lite/sdk/screen-recipes";

const admin = createAdminOperationsRecipe(client);
const recovery = createRecoveryOperationsRecipe(client);

const launchModel = await admin.loadLaunchModel();
const launchpad = await admin.loadLaunchpad();
const operatorCommandCards = await admin.loadOperatorCommandCards();
const internalWorkbench = await admin.loadInternalOperationsWorkbench({
  selectedAreaId: "migration",
  includeBlocked: true,
});
const recoveryOverview = await recovery.loadRecoveryOverview();
const verifiedRestore = await recovery.restoreArtifact({
  artifactRef: "/var/foundry-lite/backups/tenant-demo/backup.json",
  artifactHash: "sha256:...",
  restoreId: "restore-2026-07-01",
  validationId: "post-restore-2026-07-01",
});
const executedRestore = await recovery.executeArtifactRestore({
  artifactRef: verifiedRestore.artifactRef,
  artifactHash: verifiedRestore.artifactHash,
  restoreId: "restore-2026-07-01",
  runPostRestoreValidation: false,
});

if (launchModel.hasBrowserActions) {
  renderBrowserActions(launchModel.browserActions);
}

renderOperatorCommandCards(operatorCommandCards);
renderMigrationCommands(launchModel.migrationCommands);
renderWorkerCommands(launchModel.workerCommands);
renderBootstrapCommands(launchModel.bootstrapCommands);
renderRequiredBackendSurfaces(launchModel.requiredBackendSurfaces);
renderLaunchpadSections(launchpad.visibleSections, launchpad.recommendedSection);
renderInternalOperationsReadiness(internalWorkbench.readiness);
renderPrivilegedCommands(internalWorkbench.privilegedCommandCards);
renderVisibleCommands(internalWorkbench.visibleCommandCards);
renderRecoveryOverview(recoveryOverview);
renderRestoreEvidence(verifiedRestore);
```

React admin screens under `FoundryLiteProvider` can load the same launch model without rebuilding the grouping logic.

```tsx
const launchModel = useFoundryLiteProvidedAdminLaunchModel();
const launchpad = useFoundryLiteProvidedAdminLaunchpad();
const commandCenter = useFoundryLiteProvidedAdminCommandCenter({
  sectionId: "worker",
  query: commandSearch,
  selectedCommandId,
  includeBlocked: true,
});
const internalWorkbench = useFoundryLiteProvidedAdminInternalOperationsWorkbench({
  selectedAreaId: "worker",
  query: commandSearch,
  selectedCommandId,
  includeBlocked: true,
});

renderBrowserActions(launchModel.browserActions);
renderOperatorCommandCards(launchModel.operatorCommandCards);
renderMigrationCommands(launchModel.migrationCommands);
renderWorkerCommands(launchModel.workerCommands);
renderBootstrapCommands(launchModel.bootstrapCommands);
renderRequiredBackendSurfaces(launchModel.requiredBackendSurfaces);
renderLaunchpadSections(launchpad.visibleSections, launchpad.recommendedSection);
renderSelectedInternalSection(internalWorkbench.selectedAreaId, internalWorkbench.selectedSection);
renderInternalReadiness(internalWorkbench.readiness);
renderPrivilegedCommandCards(internalWorkbench.privilegedCommandCards);
renderOperatorCommandCards(commandCenter.visibleCards);
renderSelectedCommand(commandCenter.selectedCard);
renderSectionCounts(commandCenter.sectionCounts);

for (const card of launchModel.operatorCommandCards) {
  renderCommandCard({
    title: card.title,
    command: card.command,
    copyLabel: card.copyLabel,
    canCopyCommand: card.canCopyCommand,
    tone: card.tone,
    evidencePath: card.evidencePath,
    checklist: card.checklist,
  });
}
```

Current browser-safe bounded operations include surfaces like recovery overview/preflight/restore-mode validation and
bounded outbox publish. Direct migration execution, continuously running worker daemon control, and infra bootstrap
from the browser remain future privileged backend surfaces. The launch model keeps those future or operator-only tasks
visible without turning them into unsafe browser buttons.
The launchpad helper adds section counters and per-item `riskLevel`/`riskLabel` values so admin screens can show
migration, worker, bootstrap, and future work as visible disabled/operator sections instead of deriving safety rules in
the UI. `operatorCommandCards`, `migrationCommandCards`, `workerCommandCards`, and `bootstrapCommandCards` add the
copy-command/card layer on top of those rows: each card has `command`, `copyLabel`, `canCopyCommand`, `tone`,
`evidencePath`, and `checklist`, so a frontend can render operator-only work without treating it as a browser action.
`useFoundryLiteProvidedAdminCommandCenter(...)` and `adminCommandCenter(...)` add the command-center state a frontend
usually needs after those cards exist: section filtering, search, `visibleCards`, `selectedCard`, `sectionCounts`,
`visibleSectionCounts`, copyable-command grouping, approval grouping, and blocked-command grouping.
`adminInternalOperationsWorkbench(...)`, `loadInternalOperationsWorkbench(...)`, and
`useFoundryLiteProvidedAdminInternalOperationsWorkbench(...)` package the same safety split into a full internal
operations screen model: `selectedAreaId`, `selectedSection`, `visibleCommandCards`, `privilegedCommandCards`, and
`readiness` counters. This lets a frontend build one admin workbench without locally re-deciding whether migration,
worker, or bootstrap work is browser-safe.

## Screen Helper Map

| Screen Need | SDK Entry |
|---|---|
| Session-aware client | `createSessionTokenProvider(...)`, `FoundryLiteProvider`, `useFoundryLiteClient(...)`, `useFoundryLiteSessionClient(...)`, `useFoundryLiteSessionStatus(...)`, `useFoundryLiteProvidedScreenRecipes(...)`, `needsAuthentication`, `isPermissionDenied`, `canRetryLastRequest` |
| Operator workspace shell | `createOperatorWorkspaceRecipe(...)`, `operatorWorkspace.loadHome(...)`, `operatorWorkspace.loadShell(...)`, `operatorWorkspaceShell(...)`, `useFoundryLiteProvidedOperatorWorkspaceHome(...)`, `useFoundryLiteProvidedOperatorWorkspaceShell(...)`, `areaSurfaces`, `selectedSurface`, `primaryQuickAction` |
| Project/resource browser | `client.resources.projects.*`, `client.resources.folders.*`, `client.resources.items.*`, `client.resources.favorites.*`, `client.resources.trash.*`, `client.resources.admin.reconcile(...)`, `createResourceBrowserRecipe(...)`, `resourceBrowser.loadResources(...)`, `buildResourceBrowserView(...)` |
| Dataset catalog and evidence | `createDatasetExplorerRecipe(...)`, `useFoundryLiteDatasetExplorer(...)`, `useFoundryLiteProvidedDatasetExplorer(...)`, `client.datasets.*`, `client.operations.lineage.get(...)` |
| Object/action workspace | `createObjectActionWorkspaceRecipe(...)`, `useFoundryLiteObjectQuery(...)`, `useFoundryLiteGenericObjectQuery(...)`, `useFoundryLiteObjectActionWorkspace(...)`, `useFoundryLiteProvidedObjectActionWorkspace(...)`, `useFoundryLiteActionSubmit(...)`, `objectQuery`, `selectedObject`, `recommendedObject`, `actionForm` |
| Insight action proposal bridge | `foundryLiteActionProposalView(...)`, `useFoundryLiteActionProposalSubmit(...)` |
| Large ontology ergonomics | `createFoundryLiteOntologyIndex(...)`, `getObjectType(...)`, `getActionType(...)`, `useFoundryLiteOntologyCatalog(...)`, `useFoundryLiteProvidedOntologyExplorer(...)`, `useFoundryLiteOntologyWorkspaceShell(...)`, `useFoundryLiteProvidedOntologyWorkspaceShell(...)` |
| Media upload/process/search | `createMediaWorkspaceRecipe(...)`, `useFoundryLiteProvidedMediaPipeline(...)`, `useFoundryLiteMediaUpload(...)`, `useFoundryLiteMediaProcessing(...)`, `useFoundryLiteMediaSearch(...)` |
| AIP run screens | `createAipWorkspaceRecipe(...)`, `useFoundryLiteAipAgentRun(...)`, `useFoundryLiteAipBuilderRun(...)`, `useFoundryLiteProvidedAipAgentRunWithOperationsDetail(...)`, `useFoundryLiteProvidedAipBuilderRunWithOperationsDetail(...)`, `operationsDetail`, `hasOperationsDetail` |
| Insight review workspace | `createInsightReviewWorkspaceRecipe(...)`, `useFoundryLiteProvidedInsightReviewQueue(...)`, `useFoundryLiteInsightReviewDecision(...)` |
| Pipeline builder graph workspace | `client.pipelines.*`, `createPipelineBuilderRecipe(...)`, `useFoundryLitePipelineBuilder(...)`, `useFoundryLitePipelineGraph(...)`, `useFoundryLitePipelinePreview(...)`, `useFoundryLitePipelineReview(...)`, legacy `useFoundryLiteSqlTransformSubmit(...)`, `useFoundryLiteProvidedSqlTransformSubmit(...)`, `foundryLiteSqlTransformSubmitView(...)`, `outputDatasetVersionId`, `outputDatasetRowCount`, `outputManifestUri` |
| Long-running jobs | `createLongRunningOperationRecipe(...)`, `pollFoundryLiteOperation(...)`, `useFoundryLiteLongRunningJob(...)`, `useFoundryLiteLiveOperationTimeline(...)`, `useFoundryLiteProvidedLiveOperationTimeline(...)` |
| Event streams | `streamFoundryLiteOperationEvents(...)`, `useFoundryLiteOperationEventStream(...)`, `useFoundryLiteProvidedOperationEventStream(...)`, `timelineItems` |
| Operations evidence investigation | `createOperationsEvidenceRecipe(...)`, `useFoundryLiteOperationsInvestigation(...)`, `useFoundryLiteOperationsRunList(...)`, `useFoundryLiteOperationsRunDetail(...)`, `useFoundryLitePromptArtifact(...)` |
| Record DLQ and writeback remediation | `createRecordDlqOperationsRecipe(...)`, `useFoundryLiteRecordDlqQueue(...)`, `useFoundryLiteRecordDlqControls(...)`, `createWritebackReconciliationRecipe(...)`, `useFoundryLiteWritebackReconciliationQueue(...)`, `useFoundryLiteWritebackResolve(...)` |
| Maintenance and observability | `createMaintenanceOperationsRecipe(...)`, `useFoundryLiteObservabilityDetect(...)`, `useFoundryLiteIcebergMaintenancePlan(...)`, `useFoundryLiteMaintenanceControls(...)` |
| Admin/recovery console | `createAdminOperationsRecipe(...)`, `loadLaunchModel()`, `loadLaunchpad()`, `loadOperatorCommandCards()`, `loadInternalOperationsWorkbench()`, `adminCommandCenter(...)`, `adminInternalOperationsWorkbench(...)`, `createRecoveryOperationsRecipe(...)`, `useFoundryLiteAdminOperationsBoard(...)`, `useFoundryLiteProvidedAdminLaunchModel(...)`, `useFoundryLiteProvidedAdminLaunchpad(...)`, `useFoundryLiteProvidedAdminCommandCenter(...)`, `useFoundryLiteProvidedAdminInternalOperationsWorkbench(...)`, `visibleCards`, `visibleCommandCards`, `selectedCard`, `selectedAreaId`, `readiness`, `sectionCounts` |

## Current Boundary

What is current: SDK request/error/session helpers, generated named API methods, OSDK-style object/action/media facade,
Compass-style Projects/Resources DB/API/SDK surface with project grants, folders, RID-backed resources, favorites,
trash/restore, explicit admin reconcile, and `createResourceBrowserRecipe(...)`,
TypeScript ObjectSet `where({ status: { $eq: "PENDING" } })` / `orderBy({ amount: "desc" })` /
`aggregate({ select: { count: { $count: "unordered" } }, groupBy: { region: "exact" } })` / `$pageSize`
request aliases with fail-fast property/operator/aggregate validation, generated TS instance `$link` traversal such as
`order.$link.customer.fetchPage(...)`, bound instance action calls such as
`order.$actions.approveOrder.validateAction(params)` and
`order.$actions.approveOrder.applyAction(params, { idempotencyKey, onCacheRefresh })`, a Python application OSDK mirror through
`foundry(Order).where(status={"eq": "PENDING"}).fetch_page(...)`,
`foundry(Customer).aggregate({"select": {"count": {"$count": "unordered"}}, "group_by": {"region": "exact"}})`, and
`foundry(ApproveOrder).validate_action(...)`, `foundry(ApproveOrder).apply_action(..., idempotency_key=...)`, TS/Python SDK package manifest plus live-catalog
drift report/assert helpers that tell a developer when SDK regeneration is required, Developer Console-lite app/resource/client
grants, JWT claim app-scope headers, local OAuth authorization-code/PKCE/token/refresh/revoke lifecycle, local TS/Python SDK
version artifacts with channels/compatibility windows/download tokens, ObjectSet WebSocket `.subscribe(...)` with SSE/fetch-stream
fallback, at-least-once subscription semantics, reconnect/resume cursor, backpressure guard, rate-limit normalization,
browser CORS/WebSocket Origin/security threat-model proof, screen recipe builders, React screen-state hooks, request/helper
contract proof, and documentation gates.

What remains future: polished visual components, full login/session UI, broader visual server-push timeline components,
richer aggregate functions/server-side aggregate execution, richer multi-hop/batched link traversal, polished action validation/edit-return
visual UX beyond the current validation/edit/cache hints, external NPM/PyPI publishing, visual Developer Console UI, OAuth
external IdP introspection/refresh-token revocation, direct browser execution for migration/worker/bootstrap,
exactly-once subscription delivery, Palantir-grade external package lifecycle/deployment compatibility windows, and complete
S62-S64 workspace UX, including direct folder/resource grant overrides beyond v1 project-level inheritance.
