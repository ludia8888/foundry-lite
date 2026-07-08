import type { SourceManagedSync } from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import {
  CalendarClock,
  ChevronDown,
  Database,
  FileSearch,
  Play,
  Table2,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import {
  formatTimestamp,
  readSyncRunRowCount,
  statusIntent,
  statusLabel,
  toDatasetHref,
  toOperationsHref,
} from "../source-model";
import { useSyncRuns } from "../use-source-queries";
import {
  readRemainingConfig,
  readSchedule,
  readSyncIncremental,
  readSyncPreQuery,
  readSyncQuery,
  scheduleSummary,
  TRANSACTION_MODES,
} from "./sync-config";

interface SyncRunRow {
  runId: string;
  status: string;
  triggerType: string;
  rowCount: string;
  startedAt: string;
  completedAt: string | null;
  operationsPath: string | null;
}

interface SyncDetailViewProps {
  sync: SourceManagedSync;
  onRunStarted?: () => void;
}

/**
 * 동기화 상세 (Palantir sync 페이지 구조):
 * 좌상단 출력 데이터셋 링크 + 우상단 빌드 버튼, 좌측 핵심 구성 사이드바,
 * 우측 소스별 구성(SQL 쿼리/Incremental 카드), 하단 run evidence.
 */
export function SyncDetailView({ sync, onRunStarted }: SyncDetailViewProps) {
  const client = useFoundryLiteClient();
  const runsQuery = useSyncRuns(sync.syncName);
  const [isBuildOptionsOpen, setIsBuildOptionsOpen] = useState(false);
  const [shouldLimitBatch, setShouldLimitBatch] = useState(false);
  const [batchLimitText, setBatchLimitText] = useState("100");

  const startRun = useFoundryLiteMutation(
    useCallback(
      (payload: { batchLimit: number | null }) =>
        client.sources.managedSyncs.startRun(
          sync.syncName,
          {
            triggerType: "manual",
            batchLimit: payload.batchLimit,
          },
          { idempotencyKey: idempotencyKey("sync-run", crypto.randomUUID()) },
        ),
      [client, sync.syncName],
    ),
    {
      onSuccess: () => {
        void runsQuery.reload();
        onRunStarted?.();
      },
    },
  );

  const handleBuild = () => {
    setIsBuildOptionsOpen(false);
    const parsed = Number.parseInt(batchLimitText, 10);
    void startRun.execute({
      batchLimit: shouldLimitBatch && Number.isFinite(parsed) ? parsed : null,
    });
  };

  const schedule = readSchedule(sync);
  const query = readSyncQuery(sync);
  const preQuery = readSyncPreQuery(sync);
  const incremental = readSyncIncremental(sync);
  const remainingConfig = readRemainingConfig(sync);
  const datasetHref = toDatasetHref(sync.targetDatasetRef);

  const runRows = useMemo<SyncRunRow[]>(
    () =>
      (runsQuery.data ?? []).map((run) => ({
        runId: run.runId,
        status: run.status,
        triggerType: run.triggerType,
        rowCount: readRowCount(run.resultSummary),
        startedAt: run.startedAt,
        completedAt: run.completedAt,
        operationsPath: run.operationsPath,
      })),
    [runsQuery.data],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {datasetHref ? (
            <Link
              to={datasetHref}
              className="flex items-center gap-1.5 text-[15px] font-semibold text-primary hover:underline"
            >
              <Table2 className="size-4" />
              {sync.targetDatasetRef}
            </Link>
          ) : (
            <div className="text-[15px] font-semibold">{sync.displayName}</div>
          )}
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="font-mono">{sync.syncName}</span>
            <StatusPill intent={statusIntent(sync.status)}>
              {statusLabel(sync.status)}
            </StatusPill>
            <span className="font-mono">
              fp {sync.configFingerprint.slice(0, 10)}
            </span>
          </div>
        </div>
        <div className="flex items-center">
          <Button
            size="sm"
            className="rounded-r-none bg-success text-success-foreground hover:bg-success/90"
            disabled={startRun.isRunning}
            onClick={handleBuild}
          >
            <Play className="size-3.5" />
            {startRun.isRunning ? "빌드 중..." : "빌드"}
          </Button>
          <Popover
            open={isBuildOptionsOpen}
            onOpenChange={setIsBuildOptionsOpen}
          >
            <PopoverTrigger asChild>
              <Button
                size="sm"
                aria-label="빌드 옵션"
                className="rounded-l-none border-l border-success-foreground/20 bg-success px-1.5 text-success-foreground hover:bg-success/90"
              >
                <ChevronDown className="size-3.5" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-64 p-3">
              <div className="section-label mb-2">빌드 옵션</div>
              <label className="flex items-center gap-2 text-xs">
                <Checkbox
                  checked={shouldLimitBatch}
                  onCheckedChange={(checked) =>
                    setShouldLimitBatch(checked === true)
                  }
                />
                배치 행 수 제한
              </label>
              {shouldLimitBatch ? (
                <Input
                  value={batchLimitText}
                  onChange={(event) => setBatchLimitText(event.target.value)}
                  className="mt-2 h-7 font-mono text-xs"
                  inputMode="numeric"
                />
              ) : null}
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {startRun.error ? (
        <ErrorState
          error={startRun.error}
          onRetry={() => void startRun.execute({ batchLimit: null })}
        />
      ) : null}
      {startRun.result ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-success/30 bg-success/5 px-3 py-2 font-mono text-[11px]">
          <span>run_id={startRun.result.runId}</span>
          <StatusPill intent={statusIntent(startRun.result.status)}>
            {statusLabel(startRun.result.status)}
          </StatusPill>
          {startRun.requestId ? (
            <span className="text-muted-foreground">
              request_id={startRun.requestId}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-3">
          <ConfigCard icon={Database} title="핵심 구성">
            <ConfigRow label="목적지">
              {datasetHref ? (
                <Link
                  to={datasetHref}
                  className="font-mono text-[11px] text-primary hover:underline"
                >
                  {sync.targetDatasetRef}
                </Link>
              ) : (
                <span className="font-mono text-[11px]">
                  {sync.targetMediaSetId ?? "—"}
                </span>
              )}
            </ConfigRow>
            <ConfigRow label="트랜잭션 유형">
              <div className="space-y-1">
                {TRANSACTION_MODES.map((mode) => (
                  <div key={mode.value} className="flex items-start gap-1.5">
                    <span
                      className={
                        sync.mode === mode.value
                          ? "mt-1 size-2 shrink-0 rounded-full bg-primary"
                          : "mt-1 size-2 shrink-0 rounded-full border border-muted-foreground/40"
                      }
                    />
                    <span
                      className={
                        sync.mode === mode.value
                          ? "text-[11px] font-medium"
                          : "text-[11px] text-muted-foreground"
                      }
                    >
                      {mode.label}
                      <span className="block text-[10px] font-normal text-muted-foreground">
                        {mode.description}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </ConfigRow>
            <ConfigRow label="용량">
              <span className="text-[11px]">{sync.capability}</span>
            </ConfigRow>
          </ConfigCard>
          <ConfigCard icon={CalendarClock} title="일정">
            <div className="text-[11px]">{scheduleSummary(schedule)}</div>
            {schedule.batchLimit ? (
              <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                batchLimit={schedule.batchLimit}
              </div>
            ) : null}
            {schedule.mode === "manual" ? (
              <div className="mt-1 text-[10px] text-muted-foreground">
                새로 만드는 동기화에는 일정을 설정하는 것이 좋습니다.
              </div>
            ) : null}
          </ConfigCard>
        </div>

        <div className="space-y-3">
          {query ? (
            <ConfigCard icon={FileSearch} title="SQL 쿼리">
              <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
                {query}
              </pre>
              {preQuery ? (
                <>
                  <div className="section-label mt-2 mb-1">선 쿼리</div>
                  <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
                    {preQuery}
                  </pre>
                </>
              ) : null}
            </ConfigCard>
          ) : null}
          {incremental ? (
            <ConfigCard
              icon={Database}
              title="Incremental"
              meta={<StatusPill intent="success">활성</StatusPill>}
            >
              <p className="mb-2 text-[11px] text-muted-foreground">
                단조 증가 컬럼 기준으로 마지막 값 이후의 행만 추가로 가져옵니다.
              </p>
              <ConfigRow label="컬럼">
                <span className="font-mono text-[11px]">
                  {incremental.column}
                </span>
              </ConfigRow>
              <ConfigRow label="마지막 값">
                <span className="font-mono text-[11px]">
                  {incremental.initialValue || "— (첫 실행 전)"}
                </span>
              </ConfigRow>
            </ConfigCard>
          ) : null}
          {Object.keys(remainingConfig).length > 0 ? (
            <ConfigCard icon={Database} title="소스별 구성">
              <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
                {JSON.stringify(remainingConfig, null, 2)}
              </pre>
            </ConfigCard>
          ) : null}
          <div className="rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            동기화 구성 수정은 백엔드가 구성 지문 불일치를 거부하므로 새
            동기화로 생성해야 합니다.{" "}
            <StatusPill intent="neutral">future</StatusPill>
          </div>
        </div>
      </div>

      <div>
        <div className="section-label mb-2">실행 이력</div>
        {runsQuery.isLoading ? (
          <LoadingState rowCount={3} />
        ) : runsQuery.error ? (
          <ErrorState
            error={runsQuery.error}
            onRetry={() => void runsQuery.reload()}
          />
        ) : (
          <DataTable
            columns={RUN_COLUMNS}
            rows={runRows}
            rowKey={(row) => row.runId}
            emptyMessage="아직 실행된 run이 없습니다. 우상단 빌드 버튼으로 첫 run을 시작하세요."
          />
        )}
      </div>
    </div>
  );
}

const RUN_COLUMNS: readonly DataTableColumn<SyncRunRow>[] = [
  {
    key: "runId",
    header: "run id",
    isMono: true,
    render: (row) => {
      const href = toOperationsHref(row.operationsPath);
      return href ? (
        <Link to={href} className="text-primary hover:underline">
          {row.runId}
        </Link>
      ) : (
        row.runId
      );
    },
  },
  {
    key: "status",
    header: "상태",
    render: (row) => (
      <StatusPill intent={statusIntent(row.status)}>
        {statusLabel(row.status)}
      </StatusPill>
    ),
  },
  {
    key: "trigger",
    header: "트리거",
    render: (row) => (
      <span className="text-[11px]">
        {row.triggerType === "manual" ? "수동" : "예약"}
      </span>
    ),
  },
  {
    key: "rows",
    header: "행 수",
    isMono: true,
    render: (row) => row.rowCount,
  },
  {
    key: "started",
    header: "시작",
    isMono: true,
    render: (row) => formatTimestamp(row.startedAt),
  },
  {
    key: "completed",
    header: "완료",
    isMono: true,
    render: (row) => formatTimestamp(row.completedAt),
  },
];

function readRowCount(summary: Record<string, unknown> | null): string {
  const value = readSyncRunRowCount(summary);
  return value === null ? "—" : String(value);
}

function ConfigCard({
  icon: Icon,
  title,
  meta,
  children,
}: {
  icon: typeof Database;
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="flex items-center gap-1.5 text-[13px] font-semibold">
          <Icon className="size-3.5 text-muted-foreground" />
          {title}
        </span>
        {meta}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

function ConfigRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 py-1">
      <span className="w-20 shrink-0 pt-0.5 text-[10px] tracking-[0.5px] text-muted-foreground uppercase">
        {label}
      </span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}
