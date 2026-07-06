# Frontend Backend Surface Contract

**Status:** S61 partial + S62/S63 backend/API/SDK slices / current backend + catalog + SDK safety-helper surface lock

이 문서는 프론트엔드를 올리기 전에 백엔드가 어떤 방식으로 프론트에 기능을 제공해야
하는지 정의한다. 비개발자식으로 말하면, 프론트와 백엔드 사이의 공식 메뉴판이다. 프론트는
이 메뉴판에 없는 내부 주소나 DB/vendor SDK를 직접 만지지 않는다.

## Source Of Truth

현재 source of truth는 다섯 가지다.

| Evidence | Role |
|---|---|
| `apps/api/foundry_lite_api/main.py` | 실제 FastAPI route 목록 |
| `packages/sdk-ts/src/generated.ts` | package용 generated TypeScript SDK |
| `apps/web/generated-sdk.js` | browser용 generated SDK |
| `docs/frontend-api-sdk-surface-matrix.json` | route -> SDK -> proof test -> operator evidence 매핑 |
| `tests/sdk/request_contract.mjs` | browser SDK가 실제로 보내는 method/path/header/body 계약 |

`scripts/quality/check_frontend_backend_surface.py`는 위 source of truth와 `apps/web/index.html`을
함께 검사한다. 실패하면 `artifacts/quality/frontend_backend_surface.json`과
`artifacts/quality/frontend_backend_surface.md`에 어떤 route, SDK method, proof test,
SDK helper, 파일을 봐야 하는지 남긴다.

## Locked Rule

- frontend-consumable API는 반드시 named generated SDK method가 있어야 한다.
- retry/backoff, cursor pagination, duplicate-action lock, stale-version conflict 분류,
  permission-denied 분류처럼 프론트에서 반복 구현하면 위험한 공통 로직은 SDK helper로 제공하고,
  `docs/frontend-api-sdk-surface-matrix.json`의 `sdkHelpers` row로 증명한다.
- Mutation SDK calls whose matrix row declares `requiresIdempotencyKey: true` require a caller-supplied
  `idempotencyKey`; the generated browser SDK raises `MISSING_IDEMPOTENCY_KEY` before the API call if a
  JavaScript caller forgets it. Retry UX must create one key for the user intent and reuse that same key across
  retry attempts.
- Web app은 `sdkClient().request("/api/...")`처럼 raw API path를 직접 조립하지 않는다.
- route가 프론트용이 아니라면 `nonFrontendRoutes`에 reason과 access class를 명시한다.
- 프론트용 surface는 `proofClass: "sdk-request-contract"`, proof test, operator evidence 설명을
  가져야 한다.
- 문서가 frontend route surface 수나 SDK helper 수를 손으로 적으면, 그 숫자는 실제
  `docs/frontend-api-sdk-surface-matrix.json`와 `SDK_CLIENT_SURFACE.helpers` count와 일치해야 한다.
- `tests/sdk/request_contract.mjs`는 browser SDK를 fake fetch로 실제 실행해 method, path,
  query string, context/request-id header, idempotency header, JSON body, typed error
  metadata, and frontend foundation helper behavior를 검증한다.
- 실패는 화면의 에러 문구에만 머물면 안 된다. request id, run detail, audit, transaction,
  outbox/error payload 중 적절한 곳에서 원인을 추적할 수 있어야 한다.

## Current Named SDK Surface

현재 S61 surface lock은 다음 현재 backend route를 named SDK로 고정한다.

| Area | SDK Surface |
|---|---|
| System | `client.system.health()` |
| Datasets | `client.datasets.list()`, `client.datasets.versions(...)`, `client.datasets.preview(...)`, `client.datasets.inspect(...)` |
| Ontology | `client.ontology.catalog()`, `client.ontology.validate(...)` |
| Objects | `client.objects.generic.get/query/links(...)`, generated `Order`/`Customer` clients |
| Object Sets | `client.objectSets.list/create/get(...)` |
| Actions | generated `client.actions.ApproveOrder.apply(...)` |
| Materializations | `client.materializations.run(...)` |
| Transforms | `client.transforms.registerSql(...)`, `client.transforms.run(...)`, `client.transforms.previewDue(...)`, `client.transforms.tick(...)` |
| Pipelines | `client.pipelines.branches.create/list/get/updateGraph/diff/rebase/propose/abandon(...)`, `client.pipelines.graph.validate/suggestCasts/previewNode/stats/runTests(...)`, `client.pipelines.proposals.*`, `client.pipelines.versions.*`, `client.pipelines.deploy(...)`, `client.pipelines.runs.*`, `client.pipelines.schedules.*` |
| Operations | run list/detail, AI prompt artifact access, admin overview, lineage get, transform retry, index replay, bounded outbox publish, outbox DLQ retry, Record DLQ controls |
| Platform Ops | observability detect, backup/restore, reconciliation, workflows, Iceberg maintenance `planReadOnly`/`plan`/`run` |
| Connectors | `client.connectors.connections.create/list/get/update(...)`, `client.connectors.resources.upsert/test/startSync(...)` |
| Sources | `client.sources.list/get(...)`, `client.sources.templates.list(...)`, `client.sources.credentials.create/list/get(...)`, `client.sources.agents.register/list/heartbeat(...)`, `client.sources.networkPolicies.create/list(...)`, `client.sources.exploration.run(...)`, `client.sources.managedSyncs.create/list/get/startRun/listRuns/getRun(...)`, `client.sources.scheduler.previewDue/tick(...)`, `client.sources.csv.upload(...)`, `client.sources.batchFiles.upload(...)`, `client.sources.webhookListeners.create/get(...)`, `client.sources.cdc.debezium.create/startSync(...)`, `client.sources.media.uploadAndCommit(...)`, `client.sources.rest.createConnection/upsertResource/test/startSync(...)` |
| Insights | `client.insights.reviews.list/create/get/assign/decide(...)` |
| AIP | `client.aip.builder.validate(...)`, `client.aip.builder.run(...)`, `client.aip.agent.run(...)` |
| Safety Helpers | `createFoundryLiteClient(...)`, `createSessionTokenProvider(...)`, `createRequestId(...)`, `requestContextHeaders(...)`, `normalizeFoundryLiteError(...)`, `isRetryableFoundryLiteError(...)`, `retryWithBackoff(...)`, `pollFoundryLiteOperation(...)`, `streamFoundryLiteOperationEvents(...)`, `collectCursorPages(...)`, `createInFlightActionLock()`, `createFoundryLiteOntologyIndex(...)`, `adminOperationsBoard(...)`, `getObjectType(...)`, `getActionType(...)`, `actionLockKey(...)`, `idempotencyKey(...)`, `expectedObjectVersion(...)`, `classifyFoundryLiteError(...)` |

The public `client.request(...)` escape hatch still exists inside the SDK package for advanced callers and future
generated methods, but the current Web app may not use it for `/api/...` product controls. Safety helpers are also
matrix-locked: a helper exposed in `SDK_CLIENT_SURFACE.helpers` must have an `sdkHelpers` row, TypeScript export,
operator-evidence note, and helper request-contract proof test.

## Frontend SDK Recipes

These recipes are the current "build a real product screen from the SDK" path. They intentionally avoid raw
`/api/...` strings in application code. The browser request contract proves the method/path/header/body shape behind
these examples. The same screen flows are also shipped as typechecked package code in
`packages/sdk-ts/src/screen-recipes.ts`, exported as `@foundry-lite/sdk/screen-recipes`, so frontend teams can start
from recipe builders instead of copying raw request paths into screens.

```ts
import {
  createAdminOperationsRecipe,
  createAipWorkspaceRecipe,
  createConnectorOnboardingRecipe,
  createDatasetExplorerRecipe,
  createInsightReviewWorkspaceRecipe,
  createLongRunningOperationRecipe,
  createMaintenanceOperationsRecipe,
  createMediaWorkspaceRecipe,
  createObjectActionWorkspaceRecipe,
  createOperatorWorkspaceRecipe,
  createOperationsEvidenceRecipe,
  createPipelineBuilderRecipe,
  createRecordDlqOperationsRecipe,
  createRecoveryOperationsRecipe,
  createSourceOnboardingRecipe,
  createSourceSyncRecipe,
  createSourceUploadRecipe,
  createWritebackReconciliationRecipe,
  operatorWorkspaceNavigation,
} from "@foundry-lite/sdk/screen-recipes";

const datasetExplorer = createDatasetExplorerRecipe(client);
const objectWorkspace = createObjectActionWorkspaceRecipe(client, await client.ontology.catalog());
const operatorWorkspace = createOperatorWorkspaceRecipe(client);
const mediaWorkspace = createMediaWorkspaceRecipe(client);
const aipWorkspace = createAipWorkspaceRecipe(client);
const connectorOnboarding = createConnectorOnboardingRecipe(client);
const sourceOnboarding = createSourceOnboardingRecipe(client);
const sourceUpload = createSourceUploadRecipe(client);
const sourceSync = createSourceSyncRecipe(client);
const insightWorkspace = createInsightReviewWorkspaceRecipe(client);
const pipelineBuilder = createPipelineBuilderRecipe(client);
const jobs = createLongRunningOperationRecipe(client);
const maintenance = createMaintenanceOperationsRecipe(client);
const operationsEvidence = createOperationsEvidenceRecipe(client);
const recordDlq = createRecordDlqOperationsRecipe(client);
const writebackReconciliation = createWritebackReconciliationRecipe(client);
const adminOps = createAdminOperationsRecipe(client);
const recoveryOps = createRecoveryOperationsRecipe(client);
await recoveryOps.restoreArtifact({ artifactRef, artifactHash, restoreId, validationId });
await recoveryOps.executeArtifactRestore({ artifactRef, artifactHash, restoreId, runPostRestoreValidation: false });
const home = await operatorWorkspace.loadHome({ runFilters: { limit: 25 } });
const shell = await operatorWorkspace.loadShell({
  runFilters: { limit: 25 },
  selectedAreaId: "operations",
});
const navItems = operatorWorkspaceNavigation(home).navItems;
```

