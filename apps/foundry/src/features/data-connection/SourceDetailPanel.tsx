import type {
  SourceConnection,
  SourceDebeziumObjectIndexResult,
  SourceDebeziumOperationPlan,
  SourceOperationResult,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import {
  ChevronRight,
  Copy,
  Database,
  ExternalLink,
  FileSearch,
  Play,
  Plus,
  Radio,
  RotateCw,
  Terminal,
  Table2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { SourceSyncsTab } from "./detail/SourceSyncsTab";
import {
  formatTimestamp,
  readNumberField,
  readTextField,
  sourceTypeLabel,
  statusIntent,
  statusLabel,
  toDatasetHref,
  toOperationsHref,
} from "./source-model";
import { SourceExploreTab } from "./SourceExploreTab";
import { useDebeziumOperationPlan } from "./use-source-queries";

/** 탐색을 지원하는 소스 타입 (백엔드 exploration.run 구현 범위). */
const EXPLORABLE_SOURCE_TYPES = new Set(["postgres_jdbc", "rest_api"]);

type DetailTabId = "overview" | "settings" | "syncs" | "explore" | "cdcPlan";

interface SourceDetailPanelProps {
  source: SourceConnection;
  onSyncCreated?: (syncName: string) => void;
}

/**
 * 소스 상세 (Palantir 구조): breadcrumb + 개요/연결 설정/동기화/소스 탐색 탭
 * + 우상단 기본 작업(탐색 및 동기화 생성 | 동기화 생성).
 */
export function SourceDetailPanel({
  source,
  onSyncCreated,
}: SourceDetailPanelProps) {
  const [activeTab, setActiveTab] = useState<DetailTabId>("overview");
  const [shouldStartCreatingSync, setShouldStartCreatingSync] = useState(false);
  const [currentSource, setCurrentSource] = useState(source);
  useEffect(() => setCurrentSource(source), [source]);

  const datasetHref = toDatasetHref(currentSource.targetDatasetRef);
  const operationsHref = toOperationsHref(currentSource.operationsPath);
  const isExplorable = EXPLORABLE_SOURCE_TYPES.has(currentSource.kind);
  const isCdcSource = currentSource.kind === "debezium_cdc";
  const effectiveActiveTab =
    (activeTab === "explore" && !isExplorable) ||
    (activeTab === "cdcPlan" && !isCdcSource)
      ? "overview"
      : activeTab;

  const handlePrimaryAction = () => {
    if (isExplorable) {
      setActiveTab("explore");
      return;
    }
    if (isCdcSource) {
      setActiveTab("cdcPlan");
      return;
    }
    setShouldStartCreatingSync(true);
    setActiveTab("syncs");
  };

  const handleSelectTab = (tab: DetailTabId) => {
    if (tab !== "syncs") setShouldStartCreatingSync(false);
    setActiveTab(tab);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b px-4 py-2 text-xs text-muted-foreground">
        <Database className="size-3.5" />
        <span>Data Connection</span>
        <ChevronRight className="size-3" />
        <span>{sourceTypeLabel(currentSource.kind)}</span>
        <ChevronRight className="size-3" />
        <span className="min-w-0 break-words font-medium text-foreground">
          {currentSource.displayName}
        </span>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <DetailTab
            isActive={effectiveActiveTab === "overview"}
            onClick={() => handleSelectTab("overview")}
          >
            개요
          </DetailTab>
          <DetailTab
            isActive={effectiveActiveTab === "settings"}
            onClick={() => handleSelectTab("settings")}
          >
            연결 설정
          </DetailTab>
          <DetailTab
            isActive={effectiveActiveTab === "syncs"}
            onClick={() => handleSelectTab("syncs")}
          >
            동기화
          </DetailTab>
          {isCdcSource ? (
            <DetailTab
              isActive={effectiveActiveTab === "cdcPlan"}
              onClick={() => handleSelectTab("cdcPlan")}
            >
              CDC 운영
            </DetailTab>
          ) : null}
          {isExplorable ? (
            <DetailTab
              isActive={effectiveActiveTab === "explore"}
              onClick={() => handleSelectTab("explore")}
            >
              소스 탐색
            </DetailTab>
          ) : null}
        </div>
        <Button size="sm" className="my-1" onClick={handlePrimaryAction}>
          {isExplorable ? (
            <>
              <FileSearch className="size-3.5" /> 탐색 및 동기화 생성
            </>
          ) : isCdcSource ? (
            <>
              <Radio className="size-3.5" /> CDC 운영 계획
            </>
          ) : (
            <>
              <Plus className="size-3.5" /> 동기화 생성
            </>
          )}
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {effectiveActiveTab === "overview" ? (
          <OverviewTab
            source={currentSource}
            datasetHref={datasetHref}
            operationsHref={operationsHref}
          />
        ) : null}
        {effectiveActiveTab === "settings" ? <ConfigTab source={currentSource} /> : null}
        {effectiveActiveTab === "syncs" ? (
          <SourceSyncsTab
            key={`${currentSource.sourceName}:${shouldStartCreatingSync}`}
            source={currentSource}
            shouldStartCreating={shouldStartCreatingSync}
          />
        ) : null}
        {effectiveActiveTab === "cdcPlan" && isCdcSource ? (
          <CdcOperationPlanTab
            source={currentSource}
            onSourceUpdated={setCurrentSource}
          />
        ) : null}
        {effectiveActiveTab === "explore" && isExplorable ? (
          <SourceExploreTab source={currentSource} onSyncCreated={onSyncCreated} />
        ) : null}
      </div>
    </div>
  );
}

function CdcOperationPlanTab({
  source,
  onSourceUpdated,
}: {
  source: SourceConnection;
  onSourceUpdated: (source: SourceConnection) => void;
}) {
  const [objectTypeApiName, setObjectTypeApiName] = useState("Order");
  const selectedObjectTypeApiName = objectTypeApiName.trim() || "Order";
  const planQuery = useDebeziumOperationPlan(
    source.sourceName,
    selectedObjectTypeApiName,
  );
  const plan = planQuery.data;

  if (planQuery.error) {
    return <ErrorState error={planQuery.error} onRetry={() => void planQuery.reload()} />;
  }
  if (planQuery.isLoading || !plan) {
    return <LoadingState rowCount={6} className="mx-auto max-w-3xl" />;
  }

  return (
    <CdcOperationPlanView
      plan={plan}
      objectTypeApiName={objectTypeApiName}
      onObjectTypeApiNameChange={setObjectTypeApiName}
      onSourceUpdated={onSourceUpdated}
    />
  );
}

function CdcOperationPlanView({
  plan,
  objectTypeApiName,
  onObjectTypeApiNameChange,
  onSourceUpdated,
}: {
  plan: SourceDebeziumOperationPlan;
  objectTypeApiName: string;
  onObjectTypeApiNameChange: (value: string) => void;
  onSourceUpdated: (source: SourceConnection) => void;
}) {
  const readiness = plan.readiness;
  const sync = plan.sync;
  const objectIndexing = plan.objectIndexing;
  const status = readTextField(readiness, "status") ?? "unknown";
  const streamArchive = plan.workerCommands.streamArchive;
  const objectIndexer = plan.workerCommands.objectIndexer;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="rounded border bg-card">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            <Radio className="size-4 text-primary" />
            CDC operation plan
          </div>
          <StatusPill intent={cdcReadinessIntent(status)}>
            {cdcReadinessLabel(status)}
          </StatusPill>
        </div>
        <dl className="divide-y divide-border/60 px-3 py-1">
          <PropertyRow label="source">
            <span className="font-mono text-[11px]">{plan.source.sourceName}</span>
          </PropertyRow>
          <PropertyRow label="dataset">
            <span className="font-mono text-[11px]">
              {readTextField(sync, "datasetRef") ?? "—"}
            </span>
          </PropertyRow>
          <PropertyRow label="topic">
            <span className="font-mono text-[11px]">
              {readTextField(sync, "topic") ?? "—"}
            </span>
          </PropertyRow>
          <PropertyRow label="consumer group">
            <span className="font-mono text-[11px]">
              {readTextField(sync, "consumerGroup") ?? "—"}
            </span>
          </PropertyRow>
          <PropertyRow label="object type">
            <div className="flex w-full max-w-sm items-center gap-2">
              <Input
                aria-label="CDC object type"
                value={objectTypeApiName}
                onChange={(event) => onObjectTypeApiNameChange(event.target.value)}
                placeholder="Order"
                className="h-7 max-w-48 font-mono text-[11px]"
              />
              <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">
                plan={readTextField(objectIndexing, "objectTypeApiName") ?? "—"}
              </span>
            </div>
          </PropertyRow>
        </dl>
      </div>
      <div className="rounded border bg-card">
        <div className="section-label border-b px-3 py-2">Worker commands</div>
        <div className="space-y-3 p-3">
          <CommandBlock label="stream archive" value={streamArchive} />
          <CommandBlock label="object indexer" value={objectIndexer} />
        </div>
      </div>
      <CdcArchiveRunPanel plan={plan} onSourceUpdated={onSourceUpdated} />
      <CdcObjectIndexReplayPanel plan={plan} />
      <div className="rounded border bg-card">
        <div className="section-label border-b px-3 py-2">Operator checklist</div>
        <div className="divide-y divide-border/60 px-3">
          {plan.operatorChecklist.map((item) => (
            <div key={String(item.key)} className="flex items-center gap-3 py-2">
              <StatusPill intent={checklistIntent(readTextField(item, "status"))}>
                {checklistLabel(readTextField(item, "status"))}
              </StatusPill>
              <span className="text-xs">{readTextField(item, "label") ?? "—"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CdcObjectIndexReplayPanel({
  plan,
}: {
  plan: SourceDebeziumOperationPlan;
}) {
  const client = useFoundryLiteClient();
  const sourceName = plan.source.sourceName;
  const sync = plan.sync;
  const objectTypeApiName = readTextField(
    plan.objectIndexing,
    "objectTypeApiName",
  );
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<SourceDebeziumObjectIndexResult | null>(
    null,
  );
  const [error, setError] = useState<unknown>(null);
  const expectedConfigFingerprint = readTextField(
    sync,
    "expectedConfigFingerprint",
  );
  const planStatus = plan.objectIndexingStatus ?? null;
  const statusBacklog = result?.backlog ?? planStatus?.backlog ?? null;
  const statusNextAction = result?.nextAction ?? planStatus?.nextAction ?? null;
  const statusVersionId =
    result?.sourceDatasetVersionId ?? planStatus?.lastIndexedVersionId ?? null;
  const statusVersionNumber =
    result?.sourceDatasetVersionNumber ??
    planStatus?.lastIndexedVersionNumber ??
    null;
  const indexRunId = result?.indexRunId ?? planStatus?.lastIndexRunId ?? null;
  const operationsPath =
    result?.operationsPath ?? (indexRunId ? `/api/operations/runs/index/${indexRunId}` : null);
  const operationsHref = toOperationsHref(operationsPath);
  const workflowOperationPath =
    result?.workflowOperationPath ?? planStatus?.workflowOperationPath ?? null;
  const workflowHref = toOperationsHref(workflowOperationPath);

  const handleReplayIndex = async () => {
    if (!objectTypeApiName || !sourceName) return;
    const key = idempotencyKey(
      "source_debezium_object_index",
      crypto.randomUUID(),
    );
    setIsRunning(true);
    setError(null);
    try {
      const nextResult = await client.sources.cdc.debezium.startObjectIndex(
        sourceName,
        {
          objectTypeApiName,
          expectedConfigFingerprint,
          maxRowsPerVersion: 100,
        },
        { idempotencyKey: key },
      );
      setResult(nextResult);
      toast.success("CDC object index tick을 실행했습니다");
    } catch (nextError) {
      setError(nextError);
      toast.error("CDC object index tick에 실패했습니다");
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="rounded border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <div>
          <div className="section-label">CDC object index</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            다음 CDC dataset version을 객체 인덱스에 한 번 적용합니다.
          </div>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={!objectTypeApiName || isRunning}
          onClick={() => void handleReplayIndex()}
        >
          {isRunning ? (
            <>
              <RotateCw className="size-3.5 animate-spin" /> 실행 중
            </>
          ) : (
            <>
              <RotateCw className="size-3.5" /> CDC Object index 적용
            </>
          )}
        </Button>
      </div>
      <dl className="divide-y divide-border/60 px-3 py-1">
        <PropertyRow label="object type">
          <span className="font-mono text-[11px]">
            {objectTypeApiName ?? "—"}
          </span>
        </PropertyRow>
        <PropertyRow label="source dataset">
          <span className="break-all font-mono text-[11px]">
            {result?.sourceDatasetRef ??
              readTextField(plan.sync, "datasetRef") ??
              "—"}
          </span>
        </PropertyRow>
        <PropertyRow label="source version">
          {statusVersionId ? (
            <span className="break-all font-mono text-[11px]">
              version={statusVersionNumber ?? "—"} · id={statusVersionId}
            </span>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground">
              —
            </span>
          )}
        </PropertyRow>
        <PropertyRow label="index run">
          {operationsHref && operationsPath ? (
            <Link
              to={operationsHref}
              className="font-mono text-[11px] text-primary hover:underline"
            >
              {operationsPath}
            </Link>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground">
              —
            </span>
          )}
        </PropertyRow>
        <PropertyRow label="workflow cursor">
          {workflowHref && workflowOperationPath ? (
            <Link
              to={workflowHref}
              className="font-mono text-[11px] text-primary hover:underline"
            >
              {workflowOperationPath}
            </Link>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground">
              —
            </span>
          )}
        </PropertyRow>
        <PropertyRow label="status">
          {result ? (
            <StatusPill intent={result.status === "INDEXED" ? "success" : "neutral"}>
              {result.status === "INDEXED" ? "indexed" : "no versions"}
            </StatusPill>
          ) : (
            <StatusPill intent={statusVersionNumber ? "success" : "neutral"}>
              {statusVersionNumber ? "indexed" : "no index cursor"}
            </StatusPill>
          )}
        </PropertyRow>
        <PropertyRow label="events / upserted / deleted / skipped">
          <span className="font-mono text-[11px]">
            {result
              ? `${result.eventCount} / ${result.objectsUpserted} / ${result.objectsDeleted} / ${result.eventsSkipped}`
              : "—"}
          </span>
        </PropertyRow>
        <PropertyRow label="backlog">
          <span className="font-mono text-[11px]">
            {statusBacklog
              ? `remaining=${statusBacklog.remainingVersionCount} latest=${statusBacklog.latestSourceDatasetVersionNumber ?? "—"} next=${statusBacklog.nextSourceDatasetVersionNumber ?? "—"}`
              : "—"}
          </span>
        </PropertyRow>
        <PropertyRow label="next action">
          {statusNextAction ? (
            <StatusPill intent={cdcNextActionIntent(statusNextAction)}>
              {cdcNextActionLabel(statusNextAction)}
            </StatusPill>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground">
              —
            </span>
          )}
        </PropertyRow>
      </dl>
      {error ? <ErrorState error={error} className="border-t" /> : null}
    </div>
  );
}

function CdcArchiveRunPanel({
  plan,
  onSourceUpdated,
}: {
  plan: SourceDebeziumOperationPlan;
  onSourceUpdated: (source: SourceConnection) => void;
}) {
  const client = useFoundryLiteClient();
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<SourceOperationResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [lastIdempotencyKey, setLastIdempotencyKey] = useState<string | null>(
    null,
  );
  const sourceName = plan.source.sourceName;
  const sync = plan.sync;
  const canStartArchive =
    plan.readiness.canStartArchiveFromBrowser === true &&
    sourceName.length > 0;
  const expectedConfigFingerprint = readTextField(
    sync,
    "expectedConfigFingerprint",
  );
  const testStatus = readTextField(result?.testResult, "status");
  const operationsHref = toOperationsHref(
    result?.operationsPath ?? plan.source.operationsPath,
  );
  const rowCount =
    readNumberField(result?.commitResult, "rowCount") ??
    result?.commitResults.reduce<number | null>((current, commit) => {
      if (current !== null) return current;
      return readNumberField(commit, "rowCount");
    }, null) ??
    null;

  const handleStartArchive = async () => {
    const key = idempotencyKey(
      "source_debezium_archive_run",
      crypto.randomUUID(),
    );
    setIsRunning(true);
    setError(null);
    setLastIdempotencyKey(key);
    try {
      const nextResult = await client.sources.cdc.debezium.startSync(
        sourceName,
        {
          expectedConfigFingerprint,
          limit: 10,
        },
        { idempotencyKey: key },
      );
      setResult(nextResult);
      onSourceUpdated(nextResult.source);
      toast.success("CDC archive run을 시작했습니다");
    } catch (nextError) {
      setError(nextError);
      toast.error("CDC archive run 시작에 실패했습니다");
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="rounded border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <div>
          <div className="section-label">Browser action</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            화면에서 백엔드 CDC archive sync를 한 번 실행하고 증거를 확인합니다.
          </div>
        </div>
        <Button
          size="sm"
          disabled={!canStartArchive || isRunning}
          onClick={() => void handleStartArchive()}
        >
          {isRunning ? (
            <>
              <RotateCw className="size-3.5 animate-spin" /> 실행 중
            </>
          ) : (
            <>
              <Play className="size-3.5" /> Archive run 시작
            </>
          )}
        </Button>
      </div>
      <dl className="divide-y divide-border/60 px-3 py-1">
        <PropertyRow label="예상 지문">
          <span className="font-mono text-[11px]">
            {expectedConfigFingerprint ?? "—"}
          </span>
        </PropertyRow>
        <PropertyRow label="실행 상태">
          {testStatus ? (
            <span className="flex items-center gap-1.5">
              <StatusPill intent={testStatus === "no_events" ? "neutral" : "info"}>
                {testStatus === "no_events" ? "이벤트 없음" : testStatus}
              </StatusPill>
              <span className="text-[11px] text-muted-foreground">
                limit=10
              </span>
            </span>
          ) : (
            <span className="text-[11px] text-muted-foreground">
              아직 실행 전
            </span>
          )}
        </PropertyRow>
        <PropertyRow label="row count">
          <span className="font-mono text-[11px]">{rowCount ?? "—"}</span>
        </PropertyRow>
        <PropertyRow label="멱등 키">
          <span className="font-mono text-[11px]">
            {lastIdempotencyKey ?? "—"}
          </span>
        </PropertyRow>
        <PropertyRow label="operations">
          {operationsHref ? (
            <Link
              to={operationsHref}
              className="font-mono text-[11px] text-primary hover:underline"
            >
              {result?.operationsPath ?? plan.source.operationsPath}
            </Link>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground">
              —
            </span>
          )}
        </PropertyRow>
      </dl>
      {error ? <ErrorState error={error} className="border-t" /> : null}
    </div>
  );
}

function CommandBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
        <Terminal className="size-3" />
        {label}
      </div>
      <div className="rounded border bg-muted/40 p-2">
        <CopyableValue value={value} />
      </div>
    </div>
  );
}

function cdcReadinessIntent(status: string) {
  if (status === "ready_for_cdc_workers") return "success";
  if (status === "needs_primary_key") return "warning";
  return "neutral";
}

function cdcReadinessLabel(status: string) {
  if (status === "ready_for_cdc_workers") return "worker ready";
  if (status === "needs_primary_key") return "primary key 필요";
  return status;
}

function cdcNextActionIntent(action: string) {
  if (action === "monitor_operations") return "success";
  if (action === "run_object_index_again") return "warning";
  if (action === "run_stream_archive") return "info";
  return "neutral";
}

function cdcNextActionLabel(action: string) {
  if (action === "monitor_operations") return "caught up";
  if (action === "run_object_index_again") return "index again";
  if (action === "run_stream_archive") return "archive first";
  if (action === "wait_for_next_cdc_version") return "waiting";
  return action;
}

function checklistIntent(status: string | null) {
  if (status === "ready") return "success";
  if (status === "required") return "warning";
  if (status === "worker_required") return "info";
  return "neutral";
}

function checklistLabel(status: string | null) {
  if (status === "external_required") return "external";
  if (status === "worker_required") return "worker";
  if (status === "required") return "required";
  return status ?? "check";
}

function DetailTab({
  isActive,
  onClick,
  children,
}: {
  isActive: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "-mb-px border-b-2 py-2 text-xs font-medium",
        isActive
          ? "border-primary text-primary"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function OverviewTab({
  source,
  datasetHref,
  operationsHref,
}: {
  source: SourceConnection;
  datasetHref: string | null;
  operationsHref: string | null;
}) {
  const commit = source.lastCommitRef;
  const hasCommit = commit && Object.keys(commit).length > 0;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center gap-3">
        <span className="flex size-12 items-center justify-center rounded bg-primary/10">
          <Database className="size-6 text-primary" />
        </span>
        <div>
          <div className="text-base font-semibold">{source.displayName}</div>
          <div className="text-xs text-muted-foreground">
            {sourceTypeLabel(source.kind)} ·{" "}
            <span className="font-mono">{source.sourceName}</span>
          </div>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-[1fr_240px]">
        <div className="rounded border bg-card">
          <div className="border-b px-3 py-2 text-[13px] font-semibold">
            속성
          </div>
          <dl className="divide-y divide-border/60 px-3 py-1">
            <PropertyRow label="출력 데이터셋">
              {datasetHref ? (
                <Link
                  to={datasetHref}
                  className="font-mono text-[11px] text-primary hover:underline"
                >
                  {source.targetDatasetRef}
                </Link>
              ) : (
                <span className="text-[11px] text-muted-foreground">—</span>
              )}
            </PropertyRow>
            <PropertyRow label="상태">
              <StatusPill intent={statusIntent(source.status)}>
                {statusLabel(source.status)}
              </StatusPill>
            </PropertyRow>
            <PropertyRow label="구성 지문">
              <CopyableValue value={source.configFingerprint} />
            </PropertyRow>
            <PropertyRow label="마지막 run">
              {operationsHref && source.lastRunId ? (
                <Link
                  to={operationsHref}
                  className="font-mono text-[11px] text-primary hover:underline"
                >
                  {source.lastRunId}
                </Link>
              ) : (
                <span className="font-mono text-[11px] text-muted-foreground">
                  {source.lastRunId ?? "—"}
                </span>
              )}
            </PropertyRow>
            <PropertyRow label="생성">
              <span className="font-mono text-[11px]">
                {formatTimestamp(source.createdAt)}
              </span>
            </PropertyRow>
            <PropertyRow label="수정">
              <span className="font-mono text-[11px]">
                {formatTimestamp(source.updatedAt)}
              </span>
            </PropertyRow>
          </dl>
        </div>
        <div className="h-fit rounded border bg-card">
          <div className="border-b px-3 py-2 text-[13px] font-semibold">
            액션
          </div>
          <div className="space-y-2 p-3">
            {datasetHref ? (
              <Button asChild size="sm" className="w-full">
                <Link to={datasetHref}>
                  <Table2 className="size-3.5" /> 데이터셋 열기
                </Link>
              </Button>
            ) : null}
            {operationsHref ? (
              <Button asChild variant="outline" size="sm" className="w-full">
                <Link to={operationsHref}>
                  <ExternalLink className="size-3.5" /> 운영 증거 보기
                </Link>
              </Button>
            ) : null}
            <div className="flex items-center justify-between">
              <Button variant="outline" size="sm" disabled className="flex-1">
                소스 비활성화
              </Button>
              <StatusPill intent="neutral" className="ml-2">
                future
              </StatusPill>
            </div>
          </div>
        </div>
      </div>
      {hasCommit ? (
        <div className="rounded border bg-card">
          <div className="section-label border-b px-3 py-2">
            마지막 커밋 증거
          </div>
          <dl className="divide-y divide-border/60 px-3 py-1">
            <PropertyRow label="row count">
              <span className="font-mono text-[11px]">
                {readNumberField(commit, "rowCount") ?? "—"}
              </span>
            </PropertyRow>
            <PropertyRow label="버전">
              <span className="font-mono text-[11px]">
                v{readNumberField(commit, "versionNumber") ?? "?"} ·{" "}
                {readTextField(commit, "versionId") ?? "—"}
              </span>
            </PropertyRow>
            <PropertyRow label="transaction">
              <span className="font-mono text-[11px]">
                {readTextField(commit, "transactionId") ?? "—"}
              </span>
            </PropertyRow>
            <PropertyRow label="schema hash">
              <span className="truncate font-mono text-[11px]">
                {readTextField(commit, "schemaHash") ?? "—"}
              </span>
            </PropertyRow>
            <PropertyRow label="run id">
              <span className="font-mono text-[11px]">
                {readTextField(commit, "runId") ?? "—"}
              </span>
            </PropertyRow>
          </dl>
        </div>
      ) : null}
    </div>
  );
}

function ConfigTab({ source }: { source: SourceConnection }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="rounded border bg-card">
        <div className="section-label border-b px-3 py-2">구성 요약</div>
        <pre className="overflow-x-auto p-3 font-mono text-[11px] leading-5">
          {JSON.stringify(source.configSummary, null, 2)}
        </pre>
      </div>
      <div className="rounded border bg-card">
        <div className="section-label border-b px-3 py-2">구성 지문</div>
        <div className="p-3">
          <CopyableValue value={source.configFingerprint} />
        </div>
      </div>
    </div>
  );
}

function PropertyRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <dt className="w-28 shrink-0 text-[11px] text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}

function CopyableValue({ value }: { value: string }) {
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("클립보드에 복사했습니다");
    } catch {
      toast.error("복사에 실패했습니다");
    }
  };
  return (
    <span className="flex items-center gap-1">
      <span className="truncate font-mono text-[11px]">{value}</span>
      <Button
        variant="ghost"
        size="icon"
        className="size-5 shrink-0"
        onClick={() => void handleCopy()}
        aria-label="값 복사"
      >
        <Copy className="size-3" />
      </Button>
    </span>
  );
}
