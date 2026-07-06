import {
  adminOperationsBoard,
  assertFoundryLiteSdkFresh,
  createFoundryLiteOntologyIndex,
  createFoundryLiteOsdkClient,
  getActionType,
  getObjectType,
  normalizeFoundryLiteError,
  pollFoundryLiteOperation,
  sdkOntologyDriftReport,
  streamFoundryLiteOperationEvents,
} from "./generated";
import type {
  ActionApiName,
  ActionTypeRegistry,
  ActionWritebackQueueFilters,
  ActionWritebackReconciliationRequest,
  ActionWritebackRecoveryApprovalRequest,
  AdminOperationsBoard,
  AdminRequiredBackendSurface,
  AdminTaskArea,
  AdminTaskCommandRow,
  AdminTaskView,
  AipAgentRunRequest,
  AipBuilderRunRequest,
  AipBuilderValidateRequest,
  BackupRestoreArtifactRestoreRequest,
  BackupRestoreRecoveryOverview,
  BackupRestoreModeStartRequest,
  BackupRestorePostRestoreValidationRequest,
  BackupRestorePreflightRequest,
  BackupRestoreResumeApprovalRequest,
  ConnectorConnection,
  ConnectorResource,
  ConnectorResourceSyncStartRequest,
  ConnectorResourceTestResult,
  ConnectorSyncWorkflowStartRequest,
  Dataset,
  DatasetInspectionPayload,
  DeadLetterRecordStatus,
  FoundryLiteApiError,
  FoundryLiteGeneratedClient,
  FoundryLiteOperationStreamEvent,
  FoundryLiteSdkDriftReport,
  InsightActionProposal,
  InsightReviewAssignRequest,
  InsightReviewCreateRequest,
  InsightReviewDecisionRequest,
  InsightReviewListFilters,
  InsightReviewPayload,
  InsightReviewQueryResult,
  IcebergMaintenancePlanOptions,
  MediaIndexDerivativeRequest,
  MediaProcessRequest,
  MediaSearchRequest,
  MediaSetCreateRequest,
  MediaUploadRequest,
  ObjectApiName,
  ObjectTypeRegistry,
  OntologyBranchCreateRequest,
  OntologyBranchDetailPayload,
  OntologyBranchDiffResult,
  OntologyBranchListFilters,
  OntologyBranchListResult,
  OntologyBranchPayload,
  OntologyBranchProposeRequest,
  OntologyBranchRebaseRequest,
  OntologyBranchRebaseResolution,
  OntologyBranchUpdateRequest,
  OntologyCatalog,
  OntologyObjectReindexResult,
  OntologyProposalAssignRequest,
  OntologyProposalDecisionRequest,
  OntologyProposalExecuteRequest,
  OntologyProposalListFilters,
  OntologyProposalPayload,
  OntologyProposalSubmitRequest,
  OntologyProposalUpdateRequest,
  OntologyProposalWithdrawRequest,
  OntologyResourceUsageOptions,
  OntologyValidationResult,
  ObservabilityDetectRequest,
  PipelineBranchCreateRequest,
  PipelineBranchProposeRequest,
  PipelineBranchRebaseRequest,
  PipelineDeployRequest,
  PipelineGraphUpdateRequest,
  PipelinePreviewNodeRequest,
  PipelineProposalAssignRequest,
  PipelineProposalDecisionRequest,
  PipelineRunStartRequest,
  PipelineScheduleUpsertRequest,
  OsdkActionPayload,
  OsdkActionResult,
  OsdkFetchPageOptions,
  OsdkMediaUploadOptions,
  OutboxPublishRequest,
  PollFoundryLiteOperationOptions,
  PromptArtifactReadResult,
  ProductWorkflowRun,
  RestConnectorConnectionCreateRequest,
  RestConnectorResourceUpsertRequest,
  ProjectFolder,
  ResourceItem,
  ResourceListOptions,
  ResourceProject,
  RuntimeRunDetail,
  RuntimeRunQueryFilters,
  RuntimeRunQueryResult,
  RuntimeRow,
  RuntimeRunType,
  SourceBatchFileUploadRequest,
  SourceAgent,
  SourceAgentRegisterRequest,
  SourceConnection,
  SourceCredential,
  SourceCredentialCreateRequest,
  SourceCsvUploadRequest,
  SourceDebeziumCreateRequest,
  SourceDebeziumSyncStartRequest,
  SourceExploreRequest,
  SourceExploreResult,
  SourceManagedSync,
  SourceManagedSyncCreateRequest,
  SourceManagedSyncRun,
  SourceManagedSyncRunStartRequest,
  SourceMediaUploadRequest,
  SourceNetworkPolicy,
  SourceNetworkPolicyCreateRequest,
  SourceOperationResult,
  SourceTemplate,
  SourceWebhookListenerCreateRequest,
  StreamFoundryLiteOperationEventsOptions,
  TransformSqlRegisterRequest,
} from "./generated";

export type DatasetReference = {
  namespace: string;
  name: string;
};

export type DatasetExplorerSelection = DatasetReference & {
  version?: string;
  lineageResourceId?: string;
  previewLimit?: number;
};

export type ConnectorSyncPollOptions =
  PollFoundryLiteOperationOptions<ProductWorkflowRun> & {
    idempotencyKey: string;
  };

export type ConnectorOnboardingPhase =
  | "idle"
  | "creating_connection"
  | "upserting_resource"
  | "testing_resource"
  | "starting_sync"
  | "polling_workflow"
  | "ready"
  | "failed";

export type ConnectorOnboardingIdempotencyKeys = {
  createConnection: string;
  upsertResource: string;
  startFirstSync: string;
};

export type ConnectorOnboardingInput = {
  connection: RestConnectorConnectionCreateRequest;
  resourceName: string;
  resource: RestConnectorResourceUpsertRequest;
  sync?: ConnectorResourceSyncStartRequest;
  idempotencyKeys: ConnectorOnboardingIdempotencyKeys;
};

export type ConnectorOnboardingRecipeState = {
  phase: ConnectorOnboardingPhase;
  requestId: string | null;
  connection: ConnectorConnection | null;
  resource: ConnectorResource | null;
  testResult: ConnectorResourceTestResult | null;
  workflowRun: ProductWorkflowRun | null;
  operationsPath: string | null;
  error: FoundryLiteApiError | null;
  retryable: boolean;
};

export type ConnectorOnboardingRunOptions =
  PollFoundryLiteOperationOptions<ProductWorkflowRun> & {
    shouldPollWorkflow?: boolean;
    onState?: (state: ConnectorOnboardingRecipeState) => void;
  };

export type ResourceBrowserLoadOptions = ResourceListOptions & {
  favoriteIds?: Iterable<string>;
  trashedIds?: Iterable<string>;
};

export type ResourceBrowserView = {
  projects: ResourceProject[];
  folders: ProjectFolder[];
  items: ResourceItem[];
  activeItems: ResourceItem[];
  favoriteItems: ResourceItem[];
  trashedItems: ResourceItem[];
  selectedProjectId: string | null;
  selectedFolderId: string | null;
  isBackendBacked: boolean;
};

export type ResourceBrowserViewInput = {
  projects: ResourceProject[];
  folders?: ProjectFolder[];
  items: ResourceItem[];
  selectedProjectId?: string | null;
  selectedFolderId?: string | null;
  favoriteIds?: Iterable<string>;
  trashedIds?: Iterable<string>;
  isBackendBacked?: boolean;
};

export type SourceOnboardingPhase =
  | "select_kind"
  | "configure_source"
  | "test_source"
  | "upload_or_start_sync"
  | "commit_or_workflow"
  | "inspect_dataset_or_media"
  | "operations_evidence"
  | "ready_for_ontology"
  | "failed";

export type SourceOnboardingInput =
  | { kind: "csv_upload"; payload: SourceCsvUploadRequest; idempotencyKey: string }
  | { kind: "batch_file"; payload: SourceBatchFileUploadRequest; idempotencyKey: string }
  | { kind: "webhook_listener"; payload: SourceWebhookListenerCreateRequest; idempotencyKey: string }
  | {
      kind: "debezium_cdc";
      payload: SourceDebeziumCreateRequest;
      createIdempotencyKey: string;
      startSync?: SourceDebeziumSyncStartRequest;
      startSyncIdempotencyKey?: string;
    }
  | { kind: "media_upload"; payload: SourceMediaUploadRequest; idempotencyKey: string };

export type SourceOnboardingRecipeState = {
  phase: SourceOnboardingPhase;
  requestId: string | null;
  source: SourceConnection | null;
  datasetRef: string | null;
  mediaSetId: string | null;
  workflowRun: ProductWorkflowRun | null;
  commitResult: Record<string, unknown> | null;
  commitResults: Array<Record<string, unknown>>;
  mediaCommitResult: Record<string, unknown> | null;
  testResult: Record<string, unknown> | null;
  operationsPath: string | null;
  error: FoundryLiteApiError | null;
  retryable: boolean;
};

export type SourceOnboardingRunOptions = {
  onState?: (state: SourceOnboardingRecipeState) => void;
};

export type SourceWizardPhase =
  | "select_template"
  | "store_credentials"
  | "prepare_network"
  | "explore_source"
  | "create_sync"
  | "start_sync_run"
  | "inspect_run"
  | "operations_evidence"
  | "ready_for_ontology"
  | "failed";

export type SourceWizardCredentialStep = {
  payload: SourceCredentialCreateRequest;
  idempotencyKey: string;
};

export type SourceWizardAgentStep = {
  payload: SourceAgentRegisterRequest;
  idempotencyKey: string;
};

export type SourceWizardNetworkPolicyStep = {
  payload: SourceNetworkPolicyCreateRequest;
  idempotencyKey: string;
};

export type SourceWizardManagedSyncStep = {
  payload: SourceManagedSyncCreateRequest;
  idempotencyKey: string;
};

export type SourceWizardRunStep = {
  payload?: SourceManagedSyncRunStartRequest;
  idempotencyKey: string;
};

export type SourceWizardInput = {
  sourceType: string;
  credential?: SourceWizardCredentialStep;
  agent?: SourceWizardAgentStep;
  networkPolicy?: SourceWizardNetworkPolicyStep;
  exploration?: SourceExploreRequest;
  sync: SourceWizardManagedSyncStep;
  startRun?: SourceWizardRunStep;
};

export type SourceWizardRecipeState = {
  phase: SourceWizardPhase;
  requestId: string | null;
  sourceType: string | null;
  templates: SourceTemplate[];
  selectedTemplate: SourceTemplate | null;
  credential: SourceCredential | null;
  agent: SourceAgent | null;
  networkPolicy: SourceNetworkPolicy | null;
  exploration: SourceExploreResult | null;
  sync: SourceManagedSync | null;
  syncRun: SourceManagedSyncRun | null;
  datasetRef: string | null;
  mediaSetId: string | null;
  operationsPath: string | null;
  error: FoundryLiteApiError | null;
  retryable: boolean;
};

export type SourceWizardRunOptions = {
  onState?: (state: SourceWizardRecipeState) => void;
};

export type OntologyBuilderPhase =
  | "explore_dataset"
  | "design_draft"
  | "validate_draft"
  | "impact_review"
  | "submit_proposal"
  | "await_review"
  | "execute_proposal"
  | "reindex"
  | "check_sdk_drift"
  | "ready"
  | "blocked"
  | "failed";

export type OntologyBuilderStepId =
  | "dataset_explorer"
  | "draft_builder"
  | "validate"
  | "impact_plan"
  | "proposal"
  | "review"
  | "execute"
  | "reindex_status"
  | "sdk_drift";

export type OntologyBuilderStep = {
  id: OntologyBuilderStepId;
  title: string;
  description: string;
  route: string;
  reactHook: string;
  primarySdkSurface: string;
};

export type OntologyBuilderNavigationItem = OntologyBuilderStep & {
  stepNumber: number;
  isSelected: boolean;
};

export type OntologyBuilderNavigationView = {
  selectedStepId: OntologyBuilderStepId;
  steps: OntologyBuilderNavigationItem[];
  selectedStep: OntologyBuilderNavigationItem;
  stepCount: number;
};

export type OntologyBuilderProposalInput = {
  title: string;
  description?: string | null;
  idempotencyKey: string;
};

export type OntologyBuilderBranchInput = {
  name: string;
  createIdempotencyKey: string;
  proposeIdempotencyKey?: string;
  rebaseResolutions?: OntologyBranchRebaseResolution[];
};

export type OntologyBuilderRunInput = {
  dataset?: DatasetExplorerSelection;
  yamlText: string;
  proposal: OntologyBuilderProposalInput;
  branch?: OntologyBuilderBranchInput;
  submitWhenBlocked?: boolean;
  assign?: OntologyProposalAssignRequest;
  decision?: { decision: string; comment?: string | null };
  shouldExecute?: boolean;
  shouldReindex?: boolean;
  shouldCheckSdkDrift?: boolean;
};

export type OntologyBuilderRecipeState = {
  phase: OntologyBuilderPhase;
  requestId: string | null;
  datasetRef: string | null;
  inspection: DatasetInspectionPayload | null;
  validation: OntologyValidationResult | null;
  migrationPlan: Record<string, unknown> | null;
  isBlocked: boolean;
  branch: OntologyBranchPayload | null;
  branchDiff: OntologyBranchDiffResult | null;
  proposal: OntologyProposalPayload | null;
  reindexResults: OntologyObjectReindexResult[];
  driftReport: FoundryLiteSdkDriftReport | null;
  operationsPath: string | null;
  error: FoundryLiteApiError | null;
  retryable: boolean;
};

export type OntologyBuilderRunOptions = {
  onState?: (state: OntologyBuilderRecipeState) => void;
};

export type InsightReviewQueueSelection = {
  selectedReviewId?: string | null;
  currentUserId?: string | null;
};

export type InsightReviewQueueView = {
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
  hasPendingReviews: boolean;
  hasAssignedReviews: boolean;
  hasCurrentUserAssignedReviews: boolean;
  hasDecidedReviews: boolean;
  hasHighPriorityReviews: boolean;
  canReviewSelectedActionProposal: boolean;
};

export type OperationsEvidenceSelection = {
  runType?: RuntimeRunType | string | null;
  runId?: string | null;
  promptArtifactId?: string | null;
};