`createOperatorWorkspaceRecipe(client).loadHome(...)` is the current SDK-level "first operations workspace screen"
recipe. It reads ontology catalog, datasets, recent Operations runs, admin launchpad, and recovery overview through
named SDK calls, then returns screen-ready booleans and counters such as `datasetCount`, `objectTypeCount`,
`failedRunCount`, `hasAdminBrowserActions`, `hasOperatorCommands`, `hasRecoveryActions`, and
`recommendedAdminSectionId`. `operatorWorkspaceNavigation(home)` derives `navItems`, `attentionItems`, and
`quickActions` from that same home model. It does not create a new backend source of truth; it is a frontend composition
helper over the existing API/SDK surface.
`operatorWorkspaceShell(home, selectedAreaId)`, `operatorWorkspace.loadShell(...)`, and
`useFoundryLiteProvidedOperatorWorkspaceShell(...)` add the app-shell layer above that summary: they expose
`areaSurfaces`, `selectedSurface`, `selectedRoute`, `selectedTitle`, `primaryQuickAction`, and `attentionCount`, with
the recommended recipe entry and React hook for each area. A frontend app can render its left navigation and top-level
route switch from SDK state instead of hard-coding the Data/Ontology/Operations/AIP/Admin/Recovery helper map.

### Source onboarding

`client.sources` is the product-facing "first data source" surface. It gives one screen language for Source Wizard
templates, credential vault references, customer-network agents, network policies, source exploration, managed sync
runs, scheduled managed sync due preview/tick, browser CSV upload, browser batch-file upload, inbound webhook listeners, Debezium CDC source setup, media
upload/commit, and REST source wrappers. Existing `client.connectors` and `client.media` remain available for
lower-level callers, but new frontend onboarding screens should start with `client.sources` and the source recipes.

```tsx
import { idempotencyKey } from "@foundry-lite/sdk";
import { createSourceWizardRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useFoundryLiteProvidedSourceWizard } from "@foundry-lite/sdk/react";

const wizard = createSourceWizardRecipe(client);

const state = await wizard.run(
  {
    sourceType: "postgres_jdbc",
    credential: {
      payload: {
        credentialName: "erp_db",
        displayName: "ERP DB",
        kind: "postgres_jdbc",
        authScheme: "database_url",
        secretValue: connectionStringFromSecureForm,
      },
      idempotencyKey: idempotencyKey("source-credential", "erp_db"),
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
  { onState: (next) => renderSourceWizard(next.phase, next.operationsPath) },
);

function SourceWizardScreen() {
  const wizardState = useFoundryLiteProvidedSourceWizard();
  return renderSourceWizard({
    phase: wizardState.phase,
    selectedTemplate: wizardState.selectedTemplate,
    credential: wizardState.credential,
    agent: wizardState.agent,
    networkPolicy: wizardState.networkPolicy,
    exploration: wizardState.exploration,
    sync: wizardState.sync,
    syncRun: wizardState.syncRun,
    operationsPath: wizardState.operationsPath,
    requestId: wizardState.requestId,
    error: wizardState.error,
    retryable: wizardState.retryable,
    run: wizardState.run,
  });
}
```

The wizard phases are `select_template`, `store_credentials`, `prepare_network`, `explore_source`, `create_sync`,
`start_sync_run`, `inspect_run`, `operations_evidence`, and `ready_for_ontology`. Exploration returns schema/sample
evidence and an operations path without committing data; managed sync runs record idempotent run evidence, checkpoint
start/end, dataset version or workflow pointers, and redacted error payloads.

```tsx
import { idempotencyKey } from "@foundry-lite/sdk";
import { createSourceOnboardingRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useFoundryLiteProvidedSourceOnboarding } from "@foundry-lite/sdk/react";

const sourceOnboarding = createSourceOnboardingRecipe(client);

const csvState = await sourceOnboarding.run(
  {
    kind: "csv_upload",
    payload: {
      sourceName: "orders_csv",
      displayName: "Orders CSV",
      datasetRef: "raw.orders",
      file: selectedFile,
    },
    idempotencyKey: idempotencyKey("source-csv", "orders_csv"),
  },
  { onState: (state) => renderSourceProgress(state.phase) },
);

function FirstSourceScreen() {
  const onboarding = useFoundryLiteProvidedSourceOnboarding();
  return renderSourceOnboarding({
    phase: onboarding.phase,
    source: onboarding.source,
    datasetRef: onboarding.datasetRef,
    mediaSetId: onboarding.mediaSetId,
    workflowRun: onboarding.workflowRun,
    commitResult: onboarding.commitResult,
    testResult: onboarding.testResult,
    operationsPath: onboarding.operationsPath,
    requestId: onboarding.requestId,
    error: onboarding.error,
    retryable: onboarding.retryable,
    run: onboarding.run,
    reset: onboarding.reset,
  });
}
```

The recipe phases are `select_kind`, `configure_source`, `test_source`, `upload_or_start_sync`,
`commit_or_workflow`, `inspect_dataset_or_media`, `operations_evidence`, and `ready_for_ontology`. CSV and batch-file
uploads commit dataset versions through the dataset transaction path; media upload commits immutable media versions and
returns the serving-truth `mediaItemVersionId`; Debezium starts a bounded CDC sync with fingerprint fail-closed
behavior; REST source wrappers call the Generic REST connector onboarding surface without forcing screen code to use
connector language first. Source schedule evaluation/tick is available through API/SDK and `worker:source-scheduler`; Transform schedule evaluation/tick is available through API/SDK and `worker:transform-scheduler`; remote directory crawling, visual scheduler UI, managed Debezium Connect operations, cloud secret
manager, OAuth authorization flow, and SAP/NetSuite packaged source wizards remain future scope.

### Connector onboarding

Generic REST connector onboarding is now a named SDK/API surface, not a raw Operations workflow shortcut. A frontend
screen can create a tenant-scoped connector registry entry, upsert the `orders` resource, test the external REST source
without committing data, then start and poll the first sync through one recipe state machine.

```tsx
import { idempotencyKey } from "@foundry-lite/sdk";
import { createConnectorOnboardingRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useFoundryLiteProvidedConnectorOnboarding } from "@foundry-lite/sdk/react";

const onboarding = createConnectorOnboardingRecipe(client);

const finalState = await onboarding.runFirstSync(
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
  { intervalMs: 1000, maxAttempts: 120, onState: (state) => renderProgress(state.phase) },
);

function ConnectorScreen() {
  const state = useFoundryLiteProvidedConnectorOnboarding({ intervalMs: 1000, maxAttempts: 120 });
  return renderConnectorOnboarding({
    phase: state.phase,
    requestId: state.requestId,
    testResult: state.testResult,
    workflowRun: state.workflowRun,
    operationsPath: state.operationsPath,
    error: state.error,
    retryable: state.retryable,
    run: state.run,
    reset: state.reset,
  });
}
```

Connector auth is secretRef-only. SDK request types expose `tokenSecretRef` and `headerValueSecretRef`; raw `token` and
raw `headerValue` are rejected before registry persistence. `testResource` reads the external source and returns
schema/sample/error evidence without dataset commit. `startSync` starts the existing `ConnectorSyncWorkflow`, records
`datasetRef`, `connectorName`, `resourceName`, `syncName`, and `configFingerprint`, and the activity fails closed if
the saved registry fingerprint changed after workflow start. Source managed syncs can now be evaluated and ticked by
the Source scheduler API/SDK/worker; SAP/NetSuite adapter packaging, OAuth authorization flow, connector-specific visual
scheduler UI, cloud/Vault secret manager and secret rotation, and CDC/Debezium onboarding remain future scope.

### Session-aware client

```tsx
import { createFoundryLiteClient, createSessionTokenProvider } from "@foundry-lite/sdk";
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
  useFoundryLiteSessionClient,
} from "@foundry-lite/sdk/react";

function AppShell() {
  return (
    <FoundryLiteProvider
      baseUrl={window.location.origin}
      sessionProvider={() => auth.currentSession()}
      context={{ tenantId: activeTenantId, userId: currentUser.id, roles: currentUser.roles }}
    >
      <ObjectWorkspaceScreen />
    </FoundryLiteProvider>
  );
}

function ObjectWorkspaceScreen() {
  const client = useFoundryLiteClient();
  const session = useFoundryLiteSession();
  const sessionStatus = useFoundryLiteSessionStatus();
  const screen = useFoundryLiteProvidedScreenRecipes();
  const appShell = useFoundryLiteProvidedOperatorAppShell({
    runFilters: { limit: 25 },
    selectedAreaId: "ontology",
  });
  const screenStatus = useFoundryLiteScreenStatus({
    error: screen.error ?? appShell.bootError,
    hasData: screen.hasCatalog && appShell.hasCatalog,
    isLoading: screen.isLoading || appShell.isBooting,
    isRefreshing: screen.isRefreshing,
    requestId: screen.requestId ?? appShell.bootRequestId,
    retryable: screen.retryable || appShell.retryable,
  });
  const operatorHome = useFoundryLiteProvidedOperatorWorkspaceHome({
    runFilters: { limit: 25 },
    selectedAreaId: "ontology",
  });
  const workspaceShell = useFoundryLiteProvidedOperatorWorkspaceShell({
    runFilters: { limit: 25 },
    selectedAreaId: "ontology",
  });
  if (screenStatus.needsAuthentication) {
    return renderSignInPrompt(screenStatus.requestId, screenStatus.title);
  }
  if (screenStatus.isPermissionDenied) {
    return renderPermissionDenied(screenStatus.requestId, screenStatus.description);
  }
  if (!screenStatus.shouldRenderContent && screenStatus.error) {
    return renderRequestError(screenStatus.requestId, screenStatus.errorCode, screenStatus.canRetry);
  }
  return renderObjectWorkspace({
    client,
    screen,
    recipes: appShell.recipes,
    reloadWorkspace: appShell.reloadWorkspace,
    canRenderWorkspace: appShell.canRenderWorkspace,
    lastRequestId: session.lastRequestId,
    sessionStatus: appShell.sessionStatus,
    screenStatus,
    navItems: appShell.navigation.navItems,
    quickActions: appShell.navigation.quickActions,
    selectedSurface: appShell.selectedSurface,
    areaSurfaces: appShell.areaSurfaces,
    fallbackHome: operatorHome,
    fallbackWorkspaceShell: workspaceShell,
  });
}

const client = createFoundryLiteClient({
  baseUrl: window.location.origin,
  tokenProvider: createSessionTokenProvider(async () => auth.currentSession()),
  context: {
    tenantId: activeTenantId,
    userId: currentUser.id,
    roles: currentUser.roles,
  },
  onResponse: (metadata) => setLastRequest(metadata.requestId),
});
```

