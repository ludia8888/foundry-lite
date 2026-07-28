import type {
  FoundryLiteApiError,
  MediaProcessorDescriptor,
  PipelineBranch,
  PipelinePreviewRun,
} from "@foundry-lite/sdk";
import {
  createInFlightActionLock,
  idempotencyKey,
  normalizeFoundryLiteError,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import {
  ArrowLeft,
  CheckCircle2,
  FlaskConical,
  GitCompareArrows,
  History,
  LoaderCircle,
  Save,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";

import { CitationEvidencePanel } from "./CitationEvidencePanel";
import { DocumentConfigPanel } from "./DocumentConfigPanel";
import {
  DocumentPreviewCanvas,
  type CitationVerificationState,
} from "./DocumentPreviewCanvas";
import { DocumentResultDock } from "./DocumentResultDock";
import {
  citationEvidenceBlock,
  type CitationNavigationResolution,
  resolveCitationNavigation,
} from "./citation-evidence-model";
import {
  buildDocumentLabGraph,
  DEFAULT_DOCUMENT_LAB_CONFIG,
  documentLabBlocks,
  parseOutputSchema,
  processorForMode,
  type DocumentLabConfig,
} from "./document-lab-model";
import {
  documentComparisonCandidates,
  documentExtractPromotionTargets,
  emptyComparisonResults,
  promoteDocumentExtractProfile,
  type DocumentComparisonResult,
} from "./document-lab-comparison-model";

const LAB_PIPELINE_ID = "document-intelligence-lab";
const GENERAL_TABLE_PREVIEW_ROW_LIMIT = 500;
const TERMINAL_STATUSES = new Set([
  "SUCCEEDED",
  "PARTIAL",
  "FAILED",
  "CANCELLED",
]);

export default function DocumentIntelligencePage() {
  const client = useFoundryLiteClient();
  const actionLock = useMemo(() => createInFlightActionLock(), []);
  const runRequestRef = useRef<{
    signature: string;
    idempotencyKey: string;
  } | null>(null);
  const [searchParams] = useSearchParams();
  const citationNavigationRef = searchParams.get("citation")?.trim() || null;
  const isCitationMode = citationNavigationRef !== null;
  const [config, setConfig] = useState<DocumentLabConfig>(
    DEFAULT_DOCUMENT_LAB_CONFIG,
  );
  const [processors, setProcessors] = useState<MediaProcessorDescriptor[]>([]);
  const [branches, setBranches] = useState<PipelineBranch[]>([]);
  const [branchId, setBranchId] = useState<string | null>(null);
  const [run, setRun] = useState<PipelinePreviewRun | null>(null);
  const [runHistory, setRunHistory] = useState<PipelinePreviewRun[]>([]);
  const [comparisonResults, setComparisonResults] = useState<
    DocumentComparisonResult[]
  >([]);
  const [runProfiles, setRunProfiles] = useState<
    Record<string, DocumentLabConfig>
  >({});
  const [isComparing, setIsComparing] = useState(false);
  const [comparisonFocusToken, setComparisonFocusToken] = useState(0);
  const [selectedPromotionTargetId, setSelectedPromotionTargetId] = useState<
    string | null
  >(null);
  const [isPromoting, setIsPromoting] = useState(false);
  const [promotionMessage, setPromotionMessage] = useState<string | null>(null);
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [isLoadingCatalog, setIsLoadingCatalog] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [citationResolution, setCitationResolution] =
    useState<CitationNavigationResolution | null>(null);
  const [citationError, setCitationError] =
    useState<FoundryLiteApiError | null>(null);
  const [isResolvingCitation, setIsResolvingCitation] = useState(false);
  const [citationResolveAttempt, setCitationResolveAttempt] = useState(0);

  useEffect(() => {
    if (isCitationMode) {
      setIsLoadingCatalog(false);
      return;
    }
    let isCancelled = false;
    setIsLoadingCatalog(true);
    void Promise.all([
      client.media.processors.list(),
      client.pipelines.branches.list({ limit: 100 }),
    ])
      .then(([processorCatalog, branchResult]) => {
        if (isCancelled) return;
        setProcessors(processorCatalog.items);
        setBranches(branchResult.items);
        const labBranch = branchResult.items.find(
          (branch) => branch.pipelineId === LAB_PIPELINE_ID,
        );
        setBranchId(labBranch?.id ?? null);
        setConfig((current) => {
          const available = new Set(
            processorCatalog.items.map((processor) => processor.processorId),
          );
          if (available.has(current.processorId)) return current;
          return {
            ...current,
            processorId:
              processorForMode(current.extractionMode, available) ?? "",
          };
        });
      })
      .catch((caught: unknown) => {
        if (!isCancelled) setError(normalizeFoundryLiteError(caught));
      })
      .finally(() => {
        if (!isCancelled) setIsLoadingCatalog(false);
      });
    return () => {
      isCancelled = true;
    };
  }, [client, isCitationMode]);

  useEffect(() => {
    if (!citationNavigationRef) {
      setCitationResolution(null);
      setCitationError(null);
      setIsResolvingCitation(false);
      return;
    }
    const controller = new AbortController();
    let isCancelled = false;
    setRun(null);
    setCitationResolution(null);
    setCitationError(null);
    setIsResolvingCitation(true);
    void resolveCitationNavigation(
      client,
      citationNavigationRef,
      controller.signal,
    )
      .then((resolution) => {
        if (!isCancelled) setCitationResolution(resolution);
      })
      .catch((caught: unknown) => {
        if (!isCancelled && !controller.signal.aborted) {
          setCitationError(normalizeFoundryLiteError(caught));
        }
      })
      .finally(() => {
        if (!isCancelled) setIsResolvingCitation(false);
      });
    return () => {
      isCancelled = true;
      controller.abort();
    };
  }, [citationNavigationRef, citationResolveAttempt, client]);

  useEffect(() => {
    if (isCitationMode || !run || isTerminal(run.status)) return;
    let isCancelled = false;
    const timer = window.setTimeout(() => {
      void client.pipelines.previewRuns
        .get(run.id)
        .then((current) => {
          if (isCancelled) return;
          setRun(current);
          if (isTerminal(current.status)) {
            setRunHistory((history) =>
              [current, ...history.filter((item) => item.id !== current.id)].slice(
                0,
                5,
              ),
            );
          }
        })
        .catch((caught: unknown) => {
          if (!isCancelled) setError(normalizeFoundryLiteError(caught));
        });
    }, 650);
    return () => {
      isCancelled = true;
      window.clearTimeout(timer);
    };
  }, [client, isCitationMode, run]);

  const blocks = useMemo(
    () =>
      citationResolution
        ? [citationEvidenceBlock(citationResolution)]
        : documentLabBlocks(run),
    [citationResolution, run],
  );
  useEffect(() => {
    if (blocks.length === 0) {
      setSelectedBlockId(null);
      return;
    }
    setSelectedBlockId((current) =>
      current && blocks.some((block) => block.id === current)
        ? current
        : blocks[0].id,
    );
  }, [blocks]);

  const isRunning =
    isStarting || Boolean(run && !isTerminal(run.status));
  const schemaIsValid = useMemo(() => {
    try {
      parseOutputSchema(config.outputSchemaText);
      return true;
    } catch {
      return false;
    }
  }, [config.outputSchemaText]);
  const isVision =
    config.strategy === "basic_vision" ||
    config.strategy === "layout_aware_vision";
  const sourceUrl = useMemo(() => {
    if (citationResolution) {
      return client.media.versions.sourceUrl(
        citationResolution.evidence.mediaItemVersionId,
      );
    }
    if (isCitationMode) return null;
    const mediaItemVersionId = config.mediaItemVersionId.trim();
    return mediaItemVersionId
      ? client.media.versions.sourceUrl(mediaItemVersionId)
      : null;
  }, [citationResolution, client, config.mediaItemVersionId, isCitationMode]);
  const citationState: CitationVerificationState = isResolvingCitation
    ? "resolving"
    : citationResolution
      ? "verified"
      : citationError
        ? "failed"
        : "idle";
  const canRun =
    config.mediaItemVersionId.trim().length > 0 &&
    config.mediaSetRef.trim().length > 0 &&
    (isVision || config.processorId.length > 0) &&
    (config.strategy === "traditional" || schemaIsValid);
  const canCompare =
    config.mediaItemVersionId.trim().length > 0 &&
    config.mediaSetRef.trim().length > 0 &&
    schemaIsValid &&
    !isLoadingCatalog;
  const promotionTargets = useMemo(
    () => documentExtractPromotionTargets(branches),
    [branches],
  );
  useEffect(() => {
    setSelectedPromotionTargetId((current) => {
      if (current && promotionTargets.some((target) => target.id === current)) {
        return current;
      }
      return promotionTargets[0]?.id ?? null;
    });
  }, [promotionTargets]);

  const ensureLabBranch = useCallback(async () => {
    if (branchId) return branchId;
    return actionLock.run("document-lab:ensure-branch", async () => {
      const existing = branches.find(
        (branch) => branch.pipelineId === LAB_PIPELINE_ID,
      );
      if (existing) {
        setBranchId(existing.id);
        return existing.id;
      }
      const created = await client.pipelines.branches.create(
        { pipelineId: LAB_PIPELINE_ID, name: "lab-preview" },
        {
          idempotencyKey: idempotencyKey(
            "document-lab-branch",
            LAB_PIPELINE_ID,
          ),
        },
      );
      setBranches((items) => [created, ...items]);
      setBranchId(created.id);
      return created.id;
    });
  }, [actionLock, branchId, branches, client]);

  const handleRun = useCallback(async () => {
    if (!canRun) return;
    await actionLock.run("document-lab:run-preview", async () => {
      setIsStarting(true);
      setError(null);
      try {
        const activeBranchId = await ensureLabBranch();
        const graph = buildDocumentLabGraph(config);
        const signature = `${activeBranchId}:${config.mediaItemVersionId}:${JSON.stringify(graph)}`;
        if (runRequestRef.current?.signature !== signature) {
          runRequestRef.current = {
            signature,
            idempotencyKey: idempotencyKey(
              "document-lab-preview",
              signature,
            ),
          };
        }
        const created = await client.pipelines.previewRuns.create(
          activeBranchId,
          {
            graph,
            targetNodeId: "out",
            limits: {
              tableRows: GENERAL_TABLE_PREVIEW_ROW_LIMIT,
              mediaItems: 5,
              pdfPages: Math.min(10, Math.max(1, config.pageLimit)),
              totalBytes: 32 * 1024 * 1024,
              timeoutSeconds: 30,
            },
          },
          { idempotencyKey: runRequestRef.current.idempotencyKey },
        );
        runRequestRef.current = null;
        setRun(created);
        setRunProfiles((profiles) => ({ ...profiles, [created.id]: config }));
        setSelectedBlockId(null);
      } catch (caught) {
        setError(normalizeFoundryLiteError(caught));
      } finally {
        setIsStarting(false);
      }
    });
  }, [actionLock, canRun, client, config, ensureLabBranch]);

  const handleCompare = useCallback(async () => {
    if (!canCompare) return;
    await actionLock.run("document-lab:compare", async () => {
      const candidates = documentComparisonCandidates(config, processors);
      setComparisonResults(emptyComparisonResults(candidates));
      setComparisonFocusToken((token) => token + 1);
      setPromotionMessage(null);
      setIsComparing(true);
      setError(null);
      try {
        const activeBranchId = await ensureLabBranch();
        const sessionId = `${Date.now()}-${config.mediaItemVersionId}`;
        const completed = await Promise.all(
          candidates.map(async (candidate) => {
            if (!candidate.config) {
              return { ...candidate, run: null, error: null };
            }
            try {
              const graph = buildDocumentLabGraph(candidate.config);
              const created = await client.pipelines.previewRuns.create(
                activeBranchId,
                {
                  graph,
                  targetNodeId: "out",
                  limits: {
                    tableRows: GENERAL_TABLE_PREVIEW_ROW_LIMIT,
                    mediaItems: 5,
                    pdfPages: Math.min(
                      10,
                      Math.max(1, candidate.config.pageLimit),
                    ),
                    totalBytes: 32 * 1024 * 1024,
                    timeoutSeconds: 30,
                  },
                },
                {
                  idempotencyKey: idempotencyKey(
                    "document-lab-comparison",
                    `${sessionId}:${candidate.id}:${JSON.stringify(graph)}`,
                  ),
                },
              );
              setComparisonResults((results) =>
                updateComparisonResult(results, candidate.id, {
                  ...candidate,
                  run: created,
                  error: null,
                }),
              );
              const terminal = await waitForPreviewRun(
                client.pipelines.previewRuns.get,
                created,
              );
              const result = { ...candidate, run: terminal, error: null };
              setComparisonResults((results) =>
                updateComparisonResult(results, candidate.id, result),
              );
              setRunProfiles((profiles) => ({
                ...profiles,
                [terminal.id]: candidate.config as DocumentLabConfig,
              }));
              setRunHistory((history) =>
                [
                  terminal,
                  ...history.filter((item) => item.id !== terminal.id),
                ].slice(0, 8),
              );
              return result;
            } catch (caught) {
              const result = {
                ...candidate,
                run: null,
                error: comparisonErrorMessage(caught),
              };
              setComparisonResults((results) =>
                updateComparisonResult(results, candidate.id, result),
              );
              return result;
            }
          }),
        );
        setComparisonResults(completed);
        const preferred =
          completed.find(
            (result) =>
              result.id === "layout" && isSuccessfulRun(result.run),
          ) ?? completed.find((result) => isSuccessfulRun(result.run));
        if (preferred?.run && preferred.config) {
          setRun(preferred.run);
          setConfig(preferred.config);
          setSelectedBlockId(null);
        }
        setComparisonFocusToken((token) => token + 1);
      } catch (caught) {
        setError(normalizeFoundryLiteError(caught));
      } finally {
        setIsComparing(false);
      }
    });
  }, [
    actionLock,
    canCompare,
    client,
    config,
    ensureLabBranch,
    processors,
  ]);

  const handleSelectComparison = useCallback(
    (result: DocumentComparisonResult) => {
      if (!result.run || !result.config || !isSuccessfulRun(result.run)) return;
      setRun(result.run);
      setConfig(result.config);
      setSelectedBlockId(null);
      setPromotionMessage(null);
    },
    [],
  );

  const handlePromote = useCallback(async () => {
    if (!run || !isSuccessfulRun(run)) return;
    const profileConfig = runProfiles[run.id];
    const target = promotionTargets.find(
      (candidate) => candidate.id === selectedPromotionTargetId,
    );
    if (!profileConfig || !target) return;
    await actionLock.run("document-lab:promote", async () => {
      setIsPromoting(true);
      setError(null);
      setPromotionMessage(null);
      try {
        const branch = await client.pipelines.branches.get(target.branchId);
        const graph = promoteDocumentExtractProfile(
          branch,
          target.nodeId,
          profileConfig,
          run,
        );
        const updated = await client.pipelines.branches.updateGraph(branch.id, {
          graph,
          expectedFingerprint: branch.graphFingerprint,
        });
        setBranches((items) =>
          items.map((item) => (item.id === updated.id ? updated : item)),
        );
        setPromotionMessage(
          `${target.label}에 ${run.id}의 exact extraction${profileConfig.strategy === "traditional" ? "" : " + Use LLM"} profile을 저장했습니다.`,
        );
      } catch (caught) {
        setError(normalizeFoundryLiteError(caught));
      } finally {
        setIsPromoting(false);
      }
    });
  }, [
    actionLock,
    client,
    promotionTargets,
    run,
    runProfiles,
    selectedPromotionTargetId,
  ]);

  const selectedProfile = run ? runProfiles[run.id] ?? null : null;
  const canPromote =
    isSuccessfulRun(run) &&
    selectedProfile !== null &&
    selectedPromotionTargetId !== null;

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-white">
      <DocumentIntelligenceHeader
        run={run}
        runHistoryCount={runHistory.length}
        branchId={branchId}
        citationResolution={citationResolution}
        citationState={citationState}
        isCitationMode={isCitationMode}
        comparisonCount={comparisonResults.length}
        isComparing={isComparing}
        canCompare={canCompare}
        onCompare={handleCompare}
      />
      {isCitationMode && citationError ? (
        <div className="shrink-0 border-b border-[#E2A6A6] bg-[#FFF5F5] px-3 py-2">
          <ErrorState
            error={citationError}
            onRetry={() => setCitationResolveAttempt((attempt) => attempt + 1)}
          />
        </div>
      ) : null}
      {!isCitationMode && error ? (
        <div className="shrink-0 border-b border-[#E2A6A6] bg-[#FFF5F5] px-3 py-2">
          <ErrorState error={error} onRetry={handleRun} />
        </div>
      ) : null}
      {!isCitationMode && run?.status === "FAILED" && run.error ? (
        <div className="shrink-0 border-b border-[#E2A6A6] bg-[#FFF5F5] px-3 py-1.5 text-[10px] text-[#A82A2A]">
          Preview failed · {previewRunErrorMessage(run.error)}
        </div>
      ) : null}
      {!isCitationMode &&
      !schemaIsValid &&
      config.strategy !== "traditional" ? (
        <div className="shrink-0 border-b border-[#E2C36E] bg-[#FFF8E5] px-3 py-1.5 text-[10px] text-[#775500]">
          출력 JSON Schema가 유효하지 않아 preview를 실행할 수 없습니다.
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <DocumentPreviewCanvas
            blocks={blocks}
            sourceUrl={sourceUrl}
            selectedBlockId={selectedBlockId}
            onSelectBlock={setSelectedBlockId}
            viewMode={isCitationMode ? "verified-citation" : "lab"}
            citationState={citationState}
          />
          <DocumentResultDock
            run={run}
            blocks={blocks}
            selectedBlockId={selectedBlockId}
            onSelectBlock={setSelectedBlockId}
            viewMode={isCitationMode ? "verified-citation" : "lab"}
            citationState={citationState}
            citationResolution={citationResolution}
            comparisonResults={comparisonResults}
            comparisonSourceVersionId={config.mediaItemVersionId}
            activeComparisonRunId={run?.id ?? null}
            comparisonFocusToken={comparisonFocusToken}
            onSelectComparison={handleSelectComparison}
          />
        </div>
        {isCitationMode ? (
          <CitationEvidencePanel
            resolution={citationResolution}
            isLoading={isResolvingCitation}
            hasError={citationError !== null}
          />
        ) : (
          <DocumentConfigPanel
            config={config}
            processors={processors}
            isLoadingProcessors={isLoadingCatalog}
            isRunning={isRunning}
            isComparing={isComparing}
            isPromoting={isPromoting}
            canRun={canRun}
            canCompare={canCompare}
            canPromote={canPromote}
            comparisonCount={comparisonResults.length}
            promotionTargets={promotionTargets}
            selectedPromotionTargetId={selectedPromotionTargetId}
            selectedProfileLabel={
              selectedProfile && run
                ? `${selectedProfile.strategy}/${selectedProfile.extractionMode} · ${run.id}`
                : null
            }
            selectedProfileRuntimeNote={
              selectedProfile && selectedProfile.strategy !== "traditional"
                ? "Extraction, chunk, Use LLM 노드와 preview에서 확인한 model revision·prompt·schema를 함께 고정합니다. Alias가 다른 model/revision으로 이동하면 build는 provider 호출 전에 중단됩니다."
                : null
            }
            promotionMessage={promotionMessage}
            onChange={(next) => {
              setConfig(next);
              setRun(null);
              setComparisonResults([]);
              setPromotionMessage(null);
            }}
            onRun={handleRun}
            onCompare={handleCompare}
            onPromotionTargetChange={setSelectedPromotionTargetId}
            onPromote={handlePromote}
          />
        )}
      </div>
    </div>
  );
}

function DocumentIntelligenceHeader({
  run,
  runHistoryCount,
  branchId,
  citationResolution,
  citationState,
  isCitationMode,
  comparisonCount,
  isComparing,
  canCompare,
  onCompare,
}: {
  run: PipelinePreviewRun | null;
  runHistoryCount: number;
  branchId: string | null;
  citationResolution: CitationNavigationResolution | null;
  citationState: CitationVerificationState;
  isCitationMode: boolean;
  comparisonCount: number;
  isComparing: boolean;
  canCompare: boolean;
  onCompare: () => void;
}) {
  const status = isCitationMode
    ? citationHeaderStatus(citationState)
    : String(run?.status ?? "READY").toUpperCase();
  const citationPresentation = citationHeaderPresentation(citationState);
  return (
    <header className="flex h-12 shrink-0 items-center border-b border-[#B8C0CC] bg-[#F6F7F9] px-2">
      <Link
        to={isCitationMode ? "/aip" : "/pipelines"}
        aria-label={
          isCitationMode ? "AIP로 돌아가기" : "Pipeline Builder로 돌아가기"
        }
        className="mr-2 flex size-7 items-center justify-center rounded-[2px] hover:bg-[#E5E8EB]"
      >
        <ArrowLeft className="size-4" />
      </Link>
      <div
        className={`flex size-7 items-center justify-center rounded-[3px] text-white ${
          isCitationMode
            ? citationPresentation.iconClassName
            : "bg-[#137CBD]"
        }`}
      >
        {isCitationMode ? (
          <ShieldCheck className="size-4" />
        ) : (
          <FlaskConical className="size-4" />
        )}
      </div>
      <div className="ml-2 min-w-0">
        <div className="flex items-center gap-1.5">
          <h1 className="truncate text-[12px] font-semibold">
            {isCitationMode
              ? "Verified citation"
              : "Document Intelligence Lab"}
          </h1>
          <span
            className={`rounded px-1.5 py-0.5 text-[8px] font-medium ${
              isCitationMode
                ? citationPresentation.badgeClassName
                : "bg-[#E1F3F8] text-[#0E5A78]"
            }`}
            data-testid={
              isCitationMode ? "citation-header-evidence-status" : undefined
            }
          >
            {isCitationMode
              ? citationPresentation.badgeLabel
              : "experiment"}
          </span>
        </div>
        <p className="truncate font-mono text-[8px] text-[#738091]">
          {isCitationMode
            ? citationResolution
              ? `${citationResolution.sourceResourceId} · ${citationResolution.evidence.contentUnitId}`
              : "signed navigation reference"
            : branchId
              ? `scratch branch ${branchId}`
              : "scratch branch on first run"}
        </p>
      </div>
      {!isCitationMode ? (
        <div className="ml-6 flex items-center gap-1">
          <HeaderButton
            icon={<Save className="size-3" />}
            label="Save experiment"
            disabled
          />
          <HeaderButton
            icon={<GitCompareArrows className="size-3" />}
            label={isComparing ? "Comparing..." : "Compare strategies"}
            badge={comparisonCount || runHistoryCount}
            disabled={!canCompare || isComparing}
            title="같은 committed PDF로 Raw, OCR, Layout, VLM no-commit preview를 비교합니다."
            onClick={onCompare}
          />
          <HeaderButton
            icon={<History className="size-3" />}
            label="History"
            badge={runHistoryCount}
            disabled
            title="Run history browsing is a follow-up surface."
          />
        </div>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        <span
          className={`flex items-center gap-1 rounded px-1.5 py-1 text-[9px] ${
            isCitationMode
              ? citationPresentation.badgeClassName
              : "bg-[#E7F6EC] text-[#0F6B3E]"
          }`}
        >
          {citationState === "resolving" ? (
            <LoaderCircle className="size-3 animate-spin" />
          ) : citationState === "failed" ? (
            <ShieldAlert className="size-3" />
          ) : (
            <ShieldCheck className="size-3" />
          )}
          {isCitationMode
            ? citationState === "verified"
              ? "version + hash verified"
              : citationState === "failed"
                ? "verification blocked"
                : "server verification"
            : "governed preview"}
        </span>
        <span className="flex items-center gap-1 rounded border border-[#C8CED6] bg-white px-1.5 py-1 font-mono text-[9px]">
          {status === "SUCCEEDED" || status === "VERIFIED" ? (
            <CheckCircle2 className="size-3 text-[#0F9960]" />
          ) : null}
          {status}
        </span>
      </div>
    </header>
  );
}

function citationHeaderPresentation(state: CitationVerificationState): {
  badgeLabel: string;
  badgeClassName: string;
  iconClassName: string;
} {
  if (state === "verified") {
    return {
      badgeLabel: "verified committed evidence",
      badgeClassName: "bg-[#E7F6EC] text-[#0F6B3E]",
      iconClassName: "bg-[#0F9960]",
    };
  }
  if (state === "failed") {
    return {
      badgeLabel: "verification blocked",
      badgeClassName: "bg-[#FDECEC] text-[#A82A2A]",
      iconClassName: "bg-[#C23030]",
    };
  }
  return {
    badgeLabel: "verifying evidence",
    badgeClassName: "bg-[#E1F3F8] text-[#0E5A78]",
    iconClassName: "bg-[#137CBD]",
  };
}

function HeaderButton({
  icon,
  label,
  badge,
  disabled,
  title,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  badge?: number;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      disabled={disabled}
      className="h-7 rounded-[2px] px-2 text-[9px]"
      title={title ?? label}
      onClick={onClick}
    >
      {icon}
      {label}
      {badge !== undefined ? (
        <span className="rounded bg-[#E5E8EB] px-1 font-mono text-[8px]">
          {badge}
        </span>
      ) : null}
    </Button>
  );
}

function citationHeaderStatus(state: CitationVerificationState): string {
  switch (state) {
    case "resolving":
      return "VERIFYING";
    case "verified":
      return "VERIFIED";
    case "failed":
      return "FAILED";
    default:
      return "READY";
  }
}

function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status.toUpperCase());
}

function isSuccessfulRun(
  run: PipelinePreviewRun | null,
): run is PipelinePreviewRun {
  if (!run) return false;
  const status = run.status.toUpperCase();
  return status === "SUCCEEDED" || status === "PARTIAL";
}

async function waitForPreviewRun(
  getRun: (previewRunId: string) => Promise<PipelinePreviewRun>,
  created: PipelinePreviewRun,
): Promise<PipelinePreviewRun> {
  let current = created;
  for (let attempt = 0; attempt < 175; attempt += 1) {
    if (isTerminal(current.status)) return current;
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, 400);
    });
    current = await getRun(current.id);
  }
  throw new Error(`Preview run ${created.id} did not finish within 70 seconds.`);
}

function updateComparisonResult(
  results: readonly DocumentComparisonResult[],
  id: DocumentComparisonResult["id"],
  next: DocumentComparisonResult,
): DocumentComparisonResult[] {
  return results.map((result) => (result.id === id ? next : result));
}

function comparisonErrorMessage(value: unknown): string {
  return normalizeFoundryLiteError(value).message;
}

function previewRunErrorMessage(value: unknown): string {
  if (typeof value === "string" && value) return value;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return "typed runtime error";
  }
  const error = value as Record<string, unknown>;
  const message = error.message;
  const type = error.type;
  if (typeof message === "string" && message) {
    return typeof type === "string" && type ? `${type}: ${message}` : message;
  }
  return JSON.stringify(error);
}