export type OperationsEvidenceInvestigation = {
  runs: RuntimeRunQueryResult;
  detail: RuntimeRunDetail | null;
  promptArtifact: PromptArtifactReadResult | null;
};

export type OperatorWorkspaceHomeOptions = {
  runFilters?: RuntimeRunQueryFilters;
};

export type OperatorWorkspaceHomeInput = {
  catalog: OntologyCatalog | null;
  datasets: Dataset[];
  runs: RuntimeRunQueryResult;
  adminLaunchpad: AdminOperationsLaunchpadModel;
  recoveryOverview: BackupRestoreRecoveryOverview | null;
};

export type OperatorWorkspaceHomeView = OperatorWorkspaceHomeInput & {
  datasetCount: number;
  objectTypeCount: number;
  actionTypeCount: number;
  runCount: number;
  failedRunCount: number;
  runningRunCount: number;
  hasDatasets: boolean;
  hasOntologyTypes: boolean;
  hasFailedRuns: boolean;
  hasRunningRuns: boolean;
  hasAdminBrowserActions: boolean;
  hasOperatorCommands: boolean;
  hasRecoveryActions: boolean;
  recommendedAdminSectionId: AdminLaunchpadSectionId;
};

export type OperatorWorkspaceAreaId =
  | "home"
  | "data"
  | "ontology"
  | "operations"
  | "insights"
  | "media"
  | "aip"
  | "pipeline"
  | "admin"
  | "recovery";

export type OperatorWorkspaceNavPriority = "normal" | "attention";

export type OperatorWorkspaceNavItem = {
  id: OperatorWorkspaceAreaId;
  title: string;
  description: string;
  route: string;
  count: number | null;
  badge: string | null;
  priority: OperatorWorkspaceNavPriority;
  isSelected: boolean;
  isEnabled: boolean;
  disabledReason: string | null;
};

export type OperatorWorkspaceQuickAction = {
  id: string;
  title: string;
  description: string;
  area: OperatorWorkspaceAreaId;
  route: string;
  count: number | null;
  priority: OperatorWorkspaceNavPriority;
};

export type OperatorWorkspaceNavigationView = {
  selectedAreaId: OperatorWorkspaceAreaId;
  navItems: OperatorWorkspaceNavItem[];
  selectedItem: OperatorWorkspaceNavItem;
  attentionItems: OperatorWorkspaceNavItem[];
  quickActions: OperatorWorkspaceQuickAction[];
  hasAttention: boolean;
};
export type OperatorWorkspaceAreaSurface = OperatorWorkspaceNavItem & {
  recipeEntry: string;
  reactHook: string;
  primarySdkSurface: string;
};
export type OperatorWorkspaceShellView = {
  home: OperatorWorkspaceHomeView;
  navigation: OperatorWorkspaceNavigationView;
  areaSurfaces: OperatorWorkspaceAreaSurface[];
  selectedSurface: OperatorWorkspaceAreaSurface;
  selectedAreaId: OperatorWorkspaceAreaId;
  selectedRoute: string;
  selectedTitle: string;
  selectedDescription: string;
  selectedBadge: string | null;
  selectedCount: number | null;
  primaryQuickAction: OperatorWorkspaceQuickAction | null;
  attentionCount: number;
  hasPrimaryQuickAction: boolean;
  hasAttention: boolean;
};

export type MediaProcessIndexSearchOptions = OsdkMediaUploadOptions & {
  process: MediaProcessRequest;
  index: MediaIndexDerivativeRequest;
  search?: MediaSearchRequest;
};

export type AdminLaunchKind = "browser_action" | "operator_command" | "future_surface";
export type AdminLaunchSection = "browser" | "operator" | "future";
export type AdminLaunchpadSectionId =
  | "browser"
  | "operator"
  | "migration"
  | "worker"
  | "bootstrap"
  | "future";
export type AdminLaunchRiskLevel =
  | "browser_safe"
  | "operator_approval"
  | "backend_surface_required"
  | "future_surface";
export type AdminCommandCardSectionId = "operator" | "migration" | "worker" | "bootstrap";
export type AdminCommandCardTone = "normal" | "approval" | "blocked";
export type AdminLaunchItem = {
  id: string;
  title: string;
  area: AdminTaskArea;
  kind: AdminLaunchKind;
  riskLevel: AdminLaunchRiskLevel;
  riskLabel: string;
  executionSurface: AdminTaskView["task"]["executionSurface"];
  canStartFromBrowser: boolean;
  canRenderActionButton: boolean;
  canShowCommand: boolean;
  requiresApproval: boolean;
  requiresBackendSurface: boolean;
  checklist: string[];
  command: string | null;
  sdkSurface: string | null;
  apiRoute: string | null;
  evidencePath: string | null;
  requiredBackendSurface: string | null;
  blockingReason: string | null;
  disabledReason: string | null;
};
export type AdminOperatorCommandCard = {
  id: string;
  title: string;
  area: AdminTaskArea;
  sectionId: AdminCommandCardSectionId;
  description: string;
  command: string;
  copyLabel: string;
  canCopyCommand: boolean;
  tone: AdminCommandCardTone;
  riskLevel: AdminLaunchRiskLevel;
  riskLabel: string;
  executionSurface: AdminTaskView["task"]["executionSurface"];
  requiresApproval: boolean;
  checklist: string[];
  evidencePath: string | null;
  requiredBackendSurface: string | null;
  blockingReason: string | null;
  disabledReason: string | null;
};
export type AdminCommandCenterSectionId = AdminCommandCardSectionId | "all";
export type AdminCommandCenterSelection = {
  sectionId?: AdminCommandCenterSectionId | null;
  query?: string | null;
  selectedCommandId?: string | null;
  includeBlocked?: boolean;
};
export type AdminCommandCenterView = {
  cards: AdminOperatorCommandCard[];
  visibleCards: AdminOperatorCommandCard[];
  selectedCard: AdminOperatorCommandCard | null;
  recommendedCard: AdminOperatorCommandCard | null;
  sectionId: AdminCommandCenterSectionId;
  query: string;
  copyableCards: AdminOperatorCommandCard[];
  blockedCards: AdminOperatorCommandCard[];
  approvalCards: AdminOperatorCommandCard[];
  operatorCards: AdminOperatorCommandCard[];
  migrationCards: AdminOperatorCommandCard[];
  workerCards: AdminOperatorCommandCard[];
  bootstrapCards: AdminOperatorCommandCard[];
  sectionCounts: Record<AdminCommandCardSectionId, number>;
  visibleSectionCounts: Record<AdminCommandCardSectionId, number>;
  hasCards: boolean;
  hasVisibleCards: boolean;
  hasCopyableCommands: boolean;
  hasBlockedCommands: boolean;
  hasApprovalCommands: boolean;
};
export type AdminLaunchpadSection = {
  id: AdminLaunchpadSectionId;
  title: string;
  description: string;
  items: AdminLaunchItem[];
  count: number;
  hasItems: boolean;
  isRecommendedDefault: boolean;
  canStartFromBrowser: boolean;
  requiresApproval: boolean;
  hasRequiredBackendSurfaces: boolean;
};
export type AdminOperationsLaunchModel = {
  board: AdminOperationsBoard;
  browserActions: AdminLaunchItem[];
  operatorCommands: AdminLaunchItem[];
  migrationCommands: AdminLaunchItem[];
  workerCommands: AdminLaunchItem[];
  bootstrapCommands: AdminLaunchItem[];
  operatorCommandCards: AdminOperatorCommandCard[];
  migrationCommandCards: AdminOperatorCommandCard[];
  workerCommandCards: AdminOperatorCommandCard[];
  bootstrapCommandCards: AdminOperatorCommandCard[];
  futureSurfaceActions: AdminLaunchItem[];
  requiredBackendSurfaces: AdminRequiredBackendSurface[];
  recommendedDefaultSection: AdminLaunchSection;
  hasBrowserActions: boolean;
  hasOperatorCommands: boolean;
  hasCopyableCommands: boolean;
  hasFutureSurfaces: boolean;
};
export type AdminOperationsLaunchpadModel = AdminOperationsLaunchModel & {
  browserSection: AdminLaunchpadSection;
  operatorSection: AdminLaunchpadSection;
  migrationSection: AdminLaunchpadSection;
  workerSection: AdminLaunchpadSection;
  bootstrapSection: AdminLaunchpadSection;
  futureSection: AdminLaunchpadSection;
  sections: AdminLaunchpadSection[];
  visibleSections: AdminLaunchpadSection[];
  recommendedSection: AdminLaunchpadSection;
};
export type AdminInternalOperationsWorkbenchSelection = AdminCommandCenterSelection & {
  selectedAreaId?: AdminLaunchpadSectionId | null;
};
export type AdminInternalOperationsReadiness = {
  browserActionCount: number;
  operatorCommandCount: number;
  privilegedCommandCount: number;
  copyableCommandCount: number;
  approvalCommandCount: number;
  blockedCommandCount: number;
  futureSurfaceCount: number;
  requiredBackendSurfaceCount: number;
  canStartFromBrowser: boolean;
  hasOperatorReview: boolean;
  hasPrivilegedBackendGaps: boolean;
  hasCopyableCommands: boolean;
};
export type AdminInternalOperationsWorkbenchView = {
  launchpad: AdminOperationsLaunchpadModel;
  commandCenter: AdminCommandCenterView;
  selectedAreaId: AdminLaunchpadSectionId;
  selectedSection: AdminLaunchpadSection;
  browserActions: AdminLaunchItem[];
  operatorCommandCards: AdminOperatorCommandCard[];
  privilegedCommandCards: AdminOperatorCommandCard[];
  migrationCommandCards: AdminOperatorCommandCard[];
  workerCommandCards: AdminOperatorCommandCard[];
  bootstrapCommandCards: AdminOperatorCommandCard[];
  futureSurfaceActions: AdminLaunchItem[];
  requiredBackendSurfaces: AdminRequiredBackendSurface[];
  visibleCommandCards: AdminOperatorCommandCard[];
  selectedCommandCard: AdminOperatorCommandCard | null;
  recommendedCommandCard: AdminOperatorCommandCard | null;
  copyableCommandCards: AdminOperatorCommandCard[];
  approvalCommandCards: AdminOperatorCommandCard[];
  blockedCommandCards: AdminOperatorCommandCard[];
  readiness: AdminInternalOperationsReadiness;
  hasBrowserActions: boolean;
  hasOperatorCommands: boolean;
  hasPrivilegedCommands: boolean;
  hasFutureSurfaces: boolean;
  hasRequiredBackendSurfaces: boolean;
};

const TERMINAL_WORKFLOW_STATUSES = new Set<ProductWorkflowRun["status"]>([
  "succeeded",
  "failed",
  "cancelled",
  "start_unknown",
]);

export function foundryLiteDatasetResourceId(selection: DatasetExplorerSelection): string {
  const datasetRef = `${selection.namespace}.${selection.name}`;
  return selection.lineageResourceId ?? (selection.version ? `${datasetRef}@${selection.version}` : datasetRef);
}

export function isFoundryLiteProductWorkflowTerminal(run: ProductWorkflowRun): boolean {
  return TERMINAL_WORKFLOW_STATUSES.has(run.status);
}

function resourceBrowserIdSet(values?: Iterable<string>): Set<string> {
  return new Set(values ?? []);
}

function isResourceBrowserFavorite(item: ResourceItem, favoriteIds: Set<string>): boolean {
  return Boolean(item.isFavorite) || favoriteIds.has(item.id) || favoriteIds.has(item.rid);
}

function isResourceBrowserTrashed(item: ResourceItem, trashedIds: Set<string>): boolean {
  return item.status === "trashed" || trashedIds.has(item.id) || trashedIds.has(item.rid);
}

export function buildResourceBrowserView(input: ResourceBrowserViewInput): ResourceBrowserView {
  const favoriteIds = resourceBrowserIdSet(input.favoriteIds);
  const trashedIds = resourceBrowserIdSet(input.trashedIds);
  const items = input.items.map((item) => {
    const isTrashed = isResourceBrowserTrashed(item, trashedIds);
    return {
      ...item,
      isFavorite: isResourceBrowserFavorite(item, favoriteIds),
      status: isTrashed ? "trashed" : item.status,
    };
  });
  return {
    projects: input.projects,
    folders: input.folders ?? [],
    items,
    activeItems: items.filter((item) => item.status !== "trashed"),
    favoriteItems: items.filter((item) => item.isFavorite),
    trashedItems: items.filter((item) => item.status === "trashed"),
    selectedProjectId: input.selectedProjectId ?? null,
    selectedFolderId: input.selectedFolderId ?? null,
    isBackendBacked: input.isBackendBacked ?? false,
  };
}

export function createResourceBrowserRecipe(
  client: Pick<FoundryLiteGeneratedClient, "resources">,
) {
  return {
    buildView: buildResourceBrowserView,
    loadResources: async (
      options: ResourceBrowserLoadOptions = {},
    ): Promise<ResourceBrowserView> => {
      const projects = await client.resources.projects.list();
      const selectedProjectId = options.projectId ?? projects[0]?.id ?? null;
      const [folders, result] = await Promise.all([
        selectedProjectId
          ? client.resources.folders.list(selectedProjectId, {
              includeTrashed: options.includeTrashed,
            })
          : Promise.resolve([]),
        client.resources.items.search({
          projectId: selectedProjectId ?? undefined,
          folderId: options.folderId,
          includeTrashed: options.includeTrashed,
        }),
      ]);
      return buildResourceBrowserView({
        projects,
        folders,
        items: result.items,
        selectedProjectId,
        selectedFolderId: options.folderId ?? null,
        favoriteIds: options.favoriteIds,
        trashedIds: options.trashedIds,
        isBackendBacked: true,
      });
    },
  };
}

export function createDatasetExplorerRecipe(
  client: Pick<FoundryLiteGeneratedClient, "datasets" | "operations">,
) {
  return {
    listDatasets: () => client.datasets.list(),
    listVersions: (selection: DatasetReference) =>
      client.datasets.versions(selection.namespace, selection.name),
    preview: (selection: DatasetExplorerSelection) =>
      client.datasets.preview(selection.namespace, selection.name, {
        limit: selection.previewLimit ?? 100,
      }),
    inspect: (selection: DatasetExplorerSelection) =>
      client.datasets.inspect(
        selection.namespace,
        selection.name,
        selection.version ? { version: selection.version } : undefined,
      ),
    qualityChecks: (selection: DatasetReference) =>
      client.datasets.qualityChecks.list(selection.namespace, selection.name),
    qualitySummary: (selection: DatasetReference) =>
      client.datasets.qualityResults.summary(selection.namespace, selection.name),
    lineage: (selection: DatasetExplorerSelection) =>
      client.operations.lineage.get(foundryLiteDatasetResourceId(selection)),
  };
}