The token provider is called before each request, so refresh-token/session libraries stay outside screen code while
the SDK owns the `Authorization` header. React screens should prefer `useFoundryLiteSessionClient(...)` when they also
need latest request id/error/retryability metadata in screen state; non-React apps can keep the lower-level
`createFoundryLiteClient(...)` plus `createSessionTokenProvider(...)` form.
Provider-backed React screens can call `useFoundryLiteOsdkClient()` to derive the typed OSDK facade from the same
session-aware client, so object/action screens do not create a second unauthenticated client.
`foundryLiteSessionStatus(...)` and `useFoundryLiteSessionStatus(...)` convert the latest provider response into
screen-ready state such as `needsAuthentication`, `isPermissionDenied`, `canRetryLastRequest`, `tone`, `title`, and
`description`. This is not a login/session UI; it is the SDK-owned status interpretation layer that keeps each screen
from re-implementing 401/403/retryable error handling differently.
`foundryLiteScreenStatus(...)` and `useFoundryLiteScreenStatus(...)` convert query/mutation progress, request id,
retryability, errors, and the session status into one screen decision model: `shouldShowSkeleton`,
`shouldShowInlineRefresh`, `shouldRenderContent`, `needsAuthentication`, `isPermissionDenied`, `canRetry`, `requestId`,
and `errorCode`. It is not a visual alert component; it gives product screens consistent loading/auth/error branching.
`useFoundryLiteProvidedOperatorAppShell(...)` composes that session status with `operatorWorkspaceShell(...)` and a
catalog-backed `createFoundryLiteScreenRecipes(...)` bundle. It exposes `canRenderWorkspace`, `shouldShowSignIn`,
`shouldShowPermissionDenied`, `bootError`, `bootRequestId`, `reloadWorkspace`, `navigation`, `selectedSurface`,
`areaSurfaces`, and `recipes` from one hook, so an app shell can wire auth gates, route navigation, and screen recipe
creation without issuing a separate ontology-catalog request just for recipes.

### Ontology object list

```ts
import { Order, createFoundryLiteOsdkClient } from "@foundry-lite/sdk";

const osdk = createFoundryLiteOsdkClient(client);
const page = await osdk(Order)
  .where({ property: "status", op: "eq", value: "PENDING" })
  .fetchPage({ pageSize: 50 });
```

Screens should use `osdk(Order)` for object-centric product UX and reserve `client.objects.generic.*` for generic
builders.

React screens can use `useFoundryLiteObjectQuery(osdk, Order, ...)` from `@foundry-lite/sdk/react` to get typed
objects plus request-id, retryability, loading, refresh, and cursor metadata without hand-rolling screen state.

### Cursor-backed list screen

```tsx
import { useFoundryLiteCursorPagination } from "@foundry-lite/sdk/react";

const runs = useFoundryLiteCursorPagination(
  ["operations", "runs", runType, status],
  (cursor) => client.operations.runs.list({ runType, status, limit: 50, cursor }),
  {
    getItems: (page) => [
      ...page.syncRuns,
      ...page.transformRuns,
      ...page.actionRuns,
      ...page.materializationRuns,
      ...page.workflowRuns,
      ...page.aiRuns,
    ],
    getNextCursor: (page) => page.nextCursor ?? null,
    retry: { maxAttempts: 3 },
  },
);

if (runs.error) showRequestError(runs.requestId, runs.error.code, runs.retryable);
if (runs.hasNextPage) await runs.loadMore();
```

Use `useFoundryLiteCursorPagination(...)` for Operations lists, object-set result pages, search result pages, and
other cursor-backed screens that need accumulated `items`, raw `pages`, `nextCursor`, `loadMore()`, request id,
retryability, and reload/reset state in one place. It is a state helper, not a visual infinite-scroll component.

### Dataset explorer screen

```tsx
const explorer = useFoundryLiteDatasetExplorer(client, {
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
  schema: explorer.schema,
  manifest: explorer.manifest,
  manifestFiles: explorer.manifestFiles,
  previewRows: explorer.previewRows,
  qualitySummary: explorer.qualitySummary,
  lineage: explorer.lineage,
  requestId: explorer.requestId,
  retryable: explorer.retryable,
});
```

A Dataset Explorer should treat `client.datasets.inspect(...)` as the evidence view, not just a row-preview helper.
`useFoundryLiteDatasetExplorer(...)` and the provider-backed `useFoundryLiteProvidedDatasetExplorer(...)` keep the
screen on named SDK calls (`client.datasets.list/versions/preview/inspect(...)`,
`client.datasets.qualityResults.summary(...)`, and `client.operations.lineage.get(...)`) while returning committed
version, schema, manifest/file evidence, preview rows, quality summary, and lineage as one screen state.

### Large ontology registry lookup

```ts
import {
  $Ontology,
  createFoundryLiteOntologyIndex,
  createFoundryLiteOsdkClient,
  getActionType,
  getObjectType,
} from "@foundry-lite/sdk";

const osdk = createFoundryLiteOsdkClient(client);
const ontologyIndex = createFoundryLiteOntologyIndex(await client.ontology.catalog());

for (const objectApiName of $Ontology.objectApiNames) {
  const objectType = getObjectType(objectApiName);
  const page = await osdk(objectType).fetchPage({ pageSize: 25 });
  cacheObjectPreview(objectType.apiName, page.items);
}

const actionType = getActionType("ApproveOrder");
const actionTarget = actionType.targetObjectType;
const actionPalette = ontologyIndex.actionsForObjectType(actionTarget);
const supplierObjects = ontologyIndex.findObjectTypes("supplier");
```

Screens that render catalog-driven workspaces should use `$Ontology`, `getObjectType(...)`, and
`getActionType(...)` for generated types and `createFoundryLiteOntologyIndex(...)` for live-catalog search, action
palette grouping, and dynamic-only/static-only type drift hints instead of scattering object/action API name strings
through components.
React catalog-driven screens can use `useFoundryLiteOntologyCatalog(...)`,
`useFoundryLiteOntologyWorkspaceShell(...)`, or the provider-backed
`useFoundryLiteProvidedOntologyExplorer(...)` / `useFoundryLiteProvidedOntologyWorkspaceShell(...)` helpers to turn the
live ontology catalog into a screen-ready workspace model:

```tsx
const ontology = useFoundryLiteOntologyCatalog(client);
const ontologyShell = useFoundryLiteOntologyWorkspaceShell(client, {
  objectSearch: searchText,
  selectedObjectApiName,
  selectedActionApiName,
});
const ontologyExplorer = useFoundryLiteProvidedOntologyExplorer({
  objectSearch: searchText,
  selectedObjectApiName,
  selectedActionApiName,
});

for (const objectView of ontology.objectViews) {
  renderObjectCard({
    title: objectView.displayName,
    propertyCount: objectView.propertyCount,
    actionCount: objectView.actionCount,
    linkCount: objectView.linkCount,
    canUseGeneratedType: objectView.isGeneratedObjectTypeAvailable,
  });
}

if (ontology.hasDynamicOnlyTypes) {
  showSdkRefreshNotice(ontology.dynamicOnlyObjectViews, ontology.dynamicOnlyActionViews);
}

const objectsMatchingSearch = ontology.index.findObjectTypes(searchText);
const actionsForObject = ontology.index.actionsForObjectType(selectedObjectApiName);
const selectedActionPalette = ontologyExplorer.selectedActionPalette;
const selectedActionPaletteItems = ontologyShell.selectedActionPaletteItems;
const selectedActionForm = ontologyShell.selectedActionForm;

if (ontologyExplorer.hasSelectedActionOutsideObject) {
  showActionTargetMismatch(ontologyExplorer.selectedActionView);
}

if (!ontologyShell.canQuerySelectedObject) {
  showStaticSdkRequired(ontologyShell.selectedObjectDisabledReason);
}
```

The generated static registry remains the ergonomic path for known object/action types, while the live catalog model
lets a workspace render newly activated ontology types safely as dynamic-only rows until the SDK is regenerated. The
ontology explorer adds a screen-level model for large catalogs: object/action search results, selected object/action,
selected action palette, generated-type availability, target mismatch detection, and SDK regeneration hints. The
workspace shell goes one step closer to a real screen: it returns object cards, action palette items, dynamic-only
groups, disabled reasons, `selectedActionForm`, `recommendedActionForm`, and `canQuerySelectedObject` /
`canSubmitSelectedAction` booleans so large ontology screens do not reimplement the same catalog safety rules.

### Action submit

```ts
import { ApproveOrder, actionLockKey, expectedObjectVersion, idempotencyKey } from "@foundry-lite/sdk";

await actionLock.run(actionLockKey("ApproveOrder", order.objectId), () =>
  osdk(ApproveOrder).applyAction({
    objectId: order.objectId,
    expectedObjectVersion: expectedObjectVersion(order),
    params: { reason },
    idempotencyKey: idempotencyKey("ApproveOrder", order.objectId),
  }),
);
```

The screen creates one mutation intent key and reuses it across retries. Missing idempotency keys fail before network
I/O for SDK surfaces that require them.

