import type {
  SourceManagedSync,
  SourceManagedSyncRun,
  SourceSchedulerDecision,
  SourceSchedulerTickResult,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { CalendarClock, ExternalLink, Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { SyncNetworkEvidenceCard } from "./detail/SyncNetworkEvidenceCard";
import { StreamingSyncTelemetry } from "./detail/StreamingSyncTelemetry";
import { readSchedule, scheduleSummary } from "./detail/sync-config";
import {
  formatTimestamp,
  readSyncRunRowCount,
  readTextField,
  sourceTypeLabel,
  statusIntent,
  statusLabel,
  toOperationsHref,
} from "./source-model";
import { useManagedSyncs, useSyncRuns } from "./use-source-queries";

const SYNC_COLUMNS: readonly DataTableColumn<SourceManagedSync>[] = [
  {
    key: "syncName",
    header: "동기화",
    isMono: true,
    render: (sync) => sync.syncName,
  },
  {
    key: "sourceType",
    header: "소스 타입",
    render: (sync) => sourceTypeLabel(sync.sourceType),
  },
  {
    key: "capability",
    header: "capability",
    render: (sync) => sync.capability,
  },
  { key: "mode", header: "모드", isMono: true, render: (sync) => sync.mode },
  {
    key: "schedule",
    header: "스케줄",
    render: (sync) => (
      <span className="flex items-center gap-1">
        <CalendarClock className="size-3 text-muted-foreground" />
        {scheduleSummary(readSchedule(sync))}
      </span>
    ),
  },
  {
    key: "dataset",
    header: "대상 데이터셋",
    isMono: true,
    render: (sync) => sync.targetDatasetRef ?? "—",
  },
  {
    key: "status",
    header: "상태",
    render: (sync) => (
      <StatusPill intent={statusIntent(sync.status)}>
        {statusLabel(sync.status)}
      </StatusPill>
    ),
  },
];

interface SyncsPanelProps {
  initialSyncName?: string | null;
}

/** 관리형 동기화 탭: sync 목록 + 선택 sync의 run 시작/run 증거 목록. */
export function SyncsPanel({ initialSyncName = null }: SyncsPanelProps) {
  const syncsQuery = useManagedSyncs();
  const [selectedSyncName, setSelectedSyncName] = useState<string | null>(
    initialSyncName,
  );
  const [runRefreshToken, setRunRefreshToken] = useState(0);

  useEffect(() => {
    if (initialSyncName) setSelectedSyncName(initialSyncName);
  }, [initialSyncName]);

  const syncs = syncsQuery.data ?? [];
  const selectedSync =
    syncs.find((sync) => sync.syncName === selectedSyncName) ??
    syncs[0] ??
    null;

  if (syncsQuery.error) {
    return (
      <div className="p-4">
        <ErrorState
          error={syncsQuery.error}
          onRetry={() => void syncsQuery.reload()}
        />
      </div>
    );
  }
  if (syncsQuery.isLoading && !syncsQuery.data) {
    return <LoadingState rowCount={6} className="p-4" />;
  }
  if (syncs.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          title="관리형 동기화가 없습니다"
          description="새 소스 위저드에서 REST API, Postgres 또는 Kafka 타입 소스를 만들면 반복 sync가 여기에 나타납니다."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <SourceSchedulerPanel
        onSchedulerTicked={() => {
          setRunRefreshToken((value) => value + 1);
          void syncsQuery.reload();
        }}
      />
      <div>
        <div className="section-label mb-2">관리형 동기화 ({syncs.length})</div>
        <DataTable
          columns={SYNC_COLUMNS}
          rows={syncs}
          rowKey={(sync) => sync.syncName}
          selectedKey={selectedSync?.syncName ?? null}
          onRowClick={(sync) => setSelectedSyncName(sync.syncName)}
        />
      </div>
      {selectedSync ? (
        <SyncRunsSection
          key={selectedSync.syncName}
          sync={selectedSync}
          refreshToken={runRefreshToken}
          onSyncMutated={() => void syncsQuery.reload()}
        />
      ) : null}
    </div>
  );
}

function SourceSchedulerPanel({
  onSchedulerTicked,
}: {
  onSchedulerTicked: () => void;
}) {
  const client = useFoundryLiteClient();
  const dueQuery = useFoundryLiteQuery(
    ["data-connection", "source-scheduler", "due"],
    () => client.sources.scheduler.previewDue({ maxRuns: 50 }),
  );
  const tick = useFoundryLiteMutation<SourceSchedulerTickResult, undefined>(
    () => client.sources.scheduler.tick({ maxRuns: 50 }),
    {
      onSuccess: () => {
        void dueQuery.reload();
        onSchedulerTicked();
      },
    },
  );

  // previewDue is the current scheduler truth. The tick payload remains useful
  // as execution evidence, but must not overwrite a newer post-tick preview.
  const schedulerStatus = dueQuery.data;
  const dueItems = schedulerStatus?.due ?? [];
  const skippedItems = tick.result?.skipped ?? [];
  const startedItems = tick.result?.started ?? [];

  return (
    <div className="rounded border bg-card p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="section-label">Source scheduler</span>
            {schedulerStatus ? (
              <StatusPill intent={dueItems.length > 0 ? "info" : "neutral"}>
                due {dueItems.length}건
              </StatusPill>
            ) : null}
            {startedItems.length > 0 ? (
              <StatusPill intent="success">
                started {startedItems.length}건
              </StatusPill>
            ) : null}
          </div>
          <div className="mt-1 font-mono text-[11px] text-muted-foreground">
            evaluatedAt={schedulerStatus?.evaluatedAt ?? "—"}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void dueQuery.reload()}
            disabled={dueQuery.isRefreshing}
          >
            <RefreshCw className="size-3.5" /> previewDue 새로고침
          </Button>
          <Button
            size="sm"
            onClick={() => void tick.execute(undefined)}
            disabled={tick.isRunning}
          >
            <Play className="size-3.5" />
            {tick.isRunning ? "tick 실행 중..." : "scheduler tick 실행"}
          </Button>
        </div>
      </div>

      {dueQuery.error ? (
        <ErrorState
          error={dueQuery.error}
          onRetry={() => void dueQuery.reload()}
        />
      ) : null}
      {tick.error ? (
        <ErrorState
          error={tick.error}
          onRetry={() => void tick.execute(undefined)}
        />
      ) : null}

      <div className="mt-3 grid gap-2 font-mono text-[11px] md:grid-cols-4">
        <SchedulerMetric
          label="evaluated"
          value={schedulerStatus ? String(schedulerStatus.evaluated) : "—"}
        />
        <SchedulerMetric label="due" value={String(dueItems.length)} />
        <SchedulerMetric label="started" value={String(startedItems.length)} />
        <SchedulerMetric label="skipped" value={String(skippedItems.length)} />
      </div>

      {dueItems.length > 0 ? (
        <div className="mt-3 space-y-1">
          <div className="section-label">due decisions</div>
          <div className="grid gap-1 md:grid-cols-2">
            {dueItems.slice(0, 4).map((decision) => (
              <SchedulerDecisionCard
                key={`${decision.syncName}:${decision.idempotencyKey ?? "no-key"}`}
                decision={decision}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="mt-3 rounded border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
          현재 due인 managed sync가 없습니다.
        </div>
      )}

      {startedItems.length > 0 ? (
        <div className="mt-3 rounded border border-success/30 bg-success/5 px-3 py-2 font-mono text-[11px]">
          <div className="section-label mb-1">tick started runs</div>
          <div className="space-y-0.5">
            {startedItems.slice(0, 4).map((item, index) => (
              <div key={`${readStartedRunField(item, "runId") ?? index}`}>
                sync={readStartedSyncName(item) ?? "—"} · run=
                {readStartedRunField(item, "runId") ?? "—"} · status=
                {readStartedRunField(item, "status") ?? "—"} · trigger=
                {readStartedRunField(item, "triggerType") ?? "—"}
              </div>
            ))}
          </div>
          {tick.requestId ? (
            <div className="mt-1 text-muted-foreground">
              request_id={tick.requestId}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SchedulerMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded border bg-muted/30 px-2 py-1.5">
      <div className="text-muted-foreground">{label}</div>
      <div>{value}</div>
    </div>
  );
}

function SchedulerDecisionCard({
  decision,
}: {
  decision: SourceSchedulerDecision;
}) {
  return (
    <div className="rounded border bg-muted/30 px-2 py-1.5 font-mono text-[11px]">
      <div className="truncate">sync={decision.syncName}</div>
      <div className="text-muted-foreground">reason={decision.reason}</div>
      <div className="text-muted-foreground">
        slot={decision.slotStart ?? "—"} · next={decision.nextDueAt ?? "—"}
      </div>
    </div>
  );
}

function readStartedSyncName(item: Record<string, unknown>): string | null {
  return (
    readStartedRunField(item, "syncName") ??
    readStartedDecisionField(item, "syncName")
  );
}

function readStartedRunField(
  item: Record<string, unknown>,
  field: string,
): string | null {
  const run = readRecord(item["run"]);
  const value = run?.[field] ?? item[field];
  return typeof value === "string" ? value : null;
}

function readStartedDecisionField(
  item: Record<string, unknown>,
  field: string,
): string | null {
  const decision = readRecord(item["decision"]);
  const value = decision?.[field];
  return typeof value === "string" ? value : null;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

const RUN_COLUMNS: readonly DataTableColumn<SourceManagedSyncRun>[] = [
  {
    key: "runId",
    header: "run id",
    isMono: true,
    className: "max-w-64 truncate",
    render: (run) => run.runId,
  },
  {
    key: "status",
    header: "상태",
    render: (run) => (
      <StatusPill intent={statusIntent(run.status)}>
        {statusLabel(run.status)}
      </StatusPill>
    ),
  },
  { key: "trigger", header: "트리거", render: (run) => run.triggerType },
  {
    key: "startedAt",
    header: "시작",
    isMono: true,
    render: (run) => formatTimestamp(run.startedAt),
  },
  {
    key: "completedAt",
    header: "완료",
    isMono: true,
    render: (run) => formatTimestamp(run.completedAt),
  },
  {
    key: "rows",
    header: "rows",
    isMono: true,
    render: (run) => readSyncRunRowCount(run.resultSummary) ?? "—",
  },
  {
    key: "evidence",
    header: "증거",
    render: (run) => {
      const href = toOperationsHref(run.operationsPath);
      return href ? (
        <Link
          to={href}
          onClick={(event) => event.stopPropagation()}
          className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
        >
          <ExternalLink className="size-3" /> 운영
        </Link>
      ) : (
        "—"
      );
    },
  },
];

function SyncRunsSection({
  sync,
  refreshToken,
  onSyncMutated,
}: {
  sync: SourceManagedSync;
  refreshToken: number;
  onSyncMutated: () => void;
}) {
  const client = useFoundryLiteClient();
  const runsQuery = useSyncRuns(sync.syncName);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [lastRunKey, setLastRunKey] = useState<string | null>(null);
  const startRun = useFoundryLiteMutation(
    ({ syncName, key }: { syncName: string; key: string }) =>
      client.sources.managedSyncs.startRun(
        syncName,
        { triggerType: "manual" },
        { idempotencyKey: key },
      ),
    { lockKey: ({ syncName }) => `sources:managed-sync:start:${syncName}` },
  );
  const reloadRuns = runsQuery.reload;

  useEffect(() => {
    if (refreshToken === 0) return;
    void reloadRuns();
  }, [refreshToken, reloadRuns]);

  const handleStartRun = async () => {
    const key = idempotencyKey("source_sync_run", sync.syncName);
    setLastRunKey(key);
    const run = await startRun.execute({ syncName: sync.syncName, key });
    if (run) setSelectedRunId(run.runId);
    await runsQuery.reload();
    onSyncMutated();
  };

  const runs = runsQuery.data ?? [];
  const selectedRun =
    runs.find((run) => run.runId === selectedRunId) ?? runs[0] ?? null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="section-label">
          run 증거 — <span className="font-mono">{sync.syncName}</span>
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void runsQuery.reload()}
            disabled={runsQuery.isRefreshing}
          >
            <RefreshCw className="size-3.5" /> 새로고침
          </Button>
          <Button
            size="sm"
            onClick={() => void handleStartRun()}
            disabled={startRun.isRunning}
          >
            <Play className="size-3.5" /> run 시작
          </Button>
        </div>
      </div>
      {lastRunKey ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          idempotency-key={lastRunKey}
          {startRun.result ? (
            <>
              {" "}
              · run={startRun.result.runId} ·{" "}
              <StatusPill intent={statusIntent(startRun.result.status)}>
                {statusLabel(startRun.result.status)}
              </StatusPill>
            </>
          ) : null}
        </div>
      ) : null}
      {startRun.error ? (
        <ErrorState
          error={startRun.error}
          onRetry={() => void handleStartRun()}
        />
      ) : null}
      {runsQuery.error ? (
        <ErrorState
          error={runsQuery.error}
          onRetry={() => void runsQuery.reload()}
        />
      ) : runsQuery.isLoading && !runsQuery.data ? (
        <LoadingState rowCount={3} />
      ) : (
        <DataTable
          columns={RUN_COLUMNS}
          rows={runs}
          rowKey={(run) => run.runId}
          selectedKey={selectedRun?.runId ?? null}
          onRowClick={(run) => setSelectedRunId(run.runId)}
          emptyMessage="아직 run이 없습니다. 'run 시작'으로 첫 실행 증거를 만드세요."
        />
      )}
      <StreamingSyncTelemetry sync={sync} latestRun={runs[0] ?? null} />
      {selectedRun ? <RunDetailCard run={selectedRun} /> : null}
    </div>
  );
}

function RunDetailCard({ run }: { run: SourceManagedSyncRun }) {
  const operationsHref = toOperationsHref(run.operationsPath);
  const errorPayload = runErrorPayload(run);
  const errorMessage = errorPayload ? runErrorMessage(errorPayload) : null;

  return (
    <div className="rounded border bg-card p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="section-label">선택한 run 상세</span>
        <StatusPill intent={statusIntent(run.status)}>
          {statusLabel(run.status)}
        </StatusPill>
        {operationsHref ? (
          <Link
            to={operationsHref}
            className="ml-auto inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
          >
            <ExternalLink className="size-3" /> 운영 증거 보기
          </Link>
        ) : null}
      </div>
      <dl className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
        <RunDetailRow label="run id" value={run.runId} />
        <RunDetailRow label="workflow run" value={run.workflowRunId ?? "—"} />
        <RunDetailRow
          label="dataset version"
          value={run.datasetVersionId ?? "—"}
        />
        <RunDetailRow
          label="row count"
          value={String(readSyncRunRowCount(run.resultSummary) ?? "—")}
        />
        <RunDetailRow label="시작" value={formatTimestamp(run.startedAt)} />
        <RunDetailRow label="완료" value={formatTimestamp(run.completedAt)} />
      </dl>
      {run.networkEvidence ? (
        <div className="-mx-3 mt-3 -mb-3 overflow-hidden rounded-b">
          <SyncNetworkEvidenceCard evidence={run.networkEvidence} />
        </div>
      ) : null}
      {errorMessage ? (
        <div className="mt-2 rounded border border-destructive/30 bg-destructive/5 p-2">
          <div className="text-[11px] font-semibold text-destructive">
            실패 원인
          </div>
          <div className="mt-0.5 font-mono text-[11px] text-foreground/80">
            {errorMessage}
          </div>
          {errorPayload ? (
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              {JSON.stringify(errorPayload["details"] ?? errorPayload)}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function runErrorPayload(
  run: SourceManagedSyncRun,
): Record<string, unknown> | null {
  if (run.error) return run.error;
  const workflowRun = run.resultSummary["workflowRun"];
  if (!workflowRun || typeof workflowRun !== "object") return null;
  const error = (workflowRun as Record<string, unknown>)["error"];
  return error && typeof error === "object"
    ? (error as Record<string, unknown>)
    : null;
}

function runErrorMessage(error: Record<string, unknown>): string {
  return (
    readTextField(error, "message") ??
    readTextField(error, "operatorMessage") ??
    readTextField(error, "type") ??
    readTextField(error, "code") ??
    "실패 원인 상세를 확인하세요"
  );
}

function RunDetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-3 border-b border-border/60 py-1 last:border-b-0">
      <dt className="w-28 shrink-0 text-[11px] text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 truncate font-mono text-[11px]">{value}</dd>
    </div>
  );
}