export function createObjectActionWorkspaceRecipe(
  client: FoundryLiteGeneratedClient,
  catalog?: OntologyCatalog | null,
) {
  const osdk = createFoundryLiteOsdkClient(client);
  const ontologyIndex = createFoundryLiteOntologyIndex(catalog ?? null);

  return {
    osdk,
    ontologyIndex,
    findObjectTypes: (query: string) => ontologyIndex.findObjectTypes(query),
    findActionTypes: (query: string) => ontologyIndex.findActionTypes(query),
    actionsForObjectType: (objectApiName: string) =>
      ontologyIndex.actionsForObjectType(objectApiName),
    getGeneratedObjectType: <TName extends ObjectApiName>(objectApiName: TName) =>
      getObjectType(objectApiName),
    getGeneratedActionType: <TName extends ActionApiName>(actionApiName: TName) =>
      getActionType(actionApiName),
    objectSet: <TName extends ObjectApiName>(objectApiName: TName) =>
      osdk(getObjectType(objectApiName)),
    fetchObjectPage: <TName extends ObjectApiName>(
      objectApiName: TName,
      options?: OsdkFetchPageOptions,
    ) => osdk(getObjectType(objectApiName)).fetchPage(options),
    applyAction: <TName extends ActionApiName>(
      actionApiName: TName,
      payload: OsdkActionPayload<ActionTypeRegistry[TName]>,
      options?: { idempotencyKey?: string },
    ): Promise<OsdkActionResult<ActionTypeRegistry[TName]>> =>
      osdk(getActionType(actionApiName)).applyAction(payload, options),
  };
}

export async function loadObjectActionWorkspaceRecipe(
  client: FoundryLiteGeneratedClient,
) {
  return createObjectActionWorkspaceRecipe(client, await client.ontology.catalog());
}

export function createMediaWorkspaceRecipe(client: FoundryLiteGeneratedClient) {
  const osdk = createFoundryLiteOsdkClient(client);

  return {
    createMediaSet: (payload: MediaSetCreateRequest) => osdk.media.createSet(payload),
    fetchMediaSet: (mediaSetId: string) => osdk.media.fetchSet(mediaSetId),
    uploadAndCommit: (
      mediaSetId: string,
      payload: MediaUploadRequest,
      options: OsdkMediaUploadOptions,
    ) => osdk.media.uploadAndCommit(mediaSetId, payload, options),
    process: (mediaItemVersionId: string, payload: MediaProcessRequest) =>
      osdk.media.process(mediaItemVersionId, payload),
    index: (mediaDerivativeId: string, payload: MediaIndexDerivativeRequest) =>
      osdk.media.index(mediaDerivativeId, payload),
    search: (payload: MediaSearchRequest) => osdk.media.search(payload),
    uploadProcessIndexAndSearch: async (
      mediaSetId: string,
      payload: MediaUploadRequest,
      options: MediaProcessIndexSearchOptions,
    ) => {
      const committed = await osdk.media.uploadAndCommit(mediaSetId, payload, options);
      const processing = await osdk.media.process(
        committed.upload.media_item_version_id,
        options.process,
      );
      const indexed = await osdk.media.index(processing.media_derivative_id, options.index);
      const hits = options.search ? await osdk.media.search(options.search) : [];
      return { committed, processing, indexed, hits };
    },
  };
}

export function createAipWorkspaceRecipe(
  client: Pick<FoundryLiteGeneratedClient, "aip" | "operations">,
) {
  return {
    validateBuilder: (payload: AipBuilderValidateRequest) =>
      client.aip.builder.validate(payload),
    runBuilder: (payload: AipBuilderRunRequest) => client.aip.builder.run(payload),
    runAgent: (payload: AipAgentRunRequest) => client.aip.agent.run(payload),
    runAgentAndLoadOperationsDetail: async (payload: AipAgentRunRequest) => {
      const result = await client.aip.agent.run(payload);
      const detail = result.operations
        ? await client.operations.runs.detail(result.operations.runType, result.operations.runId)
        : null;
      return { result, detail };
    },
  };
}

export function insightReviewQueueView(
  result: InsightReviewQueryResult | null | undefined,
  selection: InsightReviewQueueSelection = {},
): InsightReviewQueueView {
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
    hasPendingReviews: pendingReviews.length > 0,
    hasAssignedReviews: assignedReviews.length > 0,
    hasCurrentUserAssignedReviews: currentUserAssignedReviews.length > 0,
    hasDecidedReviews: decidedReviews.length > 0,
    hasHighPriorityReviews: reviews.some((review) => review.priority === "high"),
    canReviewSelectedActionProposal: Boolean(selectedActionProposal?.actionApiName),
  };
}

export function createInsightReviewWorkspaceRecipe(
  client: Pick<FoundryLiteGeneratedClient, "insights">,
) {
  return {
    listReviews: (filters: InsightReviewListFilters = {}) => client.insights.reviews.list(filters),
    loadQueue: async (
      filters: InsightReviewListFilters = {},
      selection: InsightReviewQueueSelection = {},
    ) => insightReviewQueueView(await client.insights.reviews.list(filters), selection),
    getReview: (reviewId: string) => client.insights.reviews.get(reviewId),
    createReview: (
      payload: InsightReviewCreateRequest,
      options: { idempotencyKey: string },
    ) => client.insights.reviews.create(payload, options),
    assignReview: (
      reviewId: string,
      payload: InsightReviewAssignRequest,
      options: { idempotencyKey: string },
    ) => client.insights.reviews.assign(reviewId, payload, options),
    decideReview: (
      reviewId: string,
      payload: InsightReviewDecisionRequest,
      options: { idempotencyKey: string },
    ) => client.insights.reviews.decide(reviewId, payload, options),
    approveReview: (reviewId: string, comment: string | null, options: { idempotencyKey: string }) =>
      client.insights.reviews.decide(reviewId, { decision: "approved", comment }, options),
    rejectReview: (reviewId: string, comment: string | null, options: { idempotencyKey: string }) =>
      client.insights.reviews.decide(reviewId, { decision: "rejected", comment }, options),
  };
}

export function createPipelineBuilderRecipe(
  client: Pick<FoundryLiteGeneratedClient, "pipelines" | "transforms" | "operations">,
) {
  return {
    createBranch: (payload: PipelineBranchCreateRequest, options: { idempotencyKey: string }) =>
      client.pipelines.branches.create(payload, options),
    listBranches: (filters: { status?: string; limit?: number } = {}) =>
      client.pipelines.branches.list(filters),
    getBranch: (branchId: string) => client.pipelines.branches.get(branchId),
    updateGraph: (branchId: string, payload: PipelineGraphUpdateRequest) =>
      client.pipelines.branches.updateGraph(branchId, payload),
    diff: (branchId: string) => client.pipelines.branches.diff(branchId),
    rebase: (branchId: string, payload: PipelineBranchRebaseRequest) =>
      client.pipelines.branches.rebase(branchId, payload),
    propose: (
      branchId: string,
      payload: PipelineBranchProposeRequest,
      options: { idempotencyKey: string },
    ) => client.pipelines.branches.propose(branchId, payload, options),
    abandon: (branchId: string) => client.pipelines.branches.abandon(branchId),
    validate: (branchId: string) => client.pipelines.graph.validate(branchId),
    suggestCasts: (branchId: string, nodeId: string) =>
      client.pipelines.graph.suggestCasts(branchId, nodeId),
    previewNode: (branchId: string, nodeId: string, options: PipelinePreviewNodeRequest = {}) =>
      client.pipelines.graph.previewNode(branchId, nodeId, options),
    stats: (branchId: string, nodeId: string, options: PipelinePreviewNodeRequest = {}) =>
      client.pipelines.graph.stats(branchId, nodeId, options),
    runTests: (branchId: string) => client.pipelines.graph.runTests(branchId),
    listProposals: (filters: { status?: string; limit?: number } = {}) =>
      client.pipelines.proposals.list(filters),
    getProposal: (proposalId: string) => client.pipelines.proposals.get(proposalId),
    assignProposal: (proposalId: string, payload: PipelineProposalAssignRequest) =>
      client.pipelines.proposals.assign(proposalId, payload),
    decideProposal: (proposalId: string, payload: PipelineProposalDecisionRequest) =>
      client.pipelines.proposals.decide(proposalId, payload),
    executeProposal: (proposalId: string) => client.pipelines.proposals.execute(proposalId),
    withdrawProposal: (proposalId: string) => client.pipelines.proposals.withdraw(proposalId),
    listVersions: (pipelineId: string, filters: { limit?: number } = {}) =>
      client.pipelines.versions.list(pipelineId, filters),
    getVersion: (versionId: string) => client.pipelines.versions.get(versionId),
    deploy: (pipelineId: string, versionId: string, payload: PipelineDeployRequest = {}) =>
      client.pipelines.deploy(pipelineId, versionId, payload),
    startRun: (pipelineId: string, payload: PipelineRunStartRequest = {}) =>
      client.pipelines.runs.start(pipelineId, payload),
    getRun: (runId: string) => client.pipelines.runs.get(runId),
    timeline: (runId: string) => client.pipelines.runs.timeline(runId),
    cancelRun: (runId: string) => client.pipelines.runs.cancel(runId),
    upsertSchedule: (pipelineId: string, payload: PipelineScheduleUpsertRequest) =>
      client.pipelines.schedules.upsert(pipelineId, payload),
    getSchedule: (pipelineId: string) => client.pipelines.schedules.get(pipelineId),
    deleteSchedule: (pipelineId: string) => client.pipelines.schedules.delete(pipelineId),
    previewDue: (options: { maxRuns?: number } = {}) => client.pipelines.schedules.previewDue(options),
    tick: (options: { maxRuns?: number } = {}) => client.pipelines.schedules.tick(options),
    registerSql: (payload: TransformSqlRegisterRequest) => client.transforms.registerSql(payload),
    runTransform: (apiName: string) => client.transforms.run(apiName),
    registerAndRunSql: async (payload: TransformSqlRegisterRequest) => {
      const transform = await client.transforms.registerSql(payload);
      const run = await client.transforms.run(payload.apiName);
      return { transform, run };
    },
    retryTransformRun: (runId: string) => client.operations.transforms.retry(runId),
  };
}

export function connectorOnboardingInitialState(): ConnectorOnboardingRecipeState {
  return {
    phase: "idle",
    requestId: null,
    connection: null,
    resource: null,
    testResult: null,
    workflowRun: null,
    operationsPath: null,
    error: null,
    retryable: false,
  };
}

export function connectorOnboardingOperationsPath(
  workflowRun: ProductWorkflowRun | null | undefined,
): string | null {
  if (!workflowRun) return null;
  return workflowRun.operationPath ?? `operations/workflows/${encodeURIComponent(workflowRun.workflowRunId)}`;
}

function connectorOnboardingState(
  current: ConnectorOnboardingRecipeState,
  patch: Partial<ConnectorOnboardingRecipeState>,
): ConnectorOnboardingRecipeState {
  const nextError = patch.error === undefined ? current.error : patch.error;
  return {
    ...current,
    ...patch,
    requestId: patch.requestId ?? nextError?.requestId ?? current.requestId,
    retryable: patch.retryable ?? nextError?.retryable ?? false,
  };
}

function connectorOnboardingFailureState(
  current: ConnectorOnboardingRecipeState,
  error: unknown,
): ConnectorOnboardingRecipeState {
  const normalized = normalizeFoundryLiteError(error);
  return connectorOnboardingState(current, {
    phase: "failed",
    requestId: normalized.requestId,
    error: normalized,
    retryable: normalized.retryable,
  });
}

export function createConnectorOnboardingRecipe(
  client: Pick<FoundryLiteGeneratedClient, "connectors" | "operations">,
) {
  const pollWorkflowRun = (
    workflowRunId: string,
    options: PollFoundryLiteOperationOptions<ProductWorkflowRun> = {},
  ) =>
    pollFoundryLiteOperation(
      () => client.operations.workflows.get(workflowRunId),
      { isTerminal: isFoundryLiteProductWorkflowTerminal, ...options },
    );

  const emit = (
    state: ConnectorOnboardingRecipeState,
    onState: ConnectorOnboardingRunOptions["onState"],
  ) => {
    onState?.(state);
    return state;
  };

  return {
    initialState: connectorOnboardingInitialState,
    operationsPath: connectorOnboardingOperationsPath,
    createConnection: (
      payload: RestConnectorConnectionCreateRequest,
      options: { idempotencyKey: string },
    ) => client.connectors.connections.create(payload, options),
    upsertResource: (
      connectorName: string,
      resourceName: string,
      payload: RestConnectorResourceUpsertRequest,
      options: { idempotencyKey: string },
    ) => client.connectors.resources.upsert(connectorName, resourceName, payload, options),
    testResource: (connectorName: string, resourceName: string) =>
      client.connectors.resources.test(connectorName, resourceName),
    startFirstSync: (
      connectorName: string,
      resourceName: string,
      payload: ConnectorResourceSyncStartRequest | undefined,
      options: { idempotencyKey: string },
    ) => client.connectors.resources.startSync(connectorName, resourceName, payload, options),
    pollWorkflowRun,
    runFirstSync: async (
      input: ConnectorOnboardingInput,
      options: ConnectorOnboardingRunOptions = {},
    ): Promise<ConnectorOnboardingRecipeState> => {
      let state = emit(connectorOnboardingInitialState(), options.onState);
      const connectorName = input.connection.connectorName;
      try {
        state = emit(connectorOnboardingState(state, { phase: "creating_connection" }), options.onState);
        const connection = await client.connectors.connections.create(input.connection, {
          idempotencyKey: input.idempotencyKeys.createConnection,
        });
        state = emit(connectorOnboardingState(state, { connection }), options.onState);

        state = emit(connectorOnboardingState(state, { phase: "upserting_resource" }), options.onState);
        const resource = await client.connectors.resources.upsert(
          connectorName,
          input.resourceName,
          input.resource,
          { idempotencyKey: input.idempotencyKeys.upsertResource },
        );
        state = emit(connectorOnboardingState(state, { resource }), options.onState);

        state = emit(connectorOnboardingState(state, { phase: "testing_resource" }), options.onState);
        const testResult = await client.connectors.resources.test(connectorName, input.resourceName);
        state = emit(connectorOnboardingState(state, { testResult }), options.onState);

        state = emit(connectorOnboardingState(state, { phase: "starting_sync" }), options.onState);
        const startedRun = await client.connectors.resources.startSync(
          connectorName,
          input.resourceName,
          input.sync,
          { idempotencyKey: input.idempotencyKeys.startFirstSync },
        );
        state = emit(
          connectorOnboardingState(state, {
            workflowRun: startedRun,
            operationsPath: connectorOnboardingOperationsPath(startedRun),
          }),
          options.onState,
        );

        if (options.shouldPollWorkflow === false) {
          return emit(connectorOnboardingState(state, { phase: "ready" }), options.onState);
        }

        state = emit(connectorOnboardingState(state, { phase: "polling_workflow" }), options.onState);
        const { onState, shouldPollWorkflow, ...pollOptions } = options;
        void onState;
        void shouldPollWorkflow;
        const workflowRun = await pollWorkflowRun(startedRun.workflowRunId, pollOptions);
        return emit(
          connectorOnboardingState(state, {
            phase: "ready",
            workflowRun,
            operationsPath: connectorOnboardingOperationsPath(workflowRun),
          }),
          options.onState,
        );
      } catch (error) {
        return emit(connectorOnboardingFailureState(state, error), options.onState);
      }
    },
  };
}