React screens can use `useFoundryLiteActionSubmit(osdk, ApproveOrder)` to get the same OSDK action typing plus
duplicate-submit locking by action/object target. The caller still supplies the idempotency key so retry intent remains
explicit and auditable.
Catalog-driven React screens should use `foundryLiteActionFormView(...)` before submit. It reads
`OntologyCatalogAction.parameterSchema` and returns `parameterFields`, `requiredParameterNames`,
`missingParameterNames`, `missingFields`, `hasIdempotencyKey`, `disabledReason`, `payload`, and `canSubmitAction`.
The helper does not create a new idempotency key during render; the screen supplies a stable key for the user's submit
intent, then passes the returned payload to the generated action submit surface.
When the screen wants SDK-owned form state, `useFoundryLiteActionForm(...)` and
`useFoundryLiteProvidedActionForm(...)` add `setParam(...)`, `replaceParams(...)`, `resetForm(...)`,
`setIdempotencyKey(...)`, duplicate-submit locking, request id/retryability error state, `actionResult`, and
`submit()` on top of the same form view. The provider-backed hook uses `useFoundryLiteOsdkClient()` so it shares the
session token provider and request context from `FoundryLiteProvider`.
Full object/action workspaces can use `useFoundryLiteObjectActionWorkspace(...)` or
`useFoundryLiteProvidedObjectActionWorkspace(...)`. These hooks combine the live ontology shell,
`useFoundryLiteGenericObjectQuery(...)`, `objectQuery`, `selectedObject`, `recommendedObject`, and the managed
`actionForm` into one screen state. The generic query still goes through `client.objects.generic.query(...)`, so large
catalog screens do not fall back to raw `/api/...` paths while waiting for static SDK regeneration.

### Object action workspace screen

```tsx
const pendingOrders = useFoundryLiteObjectQuery(osdk, Order, {
  where: { property: "status", op: "eq", value: "PENDING" },
  pageSize: 50,
});
const selectedOrder = pendingOrders.items[0] ?? null;
const ontologyShell = useFoundryLiteProvidedOntologyWorkspaceShell({
  selectedObjectApiName: "Order",
  selectedActionApiName: "ApproveOrder",
});
const objectActionWorkspace = useFoundryLiteProvidedObjectActionWorkspace({
  selectedObjectApiName: "Order",
  selectedActionApiName: "ApproveOrder",
  selectedObjectId: selectedOrder?.objectId ?? null,
  objectQuery: { pageSize: 50, search: orderSearchText },
});
const approveOrder = useFoundryLiteActionSubmit(osdk, ApproveOrder);
const managedActionForm = useFoundryLiteProvidedActionForm(ontologyShell.explorer.selectedActionView, {
  targetObject: selectedOrder,
  initialIdempotencyKey: selectedOrder ? idempotencyKey("ApproveOrder", selectedOrder.objectId) : null,
});

function updateReason(reason: string) {
  managedActionForm.setParam("reason", reason);
}

async function approveSelectedOrder(order: Order, reason: string) {
  const actionForm = foundryLiteActionFormView(ontologyShell.explorer.selectedActionView, {
    targetObject: order,
    params: { reason },
    idempotencyKey: idempotencyKey("ApproveOrder", order.objectId),
    requireIdempotencyKey: true,
  });

  if (!actionForm.canSubmitAction || !actionForm.payload) {
    showActionFormIssue(actionForm.missingFields, actionForm.disabledReason);
    return;
  }

  await approveOrder.execute(actionForm.payload);
  await pendingOrders.reload();
}

async function approveManagedSelection() {
  if (!objectActionWorkspace.actionForm.canSubmitAction) {
    showActionFormIssue(
      objectActionWorkspace.actionForm.missingFields,
      objectActionWorkspace.actionForm.disabledReason,
    );
    return;
  }

  await objectActionWorkspace.actionForm.submit();
  await objectActionWorkspace.reloadObjects();
}

if (approveOrder.error) {
  const classification = classifyFoundryLiteError(approveOrder.error);
  showActionError({
    kind: classification.kind,
    requestId: approveOrder.requestId,
    retryable: approveOrder.retryable,
  });
}
```

This is the current Object Workspace pattern: object query state and action mutation state stay typed by the generated
ontology, the object version is sent with the action, retries reuse one idempotency key, and errors keep request-id and
classification data for the Operations detail path.

Insight/Action screens can also bridge a selected Insight Review proposal into the same action submit path without
hand-building an action payload in the component.

```tsx
const proposalView = foundryLiteActionProposalView(queue.selectedActionProposal);
const proposalSubmit = useFoundryLiteActionProposalSubmit(osdk, queue.selectedActionProposal);

if (proposalView.canSubmitActionProposal) {
  await proposalSubmit.execute({
    idempotencyKey: `proposal-apply-${queue.selectedReview.id}`,
  });
}

if (proposalSubmit.disabledReason) {
  showProposalIssue(proposalSubmit.missingFields, proposalSubmit.disabledReason);
}
```

The proposal bridge checks generated action availability, target object id, expected object version, and target/action
type mismatch before the screen can submit. It does not replace backend approval policy or autonomous action
orchestration; it gives the frontend a typed SDK bridge once the user has reviewed the proposal.

### Media upload and search

```ts
const committed = await osdk.media.uploadAndCommit(
  mediaSetId,
  {
    logicalPath: "/invoices/scan.png",
    schemaType: "image",
    format: "png",
    file,
    securityEnvelope: { tenantId: activeTenantId, classification: "internal" },
  },
  { idempotencyKey: idempotencyKey("UploadInvoice", invoiceId) },
);

const hits = await osdk.media.search({ text: "invoice total", topK: 5 });
```

The helper wraps transaction open, byte upload, and commit. Processing/index/search still go through named SDK media
surfaces so the committed media version remains the serving truth.

React screens can use `useFoundryLiteMediaUpload(...)`, `useFoundryLiteMediaProcessing(...)`,
`useFoundryLiteMediaSearch(...)`, or the provider-backed `useFoundryLiteProvidedMediaPipeline(...)` from
`@foundry-lite/sdk/react` to display upload/processing/index/search state without turning media commit phases into ad
hoc component state.

```tsx
const mediaUpload = useFoundryLiteMediaUpload(client);
const mediaProcessing = useFoundryLiteMediaProcessing(client);
const mediaSearch = useFoundryLiteMediaSearch(client, {
  text: "invoice total",
  topK: 5,
  allowedClassifications: ["internal"],
});

const committedMedia = await mediaUpload.execute({
  mediaSetId,
  logicalPath: "/invoices/scan.png",
  schemaType: "image",
  format: "png",
  file,
  securityEnvelope: { tenantId: activeTenantId, classification: "internal" },
  idempotencyKey: idempotencyKey("UploadInvoice", invoiceId),
});

if (committedMedia?.mediaItemVersionId) {
  await mediaProcessing.execute({
    mediaItemVersionId: committedMedia.mediaItemVersionId,
    process: { processor: "ocr_v1", processorVersion: "1" },
    indexGeneration: "invoice-search-v1",
  });
}

const mediaPipeline = useFoundryLiteProvidedMediaPipeline();
await mediaPipeline.execute({
  mediaSetId,
  logicalPath: "/invoices/scan.png",
  schemaType: "image",
  format: "png",
  file,
  securityEnvelope: { tenantId: activeTenantId, classification: "internal" },
  idempotencyKey: idempotencyKey("MediaPipelineInvoice", invoiceId),
  process: { processor: "ocr_v1", processorVersion: "1" },
  indexGeneration: "invoice-search-v1",
  search: { text: "invoice total", topK: 5, allowedClassifications: ["internal"] },
});
renderMediaPipeline(mediaPipeline.phase, mediaPipeline.servingTruthMediaItemVersionId, mediaPipeline.hits);
```

The pipeline hook is a screen-state helper, not a new media serving source. `servingTruthMediaItemVersionId` is still
the committed media item version; processing, indexing, and search hits are derived evidence/projections after commit.

### AIP agent and builder runs

```tsx
const agentRun = useFoundryLiteProvidedAipAgentRunWithOperationsDetail();
const builderRun = useFoundryLiteProvidedAipBuilderRunWithOperationsDetail();

await agentRun.execute({
  agentVersionId: "ops-agent-v1",
  promptVersionId: "prompt-v4",
  userMessage: "Summarize blocked orders with citations",
  securityPartition: "tenant-main",
  allowedSecurityPartitions: ["tenant-main"],
});

if (agentRun.phase === "failed") {
  showAipEvidence(agentRun.operationsDetail, agentRun.operationsDetailPath, agentRun.errorReason);
}

if (agentRun.hasOperationsDetail) {
  renderAipOperationsDetail(agentRun.operationsDetail);
}
```

The provider hooks are the session-aware path. Client-injected screens can use `useFoundryLiteAipAgentRun(client)` and
`useFoundryLiteAipBuilderRun(client)`, or the `WithOperationsDetail` variants when the same component needs the linked
Operations evidence. The hooks normalize AIP run status into screen phases, expose the Operations detail path, and can
load the linked Operations run detail as `operationsDetail` for evidence panels. They do not replace the backend model
gateway, policy, eval, release, or prompt-artifact evidence; those remain server-owned contracts.

### Insight review workspace

```tsx
import {
  useFoundryLiteInsightReviewDecision,
  useFoundryLiteProvidedInsightReviewQueue,
} from "@foundry-lite/sdk/react";
import { createInsightReviewWorkspaceRecipe } from "@foundry-lite/sdk/screen-recipes";

const recipe = createInsightReviewWorkspaceRecipe(client);
const queue = useFoundryLiteProvidedInsightReviewQueue({
  status: "pending",
  currentUserId: currentUser.id,
  selectedReviewId,
  limit: 50,
});
const decision = useFoundryLiteInsightReviewDecision(client);
const proposalSubmit = useFoundryLiteActionProposalSubmit(osdk, queue.selectedActionProposal);

renderReviewQueue({
  reviews: queue.pendingReviews,
  assignedToMe: queue.currentUserAssignedReviews,
  highPriorityReviews: queue.highPriorityReviews,
  selectedReview: queue.selectedReview,
  selectedActionProposal: queue.selectedActionProposal,
});

await decision.execute({
  reviewId: queue.selectedReview.id,
  decision: "approved",
  comment: "Evidence and proposed action reviewed",
  idempotencyKey: `insight-decision-${queue.selectedReview.id}`,
});

if (queue.canReviewSelectedActionProposal) {
  await proposalSubmit.execute({
    idempotencyKey: `insight-action-${queue.selectedReview.id}`,
  });
}

await recipe.decideReview(
  queue.selectedReview.id,
  { decision: "rejected", comment: "Needs stronger evidence" },
  { idempotencyKey: `insight-reject-${queue.selectedReview.id}` },
);
```

