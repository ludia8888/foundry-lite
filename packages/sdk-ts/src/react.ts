import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactElement, ReactNode } from "react";

import {
  actionLockKey,
  adminCapabilityView,
  adminOperationsBoard,
  adminReadinessScreen,
  adminTaskPlanScreen,
  createFoundryLiteClient,
  createFoundryLiteOsdkClient,
  createInFlightActionLock,
  createFoundryLiteOntologyIndex,
  createSessionTokenProvider,
  FoundryLiteApiError,
  getActionType,
  getObjectType,
  groupAdminCapabilities,
  idempotencyKey as foundryLiteGeneratedIdempotencyKey,
  normalizeFoundryLiteError,
  pollFoundryLiteOperation,
  retryWithBackoff,
  streamFoundryLiteOperationEvents,
} from "./generated";
import {
  emptyOntologyDraft,
  matchColumnsToProperties,
  ontologyDatasetColumnsFromSchema,
  ontologyDraftFromCatalog,
  ontologyDraftToYamlText,
  removeOntologyDraftActionType,
  removeOntologyDraftFunctionType,
  removeOntologyDraftInterfaceType,
  removeOntologyDraftLinkType,
  removeOntologyDraftObjectType,
  removeOntologyDraftProperty,
  setOntologyDraftObjectTypeImplements,
  updateOntologyDraftObjectType,
  updateOntologyDraftProperty,
  upsertOntologyDraftActionType,
  upsertOntologyDraftFunctionType,
  upsertOntologyDraftInterfaceType,
  upsertOntologyDraftLinkType,
  upsertOntologyDraftObjectType,
  upsertOntologyDraftProperty,
} from "./ontology-draft";
import type {
  OntologyColumnPropertyMatch,
  OntologyDatasetColumn,
  OntologyDraft,
  OntologyDraftActionType,
  OntologyDraftFunctionType,
  OntologyDraftInterfaceType,
  OntologyDraftLinkType,
  OntologyDraftObjectType,
  OntologyDraftProperty,
} from "./ontology-draft";
import {
  adminCommandCenter,
  adminInternalOperationsWorkbench,
  adminOperationsLaunchModel,
  adminOperationsLaunchpad,
  createConnectorOnboardingRecipe,
  createFoundryLiteScreenRecipes,
  createOntologyBuilderRecipe,
  createOperatorWorkspaceRecipe,
  createSourceOnboardingRecipe,
  createSourceWizardRecipe,
  operatorWorkspaceNavigation,
  operatorWorkspaceShell,
} from "./screen-recipes";
import type {
  AdminCapabilityActionKind,
  AdminCapabilityGroups,
  AdminCapabilityView,
  AdminOperationsBoard,
  AdminOperationCapability,
  AdminReadinessScreen,
  AdminReadinessOverview,
  AdminTaskPlan,
  AdminTaskView,
  AipAgentRunRequest,
  AipAgentRunResult,
  AipBuilderRunRequest,
  AipBuilderRunResult,
  ActionWritebackQueueFilters,
  ActionWritebackQueueItem,
  ActionWritebackQueueResult,
  ActionWritebackReconciliationRequest,
  ActionWritebackReconciliationResult,
  BackupRestoreModeReport,
  BackupRestoreModeStartRequest,
  BackupRestorePostRestoreValidationReport,
  BackupRestorePostRestoreValidationRequest,
  BackupRestorePreflightReport,
  BackupRestorePreflightRequest,
  BackupRestoreRecoveryOverview,
  BackupRestoreResumeApprovalRequest,
  Dataset,
  DatasetAggregateRequest,
  DatasetAggregationGroup,
  DatasetAggregationResult,
  DatasetInspectionPayload,
  DatasetQualityResultSummary,
  DatasetVersion,
  DeadLetterRecord,
  DeadLetterRecordBulkRetryResult,
  DeadLetterRecordDiscardResult,
  DeadLetterRecordRetryResult,
  DeadLetterRecordStatus,
  FoundryLiteClientOptions,
  FoundryLiteGeneratedClient,
  FoundryLiteOntologyIndex,
  FoundryLiteOperationStreamEvent,
  FoundryLiteObject,
  FoundryLiteOsdkClient,
  FoundryLiteResponseMetadata,
  FoundryLiteSessionProvider,
  GenericObject,
  InFlightActionLock,
  InsightActionProposal,
  InsightReviewAssignRequest,
  InsightReviewCreateRequest,
  InsightReviewDecisionRequest,
  InsightReviewListFilters,
  InsightReviewPayload,
  InsightReviewQueryResult,
  IcebergMaintenancePlan,
  IcebergMaintenancePlanOptions,
  MediaCommitResult,
  MediaContentSearchHit,
  MediaIndexingOutcome,
  MediaOpenTransactionResult,
  MediaProcessRequest,
  MediaProcessingOutcome,
  MediaSearchRequest,
  MediaStagedUpload,
  MediaUploadRequest,
  ActionBatchApplyResponse,
  FunctionExecutionResult,
  InterfaceQueryRequest,
  MaterializationSpecList,
  ObjectAggregateRequest,
  ObjectAggregationGroup,
  ObjectAggregationResult,
  OntologyBranchCreateRequest,
  OntologyBranchDetailPayload,
  OntologyBranchDiffResource,
  OntologyBranchDiffResult,
  OntologyBranchListResult,
  OntologyBranchPayload,
  OntologyBranchProposeRequest,
  OntologyBranchRebaseRequest,
  OntologyBranchUpdateRequest,
  OntologyCatalog,
  OntologyCatalogAction,
  OntologyCatalogLink,
  OntologyCatalogObject,
  OntologyCatalogProperty,
  OntologyProposalListResult,
  OntologyProposalPayload,
  OntologyProposalSubmitRequest,
  OntologyProposalUpdateRequest,
  OntologyResourceDependentsResult,
  OntologyResourceUsageResult,
  OntologyValidationResult,
  ObjectIndexRebuildResult,
  ObjectFilter,
  ObjectQueryRequest,
  ObjectQueryResult,
  ObservabilityDetectRequest,
  ObservabilityIncident,
  ObservabilityReport,
  ObservabilitySeverity,
  OsdkActionPayload,
  OsdkActionResult,
  OsdkActionType,
  OsdkFetchPageOptions,
  OsdkInstance,
  OsdkObjectType,
  OsdkPage,
  OutboxPublishBatchResult,
  OutboxPublishRequest,
  PipelineBranch,
  PipelineListResult,
  PipelinePreviewNodeRequest,
  PipelineProposal,
  PollFoundryLiteOperationOptions,
  PromptArtifactReadResult,
  ProductWorkflowRun,
  ProductWorkflowStatus,
  RetryWithBackoffOptions,
  RuntimeRow,
  RuntimeRunDetail,
  RuntimeRunQueryFilters,
  RuntimeRunQueryResult,
  RuntimeRunType,
  StreamFoundryLiteOperationEventsOptions,
  LineageEdge,
  TabularRow,
  TransformRow,
  TransformRetryResult,
  TransformRunResult,
  TransformSqlRegisterRequest,
} from "./generated";
import type {
  AdminCommandCenterSelection,
  AdminCommandCenterView,
  AdminInternalOperationsWorkbenchSelection,
  AdminInternalOperationsWorkbenchView,
  AdminOperationsLaunchModel,
  AdminOperationsLaunchpadModel,
  ConnectorOnboardingInput,
  ConnectorOnboardingRecipeState,
  ConnectorOnboardingRunOptions,
  FoundryLiteScreenRecipes,
  OntologyBuilderRecipeState,
  OntologyBuilderRunInput,
  OntologyBuilderRunOptions,
  OperatorWorkspaceAreaId,
  OperatorWorkspaceHomeOptions,
  OperatorWorkspaceHomeView,
  OperatorWorkspaceNavigationView,
  OperatorWorkspaceShellView,
  SourceOnboardingInput,
  SourceOnboardingRecipeState,
  SourceOnboardingRunOptions,
  SourceWizardInput,
  SourceWizardRecipeState,
  SourceWizardRunOptions,
} from "./screen-recipes";

export type FoundryLiteQueryState<TData> = {
  data: TData | null;
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  requestId: string | null;
  retryable: boolean;
  reload(): Promise<TData | null>;
};

export type FoundryLiteQueryOptions<TData> = {
  enabled?: boolean;
  initialData?: TData | null;
  retry?: RetryWithBackoffOptions;
  onSuccess?: (data: TData) => void;
  onError?: (error: FoundryLiteApiError) => void;
};

export type FoundryLiteSessionClientOptions = Omit<
  FoundryLiteClientOptions,
  "token" | "tokenProvider" | "onResponse"
> & {
  sessionProvider: FoundryLiteSessionProvider;
  onResponse?: (metadata: FoundryLiteResponseMetadata) => void;
};

export type FoundryLiteProviderOperationStreamOptions = Omit<
  StreamFoundryLiteOperationEventsOptions,
  "onEvent" | "parseEvent" | "signal"
>;

export type FoundryLiteSessionClientState = {
  client: FoundryLiteGeneratedClient;
  operationStreamOptions: FoundryLiteProviderOperationStreamOptions;
  lastResponse: FoundryLiteResponseMetadata | null;
  lastRequestId: string | null;
  lastErrorCode: string | null;
  isLastResponseRetryable: boolean;
  clearLastResponse(): void;
  getLastResponse(): FoundryLiteResponseMetadata | null;
};

export type FoundryLiteSessionStatusKind =
  | "idle"
  | "ok"
  | "authentication_required"
  | "permission_denied"
  | "retryable_error"
  | "request_failed";

export type FoundryLiteSessionStatusTone = "neutral" | "success" | "warning" | "danger";

export type FoundryLiteSessionStatusView = {
  kind: FoundryLiteSessionStatusKind;
  tone: FoundryLiteSessionStatusTone;
  lastResponse: FoundryLiteResponseMetadata | null;
  lastRequestId: string | null;
  lastErrorCode: string | null;
  lastStatus: number | null;
  hasResponseEvidence: boolean;
  isLastResponseOk: boolean;
  needsAuthentication: boolean;
  isPermissionDenied: boolean;
  canRetryLastRequest: boolean;
  title: string;
  description: string;
};

export type FoundryLiteScreenStatusKind =
  | "idle"
  | "loading"
  | "refreshing"
  | "running"
  | "ready"
  | "authentication_required"
  | "permission_denied"
  | "retryable_error"
  | "error";

export type FoundryLiteScreenStatusTone = FoundryLiteSessionStatusTone;

export type FoundryLiteScreenStatusInput = {
  sessionStatus?: FoundryLiteSessionStatusView | null;
  error?: FoundryLiteApiError | null;
  isLoading?: boolean;
  isRefreshing?: boolean;
  isRunning?: boolean;
  requestId?: string | null;
  retryable?: boolean;
  hasData?: boolean;
};

export type FoundryLiteScreenStatusView = {
  kind: FoundryLiteScreenStatusKind;
  tone: FoundryLiteScreenStatusTone;
  title: string;
  description: string;
  requestId: string | null;
  errorCode: string | null;
  error: FoundryLiteApiError | null;
  hasData: boolean;
  isBusy: boolean;
  isBlocking: boolean;
  shouldShowSkeleton: boolean;
  shouldShowInlineRefresh: boolean;
  shouldRenderContent: boolean;
  needsAuthentication: boolean;
  isPermissionDenied: boolean;
  canRetry: boolean;
};

export type FoundryLiteProviderProps = FoundryLiteSessionClientOptions & {
  children?: ReactNode;
};

export type FoundryLiteClientProviderProps = {
  value: FoundryLiteSessionClientState;
  children?: ReactNode;
};

export function useFoundryLiteSessionClient(
  options: FoundryLiteSessionClientOptions,
): FoundryLiteSessionClientState {
  const [lastResponse, setLastResponse] = useState<FoundryLiteResponseMetadata | null>(null);
  const lastResponseRef = useRef<FoundryLiteResponseMetadata | null>(null);
  const tokenProvider = useMemo(
    () => createSessionTokenProvider(options.sessionProvider),
    [options.sessionProvider],
  );
  const onResponse = useCallback(
    (metadata: FoundryLiteResponseMetadata) => {
      lastResponseRef.current = metadata;
      setLastResponse(metadata);
      options.onResponse?.(metadata);
    },
    [options.onResponse],
  );
  const client = useMemo(
    () =>
      createFoundryLiteClient({
        baseUrl: options.baseUrl,
        context: options.context,
        fetchImpl: options.fetchImpl,
        headers: options.headers,
        onResponse,
        requestIdFactory: options.requestIdFactory,
        tokenProvider,
      }),
    [
      options.baseUrl,
      options.context,
      options.fetchImpl,
      options.headers,
      options.requestIdFactory,
      onResponse,
      tokenProvider,
    ],
  );
  const operationStreamOptions = useMemo(
    () => ({
      baseUrl: options.baseUrl,
      context: options.context,
      fetchImpl: options.fetchImpl,
      headers: options.headers,
      onOpen: onResponse,
      requestIdFactory: options.requestIdFactory,
      tokenProvider,
    }),
    [
      options.baseUrl,
      options.context,
      options.fetchImpl,
      options.headers,
      options.requestIdFactory,
      onResponse,
      tokenProvider,
    ],
  );
  const clearLastResponse = useCallback(() => {
    lastResponseRef.current = null;
    setLastResponse(null);
  }, []);
  const getLastResponse = useCallback(() => lastResponseRef.current, []);

  return {
    client,
    operationStreamOptions,
    lastResponse,
    lastRequestId: lastResponse?.requestId ?? null,
    lastErrorCode: lastResponse?.errorCode ?? null,
    isLastResponseRetryable: lastResponse?.retryable ?? false,
    clearLastResponse,
    getLastResponse,
  };
}

const FoundryLiteContext = createContext<FoundryLiteSessionClientState | null>(null);

export function FoundryLiteProvider({
  children,
  ...options
}: FoundryLiteProviderProps): ReactElement {
  const value = useFoundryLiteSessionClient(options);
  return createElement(FoundryLiteContext.Provider, { value }, children);
}

export function FoundryLiteClientProvider({
  value,
  children,
}: FoundryLiteClientProviderProps): ReactElement {
  return createElement(FoundryLiteContext.Provider, { value }, children);
}

export function useFoundryLiteSession(): FoundryLiteSessionClientState {
  const state = useContext(FoundryLiteContext);
  if (state === null) {
    throw new Error("useFoundryLiteSession must be used under FoundryLiteProvider.");
  }
  return state;
}

export function useFoundryLiteClient(): FoundryLiteGeneratedClient {
  return useFoundryLiteSession().client;
}

export function useFoundryLiteOsdkClient(): FoundryLiteOsdkClient {
  const client = useFoundryLiteClient();
  return useMemo(() => createFoundryLiteOsdkClient(client), [client]);
}

export function foundryLiteSessionStatus(
  session: Pick<
    FoundryLiteSessionClientState,
    "lastResponse" | "lastRequestId" | "lastErrorCode" | "isLastResponseRetryable"
  >,
): FoundryLiteSessionStatusView {
  const response = session.lastResponse;
  const status = response?.status ?? null;
  const code = session.lastErrorCode ?? response?.errorCode ?? null;
  const kind = foundryLiteSessionStatusKind(response, code, session.isLastResponseRetryable);
  return {
    kind,
    tone: foundryLiteSessionStatusTone(kind),
    lastResponse: response,
    lastRequestId: session.lastRequestId,
    lastErrorCode: code,
    lastStatus: status,
    hasResponseEvidence: response !== null,
    isLastResponseOk: response?.ok ?? false,
    needsAuthentication: kind === "authentication_required",
    isPermissionDenied: kind === "permission_denied",
    canRetryLastRequest: kind === "retryable_error",
    title: foundryLiteSessionStatusTitle(kind),
    description: foundryLiteSessionStatusDescription(kind),
  };
}

export function useFoundryLiteSessionStatus(): FoundryLiteSessionStatusView {
  const session = useFoundryLiteSession();
  return useMemo(
    () => foundryLiteSessionStatus(session),
    [
      session.lastResponse,
      session.lastRequestId,
      session.lastErrorCode,
      session.isLastResponseRetryable,
    ],
  );
}

export function foundryLiteScreenStatus(
  input: FoundryLiteScreenStatusInput = {},
): FoundryLiteScreenStatusView {
  const error = input.error ?? null;
  const sessionStatus = input.sessionStatus ?? null;
  const hasData = input.hasData ?? false;
  const isLoading = input.isLoading ?? false;
  const isRefreshing = input.isRefreshing ?? false;
  const isRunning = input.isRunning ?? false;
  const retryable = input.retryable ?? error?.retryable ?? sessionStatus?.canRetryLastRequest ?? false;
  const requestId = input.requestId ?? error?.requestId ?? sessionStatus?.lastRequestId ?? null;
  const errorCode = error?.code ?? sessionStatus?.lastErrorCode ?? null;
  const kind = foundryLiteScreenStatusKind({
    error,
    hasData,
    isLoading,
    isRefreshing,
    isRunning,
    retryable,
    sessionStatus,
  });
  return {
    kind,
    tone: foundryLiteScreenStatusTone(kind),
    title: foundryLiteScreenStatusTitle(kind),
    description: foundryLiteScreenStatusDescription(kind),
    requestId,
    errorCode,
    error,
    hasData,
    isBusy: isLoading || isRefreshing || isRunning,
    isBlocking: foundryLiteScreenStatusIsBlocking(kind),
    shouldShowSkeleton: isLoading && !hasData,
    shouldShowInlineRefresh: isRefreshing && hasData,
    shouldRenderContent:
      hasData &&
      kind !== "authentication_required" &&
      kind !== "permission_denied" &&
      kind !== "error",
    needsAuthentication: kind === "authentication_required",
    isPermissionDenied: kind === "permission_denied",
    canRetry: retryable && (error !== null || kind === "retryable_error"),
  };
}

export function useFoundryLiteScreenStatus(
  input: FoundryLiteScreenStatusInput = {},
): FoundryLiteScreenStatusView {
  const sessionStatus = useFoundryLiteSessionStatus();
  const {
    error = null,
    hasData = false,
    isLoading = false,
    isRefreshing = false,
    isRunning = false,
    requestId = null,
    retryable = false,
  } = input;
  return useMemo(
    () =>
      foundryLiteScreenStatus({
        error,
        hasData,
        isLoading,
        isRefreshing,
        isRunning,
        requestId,
        retryable,
        sessionStatus,
      }),
    [
      error,
      hasData,
      isLoading,
      isRefreshing,
      isRunning,
      requestId,
      retryable,
      sessionStatus,
    ],
  );
}

function foundryLiteSessionStatusKind(
  response: FoundryLiteResponseMetadata | null,
  errorCode: string | null,
  isRetryable: boolean,
): FoundryLiteSessionStatusKind {
  if (response === null) return "idle";
  if (response.ok) return "ok";
  if (response.status === 401 || errorCode === "UNAUTHENTICATED") {
    return "authentication_required";
  }
  if (response.status === 403 || errorCode === "PERMISSION_DENIED") return "permission_denied";
  if (isRetryable) return "retryable_error";
  return "request_failed";
}

function foundryLiteSessionStatusTone(
  kind: FoundryLiteSessionStatusKind,
): FoundryLiteSessionStatusTone {
  switch (kind) {
    case "idle":
      return "neutral";
    case "ok":
      return "success";
    case "authentication_required":
    case "retryable_error":
      return "warning";
    case "permission_denied":
    case "request_failed":
      return "danger";
  }
}

function foundryLiteSessionStatusTitle(kind: FoundryLiteSessionStatusKind): string {
  switch (kind) {
    case "idle":
      return "No request yet";
    case "ok":
      return "Connected";
    case "authentication_required":
      return "Sign in required";
    case "permission_denied":
      return "Permission denied";
    case "retryable_error":
      return "Temporary issue";
    case "request_failed":
      return "Request failed";
  }
}

function foundryLiteSessionStatusDescription(kind: FoundryLiteSessionStatusKind): string {
  switch (kind) {
    case "idle":
      return "The SDK has not received a response yet.";
    case "ok":
      return "The latest SDK request completed successfully.";
    case "authentication_required":
      return "The latest SDK request needs a valid user session.";
    case "permission_denied":
      return "The current session is not allowed to access this operation.";
    case "retryable_error":
      return "The latest SDK request can be retried with the same request context.";
    case "request_failed":
      return "The latest SDK request failed and needs user or operator attention.";
  }
}

function foundryLiteScreenStatusKind(
  input: Required<
    Pick<FoundryLiteScreenStatusInput, "hasData" | "isLoading" | "isRefreshing" | "isRunning" | "retryable">
  > & {
    error: FoundryLiteApiError | null;
    sessionStatus: FoundryLiteSessionStatusView | null;
  },
): FoundryLiteScreenStatusKind {
  if (input.sessionStatus?.needsAuthentication) return "authentication_required";
  if (input.sessionStatus?.isPermissionDenied) return "permission_denied";
  if (input.error) return input.retryable ? "retryable_error" : "error";
  if (input.isLoading && !input.hasData) return "loading";
  if (input.isRunning) return "running";
  if (input.isRefreshing) return "refreshing";
  if (input.hasData || input.sessionStatus?.kind === "ok") return "ready";
  return "idle";
}

function foundryLiteScreenStatusTone(
  kind: FoundryLiteScreenStatusKind,
): FoundryLiteScreenStatusTone {
  switch (kind) {
    case "ready":
      return "success";
    case "loading":
    case "refreshing":
    case "running":
    case "idle":
      return "neutral";
    case "authentication_required":
    case "retryable_error":
      return "warning";
    case "permission_denied":
    case "error":
      return "danger";
  }
}

function foundryLiteScreenStatusTitle(kind: FoundryLiteScreenStatusKind): string {
  switch (kind) {
    case "idle":
      return "Ready to load";
    case "loading":
      return "Loading";
    case "refreshing":
      return "Refreshing";
    case "running":
      return "Running";
    case "ready":
      return "Ready";
    case "authentication_required":
      return "Sign in required";
    case "permission_denied":
      return "Permission denied";
    case "retryable_error":
      return "Temporary issue";
    case "error":
      return "Request failed";
  }
}

function foundryLiteScreenStatusDescription(kind: FoundryLiteScreenStatusKind): string {
  switch (kind) {
    case "idle":
      return "This screen has not loaded data yet.";
    case "loading":
      return "The screen is loading its first data snapshot.";
    case "refreshing":
      return "The screen is refreshing while keeping current content visible.";
    case "running":
      return "A user-started operation is still running.";
    case "ready":
      return "The screen has enough SDK data to render.";
    case "authentication_required":
      return "A valid user session is required before this screen can continue.";
    case "permission_denied":
      return "The current session cannot access this screen or operation.";
    case "retryable_error":
      return "The request failed with a retryable error.";
    case "error":
      return "The request failed and needs user or operator attention.";
  }
}

function foundryLiteScreenStatusIsBlocking(kind: FoundryLiteScreenStatusKind): boolean {
  switch (kind) {
    case "loading":
    case "authentication_required":
    case "permission_denied":
    case "error":
      return true;
    case "idle":
    case "refreshing":
    case "running":
    case "ready":
    case "retryable_error":
      return false;
  }
}

export function useFoundryLiteQuery<TData>(
  key: readonly unknown[],
  load: () => Promise<TData>,
  options: FoundryLiteQueryOptions<TData> = {},
): FoundryLiteQueryState<TData> {
  const { enabled = true, initialData = null } = options;
  const [data, setData] = useState<TData | null>(initialData);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(enabled && data === null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestKey = useMemo(() => JSON.stringify(key), [key]);
  const generationRef = useRef(0);
  const dataRef = useRef<TData | null>(initialData);
  const loadRef = useRef(load);
  const optionsRef = useRef(options);
  loadRef.current = load;
  optionsRef.current = options;

  useEffect(() => {
    return () => {
      generationRef.current += 1;
    };
  }, []);

  const reload = useCallback(async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    if (dataRef.current === null) setIsLoading(true);
    else setIsRefreshing(true);
    try {
      const nextData = await retryWithBackoff(() => loadRef.current(), optionsRef.current.retry);
      if (generationRef.current !== generation) return nextData;
      dataRef.current = nextData;
      setData(nextData);
      setError(null);
      optionsRef.current.onSuccess?.(nextData);
      return nextData;
    } catch (caught) {
      const normalized = normalizeFoundryLiteError(caught);
      if (generationRef.current === generation) {
        setError(normalized);
        optionsRef.current.onError?.(normalized);
      }
      return null;
    } finally {
      if (generationRef.current === generation) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void reload();
  }, [enabled, reload, requestKey]);

  return {
    data,
    error,
    isLoading,
    isRefreshing,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    reload,
  };
}

export type FoundryLiteDatasetExplorerSelection = {
  namespace?: string | null;
  name?: string | null;
  version?: string | null;
  previewLimit?: number;
  lineageResourceId?: string | null;
};

export type FoundryLiteDatasetExplorerData = {
  datasets: Dataset[];
  versions: DatasetVersion[];
  inspection: DatasetInspectionPayload | null;
  previewRows: TabularRow[];
  qualitySummary: DatasetQualityResultSummary | null;
  lineage: LineageEdge[];
};

export type FoundryLiteDatasetExplorerView = FoundryLiteDatasetExplorerData & {
  selectedDatasetRef: string | null;
  selectedDataset: Dataset | null;
  latestVersion: DatasetVersion | null;
  selectedVersion: DatasetVersion | null;
  inspectedVersion: DatasetVersion | null;
  schema: Record<string, unknown> | null;
  manifest: DatasetInspectionPayload["manifest"] | null;
  manifestFiles: DatasetInspectionPayload["manifest"]["files"];
  rowCount: number | null;
  byteSize: number | null;
  hasDatasetSelection: boolean;
  hasVersions: boolean;
  hasInspection: boolean;
  hasPreviewRows: boolean;
  hasLineage: boolean;
  hasQualitySummary: boolean;
};

export type FoundryLiteDatasetExplorerOptions =
  FoundryLiteQueryOptions<FoundryLiteDatasetExplorerData> &
    FoundryLiteDatasetExplorerSelection & {
      key?: readonly unknown[];
    };

export type FoundryLiteDatasetExplorerState =
  FoundryLiteQueryState<FoundryLiteDatasetExplorerData> &
    FoundryLiteDatasetExplorerView;

const EMPTY_DATASET_EXPLORER_DATA: FoundryLiteDatasetExplorerData = {
  datasets: [],
  versions: [],
  inspection: null,
  previewRows: [],
  qualitySummary: null,
  lineage: [],
};

export function foundryLiteDatasetExplorerResourceId(
  selection: FoundryLiteDatasetExplorerSelection,
): string | null {
  if (!selection.namespace || !selection.name) return null;
  const datasetRef = `${selection.namespace}.${selection.name}`;
  if (selection.lineageResourceId) return selection.lineageResourceId;
  return selection.version ? `${datasetRef}@${selection.version}` : datasetRef;
}

export function foundryLiteDatasetExplorerView(
  data: FoundryLiteDatasetExplorerData | null | undefined,
  selection: FoundryLiteDatasetExplorerSelection = {},
): FoundryLiteDatasetExplorerView {
  const screenData = data ?? EMPTY_DATASET_EXPLORER_DATA;
  const selectedDatasetRef =
    selection.namespace && selection.name ? `${selection.namespace}.${selection.name}` : null;
  const selectedDataset = selectedDatasetRef
    ? screenData.datasets.find(
        (dataset) => dataset.namespace === selection.namespace && dataset.name === selection.name,
      ) ?? null
    : null;
  const latestVersion = screenData.versions[0] ?? null;
  const selectedVersion = readFoundryLiteSelectedDatasetVersion(screenData.versions, selection.version);
  const inspectedVersion = screenData.inspection?.version ?? selectedVersion ?? latestVersion;
  const manifest = screenData.inspection?.manifest ?? null;

  return {
    ...screenData,
    selectedDatasetRef,
    selectedDataset,
    latestVersion,
    selectedVersion,
    inspectedVersion,
    schema: screenData.inspection?.schema ?? null,
    manifest,
    manifestFiles: manifest?.files ?? [],
    rowCount: inspectedVersion?.row_count ?? null,
    byteSize: inspectedVersion?.byte_size ?? null,
    hasDatasetSelection: selectedDatasetRef !== null,
    hasVersions: screenData.versions.length > 0,
    hasInspection: screenData.inspection !== null,
    hasPreviewRows: screenData.previewRows.length > 0,
    hasLineage: screenData.lineage.length > 0,
    hasQualitySummary: screenData.qualitySummary !== null,
  };
}

export function useFoundryLiteDatasetExplorer(
  client: Pick<FoundryLiteGeneratedClient, "datasets" | "operations">,
  options: FoundryLiteDatasetExplorerOptions = {},
): FoundryLiteDatasetExplorerState {
  const { key, lineageResourceId, name, namespace, previewLimit, version, ...queryOptions } = options;
  const selectionKey = useMemo(
    () => stableDatasetExplorerSelectionKey({ lineageResourceId, name, namespace, previewLimit, version }),
    [lineageResourceId, name, namespace, previewLimit, version],
  );
  const selection = useMemo<FoundryLiteDatasetExplorerSelection>(
    () => ({ lineageResourceId, name, namespace, previewLimit, version }),
    [selectionKey],
  );
  const queryKey = useMemo(() => key ?? ["datasets", "explorer", selection], [key, selection]);
  const load = useCallback(() => loadFoundryLiteDatasetExplorerData(client, selection), [client, selection]);
  const query = useFoundryLiteQuery(queryKey, load, queryOptions);
  const view = useMemo(() => foundryLiteDatasetExplorerView(query.data, selection), [query.data, selection]);
  return { ...query, ...view };
}

export function useFoundryLiteProvidedDatasetExplorer(
  options: FoundryLiteDatasetExplorerOptions = {},
): FoundryLiteDatasetExplorerState {
  const client = useFoundryLiteClient();
  return useFoundryLiteDatasetExplorer(client, options);
}

const EMPTY_FOUNDRY_LITE_CURSOR_ITEMS: never[] = [];

export type FoundryLiteCursorPage<TItem> = {
  items?: TItem[];
  data?: TItem[];
  nextCursor?: string | null;
  nextPageToken?: string | null;
};

export type FoundryLiteCursorPageLoader<TPage> = (cursor: string | null) => Promise<TPage>;

export type FoundryLiteCursorPaginationOptions<TPage, TItem> = {
  enabled?: boolean;
  initialCursor?: string | null;
  initialItems?: TItem[];
  retry?: RetryWithBackoffOptions;
  getItems?: (page: TPage) => TItem[];
  getNextCursor?: (page: TPage) => string | null | undefined;
  onPage?: (metadata: {
    page: TPage;
    items: TItem[];
    cursor: string | null;
    nextCursor: string | null;
  }) => void;
  onError?: (error: FoundryLiteApiError) => void;
};

export type FoundryLiteCursorPaginationState<TPage, TItem> = {
  pages: TPage[];
  items: TItem[];
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  isLoadingMore: boolean;
  requestId: string | null;
  retryable: boolean;
  nextCursor: string | null;
  hasNextPage: boolean;
  reset(): void;
  reload(): Promise<TPage | null>;
  loadMore(): Promise<TPage | null>;
};

export function useFoundryLiteCursorPagination<TPage, TItem = unknown>(
  key: readonly unknown[],
  loadPage: FoundryLiteCursorPageLoader<TPage>,
  options: FoundryLiteCursorPaginationOptions<TPage, TItem> = {},
): FoundryLiteCursorPaginationState<TPage, TItem> {
  const {
    enabled = true,
    getItems,
    getNextCursor,
    initialCursor = null,
    initialItems = EMPTY_FOUNDRY_LITE_CURSOR_ITEMS,
    onError,
    onPage,
    retry,
  } = options;
  const [pages, setPages] = useState<TPage[]>([]);
  const [items, setItems] = useState<TItem[]>(initialItems);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(enabled && initialItems.length === 0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(initialCursor);
  const requestKey = useMemo(() => JSON.stringify(key), [key]);
  const mountedRef = useRef(true);
  const pagesRef = useRef<TPage[]>([]);
  const itemsRef = useRef<TItem[]>(initialItems);
  const nextCursorRef = useRef<string | null>(initialCursor);
  const initialItemsRef = useRef<TItem[]>(initialItems);
  const loadPageRef = useRef(loadPage);
  const getItemsRef = useRef(getItems);
  const getNextCursorRef = useRef(getNextCursor);
  const onErrorRef = useRef(onError);
  const onPageRef = useRef(onPage);
  const retryRef = useRef(retry);

  initialItemsRef.current = initialItems;
  loadPageRef.current = loadPage;
  getItemsRef.current = getItems;
  getNextCursorRef.current = getNextCursor;
  onErrorRef.current = onError;
  onPageRef.current = onPage;
  retryRef.current = retry;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    const resetItems = initialItemsRef.current;
    pagesRef.current = [];
    itemsRef.current = resetItems;
    nextCursorRef.current = initialCursor;
    setPages([]);
    setItems(resetItems);
    setNextCursor(initialCursor);
    setError(null);
    setIsLoading(false);
    setIsLoadingMore(false);
  }, [initialCursor]);

  const loadCursor = useCallback(
    async (cursor: string | null, mode: "append" | "reload") => {
      if (mode === "append") setIsLoadingMore(true);
      else setIsLoading(true);
      try {
        const page = await retryWithBackoff(() => loadPageRef.current(cursor), retryRef.current);
        const pageItems = readFoundryLiteCursorItems(page, getItemsRef.current);
        const pageNextCursor = readFoundryLiteCursorNextCursor(page, getNextCursorRef.current);
        if (mountedRef.current) {
          const nextPages = mode === "append" ? [...pagesRef.current, page] : [page];
          const nextItems = mode === "append" ? [...itemsRef.current, ...pageItems] : pageItems;
          pagesRef.current = nextPages;
          itemsRef.current = nextItems;
          nextCursorRef.current = pageNextCursor;
          setPages(nextPages);
          setItems(nextItems);
          setNextCursor(pageNextCursor);
          setError(null);
          onPageRef.current?.({ page, items: pageItems, cursor, nextCursor: pageNextCursor });
        }
        return page;
      } catch (caught) {
        const normalized = normalizeFoundryLiteError(caught);
        if (mountedRef.current) {
          setError(normalized);
          onErrorRef.current?.(normalized);
        }
        return null;
      } finally {
        if (mountedRef.current) {
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [],
  );

  const reload = useCallback(async () => {
    pagesRef.current = [];
    itemsRef.current = [];
    nextCursorRef.current = initialCursor;
    setPages([]);
    setItems([]);
    setNextCursor(initialCursor);
    return loadCursor(initialCursor, "reload");
  }, [initialCursor, loadCursor]);

  const loadMore = useCallback(async () => {
    const cursor = nextCursorRef.current;
    if (cursor === null && pagesRef.current.length > 0) return null;
    const mode = pagesRef.current.length === 0 ? "reload" : "append";
    return loadCursor(cursor, mode);
  }, [loadCursor]);

  useEffect(() => {
    reset();
    if (!enabled) return;
    void reload();
  }, [enabled, reload, requestKey, reset]);

  return {
    pages,
    items,
    error,
    isLoading,
    isLoadingMore,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    nextCursor,
    hasNextPage: nextCursor !== null,
    reset,
    reload,
    loadMore,
  };
}

export type FoundryLiteMutationState<TResult, TPayload> = {
  result: TResult | null;
  error: FoundryLiteApiError | null;
  isRunning: boolean;
  requestId: string | null;
  retryable: boolean;
  execute(payload: TPayload): Promise<TResult | null>;
};

export type FoundryLiteMutationOptions<TResult, TPayload> = {
  lockKey?: (payload: TPayload) => string;
  actionLock?: InFlightActionLock;
  onSuccess?: (result: TResult) => void;
  onError?: (error: FoundryLiteApiError) => void;
};

export function useFoundryLiteMutation<TResult, TPayload>(
  mutate: (payload: TPayload) => Promise<TResult>,
  options: FoundryLiteMutationOptions<TResult, TPayload> = {},
): FoundryLiteMutationState<TResult, TPayload> {
  const session = useContext(FoundryLiteContext);
  const getLastResponse = session?.getLastResponse;
  const { actionLock: suppliedActionLock, lockKey, onSuccess, onError } = options;
  const actionLock = useMemo(() => suppliedActionLock ?? createInFlightActionLock(), [suppliedActionLock]);
  const [result, setResult] = useState<TResult | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);

  const execute = useCallback(
    async (payload: TPayload) => {
      const payloadLockKey = lockKey?.(payload);
      const task = async () => {
        setIsRunning(true);
        setRequestId(null);
        try {
          const nextResult = await mutate(payload);
          setRequestId(getLastResponse?.()?.requestId ?? null);
          setResult(nextResult);
          setError(null);
          onSuccess?.(nextResult);
          return nextResult;
        } catch (caught) {
          const normalized = normalizeFoundryLiteError(caught);
          setRequestId(normalized.requestId ?? getLastResponse?.()?.requestId ?? null);
          setError(normalized);
          onError?.(normalized);
          return null;
        } finally {
          setIsRunning(false);
        }
      };
      return payloadLockKey ? actionLock.run(payloadLockKey, task) : task();
    },
    [actionLock, getLastResponse, lockKey, mutate, onError, onSuccess],
  );

  return {
    result,
    error,
    isRunning,
    requestId: error?.requestId ?? requestId,
    retryable: error?.retryable ?? false,
    execute,
  };
}

export type FoundryLiteConnectorOnboardingOptions =
  Omit<ConnectorOnboardingRunOptions, "onState"> & {
    initialState?: ConnectorOnboardingRecipeState;
    onState?: (state: ConnectorOnboardingRecipeState) => void;
    onSuccess?: (state: ConnectorOnboardingRecipeState) => void;
    onError?: (error: FoundryLiteApiError, state: ConnectorOnboardingRecipeState) => void;
  };

export type FoundryLiteConnectorOnboardingState = ConnectorOnboardingRecipeState & {
  isRunning: boolean;
  run(
    input: ConnectorOnboardingInput,
    options?: ConnectorOnboardingRunOptions,
  ): Promise<ConnectorOnboardingRecipeState>;
  reset(): void;
};

export function useFoundryLiteConnectorOnboarding(
  client: Pick<FoundryLiteGeneratedClient, "connectors" | "operations">,
  options: FoundryLiteConnectorOnboardingOptions = {},
): FoundryLiteConnectorOnboardingState {
  const recipe = useMemo(() => createConnectorOnboardingRecipe(client), [client]);
  const initialState = options.initialState ?? recipe.initialState();
  const [state, setState] = useState<ConnectorOnboardingRecipeState>(initialState);
  const [isRunning, setIsRunning] = useState(false);
  const mountedRef = useRef(true);
  const optionsRef = useRef(options);

  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    const nextState = recipe.initialState();
    if (mountedRef.current) setState(nextState);
  }, [recipe]);

  const run = useCallback(
    async (
      input: ConnectorOnboardingInput,
      runOptions: ConnectorOnboardingRunOptions = {},
    ): Promise<ConnectorOnboardingRecipeState> => {
      const currentOptions = optionsRef.current;
      if (mountedRef.current) setIsRunning(true);
      const onState = (nextState: ConnectorOnboardingRecipeState) => {
        if (mountedRef.current) setState(nextState);
        currentOptions.onState?.(nextState);
        runOptions.onState?.(nextState);
      };
      try {
        const finalState = await recipe.runFirstSync(input, {
          ...currentOptions,
          ...runOptions,
          onState,
        });
        if (finalState.error) currentOptions.onError?.(finalState.error, finalState);
        else currentOptions.onSuccess?.(finalState);
        return finalState;
      } finally {
        if (mountedRef.current) setIsRunning(false);
      }
    },
    [recipe],
  );

  return { ...state, isRunning, run, reset };
}

export function useFoundryLiteProvidedConnectorOnboarding(
  options: FoundryLiteConnectorOnboardingOptions = {},
): FoundryLiteConnectorOnboardingState {
  const session = useFoundryLiteSession();
  const state = useFoundryLiteConnectorOnboarding(session.client, options);
  return useMemo(
    () => ({ ...state, requestId: state.requestId ?? session.lastRequestId }),
    [session.lastRequestId, state],
  );
}

export type FoundryLiteSourceOnboardingOptions =
  Omit<SourceOnboardingRunOptions, "onState"> & {
    initialState?: SourceOnboardingRecipeState;
    onState?: (state: SourceOnboardingRecipeState) => void;
    onSuccess?: (state: SourceOnboardingRecipeState) => void;
    onError?: (error: FoundryLiteApiError, state: SourceOnboardingRecipeState) => void;
  };

export type FoundryLiteSourceOnboardingState = SourceOnboardingRecipeState & {
  isRunning: boolean;
  run(
    input: SourceOnboardingInput,
    options?: SourceOnboardingRunOptions,
  ): Promise<SourceOnboardingRecipeState>;
  reset(): void;
};

export function useFoundryLiteSourceOnboarding(
  client: Pick<FoundryLiteGeneratedClient, "sources" | "operations">,
  options: FoundryLiteSourceOnboardingOptions = {},
): FoundryLiteSourceOnboardingState {
  const recipe = useMemo(() => createSourceOnboardingRecipe(client), [client]);
  const initialState = options.initialState ?? recipe.initialState();
  const [state, setState] = useState<SourceOnboardingRecipeState>(initialState);
  const [isRunning, setIsRunning] = useState(false);
  const mountedRef = useRef(true);
  const optionsRef = useRef(options);

  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    const nextState = recipe.initialState();
    if (mountedRef.current) setState(nextState);
  }, [recipe]);

  const run = useCallback(
    async (
      input: SourceOnboardingInput,
      runOptions: SourceOnboardingRunOptions = {},
    ): Promise<SourceOnboardingRecipeState> => {
      const currentOptions = optionsRef.current;
      if (mountedRef.current) setIsRunning(true);
      const onState = (nextState: SourceOnboardingRecipeState) => {
        if (mountedRef.current) setState(nextState);
        currentOptions.onState?.(nextState);
        runOptions.onState?.(nextState);
      };
      try {
        const finalState = await recipe.run(input, { ...currentOptions, ...runOptions, onState });
        if (finalState.error) currentOptions.onError?.(finalState.error, finalState);
        else currentOptions.onSuccess?.(finalState);
        return finalState;
      } finally {
        if (mountedRef.current) setIsRunning(false);
      }
    },
    [recipe],
  );

  return { ...state, isRunning, run, reset };
}

export function useFoundryLiteProvidedSourceOnboarding(
  options: FoundryLiteSourceOnboardingOptions = {},
): FoundryLiteSourceOnboardingState {
  const session = useFoundryLiteSession();
  const state = useFoundryLiteSourceOnboarding(session.client, options);
  return useMemo(
    () => ({ ...state, requestId: state.requestId ?? session.lastRequestId }),
    [session.lastRequestId, state],
  );
}

export type FoundryLiteSourceWizardOptions =
  Omit<SourceWizardRunOptions, "onState"> & {
    initialState?: SourceWizardRecipeState;
    onState?: (state: SourceWizardRecipeState) => void;
    onSuccess?: (state: SourceWizardRecipeState) => void;
    onError?: (error: FoundryLiteApiError, state: SourceWizardRecipeState) => void;
  };

export type FoundryLiteSourceWizardState = SourceWizardRecipeState & {
  isRunning: boolean;
  run(input: SourceWizardInput, options?: SourceWizardRunOptions): Promise<SourceWizardRecipeState>;
  reset(): void;
};

export function useFoundryLiteSourceWizard(
  client: Pick<FoundryLiteGeneratedClient, "sources">,
  options: FoundryLiteSourceWizardOptions = {},
): FoundryLiteSourceWizardState {
  const recipe = useMemo(() => createSourceWizardRecipe(client), [client]);
  const initialState = options.initialState ?? recipe.initialState();
  const [state, setState] = useState<SourceWizardRecipeState>(initialState);
  const [isRunning, setIsRunning] = useState(false);
  const mountedRef = useRef(true);
  const optionsRef = useRef(options);

  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    const nextState = recipe.initialState();
    if (mountedRef.current) setState(nextState);
  }, [recipe]);

  const run = useCallback(
    async (
      input: SourceWizardInput,
      runOptions: SourceWizardRunOptions = {},
    ): Promise<SourceWizardRecipeState> => {
      const currentOptions = optionsRef.current;
      if (mountedRef.current) setIsRunning(true);
      const onState = (nextState: SourceWizardRecipeState) => {
        if (mountedRef.current) setState(nextState);
        currentOptions.onState?.(nextState);
        runOptions.onState?.(nextState);
      };
      try {
        const finalState = await recipe.run(input, { ...currentOptions, ...runOptions, onState });
        if (finalState.error) currentOptions.onError?.(finalState.error, finalState);
        else currentOptions.onSuccess?.(finalState);
        return finalState;
      } finally {
        if (mountedRef.current) setIsRunning(false);
      }
    },
    [recipe],
  );

  return { ...state, isRunning, run, reset };
}

export function useFoundryLiteProvidedSourceWizard(
  options: FoundryLiteSourceWizardOptions = {},
): FoundryLiteSourceWizardState {
  const session = useFoundryLiteSession();
  const state = useFoundryLiteSourceWizard(session.client, options);
  return useMemo(
    () => ({ ...state, requestId: state.requestId ?? session.lastRequestId }),
    [session.lastRequestId, state],
  );
}

export type FoundryLiteObjectQueryOptions<TObject extends OsdkObjectType> =
  FoundryLiteQueryOptions<OsdkPage<OsdkInstance<TObject>>> &
    OsdkFetchPageOptions & {
      key?: readonly unknown[];
    };

export type FoundryLiteObjectQueryState<TObject extends OsdkObjectType> =
  FoundryLiteQueryState<OsdkPage<OsdkInstance<TObject>>> & {
    objectType: TObject;
    objects: OsdkInstance<TObject>[];
    items: OsdkInstance<TObject>[];
    nextPageToken: string | null;
    nextCursor: string | null;
    hasNextPage: boolean;
  };

export function foundryLiteObjectQueryKey<TObject extends OsdkObjectType>(
  objectType: TObject,
  options: OsdkFetchPageOptions = {},
): readonly unknown[] {
  return [
    "osdk",
    "objects",
    objectType.apiName,
    options.where ?? null,
    options.orderBy ?? null,
    options.pageSize ?? null,
    options.pageToken ?? null,
    options.search ?? null,
  ];
}

export function useFoundryLiteObjectQuery<TObject extends OsdkObjectType>(
  osdk: FoundryLiteOsdkClient,
  objectType: TObject,
  options: FoundryLiteObjectQueryOptions<TObject> = {},
): FoundryLiteObjectQueryState<TObject> {
  const { key, where, orderBy, pageSize, pageToken, search, ...queryOptions } = options;
  const fetchOptionsKey = useMemo(
    () => stableOsdkFetchOptionsKey({ where, orderBy, pageSize, pageToken, search }),
    [where, orderBy, pageSize, pageToken, search],
  );
  const fetchOptions = useMemo<OsdkFetchPageOptions>(
    () => ({ where, orderBy, pageSize, pageToken, search }),
    [fetchOptionsKey],
  );
  const queryKey = useMemo(
    () => key ?? foundryLiteObjectQueryKey(objectType, fetchOptions),
    [fetchOptions, key, objectType],
  );
  const objectSet = useMemo(() => osdk(objectType), [objectType, osdk]);
  const loadPage = useCallback(() => objectSet.fetchPage(fetchOptions), [fetchOptions, objectSet]);
  const query = useFoundryLiteQuery(queryKey, loadPage, queryOptions);
  const items = query.data?.items ?? [];
  const nextPageToken = query.data?.nextPageToken ?? null;

  return {
    ...query,
    objectType,
    objects: items,
    items,
    nextPageToken,
    nextCursor: query.data?.nextCursor ?? null,
    hasNextPage: nextPageToken !== null,
  };
}

export type FoundryLiteGenericObjectQueryOptions =
  FoundryLiteQueryOptions<ObjectQueryResult<GenericObject>> & {
    key?: readonly unknown[];
    objectApiName?: string | null;
    where?: ObjectFilter;
    orderBy?: Array<Record<string, string>>;
    pageSize?: number;
    pageToken?: string | null;
    search?: string | null;
  };

export type FoundryLiteGenericObjectQueryState =
  FoundryLiteQueryState<ObjectQueryResult<GenericObject>> & {
    objectApiName: string | null;
    objects: GenericObject[];
    items: GenericObject[];
    nextCursor: string | null;
    hasNextPage: boolean;
    canQueryObject: boolean;
    disabledReason: string | null;
  };

export function foundryLiteGenericObjectQueryRequest(
  options: Pick<
    FoundryLiteGenericObjectQueryOptions,
    "where" | "orderBy" | "pageSize" | "pageToken" | "search"
  > = {},
): ObjectQueryRequest {
  return {
    filter: options.where,
    orderBy: options.orderBy,
    limit: options.pageSize,
    cursor: options.pageToken ?? null,
    search: options.search ?? null,
  };
}

export function foundryLiteGenericObjectQueryKey(
  objectApiName: string | null | undefined,
  request: ObjectQueryRequest,
): readonly unknown[] {
  return [
    "objects",
    "generic",
    objectApiName ?? null,
    request.filter ?? null,
    request.orderBy ?? null,
    request.limit ?? null,
    request.cursor ?? null,
    request.search ?? null,
  ];
}

export function useFoundryLiteGenericObjectQuery(
  client: Pick<FoundryLiteGeneratedClient, "objects">,
  options: FoundryLiteGenericObjectQueryOptions = {},
): FoundryLiteGenericObjectQueryState {
  const {
    key,
    objectApiName = null,
    where,
    orderBy,
    pageSize,
    pageToken,
    search,
    enabled = true,
    ...queryOptions
  } = options;
  const requestKey = useMemo(
    () => stableObjectQueryRequestKey({ where, orderBy, pageSize, pageToken, search }),
    [where, orderBy, pageSize, pageToken, search],
  );
  const request = useMemo(
    () => foundryLiteGenericObjectQueryRequest({ where, orderBy, pageSize, pageToken, search }),
    [requestKey],
  );
  const canQueryObject = Boolean(objectApiName);
  const queryKey = useMemo(
    () => key ?? foundryLiteGenericObjectQueryKey(objectApiName, request),
    [key, objectApiName, request],
  );
  const load = useCallback(() => {
    if (!objectApiName) {
      throw new FoundryLiteApiError(
        0,
        "OBJECT_TYPE_NOT_SELECTED",
        "Select an object type before querying objects.",
        { missingFields: ["objectApiName"] },
        null,
        false,
      );
    }
    return client.objects.generic.query(objectApiName, request);
  }, [client, objectApiName, request]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canQueryObject,
  });
  const items = query.data?.items ?? [];
  const nextCursor = query.data?.nextCursor ?? null;
  return {
    ...query,
    objectApiName,
    objects: items,
    items,
    nextCursor,
    hasNextPage: nextCursor !== null,
    canQueryObject,
    disabledReason: canQueryObject ? null : "Select an object type before querying objects.",
  };
}

export type FoundryLiteActionSubmitOptions<TAction extends OsdkActionType> =
  FoundryLiteMutationOptions<OsdkActionResult<TAction>, OsdkActionPayload<TAction>> & {
    idempotencyKey?: string | ((payload: OsdkActionPayload<TAction>) => string | undefined);
  };

export type FoundryLiteActionSubmitState<TAction extends OsdkActionType> =
  FoundryLiteMutationState<OsdkActionResult<TAction>, OsdkActionPayload<TAction>> & {
    actionType: TAction;
    actionName: string;
    targetObjectType: string;
  };

export type FoundryLiteActionTargetLike = {
  objectType?: string | null;
  objectId?: string | null;
  objectVersion?: number | null;
};

export type FoundryLiteActionFormSelection = {
  targetObject?: FoundryLiteActionTargetLike | FoundryLiteObject<string, object> | null;
  objectId?: string | null;
  expectedObjectVersion?: number | null;
  params?: Record<string, unknown>;
  idempotencyKey?: string | null;
  requireIdempotencyKey?: boolean;
};

export type FoundryLiteActionParameterInputKind =
  | "text"
  | "number"
  | "checkbox"
  | "json"
  | "date"
  | "datetime"
  | "select"
  | "media"
  | "attachment";

export type FoundryLiteActionParameterField = {
  name: string;
  parameterPath: string;
  label: string;
  description: string | null;
  dataType: string;
  inputKind: FoundryLiteActionParameterInputKind;
  isRequired: boolean;
  isVisible: boolean;
  isEditable: boolean;
  hasServerDefault: boolean;
  matchedOverride: number | null;
  options: unknown[];
  schema: Record<string, unknown>;
  value: unknown;
  hasValue: boolean;
  constraintError: string | null;
};

export type FoundryLiteActionFormSection = {
  id: string;
  title: string;
  description: string | null;
  columns: 1 | 2;
  isCollapsible: boolean;
  isInitiallyCollapsed: boolean;
  isVisible: boolean;
  visibleWhen: Record<string, unknown> | null;
  parameterNames: string[];
  fields: FoundryLiteActionParameterField[];
};

export type FoundryLiteActionFormView = {
  actionView: FoundryLiteOntologyActionView | null;
  actionApiName: string | null;
  displayName: string | null;
  targetObjectType: string | null;
  targetObjectId: string | null;
  expectedObjectVersion: number | null;
  params: Record<string, unknown>;
  idempotencyKey: string | null;
  generatedActionType: OsdkActionType | null;
  runtimeActionType: OsdkActionType | null;
  isGeneratedActionTypeAvailable: boolean;
  isActionEnabled: boolean;
  isTargetObjectTypeMatched: boolean;
  parameterFields: FoundryLiteActionParameterField[];
  parameterSections: FoundryLiteActionFormSection[];
  requiredParameterNames: string[];
  missingParameterNames: string[];
  invalidParameterNames: string[];
  missingFields: string[];
  hasRequiredParameters: boolean;
  hasAllRequiredParameters: boolean;
  hasValidParameters: boolean;
  hasIdempotencyKey: boolean;
  canBuildPayload: boolean;
  canSubmitAction: boolean;
  disabledReason: string | null;
  payload: OsdkActionPayload<OsdkActionType> | null;
};

export type FoundryLiteActionFormResetOptions = {
  params?: Record<string, unknown>;
  idempotencyKey?: string | null;
};

export type FoundryLiteActionFormSubmitLockKey = (view: FoundryLiteActionFormView) => string;

export type FoundryLiteActionFormOptions =
  Omit<FoundryLiteActionFormSelection, "params" | "idempotencyKey"> &
    Omit<FoundryLiteMutationOptions<OsdkActionResult<OsdkActionType>, undefined>, "lockKey"> & {
      initialParams?: Record<string, unknown>;
      initialIdempotencyKey?: string | null;
      lockKey?: FoundryLiteActionFormSubmitLockKey;
    };

export type FoundryLiteActionFormState = FoundryLiteActionFormView & {
  actionResult: OsdkActionResult<OsdkActionType> | null;
  error: FoundryLiteApiError | null;
  isSubmitting: boolean;
  isRunning: boolean;
  requestId: string | null;
  retryable: boolean;
  setParam(name: string, value: unknown): void;
  replaceParams(params: Record<string, unknown>): void;
  setIdempotencyKey(idempotencyKey: string | null): void;
  resetForm(options?: FoundryLiteActionFormResetOptions): void;
  submit(): Promise<OsdkActionResult<OsdkActionType> | null>;
};

export type FoundryLiteActionFormSubmitRequest = {
  actionType: OsdkActionType;
  payload: OsdkActionPayload<OsdkActionType>;
};

export function foundryLiteActionPayloadObjectId(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null) return null;
  const objectId = (payload as { objectId?: unknown }).objectId;
  return typeof objectId === "string" && objectId.length > 0 ? objectId : null;
}

export function foundryLiteActionSubmitLockKey<TAction extends OsdkActionType>(
  actionType: TAction,
  payload: OsdkActionPayload<TAction>,
): string {
  const objectId = foundryLiteActionPayloadObjectId(payload);
  return objectId ? actionLockKey(actionType.apiName, objectId) : actionType.apiName;
}

export function useFoundryLiteActionSubmit<TAction extends OsdkActionType>(
  osdk: FoundryLiteOsdkClient,
  actionType: TAction,
  options: FoundryLiteActionSubmitOptions<TAction> = {},
): FoundryLiteActionSubmitState<TAction> {
  const {
    idempotencyKey: suppliedIdempotencyKey,
    actionLock,
    lockKey,
    onSuccess,
    onError,
  } = options;
  const actionInvoker = useMemo(() => osdk(actionType), [actionType, osdk]);
  const mutate = useCallback(
    (payload: OsdkActionPayload<TAction>) => {
      const idempotencyKey = resolveActionIdempotencyKey(suppliedIdempotencyKey, payload);
      return actionInvoker.applyAction(payload, idempotencyKey ? { idempotencyKey } : undefined);
    },
    [actionInvoker, suppliedIdempotencyKey],
  );
  const defaultLockKey = useCallback(
    (payload: OsdkActionPayload<TAction>) => foundryLiteActionSubmitLockKey(actionType, payload),
    [actionType],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    actionLock,
    lockKey: lockKey ?? defaultLockKey,
    onError,
    onSuccess,
  });

  return {
    ...mutation,
    actionType,
    actionName: actionType.apiName,
    targetObjectType: actionType.targetObjectType,
  };
}

export function foundryLiteActionFormView(
  actionView: FoundryLiteOntologyActionView | null | undefined,
  selection: FoundryLiteActionFormSelection = {},
): FoundryLiteActionFormView {
  const targetObject = selection.targetObject ?? null;
  const targetObjectId = selection.objectId ?? readFoundryLiteActionTargetObjectId(targetObject);
  const expectedObjectVersion =
    selection.expectedObjectVersion ?? readFoundryLiteActionTargetObjectVersion(targetObject);
  const targetObjectType = actionView?.targetObjectApiName ?? null;
  const targetInstanceType = readFoundryLiteActionTargetObjectType(targetObject);
  const runtimeActionType = foundryLiteRuntimeActionType(actionView);
  const isTargetObjectTypeMatched = Boolean(
    runtimeActionType?.targetKind === "interface" ||
      !targetObjectType ||
      !targetInstanceType ||
      targetObjectType === targetInstanceType,
  );
  const params = selection.params ?? {};
  const idempotencyKey = selection.idempotencyKey ?? null;
  const parameterFields = actionView
    ? foundryLiteActionParameterFields(actionView.action.parameterSchema, params)
    : [];
  const parameterSections = actionView
    ? foundryLiteActionParameterSections(actionView.action.parameterSchema, parameterFields)
    : [];
  const requiredParameterNames = parameterFields
    .filter((field) => field.isRequired)
    .map((field) => field.name);
  const missingParameterNames = parameterFields
    .filter((field) => field.isRequired && !field.hasValue)
    .map((field) => field.name);
  const invalidParameterNames = parameterFields
    .filter((field) => field.constraintError !== null)
    .map((field) => field.name);
  const missingFields = foundryLiteActionFormMissingFields(
    actionView,
    targetObjectId,
    expectedObjectVersion,
    missingParameterNames,
    idempotencyKey,
    selection.requireIdempotencyKey ?? false,
  );
  const disabledReason = foundryLiteActionFormDisabledReason(
    actionView,
    missingFields,
    invalidParameterNames,
    isTargetObjectTypeMatched,
  );
  const canBuildPayload =
    disabledReason === null && targetObjectId !== null && expectedObjectVersion !== null;
  const payload = canBuildPayload
    ? ({
        objectId: targetObjectId,
        expectedObjectVersion,
        params,
        ...(runtimeActionType?.targetKind === "interface" && targetInstanceType
          ? { objectType: targetInstanceType }
          : {}),
        ...(idempotencyKey ? { idempotencyKey } : {}),
      } as OsdkActionPayload<OsdkActionType>)
    : null;
  return {
    actionView: actionView ?? null,
    actionApiName: actionView?.apiName ?? null,
    displayName: actionView?.displayName ?? null,
    targetObjectType,
    targetObjectId,
    expectedObjectVersion,
    params,
    idempotencyKey,
    generatedActionType: actionView?.generatedActionType ?? null,
    runtimeActionType,
    isGeneratedActionTypeAvailable: actionView?.isGeneratedActionTypeAvailable ?? false,
    isActionEnabled: actionView?.isEnabled ?? false,
    isTargetObjectTypeMatched,
    parameterFields,
    parameterSections,
    requiredParameterNames,
    missingParameterNames,
    invalidParameterNames,
    missingFields,
    hasRequiredParameters: requiredParameterNames.length > 0,
    hasAllRequiredParameters: missingParameterNames.length === 0,
    hasValidParameters: invalidParameterNames.length === 0,
    hasIdempotencyKey: idempotencyKey !== null && idempotencyKey.length > 0,
    canBuildPayload,
    canSubmitAction: payload !== null,
    disabledReason,
    payload,
  };
}

export function foundryLiteActionFormSubmitLockKey(view: FoundryLiteActionFormView): string {
  const actionApiName = view.actionApiName ?? "unknown-action";
  const objectId = view.targetObjectId ?? "unknown-object";
  const intentKey = view.idempotencyKey ?? "missing-idempotency-key";
  return `action-form:${actionApiName}:${objectId}:${intentKey}`;
}

export function foundryLiteActionFormSubmitPayload(
  view: FoundryLiteActionFormView,
): OsdkActionPayload<OsdkActionType> {
  return foundryLiteActionFormSubmitRequest(view).payload;
}

export function foundryLiteActionFormSubmitRequest(
  view: FoundryLiteActionFormView,
): FoundryLiteActionFormSubmitRequest {
  if (!view.runtimeActionType) {
    throw new FoundryLiteApiError(
      0,
      "UNKNOWN_ACTION",
      view.disabledReason ?? "Selected action is not available in the active ontology catalog.",
      { actionApiName: view.actionApiName, missingFields: view.missingFields },
      null,
      false,
    );
  }
  if (!view.payload) {
    throw new FoundryLiteApiError(
      0,
      "INVALID_ACTION_FORM",
      view.disabledReason ?? "Action form is not ready to submit.",
      { actionApiName: view.actionApiName, missingFields: view.missingFields },
      null,
      false,
    );
  }
  return { actionType: view.runtimeActionType, payload: view.payload };
}

export function useFoundryLiteActionForm(
  osdk: FoundryLiteOsdkClient,
  actionView: FoundryLiteOntologyActionView | null | undefined,
  options: FoundryLiteActionFormOptions = {},
): FoundryLiteActionFormState {
  const {
    targetObject = null,
    objectId,
    expectedObjectVersion,
    requireIdempotencyKey = true,
    initialParams,
    initialIdempotencyKey = null,
    actionLock,
    lockKey,
    onError,
    onSuccess,
  } = options;
  const [params, setParamsState] = useState<Record<string, unknown>>(() => ({ ...(initialParams ?? {}) }));
  const [idempotencyKey, setIdempotencyKeyState] = useState<string | null>(initialIdempotencyKey);
  const view = useMemo(
    () =>
      foundryLiteActionFormView(actionView, {
        targetObject,
        objectId,
        expectedObjectVersion,
        params,
        idempotencyKey,
        requireIdempotencyKey,
      }),
    [
      actionView,
      expectedObjectVersion,
      idempotencyKey,
      objectId,
      params,
      requireIdempotencyKey,
      targetObject,
    ],
  );
  const setParam = useCallback((name: string, value: unknown) => {
    setParamsState((previous) => ({ ...previous, [name]: value }));
  }, []);
  const replaceParams = useCallback((nextParams: Record<string, unknown>) => {
    setParamsState({ ...nextParams });
  }, []);
  const setIdempotencyKey = useCallback((nextIdempotencyKey: string | null) => {
    setIdempotencyKeyState(nextIdempotencyKey);
  }, []);
  const resetForm = useCallback(
    (resetOptions: FoundryLiteActionFormResetOptions = {}) => {
      setParamsState({ ...(resetOptions.params ?? initialParams ?? {}) });
      setIdempotencyKeyState(
        Object.prototype.hasOwnProperty.call(resetOptions, "idempotencyKey")
          ? resetOptions.idempotencyKey ?? null
          : initialIdempotencyKey,
      );
    },
    [initialIdempotencyKey, initialParams],
  );
  const mutate = useCallback(() => {
    const { actionType, payload } = foundryLiteActionFormSubmitRequest(view);
    return osdk(actionType).applyAction(payload);
  }, [osdk, view]);
  const mutationLockKey = useCallback(
    () => (lockKey ?? foundryLiteActionFormSubmitLockKey)(view),
    [lockKey, view],
  );
  const mutation = useFoundryLiteMutation<OsdkActionResult<OsdkActionType>, undefined>(mutate, {
    actionLock,
    lockKey: mutationLockKey,
    onError,
    onSuccess,
  });
  const submit = useCallback(() => mutation.execute(undefined), [mutation.execute]);

  return {
    ...view,
    actionResult: mutation.result,
    error: mutation.error,
    isSubmitting: mutation.isRunning,
    isRunning: mutation.isRunning,
    requestId: mutation.requestId,
    retryable: mutation.retryable,
    setParam,
    replaceParams,
    setIdempotencyKey,
    resetForm,
    submit,
  };
}

export function useFoundryLiteProvidedActionForm(
  actionView: FoundryLiteOntologyActionView | null | undefined,
  options: FoundryLiteActionFormOptions = {},
): FoundryLiteActionFormState {
  const osdk = useFoundryLiteOsdkClient();
  return useFoundryLiteActionForm(osdk, actionView, options);
}

export type FoundryLiteActionProposalView = {
  proposal: InsightActionProposal | null;
  actionApiName: string | null;
  targetObjectType: string | null;
  targetObjectId: string | null;
  expectedObjectVersion: number | null;
  params: Record<string, unknown>;
  generatedActionType: OsdkActionType | null;
  isGeneratedActionTypeAvailable: boolean;
  isTargetObjectTypeMatched: boolean;
  missingFields: string[];
  canSubmitActionProposal: boolean;
  disabledReason: string | null;
};

export type FoundryLiteActionProposalSubmitPayload = {
  idempotencyKey: string;
  objectId?: string;
  expectedObjectVersion?: number;
  params?: Record<string, unknown>;
};

export type FoundryLiteActionProposalApplyRequest = {
  objectId: string;
  expectedObjectVersion: number;
  params: Record<string, unknown>;
  idempotencyKey: string;
};

export type FoundryLiteActionProposalSubmitOptions =
  FoundryLiteMutationOptions<OsdkActionResult<OsdkActionType>, FoundryLiteActionProposalSubmitPayload>;

export type FoundryLiteActionProposalSubmitState =
  FoundryLiteMutationState<OsdkActionResult<OsdkActionType>, FoundryLiteActionProposalSubmitPayload> &
    FoundryLiteActionProposalView & {
      actionResult: OsdkActionResult<OsdkActionType> | null;
    };

export function foundryLiteActionProposalView(
  proposal: InsightActionProposal | null | undefined,
): FoundryLiteActionProposalView {
  const actionApiName = readFoundryLiteActionProposalString(proposal?.actionApiName);
  const targetObjectType = readFoundryLiteActionProposalString(proposal?.targetObjectType);
  const targetObjectId = readFoundryLiteActionProposalString(proposal?.targetObjectId);
  const expectedObjectVersion = readFoundryLiteActionProposalExpectedVersion(proposal);
  const params = proposal?.params ?? {};
  const generatedActionType = actionApiName ? readGeneratedActionType(actionApiName) : null;
  const isTargetObjectTypeMatched = Boolean(
    !generatedActionType ||
      !targetObjectType ||
      generatedActionType.targetObjectType === targetObjectType,
  );
  const missingFields = foundryLiteActionProposalMissingFields(
    actionApiName,
    targetObjectId,
    expectedObjectVersion,
    generatedActionType,
  );
  const disabledReason = foundryLiteActionProposalDisabledReason(missingFields, isTargetObjectTypeMatched);

  return {
    proposal: proposal ?? null,
    actionApiName,
    targetObjectType,
    targetObjectId,
    expectedObjectVersion,
    params,
    generatedActionType,
    isGeneratedActionTypeAvailable: generatedActionType !== null,
    isTargetObjectTypeMatched,
    missingFields,
    canSubmitActionProposal: disabledReason === null,
    disabledReason,
  };
}

export function foundryLiteActionProposalApplyRequest(
  proposal: InsightActionProposal | null | undefined,
  payload: FoundryLiteActionProposalSubmitPayload,
): FoundryLiteActionProposalApplyRequest {
  const view = foundryLiteActionProposalView({
    ...(proposal ?? {}),
    targetObjectId: payload.objectId ?? proposal?.targetObjectId,
    expectedObjectVersion: payload.expectedObjectVersion ?? proposal?.expectedObjectVersion,
    params: payload.params ?? proposal?.params,
  });
  if (!view.canSubmitActionProposal) {
    throw new FoundryLiteApiError(
      0,
      "INVALID_ACTION_PROPOSAL",
      view.disabledReason ?? "Insight action proposal is not executable.",
      { actionApiName: view.actionApiName, missingFields: view.missingFields },
      null,
      false,
    );
  }
  if (view.targetObjectId === null || view.expectedObjectVersion === null) {
    throw new FoundryLiteApiError(
      0,
      "INVALID_ACTION_PROPOSAL",
      "Insight action proposal target object id and expected object version are required.",
      { actionApiName: view.actionApiName, missingFields: view.missingFields },
      null,
      false,
    );
  }
  return {
    objectId: view.targetObjectId,
    expectedObjectVersion: view.expectedObjectVersion,
    params: view.params,
    idempotencyKey: payload.idempotencyKey,
  };
}

export function useFoundryLiteActionProposalSubmit(
  osdk: FoundryLiteOsdkClient,
  proposal: InsightActionProposal | null | undefined,
  options: FoundryLiteActionProposalSubmitOptions = {},
): FoundryLiteActionProposalSubmitState {
  const proposalView = useMemo(() => foundryLiteActionProposalView(proposal), [proposal]);
  const mutate = useCallback(
    (payload: FoundryLiteActionProposalSubmitPayload) => {
      if (!proposalView.generatedActionType) {
        throw new FoundryLiteApiError(
          0,
          "UNKNOWN_OSDK_ACTION",
          proposalView.disabledReason ?? "Insight action proposal action type is not generated.",
          { actionApiName: proposalView.actionApiName },
          null,
          false,
        );
      }
      const actionPayload = foundryLiteActionProposalApplyRequest(proposal, payload);
      return osdk(proposalView.generatedActionType).applyAction(
        actionPayload as OsdkActionPayload<OsdkActionType>,
        { idempotencyKey: payload.idempotencyKey },
      );
    },
    [osdk, proposal, proposalView],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    ...options,
    lockKey: options.lockKey ?? ((payload) => foundryLiteActionProposalSubmitLockKey(proposal, payload)),
  });
  return {
    ...mutation,
    ...proposalView,
    actionResult: mutation.result,
  };
}

export function foundryLiteActionProposalSubmitLockKey(
  proposal: InsightActionProposal | null | undefined,
  payload: FoundryLiteActionProposalSubmitPayload,
): string {
  const actionApiName = proposal?.actionApiName ?? "unknown-action";
  const objectId = payload.objectId ?? proposal?.targetObjectId ?? "unknown-object";
  return `insights:action-proposal:${actionApiName}:${objectId}:${payload.idempotencyKey}`;
}

export type FoundryLiteOntologyPropertyView = {
  property: OntologyCatalogProperty;
  apiName: string;
  displayName: string;
  dataType: string;
  isPrimaryKey: boolean;
  isSearchable: boolean;
  isEditable: boolean;
  isClassified: boolean;
  classification: string | null;
};

export type FoundryLiteOntologyActionView = {
  action: OntologyCatalogAction;
  apiName: string;
  displayName: string;
  targetObjectApiName: string;
  isEnabled: boolean;
  parameterCount: number;
  generatedActionType: OsdkActionType | null;
  isGeneratedActionTypeAvailable: boolean;
};

export type FoundryLiteOntologyObjectView = {
  objectType: OntologyCatalogObject;
  apiName: string;
  displayName: string;
  description: string | null;
  primaryKeyProperty: string;
  generatedObjectType: OsdkObjectType | null;
  isGeneratedObjectTypeAvailable: boolean;
  properties: FoundryLiteOntologyPropertyView[];
  searchableProperties: FoundryLiteOntologyPropertyView[];
  editableProperties: FoundryLiteOntologyPropertyView[];
  classifiedProperties: FoundryLiteOntologyPropertyView[];
  actions: FoundryLiteOntologyActionView[];
  linksFrom: OntologyCatalogLink[];
  linksTo: OntologyCatalogLink[];
  propertyCount: number;
  actionCount: number;
  linkCount: number;
};

export type FoundryLiteOntologyWorkspace = {
  catalog: OntologyCatalog | null;
  index: FoundryLiteOntologyIndex;
  versionLabel: string | null;
  objectViews: FoundryLiteOntologyObjectView[];
  actionViews: FoundryLiteOntologyActionView[];
  objectViewsByApiName: Record<string, FoundryLiteOntologyObjectView>;
  actionViewsByApiName: Record<string, FoundryLiteOntologyActionView>;
  actionsByTargetObjectApiName: Record<string, FoundryLiteOntologyActionView[]>;
  generatedObjectViews: FoundryLiteOntologyObjectView[];
  dynamicOnlyObjectViews: FoundryLiteOntologyObjectView[];
  generatedActionViews: FoundryLiteOntologyActionView[];
  dynamicOnlyActionViews: FoundryLiteOntologyActionView[];
  objectCount: number;
  actionCount: number;
  linkCount: number;
  hasDynamicOnlyTypes: boolean;
};

export type FoundryLiteOntologyExplorerSelection = {
  objectSearch?: string;
  actionSearch?: string;
  selectedObjectApiName?: string | null;
  selectedActionApiName?: string | null;
};

export type FoundryLiteOntologyExplorerView = {
  objectSearch: string;
  actionSearch: string;
  objectSearchResults: FoundryLiteOntologyObjectView[];
  actionSearchResults: FoundryLiteOntologyActionView[];
  selectedObjectView: FoundryLiteOntologyObjectView | null;
  selectedActionView: FoundryLiteOntologyActionView | null;
  recommendedObjectView: FoundryLiteOntologyObjectView | null;
  recommendedActionView: FoundryLiteOntologyActionView | null;
  selectedActionPalette: FoundryLiteOntologyActionView[];
  selectedObjectGeneratedType: OsdkObjectType | null;
  selectedActionGeneratedType: OsdkActionType | null;
  canUseSelectedObjectGeneratedType: boolean;
  canUseSelectedActionGeneratedType: boolean;
  hasSelectedActionOutsideObject: boolean;
  sdkRegenerationHint: string | null;
};

export type FoundryLiteOntologyObjectSdkStatus = "generated" | "dynamic_only";
export type FoundryLiteOntologyActionSdkStatus = "generated" | "dynamic_only" | "disabled";

export type FoundryLiteOntologyObjectCard = {
  objectView: FoundryLiteOntologyObjectView;
  apiName: string;
  displayName: string;
  description: string | null;
  badge: string;
  sdkStatus: FoundryLiteOntologyObjectSdkStatus;
  primaryKeyProperty: string;
  propertyCount: number;
  actionCount: number;
  linkCount: number;
  searchablePropertyNames: string[];
  editablePropertyNames: string[];
  classifiedPropertyNames: string[];
  actionApiNames: string[];
  generatedObjectType: OsdkObjectType | null;
  canQueryWithGeneratedType: boolean;
  hasActions: boolean;
  hasSearchableProperties: boolean;
  hasClassifiedProperties: boolean;
  disabledReason: string | null;
};

export type FoundryLiteOntologyActionPaletteItem = {
  actionView: FoundryLiteOntologyActionView;
  apiName: string;
  displayName: string;
  badge: string;
  sdkStatus: FoundryLiteOntologyActionSdkStatus;
  targetObjectApiName: string;
  parameterCount: number;
  generatedActionType: OsdkActionType | null;
  isEnabled: boolean;
  canRenderActionButton: boolean;
  disabledReason: string | null;
};

export type FoundryLiteOntologyWorkspaceShellView = {
  workspace: FoundryLiteOntologyWorkspace;
  explorer: FoundryLiteOntologyExplorerView;
  objectCards: FoundryLiteOntologyObjectCard[];
  objectCardsByApiName: Record<string, FoundryLiteOntologyObjectCard>;
  generatedObjectCards: FoundryLiteOntologyObjectCard[];
  dynamicOnlyObjectCards: FoundryLiteOntologyObjectCard[];
  selectedObjectCard: FoundryLiteOntologyObjectCard | null;
  recommendedObjectCard: FoundryLiteOntologyObjectCard | null;
  allActionPaletteItems: FoundryLiteOntologyActionPaletteItem[];
  selectedActionPaletteItems: FoundryLiteOntologyActionPaletteItem[];
  selectedActionItem: FoundryLiteOntologyActionPaletteItem | null;
  recommendedActionItem: FoundryLiteOntologyActionPaletteItem | null;
  selectedActionForm: FoundryLiteActionFormView | null;
  recommendedActionForm: FoundryLiteActionFormView | null;
  canQuerySelectedObject: boolean;
  canSubmitSelectedAction: boolean;
  selectedObjectDisabledReason: string | null;
  selectedActionDisabledReason: string | null;
  sdkRegenerationHint: string | null;
  hasDynamicOnlyObjectCards: boolean;
  hasDynamicOnlyActions: boolean;
};

export type FoundryLiteOntologyCatalogOptions = FoundryLiteQueryOptions<OntologyCatalog> & {
  key?: readonly unknown[];
};

export type FoundryLiteOntologyExplorerOptions =
  FoundryLiteOntologyCatalogOptions &
    FoundryLiteOntologyExplorerSelection;

export type FoundryLiteOntologyCatalogState =
  FoundryLiteQueryState<OntologyCatalog> &
    FoundryLiteOntologyWorkspace;

export type FoundryLiteOntologyExplorerState =
  FoundryLiteOntologyCatalogState &
    FoundryLiteOntologyExplorerView;

export type FoundryLiteOntologyWorkspaceShellState =
  FoundryLiteOntologyCatalogState &
    FoundryLiteOntologyWorkspaceShellView;

export type FoundryLiteObjectActionWorkspaceOptions =
  FoundryLiteOntologyExplorerOptions & {
    objectQuery?: Omit<FoundryLiteGenericObjectQueryOptions, "objectApiName">;
    selectedObjectId?: string | null;
    actionForm?: Omit<
      FoundryLiteActionFormOptions,
      "targetObject" | "objectId" | "expectedObjectVersion"
    >;
  };

export type FoundryLiteObjectActionWorkspaceState =
  FoundryLiteOntologyWorkspaceShellState & {
    osdk: FoundryLiteOsdkClient;
    objectQuery: FoundryLiteGenericObjectQueryState;
    objects: GenericObject[];
    items: GenericObject[];
    selectedObjectId: string | null;
    selectedObject: GenericObject | null;
    recommendedObject: GenericObject | null;
    hasSelectedObject: boolean;
    actionForm: FoundryLiteActionFormState;
    canRenderObjectList: boolean;
    canRenderActionForm: boolean;
    reloadObjects(): Promise<ObjectQueryResult<GenericObject> | null>;
  };

export type FoundryLiteScreenRecipesOptions = FoundryLiteQueryOptions<OntologyCatalog> & {
  key?: readonly unknown[];
};

export type FoundryLiteScreenRecipesState =
  FoundryLiteQueryState<OntologyCatalog> & {
    recipes: FoundryLiteScreenRecipes;
    catalog: OntologyCatalog | null;
    hasCatalog: boolean;
  };

export type FoundryLiteOperatorWorkspaceHomeOptions =
  FoundryLiteQueryOptions<OperatorWorkspaceHomeView> &
    OperatorWorkspaceHomeOptions & {
      key?: readonly unknown[];
      selectedAreaId?: OperatorWorkspaceAreaId;
    };

export type FoundryLiteOperatorWorkspaceHomeState =
  FoundryLiteQueryState<OperatorWorkspaceHomeView> &
    OperatorWorkspaceHomeView & {
      navigation: OperatorWorkspaceNavigationView;
    };

export type FoundryLiteOperatorWorkspaceShellOptions = FoundryLiteOperatorWorkspaceHomeOptions;

export type FoundryLiteOperatorWorkspaceShellState =
  FoundryLiteQueryState<OperatorWorkspaceHomeView> &
    OperatorWorkspaceShellView;

export type FoundryLiteOperatorAppShellState =
  FoundryLiteOperatorWorkspaceShellState & {
    recipes: FoundryLiteScreenRecipes;
    catalog: OntologyCatalog | null;
    hasCatalog: boolean;
    sessionStatus: FoundryLiteSessionStatusView;
    screenStatus: FoundryLiteScreenStatusView;
    isBooting: boolean;
    canRenderWorkspace: boolean;
    shouldShowSignIn: boolean;
    shouldShowPermissionDenied: boolean;
    hasBootError: boolean;
    bootError: FoundryLiteApiError | null;
    bootRequestId: string | null;
    reloadWorkspace(): Promise<OperatorWorkspaceHomeView | null>;
  };

export type FoundryLiteOperatorAppShellOptions = FoundryLiteOperatorWorkspaceShellOptions;

export function foundryLiteOntologyWorkspace(
  catalog: OntologyCatalog | null | undefined,
): FoundryLiteOntologyWorkspace {
  const index = createFoundryLiteOntologyIndex(catalog);
  const links = catalog?.linkTypes ?? [];
  const actionViews = (catalog?.objectTypes ?? []).flatMap((objectType) =>
    objectType.actions.map((action) => foundryLiteOntologyActionView(action)),
  );
  const actionsByTargetObjectApiName = groupFoundryLiteActionsByTarget(actionViews);
  const objectViews = (catalog?.objectTypes ?? []).map((objectType) =>
    foundryLiteOntologyObjectView(
      objectType,
      actionsByTargetObjectApiName[objectType.apiName] ?? [],
      links,
    ),
  );
  const objectViewsByApiName = Object.fromEntries(
    objectViews.map((objectView) => [objectView.apiName, objectView]),
  );
  const actionViewsByApiName = Object.fromEntries(
    actionViews.map((actionView) => [actionView.apiName, actionView]),
  );
  const dynamicOnlyObjectViews = objectViews.filter(
    (objectView) => !objectView.isGeneratedObjectTypeAvailable,
  );
  const dynamicOnlyActionViews = actionViews.filter(
    (actionView) => !actionView.isGeneratedActionTypeAvailable,
  );
  return {
    catalog: catalog ?? null,
    index,
    versionLabel: catalog ? `${catalog.status} v${catalog.versionNumber}` : null,
    objectViews,
    actionViews,
    objectViewsByApiName,
    actionViewsByApiName,
    actionsByTargetObjectApiName,
    generatedObjectViews: objectViews.filter((objectView) => objectView.isGeneratedObjectTypeAvailable),
    dynamicOnlyObjectViews,
    generatedActionViews: actionViews.filter((actionView) => actionView.isGeneratedActionTypeAvailable),
    dynamicOnlyActionViews,
    objectCount: objectViews.length,
    actionCount: actionViews.length,
    linkCount: links.length,
    hasDynamicOnlyTypes: dynamicOnlyObjectViews.length > 0 || dynamicOnlyActionViews.length > 0,
  };
}

export function foundryLiteOntologyExplorer(
  workspace: FoundryLiteOntologyWorkspace,
  selection: FoundryLiteOntologyExplorerSelection = {},
): FoundryLiteOntologyExplorerView {
  const objectSearch = normalizeFoundryLiteOntologySearch(selection.objectSearch);
  const actionSearch = normalizeFoundryLiteOntologySearch(selection.actionSearch);
  const objectSearchResults = filterFoundryLiteOntologyObjectViews(workspace.objectViews, objectSearch);
  const actionSearchResults = filterFoundryLiteOntologyActionViews(workspace.actionViews, actionSearch);
  const selectedObjectView = readFoundryLiteSelectedObjectView(workspace, selection.selectedObjectApiName);
  const selectedActionView = readFoundryLiteSelectedActionView(workspace, selection.selectedActionApiName);
  const recommendedObjectView = selectedObjectView ?? objectSearchResults[0] ?? null;
  const selectedActionPalette = selectedObjectView
    ? workspace.actionsByTargetObjectApiName[selectedObjectView.apiName] ?? []
    : [];
  const recommendedActionView = selectedActionView ?? selectedActionPalette[0] ?? actionSearchResults[0] ?? null;

  return {
    objectSearch,
    actionSearch,
    objectSearchResults,
    actionSearchResults,
    selectedObjectView,
    selectedActionView,
    recommendedObjectView,
    recommendedActionView,
    selectedActionPalette,
    selectedObjectGeneratedType: selectedObjectView?.generatedObjectType ?? null,
    selectedActionGeneratedType: selectedActionView?.generatedActionType ?? null,
    canUseSelectedObjectGeneratedType: selectedObjectView?.isGeneratedObjectTypeAvailable ?? false,
    canUseSelectedActionGeneratedType: selectedActionView?.isGeneratedActionTypeAvailable ?? false,
    hasSelectedActionOutsideObject: hasFoundryLiteSelectedActionOutsideObject(
      selectedObjectView,
      selectedActionView,
    ),
    sdkRegenerationHint: foundryLiteOntologySdkRegenerationHint(workspace),
  };
}

export function foundryLiteOntologyObjectCard(
  objectView: FoundryLiteOntologyObjectView,
): FoundryLiteOntologyObjectCard {
  const canQueryWithGeneratedType = objectView.isGeneratedObjectTypeAvailable;
  return {
    objectView,
    apiName: objectView.apiName,
    displayName: objectView.displayName,
    description: objectView.description,
    badge: canQueryWithGeneratedType ? "Generated OSDK" : "Live catalog",
    sdkStatus: canQueryWithGeneratedType ? "generated" : "dynamic_only",
    primaryKeyProperty: objectView.primaryKeyProperty,
    propertyCount: objectView.propertyCount,
    actionCount: objectView.actionCount,
    linkCount: objectView.linkCount,
    searchablePropertyNames: objectView.searchableProperties.map((property) => property.apiName),
    editablePropertyNames: objectView.editableProperties.map((property) => property.apiName),
    classifiedPropertyNames: objectView.classifiedProperties.map((property) => property.apiName),
    actionApiNames: objectView.actions.map((action) => action.apiName),
    generatedObjectType: objectView.generatedObjectType,
    canQueryWithGeneratedType,
    hasActions: objectView.actionCount > 0,
    hasSearchableProperties: objectView.searchableProperties.length > 0,
    hasClassifiedProperties: objectView.classifiedProperties.length > 0,
    disabledReason: canQueryWithGeneratedType
      ? null
      : "Regenerate the SDK before using this object type as a static OSDK query type.",
  };
}

export function foundryLiteOntologyActionPaletteItem(
  actionView: FoundryLiteOntologyActionView,
): FoundryLiteOntologyActionPaletteItem {
  const hasGeneratedAction = actionView.isGeneratedActionTypeAvailable;
  const sdkStatus = foundryLiteOntologyActionSdkStatus(actionView);
  return {
    actionView,
    apiName: actionView.apiName,
    displayName: actionView.displayName,
    badge: foundryLiteOntologyActionBadge(sdkStatus),
    sdkStatus,
    targetObjectApiName: actionView.targetObjectApiName,
    parameterCount: actionView.parameterCount,
    generatedActionType: actionView.generatedActionType,
    isEnabled: actionView.isEnabled,
    canRenderActionButton: actionView.isEnabled && hasGeneratedAction,
    disabledReason: foundryLiteOntologyActionDisabledReason(actionView),
  };
}

export function foundryLiteOntologyWorkspaceShell(
  workspace: FoundryLiteOntologyWorkspace,
  selection: FoundryLiteOntologyExplorerSelection = {},
): FoundryLiteOntologyWorkspaceShellView {
  const explorer = foundryLiteOntologyExplorer(workspace, selection);
  const objectCards = workspace.objectViews.map(foundryLiteOntologyObjectCard);
  const objectCardsByApiName = Object.fromEntries(
    objectCards.map((objectCard) => [objectCard.apiName, objectCard]),
  );
  const allActionPaletteItems = workspace.actionViews.map(foundryLiteOntologyActionPaletteItem);
  const selectedActionPaletteItems = explorer.selectedActionPalette.map(
    foundryLiteOntologyActionPaletteItem,
  );
  const selectedObjectCard = explorer.selectedObjectView
    ? objectCardsByApiName[explorer.selectedObjectView.apiName] ?? null
    : null;
  const recommendedObjectCard = explorer.recommendedObjectView
    ? objectCardsByApiName[explorer.recommendedObjectView.apiName] ?? null
    : null;
  const selectedActionItem = explorer.selectedActionView
    ? foundryLiteOntologyActionPaletteItem(explorer.selectedActionView)
    : null;
  const recommendedActionItem =
    selectedActionItem ??
    selectedActionPaletteItems[0] ??
    (explorer.recommendedActionView
      ? foundryLiteOntologyActionPaletteItem(explorer.recommendedActionView)
      : null);
  const selectedActionForm = explorer.selectedActionView
    ? foundryLiteActionFormView(explorer.selectedActionView)
    : null;
  const recommendedActionForm = explorer.recommendedActionView
    ? foundryLiteActionFormView(explorer.recommendedActionView)
    : null;

  return {
    workspace,
    explorer,
    objectCards,
    objectCardsByApiName,
    generatedObjectCards: objectCards.filter((objectCard) => objectCard.sdkStatus === "generated"),
    dynamicOnlyObjectCards: objectCards.filter((objectCard) => objectCard.sdkStatus === "dynamic_only"),
    selectedObjectCard,
    recommendedObjectCard,
    allActionPaletteItems,
    selectedActionPaletteItems,
    selectedActionItem,
    recommendedActionItem,
    selectedActionForm,
    recommendedActionForm,
    canQuerySelectedObject: selectedObjectCard?.canQueryWithGeneratedType ?? false,
    canSubmitSelectedAction: selectedActionItem?.canRenderActionButton ?? false,
    selectedObjectDisabledReason: selectedObjectCard?.disabledReason ?? null,
    selectedActionDisabledReason: selectedActionItem?.disabledReason ?? null,
    sdkRegenerationHint: explorer.sdkRegenerationHint,
    hasDynamicOnlyObjectCards: objectCards.some((objectCard) => objectCard.sdkStatus === "dynamic_only"),
    hasDynamicOnlyActions: allActionPaletteItems.some((item) => item.sdkStatus === "dynamic_only"),
  };
}

export function useFoundryLiteOntologyCatalog(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyCatalogOptions = {},
): FoundryLiteOntologyCatalogState {
  const query = useFoundryLiteQuery(
    options.key ?? ["ontology", "catalog"],
    () => client.ontology.catalog(),
    options,
  );
  const workspace = useMemo(() => foundryLiteOntologyWorkspace(query.data), [query.data]);
  return {
    ...query,
    ...workspace,
  };
}

export function useFoundryLiteOntologyExplorer(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyExplorerOptions = {},
): FoundryLiteOntologyExplorerState {
  const workspace = useFoundryLiteOntologyCatalog(client, options);
  const {
    actionSearch,
    objectSearch,
    selectedActionApiName,
    selectedObjectApiName,
  } = options;
  const explorer = useMemo(
    () =>
      foundryLiteOntologyExplorer(workspace, {
        actionSearch,
        objectSearch,
        selectedActionApiName,
        selectedObjectApiName,
      }),
    [actionSearch, objectSearch, selectedActionApiName, selectedObjectApiName, workspace.catalog],
  );
  return {
    ...workspace,
    ...explorer,
  };
}

export function useFoundryLiteOntologyWorkspaceShell(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyExplorerOptions = {},
): FoundryLiteOntologyWorkspaceShellState {
  const workspace = useFoundryLiteOntologyCatalog(client, options);
  const {
    actionSearch,
    objectSearch,
    selectedActionApiName,
    selectedObjectApiName,
  } = options;
  const shell = useMemo(
    () =>
      foundryLiteOntologyWorkspaceShell(workspace, {
        actionSearch,
        objectSearch,
        selectedActionApiName,
        selectedObjectApiName,
      }),
    [actionSearch, objectSearch, selectedActionApiName, selectedObjectApiName, workspace.catalog],
  );
  return {
    ...workspace,
    ...shell,
  };
}

export function useFoundryLiteProvidedOntologyWorkspaceShell(
  options: FoundryLiteOntologyExplorerOptions = {},
): FoundryLiteOntologyWorkspaceShellState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyWorkspaceShell(client, options);
}

export function useFoundryLiteProvidedOntologyExplorer(
  options: FoundryLiteOntologyExplorerOptions = {},
): FoundryLiteOntologyExplorerState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyExplorer(client, options);
}

export function useFoundryLiteObjectActionWorkspace(
  client: FoundryLiteGeneratedClient,
  options: FoundryLiteObjectActionWorkspaceOptions = {},
): FoundryLiteObjectActionWorkspaceState {
  const {
    objectQuery: objectQueryOptions = {},
    selectedObjectId = null,
    actionForm: actionFormOptions = {},
    ...ontologyOptions
  } = options;
  const shell = useFoundryLiteOntologyWorkspaceShell(client, ontologyOptions);
  const selectedObjectApiName = shell.explorer.selectedObjectView?.apiName ?? null;
  const objectQuery = useFoundryLiteGenericObjectQuery(client, {
    ...objectQueryOptions,
    objectApiName: selectedObjectApiName,
  });
  const objects = useMemo(
    () =>
      objectQuery.items.filter(
        (object) => !selectedObjectApiName || object.objectType === selectedObjectApiName,
      ),
    [objectQuery.items, selectedObjectApiName],
  );
  const selectedObject = useMemo(
    () =>
      selectedObjectId
        ? objects.find((object) => object.objectId === selectedObjectId) ?? null
        : null,
    [objects, selectedObjectId],
  );
  const recommendedObject = objects[0] ?? null;
  const osdk = useMemo(() => createFoundryLiteOsdkClient(client), [client]);
  const actionForm = useFoundryLiteActionForm(osdk, shell.explorer.selectedActionView, {
    ...actionFormOptions,
    targetObject: selectedObject,
  });

  return {
    ...shell,
    osdk,
    objectQuery,
    objects,
    items: objects,
    selectedObjectId,
    selectedObject,
    recommendedObject,
    hasSelectedObject: selectedObject !== null,
    actionForm,
    canRenderObjectList: objectQuery.canQueryObject,
    canRenderActionForm: shell.explorer.selectedActionView !== null && selectedObject !== null,
    reloadObjects: objectQuery.reload,
  };
}

export function useFoundryLiteProvidedObjectActionWorkspace(
  options: FoundryLiteObjectActionWorkspaceOptions = {},
): FoundryLiteObjectActionWorkspaceState {
  const client = useFoundryLiteClient();
  return useFoundryLiteObjectActionWorkspace(client, options);
}

export function useFoundryLiteScreenRecipes(
  client: FoundryLiteGeneratedClient,
  options: FoundryLiteScreenRecipesOptions = {},
): FoundryLiteScreenRecipesState {
  const query = useFoundryLiteQuery(
    options.key ?? ["screen-recipes", "ontology", "catalog"],
    () => client.ontology.catalog(),
    options,
  );
  const recipes = useMemo(
    () => createFoundryLiteScreenRecipes(client, query.data),
    [client, query.data],
  );
  return {
    ...query,
    recipes,
    catalog: query.data,
    hasCatalog: query.data !== null,
  };
}

export function useFoundryLiteProvidedScreenRecipes(
  options: FoundryLiteScreenRecipesOptions = {},
): FoundryLiteScreenRecipesState {
  const client = useFoundryLiteClient();
  return useFoundryLiteScreenRecipes(client, options);
}

export function foundryLiteOperatorWorkspaceShell(
  home: OperatorWorkspaceHomeView,
  selectedAreaId?: OperatorWorkspaceAreaId,
): OperatorWorkspaceShellView {
  return operatorWorkspaceShell(home, selectedAreaId);
}

export function useFoundryLiteOperatorWorkspaceHome(
  client: FoundryLiteGeneratedClient,
  options: FoundryLiteOperatorWorkspaceHomeOptions = {},
): FoundryLiteOperatorWorkspaceHomeState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operator-workspace", "home"],
    () => createOperatorWorkspaceRecipe(client).loadHome({ runFilters: options.runFilters }),
    options,
  );
  const home = query.data ?? emptyFoundryLiteOperatorWorkspaceHome();
  const navigation = useMemo(
    () => operatorWorkspaceNavigation(home, options.selectedAreaId),
    [home, options.selectedAreaId],
  );
  return {
    ...query,
    ...home,
    navigation,
  };
}

export function useFoundryLiteProvidedOperatorWorkspaceHome(
  options: FoundryLiteOperatorWorkspaceHomeOptions = {},
): FoundryLiteOperatorWorkspaceHomeState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOperatorWorkspaceHome(client, options);
}

export function useFoundryLiteOperatorWorkspaceShell(
  client: FoundryLiteGeneratedClient,
  options: FoundryLiteOperatorWorkspaceShellOptions = {},
): FoundryLiteOperatorWorkspaceShellState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operator-workspace", "shell"],
    () => createOperatorWorkspaceRecipe(client).loadHome({ runFilters: options.runFilters }),
    options,
  );
  const home = query.data ?? emptyFoundryLiteOperatorWorkspaceHome();
  const shell = useMemo(
    () => foundryLiteOperatorWorkspaceShell(home, options.selectedAreaId),
    [home, options.selectedAreaId],
  );
  return {
    ...query,
    ...shell,
  };
}

export function useFoundryLiteProvidedOperatorWorkspaceShell(
  options: FoundryLiteOperatorWorkspaceShellOptions = {},
): FoundryLiteOperatorWorkspaceShellState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOperatorWorkspaceShell(client, options);
}

export function useFoundryLiteProvidedOperatorAppShell(
  options: FoundryLiteOperatorAppShellOptions = {},
): FoundryLiteOperatorAppShellState {
  const client = useFoundryLiteClient();
  const sessionStatus = useFoundryLiteSessionStatus();
  const shell = useFoundryLiteOperatorWorkspaceShell(client, options);
  const recipes = useMemo(
    () => createFoundryLiteScreenRecipes(client, shell.home.catalog),
    [client, shell.home.catalog],
  );
  const screenStatus = useMemo(
    () =>
      foundryLiteScreenStatus({
        error: shell.error,
        hasData: shell.data !== null,
        isLoading: shell.isLoading,
        isRefreshing: shell.isRefreshing,
        requestId: shell.requestId,
        retryable: shell.retryable,
        sessionStatus,
      }),
    [
      sessionStatus,
      shell.data,
      shell.error,
      shell.isLoading,
      shell.isRefreshing,
      shell.requestId,
      shell.retryable,
    ],
  );
  const shouldShowSignIn = screenStatus.needsAuthentication;
  const shouldShowPermissionDenied = screenStatus.isPermissionDenied;
  const hasBootError = shell.error !== null;
  return {
    ...shell,
    recipes,
    catalog: shell.home.catalog,
    hasCatalog: shell.home.catalog !== null,
    sessionStatus,
    screenStatus,
    isBooting: screenStatus.shouldShowSkeleton || screenStatus.shouldShowInlineRefresh,
    canRenderWorkspace: screenStatus.shouldRenderContent,
    shouldShowSignIn,
    shouldShowPermissionDenied,
    hasBootError,
    bootError: shell.error,
    bootRequestId: shell.requestId,
    reloadWorkspace: shell.reload,
  };
}

function emptyFoundryLiteOperatorWorkspaceHome(): OperatorWorkspaceHomeView {
  return {
    catalog: null,
    datasets: [],
    runs: emptyFoundryLiteRuntimeRunQueryResult(),
    adminLaunchpad: adminOperationsLaunchpad(adminOperationsBoard(null, null)),
    recoveryOverview: null,
    datasetCount: 0,
    objectTypeCount: 0,
    actionTypeCount: 0,
    runCount: 0,
    failedRunCount: 0,
    runningRunCount: 0,
    hasDatasets: false,
    hasOntologyTypes: false,
    hasFailedRuns: false,
    hasRunningRuns: false,
    hasAdminBrowserActions: false,
    hasOperatorCommands: false,
    hasRecoveryActions: false,
    recommendedAdminSectionId: "browser",
  };
}

function emptyFoundryLiteRuntimeRunQueryResult(): RuntimeRunQueryResult {
  return {
    syncRuns: [],
    transformRuns: [],
    indexRuns: [],
    actionRuns: [],
    actionWritebacks: [],
    materializationRuns: [],
    outboxEvents: [],
    deadLetterEvents: [],
    workflowRuns: [],
    aiRuns: [],
    auditEvents: [],
    objectEdits: [],
    nextCursor: null,
    nextCursors: {},
  };
}

export type FoundryLiteMediaUploadPhase =
  | "idle"
  | "opening"
  | "uploading"
  | "committing"
  | "succeeded"
  | "failed";

export type FoundryLiteMediaUploadPayload = MediaUploadRequest & {
  mediaSetId: string;
  idempotencyKey: string;
  mode?: string;
};

export type FoundryLiteMediaUploadResult = {
  transaction: MediaOpenTransactionResult;
  upload: MediaStagedUpload;
  commit: MediaCommitResult;
  mediaItemVersionId: string;
  committedVersionIds: string[];
};

export type FoundryLiteMediaUploadState =
  FoundryLiteMutationState<FoundryLiteMediaUploadResult, FoundryLiteMediaUploadPayload> & {
    phase: FoundryLiteMediaUploadPhase;
    transaction: MediaOpenTransactionResult | null;
    upload: MediaStagedUpload | null;
    commit: MediaCommitResult | null;
    mediaItemVersionId: string | null;
    committedVersionIds: string[];
  };

export function foundryLiteMediaUploadLockKey(payload: FoundryLiteMediaUploadPayload): string {
  return `media:upload:${payload.mediaSetId}:${payload.logicalPath}:${payload.idempotencyKey}`;
}

export function useFoundryLiteMediaUpload(
  client: Pick<FoundryLiteGeneratedClient, "media">,
  options: FoundryLiteMutationOptions<FoundryLiteMediaUploadResult, FoundryLiteMediaUploadPayload> = {},
): FoundryLiteMediaUploadState {
  const { actionLock, lockKey, onSuccess, onError } = options;
  const [phase, setPhase] = useState<FoundryLiteMediaUploadPhase>("idle");
  const [transaction, setTransaction] = useState<MediaOpenTransactionResult | null>(null);
  const [upload, setUpload] = useState<MediaStagedUpload | null>(null);
  const [commit, setCommit] = useState<MediaCommitResult | null>(null);

  const mutate = useCallback(
    async (payload: FoundryLiteMediaUploadPayload) => {
      setTransaction(null);
      setUpload(null);
      setCommit(null);
      setPhase("opening");
      const nextTransaction = await client.media.transactions.open(
        payload.mediaSetId,
        { mode: payload.mode ?? "APPEND" },
        { idempotencyKey: payload.idempotencyKey },
      );
      setTransaction(nextTransaction);
      setPhase("uploading");
      const nextUpload = await client.media.transactions.upload(
        payload.mediaSetId,
        nextTransaction.mediaTransactionId,
        payload,
      );
      setUpload(nextUpload);
      setPhase("committing");
      const nextCommit = await client.media.transactions.commit(nextTransaction.mediaTransactionId);
      setCommit(nextCommit);
      return {
        transaction: nextTransaction,
        upload: nextUpload,
        commit: nextCommit,
        mediaItemVersionId: nextUpload.media_item_version_id,
        committedVersionIds: nextCommit.committed_version_ids,
      };
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    actionLock,
    lockKey: lockKey ?? foundryLiteMediaUploadLockKey,
    onError: (error) => {
      setPhase("failed");
      onError?.(error);
    },
    onSuccess: (result) => {
      setPhase("succeeded");
      onSuccess?.(result);
    },
  });

  return {
    ...mutation,
    phase,
    transaction,
    upload,
    commit,
    mediaItemVersionId: upload?.media_item_version_id ?? mutation.result?.mediaItemVersionId ?? null,
    committedVersionIds: commit?.committed_version_ids ?? mutation.result?.committedVersionIds ?? [],
  };
}

export function useFoundryLiteProvidedMediaUpload(
  options: FoundryLiteMutationOptions<FoundryLiteMediaUploadResult, FoundryLiteMediaUploadPayload> = {},
): FoundryLiteMediaUploadState {
  const client = useFoundryLiteClient();
  return useFoundryLiteMediaUpload(client, options);
}

export type FoundryLiteMediaProcessingPhase = "idle" | "processing" | "indexing" | "succeeded" | "failed";

export type FoundryLiteMediaProcessingPayload = {
  mediaItemVersionId: string;
  process: MediaProcessRequest;
  indexGeneration?: string | null;
};

export type FoundryLiteMediaProcessingResult = {
  processing: MediaProcessingOutcome;
  indexing: MediaIndexingOutcome | null;
  mediaDerivativeId: string;
  status: string;
};

export type FoundryLiteMediaProcessingState =
  FoundryLiteMutationState<FoundryLiteMediaProcessingResult, FoundryLiteMediaProcessingPayload> & {
    phase: FoundryLiteMediaProcessingPhase;
    processing: MediaProcessingOutcome | null;
    indexing: MediaIndexingOutcome | null;
    mediaDerivativeId: string | null;
  };

export function foundryLiteMediaProcessingLockKey(payload: FoundryLiteMediaProcessingPayload): string {
  return `media:process:${payload.mediaItemVersionId}:${payload.process.processor}:${payload.process.processorVersion}`;
}

export function useFoundryLiteMediaProcessing(
  client: Pick<FoundryLiteGeneratedClient, "media">,
  options: FoundryLiteMutationOptions<FoundryLiteMediaProcessingResult, FoundryLiteMediaProcessingPayload> = {},
): FoundryLiteMediaProcessingState {
  const { actionLock, lockKey, onSuccess, onError } = options;
  const [phase, setPhase] = useState<FoundryLiteMediaProcessingPhase>("idle");
  const [processing, setProcessing] = useState<MediaProcessingOutcome | null>(null);
  const [indexing, setIndexing] = useState<MediaIndexingOutcome | null>(null);

  const mutate = useCallback(
    async (payload: FoundryLiteMediaProcessingPayload) => {
      setProcessing(null);
      setIndexing(null);
      setPhase("processing");
      const nextProcessing = await client.media.versions.process(payload.mediaItemVersionId, payload.process);
      setProcessing(nextProcessing);
      let nextIndexing: MediaIndexingOutcome | null = null;
      if (payload.indexGeneration) {
        setPhase("indexing");
        nextIndexing = await client.media.derivatives.index(nextProcessing.media_derivative_id, {
          generation: payload.indexGeneration,
        });
        setIndexing(nextIndexing);
      }
      return {
        processing: nextProcessing,
        indexing: nextIndexing,
        mediaDerivativeId: nextProcessing.media_derivative_id,
        status: nextProcessing.status,
      };
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    actionLock,
    lockKey: lockKey ?? foundryLiteMediaProcessingLockKey,
    onError: (error) => {
      setPhase("failed");
      onError?.(error);
    },
    onSuccess: (result) => {
      setPhase("succeeded");
      onSuccess?.(result);
    },
  });

  return {
    ...mutation,
    phase,
    processing: processing ?? mutation.result?.processing ?? null,
    indexing: indexing ?? mutation.result?.indexing ?? null,
    mediaDerivativeId: processing?.media_derivative_id ?? mutation.result?.mediaDerivativeId ?? null,
  };
}

export function useFoundryLiteProvidedMediaProcessing(
  options: FoundryLiteMutationOptions<FoundryLiteMediaProcessingResult, FoundryLiteMediaProcessingPayload> = {},
): FoundryLiteMediaProcessingState {
  const client = useFoundryLiteClient();
  return useFoundryLiteMediaProcessing(client, options);
}

export type FoundryLiteMediaSearchOptions =
  FoundryLiteQueryOptions<MediaContentSearchHit[]> &
    MediaSearchRequest & {
      key?: readonly unknown[];
    };

export type FoundryLiteMediaSearchState = FoundryLiteQueryState<MediaContentSearchHit[]> & {
  hits: MediaContentSearchHit[];
  hasHits: boolean;
};

export function useFoundryLiteMediaSearch(
  client: Pick<FoundryLiteGeneratedClient, "media">,
  options: FoundryLiteMediaSearchOptions = {},
): FoundryLiteMediaSearchState {
  const { key, text, topK, allowedClassifications, ...queryOptions } = options;
  const payloadKey = useMemo(
    () => stableMediaSearchPayloadKey({ text, topK, allowedClassifications }),
    [allowedClassifications, text, topK],
  );
  const payload = useMemo<MediaSearchRequest>(
    () => ({ text, topK, allowedClassifications }),
    [payloadKey],
  );
  const queryKey = useMemo(() => key ?? ["media", "search", payload], [key, payload]);
  const load = useCallback(() => client.media.content.search(payload), [client, payload]);
  const query = useFoundryLiteQuery(queryKey, load, queryOptions);
  const hits = query.data ?? [];

  return {
    ...query,
    hits,
    hasHits: hits.length > 0,
  };
}

export function useFoundryLiteProvidedMediaSearch(
  options: FoundryLiteMediaSearchOptions = {},
): FoundryLiteMediaSearchState {
  const client = useFoundryLiteClient();
  return useFoundryLiteMediaSearch(client, options);
}

export type FoundryLiteMediaPipelinePhase =
  | "idle"
  | "opening"
  | "uploading"
  | "committing"
  | "processing"
  | "indexing"
  | "searching"
  | "succeeded"
  | "failed";

export type FoundryLiteMediaPipelinePayload = FoundryLiteMediaUploadPayload & {
  process: MediaProcessRequest;
  indexGeneration?: string | null;
  search?: MediaSearchRequest | null;
};

export type FoundryLiteMediaPipelineResult = {
  uploadResult: FoundryLiteMediaUploadResult;
  processing: MediaProcessingOutcome;
  indexing: MediaIndexingOutcome | null;
  hits: MediaContentSearchHit[];
  servingTruthMediaItemVersionId: string;
  mediaDerivativeId: string;
};

export type FoundryLiteMediaPipelineState =
  FoundryLiteMutationState<FoundryLiteMediaPipelineResult, FoundryLiteMediaPipelinePayload> & {
    phase: FoundryLiteMediaPipelinePhase;
    uploadResult: FoundryLiteMediaUploadResult | null;
    processing: MediaProcessingOutcome | null;
    indexing: MediaIndexingOutcome | null;
    hits: MediaContentSearchHit[];
    hasHits: boolean;
    servingTruthMediaItemVersionId: string | null;
    mediaDerivativeId: string | null;
    committedVersionIds: string[];
    isCommitted: boolean;
    isIndexed: boolean;
    isSearchable: boolean;
  };

export function foundryLiteMediaPipelineLockKey(payload: FoundryLiteMediaPipelinePayload): string {
  return `media:pipeline:${payload.mediaSetId}:${payload.logicalPath}:${payload.process.processor}:${payload.idempotencyKey}`;
}

export type FoundryLiteMediaProcessingFailure = {
  kind: string;
  reason: string;
  message: string;
  retryable: boolean;
};

export function isFoundryLiteMediaProcessingSucceeded(outcome: MediaProcessingOutcome): boolean {
  const status = outcome.status.toUpperCase();
  return status === "SUCCEEDED" || status === "COMMITTED";
}

export function foundryLiteMediaProcessingFailure(
  outcome: MediaProcessingOutcome,
): FoundryLiteMediaProcessingFailure | null {
  if (isFoundryLiteMediaProcessingSucceeded(outcome)) return null;
  const failure = (outcome.error ?? {}) as Record<string, unknown>;
  const failureDetails =
    typeof failure.details === "object" && failure.details !== null
      ? (failure.details as Record<string, unknown>)
      : {};
  const operatorMessage =
    typeof failure.operatorMessage === "string" ? failure.operatorMessage : null;
  const reason =
    typeof failureDetails.reason === "string"
      ? failureDetails.reason
      : operatorMessage ?? "media_processing_failed";
  return {
    kind: typeof failure.kind === "string" ? failure.kind : "unknown",
    reason,
    message: operatorMessage ?? `media processing failed: ${reason}`,
    retryable: failure.retryable === true,
  };
}

export function foundryLiteMediaProcessingFailureError(
  outcome: MediaProcessingOutcome,
  failure: FoundryLiteMediaProcessingFailure,
): FoundryLiteApiError {
  return new FoundryLiteApiError(
    422,
    "MEDIA_PROCESSING_FAILED",
    failure.message,
    {
      failure_kind: failure.kind,
      failure_reason: failure.reason,
      media_derivative_id: outcome.media_derivative_id,
      status: outcome.status,
      adapterFailure: outcome.error ?? null,
    },
    null,
    failure.retryable,
  );
}

export function useFoundryLiteMediaPipeline(
  client: Pick<FoundryLiteGeneratedClient, "media">,
  options: FoundryLiteMutationOptions<FoundryLiteMediaPipelineResult, FoundryLiteMediaPipelinePayload> = {},
): FoundryLiteMediaPipelineState {
  const { actionLock, lockKey, onSuccess, onError } = options;
  const [phase, setPhase] = useState<FoundryLiteMediaPipelinePhase>("idle");
  const [uploadResult, setUploadResult] = useState<FoundryLiteMediaUploadResult | null>(null);
  const [processing, setProcessing] = useState<MediaProcessingOutcome | null>(null);
  const [indexing, setIndexing] = useState<MediaIndexingOutcome | null>(null);
  const [hits, setHits] = useState<MediaContentSearchHit[]>([]);

  const mutate = useCallback(
    async (payload: FoundryLiteMediaPipelinePayload) => {
      setUploadResult(null);
      setProcessing(null);
      setIndexing(null);
      setHits([]);
      setPhase("opening");
      const transaction = await client.media.transactions.open(
        payload.mediaSetId,
        { mode: payload.mode ?? "APPEND" },
        { idempotencyKey: payload.idempotencyKey },
      );
      setPhase("uploading");
      const upload = await client.media.transactions.upload(
        payload.mediaSetId,
        transaction.mediaTransactionId,
        payload,
      );
      setPhase("committing");
      const commit = await client.media.transactions.commit(transaction.mediaTransactionId);
      const nextUploadResult = {
        transaction,
        upload,
        commit,
        mediaItemVersionId: upload.media_item_version_id,
        committedVersionIds: commit.committed_version_ids,
      };
      setUploadResult(nextUploadResult);
      setPhase("processing");
      const nextProcessing = await client.media.versions.process(
        nextUploadResult.mediaItemVersionId,
        payload.process,
      );
      setProcessing(nextProcessing);
      const processingFailure = foundryLiteMediaProcessingFailure(nextProcessing);
      if (processingFailure !== null) {
        // A FAILED run must surface at the processing step; never advance to indexing.
        throw foundryLiteMediaProcessingFailureError(nextProcessing, processingFailure);
      }
      let nextIndexing: MediaIndexingOutcome | null = null;
      if (payload.indexGeneration) {
        setPhase("indexing");
        nextIndexing = await client.media.derivatives.index(nextProcessing.media_derivative_id, {
          generation: payload.indexGeneration,
        });
        setIndexing(nextIndexing);
      }
      let nextHits: MediaContentSearchHit[] = [];
      if (payload.search) {
        setPhase("searching");
        nextHits = await client.media.content.search(payload.search);
        setHits(nextHits);
      }
      return {
        uploadResult: nextUploadResult,
        processing: nextProcessing,
        indexing: nextIndexing,
        hits: nextHits,
        servingTruthMediaItemVersionId: nextUploadResult.mediaItemVersionId,
        mediaDerivativeId: nextProcessing.media_derivative_id,
      };
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    actionLock,
    lockKey: lockKey ?? foundryLiteMediaPipelineLockKey,
    onError: (error) => {
      setPhase("failed");
      onError?.(error);
    },
    onSuccess: (result) => {
      setPhase("succeeded");
      onSuccess?.(result);
    },
  });
  const resultUpload = uploadResult ?? mutation.result?.uploadResult ?? null;
  const resultProcessing = processing ?? mutation.result?.processing ?? null;
  const resultIndexing = indexing ?? mutation.result?.indexing ?? null;
  const resultHits = hits.length > 0 ? hits : mutation.result?.hits ?? [];

  return {
    ...mutation,
    phase,
    uploadResult: resultUpload,
    processing: resultProcessing,
    indexing: resultIndexing,
    hits: resultHits,
    hasHits: resultHits.length > 0,
    servingTruthMediaItemVersionId: resultUpload?.mediaItemVersionId ?? null,
    mediaDerivativeId: resultProcessing?.media_derivative_id ?? null,
    committedVersionIds: resultUpload?.committedVersionIds ?? [],
    isCommitted: Boolean(resultUpload),
    isIndexed: Boolean(resultIndexing),
    isSearchable: resultHits.length > 0,
  };
}

export function useFoundryLiteProvidedMediaPipeline(
  options: FoundryLiteMutationOptions<FoundryLiteMediaPipelineResult, FoundryLiteMediaPipelinePayload> = {},
): FoundryLiteMediaPipelineState {
  const client = useFoundryLiteClient();
  return useFoundryLiteMediaPipeline(client, options);
}

export type FoundryLiteAipPhase = "idle" | "running" | "succeeded" | "failed" | "unknown";

export type FoundryLiteAipRunView = {
  phase: FoundryLiteAipPhase;
  runStatus: string | null;
  runId: string | null;
  aiRunId: string | null;
  answer: string | null;
  operationsDetailPath: string | null;
  hasError: boolean;
  errorReason: string | null;
};

export type FoundryLiteAipRunResult = AipAgentRunResult | AipBuilderRunResult;

export type FoundryLiteAipRunWithOperationsDetailResult<
  TResult extends FoundryLiteAipRunResult,
> = {
  result: TResult;
  operationsDetail: RuntimeRunDetail | null;
  view: FoundryLiteAipRunView;
};

export type FoundryLiteAipRunWithOperationsDetailState<
  TResult extends FoundryLiteAipRunResult,
  TPayload,
> = FoundryLiteMutationState<
  FoundryLiteAipRunWithOperationsDetailResult<TResult>,
  TPayload
> &
  FoundryLiteAipRunView & {
    aipResult: TResult | null;
    operationsDetail: RuntimeRunDetail | null;
    hasOperationsDetail: boolean;
  };

export function foundryLiteAipPhase(status: string | null | undefined): FoundryLiteAipPhase {
  switch (String(status ?? "").toLowerCase()) {
    case "":
      return "idle";
    case "queued":
    case "requested":
    case "running":
    case "started":
      return "running";
    case "succeeded":
    case "success":
    case "completed":
    case "passed":
      return "succeeded";
    case "failed":
    case "error":
    case "blocked":
    case "rejected":
      return "failed";
    default:
      return "unknown";
  }
}

export function foundryLiteAipRunView(
  result: AipAgentRunResult | AipBuilderRunResult | null | undefined,
): FoundryLiteAipRunView {
  const runStatus = result?.runStatus ?? null;
  const error = result?.error ?? null;
  const agentRunId = result && "agentRunId" in result ? result.agentRunId : null;
  const logicRunId = result && "logicRunId" in result ? result.logicRunId : null;
  const answer = result && "answer" in result ? result.answer : null;
  return {
    phase: foundryLiteAipPhase(runStatus),
    runStatus,
    runId: agentRunId ?? logicRunId,
    aiRunId: result?.aiRunId ?? null,
    answer: answer ?? null,
    operationsDetailPath: result?.operations?.detailPath ?? null,
    hasError: error !== null && error !== undefined,
    errorReason: typeof error?.reason === "string" ? error.reason : null,
  };
}

export async function loadFoundryLiteAipOperationsDetail(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  result: FoundryLiteAipRunResult,
): Promise<RuntimeRunDetail | null> {
  return result.operations
    ? client.operations.runs.detail(result.operations.runType, result.operations.runId)
    : null;
}

export type FoundryLiteAipAgentRunState =
  FoundryLiteMutationState<AipAgentRunResult, AipAgentRunRequest> &
    FoundryLiteAipRunView;

export function useFoundryLiteAipAgentRun(
  client: Pick<FoundryLiteGeneratedClient, "aip">,
  options: FoundryLiteMutationOptions<AipAgentRunResult, AipAgentRunRequest> = {},
): FoundryLiteAipAgentRunState {
  const mutation = useFoundryLiteMutation((payload: AipAgentRunRequest) => client.aip.agent.run(payload), options);
  const view = useMemo(() => foundryLiteAipRunView(mutation.result), [mutation.result]);
  return { ...mutation, ...view, phase: mutation.isRunning ? "running" : view.phase };
}

export function useFoundryLiteProvidedAipAgentRun(
  options: FoundryLiteMutationOptions<AipAgentRunResult, AipAgentRunRequest> = {},
): FoundryLiteAipAgentRunState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAipAgentRun(client, options);
}

export type FoundryLiteAipAgentRunWithOperationsDetailState =
  FoundryLiteAipRunWithOperationsDetailState<AipAgentRunResult, AipAgentRunRequest>;

export function useFoundryLiteAipAgentRunWithOperationsDetail(
  client: Pick<FoundryLiteGeneratedClient, "aip" | "operations">,
  options: FoundryLiteMutationOptions<
    FoundryLiteAipRunWithOperationsDetailResult<AipAgentRunResult>,
    AipAgentRunRequest
  > = {},
): FoundryLiteAipAgentRunWithOperationsDetailState {
  const mutate = useCallback(
    async (payload: AipAgentRunRequest) => {
      const result = await client.aip.agent.run(payload);
      const operationsDetail = await loadFoundryLiteAipOperationsDetail(client, result);
      return { result, operationsDetail, view: foundryLiteAipRunView(result) };
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, options);
  const aipResult = mutation.result?.result ?? null;
  const operationsDetail = mutation.result?.operationsDetail ?? null;
  const view = useMemo(() => foundryLiteAipRunView(aipResult), [aipResult]);
  return {
    ...mutation,
    ...view,
    phase: mutation.isRunning ? "running" : view.phase,
    aipResult,
    operationsDetail,
    hasOperationsDetail: operationsDetail !== null,
  };
}

export function useFoundryLiteProvidedAipAgentRunWithOperationsDetail(
  options: FoundryLiteMutationOptions<
    FoundryLiteAipRunWithOperationsDetailResult<AipAgentRunResult>,
    AipAgentRunRequest
  > = {},
): FoundryLiteAipAgentRunWithOperationsDetailState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAipAgentRunWithOperationsDetail(client, options);
}

export type FoundryLiteAipBuilderRunState =
  FoundryLiteMutationState<AipBuilderRunResult, AipBuilderRunRequest> &
    FoundryLiteAipRunView & {
      releaseReady: boolean;
      validationStatus: string | null;
    };

export function useFoundryLiteAipBuilderRun(
  client: Pick<FoundryLiteGeneratedClient, "aip">,
  options: FoundryLiteMutationOptions<AipBuilderRunResult, AipBuilderRunRequest> = {},
): FoundryLiteAipBuilderRunState {
  const mutation = useFoundryLiteMutation((payload: AipBuilderRunRequest) => client.aip.builder.run(payload), options);
  const view = useMemo(() => foundryLiteAipRunView(mutation.result), [mutation.result]);
  return {
    ...mutation,
    ...view,
    phase: mutation.isRunning ? "running" : view.phase,
    releaseReady: mutation.result?.validation.releaseReady ?? false,
    validationStatus: mutation.result?.validation.validationStatus ?? null,
  };
}

export function useFoundryLiteProvidedAipBuilderRun(
  options: FoundryLiteMutationOptions<AipBuilderRunResult, AipBuilderRunRequest> = {},
): FoundryLiteAipBuilderRunState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAipBuilderRun(client, options);
}

export type FoundryLiteAipBuilderRunWithOperationsDetailState =
  FoundryLiteAipRunWithOperationsDetailState<AipBuilderRunResult, AipBuilderRunRequest> & {
    releaseReady: boolean;
    validationStatus: string | null;
  };

export function useFoundryLiteAipBuilderRunWithOperationsDetail(
  client: Pick<FoundryLiteGeneratedClient, "aip" | "operations">,
  options: FoundryLiteMutationOptions<
    FoundryLiteAipRunWithOperationsDetailResult<AipBuilderRunResult>,
    AipBuilderRunRequest
  > = {},
): FoundryLiteAipBuilderRunWithOperationsDetailState {
  const mutate = useCallback(
    async (payload: AipBuilderRunRequest) => {
      const result = await client.aip.builder.run(payload);
      const operationsDetail = await loadFoundryLiteAipOperationsDetail(client, result);
      return { result, operationsDetail, view: foundryLiteAipRunView(result) };
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, options);
  const aipResult = mutation.result?.result ?? null;
  const operationsDetail = mutation.result?.operationsDetail ?? null;
  const view = useMemo(() => foundryLiteAipRunView(aipResult), [aipResult]);
  return {
    ...mutation,
    ...view,
    phase: mutation.isRunning ? "running" : view.phase,
    aipResult,
    operationsDetail,
    hasOperationsDetail: operationsDetail !== null,
    releaseReady: aipResult?.validation.releaseReady ?? false,
    validationStatus: aipResult?.validation.validationStatus ?? null,
  };
}

export function useFoundryLiteProvidedAipBuilderRunWithOperationsDetail(
  options: FoundryLiteMutationOptions<
    FoundryLiteAipRunWithOperationsDetailResult<AipBuilderRunResult>,
    AipBuilderRunRequest
  > = {},
): FoundryLiteAipBuilderRunWithOperationsDetailState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAipBuilderRunWithOperationsDetail(client, options);
}

export type FoundryLiteInsightReviewQueueSelection = {
  selectedReviewId?: string | null;
  currentUserId?: string | null;
};

export type FoundryLiteInsightReviewQueueView = {
  result: InsightReviewQueryResult | null;
  reviews: InsightReviewPayload[];
  pendingReviews: InsightReviewPayload[];
  assignedReviews: InsightReviewPayload[];
  unassignedReviews: InsightReviewPayload[];
  currentUserAssignedReviews: InsightReviewPayload[];
  decidedReviews: InsightReviewPayload[];
  highPriorityReviews: InsightReviewPayload[];
  selectedReview: InsightReviewPayload | null;
  selectedActionProposal: InsightActionProposal | null;
  hasReviews: boolean;
  hasPendingReviews: boolean;
  hasAssignedReviews: boolean;
  hasCurrentUserAssignedReviews: boolean;
  hasDecidedReviews: boolean;
  hasHighPriorityReviews: boolean;
  canReviewSelectedActionProposal: boolean;
};

export type FoundryLiteInsightReviewQueueOptions =
  FoundryLiteQueryOptions<InsightReviewQueryResult> &
    InsightReviewListFilters &
    FoundryLiteInsightReviewQueueSelection & {
      key?: readonly unknown[];
    };

export type FoundryLiteInsightReviewQueueState =
  FoundryLiteQueryState<InsightReviewQueryResult> &
    FoundryLiteInsightReviewQueueView;

export type FoundryLiteInsightReviewCreatePayload = InsightReviewCreateRequest & {
  idempotencyKey: string;
};

export type FoundryLiteInsightReviewAssignPayload = InsightReviewAssignRequest & {
  reviewId: string;
  idempotencyKey: string;
};

export type FoundryLiteInsightReviewDecisionPayload = InsightReviewDecisionRequest & {
  reviewId: string;
  idempotencyKey: string;
};

export type FoundryLiteInsightReviewMutationState<TPayload> =
  FoundryLiteMutationState<InsightReviewPayload, TPayload> & {
    review: InsightReviewPayload | null;
    status: InsightReviewPayload["status"] | null;
    isTerminal: boolean;
  };

export function foundryLiteInsightReviewQueueView(
  result: InsightReviewQueryResult | null | undefined,
  selection: FoundryLiteInsightReviewQueueSelection = {},
): FoundryLiteInsightReviewQueueView {
  const reviews = result?.items ?? [];
  const pendingReviews = reviews.filter((review) => review.status === "pending");
  const assignedReviews = pendingReviews.filter((review) => review.assigneeUserId !== null);
  const currentUserAssignedReviews = selection.currentUserId
    ? assignedReviews.filter((review) => review.assigneeUserId === selection.currentUserId)
    : [];
  const decidedReviews = reviews.filter((review) => review.status !== "pending");
  const selectedReview = selection.selectedReviewId
    ? reviews.find((review) => review.id === selection.selectedReviewId) ?? null
    : pendingReviews[0] ?? reviews[0] ?? null;
  const selectedActionProposal = selectedReview?.actionProposal ?? null;

  return {
    result: result ?? null,
    reviews,
    pendingReviews,
    assignedReviews,
    unassignedReviews: pendingReviews.filter((review) => review.assigneeUserId === null),
    currentUserAssignedReviews,
    decidedReviews,
    highPriorityReviews: reviews.filter((review) => review.priority === "high"),
    selectedReview,
    selectedActionProposal,
    hasReviews: reviews.length > 0,
    hasPendingReviews: pendingReviews.length > 0,
    hasAssignedReviews: assignedReviews.length > 0,
    hasCurrentUserAssignedReviews: currentUserAssignedReviews.length > 0,
    hasDecidedReviews: decidedReviews.length > 0,
    hasHighPriorityReviews: reviews.some((review) => review.priority === "high"),
    canReviewSelectedActionProposal: Boolean(selectedActionProposal?.actionApiName),
  };
}

export function useFoundryLiteInsightReviewQueue(
  client: Pick<FoundryLiteGeneratedClient, "insights">,
  options: FoundryLiteInsightReviewQueueOptions = {},
): FoundryLiteInsightReviewQueueState {
  const {
    assigneeUserId,
    currentUserId,
    key,
    limit,
    selectedReviewId,
    status,
    ...queryOptions
  } = options;
  const filtersKey = useMemo(
    () => stableInsightReviewFiltersKey({ assigneeUserId, limit, status }),
    [assigneeUserId, limit, status],
  );
  const filters = useMemo<InsightReviewListFilters>(
    () => ({ assigneeUserId, limit, status }),
    [filtersKey],
  );
  const queryKey = useMemo(() => key ?? ["insights", "reviews", filters], [filters, key]);
  const load = useCallback(() => client.insights.reviews.list(filters), [client, filters]);
  const query = useFoundryLiteQuery(queryKey, load, queryOptions);
  const view = useMemo(
    () => foundryLiteInsightReviewQueueView(query.data, { currentUserId, selectedReviewId }),
    [currentUserId, query.data, selectedReviewId],
  );
  return { ...query, ...view };
}

export function useFoundryLiteProvidedInsightReviewQueue(
  options: FoundryLiteInsightReviewQueueOptions = {},
): FoundryLiteInsightReviewQueueState {
  const client = useFoundryLiteClient();
  return useFoundryLiteInsightReviewQueue(client, options);
}

export function useFoundryLiteInsightReviewCreate(
  client: Pick<FoundryLiteGeneratedClient, "insights">,
  options: FoundryLiteMutationOptions<InsightReviewPayload, FoundryLiteInsightReviewCreatePayload> = {},
): FoundryLiteInsightReviewMutationState<FoundryLiteInsightReviewCreatePayload> {
  const mutation = useFoundryLiteMutation(
    (payload: FoundryLiteInsightReviewCreatePayload) =>
      client.insights.reviews.create(foundryLiteInsightReviewCreateRequest(payload), {
        idempotencyKey: payload.idempotencyKey,
      }),
    {
      ...options,
      lockKey: options.lockKey ?? foundryLiteInsightReviewCreateLockKey,
    },
  );
  return foundryLiteInsightReviewMutationState(mutation);
}

export function useFoundryLiteInsightReviewAssignment(
  client: Pick<FoundryLiteGeneratedClient, "insights">,
  options: FoundryLiteMutationOptions<InsightReviewPayload, FoundryLiteInsightReviewAssignPayload> = {},
): FoundryLiteInsightReviewMutationState<FoundryLiteInsightReviewAssignPayload> {
  const mutation = useFoundryLiteMutation(
    (payload: FoundryLiteInsightReviewAssignPayload) =>
      client.insights.reviews.assign(
        payload.reviewId,
        { assigneeUserId: payload.assigneeUserId },
        { idempotencyKey: payload.idempotencyKey },
      ),
    {
      ...options,
      lockKey: options.lockKey ?? foundryLiteInsightReviewAssignLockKey,
    },
  );
  return foundryLiteInsightReviewMutationState(mutation);
}

export function useFoundryLiteInsightReviewDecision(
  client: Pick<FoundryLiteGeneratedClient, "insights">,
  options: FoundryLiteMutationOptions<InsightReviewPayload, FoundryLiteInsightReviewDecisionPayload> = {},
): FoundryLiteInsightReviewMutationState<FoundryLiteInsightReviewDecisionPayload> {
  const mutation = useFoundryLiteMutation(
    (payload: FoundryLiteInsightReviewDecisionPayload) =>
      client.insights.reviews.decide(
        payload.reviewId,
        { decision: payload.decision, comment: payload.comment ?? null },
        { idempotencyKey: payload.idempotencyKey },
      ),
    {
      ...options,
      lockKey: options.lockKey ?? foundryLiteInsightReviewDecisionLockKey,
    },
  );
  return foundryLiteInsightReviewMutationState(mutation);
}

export function foundryLiteInsightReviewCreateLockKey(
  payload: FoundryLiteInsightReviewCreatePayload,
): string {
  return `insights:review:create:${payload.claimId}:${payload.idempotencyKey}`;
}

export function foundryLiteInsightReviewAssignLockKey(
  payload: FoundryLiteInsightReviewAssignPayload,
): string {
  return `insights:review:assign:${payload.reviewId}:${payload.idempotencyKey}`;
}

export function foundryLiteInsightReviewDecisionLockKey(
  payload: FoundryLiteInsightReviewDecisionPayload,
): string {
  return `insights:review:decision:${payload.reviewId}:${payload.idempotencyKey}`;
}

export type FoundryLitePipelineBuilderData = {
  branches: PipelineBranch[];
  proposals: PipelineProposal[];
};

export type FoundryLitePipelineBuilderOptions =
  FoundryLiteQueryOptions<FoundryLitePipelineBuilderData> & {
    branchStatus?: string;
    proposalStatus?: string;
    limit?: number;
  };

export function useFoundryLitePipelineBuilder(
  client: Pick<FoundryLiteGeneratedClient, "pipelines">,
  options: FoundryLitePipelineBuilderOptions = {},
): FoundryLiteQueryState<FoundryLitePipelineBuilderData> {
  const { branchStatus, proposalStatus, limit, ...queryOptions } = options;
  const load = useCallback(async () => {
    const [branches, proposals] = await Promise.all([
      client.pipelines.branches.list({ status: branchStatus, limit }),
      client.pipelines.proposals.list({ status: proposalStatus, limit }),
    ]);
    return { branches: branches.items, proposals: proposals.items };
  }, [branchStatus, client, limit, proposalStatus]);
  return useFoundryLiteQuery(["pipelines", "builder", branchStatus, proposalStatus, limit], load, queryOptions);
}

export function useFoundryLitePipelineGraph(
  client: Pick<FoundryLiteGeneratedClient, "pipelines">,
  branchId: string | null | undefined,
  options: FoundryLiteQueryOptions<Record<string, unknown>> = {},
): FoundryLiteQueryState<Record<string, unknown>> {
  const load = useCallback(async () => {
    if (!branchId) return { branch: null, validation: null, diff: null };
    const [branch, validation, diff] = await Promise.all([
      client.pipelines.branches.get(branchId),
      client.pipelines.graph.validate(branchId),
      client.pipelines.branches.diff(branchId),
    ]);
    return { branch, validation, diff };
  }, [branchId, client]);
  return useFoundryLiteQuery(["pipelines", "graph", branchId], load, {
    ...options,
    enabled: options.enabled ?? Boolean(branchId),
  });
}

export function useFoundryLitePipelinePreview(
  client: Pick<FoundryLiteGeneratedClient, "pipelines">,
  branchId: string | null | undefined,
  nodeId: string | null | undefined,
  previewOptions: PipelinePreviewNodeRequest = {},
  options: FoundryLiteQueryOptions<Record<string, unknown>> = {},
): FoundryLiteQueryState<Record<string, unknown>> {
  const load = useCallback(async () => {
    if (!branchId || !nodeId) return { preview: null, stats: null, casts: null };
    const [preview, stats, casts] = await Promise.all([
      client.pipelines.graph.previewNode(branchId, nodeId, previewOptions),
      client.pipelines.graph.stats(branchId, nodeId, previewOptions),
      client.pipelines.graph.suggestCasts(branchId, nodeId),
    ]);
    return { preview, stats, casts };
  }, [branchId, client, nodeId, previewOptions]);
  return useFoundryLiteQuery(["pipelines", "preview", branchId, nodeId, previewOptions], load, {
    ...options,
    enabled: options.enabled ?? Boolean(branchId && nodeId),
  });
}

export function useFoundryLitePipelineReview(
  client: Pick<FoundryLiteGeneratedClient, "pipelines">,
  options: FoundryLiteQueryOptions<PipelineListResult<PipelineProposal>> & {
    status?: string;
    limit?: number;
  } = {},
): FoundryLiteQueryState<PipelineListResult<PipelineProposal>> {
  const { status, limit, ...queryOptions } = options;
  const load = useCallback(() => client.pipelines.proposals.list({ status, limit }), [client, limit, status]);
  return useFoundryLiteQuery(["pipelines", "review", status, limit], load, queryOptions);
}

export function useFoundryLiteProvidedPipelineBuilder(
  options: FoundryLitePipelineBuilderOptions = {},
): FoundryLiteQueryState<FoundryLitePipelineBuilderData> {
  const client = useFoundryLiteClient();
  return useFoundryLitePipelineBuilder(client, options);
}

export function useFoundryLiteProvidedPipelineReview(
  options: FoundryLiteQueryOptions<PipelineListResult<PipelineProposal>> & {
    status?: string;
    limit?: number;
  } = {},
): FoundryLiteQueryState<PipelineListResult<PipelineProposal>> {
  const client = useFoundryLiteClient();
  return useFoundryLitePipelineReview(client, options);
}

export type FoundryLiteSqlTransformSubmitPhase = "idle" | "registering" | "running" | "succeeded" | "failed";

export type FoundryLiteSqlTransformSubmitPayload = {
  definition: TransformSqlRegisterRequest;
  run?: boolean;
};

export type FoundryLiteSqlTransformSubmitResult = {
  transform: TransformRow;
  run: TransformRunResult | null;
};

export type FoundryLiteSqlTransformSubmitView = {
  transform: TransformRow | null;
  run: TransformRunResult | null;
  hasTransform: boolean;
  hasRun: boolean;
  hasOutputDatasetVersion: boolean;
  outputDatasetRef: string | null;
  outputDatasetVersionId: string | null;
  outputDatasetVersionNumber: number | null;
  outputDatasetRowCount: number | null;
  outputManifestUri: string | null;
  outputSchemaHash: string | null;
};

export type FoundryLiteSqlTransformSubmitState =
  FoundryLiteMutationState<FoundryLiteSqlTransformSubmitResult, FoundryLiteSqlTransformSubmitPayload> &
    FoundryLiteSqlTransformSubmitView & {
    phase: FoundryLiteSqlTransformSubmitPhase;
  };

export function foundryLiteSqlTransformLockKey(payload: FoundryLiteSqlTransformSubmitPayload): string {
  return `transform:sql:${payload.definition.apiName}`;
}

export function foundryLiteSqlTransformSubmitView(
  result: FoundryLiteSqlTransformSubmitResult | null | undefined,
  fallbackTransform: TransformRow | null = null,
  fallbackRun: TransformRunResult | null = null,
): FoundryLiteSqlTransformSubmitView {
  const transform = fallbackTransform ?? result?.transform ?? null;
  const run = fallbackRun ?? result?.run ?? null;
  return {
    transform,
    run,
    hasTransform: transform !== null,
    hasRun: run !== null,
    hasOutputDatasetVersion: typeof run?.version_id === "string" && run.version_id.length > 0,
    outputDatasetRef: run?.dataset_ref ?? null,
    outputDatasetVersionId: run?.version_id ?? null,
    outputDatasetVersionNumber: run?.version_number ?? null,
    outputDatasetRowCount: run?.row_count ?? null,
    outputManifestUri: run?.manifest_uri ?? null,
    outputSchemaHash: run?.schema_hash ?? null,
  };
}

export function useFoundryLiteSqlTransformSubmit(
  client: Pick<FoundryLiteGeneratedClient, "transforms">,
  options: FoundryLiteMutationOptions<
    FoundryLiteSqlTransformSubmitResult,
    FoundryLiteSqlTransformSubmitPayload
  > = {},
): FoundryLiteSqlTransformSubmitState {
  const { actionLock, lockKey, onSuccess, onError } = options;
  const [phase, setPhase] = useState<FoundryLiteSqlTransformSubmitPhase>("idle");
  const [transform, setTransform] = useState<TransformRow | null>(null);
  const [run, setRun] = useState<TransformRunResult | null>(null);

  const mutate = useCallback(
    async (payload: FoundryLiteSqlTransformSubmitPayload) => {
      setTransform(null);
      setRun(null);
      setPhase("registering");
      const nextTransform = await client.transforms.registerSql(payload.definition);
      setTransform(nextTransform);
      let nextRun: TransformRunResult | null = null;
      if (payload.run) {
        setPhase("running");
        nextRun = await client.transforms.run(payload.definition.apiName);
        setRun(nextRun);
      }
      return { transform: nextTransform, run: nextRun };
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    actionLock,
    lockKey: lockKey ?? foundryLiteSqlTransformLockKey,
    onError: (error) => {
      setPhase("failed");
      onError?.(error);
    },
    onSuccess: (result) => {
      setPhase("succeeded");
      onSuccess?.(result);
    },
  });
  const view = useMemo(
    () => foundryLiteSqlTransformSubmitView(mutation.result, transform, run),
    [mutation.result, run, transform],
  );

  return {
    ...mutation,
    ...view,
    phase,
  };
}

export function useFoundryLiteProvidedSqlTransformSubmit(
  options: FoundryLiteMutationOptions<
    FoundryLiteSqlTransformSubmitResult,
    FoundryLiteSqlTransformSubmitPayload
  > = {},
): FoundryLiteSqlTransformSubmitState {
  const client = useFoundryLiteClient();
  return useFoundryLiteSqlTransformSubmit(client, options);
}

export type FoundryLiteOperationState<TSnapshot> = {
  snapshot: TSnapshot | null;
  error: FoundryLiteApiError | null;
  isPolling: boolean;
  requestId: string | null;
  start(): Promise<TSnapshot | null>;
};

export function useFoundryLiteOperation<TSnapshot>(
  fetchSnapshot: (metadata: { attempt: number }) => Promise<TSnapshot>,
  options: PollFoundryLiteOperationOptions<TSnapshot> = {},
): FoundryLiteOperationState<TSnapshot> {
  const [snapshot, setSnapshot] = useState<TSnapshot | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isPolling, setIsPolling] = useState(false);

  const start = useCallback(async () => {
    setIsPolling(true);
    try {
      const finalSnapshot = await pollFoundryLiteOperation(fetchSnapshot, {
        ...options,
        onSnapshot: (metadata) => {
          setSnapshot(metadata.snapshot);
          options.onSnapshot?.(metadata);
        },
      });
      setSnapshot(finalSnapshot);
      setError(null);
      return finalSnapshot;
    } catch (caught) {
      const normalized = normalizeFoundryLiteError(caught);
      setError(normalized);
      return null;
    } finally {
      setIsPolling(false);
    }
  }, [fetchSnapshot, options]);

  return {
    snapshot,
    error,
    isPolling,
    requestId: error?.requestId ?? null,
    start,
  };
}

export type FoundryLiteOperationEventStreamOptions<
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
> = StreamFoundryLiteOperationEventsOptions<TEvent> & {
  autoStart?: boolean;
  maxEvents?: number;
};

export type FoundryLiteOperationEventStreamState<
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
> = {
  events: TEvent[];
  latestEvent: TEvent | null;
  error: FoundryLiteApiError | null;
  isStreaming: boolean;
  requestId: string | null;
  retryable: boolean;
  start(nextPath?: string): Promise<TEvent[]>;
  stop(): void;
  reset(): void;
};

export type FoundryLiteOperationTimelineSnapshotItem<TSnapshot> = {
  kind: "snapshot";
  id: string;
  receivedAt: string;
  attempt: number;
  isTerminal: boolean;
  snapshot: TSnapshot;
};

export type FoundryLiteOperationTimelineEventItem<
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
> = {
  kind: "event";
  id: string;
  receivedAt: string;
  eventType: string | null;
  event: TEvent;
};

export type FoundryLiteOperationTimelineItem<
  TSnapshot,
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
> =
  | FoundryLiteOperationTimelineSnapshotItem<TSnapshot>
  | FoundryLiteOperationTimelineEventItem<TEvent>;

export function useFoundryLiteOperationEventStream<
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
>(
  path: string | null | undefined,
  options: FoundryLiteOperationEventStreamOptions<TEvent> = {},
): FoundryLiteOperationEventStreamState<TEvent> {
  const { autoStart = false, maxEvents = 500 } = options;
  const [events, setEvents] = useState<TEvent[]>([]);
  const [latestEvent, setLatestEvent] = useState<TEvent | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  const reset = useCallback(() => {
    setEvents([]);
    setLatestEvent(null);
    setError(null);
    setRequestId(null);
  }, []);

  const start = useCallback(
    async (nextPath?: string) => {
      const streamPath = nextPath ?? path;
      if (!streamPath) return [];
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const collected: TEvent[] = [];
      setIsStreaming(true);
      setError(null);
      try {
        for await (const event of streamFoundryLiteOperationEvents<TEvent>(streamPath, {
          ...optionsRef.current,
          signal: controller.signal,
          onOpen: (metadata) => {
            if (mountedRef.current) setRequestId(metadata.requestId);
            optionsRef.current.onOpen?.(metadata);
          },
          onEvent: (metadata) => {
            collected.push(metadata.event);
            if (mountedRef.current) {
              setLatestEvent(metadata.event);
              setEvents((previous) => [...previous, metadata.event].slice(-maxEvents));
            }
            optionsRef.current.onEvent?.(metadata);
          },
        })) {
          void event;
        }
        return collected;
      } catch (caught) {
        if (controller.signal.aborted) return collected;
        const normalized = normalizeFoundryLiteError(caught);
        if (mountedRef.current) setError(normalized);
        return collected;
      } finally {
        if (mountedRef.current && abortRef.current === controller) {
          abortRef.current = null;
          setIsStreaming(false);
        }
      }
    },
    [maxEvents, path],
  );

  useEffect(() => {
    if (!autoStart || !path) return;
    void start(path);
    return () => {
      abortRef.current?.abort();
    };
  }, [autoStart, path, start]);

  return {
    events,
    latestEvent,
    error,
    isStreaming,
    requestId: requestId ?? error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    start,
    stop,
    reset,
  };
}

export function useFoundryLiteProvidedOperationEventStream<
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
>(
  path: string | null | undefined,
  options: FoundryLiteOperationEventStreamOptions<TEvent> = {},
): FoundryLiteOperationEventStreamState<TEvent> {
  const session = useFoundryLiteSession();
  const streamOptions = useMemo<FoundryLiteOperationEventStreamOptions<TEvent>>(
    () => ({
      ...session.operationStreamOptions,
      ...options,
      onOpen: (metadata: FoundryLiteResponseMetadata) => {
        session.operationStreamOptions.onOpen?.(metadata);
        options.onOpen?.(metadata);
      },
    }),
    [options, session.operationStreamOptions],
  );
  return useFoundryLiteOperationEventStream(path, streamOptions);
}

export type FoundryLiteLongRunningJobPhase =
  | "idle"
  | "starting"
  | "polling"
  | "succeeded"
  | "failed"
  | "unknown";

export type FoundryLiteLongRunningJobSnapshotLoader<TStartResult, TSnapshot> = (metadata: {
  startResult: TStartResult;
  attempt: number;
}) => Promise<TSnapshot>;

export type FoundryLiteLongRunningJobOptions<TStartResult, TSnapshot> =
  Omit<PollFoundryLiteOperationOptions<TSnapshot>, "onSnapshot"> & {
    pollOnStart?: boolean;
    getRunId?: (metadata: {
      startResult: TStartResult | null;
      snapshot: TSnapshot | null;
    }) => string | null | undefined;
    getStatus?: (metadata: {
      startResult: TStartResult | null;
      snapshot: TSnapshot | null;
    }) => string | null | undefined;
    isSuccess?: (snapshot: TSnapshot) => boolean;
    onStarted?: (startResult: TStartResult) => void;
    onSnapshot?: (metadata: {
      attempt: number;
      snapshot: TSnapshot;
      isTerminal: boolean;
      startResult: TStartResult;
    }) => void;
    onSuccess?: (metadata: { startResult: TStartResult; snapshot: TSnapshot }) => void;
    onFailure?: (metadata: { startResult: TStartResult; snapshot: TSnapshot }) => void;
    onError?: (error: FoundryLiteApiError) => void;
  };

export type FoundryLiteLiveOperationTimelineOptions<
  TStartResult,
  TSnapshot,
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
> = FoundryLiteLongRunningJobOptions<TStartResult, TSnapshot> & {
  getEventStreamPath?: (metadata: {
    startResult: TStartResult | null;
    snapshot: TSnapshot | null;
  }) => string | null | undefined;
  stream?: Omit<FoundryLiteOperationEventStreamOptions<TEvent>, "autoStart">;
  maxTimelineItems?: number;
  startStreamOnStart?: boolean;
  startStreamOnSnapshot?: boolean;
};

export type FoundryLiteLongRunningJobState<TPayload, TStartResult, TSnapshot> = {
  startResult: TStartResult | null;
  snapshot: TSnapshot | null;
  error: FoundryLiteApiError | null;
  phase: FoundryLiteLongRunningJobPhase;
  status: string | null;
  runId: string | null;
  isStarting: boolean;
  isPolling: boolean;
  isRunning: boolean;
  isTerminal: boolean;
  isSuccess: boolean;
  isFailure: boolean;
  startedAt: string | null;
  completedAt: string | null;
  requestId: string | null;
  retryable: boolean;
  start(payload: TPayload): Promise<TSnapshot | null>;
  poll(): Promise<TSnapshot | null>;
  reset(): void;
};

export type FoundryLiteLiveOperationTimelineState<
  TPayload,
  TStartResult,
  TSnapshot,
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
> = Omit<FoundryLiteLongRunningJobState<TPayload, TStartResult, TSnapshot>, "start" | "reset"> & {
  events: TEvent[];
  latestEvent: TEvent | null;
  timelineItems: Array<FoundryLiteOperationTimelineItem<TSnapshot, TEvent>>;
  streamPath: string | null;
  streamError: FoundryLiteApiError | null;
  streamRequestId: string | null;
  isStreaming: boolean;
  hasTimelineItems: boolean;
  isLive: boolean;
  start(payload: TPayload): Promise<TSnapshot | null>;
  startStream(path?: string): Promise<TEvent[]>;
  stopStream(): void;
  resetStream(): void;
  resetTimeline(): void;
  reset(): void;
};

export function useFoundryLiteLongRunningJob<TPayload, TStartResult, TSnapshot>(
  startJob: (payload: TPayload) => Promise<TStartResult>,
  fetchSnapshot: FoundryLiteLongRunningJobSnapshotLoader<TStartResult, TSnapshot>,
  options: FoundryLiteLongRunningJobOptions<TStartResult, TSnapshot> = {},
): FoundryLiteLongRunningJobState<TPayload, TStartResult, TSnapshot> {
  const [startResult, setStartResult] = useState<TStartResult | null>(null);
  const [snapshot, setSnapshot] = useState<TSnapshot | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [phase, setPhase] = useState<FoundryLiteLongRunningJobPhase>("idle");
  const [isTerminal, setIsTerminal] = useState(false);
  const [startedAt, setStartedAt] = useState<string | null>(null);
  const [completedAt, setCompletedAt] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const startResultRef = useRef<TStartResult | null>(null);
  const startJobRef = useRef(startJob);
  const fetchSnapshotRef = useRef(fetchSnapshot);
  const optionsRef = useRef(options);

  startJobRef.current = startJob;
  fetchSnapshotRef.current = fetchSnapshot;
  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    startResultRef.current = null;
    setStartResult(null);
    setSnapshot(null);
    setError(null);
    setPhase("idle");
    setIsTerminal(false);
    setStartedAt(null);
    setCompletedAt(null);
  }, []);

  const pollStartResult = useCallback(async (currentStartResult: TStartResult) => {
    const currentOptions = optionsRef.current;
    if (mountedRef.current) {
      setPhase("polling");
      setError(null);
    }
    try {
      const finalSnapshot = await pollFoundryLiteOperation(
        ({ attempt }) => fetchSnapshotRef.current({ startResult: currentStartResult, attempt }),
        {
          intervalMs: currentOptions.intervalMs,
          isTerminal: currentOptions.isTerminal,
          maxAttempts: currentOptions.maxAttempts,
          sleep: currentOptions.sleep,
          onSnapshot: (metadata) => {
            if (mountedRef.current) {
              setSnapshot(metadata.snapshot);
              setIsTerminal(metadata.isTerminal);
            }
            optionsRef.current.onSnapshot?.({ ...metadata, startResult: currentStartResult });
          },
        },
      );
      const didSucceed = foundryLiteLongRunningSnapshotSucceeded(
        finalSnapshot,
        optionsRef.current.isSuccess,
      );
      if (mountedRef.current) {
        setSnapshot(finalSnapshot);
        setIsTerminal(true);
        setPhase(didSucceed ? "succeeded" : "failed");
        setCompletedAt(foundryLiteNowIso());
      }
      if (didSucceed) optionsRef.current.onSuccess?.({ startResult: currentStartResult, snapshot: finalSnapshot });
      else optionsRef.current.onFailure?.({ startResult: currentStartResult, snapshot: finalSnapshot });
      return finalSnapshot;
    } catch (caught) {
      const normalized = normalizeFoundryLiteError(caught);
      if (mountedRef.current) {
        setError(normalized);
        setPhase("failed");
        setCompletedAt(foundryLiteNowIso());
      }
      optionsRef.current.onError?.(normalized);
      return null;
    }
  }, []);

  const poll = useCallback(async () => {
    const currentStartResult = startResultRef.current;
    if (!currentStartResult) {
      const normalized = normalizeFoundryLiteError(
        new Error("Cannot poll a Foundry Lite long-running job before start() succeeds"),
      );
      setError(normalized);
      optionsRef.current.onError?.(normalized);
      return null;
    }
    return pollStartResult(currentStartResult);
  }, [pollStartResult]);

  const start = useCallback(
    async (payload: TPayload) => {
      if (mountedRef.current) {
        setPhase("starting");
        setError(null);
        setSnapshot(null);
        setIsTerminal(false);
        setStartedAt(foundryLiteNowIso());
        setCompletedAt(null);
      }
      try {
        const nextStartResult = await startJobRef.current(payload);
        startResultRef.current = nextStartResult;
        if (mountedRef.current) {
          setStartResult(nextStartResult);
          setPhase(optionsRef.current.pollOnStart === false ? "polling" : "starting");
        }
        optionsRef.current.onStarted?.(nextStartResult);
        if (optionsRef.current.pollOnStart === false) return null;
        return pollStartResult(nextStartResult);
      } catch (caught) {
        const normalized = normalizeFoundryLiteError(caught);
        if (mountedRef.current) {
          setError(normalized);
          setPhase("failed");
          setCompletedAt(foundryLiteNowIso());
        }
        optionsRef.current.onError?.(normalized);
        return null;
      }
    },
    [pollStartResult],
  );

  const status = readFoundryLiteLongRunningJobStatus(options.getStatus, startResult, snapshot);
  const runId = readFoundryLiteLongRunningJobRunId(options.getRunId, startResult, snapshot);
  const isSuccess = phase === "succeeded";
  const isFailure = phase === "failed";
  const isStarting = phase === "starting";
  const isPolling = phase === "polling";

  return {
    startResult,
    snapshot,
    error,
    phase,
    status,
    runId,
    isStarting,
    isPolling,
    isRunning: isStarting || isPolling,
    isTerminal,
    isSuccess,
    isFailure,
    startedAt,
    completedAt,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    start,
    poll,
    reset,
  };
}

export function useFoundryLiteLiveOperationTimeline<
  TPayload,
  TStartResult,
  TSnapshot,
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
>(
  startJob: (payload: TPayload) => Promise<TStartResult>,
  fetchSnapshot: FoundryLiteLongRunningJobSnapshotLoader<TStartResult, TSnapshot>,
  options: FoundryLiteLiveOperationTimelineOptions<TStartResult, TSnapshot, TEvent> = {},
): FoundryLiteLiveOperationTimelineState<TPayload, TStartResult, TSnapshot, TEvent> {
  const {
    maxTimelineItems = 500,
    startStreamOnSnapshot = true,
    startStreamOnStart = true,
    stream: streamOptions,
    ...jobOptions
  } = options;
  const [timelineItems, setTimelineItems] = useState<
    Array<FoundryLiteOperationTimelineItem<TSnapshot, TEvent>>
  >([]);
  const [streamPath, setStreamPath] = useState<string | null>(null);
  const startResultRef = useRef<TStartResult | null>(null);
  const snapshotRef = useRef<TSnapshot | null>(null);
  const streamPathRef = useRef<string | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const appendTimelineItem = useCallback(
    (item: FoundryLiteOperationTimelineItem<TSnapshot, TEvent>) => {
      setTimelineItems((previous) => [...previous, item].slice(-maxTimelineItems));
    },
    [maxTimelineItems],
  );

  const stream = useFoundryLiteOperationEventStream<TEvent>(streamPath, {
    ...(streamOptions ?? {}),
    autoStart: false,
    onEvent: (metadata) => {
      appendTimelineItem({
        kind: "event",
        id: metadata.id ?? `event:${foundryLiteNowIso()}`,
        receivedAt: foundryLiteNowIso(),
        eventType: metadata.eventType,
        event: metadata.event,
      });
      streamOptions?.onEvent?.(metadata);
    },
  });

  const startResolvedStream = useCallback(
    async (path?: string | null) => {
      const resolvedPath =
        path ??
        optionsRef.current.getEventStreamPath?.({
          startResult: startResultRef.current,
          snapshot: snapshotRef.current,
        }) ??
        null;
      if (!resolvedPath) return [];
      if (streamPathRef.current !== resolvedPath) {
        streamPathRef.current = resolvedPath;
        setStreamPath(resolvedPath);
      }
      return stream.start(resolvedPath);
    },
    [stream.start],
  );

  const timelineJobOptions = useMemo<FoundryLiteLongRunningJobOptions<TStartResult, TSnapshot>>(
    () => ({
      ...jobOptions,
      onStarted: (startResult) => {
        startResultRef.current = startResult;
        jobOptions.onStarted?.(startResult);
        if (startStreamOnStart) void startResolvedStream();
      },
      onSnapshot: (metadata) => {
        snapshotRef.current = metadata.snapshot;
        appendTimelineItem({
          kind: "snapshot",
          id: `snapshot:${metadata.attempt}`,
          receivedAt: foundryLiteNowIso(),
          attempt: metadata.attempt,
          isTerminal: metadata.isTerminal,
          snapshot: metadata.snapshot,
        });
        jobOptions.onSnapshot?.(metadata);
        if (startStreamOnSnapshot) void startResolvedStream();
      },
    }),
    [
      appendTimelineItem,
      jobOptions,
      startResolvedStream,
      startStreamOnSnapshot,
      startStreamOnStart,
    ],
  );
  const job = useFoundryLiteLongRunningJob(startJob, fetchSnapshot, timelineJobOptions);

  const resetTimeline = useCallback(() => {
    setTimelineItems([]);
  }, []);

  const resetStream = useCallback(() => {
    streamPathRef.current = null;
    setStreamPath(null);
    stream.reset();
  }, [stream.reset]);

  const reset = useCallback(() => {
    startResultRef.current = null;
    snapshotRef.current = null;
    resetTimeline();
    resetStream();
    job.reset();
  }, [job.reset, resetStream, resetTimeline]);

  const start = useCallback(
    async (payload: TPayload) => {
      resetTimeline();
      resetStream();
      return job.start(payload);
    },
    [job.start, resetStream, resetTimeline],
  );

  return {
    ...job,
    events: stream.events,
    latestEvent: stream.latestEvent,
    timelineItems,
    streamPath,
    streamError: stream.error,
    streamRequestId: stream.requestId,
    isStreaming: stream.isStreaming,
    hasTimelineItems: timelineItems.length > 0,
    isLive: job.isRunning || stream.isStreaming,
    start,
    startStream: startResolvedStream,
    stopStream: stream.stop,
    resetStream,
    resetTimeline,
    reset,
  };
}

export function useFoundryLiteProvidedLiveOperationTimeline<
  TPayload,
  TStartResult,
  TSnapshot,
  TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
>(
  startJob: (payload: TPayload) => Promise<TStartResult>,
  fetchSnapshot: FoundryLiteLongRunningJobSnapshotLoader<TStartResult, TSnapshot>,
  options: FoundryLiteLiveOperationTimelineOptions<TStartResult, TSnapshot, TEvent> = {},
): FoundryLiteLiveOperationTimelineState<TPayload, TStartResult, TSnapshot, TEvent> {
  const session = useFoundryLiteSession();
  const providedOptions = useMemo<FoundryLiteLiveOperationTimelineOptions<TStartResult, TSnapshot, TEvent>>(
    () => ({
      ...options,
      stream: {
        ...session.operationStreamOptions,
        ...(options.stream ?? {}),
        onOpen: (metadata: FoundryLiteResponseMetadata) => {
          session.operationStreamOptions.onOpen?.(metadata);
          options.stream?.onOpen?.(metadata);
        },
      },
    }),
    [options, session.operationStreamOptions],
  );
  return useFoundryLiteLiveOperationTimeline(startJob, fetchSnapshot, providedOptions);
}

export type FoundryLiteWorkflowPhase = "pending" | "running" | "succeeded" | "failed" | "unknown";

export type FoundryLiteWorkflowRunView = {
  run: ProductWorkflowRun | null;
  phase: FoundryLiteWorkflowPhase;
  isTerminal: boolean;
  isSuccess: boolean;
  isFailure: boolean;
  foundryRunId: string | null;
  operationsDetailPath: string | null;
};

export type FoundryLiteWorkflowRunOptions = Omit<
  PollFoundryLiteOperationOptions<ProductWorkflowRun>,
  "isTerminal"
> & {
  autoStart?: boolean;
  enabled?: boolean;
  terminalStatuses?: readonly ProductWorkflowStatus[];
};

export type FoundryLiteWorkflowRunState = FoundryLiteOperationState<ProductWorkflowRun> &
  FoundryLiteWorkflowRunView & {
    refresh(): Promise<ProductWorkflowRun | null>;
  };

const DEFAULT_WORKFLOW_TERMINAL_STATUSES: readonly ProductWorkflowStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
  "start_unknown",
];

export function foundryLiteWorkflowPhase(
  status: ProductWorkflowStatus | string | null | undefined,
): FoundryLiteWorkflowPhase {
  switch (String(status ?? "").toLowerCase()) {
    case "queued":
    case "requested":
    case "starting":
      return "pending";
    case "running":
      return "running";
    case "succeeded":
      return "succeeded";
    case "failed":
    case "cancelled":
    case "canceled":
    case "start_unknown":
      return "failed";
    default:
      return "unknown";
  }
}

export function isFoundryLiteWorkflowTerminal(
  run: ProductWorkflowRun | null | undefined,
  terminalStatuses: readonly ProductWorkflowStatus[] = DEFAULT_WORKFLOW_TERMINAL_STATUSES,
): boolean {
  return run ? terminalStatuses.includes(run.status) : false;
}

export function foundryLiteWorkflowRunView(run: ProductWorkflowRun | null | undefined): FoundryLiteWorkflowRunView {
  const phase = foundryLiteWorkflowPhase(run?.status);
  return {
    run: run ?? null,
    phase,
    isTerminal: isFoundryLiteWorkflowTerminal(run),
    isSuccess: phase === "succeeded",
    isFailure: phase === "failed",
    foundryRunId: run?.foundryRunId ?? null,
    operationsDetailPath: run?.operationPath ?? null,
  };
}

export function useFoundryLiteWorkflowRun(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  workflowRunId: string | null | undefined,
  options: FoundryLiteWorkflowRunOptions = {},
): FoundryLiteWorkflowRunState {
  const {
    autoStart = false,
    enabled = true,
    intervalMs,
    maxAttempts,
    onSnapshot,
    sleep,
    terminalStatuses = DEFAULT_WORKFLOW_TERMINAL_STATUSES,
  } = options;
  const terminalStatusesKey = terminalStatuses.join("|");
  const fetchWorkflowRun = useCallback(async () => {
    if (!workflowRunId) {
      throw new Error("workflowRunId is required before polling a Foundry Lite workflow");
    }
    return client.operations.workflows.get(workflowRunId);
  }, [client, workflowRunId]);
  const isTerminal = useCallback(
    (run: ProductWorkflowRun) => isFoundryLiteWorkflowTerminal(run, terminalStatuses),
    [terminalStatusesKey],
  );
  const pollOptions = useMemo<PollFoundryLiteOperationOptions<ProductWorkflowRun>>(
    () => ({ intervalMs, isTerminal, maxAttempts, onSnapshot, sleep }),
    [intervalMs, isTerminal, maxAttempts, onSnapshot, sleep],
  );
  const operation = useFoundryLiteOperation(fetchWorkflowRun, pollOptions);
  const view = useMemo(() => foundryLiteWorkflowRunView(operation.snapshot), [operation.snapshot]);

  useEffect(() => {
    if (!autoStart || !enabled || !workflowRunId) return;
    void operation.start();
  }, [autoStart, enabled, operation.start, workflowRunId]);

  return {
    ...operation,
    ...view,
    refresh: operation.start,
  };
}

export type FoundryLiteOperationsRunPhase =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "unknown";

export type FoundryLiteOperationsRunRow = {
  row: RuntimeRow;
  collection: string;
  runType: RuntimeRunType | string;
  runId: string | null;
  status: string | null;
  phase: FoundryLiteOperationsRunPhase;
  operationPath: string | null;
  isPending: boolean;
  isRunning: boolean;
  isSucceeded: boolean;
  isFailure: boolean;
};

export type FoundryLiteOperationsRunListSelection = {
  selectedRunType?: RuntimeRunType | string | null;
  selectedRunId?: string | null;
};

export type FoundryLiteOperationsRunListView = {
  result: RuntimeRunQueryResult | null;
  runRows: FoundryLiteOperationsRunRow[];
  failedRuns: FoundryLiteOperationsRunRow[];
  runningRuns: FoundryLiteOperationsRunRow[];
  pendingRuns: FoundryLiteOperationsRunRow[];
  succeededRuns: FoundryLiteOperationsRunRow[];
  unknownRuns: FoundryLiteOperationsRunRow[];
  selectedRun: FoundryLiteOperationsRunRow | null;
  nextCursor: string | null;
  nextCursors: Record<string, string>;
  hasRuns: boolean;
  hasFailures: boolean;
  hasRunningRuns: boolean;
  hasMoreRuns: boolean;
};

export type FoundryLitePromptArtifactReference = {
  artifactId: string;
  label: string | null;
  contentHash: string | null;
  exportMarking: string | null;
  raw: Record<string, unknown>;
};

export type FoundryLiteOperationsRunDetailView = {
  detail: RuntimeRunDetail | null;
  runRow: FoundryLiteOperationsRunRow | null;
  promptArtifactRefs: FoundryLitePromptArtifactReference[];
  hasPromptArtifacts: boolean;
  relatedOutboxEventCount: number;
  relatedAuditEventCount: number;
  relatedObjectEditCount: number;
  relatedActionWritebackCount: number;
  runRelationCount: number;
  lineageEdgeCount: number;
  hasError: boolean;
  hasSourceEvidence: boolean;
  hasQualityEvidence: boolean;
  hasAiEvidence: boolean;
};

export type FoundryLiteOperationsRunListOptions =
  FoundryLiteQueryOptions<RuntimeRunQueryResult> &
    RuntimeRunQueryFilters &
    FoundryLiteOperationsRunListSelection & {
      key?: readonly unknown[];
    };

export type FoundryLiteOperationsRunListState =
  FoundryLiteQueryState<RuntimeRunQueryResult> &
    FoundryLiteOperationsRunListView;

export type FoundryLiteOperationsRunDetailOptions =
  FoundryLiteQueryOptions<RuntimeRunDetail> & {
    runType?: RuntimeRunType | string | null;
    runId?: string | null;
    key?: readonly unknown[];
  };

export type FoundryLiteOperationsRunDetailState =
  FoundryLiteQueryState<RuntimeRunDetail> &
    FoundryLiteOperationsRunDetailView;

export type FoundryLitePromptArtifactOptions =
  FoundryLiteQueryOptions<PromptArtifactReadResult> & {
    runId?: string | null;
    artifactId?: string | null;
    key?: readonly unknown[];
  };

export type FoundryLitePromptArtifactState =
  FoundryLiteQueryState<PromptArtifactReadResult> & {
    artifact: PromptArtifactReadResult | null;
    plaintext: string | null;
    contentHash: string | null;
    exportMarking: string | null;
  };

export type FoundryLiteOperationsInvestigationOptions =
  FoundryLiteOperationsRunListOptions & {
    promptArtifactId?: string | null;
    detailEnabled?: boolean;
    promptArtifactEnabled?: boolean;
  };

export type FoundryLiteOperationsInvestigationState = {
  list: FoundryLiteOperationsRunListState;
  detail: FoundryLiteOperationsRunDetailState;
  promptArtifact: FoundryLitePromptArtifactState;
  selectedRun: FoundryLiteOperationsRunRow | null;
  selectedPromptArtifactRef: FoundryLitePromptArtifactReference | null;
  isLoading: boolean;
  isRefreshing: boolean;
  error: FoundryLiteApiError | null;
  requestId: string | null;
  canLoadDetail: boolean;
  canLoadPromptArtifact: boolean;
};

const OPERATIONS_RUN_COLLECTIONS: readonly {
  key: keyof Pick<
    RuntimeRunQueryResult,
    | "syncRuns"
    | "transformRuns"
    | "indexRuns"
    | "actionRuns"
    | "actionWritebacks"
    | "materializationRuns"
    | "outboxEvents"
    | "deadLetterEvents"
    | "workflowRuns"
    | "aiRuns"
    | "auditEvents"
  >;
  runType: RuntimeRunType;
}[] = [
  { key: "syncRuns", runType: "sync" },
  { key: "transformRuns", runType: "transform" },
  { key: "indexRuns", runType: "index" },
  { key: "actionRuns", runType: "action" },
  { key: "actionWritebacks", runType: "action_writeback" },
  { key: "materializationRuns", runType: "materialization" },
  { key: "outboxEvents", runType: "outbox" },
  { key: "deadLetterEvents", runType: "dead_letter" },
  { key: "workflowRuns", runType: "workflow" },
  { key: "aiRuns", runType: "ai" },
  { key: "auditEvents", runType: "audit" },
];

export function foundryLiteOperationsRunPhase(
  status: string | null | undefined,
): FoundryLiteOperationsRunPhase {
  const normalized = String(status ?? "").toLowerCase();
  if (!normalized) return "unknown";
  if (isFoundryLiteFailureStatus(normalized)) return "failed";
  if (isFoundryLiteSuccessStatus(normalized)) return "succeeded";
  if (["queued", "pending", "requested", "scheduled", "starting"].includes(normalized)) {
    return "pending";
  }
  if (["running", "in_progress", "processing", "retrying", "open"].includes(normalized)) {
    return "running";
  }
  return "unknown";
}

export function foundryLiteOperationsRunRow(
  runType: RuntimeRunType | string,
  row: RuntimeRow,
  collection: string = "detail",
): FoundryLiteOperationsRunRow {
  const status = readFoundryLiteOperationsRunStatus(row);
  const phase = foundryLiteOperationsRunPhase(status);
  const runId = readFoundryLiteOperationsRunId(row);
  return {
    row,
    collection,
    runType,
    runId,
    status,
    phase,
    operationPath: readFoundryLiteOperationsRunPath(runType, row, runId),
    isPending: phase === "pending",
    isRunning: phase === "running",
    isSucceeded: phase === "succeeded",
    isFailure: phase === "failed",
  };
}

export function foundryLiteOperationsRunListView(
  result: RuntimeRunQueryResult | null | undefined,
  selection: FoundryLiteOperationsRunListSelection = {},
): FoundryLiteOperationsRunListView {
  const runRows = result
    ? OPERATIONS_RUN_COLLECTIONS.flatMap((collection) =>
        result[collection.key].map((row) =>
          foundryLiteOperationsRunRow(collection.runType, row, collection.key),
        ),
      )
    : [];
  const failedRuns = runRows.filter((run) => run.isFailure);
  const runningRuns = runRows.filter((run) => run.isRunning);
  const pendingRuns = runRows.filter((run) => run.isPending);
  const succeededRuns = runRows.filter((run) => run.isSucceeded);
  const unknownRuns = runRows.filter((run) => run.phase === "unknown");
  const selectedRun =
    findSelectedOperationsRun(runRows, selection) ??
    failedRuns[0] ??
    runningRuns[0] ??
    pendingRuns[0] ??
    runRows[0] ??
    null;

  return {
    result: result ?? null,
    runRows,
    failedRuns,
    runningRuns,
    pendingRuns,
    succeededRuns,
    unknownRuns,
    selectedRun,
    nextCursor: result?.nextCursor ?? null,
    nextCursors: result?.nextCursors ?? {},
    hasRuns: runRows.length > 0,
    hasFailures: failedRuns.length > 0,
    hasRunningRuns: runningRuns.length > 0 || pendingRuns.length > 0,
    hasMoreRuns: Boolean(result?.nextCursor) || Object.keys(result?.nextCursors ?? {}).length > 0,
  };
}

export function foundryLiteOperationsRunDetailView(
  detail: RuntimeRunDetail | null | undefined,
): FoundryLiteOperationsRunDetailView {
  const runRow = detail
    ? {
        ...foundryLiteOperationsRunRow(detail.runType, detail.row),
        runId: detail.runId,
        status: detail.status,
        phase: foundryLiteOperationsRunPhase(detail.status),
        isPending: foundryLiteOperationsRunPhase(detail.status) === "pending",
        isRunning: foundryLiteOperationsRunPhase(detail.status) === "running",
        isSucceeded: foundryLiteOperationsRunPhase(detail.status) === "succeeded",
        isFailure: foundryLiteOperationsRunPhase(detail.status) === "failed",
      }
    : null;
  const promptArtifactRefs = readFoundryLitePromptArtifactReferences(detail);
  return {
    detail: detail ?? null,
    runRow,
    promptArtifactRefs,
    hasPromptArtifacts: promptArtifactRefs.length > 0,
    relatedOutboxEventCount: detail?.relatedOutboxEvents.length ?? 0,
    relatedAuditEventCount: detail?.relatedAuditEvents.length ?? 0,
    relatedObjectEditCount: detail?.relatedObjectEdits.length ?? 0,
    relatedActionWritebackCount: detail?.relatedActionWritebacks.length ?? 0,
    runRelationCount: detail?.runRelations.length ?? 0,
    lineageEdgeCount: detail?.lineageEdges.length ?? 0,
    hasError: Boolean(detail?.errorMessage || detail?.error),
    hasSourceEvidence: Boolean(detail?.sourceEvidence),
    hasQualityEvidence: Boolean(detail?.quality),
    hasAiEvidence: Boolean(detail?.ai),
  };
}

export function useFoundryLiteOperationsRunList(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteOperationsRunListOptions = {},
): FoundryLiteOperationsRunListState {
  const {
    cursor,
    key,
    limit,
    runType,
    selectedRunId,
    selectedRunType,
    since,
    status,
    until,
    ...queryOptions
  } = options;
  const filtersKey = stableOperationsRunFiltersKey({ cursor, limit, runType, since, status, until });
  const filters = useMemo(
    () => compactOperationsRunFilters({ cursor, limit, runType, since, status, until }),
    [filtersKey],
  );
  const selection = useMemo(
    () => ({ selectedRunId, selectedRunType }),
    [selectedRunId, selectedRunType],
  );
  const query = useFoundryLiteQuery(
    key ?? ["operations", "runs", filters],
    () => client.operations.runs.list(filters),
    queryOptions,
  );
  const view = useMemo(
    () => foundryLiteOperationsRunListView(query.data, selection),
    [query.data, selection],
  );
  return { ...query, ...view };
}

export function useFoundryLiteProvidedOperationsRunList(
  options: FoundryLiteOperationsRunListOptions = {},
): FoundryLiteOperationsRunListState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOperationsRunList(client, options);
}

export function useFoundryLiteOperationsRunDetail(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteOperationsRunDetailOptions = {},
): FoundryLiteOperationsRunDetailState {
  const { key, runId, runType, ...queryOptions } = options;
  const enabled = queryOptions.enabled ?? Boolean(runType && runId);
  const query = useFoundryLiteQuery(
    key ?? ["operations", "runs", "detail", runType ?? null, runId ?? null],
    () => {
      if (!runType || !runId) {
        throw new Error("runType and runId are required before loading a Foundry Lite run detail");
      }
      return client.operations.runs.detail(runType, runId);
    },
    { ...queryOptions, enabled },
  );
  const view = useMemo(() => foundryLiteOperationsRunDetailView(query.data), [query.data]);
  return { ...query, ...view };
}

export function useFoundryLiteProvidedOperationsRunDetail(
  options: FoundryLiteOperationsRunDetailOptions = {},
): FoundryLiteOperationsRunDetailState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOperationsRunDetail(client, options);
}

export function useFoundryLitePromptArtifact(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLitePromptArtifactOptions = {},
): FoundryLitePromptArtifactState {
  const { artifactId, key, runId, ...queryOptions } = options;
  const enabled = queryOptions.enabled ?? Boolean(runId && artifactId);
  const query = useFoundryLiteQuery(
    key ?? ["operations", "runs", "prompt-artifact", runId ?? null, artifactId ?? null],
    () => {
      if (!runId || !artifactId) {
        throw new Error("runId and artifactId are required before loading a Foundry Lite prompt artifact");
      }
      return client.operations.runs.promptArtifact(runId, artifactId);
    },
    { ...queryOptions, enabled },
  );
  return {
    ...query,
    artifact: query.data,
    plaintext: query.data?.plaintext ?? null,
    contentHash: query.data?.contentHash ?? null,
    exportMarking: query.data?.exportMarking ?? null,
  };
}

export function useFoundryLiteProvidedPromptArtifact(
  options: FoundryLitePromptArtifactOptions = {},
): FoundryLitePromptArtifactState {
  const client = useFoundryLiteClient();
  return useFoundryLitePromptArtifact(client, options);
}

export function useFoundryLiteOperationsInvestigation(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteOperationsInvestigationOptions = {},
): FoundryLiteOperationsInvestigationState {
  const {
    detailEnabled = true,
    promptArtifactEnabled = true,
    promptArtifactId,
    ...listOptions
  } = options;
  const list = useFoundryLiteOperationsRunList(client, listOptions);
  const selectedRunType = options.selectedRunType ?? list.selectedRun?.runType ?? null;
  const selectedRunId = options.selectedRunId ?? list.selectedRun?.runId ?? null;
  const canLoadDetail = Boolean(selectedRunType && selectedRunId);
  const detail = useFoundryLiteOperationsRunDetail(client, {
    runType: selectedRunType,
    runId: selectedRunId,
    enabled: detailEnabled && canLoadDetail,
    key: ["operations", "runs", "investigation", "detail", selectedRunType, selectedRunId],
  });
  const selectedPromptArtifactRef =
    (promptArtifactId
      ? detail.promptArtifactRefs.find((ref) => ref.artifactId === promptArtifactId)
      : detail.promptArtifactRefs[0]) ?? null;
  const selectedPromptArtifactId = promptArtifactId ?? selectedPromptArtifactRef?.artifactId ?? null;
  const canLoadPromptArtifact = Boolean(selectedRunId && selectedPromptArtifactId);
  const promptArtifact = useFoundryLitePromptArtifact(client, {
    runId: selectedRunId,
    artifactId: selectedPromptArtifactId,
    enabled: promptArtifactEnabled && canLoadPromptArtifact,
    key: [
      "operations",
      "runs",
      "investigation",
      "prompt-artifact",
      selectedRunId,
      selectedPromptArtifactId,
    ],
  });

  return {
    list,
    detail,
    promptArtifact,
    selectedRun: list.selectedRun,
    selectedPromptArtifactRef,
    isLoading: list.isLoading || detail.isLoading || promptArtifact.isLoading,
    isRefreshing: list.isRefreshing || detail.isRefreshing || promptArtifact.isRefreshing,
    error: list.error ?? detail.error ?? promptArtifact.error,
    requestId: promptArtifact.requestId ?? detail.requestId ?? list.requestId,
    canLoadDetail,
    canLoadPromptArtifact,
  };
}

export function useFoundryLiteProvidedOperationsInvestigation(
  options: FoundryLiteOperationsInvestigationOptions = {},
): FoundryLiteOperationsInvestigationState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOperationsInvestigation(client, options);
}

export type FoundryLiteRecordDlqQueueSelection = {
  selectedRecordId?: string | null;
};

export type FoundryLiteRecordDlqQueueView = {
  records: DeadLetterRecord[];
  quarantinedRecords: DeadLetterRecord[];
  replayRequestedRecords: DeadLetterRecord[];
  replayingRecords: DeadLetterRecord[];
  resolvedRecords: DeadLetterRecord[];
  discardedRecords: DeadLetterRecord[];
  failedReplayRecords: DeadLetterRecord[];
  openRecords: DeadLetterRecord[];
  selectedRecord: DeadLetterRecord | null;
  hasRecords: boolean;
  hasOpenRecords: boolean;
  hasFailedReplay: boolean;
  canRetrySelected: boolean;
  canDiscardSelected: boolean;
};

export type FoundryLiteRecordDlqQueueOptions =
  FoundryLiteQueryOptions<DeadLetterRecord[]> &
    FoundryLiteRecordDlqQueueSelection & {
      status?: DeadLetterRecordStatus;
      key?: readonly unknown[];
    };

export type FoundryLiteRecordDlqQueueState =
  FoundryLiteQueryState<DeadLetterRecord[]> &
    FoundryLiteRecordDlqQueueView;

export type FoundryLiteRecordDlqRetryPayload = {
  id: string;
  idempotencyKey: string;
};

export type FoundryLiteRecordDlqBulkRetryPayload = {
  ids: string[];
  idempotencyKey: string;
};

export type FoundryLiteRecordDlqDiscardPayload = {
  id: string;
};

export type FoundryLiteRecordDlqControlsState = {
  retry: FoundryLiteMutationState<DeadLetterRecordRetryResult, FoundryLiteRecordDlqRetryPayload>;
  bulkRetry: FoundryLiteMutationState<DeadLetterRecordBulkRetryResult, FoundryLiteRecordDlqBulkRetryPayload>;
  discard: FoundryLiteMutationState<DeadLetterRecordDiscardResult, FoundryLiteRecordDlqDiscardPayload>;
  isBusy: boolean;
  latestRetryResult: DeadLetterRecordRetryResult | null;
  latestBulkRetryResult: DeadLetterRecordBulkRetryResult | null;
  latestDiscardResult: DeadLetterRecordDiscardResult | null;
};

export function foundryLiteRecordDlqQueueView(
  records: DeadLetterRecord[] | null | undefined,
  selection: FoundryLiteRecordDlqQueueSelection = {},
): FoundryLiteRecordDlqQueueView {
  const queueRecords = records ?? [];
  const quarantinedRecords = queueRecords.filter((record) => record.status === "QUARANTINED");
  const replayRequestedRecords = queueRecords.filter((record) => record.status === "REPLAY_REQUESTED");
  const replayingRecords = queueRecords.filter((record) => record.status === "REPLAYING");
  const resolvedRecords = queueRecords.filter((record) => record.status === "RESOLVED");
  const discardedRecords = queueRecords.filter((record) => record.status === "DISCARDED");
  const failedReplayRecords = queueRecords.filter((record) => record.replay_status === "FAILED");
  const openRecords = queueRecords.filter((record) => !isFoundryLiteRecordDlqTerminal(record));
  const selectedRecord =
    (selection.selectedRecordId
      ? queueRecords.find((record) => record.id === selection.selectedRecordId)
      : null) ??
    failedReplayRecords[0] ??
    openRecords[0] ??
    queueRecords[0] ??
    null;

  return {
    records: queueRecords,
    quarantinedRecords,
    replayRequestedRecords,
    replayingRecords,
    resolvedRecords,
    discardedRecords,
    failedReplayRecords,
    openRecords,
    selectedRecord,
    hasRecords: queueRecords.length > 0,
    hasOpenRecords: openRecords.length > 0,
    hasFailedReplay: failedReplayRecords.length > 0,
    canRetrySelected: selectedRecord ? canRetryFoundryLiteRecordDlq(selectedRecord) : false,
    canDiscardSelected: selectedRecord ? canDiscardFoundryLiteRecordDlq(selectedRecord) : false,
  };
}

export function useFoundryLiteRecordDlqQueue(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteRecordDlqQueueOptions = {},
): FoundryLiteRecordDlqQueueState {
  const { key, selectedRecordId, status, ...queryOptions } = options;
  const query = useFoundryLiteQuery(
    key ?? ["operations", "deadLetterRecords", status ?? null],
    () => client.operations.deadLetterRecords.list(status ? { status } : {}),
    queryOptions,
  );
  const selection = useMemo(() => ({ selectedRecordId }), [selectedRecordId]);
  const view = useMemo(() => foundryLiteRecordDlqQueueView(query.data, selection), [query.data, selection]);
  return { ...query, ...view };
}

export function useFoundryLiteProvidedRecordDlqQueue(
  options: FoundryLiteRecordDlqQueueOptions = {},
): FoundryLiteRecordDlqQueueState {
  const client = useFoundryLiteClient();
  return useFoundryLiteRecordDlqQueue(client, options);
}

export function useFoundryLiteRecordDlqControls(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
): FoundryLiteRecordDlqControlsState {
  const retry = useFoundryLiteMutation(
    (payload: FoundryLiteRecordDlqRetryPayload) =>
      client.operations.deadLetterRecords.retry(payload.id, { idempotencyKey: payload.idempotencyKey }),
    { lockKey: foundryLiteRecordDlqRetryLockKey },
  );
  const bulkRetry = useFoundryLiteMutation(
    (payload: FoundryLiteRecordDlqBulkRetryPayload) =>
      client.operations.deadLetterRecords.bulkRetry(payload.ids, {
        idempotencyKey: payload.idempotencyKey,
      }),
    { lockKey: foundryLiteRecordDlqBulkRetryLockKey },
  );
  const discard = useFoundryLiteMutation((payload: FoundryLiteRecordDlqDiscardPayload) =>
    client.operations.deadLetterRecords.discard(payload.id),
  );

  return {
    retry,
    bulkRetry,
    discard,
    isBusy: retry.isRunning || bulkRetry.isRunning || discard.isRunning,
    latestRetryResult: retry.result,
    latestBulkRetryResult: bulkRetry.result,
    latestDiscardResult: discard.result,
  };
}

export function useFoundryLiteProvidedRecordDlqControls(): FoundryLiteRecordDlqControlsState {
  const client = useFoundryLiteClient();
  return useFoundryLiteRecordDlqControls(client);
}

export type FoundryLiteWritebackReconciliationSelection = {
  selectedWritebackId?: string | null;
};

export type FoundryLiteWritebackReconciliationView = {
  result: ActionWritebackQueueResult | null;
  items: ActionWritebackQueueItem[];
  outcomeUnknownItems: ActionWritebackQueueItem[];
  compensationRequiredItems: ActionWritebackQueueItem[];
  retryableItems: ActionWritebackQueueItem[];
  selectedWriteback: ActionWritebackQueueItem | null;
  hasItems: boolean;
  hasOutcomeUnknown: boolean;
  hasCompensationRequired: boolean;
  hasRetryable: boolean;
  canResolveSelected: boolean;
};

export type FoundryLiteWritebackReconciliationOptions =
  FoundryLiteQueryOptions<ActionWritebackQueueResult> &
    ActionWritebackQueueFilters &
    FoundryLiteWritebackReconciliationSelection & {
      key?: readonly unknown[];
    };

export type FoundryLiteWritebackReconciliationState =
  FoundryLiteQueryState<ActionWritebackQueueResult> &
    FoundryLiteWritebackReconciliationView;

export type FoundryLiteWritebackResolvePayload = {
  writebackId: string;
  payload: ActionWritebackReconciliationRequest;
};

export type FoundryLiteWritebackResolveState =
  FoundryLiteMutationState<ActionWritebackReconciliationResult, FoundryLiteWritebackResolvePayload> & {
    reconciliation: ActionWritebackReconciliationResult | null;
    hasObjectEdit: boolean;
    alreadyReconciled: boolean;
  };

export function foundryLiteWritebackReconciliationView(
  result: ActionWritebackQueueResult | null | undefined,
  selection: FoundryLiteWritebackReconciliationSelection = {},
): FoundryLiteWritebackReconciliationView {
  const items = result?.items ?? [];
  const outcomeUnknownItems = items.filter((item) => item.status === "outcome_unknown");
  const compensationRequiredItems = items.filter((item) => item.status === "compensation_required");
  const retryableItems = items.filter((item) => item.status === "retryable");
  const selectedWriteback =
    (selection.selectedWritebackId
      ? items.find((item) => item.writebackId === selection.selectedWritebackId)
      : null) ??
    compensationRequiredItems[0] ??
    outcomeUnknownItems[0] ??
    retryableItems[0] ??
    items[0] ??
    null;

  return {
    result: result ?? null,
    items,
    outcomeUnknownItems,
    compensationRequiredItems,
    retryableItems,
    selectedWriteback,
    hasItems: items.length > 0,
    hasOutcomeUnknown: outcomeUnknownItems.length > 0,
    hasCompensationRequired: compensationRequiredItems.length > 0,
    hasRetryable: retryableItems.length > 0,
    canResolveSelected: selectedWriteback ? canResolveFoundryLiteWriteback(selectedWriteback) : false,
  };
}

export function useFoundryLiteWritebackReconciliationQueue(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteWritebackReconciliationOptions = {},
): FoundryLiteWritebackReconciliationState {
  const { key, limit, selectedWritebackId, status, ...queryOptions } = options;
  const filtersKey = stableWritebackReconciliationFiltersKey({ limit, status });
  const filters = useMemo(() => compactWritebackReconciliationFilters({ limit, status }), [filtersKey]);
  const selection = useMemo(() => ({ selectedWritebackId }), [selectedWritebackId]);
  const query = useFoundryLiteQuery(
    key ?? ["operations", "reconciliation", "writebacks", filters],
    () => client.operations.reconciliation.list(filters),
    queryOptions,
  );
  const view = useMemo(
    () => foundryLiteWritebackReconciliationView(query.data, selection),
    [query.data, selection],
  );
  return { ...query, ...view };
}

export function useFoundryLiteProvidedWritebackReconciliationQueue(
  options: FoundryLiteWritebackReconciliationOptions = {},
): FoundryLiteWritebackReconciliationState {
  const client = useFoundryLiteClient();
  return useFoundryLiteWritebackReconciliationQueue(client, options);
}

export function useFoundryLiteWritebackResolve(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteMutationOptions<ActionWritebackReconciliationResult, FoundryLiteWritebackResolvePayload> = {},
): FoundryLiteWritebackResolveState {
  const mutation = useFoundryLiteMutation(
    (payload: FoundryLiteWritebackResolvePayload) =>
      client.operations.reconciliation.resolve(payload.writebackId, payload.payload),
    {
      ...options,
      lockKey: options.lockKey ?? foundryLiteWritebackResolveLockKey,
    },
  );

  return {
    ...mutation,
    reconciliation: mutation.result,
    hasObjectEdit: Boolean(mutation.result?.objectEditId),
    alreadyReconciled: mutation.result?.alreadyReconciled ?? false,
  };
}

export function useFoundryLiteProvidedWritebackResolve(
  options: FoundryLiteMutationOptions<ActionWritebackReconciliationResult, FoundryLiteWritebackResolvePayload> = {},
): FoundryLiteWritebackResolveState {
  const client = useFoundryLiteClient();
  return useFoundryLiteWritebackResolve(client, options);
}

export type FoundryLiteObservabilityReportView = {
  report: ObservabilityReport | null;
  activeIncidents: ObservabilityIncident[];
  suppressedIncidents: ObservabilityIncident[];
  criticalIncidents: ObservabilityIncident[];
  warningIncidents: ObservabilityIncident[];
  infoIncidents: ObservabilityIncident[];
  highestSeverity: ObservabilitySeverity | null;
  hasActiveIncidents: boolean;
  hasCriticalIncidents: boolean;
  incidentCount: number;
};

export type FoundryLiteObservabilityDetectState =
  FoundryLiteMutationState<ObservabilityReport, ObservabilityDetectRequest> &
    FoundryLiteObservabilityReportView;

export type FoundryLiteIcebergMaintenancePlanView = {
  plan: IcebergMaintenancePlan | null;
  snapshots: IcebergMaintenancePlan["snapshots"];
  compactionCandidates: IcebergMaintenancePlan["compaction_candidates"];
  orphanSnapshots: IcebergMaintenancePlan["orphan_snapshots"];
  protectedSnapshotIds: number[];
  retainedSnapshotIds: number[];
  deletableSnapshotIds: number[];
  hasCompactionCandidates: boolean;
  hasOrphanSnapshots: boolean;
  hasDeletableSnapshots: boolean;
  isCurrentSnapshotProtected: boolean;
  candidateBytes: number;
};

export type FoundryLiteIcebergMaintenancePlanQueryOptions =
  FoundryLiteQueryOptions<IcebergMaintenancePlan> & {
    datasetRef?: string | null;
    planOptions?: IcebergMaintenancePlanOptions;
    key?: readonly unknown[];
  };

export type FoundryLiteIcebergMaintenancePlanState =
  FoundryLiteQueryState<IcebergMaintenancePlan> &
    FoundryLiteIcebergMaintenancePlanView;

export type FoundryLiteIcebergMaintenancePlanPayload = {
  datasetRef: string;
  options?: IcebergMaintenancePlanOptions;
};

export type FoundryLiteIndexReplayObjectTypePayload = {
  objectType: string;
};

export type FoundryLiteIndexReplayFailedRunPayload = {
  runId: string;
};

export type FoundryLiteTransformRetryPayload = {
  runId: string;
};

export type FoundryLiteMaintenanceControlsState = {
  requestIcebergPlan: FoundryLiteMutationState<
    IcebergMaintenancePlan,
    FoundryLiteIcebergMaintenancePlanPayload
  >;
  replayObjectTypeIndex: FoundryLiteMutationState<
    ObjectIndexRebuildResult,
    FoundryLiteIndexReplayObjectTypePayload
  >;
  replayFailedIndexRun: FoundryLiteMutationState<
    ObjectIndexRebuildResult,
    FoundryLiteIndexReplayFailedRunPayload
  >;
  retryTransformRun: FoundryLiteMutationState<TransformRetryResult, FoundryLiteTransformRetryPayload>;
  isBusy: boolean;
  latestIcebergPlan: IcebergMaintenancePlan | null;
  latestIndexReplay: ObjectIndexRebuildResult | null;
  latestTransformRetry: TransformRetryResult | null;
};

export function foundryLiteObservabilityReportView(
  report: ObservabilityReport | null | undefined,
): FoundryLiteObservabilityReportView {
  const activeIncidents = report?.activeIncidents ?? [];
  const suppressedIncidents = report?.suppressedIncidents ?? [];
  const criticalIncidents = activeIncidents.filter((incident) => incident.severity === "critical");
  const warningIncidents = activeIncidents.filter((incident) => incident.severity === "warning");
  const infoIncidents = activeIncidents.filter((incident) => incident.severity === "info");
  return {
    report: report ?? null,
    activeIncidents,
    suppressedIncidents,
    criticalIncidents,
    warningIncidents,
    infoIncidents,
    highestSeverity: readFoundryLiteHighestSeverity(activeIncidents),
    hasActiveIncidents: activeIncidents.length > 0,
    hasCriticalIncidents: criticalIncidents.length > 0,
    incidentCount: activeIncidents.length + suppressedIncidents.length,
  };
}

export function useFoundryLiteObservabilityDetect(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteMutationOptions<ObservabilityReport, ObservabilityDetectRequest> = {},
): FoundryLiteObservabilityDetectState {
  const mutation = useFoundryLiteMutation(
    (payload: ObservabilityDetectRequest) => client.operations.observability.detect(payload),
    options,
  );
  const view = useMemo(() => foundryLiteObservabilityReportView(mutation.result), [mutation.result]);
  return { ...mutation, ...view };
}

export function useFoundryLiteProvidedObservabilityDetect(
  options: FoundryLiteMutationOptions<ObservabilityReport, ObservabilityDetectRequest> = {},
): FoundryLiteObservabilityDetectState {
  const client = useFoundryLiteClient();
  return useFoundryLiteObservabilityDetect(client, options);
}

export function foundryLiteIcebergMaintenancePlanView(
  plan: IcebergMaintenancePlan | null | undefined,
): FoundryLiteIcebergMaintenancePlanView {
  const compactionCandidates = plan?.compaction_candidates ?? [];
  const orphanSnapshots = plan?.orphan_snapshots ?? [];
  const protectedSnapshotIds = plan?.protected_snapshot_ids ?? [];
  const currentSnapshotId = plan?.current_snapshot_id ?? null;
  return {
    plan: plan ?? null,
    snapshots: plan?.snapshots ?? [],
    compactionCandidates,
    orphanSnapshots,
    protectedSnapshotIds,
    retainedSnapshotIds: plan?.retained_snapshot_ids ?? [],
    deletableSnapshotIds: plan?.deletable_snapshot_ids ?? [],
    hasCompactionCandidates: compactionCandidates.length > 0,
    hasOrphanSnapshots: orphanSnapshots.length > 0,
    hasDeletableSnapshots: (plan?.deletable_snapshot_ids.length ?? 0) > 0,
    isCurrentSnapshotProtected:
      currentSnapshotId !== null && protectedSnapshotIds.includes(currentSnapshotId),
    candidateBytes: compactionCandidates.reduce((total, snapshot) => total + snapshot.total_bytes, 0),
  };
}

export function useFoundryLiteIcebergMaintenancePlan(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteIcebergMaintenancePlanQueryOptions = {},
): FoundryLiteIcebergMaintenancePlanState {
  const { datasetRef, key, planOptions = {}, ...queryOptions } = options;
  const planOptionsKey = stableIcebergMaintenancePlanOptionsKey(planOptions);
  const stablePlanOptions = useMemo(() => planOptions, [planOptionsKey]);
  const enabled = queryOptions.enabled ?? Boolean(datasetRef);
  const query = useFoundryLiteQuery(
    key ?? ["operations", "maintenance", "iceberg", datasetRef ?? null, stablePlanOptions],
    () => {
      if (!datasetRef) {
        throw new Error("datasetRef is required before loading an Iceberg maintenance plan");
      }
      return client.operations.icebergMaintenance.planReadOnly(datasetRef, stablePlanOptions);
    },
    { ...queryOptions, enabled },
  );
  const view = useMemo(() => foundryLiteIcebergMaintenancePlanView(query.data), [query.data]);
  return { ...query, ...view };
}

export function useFoundryLiteProvidedIcebergMaintenancePlan(
  options: FoundryLiteIcebergMaintenancePlanQueryOptions = {},
): FoundryLiteIcebergMaintenancePlanState {
  const client = useFoundryLiteClient();
  return useFoundryLiteIcebergMaintenancePlan(client, options);
}

export function useFoundryLiteMaintenanceControls(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
): FoundryLiteMaintenanceControlsState {
  const requestIcebergPlan = useFoundryLiteMutation(
    (payload: FoundryLiteIcebergMaintenancePlanPayload) =>
      client.operations.icebergMaintenance.plan(payload.datasetRef, payload.options ?? {}),
    { lockKey: foundryLiteIcebergMaintenancePlanLockKey },
  );
  const replayObjectTypeIndex = useFoundryLiteMutation(
    (payload: FoundryLiteIndexReplayObjectTypePayload) =>
      client.operations.index.replayObjectType(payload.objectType),
    { lockKey: foundryLiteIndexReplayObjectTypeLockKey },
  );
  const replayFailedIndexRun = useFoundryLiteMutation(
    (payload: FoundryLiteIndexReplayFailedRunPayload) =>
      client.operations.index.replayFailedRun(payload.runId),
    { lockKey: foundryLiteIndexReplayFailedRunLockKey },
  );
  const retryTransformRun = useFoundryLiteMutation(
    (payload: FoundryLiteTransformRetryPayload) => client.operations.transforms.retry(payload.runId),
    { lockKey: foundryLiteTransformRetryLockKey },
  );

  return {
    requestIcebergPlan,
    replayObjectTypeIndex,
    replayFailedIndexRun,
    retryTransformRun,
    isBusy:
      requestIcebergPlan.isRunning ||
      replayObjectTypeIndex.isRunning ||
      replayFailedIndexRun.isRunning ||
      retryTransformRun.isRunning,
    latestIcebergPlan: requestIcebergPlan.result,
    latestIndexReplay: replayFailedIndexRun.result ?? replayObjectTypeIndex.result,
    latestTransformRetry: retryTransformRun.result,
  };
}

export function useFoundryLiteProvidedMaintenanceControls(): FoundryLiteMaintenanceControlsState {
  const client = useFoundryLiteClient();
  return useFoundryLiteMaintenanceControls(client);
}

export type FoundryLiteAdminCapabilityGroups = AdminCapabilityGroups;
export type FoundryLiteAdminCapabilityView = AdminCapabilityView;
export type FoundryLiteAdminOverviewState = FoundryLiteQueryState<AdminReadinessOverview> & AdminReadinessScreen;
export type FoundryLiteAdminLaunchAction = {
  capability: AdminOperationCapability;
  view: FoundryLiteAdminCapabilityView;
  actionKind: AdminCapabilityActionKind;
  label: string;
  description: string;
  primarySurface: string | null;
  canRunInBrowser: boolean;
  canShowCommand: boolean;
  command: string | null;
  nextSurface: string | null;
  evidencePath: string | null;
  executionSurface: AdminOperationCapability["executionSurface"];
  requiresApproval: boolean;
  operatorChecklist: string[];
  blockingReason: string | null;
  disabledReason: string | null;
};

export type FoundryLiteAdminConsoleScreen = AdminReadinessScreen & {
  launchActions: FoundryLiteAdminLaunchAction[];
  browserActions: FoundryLiteAdminLaunchAction[];
  operatorCommandActions: FoundryLiteAdminLaunchAction[];
  futureActions: FoundryLiteAdminLaunchAction[];
  hasBrowserActions: boolean;
  hasOperatorCommands: boolean;
  hasFutureActions: boolean;
};

export type FoundryLiteAdminConsoleState =
  FoundryLiteQueryState<AdminReadinessOverview> &
    FoundryLiteAdminConsoleScreen;

export type FoundryLiteAdminOverviewOptions = FoundryLiteQueryOptions<AdminReadinessOverview> & {
  key?: readonly unknown[];
};

export type FoundryLiteAdminTaskPlanScreen = {
  plan: AdminTaskPlan | null;
  taskViews: AdminTaskView[];
  browserRunnableTaskViews: AdminTaskView[];
  operatorRequiredTaskViews: AdminTaskView[];
  futureBackendSurfaceTaskViews: AdminTaskView[];
  workerTaskViews: AdminTaskView[];
  migrationTaskViews: AdminTaskView[];
  bootstrapTaskViews: AdminTaskView[];
  hasBrowserRunnableTasks: boolean;
  hasOperatorRequiredTasks: boolean;
  hasFutureBackendSurfaces: boolean;
};

export type FoundryLiteAdminTaskPlanState =
  FoundryLiteQueryState<AdminTaskPlan> &
    FoundryLiteAdminTaskPlanScreen;

export type FoundryLiteAdminTaskPlanOptions = FoundryLiteQueryOptions<AdminTaskPlan> & {
  key?: readonly unknown[];
};

export type FoundryLiteAdminOperationsBoardData = {
  overview: AdminReadinessOverview;
  taskPlan: AdminTaskPlan;
};

export type FoundryLiteAdminOperationsBoardState =
  FoundryLiteQueryState<FoundryLiteAdminOperationsBoardData> &
    AdminOperationsBoard;

export type FoundryLiteAdminOperationsBoardOptions =
  FoundryLiteQueryOptions<FoundryLiteAdminOperationsBoardData> & {
    key?: readonly unknown[];
  };

export type FoundryLiteAdminLaunchModelState =
  FoundryLiteQueryState<FoundryLiteAdminOperationsBoardData> &
    AdminOperationsLaunchModel;

export type FoundryLiteAdminLaunchModelOptions = FoundryLiteAdminOperationsBoardOptions;

export type FoundryLiteAdminLaunchpadState =
  FoundryLiteQueryState<FoundryLiteAdminOperationsBoardData> &
    AdminOperationsLaunchpadModel;

export type FoundryLiteAdminLaunchpadOptions = FoundryLiteAdminOperationsBoardOptions;

export type FoundryLiteAdminCommandCenterOptions =
  FoundryLiteAdminOperationsBoardOptions &
    AdminCommandCenterSelection;

export type FoundryLiteAdminCommandCenterState =
  FoundryLiteAdminLaunchModelState &
    AdminCommandCenterView & {
      commandCenter: AdminCommandCenterView;
    };

export type FoundryLiteAdminInternalOperationsWorkbenchOptions =
  FoundryLiteAdminOperationsBoardOptions &
    AdminInternalOperationsWorkbenchSelection;

export type FoundryLiteAdminInternalOperationsWorkbenchState =
  FoundryLiteAdminLaunchModelState &
    AdminInternalOperationsWorkbenchView & {
      internalOperationsWorkbench: AdminInternalOperationsWorkbenchView;
    };

export type FoundryLiteAdminInternalOperationsWorkbenchSurface = Pick<
  AdminInternalOperationsWorkbenchView,
  | "selectedAreaId"
  | "selectedSection"
  | "visibleCommandCards"
  | "privilegedCommandCards"
  | "readiness"
>;

export type FoundryLiteAdminLaunchSections = Pick<
  AdminOperationsLaunchModel,
  | "browserActions"
  | "operatorCommands"
  | "migrationCommands"
  | "workerCommands"
  | "bootstrapCommands"
  | "operatorCommandCards"
  | "migrationCommandCards"
  | "workerCommandCards"
  | "bootstrapCommandCards"
  | "futureSurfaceActions"
  | "requiredBackendSurfaces"
>;

export function groupFoundryLiteAdminCapabilities(
  overview: AdminReadinessOverview | null | undefined,
): FoundryLiteAdminCapabilityGroups {
  return groupAdminCapabilities(overview);
}

export function foundryLiteAdminCapabilityView(
  capability: AdminOperationCapability,
): FoundryLiteAdminCapabilityView {
  return adminCapabilityView(capability);
}

export function foundryLiteAdminLaunchAction(
  capability: AdminOperationCapability,
): FoundryLiteAdminLaunchAction {
  const view = foundryLiteAdminCapabilityView(capability);
  return {
    capability,
    view,
    actionKind: view.actionKind,
    label: foundryLiteAdminLaunchLabel(view.actionKind),
    description: foundryLiteAdminLaunchDescription(view),
    primarySurface: view.primarySurface,
    canRunInBrowser: view.canRenderActionButton,
    canShowCommand: view.isWorkerEntrypoint || view.isCliOnly || view.isRunbookOnly,
    command: capability.command ?? null,
    nextSurface: capability.nextSurface ?? null,
    evidencePath: capability.evidencePath ?? null,
    executionSurface: view.executionSurface,
    requiresApproval: view.requiresApproval,
    operatorChecklist: view.operatorChecklist,
    blockingReason: view.blockingReason,
    disabledReason: view.disabledReason,
  };
}

export function foundryLiteAdminConsoleScreen(
  overview: AdminReadinessOverview | null | undefined,
): FoundryLiteAdminConsoleScreen {
  const screen = adminReadinessScreen(overview);
  const launchActions = screen.capabilityViews.map((view) =>
    foundryLiteAdminLaunchAction(view.capability),
  );
  const browserActions = launchActions.filter((action) => action.canRunInBrowser);
  const operatorCommandActions = launchActions.filter((action) => action.canShowCommand);
  const futureActions = launchActions.filter((action) => action.actionKind === "future");
  return {
    ...screen,
    launchActions,
    browserActions,
    operatorCommandActions,
    futureActions,
    hasBrowserActions: browserActions.length > 0,
    hasOperatorCommands: operatorCommandActions.length > 0,
    hasFutureActions: futureActions.length > 0,
  };
}

export function foundryLiteAdminTaskPlanScreen(
  plan: AdminTaskPlan | null | undefined,
): FoundryLiteAdminTaskPlanScreen {
  return adminTaskPlanScreen(plan);
}

export function foundryLiteAdminOperationsBoard(
  overview: AdminReadinessOverview | null | undefined,
  plan: AdminTaskPlan | null | undefined,
): AdminOperationsBoard {
  return adminOperationsBoard(overview, plan);
}

export function foundryLiteAdminLaunchModel(
  overview: AdminReadinessOverview | null | undefined,
  plan: AdminTaskPlan | null | undefined,
): AdminOperationsLaunchModel {
  return adminOperationsLaunchModel(foundryLiteAdminOperationsBoard(overview, plan));
}

export function foundryLiteAdminLaunchpad(
  overview: AdminReadinessOverview | null | undefined,
  plan: AdminTaskPlan | null | undefined,
): AdminOperationsLaunchpadModel {
  return adminOperationsLaunchpad(foundryLiteAdminOperationsBoard(overview, plan));
}

export function foundryLiteAdminLaunchSections(
  model: AdminOperationsLaunchModel,
): FoundryLiteAdminLaunchSections {
  return {
    browserActions: model.browserActions,
    operatorCommands: model.operatorCommands,
    migrationCommands: model.migrationCommands,
    workerCommands: model.workerCommands,
    bootstrapCommands: model.bootstrapCommands,
    operatorCommandCards: model.operatorCommandCards,
    migrationCommandCards: model.migrationCommandCards,
    workerCommandCards: model.workerCommandCards,
    bootstrapCommandCards: model.bootstrapCommandCards,
    futureSurfaceActions: model.futureSurfaceActions,
    requiredBackendSurfaces: model.requiredBackendSurfaces,
  };
}

export function foundryLiteAdminCommandCenter(
  model: AdminOperationsLaunchModel,
  selection: AdminCommandCenterSelection = {},
): AdminCommandCenterView {
  return adminCommandCenter(model, selection);
}

export function foundryLiteAdminInternalOperationsWorkbench(
  board: AdminOperationsBoard,
  selection: AdminInternalOperationsWorkbenchSelection = {},
): AdminInternalOperationsWorkbenchView {
  return adminInternalOperationsWorkbench(board, selection);
}

export function useFoundryLiteAdminOverview(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminOverviewOptions = {},
): FoundryLiteAdminOverviewState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "admin", "overview"],
    () => client.operations.admin.overview(),
    options,
  );
  const screen = useMemo(() => adminReadinessScreen(query.data), [query.data]);

  return {
    ...query,
    ...screen,
  };
}

export function useFoundryLiteAdminConsole(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminOverviewOptions = {},
): FoundryLiteAdminConsoleState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "admin", "overview"],
    () => client.operations.admin.overview(),
    options,
  );
  const screen = useMemo(() => foundryLiteAdminConsoleScreen(query.data), [query.data]);
  return {
    ...query,
    ...screen,
  };
}

export function useFoundryLiteAdminTaskPlan(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminTaskPlanOptions = {},
): FoundryLiteAdminTaskPlanState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "admin", "task-plan"],
    () => client.operations.admin.taskPlan(),
    options,
  );
  const screen = useMemo(() => foundryLiteAdminTaskPlanScreen(query.data), [query.data]);
  return {
    ...query,
    ...screen,
  };
}

export function useFoundryLiteAdminOperationsBoard(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminOperationsBoardOptions = {},
): FoundryLiteAdminOperationsBoardState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "admin", "operations-board"],
    async () => {
      const [overview, taskPlan] = await Promise.all([
        client.operations.admin.overview(),
        client.operations.admin.taskPlan(),
      ]);
      return { overview, taskPlan };
    },
    options,
  );
  const board = useMemo(
    () => foundryLiteAdminOperationsBoard(query.data?.overview, query.data?.taskPlan),
    [query.data],
  );
  return {
    ...query,
    ...board,
  };
}

export function useFoundryLiteAdminLaunchModel(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminLaunchModelOptions = {},
): FoundryLiteAdminLaunchModelState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "admin", "launch-model"],
    async () => {
      const [overview, taskPlan] = await Promise.all([
        client.operations.admin.overview(),
        client.operations.admin.taskPlan(),
      ]);
      return { overview, taskPlan };
    },
    options,
  );
  const launchModel = useMemo(
    () => foundryLiteAdminLaunchModel(query.data?.overview, query.data?.taskPlan),
    [query.data],
  );
  return {
    ...query,
    ...launchModel,
  };
}

export function useFoundryLiteProvidedAdminLaunchModel(
  options: FoundryLiteAdminLaunchModelOptions = {},
): FoundryLiteAdminLaunchModelState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAdminLaunchModel(client, options);
}

export function useFoundryLiteAdminLaunchpad(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminLaunchpadOptions = {},
): FoundryLiteAdminLaunchpadState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "admin", "launchpad"],
    async () => {
      const [overview, taskPlan] = await Promise.all([
        client.operations.admin.overview(),
        client.operations.admin.taskPlan(),
      ]);
      return { overview, taskPlan };
    },
    options,
  );
  const launchpad = useMemo(
    () => foundryLiteAdminLaunchpad(query.data?.overview, query.data?.taskPlan),
    [query.data],
  );
  return {
    ...query,
    ...launchpad,
  };
}

export function useFoundryLiteProvidedAdminLaunchpad(
  options: FoundryLiteAdminLaunchpadOptions = {},
): FoundryLiteAdminLaunchpadState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAdminLaunchpad(client, options);
}

export function useFoundryLiteAdminCommandCenter(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminCommandCenterOptions = {},
): FoundryLiteAdminCommandCenterState {
  const { sectionId, query, selectedCommandId, includeBlocked, ...queryOptions } = options;
  const launchModel = useFoundryLiteAdminLaunchModel(client, queryOptions);
  const commandCenter = useMemo(
    () =>
      foundryLiteAdminCommandCenter(launchModel, {
        sectionId,
        query,
        selectedCommandId,
        includeBlocked,
      }),
    [includeBlocked, launchModel, query, sectionId, selectedCommandId],
  );
  return {
    ...launchModel,
    ...commandCenter,
    commandCenter,
  };
}

export function useFoundryLiteProvidedAdminCommandCenter(
  options: FoundryLiteAdminCommandCenterOptions = {},
): FoundryLiteAdminCommandCenterState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAdminCommandCenter(client, options);
}

export function useFoundryLiteAdminInternalOperationsWorkbench(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteAdminInternalOperationsWorkbenchOptions = {},
): FoundryLiteAdminInternalOperationsWorkbenchState {
  const {
    selectedAreaId,
    sectionId,
    query,
    selectedCommandId,
    includeBlocked,
    ...queryOptions
  } = options;
  const launchModel = useFoundryLiteAdminLaunchModel(client, queryOptions);
  const internalOperationsWorkbench = useMemo(
    () =>
      foundryLiteAdminInternalOperationsWorkbench(launchModel.board, {
        selectedAreaId,
        sectionId,
        query,
        selectedCommandId,
        includeBlocked,
      }),
    [
      includeBlocked,
      launchModel.board,
      query,
      sectionId,
      selectedAreaId,
      selectedCommandId,
    ],
  );
  return {
    ...launchModel,
    ...internalOperationsWorkbench,
    internalOperationsWorkbench,
  };
}

export function useFoundryLiteProvidedAdminInternalOperationsWorkbench(
  options: FoundryLiteAdminInternalOperationsWorkbenchOptions = {},
): FoundryLiteAdminInternalOperationsWorkbenchState {
  const client = useFoundryLiteClient();
  return useFoundryLiteAdminInternalOperationsWorkbench(client, options);
}

export type FoundryLiteRecoveryPhase =
  | "normal"
  | "preflight_blocked"
  | "restore_paused"
  | "resume_ready"
  | "action_required"
  | "unknown";

export type FoundryLiteRecoveryOverviewView = {
  overview: BackupRestoreRecoveryOverview | null;
  phase: FoundryLiteRecoveryPhase;
  activeRestoreId: string | null;
  latestRestoreId: string | null;
  latestBackupId: string | null;
  requiredOperatorActions: string[];
  hasRequiredOperatorActions: boolean;
  isWriteTrafficPaused: boolean;
  isOutboxPublisherPaused: boolean;
  canApproveResume: boolean;
};

export type FoundryLiteRecoveryOverviewState =
  FoundryLiteQueryState<BackupRestoreRecoveryOverview> &
    FoundryLiteRecoveryOverviewView;

export type FoundryLiteRecoveryOverviewOptions = FoundryLiteQueryOptions<BackupRestoreRecoveryOverview> & {
  key?: readonly unknown[];
};

export function foundryLiteRecoveryOverviewView(
  overview: BackupRestoreRecoveryOverview | null | undefined,
): FoundryLiteRecoveryOverviewView {
  const activeRestoreMode = overview?.activeRestoreMode ?? null;
  const latestRestoreMode = overview?.latestRestoreMode ?? null;
  const requiredOperatorActions = overview?.requiredOperatorActions ?? [];
  const phase = foundryLiteRecoveryPhase(overview);
  return {
    overview: overview ?? null,
    phase,
    activeRestoreId: activeRestoreMode?.restoreId ?? null,
    latestRestoreId: latestRestoreMode?.restoreId ?? null,
    latestBackupId: activeRestoreMode?.backupId ?? latestRestoreMode?.backupId ?? overview?.latestPreflight?.backupId ?? null,
    requiredOperatorActions,
    hasRequiredOperatorActions: requiredOperatorActions.length > 0,
    isWriteTrafficPaused: activeRestoreMode?.is_write_traffic_paused ?? false,
    isOutboxPublisherPaused: activeRestoreMode?.is_outbox_publisher_paused ?? false,
    canApproveResume: activeRestoreMode?.status === "paused" && activeRestoreMode.is_operator_approval_required,
  };
}

export function foundryLiteRecoveryPhase(
  overview: BackupRestoreRecoveryOverview | null | undefined,
): FoundryLiteRecoveryPhase {
  const activeRestoreMode = overview?.activeRestoreMode ?? null;
  if (activeRestoreMode?.status === "resume_approved") return "resume_ready";
  if (activeRestoreMode?.status === "paused") return "restore_paused";
  if (activeRestoreMode?.status === "blocked") return "preflight_blocked";
  if ((overview?.requiredOperatorActions ?? []).length > 0) return "action_required";
  if (overview) return "normal";
  return "unknown";
}

export function useFoundryLiteRecoveryOverview(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteRecoveryOverviewOptions = {},
): FoundryLiteRecoveryOverviewState {
  const query = useFoundryLiteQuery(
    options.key ?? ["operations", "backupRestore", "recoveryOverview"],
    () => client.operations.backupRestore.recoveryOverview(),
    options,
  );
  const view = useMemo(() => foundryLiteRecoveryOverviewView(query.data), [query.data]);
  return { ...query, ...view };
}

export type FoundryLiteBackupRestorePreflightState =
  FoundryLiteMutationState<BackupRestorePreflightReport, BackupRestorePreflightRequest | undefined> & {
    report: BackupRestorePreflightReport | null;
    isReady: boolean;
    isBlocked: boolean;
    issueCount: number;
  };

export function useFoundryLiteBackupRestorePreflight(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteMutationOptions<BackupRestorePreflightReport, BackupRestorePreflightRequest | undefined> = {},
): FoundryLiteBackupRestorePreflightState {
  const mutation = useFoundryLiteMutation(
    (payload: BackupRestorePreflightRequest | undefined) => client.operations.backupRestore.preflight(payload),
    options,
  );
  const report = mutation.result;
  return {
    ...mutation,
    report,
    isReady: report?.status === "ready",
    isBlocked: report?.status === "blocked",
    issueCount: report?.issues.length ?? 0,
  };
}

export type FoundryLiteRestoreModeValidationPayload = {
  restoreId: string;
  payload?: BackupRestorePostRestoreValidationRequest;
};

export type FoundryLiteRestoreResumeApprovalPayload = {
  restoreId: string;
  payload?: BackupRestoreResumeApprovalRequest;
};

export type FoundryLiteRestoreModeControlsState = {
  start: FoundryLiteMutationState<BackupRestoreModeReport, BackupRestoreModeStartRequest | undefined>;
  postRestoreValidation: FoundryLiteMutationState<
    BackupRestorePostRestoreValidationReport,
    FoundryLiteRestoreModeValidationPayload
  >;
  approveResume: FoundryLiteMutationState<BackupRestoreModeReport, FoundryLiteRestoreResumeApprovalPayload>;
  isBusy: boolean;
  latestModeReport: BackupRestoreModeReport | null;
  latestValidationReport: BackupRestorePostRestoreValidationReport | null;
};

export function useFoundryLiteRestoreModeControls(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
): FoundryLiteRestoreModeControlsState {
  const start = useFoundryLiteMutation((payload: BackupRestoreModeStartRequest | undefined) =>
    client.operations.backupRestore.startRestoreMode(payload),
  );
  const postRestoreValidation = useFoundryLiteMutation((payload: FoundryLiteRestoreModeValidationPayload) =>
    client.operations.backupRestore.postRestoreValidation(payload.restoreId, payload.payload),
  );
  const approveResume = useFoundryLiteMutation((payload: FoundryLiteRestoreResumeApprovalPayload) =>
    client.operations.backupRestore.approveResume(payload.restoreId, payload.payload),
  );

  return {
    start,
    postRestoreValidation,
    approveResume,
    isBusy: start.isRunning || postRestoreValidation.isRunning || approveResume.isRunning,
    latestModeReport: approveResume.result ?? start.result,
    latestValidationReport: postRestoreValidation.result,
  };
}

export type FoundryLiteOutboxPublishState =
  FoundryLiteMutationState<OutboxPublishBatchResult, OutboxPublishRequest | undefined> & {
    batch: OutboxPublishBatchResult | null;
    hasFailures: boolean;
    deadLetterEventIds: string[];
  };

export function useFoundryLiteOutboxPublish(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
  options: FoundryLiteMutationOptions<OutboxPublishBatchResult, OutboxPublishRequest | undefined> = {},
): FoundryLiteOutboxPublishState {
  const mutation = useFoundryLiteMutation(
    (payload: OutboxPublishRequest | undefined) => client.operations.outbox.publishPending(payload),
    {
      ...options,
      lockKey: options.lockKey ?? foundryLiteOutboxPublishLockKey,
    },
  );
  return {
    ...mutation,
    batch: mutation.result,
    hasFailures: (mutation.result?.failed ?? 0) > 0,
    deadLetterEventIds: mutation.result?.deadLetterEventIds ?? [],
  };
}

export function foundryLiteOutboxPublishLockKey(payload: OutboxPublishRequest | undefined): string {
  return `operations:outbox:publish:${payload?.streamName ?? "default"}`;
}

export function foundryLiteRecordDlqRetryLockKey(payload: FoundryLiteRecordDlqRetryPayload): string {
  return `operations:record-dlq:retry:${payload.id}`;
}

export function foundryLiteRecordDlqBulkRetryLockKey(payload: FoundryLiteRecordDlqBulkRetryPayload): string {
  return `operations:record-dlq:bulk-retry:${payload.ids.slice().sort().join(",")}`;
}

export function foundryLiteWritebackResolveLockKey(payload: FoundryLiteWritebackResolvePayload): string {
  return `operations:writeback-reconciliation:resolve:${payload.writebackId}`;
}

export function foundryLiteIcebergMaintenancePlanLockKey(
  payload: FoundryLiteIcebergMaintenancePlanPayload,
): string {
  return `operations:iceberg-maintenance:plan:${payload.datasetRef}`;
}

export function foundryLiteIndexReplayObjectTypeLockKey(
  payload: FoundryLiteIndexReplayObjectTypePayload,
): string {
  return `operations:index:replay-object-type:${payload.objectType}`;
}

export function foundryLiteIndexReplayFailedRunLockKey(
  payload: FoundryLiteIndexReplayFailedRunPayload,
): string {
  return `operations:index:replay-failed-run:${payload.runId}`;
}

export function foundryLiteTransformRetryLockKey(payload: FoundryLiteTransformRetryPayload): string {
  return `operations:transform:retry:${payload.runId}`;
}

function readFoundryLiteHighestSeverity(
  incidents: ObservabilityIncident[],
): ObservabilitySeverity | null {
  if (incidents.some((incident) => incident.severity === "critical")) return "critical";
  if (incidents.some((incident) => incident.severity === "warning")) return "warning";
  if (incidents.some((incident) => incident.severity === "info")) return "info";
  return null;
}

function stableIcebergMaintenancePlanOptionsKey(options: IcebergMaintenancePlanOptions): string {
  return JSON.stringify({
    branch: options.branch ?? null,
    smallFileThresholdBytes: options.smallFileThresholdBytes ?? null,
    fileCountThreshold: options.fileCountThreshold ?? null,
    readAmplificationThreshold: options.readAmplificationThreshold ?? null,
    retentionMinSnapshots: options.retentionMinSnapshots ?? null,
  });
}

function isFoundryLiteRecordDlqTerminal(record: DeadLetterRecord): boolean {
  return record.status === "RESOLVED" || record.status === "DISCARDED";
}

function canRetryFoundryLiteRecordDlq(record: DeadLetterRecord): boolean {
  return record.status === "QUARANTINED" || record.status === "REPLAY_REQUESTED" || record.replay_status === "FAILED";
}

function canDiscardFoundryLiteRecordDlq(record: DeadLetterRecord): boolean {
  return !isFoundryLiteRecordDlqTerminal(record);
}

function compactWritebackReconciliationFilters(
  filters: ActionWritebackQueueFilters,
): ActionWritebackQueueFilters {
  return {
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.limit !== undefined ? { limit: filters.limit } : {}),
  };
}

function stableWritebackReconciliationFiltersKey(filters: ActionWritebackQueueFilters): string {
  return JSON.stringify(compactWritebackReconciliationFilters(filters));
}

function canResolveFoundryLiteWriteback(item: ActionWritebackQueueItem): boolean {
  return item.status === "outcome_unknown" || item.status === "compensation_required";
}

function resolveActionIdempotencyKey<TAction extends OsdkActionType>(
  idempotencyKey: FoundryLiteActionSubmitOptions<TAction>["idempotencyKey"],
  payload: OsdkActionPayload<TAction>,
): string | undefined {
  return typeof idempotencyKey === "function" ? idempotencyKey(payload) : idempotencyKey;
}

function stableOsdkFetchOptionsKey(options: OsdkFetchPageOptions): string {
  return JSON.stringify({
    where: options.where ?? null,
    orderBy: options.orderBy ?? null,
    pageSize: options.pageSize ?? null,
    pageToken: options.pageToken ?? null,
    search: options.search ?? null,
  });
}

function stableObjectQueryRequestKey(
  options: Pick<
    FoundryLiteGenericObjectQueryOptions,
    "where" | "orderBy" | "pageSize" | "pageToken" | "search"
  >,
): string {
  return JSON.stringify(foundryLiteGenericObjectQueryRequest(options));
}

function stableMediaSearchPayloadKey(payload: MediaSearchRequest): string {
  return JSON.stringify({
    text: payload.text ?? null,
    topK: payload.topK ?? null,
    allowedClassifications: payload.allowedClassifications ?? null,
  });
}

function stableDatasetExplorerSelectionKey(selection: FoundryLiteDatasetExplorerSelection): string {
  return JSON.stringify({
    namespace: selection.namespace ?? null,
    name: selection.name ?? null,
    version: selection.version ?? null,
    previewLimit: selection.previewLimit ?? null,
    lineageResourceId: selection.lineageResourceId ?? null,
  });
}

function isFoundryLiteDatasetSelectionMissingError(error: unknown): boolean {
  const normalized = normalizeFoundryLiteError(error);
  return (
    normalized.code === "NOT_FOUND" &&
    normalized.message.toLowerCase().includes("dataset not found")
  );
}

async function loadFoundryLiteDatasetExplorerData(
  client: Pick<FoundryLiteGeneratedClient, "datasets" | "operations">,
  selection: FoundryLiteDatasetExplorerSelection,
): Promise<FoundryLiteDatasetExplorerData> {
  const datasetsPromise = client.datasets.list();
  if (!selection.namespace || !selection.name) {
    return { ...EMPTY_DATASET_EXPLORER_DATA, datasets: await datasetsPromise };
  }
  const datasets = await datasetsPromise;
  const selectedExists = datasets.some(
    (dataset) =>
      dataset.namespace === selection.namespace &&
      dataset.name === selection.name,
  );
  if (!selectedExists) {
    return { ...EMPTY_DATASET_EXPLORER_DATA, datasets };
  }
  const inspectOptions = selection.version
    ? { version: selection.version }
    : undefined;
  const lineageResourceId = foundryLiteDatasetExplorerResourceId(selection);
  const selectedDatasetPromise = Promise.all([
    client.datasets.versions(selection.namespace, selection.name),
    client.datasets.inspect(selection.namespace, selection.name, inspectOptions),
    client.datasets.preview(selection.namespace, selection.name, {
      limit: selection.previewLimit ?? 100,
    }),
    client.datasets.qualityResults.summary(selection.namespace, selection.name),
    lineageResourceId
      ? client.operations.lineage.get(lineageResourceId)
      : Promise.resolve([]),
  ]);
  try {
    const [versions, inspection, previewRows, qualitySummary, lineage] =
      await selectedDatasetPromise;
    return {
      datasets,
      versions,
      inspection,
      previewRows,
      qualitySummary,
      lineage,
    };
  } catch (caught) {
    if (isFoundryLiteDatasetSelectionMissingError(caught)) {
      return { ...EMPTY_DATASET_EXPLORER_DATA, datasets };
    }
    throw caught;
  }
}

function readFoundryLiteSelectedDatasetVersion(
  versions: DatasetVersion[],
  selectedVersion: string | null | undefined,
): DatasetVersion | null {
  if (!selectedVersion) return readFoundryLiteLatestDatasetVersion(versions);
  return (
    versions.find(
      (version) =>
        version.id === selectedVersion ||
        String(version.version_number) === selectedVersion ||
        `v${version.version_number}` === selectedVersion,
    ) ?? null
  );
}

function readFoundryLiteLatestDatasetVersion(versions: DatasetVersion[]): DatasetVersion | null {
  if (versions.length === 0) return null;
  return versions.reduce((latest, version) =>
    version.version_number > latest.version_number ? version : latest,
  );
}

function stableInsightReviewFiltersKey(filters: InsightReviewListFilters): string {
  return JSON.stringify({
    status: filters.status ?? null,
    assigneeUserId: filters.assigneeUserId ?? null,
    limit: filters.limit ?? null,
  });
}

function foundryLiteInsightReviewCreateRequest(
  payload: FoundryLiteInsightReviewCreatePayload,
): InsightReviewCreateRequest {
  return {
    claimId: payload.claimId,
    claimText: payload.claimText,
    evidenceObjectIds: payload.evidenceObjectIds,
    evidenceRefs: payload.evidenceRefs,
    priority: payload.priority,
    assigneeUserId: payload.assigneeUserId,
    actionProposal: payload.actionProposal,
  };
}

function foundryLiteInsightReviewMutationState<TPayload>(
  mutation: FoundryLiteMutationState<InsightReviewPayload, TPayload>,
): FoundryLiteInsightReviewMutationState<TPayload> {
  return {
    ...mutation,
    review: mutation.result,
    status: mutation.result?.status ?? null,
    isTerminal: mutation.result ? mutation.result.status !== "pending" : false,
  };
}

function readFoundryLiteCursorItems<TPage, TItem>(
  page: TPage,
  getItems: ((page: TPage) => TItem[]) | undefined,
): TItem[] {
  if (getItems) return getItems(page);
  if (!isFoundryLiteRecord(page)) return [];
  const items = page.items ?? page.data;
  return Array.isArray(items) ? (items as TItem[]) : [];
}

function readFoundryLiteCursorNextCursor<TPage>(
  page: TPage,
  getNextCursor: ((page: TPage) => string | null | undefined) | undefined,
): string | null {
  if (getNextCursor) return getNextCursor(page) ?? null;
  if (!isFoundryLiteRecord(page)) return null;
  const cursor = page.nextCursor ?? page.nextPageToken;
  return typeof cursor === "string" && cursor.length > 0 ? cursor : null;
}

function isFoundryLiteRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function foundryLiteNowIso(): string {
  return new Date().toISOString();
}

function foundryLiteOntologyObjectView(
  objectType: OntologyCatalogObject,
  actions: FoundryLiteOntologyActionView[],
  links: OntologyCatalogLink[],
): FoundryLiteOntologyObjectView {
  const properties = objectType.properties.map((property) =>
    foundryLiteOntologyPropertyView(property, objectType.primaryKeyProperty),
  );
  const generatedObjectType = readGeneratedObjectType(objectType.apiName);
  const linksFrom = links.filter((link) => link.fromObjectType === objectType.apiName);
  const linksTo = links.filter((link) => link.toObjectType === objectType.apiName);
  return {
    objectType,
    apiName: objectType.apiName,
    displayName: objectType.displayName,
    description: objectType.description,
    primaryKeyProperty: objectType.primaryKeyProperty,
    generatedObjectType,
    isGeneratedObjectTypeAvailable: generatedObjectType !== null,
    properties,
    searchableProperties: properties.filter((property) => property.isSearchable),
    editableProperties: properties.filter((property) => property.isEditable),
    classifiedProperties: properties.filter((property) => property.isClassified),
    actions,
    linksFrom,
    linksTo,
    propertyCount: properties.length,
    actionCount: actions.length,
    linkCount: linksFrom.length + linksTo.length,
  };
}

function foundryLiteOntologyActionView(
  action: OntologyCatalogAction,
): FoundryLiteOntologyActionView {
  const generatedActionType = readGeneratedActionType(action.apiName);
  return {
    action,
    apiName: action.apiName,
    displayName: action.displayName,
    targetObjectApiName: action.target,
    isEnabled: action.enabled,
    parameterCount: foundryLiteSchemaPropertyCount(action.parameterSchema),
    generatedActionType,
    isGeneratedActionTypeAvailable: generatedActionType !== null,
  };
}

function foundryLiteOntologyPropertyView(
  property: OntologyCatalogProperty,
  primaryKeyProperty: string,
): FoundryLiteOntologyPropertyView {
  return {
    property,
    apiName: property.apiName,
    displayName: property.displayName,
    dataType: property.dataType,
    isPrimaryKey: property.apiName === primaryKeyProperty,
    isSearchable: property.searchable,
    isEditable: property.editable,
    isClassified: property.classification !== null,
    classification: property.classification,
  };
}

function groupFoundryLiteActionsByTarget(
  actionViews: FoundryLiteOntologyActionView[],
): Record<string, FoundryLiteOntologyActionView[]> {
  const grouped: Record<string, FoundryLiteOntologyActionView[]> = {};
  for (const actionView of actionViews) {
    grouped[actionView.targetObjectApiName] ??= [];
    grouped[actionView.targetObjectApiName].push(actionView);
  }
  return grouped;
}

function normalizeFoundryLiteOntologySearch(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function filterFoundryLiteOntologyObjectViews(
  objectViews: FoundryLiteOntologyObjectView[],
  search: string,
): FoundryLiteOntologyObjectView[] {
  if (!search) return objectViews;
  return objectViews.filter((objectView) => isFoundryLiteObjectViewSearchMatch(objectView, search));
}

function filterFoundryLiteOntologyActionViews(
  actionViews: FoundryLiteOntologyActionView[],
  search: string,
): FoundryLiteOntologyActionView[] {
  if (!search) return actionViews;
  return actionViews.filter((actionView) => isFoundryLiteActionViewSearchMatch(actionView, search));
}

function isFoundryLiteObjectViewSearchMatch(
  objectView: FoundryLiteOntologyObjectView,
  search: string,
): boolean {
  return [
    objectView.apiName,
    objectView.displayName,
    objectView.description ?? "",
    objectView.primaryKeyProperty,
    ...objectView.properties.map((property) => property.apiName),
    ...objectView.properties.map((property) => property.displayName),
  ].some((value) => value.toLowerCase().includes(search));
}

function isFoundryLiteActionViewSearchMatch(
  actionView: FoundryLiteOntologyActionView,
  search: string,
): boolean {
  return [
    actionView.apiName,
    actionView.displayName,
    actionView.targetObjectApiName,
  ].some((value) => value.toLowerCase().includes(search));
}

function readFoundryLiteSelectedObjectView(
  workspace: FoundryLiteOntologyWorkspace,
  apiName: string | null | undefined,
): FoundryLiteOntologyObjectView | null {
  return apiName ? workspace.objectViewsByApiName[apiName] ?? null : null;
}

function readFoundryLiteSelectedActionView(
  workspace: FoundryLiteOntologyWorkspace,
  apiName: string | null | undefined,
): FoundryLiteOntologyActionView | null {
  return apiName ? workspace.actionViewsByApiName[apiName] ?? null : null;
}

function hasFoundryLiteSelectedActionOutsideObject(
  objectView: FoundryLiteOntologyObjectView | null,
  actionView: FoundryLiteOntologyActionView | null,
): boolean {
  return Boolean(objectView && actionView && actionView.targetObjectApiName !== objectView.apiName);
}

function foundryLiteOntologySdkRegenerationHint(
  workspace: FoundryLiteOntologyWorkspace,
): string | null {
  if (!workspace.hasDynamicOnlyTypes) return null;
  return [
    `${workspace.dynamicOnlyObjectViews.length} object type(s)`,
    `${workspace.dynamicOnlyActionViews.length} action type(s)`,
    "exist only in the live ontology catalog. Regenerate the SDK to use static OSDK types.",
  ].join(" ");
}

function foundryLiteOntologyActionSdkStatus(
  actionView: FoundryLiteOntologyActionView,
): FoundryLiteOntologyActionSdkStatus {
  if (!actionView.isEnabled) return "disabled";
  return actionView.isGeneratedActionTypeAvailable ? "generated" : "dynamic_only";
}

function foundryLiteOntologyActionBadge(status: FoundryLiteOntologyActionSdkStatus): string {
  switch (status) {
    case "generated":
      return "Generated OSDK";
    case "dynamic_only":
      return "Live catalog";
    case "disabled":
      return "Disabled";
  }
}

function foundryLiteOntologyActionDisabledReason(
  actionView: FoundryLiteOntologyActionView,
): string | null {
  if (!actionView.isEnabled) return "This action is disabled in the active ontology.";
  if (!actionView.isGeneratedActionTypeAvailable) {
    return "Regenerate the SDK before rendering this action as a static OSDK action button.";
  }
  return null;
}

function readGeneratedObjectType(apiName: string): OsdkObjectType | null {
  try {
    return getObjectType(apiName as never) as unknown as OsdkObjectType;
  } catch {
    return null;
  }
}

function readGeneratedActionType(apiName: string): OsdkActionType | null {
  try {
    return getActionType(apiName as never) as unknown as OsdkActionType;
  } catch {
    return null;
  }
}

function readFoundryLiteActionProposalString(value: string | null | undefined): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readFoundryLiteActionProposalExpectedVersion(
  proposal: InsightActionProposal | null | undefined,
): number | null {
  const expectedObjectVersion = proposal?.expectedObjectVersion;
  return typeof expectedObjectVersion === "number" && Number.isFinite(expectedObjectVersion)
    ? expectedObjectVersion
    : null;
}

function foundryLiteActionProposalMissingFields(
  actionApiName: string | null,
  targetObjectId: string | null,
  expectedObjectVersion: number | null,
  generatedActionType: OsdkActionType | null,
): string[] {
  const missingFields: string[] = [];
  if (!actionApiName) missingFields.push("actionApiName");
  if (actionApiName && !generatedActionType) missingFields.push("generatedActionType");
  if (!targetObjectId) missingFields.push("targetObjectId");
  if (expectedObjectVersion === null) missingFields.push("expectedObjectVersion");
  return missingFields;
}

function foundryLiteActionProposalDisabledReason(
  missingFields: string[],
  isTargetObjectTypeMatched: boolean,
): string | null {
  if (missingFields.length > 0) {
    return `Action proposal is missing ${missingFields.join(", ")}.`;
  }
  if (!isTargetObjectTypeMatched) {
    return "Action proposal target object type does not match the generated action target.";
  }
  return null;
}

function foundryLiteSchemaPropertyCount(schema: Record<string, unknown>): number {
  const properties = schema.properties;
  if (isFoundryLiteRecord(properties)) return Object.keys(properties).length;
  return Object.keys(schema).length;
}

function foundryLiteActionParameterFields(
  schema: Record<string, unknown>,
  params: Record<string, unknown>,
): FoundryLiteActionParameterField[] {
  const properties = foundryLiteActionSchemaProperties(schema);
  const requiredNames = foundryLiteActionSchemaRequiredNames(schema);
  const resolvedValues: Record<string, unknown> = { ...params };
  return Object.entries(properties).map(([name, fieldSchema]) => {
    const fieldRecord = isFoundryLiteRecord(fieldSchema) ? fieldSchema : {};
    const parameterConfig = isFoundryLiteRecord(fieldRecord["x-foundry-parameter-config"])
      ? fieldRecord["x-foundry-parameter-config"]
      : {};
    const effectiveConfig = foundryLiteEffectiveParameterConfig(
      parameterConfig,
      requiredNames.has(name),
      resolvedValues,
      foundryLiteParameterConstraints(fieldRecord),
    );
    const effectiveSchema = foundryLiteEffectiveParameterSchema(fieldRecord, effectiveConfig.constraints);
    const dataType = foundryLiteActionParameterDataType(fieldRecord, parameterConfig);
    const submittedValue = params[name];
    const defaultValue = foundryLiteActionParameterDefaultValue(effectiveConfig.defaultValue, resolvedValues);
    const value = submittedValue ?? defaultValue;
    if (value !== undefined) resolvedValues[name] = value;
    return {
      name,
      parameterPath: name,
      label: foundryLiteActionParameterLabel(name, fieldRecord),
      description: foundryLiteActionParameterDescription(fieldRecord),
      dataType,
      inputKind: foundryLiteActionParameterInputKind(dataType, effectiveSchema),
      isRequired: effectiveConfig.isRequired,
      isVisible: effectiveConfig.isVisible,
      isEditable: effectiveConfig.isEditable,
      hasServerDefault: effectiveConfig.defaultValue !== null,
      matchedOverride: effectiveConfig.matchedOverride,
      options: Array.isArray(effectiveSchema.enum) ? effectiveSchema.enum : [],
      schema: effectiveSchema,
      value,
      hasValue:
        foundryLiteActionParameterHasValue(value) || effectiveConfig.defaultValue !== null,
      constraintError: foundryLiteActionParameterConstraintError(name, value, effectiveSchema),
    };
  });
}

function foundryLiteActionParameterSections(
  schema: Record<string, unknown>,
  fields: FoundryLiteActionParameterField[],
): FoundryLiteActionFormSection[] {
  const layout = isFoundryLiteRecord(schema["x-foundry-form-layout"])
    ? schema["x-foundry-form-layout"]
    : {};
  const rawSections = Array.isArray(layout.sections) ? layout.sections : [];
  const fieldsByName = new Map(fields.map((field) => [field.name, field]));
  const values = Object.fromEntries(fields.filter((field) => field.hasValue).map((field) => [field.name, field.value]));
  const assigned = new Set<string>();
  const sections = rawSections.map((raw, index) => {
    const section = isFoundryLiteRecord(raw) ? raw : {};
    const names = Array.isArray(section.parameterNames)
      ? section.parameterNames.filter((name): name is string => typeof name === "string")
      : [];
    const sectionFields = names.flatMap((name) => {
      const field = fieldsByName.get(name);
      if (!field || assigned.has(name)) return [];
      assigned.add(name);
      return [field];
    });
    return foundryLiteActionFormSection(section, index, names, sectionFields, values);
  });
  const remaining = fields.filter((field) => !assigned.has(field.name));
  if (remaining.length > 0 || sections.length === 0) {
    sections.push(
      foundryLiteActionFormSection(
        {},
        sections.length,
        remaining.map((field) => field.name),
        remaining,
        values,
      ),
    );
  }
  return sections;
}

function foundryLiteActionFormSection(
  section: Record<string, unknown>,
  index: number,
  parameterNames: string[],
  fields: FoundryLiteActionParameterField[],
  values: Record<string, unknown>,
): FoundryLiteActionFormSection {
  const columns = section.columns === 2 ? 2 : 1;
  const visibleWhen = isFoundryLiteRecord(section.visibleWhen) ? section.visibleWhen : null;
  return {
    id: typeof section.id === "string" && section.id ? section.id : `parameters-${index + 1}`,
    title: typeof section.title === "string" && section.title ? section.title : "Parameters",
    description: typeof section.description === "string" ? section.description : null,
    columns,
    isCollapsible: section.isCollapsible === true,
    isInitiallyCollapsed: section.isInitiallyCollapsed === true,
    isVisible: visibleWhen === null || foundryLiteActionConditionMatches(visibleWhen, values),
    visibleWhen,
    parameterNames,
    fields,
  };
}

type FoundryLiteEffectiveParameterConfig = {
  isRequired: boolean;
  isVisible: boolean;
  isEditable: boolean;
  defaultValue: Record<string, unknown> | null;
  constraints: Record<string, unknown>;
  matchedOverride: number | null;
};

function foundryLiteEffectiveParameterConfig(
  config: Record<string, unknown>,
  schemaRequired: boolean,
  values: Record<string, unknown>,
  schemaConstraints: Record<string, unknown>,
): FoundryLiteEffectiveParameterConfig {
  const result: FoundryLiteEffectiveParameterConfig = {
    isRequired: typeof config.required === "boolean" ? config.required : schemaRequired,
    isVisible: true,
    isEditable: true,
    defaultValue: isFoundryLiteRecord(config.default) ? config.default : null,
    constraints: isFoundryLiteRecord(config.constraints) ? config.constraints : schemaConstraints,
    matchedOverride: null,
  };
  const overrides = Array.isArray(config.overrides) ? config.overrides : [];
  overrides.some((raw, index) => {
    const override = isFoundryLiteRecord(raw) ? raw : {};
    if (!foundryLiteActionConditionMatches(override.when, values)) return false;
    const next = isFoundryLiteRecord(override.config) ? override.config : {};
    if (typeof next.required === "boolean") result.isRequired = next.required;
    if (typeof next.visible === "boolean") result.isVisible = next.visible;
    if (typeof next.editable === "boolean") result.isEditable = next.editable;
    if (isFoundryLiteRecord(next.default)) result.defaultValue = next.default;
    if (isFoundryLiteRecord(next.constraints)) result.constraints = next.constraints;
    result.matchedOverride = index;
    return true;
  });
  return result;
}

const FOUNDRY_LITE_PARAMETER_CONSTRAINT_KEYS = [
  "enum", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems",
] as const;

function foundryLiteParameterConstraints(schema: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    FOUNDRY_LITE_PARAMETER_CONSTRAINT_KEYS.flatMap((key) =>
      Object.prototype.hasOwnProperty.call(schema, key) ? [[key, schema[key]]] : [],
    ),
  );
}

function foundryLiteEffectiveParameterSchema(
  schema: Record<string, unknown>,
  constraints: Record<string, unknown>,
): Record<string, unknown> {
  const result = { ...schema };
  for (const key of FOUNDRY_LITE_PARAMETER_CONSTRAINT_KEYS) delete result[key];
  return { ...result, ...constraints };
}

function foundryLiteActionParameterConstraintError(
  name: string,
  value: unknown,
  constraints: Record<string, unknown>,
): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (Array.isArray(constraints.enum) && !constraints.enum.some((item) => Object.is(item, value))) {
    return `${name}: enum`;
  }
  if (typeof value === "string") {
    if (typeof constraints.minLength === "number" && value.length < constraints.minLength) return `${name}: minLength`;
    if (typeof constraints.maxLength === "number" && value.length > constraints.maxLength) return `${name}: maxLength`;
  }
  if (typeof value === "number") {
    if (typeof constraints.minimum === "number" && value < constraints.minimum) return `${name}: minimum`;
    if (typeof constraints.maximum === "number" && value > constraints.maximum) return `${name}: maximum`;
  }
  if (Array.isArray(value)) {
    if (typeof constraints.minItems === "number" && value.length < constraints.minItems) return `${name}: minItems`;
    if (typeof constraints.maxItems === "number" && value.length > constraints.maxItems) return `${name}: maxItems`;
  }
  return null;
}

function foundryLiteActionConditionMatches(
  raw: unknown,
  values: Record<string, unknown>,
): boolean {
  const condition = isFoundryLiteRecord(raw) ? raw : {};
  if (Array.isArray(condition.all)) {
    return condition.all.every((child) => foundryLiteActionConditionMatches(child, values));
  }
  if (Array.isArray(condition.any)) {
    return condition.any.some((child) => foundryLiteActionConditionMatches(child, values));
  }
  if (condition.not !== undefined) return !foundryLiteActionConditionMatches(condition.not, values);
  const left = foundryLiteActionConditionValue(condition.left, values);
  const right = foundryLiteActionConditionValue(condition.right, values);
  if (left === undefined || (condition.op !== "exists" && right === undefined)) return false;
  if (condition.op === "eq") return Object.is(left, right);
  if (condition.op === "neq") return !Object.is(left, right);
  if (condition.op === "exists") return left !== null;
  if (condition.op === "in" && Array.isArray(right)) return right.includes(left);
  if (condition.op === "notIn" && Array.isArray(right)) return !right.includes(left);
  if (condition.op === "contains" && Array.isArray(left)) return left.includes(right);
  if (condition.op === "contains" && typeof left === "string") return left.includes(String(right));
  if (condition.op === "startsWith" && typeof left === "string") return left.startsWith(String(right));
  return foundryLiteOrderedActionCondition(condition.op, left, right);
}

function foundryLiteActionConditionValue(
  raw: unknown,
  values: Record<string, unknown>,
): unknown {
  const value = isFoundryLiteRecord(raw) ? raw : {};
  if (value.kind === "literal") return value.value;
  if (value.kind === "parameter" && typeof value.parameter === "string") {
    return values[value.parameter];
  }
  return undefined;
}

function foundryLiteOrderedActionCondition(op: unknown, left: unknown, right: unknown): boolean {
  if (typeof left !== "number" || typeof right !== "number") return false;
  if (op === "lt") return left < right;
  if (op === "lte") return left <= right;
  if (op === "gt") return left > right;
  if (op === "gte") return left >= right;
  return false;
}

function foundryLiteActionParameterDefaultValue(
  defaultValue: Record<string, unknown> | null,
  values: Record<string, unknown>,
): unknown {
  if (defaultValue?.kind === "literal") return defaultValue.value;
  if (defaultValue?.kind === "parameter" && typeof defaultValue.parameter === "string") {
    return values[defaultValue.parameter];
  }
  return undefined;
}

function foundryLiteActionSchemaProperties(
  schema: Record<string, unknown>,
): Record<string, unknown> {
  if (isFoundryLiteRecord(schema.properties)) return schema.properties;
  const reservedKeys = new Set(["type", "required", "title", "description", "additionalProperties"]);
  return Object.fromEntries(Object.entries(schema).filter(([key]) => !reservedKeys.has(key)));
}

function foundryLiteActionSchemaRequiredNames(schema: Record<string, unknown>): Set<string> {
  const required = schema.required;
  if (!Array.isArray(required)) return new Set();
  return new Set(required.filter((name): name is string => typeof name === "string"));
}

function foundryLiteActionParameterDataType(
  schema: Record<string, unknown>,
  parameterConfig: Record<string, unknown>,
): string {
  if (typeof parameterConfig.type === "string") return parameterConfig.type;
  const type = schema.type;
  if (typeof type === "string") return type;
  if (Array.isArray(type)) return type.filter((item) => typeof item === "string").join("|");
  const format = schema.format;
  return typeof format === "string" ? format : "unknown";
}

function foundryLiteActionParameterInputKind(
  dataType: string,
  schema: Record<string, unknown>,
): FoundryLiteActionParameterInputKind {
  if (Array.isArray(schema.enum)) return "select";
  if (dataType === "date") return "date";
  if (dataType === "timestamp") return "datetime";
  if (dataType === "media") return "media";
  if (dataType === "attachment") return "attachment";
  if (["float", "integer", "long"].includes(dataType)) return "number";
  if (dataType.includes("boolean")) return "checkbox";
  if (dataType.includes("object") || dataType.includes("array") || dataType === "unknown") return "json";
  return "text";
}

function foundryLiteActionParameterLabel(name: string, schema: Record<string, unknown>): string {
  const title = schema.title;
  if (typeof title === "string" && title.length > 0) return title;
  return name;
}

function foundryLiteActionParameterDescription(schema: Record<string, unknown>): string | null {
  const description = schema.description;
  return typeof description === "string" && description.length > 0 ? description : null;
}

function foundryLiteActionParameterHasValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  return true;
}

function readFoundryLiteActionTargetObjectId(
  targetObject: FoundryLiteActionTargetLike | FoundryLiteObject<string, object> | null,
): string | null {
  const objectId = targetObject?.objectId;
  return typeof objectId === "string" && objectId.length > 0 ? objectId : null;
}

function readFoundryLiteActionTargetObjectVersion(
  targetObject: FoundryLiteActionTargetLike | FoundryLiteObject<string, object> | null,
): number | null {
  const objectVersion = targetObject?.objectVersion;
  return typeof objectVersion === "number" ? objectVersion : null;
}

function readFoundryLiteActionTargetObjectType(
  targetObject: FoundryLiteActionTargetLike | FoundryLiteObject<string, object> | null,
): string | null {
  const objectType = targetObject?.objectType;
  return typeof objectType === "string" && objectType.length > 0 ? objectType : null;
}

function foundryLiteActionFormMissingFields(
  actionView: FoundryLiteOntologyActionView | null | undefined,
  objectId: string | null,
  expectedObjectVersion: number | null,
  missingParameterNames: string[],
  idempotencyKey: string | null,
  shouldRequireIdempotencyKey: boolean,
): string[] {
  const missing: string[] = [];
  if (!actionView) missing.push("action");
  if (!objectId) missing.push("objectId");
  if (expectedObjectVersion === null) missing.push("expectedObjectVersion");
  missing.push(...missingParameterNames.map((name) => `params.${name}`));
  if (shouldRequireIdempotencyKey && !idempotencyKey) missing.push("idempotencyKey");
  return missing;
}

function foundryLiteActionFormDisabledReason(
  actionView: FoundryLiteOntologyActionView | null | undefined,
  missingFields: string[],
  invalidParameterNames: string[],
  isTargetObjectTypeMatched: boolean,
): string | null {
  if (!actionView) return "Select an action before building an action form.";
  if (!actionView.isEnabled) return "Selected action is disabled in the active ontology.";
  if (!isTargetObjectTypeMatched) {
    return "Selected object type does not match this action target.";
  }
  if (missingFields.length > 0) return `Action form is missing ${missingFields.join(", ")}.`;
  if (invalidParameterNames.length > 0) {
    return `Action form has invalid parameters ${invalidParameterNames.join(", ")}.`;
  }
  return null;
}

function foundryLiteRuntimeActionType(
  actionView: FoundryLiteOntologyActionView | null | undefined,
): OsdkActionType | null {
  if (!actionView) return null;
  if (actionView.generatedActionType) return actionView.generatedActionType;
  const targetKind = actionView.action.definition.targetKind === "interface" ? "interface" : "object";
  return {
    kind: "action",
    apiName: actionView.apiName,
    targetObjectType: actionView.targetObjectApiName,
    targetKind,
  };
}

function foundryLiteAdminLaunchLabel(actionKind: AdminCapabilityActionKind): string {
  switch (actionKind) {
    case "browser_action":
      return "Run";
    case "api_without_sdk":
      return "Open API";
    case "worker_entrypoint":
      return "Show worker command";
    case "cli_command":
      return "Show CLI command";
    case "runbook_step":
      return "Open runbook";
    case "future":
      return "Future surface";
  }
}

function foundryLiteAdminLaunchDescription(view: FoundryLiteAdminCapabilityView): string {
  if (view.canRenderActionButton) return view.capability.summary;
  return view.disabledReason ?? view.capability.safetyBoundary;
}

function compactOperationsRunFilters(filters: RuntimeRunQueryFilters): RuntimeRunQueryFilters {
  return {
    ...(filters.runType ? { runType: filters.runType } : {}),
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: filters.until } : {}),
    ...(filters.limit !== undefined ? { limit: filters.limit } : {}),
    ...(filters.cursor ? { cursor: filters.cursor } : {}),
  };
}

function stableOperationsRunFiltersKey(filters: RuntimeRunQueryFilters): string {
  return JSON.stringify(compactOperationsRunFilters(filters));
}

function findSelectedOperationsRun(
  runRows: FoundryLiteOperationsRunRow[],
  selection: FoundryLiteOperationsRunListSelection,
): FoundryLiteOperationsRunRow | null {
  if (!selection.selectedRunId) return null;
  return (
    runRows.find(
      (run) =>
        run.runId === selection.selectedRunId &&
        (!selection.selectedRunType || run.runType === selection.selectedRunType),
    ) ?? null
  );
}

function readFoundryLiteOperationsRunStatus(row: RuntimeRow): string | null {
  return readFoundryLiteStringField(row, [
    "status",
    "runStatus",
    "run_status",
    "state",
    "phase",
    "eventStatus",
    "event_status",
  ]);
}

function readFoundryLiteOperationsRunId(row: RuntimeRow): string | null {
  return readFoundryLiteStringField(row, [
    "runId",
    "run_id",
    "id",
    "syncRunId",
    "sync_run_id",
    "transformRunId",
    "transform_run_id",
    "indexRunId",
    "index_run_id",
    "actionRunId",
    "action_run_id",
    "writebackId",
    "writeback_id",
    "materializationRunId",
    "materialization_run_id",
    "outboxEventId",
    "outbox_event_id",
    "deadLetterEventId",
    "dead_letter_event_id",
    "workflowRunId",
    "workflow_run_id",
    "aiRunId",
    "ai_run_id",
    "auditEventId",
    "audit_event_id",
    "eventId",
    "event_id",
  ]);
}

function readFoundryLiteOperationsRunPath(
  runType: RuntimeRunType | string,
  row: RuntimeRow,
  runId: string | null,
): string | null {
  const explicitPath = readFoundryLiteStringField(row, [
    "operationPath",
    "operation_path",
    "detailPath",
    "detail_path",
    "path",
  ]);
  if (explicitPath) return explicitPath;
  if (!runId) return null;
  return `/api/operations/runs/${encodeURIComponent(String(runType))}/${encodeURIComponent(runId)}`;
}

function readFoundryLitePromptArtifactReferences(
  detail: RuntimeRunDetail | null | undefined,
): FoundryLitePromptArtifactReference[] {
  if (!detail) return [];
  const records = [detail.ai, detail.references, detail.investigation, detail.row].filter(isFoundryLiteRecord);
  const references = records.flatMap((record) => promptArtifactReferencesFromRecord(record));
  const seen = new Set<string>();
  return references.filter((reference) => {
    if (seen.has(reference.artifactId)) return false;
    seen.add(reference.artifactId);
    return true;
  });
}

function promptArtifactReferencesFromRecord(record: Record<string, unknown>): FoundryLitePromptArtifactReference[] {
  const direct = promptArtifactReferenceFromRecord(record, {
    allowGenericId: false,
  });
  const nested = ["promptArtifacts", "prompt_artifacts", "artifacts"]
    .flatMap((key) => {
      const value = record[key];
      return Array.isArray(value) ? value : [];
    })
    .filter(isFoundryLiteRecord)
    .map((item) =>
      promptArtifactReferenceFromRecord(item, { allowGenericId: true }),
    )
    .filter((reference): reference is FoundryLitePromptArtifactReference => reference !== null);
  return direct ? [direct, ...nested] : nested;
}

function promptArtifactReferenceFromRecord(
  record: Record<string, unknown>,
  options: { allowGenericId: boolean },
): FoundryLitePromptArtifactReference | null {
  const artifactId = readFoundryLiteStringField(record, [
    "artifactId",
    "artifact_id",
    "promptArtifactId",
    "prompt_artifact_id",
  ]) ?? (
    options.allowGenericId ? readFoundryLiteStringField(record, ["id"]) : null
  );
  if (!artifactId) return null;
  return {
    artifactId,
    label: readFoundryLiteStringField(record, ["label", "name", "kind", "artifactKind", "artifact_kind"]),
    contentHash: readFoundryLiteStringField(record, ["contentHash", "content_hash", "hash"]),
    exportMarking: readFoundryLiteStringField(record, ["exportMarking", "export_marking", "marking"]),
    raw: record,
  };
}

function readFoundryLiteLongRunningJobStatus<TStartResult, TSnapshot>(
  getStatus: FoundryLiteLongRunningJobOptions<TStartResult, TSnapshot>["getStatus"],
  startResult: TStartResult | null,
  snapshot: TSnapshot | null,
): string | null {
  return getStatus?.({ startResult, snapshot }) ?? readFoundryLiteStatus(snapshot) ?? readFoundryLiteStatus(startResult);
}

function readFoundryLiteLongRunningJobRunId<TStartResult, TSnapshot>(
  getRunId: FoundryLiteLongRunningJobOptions<TStartResult, TSnapshot>["getRunId"],
  startResult: TStartResult | null,
  snapshot: TSnapshot | null,
): string | null {
  return getRunId?.({ startResult, snapshot }) ?? readFoundryLiteRunId(snapshot) ?? readFoundryLiteRunId(startResult);
}

function foundryLiteLongRunningSnapshotSucceeded<TSnapshot>(
  snapshot: TSnapshot,
  isSuccess: ((snapshot: TSnapshot) => boolean) | undefined,
): boolean {
  if (isSuccess) return isSuccess(snapshot);
  const status = readFoundryLiteStatus(snapshot);
  if (status && isFoundryLiteFailureStatus(status)) return false;
  if (status && isFoundryLiteSuccessStatus(status)) return true;
  return true;
}

function readFoundryLiteStatus(value: unknown): string | null {
  const status = readFoundryLiteStringField(value, ["status", "runStatus", "state", "phase"]);
  return status ? status.toLowerCase() : null;
}

function readFoundryLiteRunId(value: unknown): string | null {
  return readFoundryLiteStringField(value, [
    "runId",
    "workflowRunId",
    "foundryRunId",
    "aiRunId",
    "agentRunId",
    "logicRunId",
    "transformRunId",
    "transform_run_id",
    "materializationRunId",
    "materialization_run_id",
    "mediaProcessingRunId",
    "media_processing_run_id",
  ]);
}

function readFoundryLiteStringField(value: unknown, fields: readonly string[]): string | null {
  if (!isFoundryLiteRecord(value)) return null;
  for (const field of fields) {
    const candidate = value[field];
    if (typeof candidate === "string" && candidate.length > 0) return candidate;
  }
  return null;
}

function isFoundryLiteSuccessStatus(status: string): boolean {
  return ["succeeded", "success", "completed", "complete", "passed", "done"].includes(status);
}

function isFoundryLiteFailureStatus(status: string): boolean {
  return [
    "failed",
    "failure",
    "error",
    "errored",
    "blocked",
    "rejected",
    "cancelled",
    "canceled",
    "aborted",
    "start_unknown",
    "outcome_unknown",
    "compensation_required",
  ].includes(status);
}

export type FoundryLiteOntologyDraftOptions = {
  initialCatalog?: OntologyCatalog | null;
  initialDraft?: OntologyDraft | null;
};

export type FoundryLiteOntologyDraftState = {
  draft: OntologyDraft;
  isDirty: boolean;
  objectTypeCount: number;
  linkTypeCount: number;
  actionTypeCount: number;
  interfaceCount: number;
  functionTypeCount: number;
  loadFromCatalog(catalog: OntologyCatalog): void;
  replaceDraft(draft: OntologyDraft): void;
  reset(): void;
  toYamlText(): string;
  upsertObjectType(objectType: OntologyDraftObjectType): void;
  updateObjectType(apiName: string, patch: Partial<Omit<OntologyDraftObjectType, "apiName">>): void;
  removeObjectType(apiName: string): void;
  setObjectTypeImplements(apiName: string, implementsApiNames: string[]): void;
  upsertProperty(objectApiName: string, property: OntologyDraftProperty): void;
  updateProperty(
    objectApiName: string,
    propertyApiName: string,
    patch: Partial<Omit<OntologyDraftProperty, "apiName">>,
  ): void;
  removeProperty(objectApiName: string, propertyApiName: string): void;
  upsertLinkType(linkType: OntologyDraftLinkType): void;
  removeLinkType(apiName: string): void;
  upsertActionType(actionType: OntologyDraftActionType): void;
  removeActionType(apiName: string): void;
  upsertInterfaceType(interfaceType: OntologyDraftInterfaceType): void;
  removeInterfaceType(apiName: string): void;
  upsertFunctionType(functionType: OntologyDraftFunctionType): void;
  removeFunctionType(apiName: string): void;
};

/**
 * Client-side editable ontology draft for click-driven Object/Property/Link/
 * Action/Interface/Function design. `loadFromCatalog` seeds the draft from the
 * active catalog (unknown config keys ride along verbatim so a round-trip does
 * not drop features), the mutators update it immutably, and `toYamlText`
 * serializes it for `client.ontology.validate` / proposals. The serialization
 * is pretty-printed JSON: YAML 1.2 is a JSON superset and the backend loads
 * `yamlText` with a YAML loader, so JSON is valid YAML while hand-authored
 * YAML files remain equally valid input. Dirty tracking compares against the
 * last loaded baseline; `reset` restores that baseline.
 */
export function useFoundryLiteOntologyDraft(
  options: FoundryLiteOntologyDraftOptions = {},
): FoundryLiteOntologyDraftState {
  const initialDraftRef = useRef<OntologyDraft | null>(null);
  if (initialDraftRef.current === null) {
    initialDraftRef.current =
      options.initialDraft ??
      (options.initialCatalog ? ontologyDraftFromCatalog(options.initialCatalog) : emptyOntologyDraft());
  }
  const [draft, setDraft] = useState<OntologyDraft>(initialDraftRef.current);
  const [baseline, setBaseline] = useState<OntologyDraft>(initialDraftRef.current);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  const loadFromCatalog = useCallback((catalog: OntologyCatalog) => {
    const nextDraft = ontologyDraftFromCatalog(catalog);
    setBaseline(nextDraft);
    setDraft(nextDraft);
  }, []);
  const replaceDraft = useCallback((nextDraft: OntologyDraft) => setDraft(nextDraft), []);
  const reset = useCallback(() => setDraft(baseline), [baseline]);
  const toYamlText = useCallback(() => ontologyDraftToYamlText(draftRef.current), []);
  const baselineKey = useMemo(() => JSON.stringify(baseline), [baseline]);
  const draftKey = useMemo(() => JSON.stringify(draft), [draft]);

  const upsertObjectType = useCallback(
    (objectType: OntologyDraftObjectType) =>
      setDraft((current) => upsertOntologyDraftObjectType(current, objectType)),
    [],
  );
  const updateObjectType = useCallback(
    (apiName: string, patch: Partial<Omit<OntologyDraftObjectType, "apiName">>) =>
      setDraft((current) => updateOntologyDraftObjectType(current, apiName, patch)),
    [],
  );
  const removeObjectType = useCallback(
    (apiName: string) => setDraft((current) => removeOntologyDraftObjectType(current, apiName)),
    [],
  );
  const setObjectTypeImplements = useCallback(
    (apiName: string, implementsApiNames: string[]) =>
      setDraft((current) => setOntologyDraftObjectTypeImplements(current, apiName, implementsApiNames)),
    [],
  );
  const upsertProperty = useCallback(
    (objectApiName: string, property: OntologyDraftProperty) =>
      setDraft((current) => upsertOntologyDraftProperty(current, objectApiName, property)),
    [],
  );
  const updateProperty = useCallback(
    (
      objectApiName: string,
      propertyApiName: string,
      patch: Partial<Omit<OntologyDraftProperty, "apiName">>,
    ) =>
      setDraft((current) =>
        updateOntologyDraftProperty(current, objectApiName, propertyApiName, patch),
      ),
    [],
  );
  const removeProperty = useCallback(
    (objectApiName: string, propertyApiName: string) =>
      setDraft((current) => removeOntologyDraftProperty(current, objectApiName, propertyApiName)),
    [],
  );
  const upsertLinkType = useCallback(
    (linkType: OntologyDraftLinkType) =>
      setDraft((current) => upsertOntologyDraftLinkType(current, linkType)),
    [],
  );
  const removeLinkType = useCallback(
    (apiName: string) => setDraft((current) => removeOntologyDraftLinkType(current, apiName)),
    [],
  );
  const upsertActionType = useCallback(
    (actionType: OntologyDraftActionType) =>
      setDraft((current) => upsertOntologyDraftActionType(current, actionType)),
    [],
  );
  const removeActionType = useCallback(
    (apiName: string) => setDraft((current) => removeOntologyDraftActionType(current, apiName)),
    [],
  );
  const upsertInterfaceType = useCallback(
    (interfaceType: OntologyDraftInterfaceType) =>
      setDraft((current) => upsertOntologyDraftInterfaceType(current, interfaceType)),
    [],
  );
  const removeInterfaceType = useCallback(
    (apiName: string) => setDraft((current) => removeOntologyDraftInterfaceType(current, apiName)),
    [],
  );
  const upsertFunctionType = useCallback(
    (functionType: OntologyDraftFunctionType) =>
      setDraft((current) => upsertOntologyDraftFunctionType(current, functionType)),
    [],
  );
  const removeFunctionType = useCallback(
    (apiName: string) => setDraft((current) => removeOntologyDraftFunctionType(current, apiName)),
    [],
  );

  return {
    draft,
    isDirty: draftKey !== baselineKey,
    objectTypeCount: draft.objectTypes.length,
    linkTypeCount: draft.linkTypes.length,
    actionTypeCount: draft.actionTypes.length,
    interfaceCount: draft.interfaces.length,
    functionTypeCount: draft.functionTypes.length,
    loadFromCatalog,
    replaceDraft,
    reset,
    toYamlText,
    upsertObjectType,
    updateObjectType,
    removeObjectType,
    setObjectTypeImplements,
    upsertProperty,
    updateProperty,
    removeProperty,
    upsertLinkType,
    removeLinkType,
    upsertActionType,
    removeActionType,
    upsertInterfaceType,
    removeInterfaceType,
    upsertFunctionType,
    removeFunctionType,
  };
}

export type FoundryLiteOntologyMigrationChangeView = {
  kind: string;
  path: string;
  status: string;
  reason: string;
  requiresSdkMajorVersion: boolean;
  requiresObjectReindex: boolean;
  details: Record<string, unknown> | null;
};

export type FoundryLiteOntologyReindexOperationView = {
  objectTypeApiName: string;
  reason: string;
  changedFields: string[];
  reindexKey: string;
};

export type FoundryLiteOntologySdkImpact = {
  requiresSdkRegeneration: boolean;
  requiresSdkMajorVersion: boolean;
  sdkCompatibility: string | null;
  impactedPaths: string[];
};

export type FoundryLiteOntologyDraftValidationCounts = {
  objectTypes: number;
  linkTypes: number;
  actionTypes: number;
};

export type FoundryLiteOntologyDraftValidationView = {
  counts: FoundryLiteOntologyDraftValidationCounts | null;
  migrationPlan: Record<string, unknown> | null;
  planStatus: string | null;
  changes: FoundryLiteOntologyMigrationChangeView[];
  blockedChanges: FoundryLiteOntologyMigrationChangeView[];
  warnings: FoundryLiteOntologyMigrationChangeView[];
  reindexOperations: FoundryLiteOntologyReindexOperationView[];
  isBlocked: boolean;
  requiresReindex: boolean;
  sdkImpact: FoundryLiteOntologySdkImpact;
};

export type FoundryLiteOntologyDraftValidationOptions = {
  debounceMs?: number;
  onSuccess?: (validation: OntologyValidationResult) => void;
  onError?: (error: FoundryLiteApiError) => void;
};

export type FoundryLiteOntologyDraftValidationState = FoundryLiteOntologyDraftValidationView & {
  validation: OntologyValidationResult | null;
  yamlText: string | null;
  error: FoundryLiteApiError | null;
  isValidating: boolean;
  requestId: string | null;
  retryable: boolean;
  validate(): Promise<OntologyValidationResult | null>;
  validateNow(): Promise<OntologyValidationResult | null>;
};

/**
 * Screen-ready view over one `client.ontology.validate` result: entity
 * counts, migration plan changes split into `blockedChanges`/`warnings`,
 * `reindexOperations` (the object reindex plan), and the derived `sdkImpact`.
 * `sdkImpact.requiresSdkRegeneration` is true when any change requires an SDK
 * major version or touches the action/interface/function SDK surface, so
 * screens can render an "SDK regeneration required" (SDK 재생성 필요) badge.
 */
export function foundryLiteOntologyDraftValidationView(
  validation: OntologyValidationResult | null | undefined,
): FoundryLiteOntologyDraftValidationView {
  const migrationPlan = readFoundryLiteOntologyMigrationPlan(validation);
  const changes = readFoundryLiteOntologyMigrationChanges(migrationPlan?.changes);
  const blockedChanges = changes.filter((change) => change.status === "blocked");
  const warnings = changes.filter((change) => change.status === "warning");
  const reindexOperations = readFoundryLiteOntologyReindexOperations(
    migrationPlan?.objectReindexPlan,
  );
  const planStatus =
    typeof migrationPlan?.status === "string" ? migrationPlan.status : null;
  return {
    counts: validation
      ? {
          objectTypes: validation.object_type_count,
          linkTypes: validation.link_type_count,
          actionTypes: validation.action_type_count,
        }
      : null,
    migrationPlan,
    planStatus,
    changes,
    blockedChanges,
    warnings,
    reindexOperations,
    isBlocked: planStatus === "blocked" || blockedChanges.length > 0,
    requiresReindex: reindexOperations.length > 0,
    sdkImpact: foundryLiteOntologySdkImpact(migrationPlan, changes),
  };
}

/**
 * Derive the SDK regeneration impact of a migration plan. A draft needs a
 * regenerated OSDK package when the plan flags an SDK major version or when
 * any change touches the `actionTypes`/`interfaces`/`functionTypes` surface.
 */
export function foundryLiteOntologySdkImpact(
  migrationPlan: Record<string, unknown> | null,
  changes: FoundryLiteOntologyMigrationChangeView[],
): FoundryLiteOntologySdkImpact {
  const sdkCompatibility =
    typeof migrationPlan?.sdkCompatibility === "string" ? migrationPlan.sdkCompatibility : null;
  const requiresSdkMajorVersion =
    sdkCompatibility === "major_version_required" ||
    changes.some((change) => change.requiresSdkMajorVersion);
  const impactedChanges = changes.filter(
    (change) =>
      change.requiresSdkMajorVersion || isFoundryLiteOntologySdkSurfacePath(change.path),
  );
  return {
    requiresSdkRegeneration: requiresSdkMajorVersion || impactedChanges.length > 0,
    requiresSdkMajorVersion,
    sdkCompatibility,
    impactedPaths: Array.from(new Set(impactedChanges.map((change) => change.path))),
  };
}

/**
 * Debounced on-demand validation of an ontology draft via
 * `client.ontology.validate`. `validate()` waits `debounceMs` of quiet before
 * sending (superseded calls resolve `null`); `validateNow()` skips the
 * debounce. State exposes the raw result plus the migration-plan view
 * (`counts`, `migrationPlan`, `blockedChanges`, `warnings`,
 * `reindexOperations`, `isBlocked`, `sdkImpact`).
 */
export function useFoundryLiteOntologyDraftValidation(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  draft: OntologyDraft | null,
  options: FoundryLiteOntologyDraftValidationOptions = {},
): FoundryLiteOntologyDraftValidationState {
  const { debounceMs = 400, onSuccess, onError } = options;
  const [validation, setValidation] = useState<OntologyValidationResult | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const yamlText = useMemo(() => (draft ? ontologyDraftToYamlText(draft) : null), [draft]);
  const yamlTextRef = useRef(yamlText);
  const onSuccessRef = useRef(onSuccess);
  const onErrorRef = useRef(onError);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingResolveRef = useRef<((result: OntologyValidationResult | null) => void) | null>(null);
  const mountedRef = useRef(true);

  yamlTextRef.current = yamlText;
  onSuccessRef.current = onSuccess;
  onErrorRef.current = onError;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      pendingResolveRef.current?.(null);
      pendingResolveRef.current = null;
    };
  }, []);

  const validateNow = useCallback(async () => {
    const currentYamlText = yamlTextRef.current;
    if (currentYamlText === null) return null;
    if (mountedRef.current) setIsValidating(true);
    try {
      const result = await client.ontology.validate({ yaml: currentYamlText });
      if (mountedRef.current) {
        setValidation(result);
        setError(null);
        onSuccessRef.current?.(result);
      }
      return result;
    } catch (caught) {
      const normalized = normalizeFoundryLiteError(caught);
      if (mountedRef.current) {
        setError(normalized);
        onErrorRef.current?.(normalized);
      }
      return null;
    } finally {
      if (mountedRef.current) setIsValidating(false);
    }
  }, [client]);

  const validate = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    pendingResolveRef.current?.(null);
    return new Promise<OntologyValidationResult | null>((resolve) => {
      pendingResolveRef.current = resolve;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        pendingResolveRef.current = null;
        void validateNow().then(resolve);
      }, debounceMs);
    });
  }, [debounceMs, validateNow]);

  const view = useMemo(() => foundryLiteOntologyDraftValidationView(validation), [validation]);

  return {
    ...view,
    validation,
    yamlText,
    error,
    isValidating,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    validate,
    validateNow,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyDraftValidation}. */
export function useFoundryLiteProvidedOntologyDraftValidation(
  draft: OntologyDraft | null,
  options: FoundryLiteOntologyDraftValidationOptions = {},
): FoundryLiteOntologyDraftValidationState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyDraftValidation(client, draft, options);
}

export type FoundryLiteOntologyProposalSubmitPayload = OntologyProposalSubmitRequest & {
  idempotencyKey?: string;
};

export type FoundryLiteOntologyProposalMutationState<TPayload> =
  FoundryLiteMutationState<OntologyProposalPayload, TPayload> & {
    proposal: OntologyProposalPayload | null;
  };

export type FoundryLiteOntologyProposalSubmitOptions =
  FoundryLiteMutationOptions<OntologyProposalPayload, FoundryLiteOntologyProposalSubmitPayload> & {
    idempotencyKey?:
      | string
      | ((payload: FoundryLiteOntologyProposalSubmitPayload) => string | undefined);
  };

export function foundryLiteOntologyProposalSubmitLockKey(
  payload: FoundryLiteOntologyProposalSubmitPayload,
): string {
  return `ontology:proposal:submit:${payload.title}`;
}

/**
 * Submit an ontology change proposal (`client.ontology.proposals.submit`).
 * The endpoint requires an Idempotency-Key: the hook resolves it from
 * `payload.idempotencyKey`, then `options.idempotencyKey`, and otherwise
 * generates one at the call site with the exported `idempotencyKey` helper,
 * mirroring the `useFoundryLiteActionSubmit` convention.
 */
export function useFoundryLiteOntologyProposalSubmit(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyProposalSubmitOptions = {},
): FoundryLiteOntologyProposalMutationState<FoundryLiteOntologyProposalSubmitPayload> {
  const { idempotencyKey: suppliedIdempotencyKey, ...mutationOptions } = options;
  const mutate = useCallback(
    (payload: FoundryLiteOntologyProposalSubmitPayload) => {
      const { idempotencyKey: payloadIdempotencyKey, ...request } = payload;
      const supplied =
        typeof suppliedIdempotencyKey === "function"
          ? suppliedIdempotencyKey(payload)
          : suppliedIdempotencyKey;
      const resolvedIdempotencyKey =
        payloadIdempotencyKey ??
        supplied ??
        foundryLiteGeneratedIdempotencyKey("ontology.proposals.submit", request.title);
      return client.ontology.proposals.submit(request, { idempotencyKey: resolvedIdempotencyKey });
    },
    [client, suppliedIdempotencyKey],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    ...mutationOptions,
    lockKey: mutationOptions.lockKey ?? foundryLiteOntologyProposalSubmitLockKey,
  });
  return { ...mutation, proposal: mutation.result };
}

/** Context-client variant of {@link useFoundryLiteOntologyProposalSubmit}. */
export function useFoundryLiteProvidedOntologyProposalSubmit(
  options: FoundryLiteOntologyProposalSubmitOptions = {},
): FoundryLiteOntologyProposalMutationState<FoundryLiteOntologyProposalSubmitPayload> {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyProposalSubmit(client, options);
}

export type FoundryLiteOntologyProposalUpdatePayload = OntologyProposalUpdateRequest & {
  proposalId: string;
};

export function foundryLiteOntologyProposalUpdateLockKey(
  payload: FoundryLiteOntologyProposalUpdatePayload,
): string {
  return `ontology:proposal:update:${payload.proposalId}:${payload.expectedFingerprint}`;
}

/**
 * Revise an open proposal (`client.ontology.proposals.update`). The payload
 * threads `expectedFingerprint` so a concurrent revision of the same proposal
 * fails with a typed conflict instead of silently clobbering the reviewer's
 * copy; read the fingerprint from the latest proposal payload.
 */
export function useFoundryLiteOntologyProposalUpdate(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteMutationOptions<
    OntologyProposalPayload,
    FoundryLiteOntologyProposalUpdatePayload
  > = {},
): FoundryLiteOntologyProposalMutationState<FoundryLiteOntologyProposalUpdatePayload> {
  const mutate = useCallback(
    (payload: FoundryLiteOntologyProposalUpdatePayload) => {
      const { proposalId, ...request } = payload;
      return client.ontology.proposals.update(proposalId, request);
    },
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    ...options,
    lockKey: options.lockKey ?? foundryLiteOntologyProposalUpdateLockKey,
  });
  return { ...mutation, proposal: mutation.result };
}

/** Context-client variant of {@link useFoundryLiteOntologyProposalUpdate}. */
export function useFoundryLiteProvidedOntologyProposalUpdate(
  options: FoundryLiteMutationOptions<
    OntologyProposalPayload,
    FoundryLiteOntologyProposalUpdatePayload
  > = {},
): FoundryLiteOntologyProposalMutationState<FoundryLiteOntologyProposalUpdatePayload> {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyProposalUpdate(client, options);
}

export type FoundryLiteOntologyProposalQueueOptions = FoundryLiteCursorPaginationOptions<
  OntologyProposalListResult,
  OntologyProposalPayload
> & {
  key?: readonly unknown[];
  status?: string;
  pageSize?: number;
};

export type FoundryLiteOntologyProposalQueueState = FoundryLiteCursorPaginationState<
  OntologyProposalListResult,
  OntologyProposalPayload
> & {
  proposals: OntologyProposalPayload[];
  status: string | null;
  hasProposals: boolean;
};

/**
 * Cursor-paginated proposal review queue over
 * `client.ontology.proposals.list`, mirroring
 * `useFoundryLiteCursorPagination`: `items`/`proposals` accumulate loaded
 * pages, `loadMore` follows `nextCursor`, and changing `status` resets and
 * reloads the queue.
 */
export function useFoundryLiteOntologyProposalQueue(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyProposalQueueOptions = {},
): FoundryLiteOntologyProposalQueueState {
  const { key, status, pageSize, ...paginationOptions } = options;
  const queryKey = useMemo(
    () => key ?? ["ontology", "proposals", status ?? null, pageSize ?? null],
    [key, pageSize, status],
  );
  const loadPage = useCallback(
    (cursor: string | null) =>
      client.ontology.proposals.list({ status, cursor, limit: pageSize }),
    [client, pageSize, status],
  );
  const pagination = useFoundryLiteCursorPagination<
    OntologyProposalListResult,
    OntologyProposalPayload
  >(queryKey, loadPage, paginationOptions);
  return {
    ...pagination,
    proposals: pagination.items,
    status: status ?? null,
    hasProposals: pagination.items.length > 0,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyProposalQueue}. */
export function useFoundryLiteProvidedOntologyProposalQueue(
  options: FoundryLiteOntologyProposalQueueOptions = {},
): FoundryLiteOntologyProposalQueueState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyProposalQueue(client, options);
}

export type FoundryLiteOntologyProposalOptions = FoundryLiteQueryOptions<OntologyProposalPayload> & {
  key?: readonly unknown[];
};

export type FoundryLiteOntologyProposalState = FoundryLiteQueryState<OntologyProposalPayload> & {
  proposal: OntologyProposalPayload | null;
  proposalId: string | null;
  fingerprint: string | null;
  hasProposal: boolean;
  canLoadProposal: boolean;
  disabledReason: string | null;
  refetch(): Promise<OntologyProposalPayload | null>;
};

/**
 * Detail view of one proposal (`client.ontology.proposals.get`) with
 * `refetch` for pulling decision/execution updates. `fingerprint` is the
 * value to thread as `expectedFingerprint` into update/decide/execute calls.
 */
export function useFoundryLiteOntologyProposal(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  proposalId: string | null | undefined,
  options: FoundryLiteOntologyProposalOptions = {},
): FoundryLiteOntologyProposalState {
  const { key, enabled = true, ...queryOptions } = options;
  const canLoadProposal = Boolean(proposalId);
  const queryKey = useMemo(
    () => key ?? ["ontology", "proposals", "detail", proposalId ?? null],
    [key, proposalId],
  );
  const load = useCallback(() => {
    if (!proposalId) {
      throw new FoundryLiteApiError(
        0,
        "PROPOSAL_NOT_SELECTED",
        "Select a proposal before loading its detail.",
        { missingFields: ["proposalId"] },
        null,
        false,
      );
    }
    return client.ontology.proposals.get(proposalId);
  }, [client, proposalId]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canLoadProposal,
  });
  return {
    ...query,
    proposal: query.data,
    proposalId: proposalId ?? null,
    fingerprint: query.data?.fingerprint ?? null,
    hasProposal: query.data !== null,
    canLoadProposal,
    disabledReason: canLoadProposal ? null : "Select a proposal before loading its detail.",
    refetch: query.reload,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyProposal}. */
export function useFoundryLiteProvidedOntologyProposal(
  proposalId: string | null | undefined,
  options: FoundryLiteOntologyProposalOptions = {},
): FoundryLiteOntologyProposalState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyProposal(client, proposalId, options);
}

export type FoundryLiteOntologyProposalReviewOperation =
  | "assign"
  | "decide"
  | "execute"
  | "withdraw";

export type FoundryLiteOntologyProposalAssignPayload = {
  proposalId: string;
  reviewerUserId: string;
};

export type FoundryLiteOntologyProposalDecisionPayload = {
  proposalId: string;
  decision: string;
  expectedFingerprint: string;
  comment?: string | null;
};

export type FoundryLiteOntologyProposalExecutePayload = {
  proposalId: string;
  expectedFingerprint: string;
};

export type FoundryLiteOntologyProposalWithdrawPayload = {
  proposalId: string;
  reason?: string | null;
};

export type FoundryLiteOntologySeparationOfDutiesState = {
  isDenied: boolean;
  deniedOperation: FoundryLiteOntologyProposalReviewOperation | null;
  error: FoundryLiteApiError | null;
  requestId: string | null;
};

export type FoundryLiteOntologyProposalReviewOptions = {
  actionLock?: InFlightActionLock;
  onSuccess?: (
    proposal: OntologyProposalPayload,
    operation: FoundryLiteOntologyProposalReviewOperation,
  ) => void;
  onError?: (
    error: FoundryLiteApiError,
    operation: FoundryLiteOntologyProposalReviewOperation,
  ) => void;
};

export type FoundryLiteOntologyProposalReviewState = {
  assign: FoundryLiteMutationState<OntologyProposalPayload, FoundryLiteOntologyProposalAssignPayload>;
  decide: FoundryLiteMutationState<
    OntologyProposalPayload,
    FoundryLiteOntologyProposalDecisionPayload
  >;
  execute: FoundryLiteMutationState<
    OntologyProposalPayload,
    FoundryLiteOntologyProposalExecutePayload
  >;
  withdraw: FoundryLiteMutationState<
    OntologyProposalPayload,
    FoundryLiteOntologyProposalWithdrawPayload
  >;
  proposal: OntologyProposalPayload | null;
  lastOperation: FoundryLiteOntologyProposalReviewOperation | null;
  isRunning: boolean;
  error: FoundryLiteApiError | null;
  requestId: string | null;
  retryable: boolean;
  separationOfDuties: FoundryLiteOntologySeparationOfDutiesState;
};

export function foundryLiteOntologyProposalReviewLockKey(
  operation: FoundryLiteOntologyProposalReviewOperation,
  proposalId: string,
): string {
  return `ontology:proposal:${operation}:${proposalId}`;
}

/**
 * Reviewer surface for one or more proposals: `assign`, `decide`, `execute`,
 * and `withdraw` mutations over `client.ontology.proposals.*`. Decision and
 * execution payloads thread `expectedFingerprint` (take it from the proposal
 * detail). Separation-of-duties denials — the backend rejects submitters
 * deciding or executing their own proposal with `PERMISSION_DENIED` — surface
 * as the typed `separationOfDuties` state instead of a generic error.
 */
export function useFoundryLiteOntologyProposalReview(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyProposalReviewOptions = {},
): FoundryLiteOntologyProposalReviewState {
  const [proposal, setProposal] = useState<OntologyProposalPayload | null>(null);
  const [lastOperation, setLastOperation] =
    useState<FoundryLiteOntologyProposalReviewOperation | null>(null);
  const [lastFailure, setLastFailure] = useState<{
    operation: FoundryLiteOntologyProposalReviewOperation;
    error: FoundryLiteApiError;
  } | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const reviewMutationOptions = useCallback(
    <TPayload extends { proposalId: string }>(
      operation: FoundryLiteOntologyProposalReviewOperation,
    ): FoundryLiteMutationOptions<OntologyProposalPayload, TPayload> => ({
      actionLock: optionsRef.current.actionLock,
      lockKey: (payload: TPayload) =>
        foundryLiteOntologyProposalReviewLockKey(operation, payload.proposalId),
      onSuccess: (result: OntologyProposalPayload) => {
        setProposal(result);
        setLastOperation(operation);
        setLastFailure(null);
        optionsRef.current.onSuccess?.(result, operation);
      },
      onError: (error: FoundryLiteApiError) => {
        setLastOperation(operation);
        setLastFailure({ operation, error });
        optionsRef.current.onError?.(error, operation);
      },
    }),
    [],
  );

  const assign = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyProposalAssignPayload) =>
      client.ontology.proposals.assign(payload.proposalId, {
        reviewerUserId: payload.reviewerUserId,
      }),
    reviewMutationOptions<FoundryLiteOntologyProposalAssignPayload>("assign"),
  );
  const decide = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyProposalDecisionPayload) =>
      client.ontology.proposals.decide(payload.proposalId, {
        decision: payload.decision,
        expectedFingerprint: payload.expectedFingerprint,
        comment: payload.comment ?? null,
      }),
    reviewMutationOptions<FoundryLiteOntologyProposalDecisionPayload>("decide"),
  );
  const execute = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyProposalExecutePayload) =>
      client.ontology.proposals.execute(payload.proposalId, {
        expectedFingerprint: payload.expectedFingerprint,
      }),
    reviewMutationOptions<FoundryLiteOntologyProposalExecutePayload>("execute"),
  );
  const withdraw = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyProposalWithdrawPayload) =>
      client.ontology.proposals.withdraw(payload.proposalId, { reason: payload.reason ?? null }),
    reviewMutationOptions<FoundryLiteOntologyProposalWithdrawPayload>("withdraw"),
  );

  const separationOfDuties = useMemo<FoundryLiteOntologySeparationOfDutiesState>(() => {
    const denied =
      lastFailure && lastFailure.error.code === "PERMISSION_DENIED" ? lastFailure : null;
    return {
      isDenied: denied !== null,
      deniedOperation: denied?.operation ?? null,
      error: denied?.error ?? null,
      requestId: denied?.error.requestId ?? null,
    };
  }, [lastFailure]);

  const error = lastFailure?.error ?? null;
  return {
    assign,
    decide,
    execute,
    withdraw,
    proposal,
    lastOperation,
    isRunning: assign.isRunning || decide.isRunning || execute.isRunning || withdraw.isRunning,
    error,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    separationOfDuties,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyProposalReview}. */
export function useFoundryLiteProvidedOntologyProposalReview(
  options: FoundryLiteOntologyProposalReviewOptions = {},
): FoundryLiteOntologyProposalReviewState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyProposalReview(client, options);
}

export type FoundryLiteOntologyBranchesOptions = FoundryLiteCursorPaginationOptions<
  OntologyBranchListResult,
  OntologyBranchPayload
> & {
  key?: readonly unknown[];
  status?: string;
  pageSize?: number;
};

export type FoundryLiteOntologyBranchesState = FoundryLiteCursorPaginationState<
  OntologyBranchListResult,
  OntologyBranchPayload
> & {
  branches: OntologyBranchPayload[];
  status: string | null;
  hasBranches: boolean;
};

/**
 * Cursor-paginated ontology branch list over `client.ontology.branches.list`,
 * mirroring `useFoundryLiteOntologyProposalQueue`: `items`/`branches`
 * accumulate loaded pages, `loadMore` follows `nextCursor`, and changing
 * `status` (open/merged/abandoned) resets and reloads the list.
 */
export function useFoundryLiteOntologyBranches(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyBranchesOptions = {},
): FoundryLiteOntologyBranchesState {
  const { key, status, pageSize, ...paginationOptions } = options;
  const queryKey = useMemo(
    () => key ?? ["ontology", "branches", status ?? null, pageSize ?? null],
    [key, pageSize, status],
  );
  const loadPage = useCallback(
    (cursor: string | null) => client.ontology.branches.list({ status, cursor, limit: pageSize }),
    [client, pageSize, status],
  );
  const pagination = useFoundryLiteCursorPagination<OntologyBranchListResult, OntologyBranchPayload>(
    queryKey,
    loadPage,
    paginationOptions,
  );
  return {
    ...pagination,
    branches: pagination.items,
    status: status ?? null,
    hasBranches: pagination.items.length > 0,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyBranches}. */
export function useFoundryLiteProvidedOntologyBranches(
  options: FoundryLiteOntologyBranchesOptions = {},
): FoundryLiteOntologyBranchesState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyBranches(client, options);
}

export type FoundryLiteOntologyBranchOptions = FoundryLiteQueryOptions<OntologyBranchDetailPayload> & {
  key?: readonly unknown[];
};

export type FoundryLiteOntologyBranchState = FoundryLiteQueryState<OntologyBranchDetailPayload> & {
  branch: OntologyBranchDetailPayload | null;
  branchId: string | null;
  fingerprint: string | null;
  baseStale: boolean;
  yamlText: string | null;
  baseYamlText: string | null;
  hasBranch: boolean;
  canLoadBranch: boolean;
  disabledReason: string | null;
  refetch(): Promise<OntologyBranchDetailPayload | null>;
};

/**
 * Detail view of one ontology branch (`client.ontology.branches.get`) with
 * `refetch` for pulling working-copy updates. `fingerprint` is the branch's
 * `contentFingerprint` — thread it as `expectedFingerprint` into
 * update/rebase calls — and `baseStale` signals that main moved since the
 * branch's base, so a rebase is required before propose.
 */
export function useFoundryLiteOntologyBranch(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  branchId: string | null | undefined,
  options: FoundryLiteOntologyBranchOptions = {},
): FoundryLiteOntologyBranchState {
  const { key, enabled = true, ...queryOptions } = options;
  const canLoadBranch = Boolean(branchId);
  const queryKey = useMemo(
    () => key ?? ["ontology", "branches", "detail", branchId ?? null],
    [key, branchId],
  );
  const load = useCallback(() => {
    if (!branchId) throw foundryLiteOntologyBranchNotSelectedError();
    return client.ontology.branches.get(branchId);
  }, [client, branchId]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canLoadBranch,
  });
  return {
    ...query,
    branch: query.data,
    branchId: branchId ?? null,
    fingerprint: query.data?.contentFingerprint ?? null,
    baseStale: query.data?.baseStale ?? false,
    yamlText: query.data?.yamlText ?? null,
    baseYamlText: query.data?.baseYamlText ?? null,
    hasBranch: query.data !== null,
    canLoadBranch,
    disabledReason: canLoadBranch ? null : "Select a branch before loading its detail.",
    refetch: query.reload,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyBranch}. */
export function useFoundryLiteProvidedOntologyBranch(
  branchId: string | null | undefined,
  options: FoundryLiteOntologyBranchOptions = {},
): FoundryLiteOntologyBranchState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyBranch(client, branchId, options);
}

export type FoundryLiteOntologyBranchDiffOptions = FoundryLiteQueryOptions<OntologyBranchDiffResult> & {
  key?: readonly unknown[];
};

export type FoundryLiteOntologyBranchDiffState = FoundryLiteQueryState<OntologyBranchDiffResult> & {
  diff: OntologyBranchDiffResult | null;
  branchId: string | null;
  resources: OntologyBranchDiffResource[];
  conflicts: OntologyBranchDiffResource[];
  hasConflicts: boolean;
  baseStale: boolean;
  migrationPlan: Record<string, unknown> | null;
  canLoadDiff: boolean;
  disabledReason: string | null;
  refetch(): Promise<OntologyBranchDiffResult | null>;
};

/**
 * Three-way branch diff (`client.ontology.branches.diff`): per-resource
 * branch-vs-base-vs-active classification (`resources`), the subset both
 * sides changed (`conflicts` — each needs a `use: main|branch` resolution at
 * rebase time), `baseStale` (main moved since the branch base), and the
 * merge-time `migrationPlan` for impact screens.
 */
export function useFoundryLiteOntologyBranchDiff(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  branchId: string | null | undefined,
  options: FoundryLiteOntologyBranchDiffOptions = {},
): FoundryLiteOntologyBranchDiffState {
  const { key, enabled = true, ...queryOptions } = options;
  const canLoadDiff = Boolean(branchId);
  const queryKey = useMemo(
    () => key ?? ["ontology", "branches", "diff", branchId ?? null],
    [key, branchId],
  );
  const load = useCallback(() => {
    if (!branchId) throw foundryLiteOntologyBranchNotSelectedError();
    return client.ontology.branches.diff(branchId);
  }, [client, branchId]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canLoadDiff,
  });
  return {
    ...query,
    diff: query.data,
    branchId: branchId ?? null,
    resources: query.data?.resources ?? [],
    conflicts: query.data?.conflicts ?? [],
    hasConflicts: (query.data?.conflicts.length ?? 0) > 0,
    baseStale: query.data?.baseStale ?? false,
    migrationPlan: query.data?.migrationPlan ?? null,
    canLoadDiff,
    disabledReason: canLoadDiff ? null : "Select a branch before loading its diff.",
    refetch: query.reload,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyBranchDiff}. */
export function useFoundryLiteProvidedOntologyBranchDiff(
  branchId: string | null | undefined,
  options: FoundryLiteOntologyBranchDiffOptions = {},
): FoundryLiteOntologyBranchDiffState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyBranchDiff(client, branchId, options);
}

export type FoundryLiteOntologyBranchOperation =
  | "create"
  | "update"
  | "rebase"
  | "propose"
  | "abandon";

export type FoundryLiteOntologyBranchCreatePayload = OntologyBranchCreateRequest & {
  idempotencyKey?: string;
};

export type FoundryLiteOntologyBranchUpdatePayload = OntologyBranchUpdateRequest & {
  branchId: string;
};

export type FoundryLiteOntologyBranchRebasePayload = OntologyBranchRebaseRequest & {
  branchId: string;
};

export type FoundryLiteOntologyBranchProposePayload = OntologyBranchProposeRequest & {
  branchId: string;
  idempotencyKey?: string;
};

export type FoundryLiteOntologyBranchAbandonPayload = {
  branchId: string;
};

export type FoundryLiteOntologyBranchMutationsOptions = {
  actionLock?: InFlightActionLock;
  onSuccess?: (branch: OntologyBranchPayload, operation: FoundryLiteOntologyBranchOperation) => void;
  onError?: (error: FoundryLiteApiError, operation: FoundryLiteOntologyBranchOperation) => void;
};

export type FoundryLiteOntologyBranchMutationsState = {
  create: FoundryLiteMutationState<OntologyBranchPayload, FoundryLiteOntologyBranchCreatePayload>;
  update: FoundryLiteMutationState<OntologyBranchPayload, FoundryLiteOntologyBranchUpdatePayload>;
  rebase: FoundryLiteMutationState<OntologyBranchPayload, FoundryLiteOntologyBranchRebasePayload>;
  propose: FoundryLiteMutationState<OntologyBranchPayload, FoundryLiteOntologyBranchProposePayload>;
  abandon: FoundryLiteMutationState<OntologyBranchPayload, FoundryLiteOntologyBranchAbandonPayload>;
  branch: OntologyBranchPayload | null;
  fingerprint: string | null;
  proposal: OntologyProposalPayload | null;
  lastOperation: FoundryLiteOntologyBranchOperation | null;
  isRunning: boolean;
  error: FoundryLiteApiError | null;
  requestId: string | null;
  retryable: boolean;
};

export function foundryLiteOntologyBranchMutationLockKey(
  operation: FoundryLiteOntologyBranchOperation,
  resourceKey: string,
): string {
  return `ontology:branch:${operation}:${resourceKey}`;
}

/**
 * Branch working-copy mutations over `client.ontology.branches.*`: `create`
 * (Idempotency-Key resolved from `payload.idempotencyKey` and otherwise
 * generated with the exported `idempotencyKey` helper, mirroring
 * `useFoundryLiteOntologyProposalSubmit`), fingerprint-CAS `update`/`rebase`
 * (thread `expectedFingerprint` from `useFoundryLiteOntologyBranch().fingerprint`
 * or this hook's `fingerprint`, which tracks the latest mutation result),
 * `propose` (same Idempotency-Key convention; requires a rebased/up-to-date
 * base), and `abandon`. Every mutation resolves to the updated branch payload,
 * so `fingerprint` always carries the next `expectedFingerprint` to use.
 */
export function useFoundryLiteOntologyBranchMutations(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  options: FoundryLiteOntologyBranchMutationsOptions = {},
): FoundryLiteOntologyBranchMutationsState {
  const [branch, setBranch] = useState<OntologyBranchPayload | null>(null);
  const [lastOperation, setLastOperation] = useState<FoundryLiteOntologyBranchOperation | null>(
    null,
  );
  const [lastError, setLastError] = useState<FoundryLiteApiError | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const branchMutationOptions = useCallback(
    <TPayload,>(
      operation: FoundryLiteOntologyBranchOperation,
      resourceKey: (payload: TPayload) => string,
    ): FoundryLiteMutationOptions<OntologyBranchPayload, TPayload> => ({
      actionLock: optionsRef.current.actionLock,
      lockKey: (payload: TPayload) =>
        foundryLiteOntologyBranchMutationLockKey(operation, resourceKey(payload)),
      onSuccess: (result: OntologyBranchPayload) => {
        setBranch(result);
        setLastOperation(operation);
        setLastError(null);
        optionsRef.current.onSuccess?.(result, operation);
      },
      onError: (error: FoundryLiteApiError) => {
        setLastOperation(operation);
        setLastError(error);
        optionsRef.current.onError?.(error, operation);
      },
    }),
    [],
  );

  const create = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyBranchCreatePayload) => {
      const { idempotencyKey: payloadIdempotencyKey, ...request } = payload;
      const resolvedIdempotencyKey =
        payloadIdempotencyKey ??
        foundryLiteGeneratedIdempotencyKey("ontology.branches.create", request.name);
      return client.ontology.branches.create(request, { idempotencyKey: resolvedIdempotencyKey });
    },
    branchMutationOptions<FoundryLiteOntologyBranchCreatePayload>("create", (payload) => payload.name),
  );
  const update = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyBranchUpdatePayload) => {
      const { branchId, ...request } = payload;
      return client.ontology.branches.update(branchId, request);
    },
    branchMutationOptions<FoundryLiteOntologyBranchUpdatePayload>(
      "update",
      (payload) => `${payload.branchId}:${payload.expectedFingerprint}`,
    ),
  );
  const rebase = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyBranchRebasePayload) => {
      const { branchId, ...request } = payload;
      return client.ontology.branches.rebase(branchId, request);
    },
    branchMutationOptions<FoundryLiteOntologyBranchRebasePayload>(
      "rebase",
      (payload) => `${payload.branchId}:${payload.expectedFingerprint}`,
    ),
  );
  const propose = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyBranchProposePayload) => {
      const { branchId, idempotencyKey: payloadIdempotencyKey, ...request } = payload;
      const resolvedIdempotencyKey =
        payloadIdempotencyKey ??
        foundryLiteGeneratedIdempotencyKey("ontology.branches.propose", branchId);
      return client.ontology.branches.propose(branchId, request, {
        idempotencyKey: resolvedIdempotencyKey,
      });
    },
    branchMutationOptions<FoundryLiteOntologyBranchProposePayload>(
      "propose",
      (payload) => payload.branchId,
    ),
  );
  const abandon = useFoundryLiteMutation(
    (payload: FoundryLiteOntologyBranchAbandonPayload) =>
      client.ontology.branches.abandon(payload.branchId),
    branchMutationOptions<FoundryLiteOntologyBranchAbandonPayload>(
      "abandon",
      (payload) => payload.branchId,
    ),
  );

  return {
    create,
    update,
    rebase,
    propose,
    abandon,
    branch,
    fingerprint: branch?.contentFingerprint ?? null,
    proposal: branch?.proposal ?? null,
    lastOperation,
    isRunning:
      create.isRunning ||
      update.isRunning ||
      rebase.isRunning ||
      propose.isRunning ||
      abandon.isRunning,
    error: lastError,
    requestId: lastError?.requestId ?? null,
    retryable: lastError?.retryable ?? false,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyBranchMutations}. */
export function useFoundryLiteProvidedOntologyBranchMutations(
  options: FoundryLiteOntologyBranchMutationsOptions = {},
): FoundryLiteOntologyBranchMutationsState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyBranchMutations(client, options);
}

function foundryLiteOntologyBranchNotSelectedError(): FoundryLiteApiError {
  return new FoundryLiteApiError(
    0,
    "BRANCH_NOT_SELECTED",
    "Select an ontology branch before loading it.",
    { missingFields: ["branchId"] },
    null,
    false,
  );
}

export type FoundryLiteOntologyResourceInsightsOptions = {
  key?: readonly unknown[];
  enabled?: boolean;
  windowDays?: number;
  retry?: RetryWithBackoffOptions;
  onError?: (error: FoundryLiteApiError) => void;
};

export type FoundryLiteOntologyResourceInsightsState = {
  resourceType: string;
  apiName: string | null;
  windowDays: number | null;
  usage: OntologyResourceUsageResult | null;
  dependents: OntologyResourceDependentsResult | null;
  usageQuery: FoundryLiteQueryState<OntologyResourceUsageResult>;
  dependentsQuery: FoundryLiteQueryState<OntologyResourceDependentsResult>;
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  requestId: string | null;
  retryable: boolean;
  hasUsage: boolean;
  hasDependents: boolean;
  notes: string[];
  canLoadInsights: boolean;
  disabledReason: string | null;
  reload(): Promise<void>;
};

/**
 * Combined impact view for one ontology resource: usage evidence
 * (`client.ontology.resources.usage`, optionally windowed via `windowDays`)
 * and dependents (`client.ontology.resources.dependents`) loaded together
 * with a combined loading/error state, for "what breaks if I change this"
 * screens.
 */
export function useFoundryLiteOntologyResourceInsights(
  client: Pick<FoundryLiteGeneratedClient, "ontology">,
  resourceType: string,
  apiName: string | null | undefined,
  options: FoundryLiteOntologyResourceInsightsOptions = {},
): FoundryLiteOntologyResourceInsightsState {
  const { key, enabled = true, windowDays, retry, onError } = options;
  const canLoadInsights = Boolean(apiName);
  const usageKey = useMemo(
    () => key ?? ["ontology", "resources", resourceType, apiName ?? null, "usage", windowDays ?? null],
    [apiName, key, resourceType, windowDays],
  );
  const dependentsKey = useMemo(
    () => key ?? ["ontology", "resources", resourceType, apiName ?? null, "dependents"],
    [apiName, key, resourceType],
  );
  const loadUsage = useCallback(() => {
    if (!apiName) throw foundryLiteOntologyResourceNotSelectedError();
    return client.ontology.resources.usage(
      resourceType,
      apiName,
      windowDays === undefined ? undefined : { windowDays },
    );
  }, [apiName, client, resourceType, windowDays]);
  const loadDependents = useCallback(() => {
    if (!apiName) throw foundryLiteOntologyResourceNotSelectedError();
    return client.ontology.resources.dependents(resourceType, apiName);
  }, [apiName, client, resourceType]);
  const usageQuery = useFoundryLiteQuery(usageKey, loadUsage, {
    enabled: enabled && canLoadInsights,
    retry,
    onError,
  });
  const dependentsQuery = useFoundryLiteQuery(dependentsKey, loadDependents, {
    enabled: enabled && canLoadInsights,
    retry,
    onError,
  });
  const reload = useCallback(async () => {
    await Promise.all([usageQuery.reload(), dependentsQuery.reload()]);
  }, [dependentsQuery.reload, usageQuery.reload]);
  const error = usageQuery.error ?? dependentsQuery.error;

  return {
    resourceType,
    apiName: apiName ?? null,
    windowDays: windowDays ?? null,
    usage: usageQuery.data,
    dependents: dependentsQuery.data,
    usageQuery,
    dependentsQuery,
    error,
    isLoading: usageQuery.isLoading || dependentsQuery.isLoading,
    isRefreshing: usageQuery.isRefreshing || dependentsQuery.isRefreshing,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    hasUsage: usageQuery.data !== null,
    hasDependents: dependentsQuery.data !== null,
    notes: [...(usageQuery.data?.notes ?? []), ...(dependentsQuery.data?.notes ?? [])],
    canLoadInsights,
    disabledReason: canLoadInsights
      ? null
      : "Select an ontology resource before loading usage and dependents.",
    reload,
  };
}

/** Context-client variant of {@link useFoundryLiteOntologyResourceInsights}. */
export function useFoundryLiteProvidedOntologyResourceInsights(
  resourceType: string,
  apiName: string | null | undefined,
  options: FoundryLiteOntologyResourceInsightsOptions = {},
): FoundryLiteOntologyResourceInsightsState {
  const client = useFoundryLiteClient();
  return useFoundryLiteOntologyResourceInsights(client, resourceType, apiName, options);
}

export type FoundryLiteDatasetColumnMappingReference = {
  namespace: string;
  name: string;
};

export type FoundryLiteDatasetColumnMappingOptions =
  FoundryLiteQueryOptions<DatasetInspectionPayload> & {
    key?: readonly unknown[];
    version?: string;
  };

export type FoundryLiteDatasetColumnMappingState =
  FoundryLiteQueryState<DatasetInspectionPayload> & {
    datasetRef: string | null;
    inspection: DatasetInspectionPayload | null;
    schema: Record<string, unknown> | null;
    columns: OntologyDatasetColumn[];
    hasColumns: boolean;
    canInspectDataset: boolean;
    disabledReason: string | null;
    matchColumns(
      draftObject: OntologyDraftObjectType | null | undefined,
    ): OntologyColumnPropertyMatch[];
  };

/**
 * Dataset column -> property mapping surface for the builder screen. Wraps
 * `client.datasets.inspect` to expose the schema `columns`, and
 * `matchColumns(draftObject)` suggests mappings against a draft object type
 * by declared `column`, name equality, and snake_case/camelCase conversion
 * (see `matchColumnsToProperties` in `./ontology-draft`).
 */
export function useFoundryLiteDatasetColumnMapping(
  client: Pick<FoundryLiteGeneratedClient, "datasets">,
  datasetRef: FoundryLiteDatasetColumnMappingReference | null | undefined,
  options: FoundryLiteDatasetColumnMappingOptions = {},
): FoundryLiteDatasetColumnMappingState {
  const { key, version, enabled = true, ...queryOptions } = options;
  const namespace = datasetRef?.namespace ?? null;
  const name = datasetRef?.name ?? null;
  const canInspectDataset = Boolean(namespace && name);
  const queryKey = useMemo(
    () => key ?? ["datasets", "column-mapping", namespace, name, version ?? null],
    [key, name, namespace, version],
  );
  const load = useCallback(() => {
    if (!namespace || !name) {
      throw new FoundryLiteApiError(
        0,
        "DATASET_NOT_SELECTED",
        "Select a dataset before inspecting its columns.",
        { missingFields: ["datasetRef"] },
        null,
        false,
      );
    }
    return client.datasets.inspect(namespace, name, version ? { version } : undefined);
  }, [client, name, namespace, version]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canInspectDataset,
  });
  const schema = query.data?.schema ?? null;
  const columns = useMemo(() => ontologyDatasetColumnsFromSchema(schema), [schema]);
  const matchColumns = useCallback(
    (draftObject: OntologyDraftObjectType | null | undefined) =>
      matchColumnsToProperties(columns, draftObject),
    [columns],
  );

  return {
    ...query,
    datasetRef: query.data?.dataset ?? (namespace && name ? `${namespace}.${name}` : null),
    inspection: query.data,
    schema,
    columns,
    hasColumns: columns.length > 0,
    canInspectDataset,
    disabledReason: canInspectDataset ? null : "Select a dataset before inspecting its columns.",
    matchColumns,
  };
}

/** Context-client variant of {@link useFoundryLiteDatasetColumnMapping}. */
export function useFoundryLiteProvidedDatasetColumnMapping(
  datasetRef: FoundryLiteDatasetColumnMappingReference | null | undefined,
  options: FoundryLiteDatasetColumnMappingOptions = {},
): FoundryLiteDatasetColumnMappingState {
  const client = useFoundryLiteClient();
  return useFoundryLiteDatasetColumnMapping(client, datasetRef, options);
}

export type FoundryLiteDatasetAggregateOptions =
  FoundryLiteQueryOptions<DatasetAggregationResult> & {
    key?: readonly unknown[];
  };

export type FoundryLiteDatasetAggregateState =
  FoundryLiteQueryState<DatasetAggregationResult> & {
    datasetRef: string | null;
    groups: DatasetAggregationGroup[];
    totalGroups: number;
    filteredRowCount: number;
    rowCount: number;
    hasGroups: boolean;
    canAggregate: boolean;
    disabledReason: string | null;
  };

/**
 * Server-side aggregation over one committed dataset version. The backend
 * validates columns, masking, filters, groupBy, and metrics before returning
 * grouped results to the browser.
 */
export function useFoundryLiteDatasetAggregate(
  client: Pick<FoundryLiteGeneratedClient, "datasets">,
  datasetRef: FoundryLiteDatasetColumnMappingReference | null | undefined,
  request: DatasetAggregateRequest | null | undefined,
  options: FoundryLiteDatasetAggregateOptions = {},
): FoundryLiteDatasetAggregateState {
  const { key, enabled = true, ...queryOptions } = options;
  const namespace = datasetRef?.namespace ?? null;
  const name = datasetRef?.name ?? null;
  const canAggregate = Boolean(namespace && name && request);
  const requestKey = useMemo(() => JSON.stringify(request ?? null), [request]);
  const stableRequest = useMemo(() => request, [requestKey]);
  const queryKey = useMemo(
    () => key ?? ["datasets", "aggregate", namespace, name, stableRequest],
    [key, name, namespace, stableRequest],
  );
  const load = useCallback(() => {
    if (!namespace || !name || !stableRequest) {
      throw new FoundryLiteApiError(
        0,
        "DATASET_AGGREGATE_NOT_READY",
        "Select a dataset and aggregate request before aggregating.",
        { missingFields: ["datasetRef", "request"] },
        null,
        false,
      );
    }
    return client.datasets.aggregate(namespace, name, stableRequest);
  }, [client, name, namespace, stableRequest]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canAggregate,
  });

  return {
    ...query,
    datasetRef: namespace && name ? `${namespace}.${name}` : null,
    groups: query.data?.groups ?? [],
    totalGroups: query.data?.totalGroups ?? 0,
    filteredRowCount: query.data?.filteredRowCount ?? 0,
    rowCount: query.data?.rowCount ?? 0,
    hasGroups: (query.data?.groups.length ?? 0) > 0,
    canAggregate,
    disabledReason: canAggregate ? null : "Select a dataset and aggregate request before aggregating.",
  };
}

/** Context-client variant of {@link useFoundryLiteDatasetAggregate}. */
export function useFoundryLiteProvidedDatasetAggregate(
  datasetRef: FoundryLiteDatasetColumnMappingReference | null | undefined,
  request: DatasetAggregateRequest | null | undefined,
  options: FoundryLiteDatasetAggregateOptions = {},
): FoundryLiteDatasetAggregateState {
  const client = useFoundryLiteClient();
  return useFoundryLiteDatasetAggregate(client, datasetRef, request, options);
}

export type FoundryLiteObjectAggregateOptions = FoundryLiteQueryOptions<ObjectAggregationResult> & {
  key?: readonly unknown[];
};

export type FoundryLiteObjectAggregateState = FoundryLiteQueryState<ObjectAggregationResult> & {
  objectApiName: string | null;
  groups: ObjectAggregationGroup[];
  totalGroups: number;
  hasGroups: boolean;
  canAggregate: boolean;
  disabledReason: string | null;
};

/**
 * Server-side aggregation over one object type
 * (`client.objects.generic.aggregate`): select count/sum/avg/min/max metrics,
 * optionally grouped and filtered, without paging raw objects to the browser.
 */
export function useFoundryLiteObjectAggregate(
  client: Pick<FoundryLiteGeneratedClient, "objects">,
  objectApiName: string | null | undefined,
  request: ObjectAggregateRequest,
  options: FoundryLiteObjectAggregateOptions = {},
): FoundryLiteObjectAggregateState {
  const { key, enabled = true, ...queryOptions } = options;
  const canAggregate = Boolean(objectApiName);
  const requestKey = useMemo(() => JSON.stringify(request), [request]);
  const stableRequest = useMemo(() => request, [requestKey]);
  const queryKey = useMemo(
    () => key ?? ["objects", "aggregate", objectApiName ?? null, stableRequest],
    [key, objectApiName, stableRequest],
  );
  const load = useCallback(() => {
    if (!objectApiName) {
      throw new FoundryLiteApiError(
        0,
        "OBJECT_TYPE_NOT_SELECTED",
        "Select an object type before aggregating objects.",
        { missingFields: ["objectApiName"] },
        null,
        false,
      );
    }
    return client.objects.generic.aggregate(objectApiName, stableRequest);
  }, [client, objectApiName, stableRequest]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canAggregate,
  });

  return {
    ...query,
    objectApiName: objectApiName ?? null,
    groups: query.data?.groups ?? [],
    totalGroups: query.data?.totalGroups ?? 0,
    hasGroups: (query.data?.groups.length ?? 0) > 0,
    canAggregate,
    disabledReason: canAggregate ? null : "Select an object type before aggregating objects.",
  };
}

/** Context-client variant of {@link useFoundryLiteObjectAggregate}. */
export function useFoundryLiteProvidedObjectAggregate(
  objectApiName: string | null | undefined,
  request: ObjectAggregateRequest,
  options: FoundryLiteObjectAggregateOptions = {},
): FoundryLiteObjectAggregateState {
  const client = useFoundryLiteClient();
  return useFoundryLiteObjectAggregate(client, objectApiName, request, options);
}

export type FoundryLiteInterfaceQueryOptions =
  FoundryLiteQueryOptions<ObjectQueryResult<GenericObject>> &
    InterfaceQueryRequest & {
      key?: readonly unknown[];
    };

export type FoundryLiteInterfaceQueryState = FoundryLiteQueryState<
  ObjectQueryResult<GenericObject>
> & {
  interfaceApiName: string | null;
  objects: GenericObject[];
  items: GenericObject[];
  nextCursor: string | null;
  hasNextPage: boolean;
  canQueryInterface: boolean;
  disabledReason: string | null;
};

/**
 * Query objects through an interface type
 * (`client.interfaces.generic.query`), returning implementing objects across
 * object types with the shared filter/orderBy/limit request shape.
 */
export function useFoundryLiteInterfaceQuery(
  client: Pick<FoundryLiteGeneratedClient, "interfaces">,
  interfaceApiName: string | null | undefined,
  options: FoundryLiteInterfaceQueryOptions = {},
): FoundryLiteInterfaceQueryState {
  const { key, filter, orderBy, limit, enabled = true, ...queryOptions } = options;
  const canQueryInterface = Boolean(interfaceApiName);
  const requestKey = useMemo(
    () => JSON.stringify({ filter: filter ?? null, orderBy: orderBy ?? null, limit: limit ?? null }),
    [filter, limit, orderBy],
  );
  const request = useMemo<InterfaceQueryRequest>(
    () => ({ filter, orderBy, limit }),
    [requestKey],
  );
  const queryKey = useMemo(
    () => key ?? ["interfaces", "query", interfaceApiName ?? null, request],
    [interfaceApiName, key, request],
  );
  const load = useCallback(() => {
    if (!interfaceApiName) {
      throw new FoundryLiteApiError(
        0,
        "INTERFACE_TYPE_NOT_SELECTED",
        "Select an interface type before querying objects.",
        { missingFields: ["interfaceApiName"] },
        null,
        false,
      );
    }
    return client.interfaces.generic.query(interfaceApiName, request);
  }, [client, interfaceApiName, request]);
  const query = useFoundryLiteQuery(queryKey, load, {
    ...queryOptions,
    enabled: enabled && canQueryInterface,
  });
  const items = query.data?.items ?? [];
  const nextCursor = query.data?.nextCursor ?? null;

  return {
    ...query,
    interfaceApiName: interfaceApiName ?? null,
    objects: items,
    items,
    nextCursor,
    hasNextPage: nextCursor !== null,
    canQueryInterface,
    disabledReason: canQueryInterface ? null : "Select an interface type before querying objects.",
  };
}

/** Context-client variant of {@link useFoundryLiteInterfaceQuery}. */
export function useFoundryLiteProvidedInterfaceQuery(
  interfaceApiName: string | null | undefined,
  options: FoundryLiteInterfaceQueryOptions = {},
): FoundryLiteInterfaceQueryState {
  const client = useFoundryLiteClient();
  return useFoundryLiteInterfaceQuery(client, interfaceApiName, options);
}

export type FoundryLiteFunctionExecutePayload = {
  functionApiName: string;
  inputs?: Record<string, unknown>;
};

export type FoundryLiteFunctionExecuteState = FoundryLiteMutationState<
  FunctionExecutionResult,
  FoundryLiteFunctionExecutePayload
> & {
  execution: FunctionExecutionResult | null;
};

export function foundryLiteFunctionExecuteLockKey(
  payload: FoundryLiteFunctionExecutePayload,
): string {
  return `functions:execute:${payload.functionApiName}`;
}

/**
 * Execute a governed ontology function (`client.functions.generic.execute`)
 * as a mutation, exposing the run evidence (`logicRunId`, `resultHash`,
 * `output`) as `execution`.
 */
export function useFoundryLiteFunctionExecute(
  client: Pick<FoundryLiteGeneratedClient, "functions">,
  options: FoundryLiteMutationOptions<
    FunctionExecutionResult,
    FoundryLiteFunctionExecutePayload
  > = {},
): FoundryLiteFunctionExecuteState {
  const mutate = useCallback(
    (payload: FoundryLiteFunctionExecutePayload) =>
      client.functions.generic.execute(payload.functionApiName, { inputs: payload.inputs }),
    [client],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    ...options,
    lockKey: options.lockKey ?? foundryLiteFunctionExecuteLockKey,
  });
  return { ...mutation, execution: mutation.result };
}

/** Context-client variant of {@link useFoundryLiteFunctionExecute}. */
export function useFoundryLiteProvidedFunctionExecute(
  options: FoundryLiteMutationOptions<
    FunctionExecutionResult,
    FoundryLiteFunctionExecutePayload
  > = {},
): FoundryLiteFunctionExecuteState {
  const client = useFoundryLiteClient();
  return useFoundryLiteFunctionExecute(client, options);
}

export type FoundryLiteBatchActionInvoker<TRequest> = {
  applyBatch(payload: TRequest, options: { idempotencyKey: string }): Promise<ActionBatchApplyResponse>;
};

export type FoundryLiteBatchActionSubmitPayload<TRequest> = {
  request: TRequest;
  idempotencyKey?: string;
};

export type FoundryLiteBatchActionSubmitOptions<TRequest> = FoundryLiteMutationOptions<
  ActionBatchApplyResponse,
  FoundryLiteBatchActionSubmitPayload<TRequest>
> & {
  idempotencyKey?:
    | string
    | ((payload: FoundryLiteBatchActionSubmitPayload<TRequest>) => string | undefined);
};

export type FoundryLiteBatchActionSubmitState<TRequest> = FoundryLiteMutationState<
  ActionBatchApplyResponse,
  FoundryLiteBatchActionSubmitPayload<TRequest>
> & {
  actionName: string;
  results: ActionBatchApplyResponse["results"];
};

export function foundryLiteBatchActionSubmitLockKey(actionName: string): string {
  return `actions:batch:${actionName}`;
}

/**
 * Submit a batch action (`client.actions.<Name>.applyBatch`) as a mutation,
 * e.g. `useFoundryLiteBatchActionSubmit(client.actions.ApproveOrder,
 * "ApproveOrder")`. Batch apply requires an Idempotency-Key: the hook resolves
 * it from `payload.idempotencyKey`, then `options.idempotencyKey`, and
 * otherwise generates one at the call site with the exported `idempotencyKey`
 * helper — the same convention as `useFoundryLiteActionSubmit`.
 */
export function useFoundryLiteBatchActionSubmit<TRequest>(
  action: FoundryLiteBatchActionInvoker<TRequest>,
  actionName: string,
  options: FoundryLiteBatchActionSubmitOptions<TRequest> = {},
): FoundryLiteBatchActionSubmitState<TRequest> {
  const { idempotencyKey: suppliedIdempotencyKey, ...mutationOptions } = options;
  const mutate = useCallback(
    (payload: FoundryLiteBatchActionSubmitPayload<TRequest>) => {
      const supplied =
        typeof suppliedIdempotencyKey === "function"
          ? suppliedIdempotencyKey(payload)
          : suppliedIdempotencyKey;
      const resolvedIdempotencyKey =
        payload.idempotencyKey ??
        supplied ??
        foundryLiteGeneratedIdempotencyKey(actionName, "batch");
      return action.applyBatch(payload.request, { idempotencyKey: resolvedIdempotencyKey });
    },
    [action, actionName, suppliedIdempotencyKey],
  );
  const defaultLockKey = useCallback(
    () => foundryLiteBatchActionSubmitLockKey(actionName),
    [actionName],
  );
  const mutation = useFoundryLiteMutation(mutate, {
    ...mutationOptions,
    lockKey: mutationOptions.lockKey ?? defaultLockKey,
  });
  return {
    ...mutation,
    actionName,
    results: mutation.result?.results ?? [],
  };
}

export type FoundryLiteMaterializationSpecsOptions =
  FoundryLiteQueryOptions<MaterializationSpecList> & {
    key?: readonly unknown[];
  };

export type FoundryLiteMaterializationSpecsState =
  FoundryLiteQueryState<MaterializationSpecList> & {
    specs: Array<Record<string, unknown>>;
    specCount: number;
    hasSpecs: boolean;
  };

/**
 * List declared object materialization specs (`client.materializations.list`)
 * so screens can show which object types write snapshot datasets back out.
 */
export function useFoundryLiteMaterializationSpecs(
  client: Pick<FoundryLiteGeneratedClient, "materializations">,
  options: FoundryLiteMaterializationSpecsOptions = {},
): FoundryLiteMaterializationSpecsState {
  const query = useFoundryLiteQuery(
    options.key ?? ["materializations", "specs"],
    () => client.materializations.list(),
    options,
  );
  const specs = query.data?.specs ?? [];
  return {
    ...query,
    specs,
    specCount: specs.length,
    hasSpecs: specs.length > 0,
  };
}

/** Context-client variant of {@link useFoundryLiteMaterializationSpecs}. */
export function useFoundryLiteProvidedMaterializationSpecs(
  options: FoundryLiteMaterializationSpecsOptions = {},
): FoundryLiteMaterializationSpecsState {
  const client = useFoundryLiteClient();
  return useFoundryLiteMaterializationSpecs(client, options);
}

export type FoundryLiteOntologyBuilderOptions = Omit<OntologyBuilderRunOptions, "onState"> & {
  initialState?: OntologyBuilderRecipeState;
  onState?: (state: OntologyBuilderRecipeState) => void;
  onSuccess?: (state: OntologyBuilderRecipeState) => void;
  onError?: (error: FoundryLiteApiError, state: OntologyBuilderRecipeState) => void;
};

export type FoundryLiteOntologyBuilderState = OntologyBuilderRecipeState & {
  isRunning: boolean;
  run(
    input: OntologyBuilderRunInput,
    options?: OntologyBuilderRunOptions,
  ): Promise<OntologyBuilderRecipeState>;
  reset(): void;
};

/**
 * Stateful wrapper around `createOntologyBuilderRecipe(client).run`: drives
 * the governed Dataset Explorer -> Builder -> Validate -> Impact -> Proposal
 * -> Review -> Execute -> Reindex -> SDK-drift flow and mirrors each recipe
 * state snapshot into hook state, following the
 * `useFoundryLiteSourceOnboarding` pattern.
 */
export function useFoundryLiteOntologyBuilder(
  client: Pick<FoundryLiteGeneratedClient, "datasets" | "ontology" | "operations">,
  options: FoundryLiteOntologyBuilderOptions = {},
): FoundryLiteOntologyBuilderState {
  const recipe = useMemo(() => createOntologyBuilderRecipe(client), [client]);
  const initialState = options.initialState ?? recipe.initialState();
  const [state, setState] = useState<OntologyBuilderRecipeState>(initialState);
  const [isRunning, setIsRunning] = useState(false);
  const mountedRef = useRef(true);
  const optionsRef = useRef(options);

  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    const nextState = recipe.initialState();
    if (mountedRef.current) setState(nextState);
  }, [recipe]);

  const run = useCallback(
    async (
      input: OntologyBuilderRunInput,
      runOptions: OntologyBuilderRunOptions = {},
    ): Promise<OntologyBuilderRecipeState> => {
      const currentOptions = optionsRef.current;
      if (mountedRef.current) setIsRunning(true);
      const onState = (nextState: OntologyBuilderRecipeState) => {
        if (mountedRef.current) setState(nextState);
        currentOptions.onState?.(nextState);
        runOptions.onState?.(nextState);
      };
      try {
        const finalState = await recipe.run(input, { ...currentOptions, ...runOptions, onState });
        if (finalState.error) currentOptions.onError?.(finalState.error, finalState);
        else currentOptions.onSuccess?.(finalState);
        return finalState;
      } finally {
        if (mountedRef.current) setIsRunning(false);
      }
    },
    [recipe],
  );

  return { ...state, isRunning, run, reset };
}

/** Context-client variant of {@link useFoundryLiteOntologyBuilder}. */
export function useFoundryLiteProvidedOntologyBuilder(
  options: FoundryLiteOntologyBuilderOptions = {},
): FoundryLiteOntologyBuilderState {
  const session = useFoundryLiteSession();
  const state = useFoundryLiteOntologyBuilder(session.client, options);
  return useMemo(
    () => ({ ...state, requestId: state.requestId ?? session.lastRequestId }),
    [session.lastRequestId, state],
  );
}

function foundryLiteOntologyResourceNotSelectedError(): FoundryLiteApiError {
  return new FoundryLiteApiError(
    0,
    "ONTOLOGY_RESOURCE_NOT_SELECTED",
    "Select an ontology resource before loading usage and dependents.",
    { missingFields: ["apiName"] },
    null,
    false,
  );
}

function readFoundryLiteOntologyMigrationPlan(
  validation: OntologyValidationResult | null | undefined,
): Record<string, unknown> | null {
  const plan = validation?.migration_plan;
  return isFoundryLiteRecord(plan) ? plan : null;
}

function readFoundryLiteOntologyMigrationChanges(
  value: unknown,
): FoundryLiteOntologyMigrationChangeView[] {
  if (!Array.isArray(value)) return [];
  const changes: FoundryLiteOntologyMigrationChangeView[] = [];
  for (const rawChange of value) {
    if (!isFoundryLiteRecord(rawChange)) continue;
    changes.push({
      kind: readFoundryLiteOntologyPlanString(rawChange.kind),
      path: readFoundryLiteOntologyPlanString(rawChange.path),
      status: readFoundryLiteOntologyPlanString(rawChange.status),
      reason: readFoundryLiteOntologyPlanString(rawChange.reason),
      requiresSdkMajorVersion: rawChange.requiresSdkMajorVersion === true,
      requiresObjectReindex: rawChange.requiresObjectReindex === true,
      details: isFoundryLiteRecord(rawChange.details) ? rawChange.details : null,
    });
  }
  return changes;
}

function readFoundryLiteOntologyReindexOperations(
  value: unknown,
): FoundryLiteOntologyReindexOperationView[] {
  if (!Array.isArray(value)) return [];
  const operations: FoundryLiteOntologyReindexOperationView[] = [];
  for (const rawOperation of value) {
    if (!isFoundryLiteRecord(rawOperation)) continue;
    operations.push({
      objectTypeApiName: readFoundryLiteOntologyPlanString(rawOperation.objectTypeApiName),
      reason: readFoundryLiteOntologyPlanString(rawOperation.reason),
      changedFields: Array.isArray(rawOperation.changedFields)
        ? rawOperation.changedFields.filter(
            (field): field is string => typeof field === "string",
          )
        : [],
      reindexKey: readFoundryLiteOntologyPlanString(rawOperation.reindexKey),
    });
  }
  return operations;
}

function readFoundryLiteOntologyPlanString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function isFoundryLiteOntologySdkSurfacePath(path: string): boolean {
  return (
    path.startsWith("actionTypes.") ||
    path.startsWith("interfaces.") ||
    path.startsWith("functionTypes.")
  );
}