export function sourceOnboardingInitialState(): SourceOnboardingRecipeState {
  return {
    phase: "select_kind",
    requestId: null,
    source: null,
    datasetRef: null,
    mediaSetId: null,
    workflowRun: null,
    commitResult: null,
    commitResults: [],
    mediaCommitResult: null,
    testResult: null,
    operationsPath: null,
    error: null,
    retryable: false,
  };
}

function sourceOnboardingState(
  current: SourceOnboardingRecipeState,
  patch: Partial<SourceOnboardingRecipeState>,
): SourceOnboardingRecipeState {
  const nextError = patch.error === undefined ? current.error : patch.error;
  return {
    ...current,
    ...patch,
    requestId: patch.requestId ?? nextError?.requestId ?? current.requestId,
    retryable: patch.retryable ?? nextError?.retryable ?? false,
  };
}

function sourceOnboardingFailureState(
  current: SourceOnboardingRecipeState,
  error: unknown,
): SourceOnboardingRecipeState {
  const normalized = normalizeFoundryLiteError(error);
  return sourceOnboardingState(current, {
    phase: "failed",
    requestId: normalized.requestId,
    error: normalized,
    retryable: normalized.retryable,
  });
}

function sourceStateFromResult(
  current: SourceOnboardingRecipeState,
  result: SourceOperationResult,
): SourceOnboardingRecipeState {
  return sourceOnboardingState(current, {
    source: result.source,
    datasetRef: result.source.targetDatasetRef,
    mediaSetId: result.source.targetMediaSetId,
    workflowRun: (result.workflowRun as ProductWorkflowRun | null) ?? null,
    commitResult: result.commitResult,
    commitResults: result.commitResults,
    mediaCommitResult: result.mediaCommitResult,
    testResult: result.testResult,
    operationsPath: result.operationsPath ?? result.source.operationsPath,
  });
}

export function sourceWizardInitialState(): SourceWizardRecipeState {
  return {
    phase: "select_template",
    requestId: null,
    sourceType: null,
    templates: [],
    selectedTemplate: null,
    credential: null,
    agent: null,
    networkPolicy: null,
    exploration: null,
    sync: null,
    syncRun: null,
    datasetRef: null,
    mediaSetId: null,
    operationsPath: null,
    error: null,
    retryable: false,
  };
}

function sourceWizardState(
  current: SourceWizardRecipeState,
  patch: Partial<SourceWizardRecipeState>,
): SourceWizardRecipeState {
  const nextError = patch.error === undefined ? current.error : patch.error;
  return {
    ...current,
    ...patch,
    requestId: patch.requestId ?? nextError?.requestId ?? current.requestId,
    retryable: patch.retryable ?? nextError?.retryable ?? false,
  };
}

function sourceWizardFailureState(
  current: SourceWizardRecipeState,
  error: unknown,
): SourceWizardRecipeState {
  const normalized = normalizeFoundryLiteError(error);
  return sourceWizardState(current, {
    phase: "failed",
    requestId: normalized.requestId,
    error: normalized,
    retryable: normalized.retryable,
  });
}

function sourceWizardOperationsPath(run: SourceManagedSyncRun | null, sync: SourceManagedSync | null): string | null {
  return run?.operationsPath ?? (sync?.lastRunId ? `/operations/source-sync-runs/${sync.lastRunId}` : null);
}

export function createSourceWizardRecipe(client: Pick<FoundryLiteGeneratedClient, "sources">) {
  const emit = (state: SourceWizardRecipeState, onState: SourceWizardRunOptions["onState"]) => {
    onState?.(state);
    return state;
  };

  const chooseTemplate = (templates: SourceTemplate[], sourceType: string) =>
    templates.find((template) => template.sourceType === sourceType) ?? null;

  return {
    initialState: sourceWizardInitialState,
    listTemplates: () => client.sources.templates.list(),
    createCredential: (payload: SourceCredentialCreateRequest, options: { idempotencyKey: string }) =>
      client.sources.credentials.create(payload, options),
    registerAgent: (payload: SourceAgentRegisterRequest, options: { idempotencyKey: string }) =>
      client.sources.agents.register(payload, options),
    createNetworkPolicy: (payload: SourceNetworkPolicyCreateRequest, options: { idempotencyKey: string }) =>
      client.sources.networkPolicies.create(payload, options),
    exploreSource: (payload: SourceExploreRequest) => client.sources.exploration.run(payload),
    createManagedSync: (payload: SourceManagedSyncCreateRequest, options: { idempotencyKey: string }) =>
      client.sources.managedSyncs.create(payload, options),
    startManagedSyncRun: (
      syncName: string,
      payload: SourceManagedSyncRunStartRequest | undefined,
      options: { idempotencyKey: string },
    ) => client.sources.managedSyncs.startRun(syncName, payload, options),
    listManagedSyncRuns: (syncName: string) => client.sources.managedSyncs.listRuns(syncName),
    getManagedSyncRun: (runId: string) => client.sources.managedSyncs.getRun(runId),
    run: async (
      input: SourceWizardInput,
      options: SourceWizardRunOptions = {},
    ): Promise<SourceWizardRecipeState> => {
      let state = emit(sourceWizardInitialState(), options.onState);
      try {
        const templates = await client.sources.templates.list();
        state = emit(
          sourceWizardState(state, {
            templates,
            sourceType: input.sourceType,
            selectedTemplate: chooseTemplate(templates, input.sourceType),
          }),
          options.onState,
        );

        if (input.credential) {
          state = emit(sourceWizardState(state, { phase: "store_credentials" }), options.onState);
          const credential = await client.sources.credentials.create(input.credential.payload, {
            idempotencyKey: input.credential.idempotencyKey,
          });
          state = emit(sourceWizardState(state, { credential }), options.onState);
        }

        if (input.agent || input.networkPolicy) {
          state = emit(sourceWizardState(state, { phase: "prepare_network" }), options.onState);
          if (input.agent) {
            const agent = await client.sources.agents.register(input.agent.payload, {
              idempotencyKey: input.agent.idempotencyKey,
            });
            state = emit(sourceWizardState(state, { agent }), options.onState);
          }
          if (input.networkPolicy) {
            const networkPolicy = await client.sources.networkPolicies.create(input.networkPolicy.payload, {
              idempotencyKey: input.networkPolicy.idempotencyKey,
            });
            state = emit(sourceWizardState(state, { networkPolicy }), options.onState);
          }
        }

        if (input.exploration) {
          state = emit(sourceWizardState(state, { phase: "explore_source" }), options.onState);
          const exploration = await client.sources.exploration.run(input.exploration);
          state = emit(
            sourceWizardState(state, {
              exploration,
              operationsPath: exploration.operationsPath,
            }),
            options.onState,
          );
        }

        state = emit(sourceWizardState(state, { phase: "create_sync" }), options.onState);
        const sync = await client.sources.managedSyncs.create(input.sync.payload, {
          idempotencyKey: input.sync.idempotencyKey,
        });
        state = emit(
          sourceWizardState(state, {
            sync,
            datasetRef: sync.targetDatasetRef,
            mediaSetId: sync.targetMediaSetId,
          }),
          options.onState,
        );

        if (input.startRun) {
          state = emit(sourceWizardState(state, { phase: "start_sync_run" }), options.onState);
          const syncRun = await client.sources.managedSyncs.startRun(
            sync.syncName,
            input.startRun.payload,
            { idempotencyKey: input.startRun.idempotencyKey },
          );
          state = emit(
            sourceWizardState(state, {
              syncRun,
              operationsPath: sourceWizardOperationsPath(syncRun, sync),
            }),
            options.onState,
          );
        }

        state = emit(sourceWizardState(state, { phase: "inspect_run" }), options.onState);
        state = emit(sourceWizardState(state, { phase: "operations_evidence" }), options.onState);
        return emit(sourceWizardState(state, { phase: "ready_for_ontology" }), options.onState);
      } catch (error) {
        return emit(sourceWizardFailureState(state, error), options.onState);
      }
    },
  };
}

export function createSourceUploadRecipe(client: Pick<FoundryLiteGeneratedClient, "sources">) {
  return {
    uploadCsv: (payload: SourceCsvUploadRequest, options: { idempotencyKey: string }) =>
      client.sources.csv.upload(payload, options),
    uploadBatchFiles: (payload: SourceBatchFileUploadRequest, options: { idempotencyKey: string }) =>
      client.sources.batchFiles.upload(payload, options),
    uploadMediaAndCommit: (payload: SourceMediaUploadRequest, options: { idempotencyKey: string }) =>
      client.sources.media.uploadAndCommit(payload, options),
  };
}

export function createSourceSyncRecipe(client: Pick<FoundryLiteGeneratedClient, "sources">) {
  return {
    createDebezium: (payload: SourceDebeziumCreateRequest, options: { idempotencyKey: string }) =>
      client.sources.cdc.debezium.create(payload, options),
    startDebeziumSync: (
      sourceName: string,
      payload: SourceDebeziumSyncStartRequest | undefined,
      options: { idempotencyKey: string },
    ) => client.sources.cdc.debezium.startSync(sourceName, payload, options),
    createWebhookListener: (payload: SourceWebhookListenerCreateRequest, options: { idempotencyKey: string }) =>
      client.sources.webhookListeners.create(payload, options),
  };
}

export function createSourceOnboardingRecipe(client: Pick<FoundryLiteGeneratedClient, "sources" | "operations">) {
  const emit = (
    state: SourceOnboardingRecipeState,
    onState: SourceOnboardingRunOptions["onState"],
  ) => {
    onState?.(state);
    return state;
  };

  const applyResult = (
    state: SourceOnboardingRecipeState,
    result: SourceOperationResult,
    options: SourceOnboardingRunOptions,
  ) => emit(sourceStateFromResult(state, result), options.onState);

  return {
    initialState: sourceOnboardingInitialState,
    uploads: createSourceUploadRecipe(client),
    syncs: createSourceSyncRecipe(client),
    listSources: () => client.sources.list(),
    getSource: (sourceName: string) => client.sources.get(sourceName),
    run: async (
      input: SourceOnboardingInput,
      options: SourceOnboardingRunOptions = {},
    ): Promise<SourceOnboardingRecipeState> => {
      let state = emit(sourceOnboardingInitialState(), options.onState);
      try {
        state = emit(sourceOnboardingState(state, { phase: "configure_source" }), options.onState);
        if (input.kind === "csv_upload") {
          state = emit(sourceOnboardingState(state, { phase: "upload_or_start_sync" }), options.onState);
          const result = await client.sources.csv.upload(input.payload, { idempotencyKey: input.idempotencyKey });
          state = applyResult(state, result, options);
        } else if (input.kind === "batch_file") {
          state = emit(sourceOnboardingState(state, { phase: "upload_or_start_sync" }), options.onState);
          const result = await client.sources.batchFiles.upload(input.payload, { idempotencyKey: input.idempotencyKey });
          state = applyResult(state, result, options);
        } else if (input.kind === "webhook_listener") {
          const result = await client.sources.webhookListeners.create(input.payload, {
            idempotencyKey: input.idempotencyKey,
          });
          state = applyResult(state, result, options);
        } else if (input.kind === "media_upload") {
          state = emit(sourceOnboardingState(state, { phase: "upload_or_start_sync" }), options.onState);
          const result = await client.sources.media.uploadAndCommit(input.payload, {
            idempotencyKey: input.idempotencyKey,
          });
          state = applyResult(state, result, options);
        } else {
          state = await runDebeziumSourceOnboarding(client, state, input, options, emit);
        }
        state = emit(sourceOnboardingState(state, { phase: "operations_evidence" }), options.onState);
        return emit(sourceOnboardingState(state, { phase: "ready_for_ontology" }), options.onState);
      } catch (error) {
        return emit(sourceOnboardingFailureState(state, error), options.onState);
      }
    },
  };
}

async function runDebeziumSourceOnboarding(
  client: Pick<FoundryLiteGeneratedClient, "sources">,
  state: SourceOnboardingRecipeState,
  input: Extract<SourceOnboardingInput, { kind: "debezium_cdc" }>,
  options: SourceOnboardingRunOptions,
  emit: (
    state: SourceOnboardingRecipeState,
    onState: SourceOnboardingRunOptions["onState"],
  ) => SourceOnboardingRecipeState,
): Promise<SourceOnboardingRecipeState> {
  const created = await client.sources.cdc.debezium.create(input.payload, {
    idempotencyKey: input.createIdempotencyKey,
  });
  let next = emit(sourceStateFromResult(state, created), options.onState);
  if (!input.startSync || !input.startSyncIdempotencyKey) return next;
  next = emit(sourceOnboardingState(next, { phase: "upload_or_start_sync" }), options.onState);
  const started = await client.sources.cdc.debezium.startSync(
    input.payload.sourceName,
    input.startSync,
    { idempotencyKey: input.startSyncIdempotencyKey },
  );
  return emit(sourceStateFromResult(next, started), options.onState);
}