The queue helper turns generated `client.insights.reviews.list/create/get/assign/decide(...)` calls into screen-ready
lanes: pending, assigned, unassigned, assigned-to-current-user, decided, high priority, selected review, and selected
action proposal. `useFoundryLiteActionProposalSubmit(...)` then maps the selected proposal to generated OSDK action
typing when the proposal has a generated action, target object id, and expected object version. It keeps review
creation/assignment/decision idempotency in the SDK layer, while evidence panel UI, model-diff UI, approval policy UI,
and autonomous approved-action orchestration remain product-specific workspace work.

### Pipeline builder graph workspace

```tsx
const builder = useFoundryLiteProvidedPipelineBuilder();
const branch = await builder.createBranch({
  pipelineId: "orders-readiness",
  name: "join-orders-customers",
  idempotencyKey: "pipeline-branch-orders-readiness",
});

await builder.updateGraph(branch.id, {
  graph: {
    nodes: [
      { id: "orders", type: "dataset", config: { datasetRef: "raw.orders" } },
      { id: "customers", type: "dataset", config: { datasetRef: "raw.customers" } },
      { id: "join", type: "join", config: { leftKey: "customer_id", rightKey: "id" } },
      { id: "py", type: "python", config: { functionName: "score_rows", sourceCode: "def score_rows(rows): return rows" } },
      { id: "out", type: "output_dataset", config: { outputDatasetRef: "clean.orders_readiness" } },
    ],
    edges: [
      { source: "orders", target: "join", targetHandle: "left" },
      { source: "customers", target: "join", targetHandle: "right" },
      { source: "join", target: "py" },
      { source: "py", target: "out" },
    ],
    outputContract: { columns: [{ name: "id", type: "string", nullable: false }] },
  },
  expectedFingerprint: branch.graphFingerprint,
});

const validation = await builder.validate(branch.id);
const casts = await builder.suggestCasts(branch.id, "join");
const preview = await builder.previewNode(branch.id, "py", { limit: 50 });
const proposal = await builder.propose(branch.id, {
  title: "Deploy orders readiness pipeline",
  idempotencyKey: "pipeline-proposal-orders-readiness",
});
```

The graph workspace SDK now covers branch creation, CAS graph save, validation, cast suggestions, preview/stats,
test execution, proposal/review, deploy, runs, schedules, and version reads without raw `/api/...` calls. Python nodes
send `sourceCode` and `functionName`; frontend code does not send or receive server file paths. Ontology activation,
virtual table output, and time-series output remain future Pipeline Builder scope.

### SQL pipeline builder

```tsx
const sqlTransform = useFoundryLiteProvidedSqlTransformSubmit();

await sqlTransform.execute({
  definition: {
    apiName: "clean_invoice_totals",
    sql: "select * from {{ input('raw.invoices') }}",
    inputs: { invoices: "raw.invoices" },
    outputDatasetRef: "clean.invoice_totals",
  },
  run: true,
});

const manualRun = await client.transforms.run("clean_invoice_totals");
showTransformRun(sqlTransform.phase, sqlTransform.outputDatasetVersionId, manualRun.version_id);

if (sqlTransform.hasOutputDatasetVersion) {
  renderOutputEvidence({
    datasetRef: sqlTransform.outputDatasetRef,
    versionId: sqlTransform.outputDatasetVersionId,
    versionNumber: sqlTransform.outputDatasetVersionNumber,
    rowCount: sqlTransform.outputDatasetRowCount,
    manifestUri: sqlTransform.outputManifestUri,
    schemaHash: sqlTransform.outputSchemaHash,
  });
}
```

This remains the compatibility SDK path for a focused SQL transform builder screen: register the transform definition,
optionally run it, then use the returned dataset version metadata as the serving evidence. Client-injected screens can still use
`useFoundryLiteSqlTransformSubmit(client)`, and shared model code can call `foundryLiteSqlTransformSubmitView(...)` to
derive the same screen state.

### Long-running operation

```tsx
import { pollFoundryLiteOperation } from "@foundry-lite/sdk";
import {
  isFoundryLiteWorkflowTerminal,
  useFoundryLiteLongRunningJob,
  useFoundryLiteLiveOperationTimeline,
  useFoundryLiteProvidedLiveOperationTimeline,
  useFoundryLiteWorkflowRun,
} from "@foundry-lite/sdk/react";

const run = await client.operations.workflows.startConnectorSync(
  { datasetRef: "raw.orders", connectorName: "erp", resourceName: "orders" },
  { idempotencyKey },
);

const workflowState = useFoundryLiteWorkflowRun(client, run.workflowRunId, {
  autoStart: true,
  intervalMs: 1000,
});

if (workflowState.phase === "failed") {
  showWorkflowFailure(workflowState.operationsDetailPath);
}

const connectorSyncJob = useFoundryLiteLongRunningJob(
  (payload: { datasetRef: string; connectorName: string; resourceName: string }) =>
    client.operations.workflows.startConnectorSync(payload, {
      idempotencyKey: `connector-sync-${payload.datasetRef}`,
    }),
  ({ startResult }) => client.operations.workflows.get(startResult.workflowRunId),
  {
    intervalMs: 1000,
    maxAttempts: 120,
    isTerminal: isFoundryLiteWorkflowTerminal,
    isSuccess: (snapshot) => snapshot.status === "succeeded",
    getRunId: ({ snapshot, startResult }) => snapshot?.workflowRunId ?? startResult?.workflowRunId,
    getStatus: ({ snapshot, startResult }) => snapshot?.status ?? startResult?.status,
  },
);

await connectorSyncJob.start({
  datasetRef: "raw.orders",
  connectorName: "erp",
  resourceName: "orders",
});

if (connectorSyncJob.isRunning) showProgress(connectorSyncJob.runId, connectorSyncJob.status);
if (connectorSyncJob.isFailure) showWorkflowFailure(connectorSyncJob.runId);

const liveConnectorSync = useFoundryLiteProvidedLiveOperationTimeline(
  (payload: { datasetRef: string; connectorName: string; resourceName: string }) =>
    client.operations.workflows.startConnectorSync(payload, {
      idempotencyKey: `connector-sync-${payload.datasetRef}`,
    }),
  ({ startResult }) => client.operations.workflows.get(startResult.workflowRunId),
  {
    intervalMs: 1000,
    maxAttempts: 120,
    isTerminal: isFoundryLiteWorkflowTerminal,
    isSuccess: (snapshot) => snapshot.status === "succeeded",
    getRunId: ({ snapshot, startResult }) => snapshot?.workflowRunId ?? startResult?.workflowRunId,
    getStatus: ({ snapshot, startResult }) => snapshot?.status ?? startResult?.status,
    getEventStreamPath: ({ snapshot }) => snapshot?.eventsPath ?? null,
  },
);

renderTimeline(liveConnectorSync.timelineItems);
if (liveConnectorSync.isLive) showProgress(liveConnectorSync.runId, liveConnectorSync.status);
if (liveConnectorSync.streamError) showRequestError(liveConnectorSync.streamRequestId);

const finalRun = await pollFoundryLiteOperation(
  () => client.operations.workflows.get(run.workflowRunId),
  {
    intervalMs: 1000,
    maxAttempts: 120,
    onSnapshot: ({ snapshot }) => setWorkflowRun(snapshot),
  },
);
```

Polling is bounded and terminal-state aware. `useFoundryLiteWorkflowRun(...)` turns workflow status into screen-ready
`pending/running/succeeded/failed/unknown` phases and exposes the Operations detail path. `useFoundryLiteLongRunningJob(...)`
is the generic screen helper for "start a backend job, poll snapshots, show status/run id, and keep request-id/error
state" flows across connector sync, recovery validation, media processing, transform runs, and future admin actions.
`useFoundryLiteLiveOperationTimeline(...)` and `useFoundryLiteProvidedLiveOperationTimeline(...)` combine the bounded
poll snapshots and optional operation event stream into one `timelineItems` array so a progress screen does not need to
hand-roll two independent loops.

When a backend route exposes server-sent event frames, screens should still avoid hand-written `fetch(...)` loops:

```tsx
import { streamFoundryLiteOperationEvents } from "@foundry-lite/sdk";
import {
  useFoundryLiteOperationEventStream,
  useFoundryLiteProvidedOperationEventStream,
} from "@foundry-lite/sdk/react";

for await (const event of streamFoundryLiteOperationEvents(
  run.eventsPath,
  {
    baseUrl: window.location.origin,
    tokenProvider: sessionTokenProvider,
    context: activeRequestContext,
    onEvent: ({ event }) => appendTimelineEvent(event),
  },
)) {
  if (event.eventType === "done") break;
}

const stream = useFoundryLiteOperationEventStream(
  run.eventsPath ?? null,
  {
    autoStart: true,
    baseUrl: window.location.origin,
    tokenProvider: sessionTokenProvider,
    context: activeRequestContext,
  },
);

const providerStream = useFoundryLiteProvidedOperationEventStream(run.eventsPath ?? null, {
  autoStart: true,
  onEvent: ({ event }) => appendTimelineEvent(event),
});
```

The stream helper is fetch-based, not `EventSource`-based, so it preserves bearer tokens, tenant/user/role headers,
request id propagation, abort handling, and typed `parseEvent(...)` behavior. Actual server push routes and rich
timeline/progress visuals remain separate backend/product slices; worker-daemon control also remains a future admin
surface.

### Operations evidence screen

```tsx
import { createOperationsEvidenceRecipe } from "@foundry-lite/sdk/screen-recipes";
import {
  useFoundryLiteOperationsInvestigation,
  useFoundryLiteOperationsRunDetail,
  useFoundryLiteOperationsRunList,
  useFoundryLitePromptArtifact,
} from "@foundry-lite/sdk/react";

const evidence = createOperationsEvidenceRecipe(client);
const investigation = await evidence.loadRunInvestigation(
  { runType: "ai", runId: selectedRunId, promptArtifactId },
  { status: "failed", limit: 50 },
);
const rawPromptArtifact = await client.operations.runs.promptArtifact(selectedRunId, promptArtifactId);

renderRunRows(investigation.runs);
renderRunDetail(investigation.detail);
renderPromptArtifact(investigation.promptArtifact?.plaintext ?? rawPromptArtifact.plaintext);

const runList = useFoundryLiteOperationsRunList(client, {
  status: "failed",
  limit: 50,
  selectedRunType,
  selectedRunId,
});
const runDetail = useFoundryLiteOperationsRunDetail(client, {
  runType: runList.selectedRun?.runType,
  runId: runList.selectedRun?.runId,
});
const promptArtifact = useFoundryLitePromptArtifact(client, {
  runId: runDetail.runRow?.runId,
  artifactId: runDetail.promptArtifactRefs[0]?.artifactId,
});
const investigationState = useFoundryLiteOperationsInvestigation(client, {
  status: "failed",
  limit: 50,
  selectedRunType,
  selectedRunId,
});
```

The Operations evidence helpers turn raw run-list buckets into one screen model: failed/running/pending/succeeded
rows, selected run detail, related outbox/audit/object-edit/writeback counts, quality/AI evidence flags, and optional
prompt-artifact loading through the governed SDK route. They do not execute privileged operations by themselves; they
give a frontend investigation screen the same run evidence that operators use after sync, transform, materialization,
action, workflow, media, and AIP work.

### Record DLQ and reconciliation workbench

```tsx
import {
  createRecordDlqOperationsRecipe,
  createWritebackReconciliationRecipe,
} from "@foundry-lite/sdk/screen-recipes";
import {
  useFoundryLiteRecordDlqControls,
  useFoundryLiteRecordDlqQueue,
  useFoundryLiteWritebackReconciliationQueue,
  useFoundryLiteWritebackResolve,
} from "@foundry-lite/sdk/react";

const recordDlq = createRecordDlqOperationsRecipe(client);
const writebacks = createWritebackReconciliationRecipe(client);

const quarantinedRecords = await recordDlq.listRecords({ status: "QUARANTINED" });
const selectedRecord = await recordDlq.loadRecord(selectedRecordId);
await recordDlq.retryRecord(selectedRecord.id, { idempotencyKey });
await recordDlq.bulkRetryRecords(quarantinedRecords.map((record) => record.id), { idempotencyKey });
await recordDlq.discardRecord(selectedRecord.id);

const unresolvedWritebacks = await writebacks.listWritebacks({
  status: "outcome_unknown",
  limit: 50,
});
await writebacks.resolveWriteback(selectedWritebackId, {
  remoteStatus: "succeeded",
  remoteResourceId,
});

const dlqQueue = useFoundryLiteRecordDlqQueue(client, {
  status: "QUARANTINED",
  selectedRecordId,
});
const dlqControls = useFoundryLiteRecordDlqControls(client);
const reconciliation = useFoundryLiteWritebackReconciliationQueue(client, {
  status: "compensation_required",
  selectedWritebackId,
  limit: 50,
});
const writebackResolve = useFoundryLiteWritebackResolve(client);

if (dlqQueue.canRetrySelected && dlqQueue.selectedRecord) {
  await dlqControls.retry.execute({
    id: dlqQueue.selectedRecord.id,
    idempotencyKey,
  });
}

if (reconciliation.canResolveSelected && reconciliation.selectedWriteback) {
  await writebackResolve.execute({
    writebackId: reconciliation.selectedWriteback.writebackId,
    payload: { remoteStatus: "succeeded", remoteResourceId },
  });
}
```

The Record DLQ helper separates open, replaying, resolved, discarded, and failed replay rows, and it locks retry/bulk
retry by idempotency key so a screen does not double-submit remediation. The writeback reconciliation helper separates
`outcome_unknown` from `compensation_required` rows and resolves them only through the governed Operations SDK
surface. These helpers are browser-safe operator workbench helpers; they are not autonomous compensation workers and
do not claim migration, worker daemon, or infra bootstrap control.

### Maintenance and observability workbench

```tsx
import { createMaintenanceOperationsRecipe } from "@foundry-lite/sdk/screen-recipes";
import {
  useFoundryLiteIcebergMaintenancePlan,
  useFoundryLiteMaintenanceControls,
  useFoundryLiteObservabilityDetect,
} from "@foundry-lite/sdk/react";

const maintenance = createMaintenanceOperationsRecipe(client);

const observabilityReport = await maintenance.detectObservability({
  configs: detectorConfigs,
  previousIncidents,
});
const readOnlyIcebergPlan = await maintenance.planIcebergMaintenanceReadOnly("clean.orders", {
  branch: "main",
  retentionMinSnapshots: 3,
});
const requestedIcebergPlan = await maintenance.requestIcebergMaintenancePlan("clean.orders");
const maintenanceRun = await client.operations.icebergMaintenance.run("clean.orders", {
  branch: "main",
  retentionMinSnapshots: 3,
});
const objectIndexReplay = await maintenance.replayObjectTypeIndex("Order");
const failedIndexReplay = await maintenance.replayFailedIndexRun(indexRunId);
const transformRetry = await maintenance.retryTransformRun(transformRunId);

const observability = useFoundryLiteObservabilityDetect(client);
const icebergPlan = useFoundryLiteIcebergMaintenancePlan(client, {
  datasetRef: "clean.orders",
  planOptions: { branch: "main", retentionMinSnapshots: 3 },
});
const maintenanceControls = useFoundryLiteMaintenanceControls(client);

if (observability.hasCriticalIncidents) {
  renderCriticalIncidents(observability.criticalIncidents);
}

if (icebergPlan.hasCompactionCandidates || icebergPlan.hasOrphanSnapshots) {
  renderMaintenancePlan(icebergPlan.compactionCandidates, icebergPlan.orphanSnapshots);
}

await maintenanceControls.replayObjectTypeIndex.execute({ objectType: "Order" });
await maintenanceControls.replayFailedIndexRun.execute({ runId: indexRunId });
await maintenanceControls.retryTransformRun.execute({ runId: transformRunId });
```

The maintenance workbench covers current browser-safe Operations surfaces: observability detection, read-only
Iceberg maintenance planning, maintenance plan request evidence, bounded Iceberg maintenance run evidence,
object-type index replay, failed index-run replay, and failed transform retry. It does not start managed worker
daemons, run database migrations, bootstrap infrastructure from the browser, or claim full retention policy across
transform/materialization/backup pins.

### Admin outbox publish

```ts
const publishResult = await client.operations.outbox.publishPending({
  streamName: "foundry-lite-outbox",
  limit: 100,
});

if (publishResult.failed > 0) {
  showDeadLetterFollowUp(publishResult.deadLetterEventIds);
}
```

This is the current bounded admin-control starting point. It does not claim a long-running worker daemon console,
but it lets an operations screen run one outbox publish batch and then follow the durable event/DLQ evidence.

### Admin readiness screen

```tsx
import {
  useFoundryLiteAdminConsole,
  useFoundryLiteProvidedAdminCommandCenter,
  useFoundryLiteProvidedAdminInternalOperationsWorkbench,
  useFoundryLiteProvidedAdminLaunchpad,
  useFoundryLiteProvidedAdminLaunchModel,
  useFoundryLiteAdminOperationsBoard,
  useFoundryLiteAdminTaskPlan,
} from "@foundry-lite/sdk/react";

const admin = useFoundryLiteAdminConsole(client);
const taskPlan = useFoundryLiteAdminTaskPlan(client);
const adminBoard = useFoundryLiteAdminOperationsBoard(client);
const launchModel = useFoundryLiteProvidedAdminLaunchModel();
const launchpad = useFoundryLiteProvidedAdminLaunchpad();
const commandCenter = useFoundryLiteProvidedAdminCommandCenter({
  sectionId: "worker",
  query: operatorSearch,
  selectedCommandId,
  includeBlocked: true,
});
const internalWorkbench = useFoundryLiteProvidedAdminInternalOperationsWorkbench({
  selectedAreaId: "worker",
  query: operatorSearch,
  selectedCommandId,
  includeBlocked: true,
});

return (
  <OperationsAdminPanel
    browserActions={admin.browserActions}
    operatorCommandActions={admin.operatorCommandActions}
    futureActions={admin.futureActions}
    launchBrowserActions={launchModel.browserActions}
    operatorCommandCards={launchModel.operatorCommandCards}
    migrationCommandCards={launchModel.migrationCommandCards}
    workerCommandCards={launchModel.workerCommandCards}
    bootstrapCommandCards={launchModel.bootstrapCommandCards}
    migrationCommands={launchModel.migrationCommands}
    workerCommands={launchModel.workerCommands}
    bootstrapCommands={launchModel.bootstrapCommands}
    visibleCommandCards={commandCenter.visibleCards}
    internalVisibleCommandCards={internalWorkbench.visibleCommandCards}
    privilegedCommandCards={internalWorkbench.privilegedCommandCards}
    selectedCommandCard={commandCenter.selectedCard}
    selectedInternalArea={internalWorkbench.selectedAreaId}
    internalReadiness={internalWorkbench.readiness}
    commandSectionCounts={commandCenter.sectionCounts}
    launchpadSections={launchpad.visibleSections}
    recommendedSection={launchpad.recommendedSection}
    operatorRequiredTaskViews={taskPlan.operatorRequiredTaskViews}
    futureBackendSurfaceTaskViews={taskPlan.futureBackendSurfaceTaskViews}
    commandRows={adminBoard.commandRows}
    requiredBackendSurfaces={adminBoard.requiredBackendSurfaces}
    hasFutureWork={admin.hasFutureWork}
  />
);
```