/** Initial (pre-run) state for the ontology builder flow. */
export function ontologyBuilderInitialState(): OntologyBuilderRecipeState {
  return {
    phase: "explore_dataset",
    requestId: null,
    datasetRef: null,
    inspection: null,
    validation: null,
    migrationPlan: null,
    isBlocked: false,
    branch: null,
    branchDiff: null,
    proposal: null,
    reindexResults: [],
    driftReport: null,
    operationsPath: null,
    error: null,
    retryable: false,
  };
}

/**
 * Ordered screen steps for the ontology builder flow:
 * Dataset Explorer -> Builder(draft) -> Validate -> Impact/Reindex plan ->
 * Proposal -> Review -> Execute -> Reindex status -> SDK drift check.
 */
export function ontologyBuilderSteps(): OntologyBuilderStep[] {
  return [
    ontologyBuilderStep(
      "dataset_explorer",
      "Dataset Explorer",
      "Inspect dataset schema and pick backing columns for the draft.",
      "/ontology/builder/datasets",
      "useFoundryLiteDatasetColumnMapping",
      "client.datasets.inspect",
    ),
    ontologyBuilderStep(
      "draft_builder",
      "Builder",
      "Click-driven object/property/link/action design over an editable draft.",
      "/ontology/builder/draft",
      "useFoundryLiteOntologyDraft",
      "ontologyDraftToYamlText",
    ),
    ontologyBuilderStep(
      "validate",
      "Validate",
      "Validate the serialized draft against the active ontology.",
      "/ontology/builder/validate",
      "useFoundryLiteOntologyDraftValidation",
      "client.ontology.validate",
    ),
    ontologyBuilderStep(
      "impact_plan",
      "Impact & Reindex plan",
      "Review blocked changes, warnings, reindex operations, and SDK impact.",
      "/ontology/builder/impact",
      "useFoundryLiteOntologyResourceInsights",
      "client.ontology.resources",
    ),
    ontologyBuilderStep(
      "proposal",
      "Proposal",
      "Submit the draft as a change proposal or revise an open one.",
      "/ontology/builder/proposal",
      "useFoundryLiteOntologyProposalSubmit",
      "client.ontology.proposals.submit",
    ),
    ontologyBuilderStep(
      "review",
      "Review",
      "Assign a reviewer and record an approve/reject decision.",
      "/ontology/builder/review",
      "useFoundryLiteOntologyProposalReview",
      "client.ontology.proposals.decide",
    ),
    ontologyBuilderStep(
      "execute",
      "Execute",
      "Apply the approved proposal as the next active ontology version.",
      "/ontology/builder/execute",
      "useFoundryLiteOntologyProposalReview",
      "client.ontology.proposals.execute",
    ),
    ontologyBuilderStep(
      "reindex_status",
      "Reindex status",
      "Replay required object reindex operations and track their evidence.",
      "/ontology/builder/reindex",
      "useFoundryLiteOntologyProposal",
      "client.operations.index.replayOntologyReindex",
    ),
    ontologyBuilderStep(
      "sdk_drift",
      "SDK drift",
      "Compare the generated OSDK package against the newly active catalog.",
      "/ontology/builder/sdk-drift",
      "useFoundryLiteOntologyCatalog",
      "sdkOntologyDriftReport",
    ),
  ];
}

/** Navigation view over the builder steps with the selected step marked. */
export function ontologyBuilderNavigation(
  selectedStepId: OntologyBuilderStepId = "dataset_explorer",
): OntologyBuilderNavigationView {
  const steps = ontologyBuilderSteps().map((step, index) => ({
    ...step,
    stepNumber: index + 1,
    isSelected: step.id === selectedStepId,
  }));
  const selectedStep = steps.find((step) => step.isSelected) ?? steps[0];
  return {
    selectedStepId: selectedStep.id,
    steps,
    selectedStep,
    stepCount: steps.length,
  };
}

function ontologyBuilderStep(
  id: OntologyBuilderStepId,
  title: string,
  description: string,
  route: string,
  reactHook: string,
  primarySdkSurface: string,
): OntologyBuilderStep {
  return { id, title, description, route, reactHook, primarySdkSurface };
}

function ontologyBuilderState(
  current: OntologyBuilderRecipeState,
  patch: Partial<OntologyBuilderRecipeState>,
): OntologyBuilderRecipeState {
  const nextError = patch.error === undefined ? current.error : patch.error;
  return {
    ...current,
    ...patch,
    requestId: patch.requestId ?? nextError?.requestId ?? current.requestId,
    retryable: patch.retryable ?? nextError?.retryable ?? false,
  };
}

function ontologyBuilderFailureState(
  current: OntologyBuilderRecipeState,
  error: unknown,
): OntologyBuilderRecipeState {
  const normalized = normalizeFoundryLiteError(error);
  return ontologyBuilderState(current, {
    phase: "failed",
    requestId: normalized.requestId,
    error: normalized,
    retryable: normalized.retryable,
  });
}

function ontologyBuilderMigrationPlan(
  validation: OntologyValidationResult,
): Record<string, unknown> | null {
  const plan = validation.migration_plan;
  return typeof plan === "object" && plan !== null ? plan : null;
}

function ontologyBuilderPlanIsBlocked(migrationPlan: Record<string, unknown> | null): boolean {
  return migrationPlan?.status === "blocked";
}

function ontologyBuilderReindexOperations(
  proposal: OntologyProposalPayload,
): Array<{ objectTypeApiName: string; reindexKey: string }> {
  const plan = proposal.submittedMigrationPlan;
  const rawOperations = plan?.objectReindexPlan;
  if (!Array.isArray(rawOperations)) return [];
  const operations: Array<{ objectTypeApiName: string; reindexKey: string }> = [];
  for (const rawOperation of rawOperations) {
    if (typeof rawOperation !== "object" || rawOperation === null) continue;
    const { objectTypeApiName, reindexKey } = rawOperation as {
      objectTypeApiName?: unknown;
      reindexKey?: unknown;
    };
    if (typeof objectTypeApiName === "string" && typeof reindexKey === "string") {
      operations.push({ objectTypeApiName, reindexKey });
    }
  }
  return operations;
}

/**
 * SDK surface for the full Ontology Builder flow: Dataset Explorer ->
 * Builder(draft) -> Validate -> Impact/Reindex plan -> Proposal(submit/update)
 * -> Review(assign/decide) -> Execute -> Reindex status, plus an SDK drift
 * check step. `steps`/`navigation` feed screen chrome; the named methods back
 * each panel; `run` drives the whole governed flow with `onState` snapshots.
 * Passing the optional `input.branch` flag routes the draft through an
 * isolated ontology branch instead of a direct proposal submit: create branch
 * -> write the draft onto the branch (fingerprint-CAS) -> diff -> rebase when
 * the base is stale (per-resource `use: main|branch` resolutions) -> propose
 * the branch, then the existing review/execute/reindex/drift path continues
 * unchanged.
 */
export function createOntologyBuilderRecipe(
  client: Pick<FoundryLiteGeneratedClient, "datasets" | "ontology" | "operations">,
) {
  const emit = (
    state: OntologyBuilderRecipeState,
    onState: OntologyBuilderRunOptions["onState"],
  ) => {
    onState?.(state);
    return state;
  };

  const loadCatalog = () => client.ontology.catalog();
  const validateDraft = (yamlText: string) => client.ontology.validate({ yaml: yamlText });
  const replayObjectReindex = (objectTypeApiName: string, reindexKey: string) =>
    client.operations.index.replayOntologyReindex(objectTypeApiName, { reindexKey });
  const loadSdkDriftReport = async (): Promise<FoundryLiteSdkDriftReport> =>
    sdkOntologyDriftReport(await loadCatalog());

  return {
    initialState: ontologyBuilderInitialState,
    steps: ontologyBuilderSteps(),
    navigation: ontologyBuilderNavigation,
    inspectDataset: (selection: DatasetExplorerSelection) =>
      client.datasets.inspect(
        selection.namespace,
        selection.name,
        selection.version ? { version: selection.version } : undefined,
      ),
    loadCatalog,
    validateDraft,
    loadResourceUsage: (
      resourceType: string,
      apiName: string,
      options?: OntologyResourceUsageOptions,
    ) => client.ontology.resources.usage(resourceType, apiName, options),
    loadResourceDependents: (resourceType: string, apiName: string) =>
      client.ontology.resources.dependents(resourceType, apiName),
    submitProposal: (payload: OntologyProposalSubmitRequest, options: { idempotencyKey: string }) =>
      client.ontology.proposals.submit(payload, options),
    updateProposal: (proposalId: string, payload: OntologyProposalUpdateRequest) =>
      client.ontology.proposals.update(proposalId, payload),
    listProposals: (filters?: OntologyProposalListFilters) =>
      client.ontology.proposals.list(filters),
    getProposal: (proposalId: string) => client.ontology.proposals.get(proposalId),
    assignProposal: (proposalId: string, payload: OntologyProposalAssignRequest) =>
      client.ontology.proposals.assign(proposalId, payload),
    decideProposal: (proposalId: string, payload: OntologyProposalDecisionRequest) =>
      client.ontology.proposals.decide(proposalId, payload),
    executeProposal: (proposalId: string, payload: OntologyProposalExecuteRequest) =>
      client.ontology.proposals.execute(proposalId, payload),
    withdrawProposal: (proposalId: string, payload?: OntologyProposalWithdrawRequest) =>
      client.ontology.proposals.withdraw(proposalId, payload),
    createBranch: (payload: OntologyBranchCreateRequest, options: { idempotencyKey: string }) =>
      client.ontology.branches.create(payload, options),
    listBranches: (filters?: OntologyBranchListFilters): Promise<OntologyBranchListResult> =>
      client.ontology.branches.list(filters),
    getBranch: (branchId: string): Promise<OntologyBranchDetailPayload> =>
      client.ontology.branches.get(branchId),
    updateBranch: (branchId: string, payload: OntologyBranchUpdateRequest) =>
      client.ontology.branches.update(branchId, payload),
    diffBranch: (branchId: string) => client.ontology.branches.diff(branchId),
    rebaseBranch: (branchId: string, payload: OntologyBranchRebaseRequest) =>
      client.ontology.branches.rebase(branchId, payload),
    proposeBranch: (
      branchId: string,
      payload: OntologyBranchProposeRequest,
      options: { idempotencyKey: string },
    ) => client.ontology.branches.propose(branchId, payload, options),
    abandonBranch: (branchId: string) => client.ontology.branches.abandon(branchId),
    replayObjectReindex,
    loadSdkDriftReport,
    assertSdkFresh: async (): Promise<OntologyCatalog> => {
      const catalog = await loadCatalog();
      assertFoundryLiteSdkFresh(catalog);
      return catalog;
    },
    run: async (
      input: OntologyBuilderRunInput,
      options: OntologyBuilderRunOptions = {},
    ): Promise<OntologyBuilderRecipeState> => {
      let state = emit(ontologyBuilderInitialState(), options.onState);
      try {
        if (input.dataset) {
          const inspection = await client.datasets.inspect(
            input.dataset.namespace,
            input.dataset.name,
            input.dataset.version ? { version: input.dataset.version } : undefined,
          );
          state = emit(
            ontologyBuilderState(state, {
              phase: "design_draft",
              datasetRef: inspection.dataset,
              inspection,
            }),
            options.onState,
          );
        }
        state = emit(ontologyBuilderState(state, { phase: "validate_draft" }), options.onState);
        const validation = await validateDraft(input.yamlText);
        const migrationPlan = ontologyBuilderMigrationPlan(validation);
        const isBlocked = ontologyBuilderPlanIsBlocked(migrationPlan);
        state = emit(
          ontologyBuilderState(state, {
            phase: "impact_review",
            validation,
            migrationPlan,
            isBlocked,
          }),
          options.onState,
        );
        if (isBlocked && !input.submitWhenBlocked) {
          return emit(ontologyBuilderState(state, { phase: "blocked" }), options.onState);
        }
        state = emit(ontologyBuilderState(state, { phase: "submit_proposal" }), options.onState);
        let proposal: OntologyProposalPayload;
        if (input.branch) {
          // Optional branch path: draft-on-branch -> diff -> rebase-if-stale
          // -> propose through the same proposal governance flow.
          let branch = await client.ontology.branches.create(
            { name: input.branch.name },
            { idempotencyKey: input.branch.createIdempotencyKey },
          );
          state = emit(ontologyBuilderState(state, { branch }), options.onState);
          branch = await client.ontology.branches.update(branch.id, {
            yamlText: input.yamlText,
            expectedFingerprint: branch.contentFingerprint,
          });
          state = emit(ontologyBuilderState(state, { branch }), options.onState);
          const branchDiff = await client.ontology.branches.diff(branch.id);
          state = emit(ontologyBuilderState(state, { branchDiff }), options.onState);
          if (branchDiff.baseStale) {
            branch = await client.ontology.branches.rebase(branch.id, {
              expectedFingerprint: branch.contentFingerprint,
              resolutions: input.branch.rebaseResolutions ?? [],
            });
            state = emit(ontologyBuilderState(state, { branch }), options.onState);
          }
          branch = await client.ontology.branches.propose(
            branch.id,
            {
              title: input.proposal.title,
              description: input.proposal.description ?? null,
            },
            {
              idempotencyKey:
                input.branch.proposeIdempotencyKey ?? input.proposal.idempotencyKey,
            },
          );
          state = emit(ontologyBuilderState(state, { branch }), options.onState);
          const proposedBranchProposal = branch.proposal;
          if (!proposedBranchProposal) {
            throw new Error("ontology branch propose did not return the created proposal");
          }
          proposal = proposedBranchProposal;
        } else {
          proposal = await client.ontology.proposals.submit(
            {
              yamlText: input.yamlText,
              title: input.proposal.title,
              description: input.proposal.description ?? null,
            },
            { idempotencyKey: input.proposal.idempotencyKey },
          );
        }
        state = emit(
          ontologyBuilderState(state, {
            phase: "await_review",
            proposal,
            operationsPath: `ontology/proposals/${encodeURIComponent(proposal.id)}`,
          }),
          options.onState,
        );
        if (input.assign) {
          proposal = await client.ontology.proposals.assign(proposal.id, input.assign);
          state = emit(ontologyBuilderState(state, { proposal }), options.onState);
        }
        if (!input.decision) return state;
        proposal = await client.ontology.proposals.decide(proposal.id, {
          decision: input.decision.decision,
          comment: input.decision.comment ?? null,
          expectedFingerprint: proposal.fingerprint,
        });
        state = emit(ontologyBuilderState(state, { proposal }), options.onState);
        if (!input.shouldExecute) return state;
        state = emit(ontologyBuilderState(state, { phase: "execute_proposal" }), options.onState);
        proposal = await client.ontology.proposals.execute(proposal.id, {
          expectedFingerprint: proposal.fingerprint,
        });
        state = emit(ontologyBuilderState(state, { proposal }), options.onState);
        if (input.shouldReindex ?? true) {
          state = emit(ontologyBuilderState(state, { phase: "reindex" }), options.onState);
          const reindexResults: OntologyObjectReindexResult[] = [];
          for (const operation of ontologyBuilderReindexOperations(proposal)) {
            reindexResults.push(
              await replayObjectReindex(operation.objectTypeApiName, operation.reindexKey),
            );
            state = emit(
              ontologyBuilderState(state, { reindexResults: [...reindexResults] }),
              options.onState,
            );
          }
        }
        if (input.shouldCheckSdkDrift ?? true) {
          state = emit(
            ontologyBuilderState(state, { phase: "check_sdk_drift" }),
            options.onState,
          );
          const driftReport = await loadSdkDriftReport();
          state = emit(ontologyBuilderState(state, { driftReport }), options.onState);
        }
        return emit(ontologyBuilderState(state, { phase: "ready" }), options.onState);
      } catch (error) {
        return emit(ontologyBuilderFailureState(state, error), options.onState);
      }
    },
  };
}

export function createLongRunningOperationRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  const pollWorkflowRun = (
    workflowRunId: string,
    options: PollFoundryLiteOperationOptions<ProductWorkflowRun> = {},
  ) =>
    pollFoundryLiteOperation(
      () => client.operations.workflows.get(workflowRunId),
      { isTerminal: isFoundryLiteProductWorkflowTerminal, ...options },
    );

  return {
    startConnectorSync: (
      payload: ConnectorSyncWorkflowStartRequest,
      options: { idempotencyKey: string },
    ) => client.operations.workflows.startConnectorSync(payload, options),
    getWorkflowRun: (workflowRunId: string) => client.operations.workflows.get(workflowRunId),
    pollWorkflowRun,
    startAndPollConnectorSync: async (
      payload: ConnectorSyncWorkflowStartRequest,
      options: ConnectorSyncPollOptions,
    ) => {
      const { idempotencyKey, ...pollOptions } = options;
      const started = await client.operations.workflows.startConnectorSync(payload, {
        idempotencyKey,
      });
      return pollWorkflowRun(started.workflowRunId, pollOptions);
    },
    streamOperationEvents: <
      TEvent extends FoundryLiteOperationStreamEvent = FoundryLiteOperationStreamEvent,
    >(
      path: string,
      options?: StreamFoundryLiteOperationEventsOptions<TEvent>,
    ) => streamFoundryLiteOperationEvents<TEvent>(path, options),
  };
}

export function createOperationsEvidenceRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  return {
    listRuns: (filters: RuntimeRunQueryFilters = {}) => client.operations.runs.list(filters),
    loadRunDetail: (runType: RuntimeRunType | string, runId: string) =>
      client.operations.runs.detail(runType, runId),
    loadPromptArtifact: (runId: string, artifactId: string) =>
      client.operations.runs.promptArtifact(runId, artifactId),
    loadRunInvestigation: async (
      selection: OperationsEvidenceSelection = {},
      filters: RuntimeRunQueryFilters = {},
    ): Promise<OperationsEvidenceInvestigation> => {
      const runsPromise = client.operations.runs.list(filters);
      const detailPromise =
        selection.runType && selection.runId
          ? client.operations.runs.detail(selection.runType, selection.runId)
          : Promise.resolve(null);
      const promptArtifactPromise =
        selection.runId && selection.promptArtifactId
          ? client.operations.runs.promptArtifact(selection.runId, selection.promptArtifactId)
          : Promise.resolve(null);
      const [runs, detail, promptArtifact] = await Promise.all([
        runsPromise,
        detailPromise,
        promptArtifactPromise,
      ]);
      return { runs, detail, promptArtifact };
    },
  };
}

export function createRecordDlqOperationsRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  return {
    listRecords: (filters: { status?: DeadLetterRecordStatus } = {}) =>
      client.operations.deadLetterRecords.list(filters),
    loadRecord: (id: string) => client.operations.deadLetterRecords.get(id),
    retryRecord: (id: string, options: { idempotencyKey: string }) =>
      client.operations.deadLetterRecords.retry(id, options),
    bulkRetryRecords: (ids: string[], options: { idempotencyKey: string }) =>
      client.operations.deadLetterRecords.bulkRetry(ids, options),
    discardRecord: (id: string) => client.operations.deadLetterRecords.discard(id),
  };
}

export function createWritebackReconciliationRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  return {
    listWritebacks: (filters: ActionWritebackQueueFilters = {}) =>
      client.operations.reconciliation.list(filters),
    resolveWriteback: (writebackId: string, payload: ActionWritebackReconciliationRequest) =>
      client.operations.reconciliation.resolve(writebackId, payload),
    approveRecovery: (writebackId: string, payload: ActionWritebackRecoveryApprovalRequest) =>
      client.operations.reconciliation.approveRecovery(writebackId, payload),
  };
}

export function createMaintenanceOperationsRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  return {
    detectObservability: (payload: ObservabilityDetectRequest) =>
      client.operations.observability.detect(payload),
    planIcebergMaintenanceReadOnly: (
      datasetRef: string,
      options: IcebergMaintenancePlanOptions = {},
    ) => client.operations.icebergMaintenance.planReadOnly(datasetRef, options),
    requestIcebergMaintenancePlan: (
      datasetRef: string,
      options: IcebergMaintenancePlanOptions = {},
    ) => client.operations.icebergMaintenance.plan(datasetRef, options),
    runIcebergMaintenance: (
      datasetRef: string,
      options: IcebergMaintenancePlanOptions = {},
    ) => client.operations.icebergMaintenance.run(datasetRef, options),
    replayObjectTypeIndex: (objectType: string) =>
      client.operations.index.replayObjectType(objectType),
    replayFailedIndexRun: (runId: string) => client.operations.index.replayFailedRun(runId),
    retryTransformRun: (runId: string) => client.operations.transforms.retry(runId),
  };
}

export function createAdminOperationsRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  const loadOverview = () => client.operations.admin.overview();
  const loadTaskPlan = () => client.operations.admin.taskPlan();
  const loadOperationsBoard = async () => {
    const [overview, plan] = await Promise.all([loadOverview(), loadTaskPlan()]);
    return adminOperationsBoard(overview, plan);
  };

  return {
    loadOverview,
    loadTaskPlan,
    loadOperationsBoard,
    launchModel: adminOperationsLaunchModel,
    loadLaunchModel: async () => adminOperationsLaunchModel(await loadOperationsBoard()),
    launchpad: adminOperationsLaunchpad,
    loadLaunchpad: async () => adminOperationsLaunchpad(await loadOperationsBoard()),
    operatorCommandCards: adminOperatorCommandCards,
    loadOperatorCommandCards: async () =>
      adminOperationsLaunchModel(await loadOperationsBoard()).operatorCommandCards,
    internalOperationsWorkbench: adminInternalOperationsWorkbench,
    loadInternalOperationsWorkbench: async (
      selection: AdminInternalOperationsWorkbenchSelection = {},
    ) => adminInternalOperationsWorkbench(await loadOperationsBoard(), selection),
    publishOutboxBatch: (payload: OutboxPublishRequest = {}) =>
      client.operations.outbox.publishPending(payload),
  };
}

export function adminOperationsLaunchModel(board: AdminOperationsBoard): AdminOperationsLaunchModel {
  const browserActions = board.browserRunnableTaskViews.map((view) =>
    adminLaunchItemFromTaskView(view, "browser_action"),
  );
  const operatorCommands = board.commandRows.map((row) => adminLaunchItemFromCommandRow(row));
  const futureSurfaceActions = board.requiredBackendSurfaces.flatMap((surface) =>
    surface.taskViews.map((view) => adminLaunchItemFromTaskView(view, "future_surface")),
  );
  const operatorCommandCards = adminOperatorCommandCards(operatorCommands);
  return {
    board,
    browserActions,
    operatorCommands,
    migrationCommands: operatorCommands.filter((item) => item.area === "migration"),
    workerCommands: operatorCommands.filter((item) => item.area === "worker"),
    bootstrapCommands: operatorCommands.filter((item) => item.area === "bootstrap"),
    operatorCommandCards,
    migrationCommandCards: operatorCommandCards.filter((card) => card.sectionId === "migration"),
    workerCommandCards: operatorCommandCards.filter((card) => card.sectionId === "worker"),
    bootstrapCommandCards: operatorCommandCards.filter((card) => card.sectionId === "bootstrap"),
    futureSurfaceActions,
    requiredBackendSurfaces: board.requiredBackendSurfaces,
    recommendedDefaultSection: adminLaunchDefaultSection(
      browserActions,
      operatorCommands,
      futureSurfaceActions,
    ),
    hasBrowserActions: browserActions.length > 0,
    hasOperatorCommands: operatorCommands.length > 0,
    hasCopyableCommands: operatorCommandCards.some((card) => card.canCopyCommand),
    hasFutureSurfaces: futureSurfaceActions.length > 0,
  };
}

export function adminOperatorCommandCards(items: AdminLaunchItem[]): AdminOperatorCommandCard[] {
  return items
    .filter((item) => item.command !== null)
    .map((item) => adminOperatorCommandCard(item, item.command ?? ""));
}

export function adminCommandCenter(
  input: AdminOperationsLaunchModel | AdminOperatorCommandCard[],
  selection: AdminCommandCenterSelection = {},
): AdminCommandCenterView {
  const cards = Array.isArray(input) ? input : input.operatorCommandCards;
  const sectionId = selection.sectionId ?? "all";
  const query = (selection.query ?? "").trim().toLowerCase();
  const includeBlocked = selection.includeBlocked ?? true;
  const visibleCards = cards.filter((card) =>
    adminCommandCardMatches(card, sectionId, query, includeBlocked),
  );
  const selectedCard =
    visibleCards.find((card) => card.id === selection.selectedCommandId) ?? null;
  const recommendedCard =
    selectedCard ?? visibleCards.find((card) => card.canCopyCommand) ?? visibleCards[0] ?? null;
  return {
    cards,
    visibleCards,
    selectedCard,
    recommendedCard,
    sectionId,
    query,
    copyableCards: cards.filter((card) => card.canCopyCommand),
    blockedCards: cards.filter((card) => card.tone === "blocked"),
    approvalCards: cards.filter((card) => card.tone === "approval"),
    operatorCards: cards.filter((card) => card.sectionId === "operator"),
    migrationCards: cards.filter((card) => card.sectionId === "migration"),
    workerCards: cards.filter((card) => card.sectionId === "worker"),
    bootstrapCards: cards.filter((card) => card.sectionId === "bootstrap"),
    sectionCounts: adminCommandCardCounts(cards),
    visibleSectionCounts: adminCommandCardCounts(visibleCards),
    hasCards: cards.length > 0,
    hasVisibleCards: visibleCards.length > 0,
    hasCopyableCommands: cards.some((card) => card.canCopyCommand),
    hasBlockedCommands: cards.some((card) => card.tone === "blocked"),
    hasApprovalCommands: cards.some((card) => card.tone === "approval"),
  };
}

export function adminOperationsLaunchpad(board: AdminOperationsBoard): AdminOperationsLaunchpadModel {
  const model = adminOperationsLaunchModel(board);
  const browserSection = adminLaunchpadSection(
    "browser",
    "Browser-safe actions",
    "Bounded actions that the current SDK can start from an authenticated browser screen.",
    model.browserActions,
    model.recommendedDefaultSection,
  );
  const operatorSection = adminLaunchpadSection(
    "operator",
    "Operator commands",
    "Commands and runbook steps that the frontend can display but must not execute directly.",
    model.operatorCommands,
    model.recommendedDefaultSection,
  );
  const migrationSection = adminLaunchpadSection(
    "migration",
    "Migrations",
    "Schema-changing work stays CLI-only until a privileged approval and lock-aware backend exists.",
    model.migrationCommands,
    model.recommendedDefaultSection,
  );
  const workerSection = adminLaunchpadSection(
    "worker",
    "Worker daemons",
    "Worker entrypoints can be shown with evidence paths, but daemon lifecycle control is not browser-safe yet.",
    model.workerCommands,
    model.recommendedDefaultSection,
  );
  const bootstrapSection = adminLaunchpadSection(
    "bootstrap",
    "Infrastructure bootstrap",
    "Bootstrap remains runbook/package-script driven until hardened privileged admin services exist.",
    model.bootstrapCommands,
    model.recommendedDefaultSection,
  );
  const futureSection = adminLaunchpadSection(
    "future",
    "Future backend surfaces",
    "Missing privileged surfaces remain visible as product work instead of being rendered as fake buttons.",
    model.futureSurfaceActions,
    model.recommendedDefaultSection,
  );
  const sections = [
    browserSection,
    operatorSection,
    migrationSection,
    workerSection,
    bootstrapSection,
    futureSection,
  ];
  return {
    ...model,
    browserSection,
    operatorSection,
    migrationSection,
    workerSection,
    bootstrapSection,
    futureSection,
    sections,
    visibleSections: sections.filter((section) => section.hasItems),
    recommendedSection:
      sections.find((section) => section.id === model.recommendedDefaultSection) ?? browserSection,
  };
}