This helper is deliberately screen-level, not a new backend source of truth. The server still owns
`client.operations.admin.overview()`, while React screens receive grouped state that prevents CLI-only migration
execution or runbook-only bootstrap from being rendered as normal browser buttons.
`foundryLiteAdminCapabilityView(...)` also exposes `canRenderActionButton`, `badge`, `primarySurface`, and
`disabledReason` so migration, worker daemon, and infra bootstrap cards can be shown honestly before browser-safe
backend controls exist. The same capability row now carries `executionSurface`, `canStartFromBrowser`,
`requiresApproval`, `operatorChecklist`, and `blockingReason`, so an admin screen can render preflight copy and
operator evidence requirements without guessing which backend operations are safe to start from a browser.
`useFoundryLiteAdminConsole(...)` adds a screen launch model on top: `browserActions` can become buttons,
`operatorCommandActions` show worker/CLI/runbook commands without pretending they run in the browser, and
`futureActions` keep missing privileged backend surfaces visible as product work.
`client.operations.admin.taskPlan()` and `useFoundryLiteAdminTaskPlan(...)` add a task-plan read model for
admin screens that want sections such as "browser runnable", "operator approval required", and "future backend
surface required" without deriving those groups from raw capability rows in every screen.
`adminOperationsBoard(...)` and `useFoundryLiteAdminOperationsBoard(...)` combine the read-only overview and task plan
into one board model with browser-runnable tasks, operator command rows, evidence rows, required backend surfaces,
migration, worker, bootstrap, and future sections. That lets a frontend admin console render migration/worker/bootstrap
work honestly without turning them into unsafe browser buttons.
For non-React SDK consumers, `createAdminOperationsRecipe(client).loadLaunchModel()` produces the same launch model
shape with `browserActions`, `operatorCommands`, `migrationCommands`, `workerCommands`, `bootstrapCommands`,
`futureSurfaceActions`, and `requiredBackendSurfaces`, so browser buttons, copyable operator commands, and future
backend work stay separate in custom admin consoles.
For React apps, `useFoundryLiteProvidedAdminLaunchModel(...)` returns that same split under `FoundryLiteProvider`,
so the app shell can keep auth/session wiring in one place while admin screens keep browser-safe controls separate
from migration, worker daemon, infra bootstrap, and future privileged backend surfaces.
`adminOperationsLaunchpad(...)`, `createAdminOperationsRecipe(client).loadLaunchpad()`, and
`useFoundryLiteProvidedAdminLaunchpad(...)` go one step further for real screen composition: they expose
`browserSection`, `migrationSection`, `workerSection`, `bootstrapSection`, `futureSection`, `visibleSections`,
`recommendedSection`, and per-item `riskLevel`/`riskLabel` values, so a frontend can render tabs, counters, warning
copy, and disabled states without re-classifying dangerous admin work locally.
`adminOperatorCommandCards(...)`, `createAdminOperationsRecipe(client).loadOperatorCommandCards()`, and the
`operatorCommandCards` fields on `useFoundryLiteProvidedAdminLaunchModel(...)` provide the command-card layer for
operator-only work. Each card carries `command`, `copyLabel`, `canCopyCommand`, `tone`, `evidencePath`, and
`checklist`, so migration, worker daemon, and bootstrap work can be rendered as copyable/operator-reviewed steps rather
than unsafe browser buttons.
`adminCommandCenter(...)`, `foundryLiteAdminCommandCenter(...)`, and
`useFoundryLiteProvidedAdminCommandCenter(...)` add the final screen-state layer for those command cards. A frontend can
filter by `sectionId`, search by `query`, preserve `selectedCard`, render `visibleCards`, show `sectionCounts` and
`visibleSectionCounts`, and separate copyable, approval, and blocked cards without reimplementing admin safety rules in
every screen. This still does not make migrations, worker daemons, or bootstrap browser-executable; it makes their
operator-command UX safer and easier to build.
`adminInternalOperationsWorkbench(...)`, `foundryLiteAdminInternalOperationsWorkbench(...)`, and
`useFoundryLiteProvidedAdminInternalOperationsWorkbench(...)` add a full internal-operations screen model on top of the
same launchpad and command center. It exposes `selectedAreaId`, `selectedSection`, `visibleCommandCards`,
`privilegedCommandCards`, and `readiness` counters so migration, worker, bootstrap, blocked, approval-required, and
future backend-surface work can be rendered consistently without pushing those safety decisions into every UI.

### Recovery and bounded operations controls

```tsx
import {
  useFoundryLiteBackupRestorePreflight,
  useFoundryLiteOutboxPublish,
  useFoundryLiteRecoveryOverview,
  useFoundryLiteRestoreModeControls,
} from "@foundry-lite/sdk/react";

const recovery = useFoundryLiteRecoveryOverview(client);
const preflight = useFoundryLiteBackupRestorePreflight(client);
const restoreMode = useFoundryLiteRestoreModeControls(client);
const outboxPublish = useFoundryLiteOutboxPublish(client);

if (recovery.phase === "restore_paused" && recovery.canApproveResume && recovery.activeRestoreId) {
  await restoreMode.postRestoreValidation.execute({ restoreId: recovery.activeRestoreId });
  await restoreMode.approveResume.execute({ restoreId: recovery.activeRestoreId });
}

const batch = await outboxPublish.execute({ streamName: "foundry-lite-outbox", limit: 100 });
if (batch?.deadLetterEventIds.length) {
  showDeadLetterFollowUp(batch.deadLetterEventIds);
}
```

These helpers only wrap current bounded backend controls: recovery overview, backup/restore preflight, restore-mode
validation/resume, and one outbox publish batch. They do not claim direct browser execution for migrations,
continuously running worker daemon management, or infra bootstrap.

### React screen helpers

```tsx
import {
  ApproveOrder,
  Order,
  createFoundryLiteOsdkClient,
  expectedObjectVersion,
  idempotencyKey,
} from "@foundry-lite/sdk";
import {
  FoundryLiteProvider,
  isFoundryLiteWorkflowTerminal,
  useFoundryLiteActionSubmit,
  useFoundryLiteProvidedAipAgentRunWithOperationsDetail,
  useFoundryLiteAdminConsole,
  useFoundryLiteAdminOverview,
  useFoundryLiteAdminLaunchModel,
  useFoundryLiteCursorPagination,
  useFoundryLiteOutboxPublish,
  useFoundryLiteRecoveryOverview,
  useFoundryLiteRestoreModeControls,
  useFoundryLiteMediaProcessing,
  useFoundryLiteMediaSearch,
  useFoundryLiteMediaUpload,
  useFoundryLiteProvidedMediaPipeline,
  useFoundryLiteLongRunningJob,
  useFoundryLiteIcebergMaintenancePlan,
  useFoundryLiteMaintenanceControls,
  useFoundryLiteObjectQuery,
  useFoundryLiteObservabilityDetect,
  useFoundryLiteOntologyCatalog,
  useFoundryLiteOntologyExplorer,
  useFoundryLiteOntologyWorkspaceShell,
  useFoundryLiteOperation,
  useFoundryLiteOperationsInvestigation,
  useFoundryLiteOperationsRunDetail,
  useFoundryLiteOperationsRunList,
  useFoundryLiteProvidedAdminLaunchModel,
  useFoundryLiteProvidedOperationEventStream,
  useFoundryLiteProvidedOntologyExplorer,
  useFoundryLiteProvidedOperatorWorkspaceHome,
  useFoundryLiteProvidedScreenRecipes,
  useFoundryLiteQuery,
  useFoundryLiteRecordDlqControls,
  useFoundryLiteRecordDlqQueue,
  useFoundryLiteSessionClient,
  useFoundryLiteClient,
  useFoundryLitePromptArtifact,
  useFoundryLiteSqlTransformSubmit,
  useFoundryLiteWritebackReconciliationQueue,
  useFoundryLiteWritebackResolve,
  useFoundryLiteWorkflowRun,
} from "@foundry-lite/sdk/react";

const client = useFoundryLiteClient();
const screenRecipes = useFoundryLiteProvidedScreenRecipes();
const operatorHome = useFoundryLiteProvidedOperatorWorkspaceHome({ runFilters: { limit: 25 } });
const osdk = createFoundryLiteOsdkClient(client);
const ontology = useFoundryLiteOntologyCatalog(client);
const ontologyShell = useFoundryLiteOntologyWorkspaceShell(client, {
  objectSearch: objectSearchText,
  selectedObjectApiName,
  selectedActionApiName,
});
const ontologyExplorer = useFoundryLiteProvidedOntologyExplorer({
  objectSearch: objectSearchText,
  selectedObjectApiName,
});
const adminLaunchModel = useFoundryLiteProvidedAdminLaunchModel();

const orders = useFoundryLiteObjectQuery(osdk, Order, {
  where: { property: "status", op: "eq", value: "PENDING" },
  pageSize: 50,
});

const approve = useFoundryLiteActionSubmit(osdk, ApproveOrder);
const mediaUpload = useFoundryLiteMediaUpload(client);
const mediaProcessing = useFoundryLiteMediaProcessing(client);
const mediaSearch = useFoundryLiteMediaSearch(client, { text: "invoice total", topK: 5 });
const aipAgent = useFoundryLiteProvidedAipAgentRunWithOperationsDetail();
const sqlTransform = useFoundryLiteSqlTransformSubmit(client);
const recovery = useFoundryLiteRecoveryOverview(client);
const restoreMode = useFoundryLiteRestoreModeControls(client);
const outboxPublish = useFoundryLiteOutboxPublish(client);
const operationsEvidence = useFoundryLiteOperationsInvestigation(client, {
  status: "failed",
  limit: 50,
  selectedRunType,
  selectedRunId,
});
const recordDlqQueue = useFoundryLiteRecordDlqQueue(client, {
  status: "QUARANTINED",
  selectedRecordId,
});
const recordDlqControls = useFoundryLiteRecordDlqControls(client);
const writebackQueue = useFoundryLiteWritebackReconciliationQueue(client, {
  status: "outcome_unknown",
  selectedWritebackId,
});
const writebackResolve = useFoundryLiteWritebackResolve(client);
const observabilityDetect = useFoundryLiteObservabilityDetect(client);
const icebergMaintenance = useFoundryLiteIcebergMaintenancePlan(client, {
  datasetRef: selectedDatasetRef,
});
const maintenanceControls = useFoundryLiteMaintenanceControls(client);
const runList = useFoundryLiteCursorPagination(
  ["operations", "runs"],
  (cursor) => client.operations.runs.list({ limit: 50, cursor }),
  {
    getItems: (page) => [
      ...page.syncRuns,
      ...page.transformRuns,
      ...page.actionRuns,
      ...page.materializationRuns,
      ...page.workflowRuns,
      ...page.aiRuns,
    ],
  },
);

async function approveOrder(order: Order) {
  await approve.execute({
    objectId: order.objectId,
    expectedObjectVersion: expectedObjectVersion(order),
    params: { reason: "screen approval" },
    idempotencyKey: idempotencyKey("ApproveOrder", order.objectId),
  });
}

const workflow = useFoundryLiteOperation(() => client.operations.workflows.get(workflowRunId), {
  intervalMs: 1000,
});

const workflowRun = useFoundryLiteWorkflowRun(client, workflowRunId, {
  autoStart: true,
  intervalMs: 1000,
});
const connectorJob = useFoundryLiteLongRunningJob(
  (payload: { datasetRef: string; connectorName: string; resourceName: string }) =>
    client.operations.workflows.startConnectorSync(payload, {
      idempotencyKey: `connector-sync-${payload.datasetRef}`,
    }),
  ({ startResult }) => client.operations.workflows.get(startResult.workflowRunId),
  { isTerminal: isFoundryLiteWorkflowTerminal, isSuccess: (snapshot) => snapshot.status === "succeeded" },
);

const admin = useFoundryLiteAdminOverview(client);
const apiCards = admin.browserRunnableCapabilityViews;
const cliOnlyRows = admin.capabilityViews.filter((capability) => capability.isCliOnly);
const adminConsole = useFoundryLiteAdminConsole(client);
const browserAdminActions = adminConsole.browserActions;
const operatorCommandRows = adminConsole.operatorCommandActions;
const catalogObjectCards = ontology.objectViews;
```