export function adminInternalOperationsWorkbench(
  board: AdminOperationsBoard,
  selection: AdminInternalOperationsWorkbenchSelection = {},
): AdminInternalOperationsWorkbenchView {
  const launchpad = adminOperationsLaunchpad(board);
  const selectedAreaId = selection.selectedAreaId ?? launchpad.recommendedSection.id;
  const selectedSection = adminLaunchpadSectionById(launchpad, selectedAreaId);
  const sectionId = selection.sectionId ?? adminCommandCenterSectionForArea(selectedAreaId);
  const commandCenter = adminCommandCenter(launchpad, {
    sectionId,
    query: selection.query,
    selectedCommandId: selection.selectedCommandId,
    includeBlocked: selection.includeBlocked,
  });
  const privilegedCommandCards = [
    ...launchpad.migrationCommandCards,
    ...launchpad.workerCommandCards,
    ...launchpad.bootstrapCommandCards,
  ];
  const readiness = adminInternalOperationsReadiness(
    launchpad,
    commandCenter,
    privilegedCommandCards,
  );
  return {
    launchpad,
    commandCenter,
    selectedAreaId,
    selectedSection,
    browserActions: launchpad.browserActions,
    operatorCommandCards: launchpad.operatorCommandCards,
    privilegedCommandCards,
    migrationCommandCards: launchpad.migrationCommandCards,
    workerCommandCards: launchpad.workerCommandCards,
    bootstrapCommandCards: launchpad.bootstrapCommandCards,
    futureSurfaceActions: launchpad.futureSurfaceActions,
    requiredBackendSurfaces: launchpad.requiredBackendSurfaces,
    visibleCommandCards: commandCenter.visibleCards,
    selectedCommandCard: commandCenter.selectedCard,
    recommendedCommandCard: commandCenter.recommendedCard,
    copyableCommandCards: commandCenter.copyableCards,
    approvalCommandCards: commandCenter.approvalCards,
    blockedCommandCards: commandCenter.blockedCards,
    readiness,
    hasBrowserActions: launchpad.hasBrowserActions,
    hasOperatorCommands: launchpad.hasOperatorCommands,
    hasPrivilegedCommands: privilegedCommandCards.length > 0,
    hasFutureSurfaces: launchpad.hasFutureSurfaces,
    hasRequiredBackendSurfaces: launchpad.requiredBackendSurfaces.length > 0,
  };
}

function adminLaunchItemFromCommandRow(row: AdminTaskCommandRow): AdminLaunchItem {
  return adminLaunchItemFromTaskView(row.view, "operator_command", row.command);
}

function adminLaunchItemFromTaskView(
  view: AdminTaskView,
  kind: AdminLaunchKind,
  command: string | null = null,
): AdminLaunchItem {
  const task = view.task;
  const riskLevel = adminLaunchRiskLevel(view, kind);
  return {
    id: task.id,
    title: task.title,
    area: task.area,
    kind,
    riskLevel,
    riskLabel: adminLaunchRiskLabel(riskLevel),
    executionSurface: task.executionSurface,
    canStartFromBrowser: task.canStartFromBrowser,
    canRenderActionButton: view.canRenderActionButton,
    canShowCommand: view.canShowCommand,
    requiresApproval: task.requiresApproval,
    requiresBackendSurface: view.requiresBackendSurface,
    checklist: view.checklist,
    command,
    sdkSurface: task.sdkSurface,
    apiRoute: task.apiRoute,
    evidencePath: task.evidencePath,
    requiredBackendSurface: task.requiredBackendSurface,
    blockingReason: task.blockingReason,
    disabledReason: view.disabledReason,
  };
}

function adminOperatorCommandCard(
  item: AdminLaunchItem,
  command: string,
): AdminOperatorCommandCard {
  const sectionId = adminCommandCardSectionId(item.area);
  return {
    id: item.id,
    title: item.title,
    area: item.area,
    sectionId,
    description: adminOperatorCommandDescription(item),
    command,
    copyLabel: adminCommandCopyLabel(sectionId),
    canCopyCommand: !item.requiredBackendSurface,
    tone: adminCommandCardTone(item),
    riskLevel: item.riskLevel,
    riskLabel: item.riskLabel,
    executionSurface: item.executionSurface,
    requiresApproval: item.requiresApproval,
    checklist: item.checklist,
    evidencePath: item.evidencePath,
    requiredBackendSurface: item.requiredBackendSurface,
    blockingReason: item.blockingReason,
    disabledReason: item.disabledReason,
  };
}

function adminCommandCardSectionId(area: AdminTaskArea): AdminCommandCardSectionId {
  switch (area) {
    case "migration":
      return "migration";
    case "worker":
      return "worker";
    case "bootstrap":
      return "bootstrap";
    case "overview":
    case "event_plane":
    case "workspace":
      return "operator";
  }
}

function adminCommandCardMatches(
  card: AdminOperatorCommandCard,
  sectionId: AdminCommandCenterSectionId,
  query: string,
  includeBlocked: boolean,
): boolean {
  if (sectionId !== "all" && card.sectionId !== sectionId) return false;
  if (!includeBlocked && card.tone === "blocked") return false;
  if (!query) return true;
  return adminCommandCardSearchText(card).includes(query);
}

function adminCommandCardSearchText(card: AdminOperatorCommandCard): string {
  return [
    card.id,
    card.title,
    card.area,
    card.sectionId,
    card.description,
    card.command,
    card.copyLabel,
    card.riskLabel,
    card.executionSurface,
    card.evidencePath ?? "",
    card.requiredBackendSurface ?? "",
    card.blockingReason ?? "",
    card.disabledReason ?? "",
    ...card.checklist,
  ]
    .join(" ")
    .toLowerCase();
}

function adminCommandCardCounts(
  cards: AdminOperatorCommandCard[],
): Record<AdminCommandCardSectionId, number> {
  return {
    operator: cards.filter((card) => card.sectionId === "operator").length,
    migration: cards.filter((card) => card.sectionId === "migration").length,
    worker: cards.filter((card) => card.sectionId === "worker").length,
    bootstrap: cards.filter((card) => card.sectionId === "bootstrap").length,
  };
}

function adminOperatorCommandDescription(item: AdminLaunchItem): string {
  if (item.blockingReason) return item.blockingReason;
  if (item.requiredBackendSurface) return `Requires ${item.requiredBackendSurface}.`;
  if (item.requiresApproval) return "Requires operator approval before running outside the browser.";
  return item.disabledReason ?? "Run outside the browser and keep the evidence path visible.";
}

function adminCommandCopyLabel(sectionId: AdminCommandCardSectionId): string {
  switch (sectionId) {
    case "migration":
      return "Copy migration command";
    case "worker":
      return "Copy worker command";
    case "bootstrap":
      return "Copy bootstrap command";
    case "operator":
      return "Copy operator command";
  }
}

function adminCommandCardTone(item: AdminLaunchItem): AdminCommandCardTone {
  if (item.requiredBackendSurface || item.blockingReason) return "blocked";
  if (item.requiresApproval) return "approval";
  return "normal";
}

function adminLaunchpadSection(
  id: AdminLaunchpadSectionId,
  title: string,
  description: string,
  items: AdminLaunchItem[],
  recommendedDefaultSection: AdminLaunchSection,
): AdminLaunchpadSection {
  return {
    id,
    title,
    description,
    items,
    count: items.length,
    hasItems: items.length > 0,
    isRecommendedDefault: id === recommendedDefaultSection,
    canStartFromBrowser: items.some((item) => item.canStartFromBrowser),
    requiresApproval: items.some((item) => item.requiresApproval),
    hasRequiredBackendSurfaces: items.some((item) => item.requiresBackendSurface),
  };
}

function adminLaunchpadSectionById(
  launchpad: AdminOperationsLaunchpadModel,
  id: AdminLaunchpadSectionId,
): AdminLaunchpadSection {
  return launchpad.sections.find((section) => section.id === id) ?? launchpad.recommendedSection;
}

function adminCommandCenterSectionForArea(
  areaId: AdminLaunchpadSectionId,
): AdminCommandCenterSectionId {
  switch (areaId) {
    case "operator":
      return "operator";
    case "migration":
      return "migration";
    case "worker":
      return "worker";
    case "bootstrap":
      return "bootstrap";
    case "browser":
    case "future":
      return "all";
  }
}

function adminInternalOperationsReadiness(
  launchpad: AdminOperationsLaunchpadModel,
  commandCenter: AdminCommandCenterView,
  privilegedCommandCards: AdminOperatorCommandCard[],
): AdminInternalOperationsReadiness {
  return {
    browserActionCount: launchpad.browserActions.length,
    operatorCommandCount: launchpad.operatorCommandCards.length,
    privilegedCommandCount: privilegedCommandCards.length,
    copyableCommandCount: commandCenter.copyableCards.length,
    approvalCommandCount: commandCenter.approvalCards.length,
    blockedCommandCount: commandCenter.blockedCards.length,
    futureSurfaceCount: launchpad.futureSurfaceActions.length,
    requiredBackendSurfaceCount: launchpad.requiredBackendSurfaces.length,
    canStartFromBrowser: launchpad.hasBrowserActions,
    hasOperatorReview: launchpad.hasOperatorCommands || commandCenter.hasApprovalCommands,
    hasPrivilegedBackendGaps:
      launchpad.hasFutureSurfaces ||
      launchpad.requiredBackendSurfaces.length > 0 ||
      commandCenter.hasBlockedCommands,
    hasCopyableCommands: commandCenter.hasCopyableCommands,
  };
}

function adminLaunchRiskLevel(
  view: AdminTaskView,
  kind: AdminLaunchKind,
): AdminLaunchRiskLevel {
  if (kind === "future_surface") return "future_surface";
  if (view.requiresBackendSurface) return "backend_surface_required";
  if (view.task.requiresApproval) return "operator_approval";
  return "browser_safe";
}

function adminLaunchRiskLabel(riskLevel: AdminLaunchRiskLevel): string {
  switch (riskLevel) {
    case "browser_safe":
      return "Browser safe";
    case "operator_approval":
      return "Operator approval";
    case "backend_surface_required":
      return "Backend surface required";
    case "future_surface":
      return "Future surface";
  }
}

function adminLaunchDefaultSection(
  browserActions: AdminLaunchItem[],
  operatorCommands: AdminLaunchItem[],
  futureSurfaceActions: AdminLaunchItem[],
): AdminLaunchSection {
  if (browserActions.length > 0) return "browser";
  if (operatorCommands.length > 0) return "operator";
  if (futureSurfaceActions.length > 0) return "future";
  return "browser";
}

export function createRecoveryOperationsRecipe(
  client: Pick<FoundryLiteGeneratedClient, "operations">,
) {
  return {
    loadRecoveryOverview: () => client.operations.backupRestore.recoveryOverview(),
    preflight: (payload: BackupRestorePreflightRequest = {}) =>
      client.operations.backupRestore.preflight(payload),
    restoreArtifact: (payload: BackupRestoreArtifactRestoreRequest) =>
      client.operations.backupRestore.restoreArtifact(payload),
    executeArtifactRestore: (payload: BackupRestoreArtifactRestoreRequest) =>
      client.operations.backupRestore.executeArtifactRestore(payload),
    startRestoreMode: (payload: BackupRestoreModeStartRequest = {}) =>
      client.operations.backupRestore.startRestoreMode(payload),
    restoreModeStatus: (restoreId: string) =>
      client.operations.backupRestore.restoreModeStatus(restoreId),
    postRestoreValidation: (
      restoreId: string,
      payload: BackupRestorePostRestoreValidationRequest = {},
    ) => client.operations.backupRestore.postRestoreValidation(restoreId, payload),
    approveResume: (restoreId: string, payload: BackupRestoreResumeApprovalRequest = {}) =>
      client.operations.backupRestore.approveResume(restoreId, payload),
  };
}

export function operatorWorkspaceHomeView(input: OperatorWorkspaceHomeInput): OperatorWorkspaceHomeView {
  const runItems = operatorWorkspaceRunRows(input.runs);
  const requiredOperatorActions = input.recoveryOverview?.requiredOperatorActions ?? [];
  const objectTypeCount = input.catalog?.objectTypes.length ?? 0;
  const actionTypeCount =
    input.catalog?.objectTypes.reduce((count, objectType) => count + objectType.actions.length, 0) ??
    0;
  const failedRunCount = runItems.filter((run) => operatorWorkspaceRunStatus(run) === "failed").length;
  const runningRunCount = runItems.filter((run) => operatorWorkspaceRunStatus(run) === "running").length;
  return {
    ...input,
    datasetCount: input.datasets.length,
    objectTypeCount,
    actionTypeCount,
    runCount: runItems.length,
    failedRunCount,
    runningRunCount,
    hasDatasets: input.datasets.length > 0,
    hasOntologyTypes: objectTypeCount > 0,
    hasFailedRuns: failedRunCount > 0,
    hasRunningRuns: runningRunCount > 0,
    hasAdminBrowserActions: input.adminLaunchpad.hasBrowserActions,
    hasOperatorCommands: input.adminLaunchpad.hasOperatorCommands,
    hasRecoveryActions: requiredOperatorActions.length > 0,
    recommendedAdminSectionId: input.adminLaunchpad.recommendedSection.id,
  };
}

export function operatorWorkspaceNavigation(
  home: OperatorWorkspaceHomeView,
  selectedAreaId: OperatorWorkspaceAreaId = "home",
): OperatorWorkspaceNavigationView {
  const navItems = operatorWorkspaceNavItems(home, selectedAreaId);
  const selectedItem =
    navItems.find((item) => item.isSelected) ?? operatorWorkspaceFallbackNavItem();
  const attentionItems = navItems.filter((item) => item.priority === "attention");
  const quickActions = operatorWorkspaceQuickActions(home);
  return {
    selectedAreaId: selectedItem.id,
    navItems,
    selectedItem,
    attentionItems,
    quickActions,
    hasAttention:
      attentionItems.length > 0 ||
      quickActions.some((action) => action.priority === "attention"),
  };
}

export function operatorWorkspaceShell(
  home: OperatorWorkspaceHomeView,
  selectedAreaId: OperatorWorkspaceAreaId = "home",
): OperatorWorkspaceShellView {
  const navigation = operatorWorkspaceNavigation(home, selectedAreaId);
  const areaSurfaces = navigation.navItems.map(operatorWorkspaceAreaSurface);
  const selectedSurface =
    areaSurfaces.find((surface) => surface.id === navigation.selectedAreaId) ??
    operatorWorkspaceAreaSurface(navigation.selectedItem);
  const primaryQuickAction =
    navigation.quickActions.find((action) => action.priority === "attention") ??
    navigation.quickActions[0] ??
    null;
  return {
    home,
    navigation,
    areaSurfaces,
    selectedSurface,
    selectedAreaId: selectedSurface.id,
    selectedRoute: selectedSurface.route,
    selectedTitle: selectedSurface.title,
    selectedDescription: selectedSurface.description,
    selectedBadge: selectedSurface.badge,
    selectedCount: selectedSurface.count,
    primaryQuickAction,
    attentionCount: navigation.attentionItems.length,
    hasPrimaryQuickAction: primaryQuickAction !== null,
    hasAttention: navigation.hasAttention,
  };
}

function operatorWorkspaceNavItems(
  home: OperatorWorkspaceHomeView,
  selectedAreaId: OperatorWorkspaceAreaId,
): OperatorWorkspaceNavItem[] {
  const items: Array<Omit<OperatorWorkspaceNavItem, "isSelected">> = [
    operatorWorkspaceNavItem(
      "home",
      "Home",
      "Cross-workspace summary and next actions.",
      "/",
      null,
      "Workspace",
    ),
    operatorWorkspaceNavItem(
      "data",
      "Data",
      "Datasets, versions, quality, preview, and lineage.",
      "/datasets",
      home.datasetCount,
      null,
    ),
    operatorWorkspaceNavItem(
      "ontology",
      "Ontology",
      "Object types, action palette, links, and SDK drift hints.",
      "/ontology",
      home.objectTypeCount,
      null,
    ),
    operatorWorkspaceNavItem(
      "operations",
      "Operations",
      "Runs, failures, evidence, prompt artifacts, and live timelines.",
      "/operations",
      home.runCount,
      home.hasFailedRuns ? `${home.failedRunCount} failed` : null,
      home.hasFailedRuns ? "attention" : "normal",
    ),
    operatorWorkspaceNavItem("insights", "Insights", "Human review queues and action proposal decisions.", "/insights", null, null),
    operatorWorkspaceNavItem("media", "Media", "Upload, processing, indexing, and search workflows.", "/media", null, null),
    operatorWorkspaceNavItem("aip", "AIP", "Agent and builder runs with governed evidence.", "/aip", null, null),
    operatorWorkspaceNavItem("pipeline", "Pipeline", "SQL transform registration, runs, retries, and evidence.", "/pipeline", null, null),
    operatorWorkspaceNavItem(
      "admin",
      "Admin",
      "Browser-safe actions, operator commands, migration, worker, and bootstrap sections.",
      "/admin",
      home.adminLaunchpad.visibleSections.length,
      home.hasOperatorCommands ? "operator work" : null,
      home.hasOperatorCommands ? "attention" : "normal",
    ),
    operatorWorkspaceNavItem(
      "recovery",
      "Recovery",
      "Restore-mode status, validation, resume checks, and required operator actions.",
      "/recovery",
      home.recoveryOverview?.requiredOperatorActions.length ?? 0,
      home.hasRecoveryActions ? "action required" : null,
      home.hasRecoveryActions ? "attention" : "normal",
    ),
  ];
  return items.map((item) => ({ ...item, isSelected: item.id === selectedAreaId }));
}

function operatorWorkspaceNavItem(
  id: OperatorWorkspaceAreaId,
  title: string,
  description: string,
  route: string,
  count: number | null,
  badge: string | null,
  priority: OperatorWorkspaceNavPriority = "normal",
): Omit<OperatorWorkspaceNavItem, "isSelected"> {
  return {
    id,
    title,
    description,
    route,
    count,
    badge,
    priority,
    isEnabled: true,
    disabledReason: null,
  };
}

function operatorWorkspaceFallbackNavItem(): OperatorWorkspaceNavItem {
  return {
    ...operatorWorkspaceNavItem(
      "home",
      "Home",
      "Cross-workspace summary and next actions.",
      "/",
      null,
      "Workspace",
    ),
    isSelected: true,
  };
}

function operatorWorkspaceAreaSurface(
  item: OperatorWorkspaceNavItem,
): OperatorWorkspaceAreaSurface {
  return {
    ...item,
    recipeEntry: operatorWorkspaceRecipeEntry(item.id),
    reactHook: operatorWorkspaceReactHook(item.id),
    primarySdkSurface: operatorWorkspacePrimarySdkSurface(item.id),
  };
}

function operatorWorkspaceRecipeEntry(area: OperatorWorkspaceAreaId): string {
  switch (area) {
    case "home":
      return "operatorWorkspace.loadHome";
    case "data":
      return "datasetExplorer";
    case "ontology":
      return "objectActionWorkspace";
    case "operations":
      return "operationsEvidence";
    case "insights":
      return "insightReviewWorkspace";
    case "media":
      return "mediaWorkspace";
    case "aip":
      return "aipWorkspace";
    case "pipeline":
      return "pipelineBuilder";
    case "admin":
      return "adminOperations";
    case "recovery":
      return "recoveryOperations";
  }
}

function operatorWorkspaceReactHook(area: OperatorWorkspaceAreaId): string {
  switch (area) {
    case "home":
      return "useFoundryLiteProvidedOperatorWorkspaceShell";
    case "data":
      return "useFoundryLiteProvidedDatasetExplorer";
    case "ontology":
      return "useFoundryLiteProvidedOntologyWorkspaceShell";
    case "operations":
      return "useFoundryLiteProvidedOperationsInvestigation";
    case "insights":
      return "useFoundryLiteProvidedInsightReviewQueue";
    case "media":
      return "useFoundryLiteProvidedMediaPipeline";
    case "aip":
      return "useFoundryLiteProvidedAipAgentRunWithOperationsDetail";
    case "pipeline":
      return "useFoundryLiteProvidedSqlTransformSubmit";
    case "admin":
      return "useFoundryLiteProvidedAdminCommandCenter";
    case "recovery":
      return "useFoundryLiteRecoveryOverview";
  }
}

function operatorWorkspacePrimarySdkSurface(area: OperatorWorkspaceAreaId): string {
  switch (area) {
    case "home":
      return "createOperatorWorkspaceRecipe";
    case "data":
      return "client.datasets";
    case "ontology":
      return "client.ontology";
    case "operations":
      return "client.operations.runs";
    case "insights":
      return "client.insights.reviews";
    case "media":
      return "client.media";
    case "aip":
      return "client.aip";
    case "pipeline":
      return "client.transforms";
    case "admin":
      return "client.operations.admin";
    case "recovery":
      return "client.operations.backupRestore";
  }
}

function operatorWorkspaceQuickActions(home: OperatorWorkspaceHomeView): OperatorWorkspaceQuickAction[] {
  const actions: OperatorWorkspaceQuickAction[] = [];
  if (!home.hasDatasets) {
    actions.push(
      operatorWorkspaceQuickAction(
        "open-data",
        "Connect data",
        "Start from datasets and connector evidence.",
        "data",
        "/datasets",
      ),
    );
  }
  if (!home.hasOntologyTypes) {
    actions.push(
      operatorWorkspaceQuickAction(
        "open-ontology",
        "Review ontology",
        "Check object/action catalog readiness.",
        "ontology",
        "/ontology",
      ),
    );
  }
  if (home.hasFailedRuns) {
    actions.push(
      operatorWorkspaceQuickAction(
        "investigate-failed-runs",
        "Investigate failed runs",
        "Open Operations evidence for failed runs.",
        "operations",
        "/operations",
        home.failedRunCount,
        "attention",
      ),
    );
  }
  if (home.hasRecoveryActions) {
    actions.push(
      operatorWorkspaceQuickAction(
        "review-recovery",
        "Review recovery actions",
        "Open recovery controls and required operator actions.",
        "recovery",
        "/recovery",
        home.recoveryOverview?.requiredOperatorActions.length ?? null,
        "attention",
      ),
    );
  }
  if (home.hasAdminBrowserActions || home.hasOperatorCommands) {
    actions.push(
      operatorWorkspaceQuickAction(
        "open-admin-launchpad",
        "Open admin launchpad",
        "Separate browser-safe actions from operator commands.",
        "admin",
        "/admin",
        home.adminLaunchpad.visibleSections.length,
        home.hasOperatorCommands ? "attention" : "normal",
      ),
    );
  }
  return actions;
}

function operatorWorkspaceQuickAction(
  id: string,
  title: string,
  description: string,
  area: OperatorWorkspaceAreaId,
  route: string,
  count: number | null = null,
  priority: OperatorWorkspaceNavPriority = "normal",
): OperatorWorkspaceQuickAction {
  return { id, title, description, area, route, count, priority };
}

function operatorWorkspaceRunRows(runs: RuntimeRunQueryResult): RuntimeRow[] {
  return [
    ...runs.syncRuns,
    ...runs.transformRuns,
    ...runs.indexRuns,
    ...runs.actionRuns,
    ...runs.actionWritebacks,
    ...runs.materializationRuns,
    ...runs.outboxEvents,
    ...runs.deadLetterEvents,
    ...runs.workflowRuns,
    ...runs.aiRuns,
    ...runs.auditEvents,
    ...runs.objectEdits,
  ];
}

function operatorWorkspaceRunStatus(run: RuntimeRow): string | null {
  const status = run.status;
  return typeof status === "string" ? status : null;
}

export function createOperatorWorkspaceRecipe(
  client: FoundryLiteGeneratedClient,
  catalog?: OntologyCatalog | null,
) {
  const adminOperations = createAdminOperationsRecipe(client);
  const recoveryOperations = createRecoveryOperationsRecipe(client);
  const loadHome = async (
    options: OperatorWorkspaceHomeOptions = {},
  ): Promise<OperatorWorkspaceHomeView> => {
    const catalogPromise = catalog ? Promise.resolve(catalog) : client.ontology.catalog();
    const adminBoardPromise = adminOperations.loadOperationsBoard();
    const [nextCatalog, datasets, runs, adminBoard, recoveryOverview] = await Promise.all([
      catalogPromise,
      client.datasets.list(),
      client.operations.runs.list(options.runFilters ?? {}),
      adminBoardPromise,
      recoveryOperations.loadRecoveryOverview(),
    ]);
    return operatorWorkspaceHomeView({
      catalog: nextCatalog,
      datasets,
      runs,
      adminLaunchpad: adminOperationsLaunchpad(adminBoard),
      recoveryOverview,
    });
  };
  return {
    datasetExplorer: createDatasetExplorerRecipe(client),
    resourceBrowser: createResourceBrowserRecipe(client),
    objectActionWorkspace: createObjectActionWorkspaceRecipe(client, catalog ?? null),
    operationsEvidence: createOperationsEvidenceRecipe(client),
    adminOperations,
    recoveryOperations,
    loadHome,
    loadShell: async (
      options: OperatorWorkspaceHomeOptions & { selectedAreaId?: OperatorWorkspaceAreaId } = {},
    ) => operatorWorkspaceShell(await loadHome(options), options.selectedAreaId),
  };
}

export function createFoundryLiteScreenRecipes(
  client: FoundryLiteGeneratedClient,
  catalog?: OntologyCatalog | null,
) {
  return {
    operatorWorkspace: createOperatorWorkspaceRecipe(client, catalog ?? null),
    datasetExplorer: createDatasetExplorerRecipe(client),
    resourceBrowser: createResourceBrowserRecipe(client),
    objectActionWorkspace: createObjectActionWorkspaceRecipe(client, catalog ?? null),
    mediaWorkspace: createMediaWorkspaceRecipe(client),
    aipWorkspace: createAipWorkspaceRecipe(client),
    insightReviewWorkspace: createInsightReviewWorkspaceRecipe(client),
    ontologyBuilder: createOntologyBuilderRecipe(client),
    connectorOnboarding: createConnectorOnboardingRecipe(client),
    sourceWizard: createSourceWizardRecipe(client),
    sourceOnboarding: createSourceOnboardingRecipe(client),
    sourceUpload: createSourceUploadRecipe(client),
    sourceSync: createSourceSyncRecipe(client),
    pipelineBuilder: createPipelineBuilderRecipe(client),
    longRunningOperations: createLongRunningOperationRecipe(client),
    operationsEvidence: createOperationsEvidenceRecipe(client),
    recordDlqOperations: createRecordDlqOperationsRecipe(client),
    writebackReconciliation: createWritebackReconciliationRecipe(client),
    maintenanceOperations: createMaintenanceOperationsRecipe(client),
    adminOperations: createAdminOperationsRecipe(client),
    recoveryOperations: createRecoveryOperationsRecipe(client),
  };
}

export async function loadFoundryLiteScreenRecipes(client: FoundryLiteGeneratedClient) {
  return createFoundryLiteScreenRecipes(client, await client.ontology.catalog());
}

export type DatasetExplorerRecipe = ReturnType<typeof createDatasetExplorerRecipe>;
export type ResourceBrowserRecipe = ReturnType<typeof createResourceBrowserRecipe>;
export type ObjectActionWorkspaceRecipe = ReturnType<typeof createObjectActionWorkspaceRecipe>;
export type MediaWorkspaceRecipe = ReturnType<typeof createMediaWorkspaceRecipe>;
export type AipWorkspaceRecipe = ReturnType<typeof createAipWorkspaceRecipe>;
export type InsightReviewWorkspaceRecipe = ReturnType<typeof createInsightReviewWorkspaceRecipe>;
export type OntologyBuilderRecipe = ReturnType<typeof createOntologyBuilderRecipe>;
export type ConnectorOnboardingRecipe = ReturnType<typeof createConnectorOnboardingRecipe>;
export type PipelineBuilderRecipe = ReturnType<typeof createPipelineBuilderRecipe>;
export type LongRunningOperationRecipe = ReturnType<typeof createLongRunningOperationRecipe>;
export type OperationsEvidenceRecipe = ReturnType<typeof createOperationsEvidenceRecipe>;
export type RecordDlqOperationsRecipe = ReturnType<typeof createRecordDlqOperationsRecipe>;
export type WritebackReconciliationRecipe = ReturnType<typeof createWritebackReconciliationRecipe>;
export type MaintenanceOperationsRecipe = ReturnType<typeof createMaintenanceOperationsRecipe>;
export type AdminOperationsRecipe = ReturnType<typeof createAdminOperationsRecipe>;
export type RecoveryOperationsRecipe = ReturnType<typeof createRecoveryOperationsRecipe>;
export type OperatorWorkspaceRecipe = ReturnType<typeof createOperatorWorkspaceRecipe>;
export type FoundryLiteScreenRecipes = ReturnType<typeof createFoundryLiteScreenRecipes>;
export type GeneratedObjectType<TName extends ObjectApiName> = ObjectTypeRegistry[TName];
export type GeneratedActionType<TName extends ActionApiName> = ActionTypeRegistry[TName];