The React helpers are intentionally in the optional `@foundry-lite/sdk/react` entrypoint. Non-React consumers can keep
using the generated SDK and OSDK facade without pulling React into their bundle. `useFoundryLiteAdminOverview(...)`
keeps the admin console honest by separating API-backed controls from worker entrypoints, CLI-only operations,
runbook-only bootstrap work, and future workspace items before a screen renders action buttons.
`useFoundryLiteAdminConsole(...)` goes one step closer to screen implementation by returning launch actions split into
browser-safe actions, operator command/runbook rows, and future surfaces.
`useFoundryLiteObjectQuery(...)` and `useFoundryLiteActionSubmit(...)` cover the common object list + action button
screen shape while preserving the same generated OSDK types, cursor metadata, request id, retryability, and
idempotency-key fail-fast behavior.
`useFoundryLiteOntologyCatalog(...)` covers catalog-driven workspace shells by grouping live object/action/link
metadata, generated static type availability, and dynamic-only SDK-regeneration hints.
`useFoundryLiteOntologyWorkspaceShell(...)` and `useFoundryLiteProvidedOntologyWorkspaceShell(...)` add a higher-level
large-catalog shell: object cards, selected action palette items, generated/dynamic badges, disabled reasons, and
query/action-ready booleans come from one helper instead of per-screen catalog guessing.
`useFoundryLiteCursorPagination(...)` covers the common cursor-backed list screen shape by accumulating pages/items
and exposing `loadMore()`, `reload()`, `reset()`, `nextCursor`, request id, and retryability without raw API path
construction.
The media, AIP, Insight Review, and SQL transform helpers extend the same screen-level pattern to byte
upload/processing/search, agent execution evidence, review queue decisions, and pipeline registration/run flows
without claiming full visual workspace completion.
`useFoundryLiteLongRunningJob(...)` covers start-and-poll job screens by exposing `phase`, `status`, `runId`,
`startedAt`, `completedAt`, `requestId`, `retryable`, `start()`, `poll()`, and `reset()` while leaving terminal-state
rules server-contract-specific.
`useFoundryLiteOperationEventStream(...)` covers event-stream-backed operation screens by exposing `events`,
`latestEvent`, `isStreaming`, `requestId`, and `retryable` under the same provider session.
`useFoundryLiteLiveOperationTimeline(...)` and `useFoundryLiteProvidedLiveOperationTimeline(...)` combine those event
frames with bounded polling snapshots into a single `timelineItems` model for screens that show live operation progress,
without claiming a finished visual timeline component or a new server push route.
`useFoundryLiteOperationsRunList(...)`, `useFoundryLiteOperationsRunDetail(...)`,
`useFoundryLitePromptArtifact(...)`, and `useFoundryLiteOperationsInvestigation(...)` cover the run investigation
screen shape: run buckets become failed/running/pending/succeeded rows, a selected run opens detail evidence, and AI
prompt artifacts load only through the governed Operations SDK surface.
`useFoundryLiteRecordDlqQueue(...)`, `useFoundryLiteRecordDlqControls(...)`,
`useFoundryLiteWritebackReconciliationQueue(...)`, and `useFoundryLiteWritebackResolve(...)` cover browser-safe
operations workbench screens for record quarantine replay/discard and unresolved external writeback resolution while
keeping privileged migration/worker/bootstrap execution out of the browser surface.
`useFoundryLiteObservabilityDetect(...)`, `useFoundryLiteIcebergMaintenancePlan(...)`, and
`useFoundryLiteMaintenanceControls(...)` cover browser-safe observability and maintenance planning screens, while
`client.operations.icebergMaintenance.run(...)` covers the bounded maintenance execution API. Full retention policy
controls, migration execution, and daemon control remain outside the current browser surface.
`latestEvent`, `isStreaming`, `requestId`, `retryable`, `start()`, `stop()`, and `reset()` while leaving the backend
event route and visual timeline component product-specific. `useFoundryLiteProvidedOperationEventStream(...)` is the
Provider-backed shortcut for React screens that should inherit auth/session/request context instead of repeating it.
The admin/recovery helpers keep the first operations console safe: current API-backed controls can become buttons,
while worker, CLI-only, runbook-only, and future capabilities stay visible as launch actions without pretending they are
browser actions.

## Still Future

이 contract는 현재 백엔드에 존재하는 route와 current Web Operations controls를 잠그는 단계다.
아래는 아직 full product workspace를 위해 남아 있다.

| Future Surface | Why It Is Not Claimed Current |
|---|---|
| Full login/session UI | SDK `createSessionTokenProvider(...)` and React `useFoundryLiteSessionClient(...)` are current; complete login/session screens and refresh-token lifecycle UI remain product work. |
| Automatic retry/backoff UX | SDK `retryWithBackoff(...)` and React query helpers are current; screen-specific copy and UX timing remain product work. |
| Cursor pagination UX | SDK `collectCursorPages(...)` and React `useFoundryLiteCursorPagination(...)` are current state helpers; visual pagination/infinite-scroll components and product copy remain product work. |
| Push streaming UX | SDK `streamFoundryLiteOperationEvents(...)` and React `useFoundryLiteOperationEventStream(...)` are current fetch-based SSE consumers; server push route implementation and visual streaming timeline components remain future. |
| Duplicate-click action UX | SDK `createInFlightActionLock()` and `actionLockKey(...)` are current; button disabled state and screen copy remain product work. |
| Stale-version conflict UI | SDK `classifyFoundryLiteError(...)` can identify `stale_object_version`; the human-facing compare/refresh flow remains product work. |
| Permission-denied masking UX | SDK `classifyFoundryLiteError(...)` can identify `permission_denied`; dedicated masked-field/role guidance UX remains product work. |
| Full catalog-driven workspace UX | `ontology.catalog()` and dataset list/inspect give the frontend active metadata entrypoints, but S62-S64 screens still need richer drill-down flows. |
| Insight review workspace UI | `insight_reviews` persistence, `/api/insights/reviews`, generated `client.insights.reviews.*`, idempotent create/assign/decision, terminal decision conflict, and audit evidence are current. Evidence viewer UI, action execution orchestration, approval policy UI, and rich review workspace screens remain product work. |
| Browser admin for migration/worker/bootstrap | `client.operations.admin.overview()` now names which admin capabilities are API-backed, worker-backed, CLI-only, runbook-only, or future and includes execution surface, approval, checklist, browser-start, and blocking reason fields. `client.operations.admin.taskPlan()` turns those capabilities into screen-ready admin tasks with `requiredBackendSurface` and operator evidence fields. `useFoundryLiteAdminConsole(...)` and `useFoundryLiteAdminTaskPlan(...)` split that backend truth into browser actions, operator command/runbook rows, and future rows. Bounded `client.operations.outbox.publishPending(...)`, backup/restore, workflow, maintenance, and recovery surfaces can power an admin console start point; direct migration execution, long-running worker daemon control, and infra bootstrap from the browser still need separate privileged backend surfaces and are not claimed current. |

## Completion Meaning

이 문서의 현재 의미는 "프론트 전체가 완성됐다"가 아니다. 현재 의미는 더 좁고 강하다:

```text
현재 존재하는 frontend-consumable backend API
-> generated SDK named method
-> browser SDK request-contract method/path/header/body proof for 216 frontend route surfaces
-> browser SDK helper-contract proof for 25 frontend foundation helpers
-> SDK TypeScript typecheck for package entrypoints, generated types, optional React helpers, and screen recipes
-> `@foundry-lite/sdk/screen-recipes` importable recipe builders for core product screens
-> documentation count claims checked against the matrix and generated SDK helper list
-> Web named-SDK-only usage
-> proof test
-> CI gate
-> operator evidence 설명
```

이 사슬이 끊기면 PR은 실패해야 한다. 그래서 다음 프론트 작업은 raw API path를 새로 invent하지
않고, matrix와 generated SDK를 먼저 확장하는 방식으로 진행한다.
