import type { SourceManagedSyncRun } from "@foundry-lite/sdk";
import { ListFilter } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import {
  formatTimestamp,
  readSyncRunRowCount,
  statusIntent,
  statusLabel,
  toOperationsHref,
} from "../source-model";
import { SyncRunEvidencePanel } from "./SyncRunEvidencePanel";

type RunFilter = "all" | "failed";

interface SyncRunHistoryProps {
  runs: readonly SourceManagedSyncRun[];
  isLoading: boolean;
  error: unknown;
  selectedRunId: string | null;
  onSelectRun: (runId: string | null) => void;
  onReload: () => void;
}

interface SyncRunRow {
  runId: string;
  status: string;
  triggerType: string;
  rowCount: string;
  duration: string;
  datasetVersionId: string | null;
  errorSummary: string;
  startedAt: string;
  completedAt: string | null;
  operationsPath: string | null;
}

/** 실행 이력 필터와 선택 run의 durable evidence를 함께 제공한다. */
export function SyncRunHistory({
  runs,
  isLoading,
  error,
  selectedRunId,
  onSelectRun,
  onReload,
}: SyncRunHistoryProps) {
  const [filter, setFilter] = useState<RunFilter>("all");
  const failedCount = runs.filter((run) => run.status === "failed").length;
  const visibleRuns = filter === "failed" ? runs.filter((run) => run.status === "failed") : runs;
  const rows = visibleRuns.map(runRow);
  const selectedRun = runs.find((run) => run.runId === selectedRunId) ?? null;

  return (
    <section aria-label="동기화 실행 이력">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="section-label">실행 이력</div>
        <div className="flex items-center gap-1" aria-label="실행 이력 필터">
          <ListFilter className="mr-1 size-3.5 text-muted-foreground" />
          <Button
            variant={filter === "all" ? "secondary" : "ghost"}
            size="sm"
            className="h-6 px-2 text-[10px]"
            onClick={() => setFilter("all")}
          >
            전체 {runs.length}
          </Button>
          <Button
            variant={filter === "failed" ? "secondary" : "ghost"}
            size="sm"
            className="h-6 px-2 text-[10px]"
            onClick={() => setFilter("failed")}
          >
            실패 {failedCount}
          </Button>
        </div>
      </div>
      {isLoading ? (
        <LoadingState rowCount={3} />
      ) : error ? (
        <ErrorState error={error} onRetry={onReload} />
      ) : (
        <DataTable
          columns={RUN_COLUMNS}
          rows={rows}
          rowKey={(row) => row.runId}
          selectedKey={selectedRunId}
          onRowClick={(row) => onSelectRun(row.runId === selectedRunId ? null : row.runId)}
          emptyMessage={
            filter === "failed"
              ? "실패한 실행이 없습니다."
              : "아직 실행된 run이 없습니다. 우상단 빌드 버튼으로 첫 run을 시작하세요."
          }
        />
      )}
      {selectedRun ? <SyncRunEvidencePanel run={selectedRun} /> : null}
    </section>
  );
}

const RUN_COLUMNS: readonly DataTableColumn<SyncRunRow>[] = [
  {
    key: "runId",
    header: "run id",
    isMono: true,
    render: (row) => {
      const href = toOperationsHref(row.operationsPath);
      return href ? <Link to={href} className="text-primary hover:underline">{row.runId}</Link> : row.runId;
    },
  },
  {
    key: "status",
    header: "상태",
    render: (row) => <StatusPill intent={statusIntent(row.status)}>{statusLabel(row.status)}</StatusPill>,
  },
  { key: "trigger", header: "트리거", render: (row) => <span className="text-[11px]">{triggerLabel(row.triggerType)}</span> },
  { key: "rows", header: "행 수", isMono: true, render: (row) => row.rowCount },
  { key: "duration", header: "소요 시간", isMono: true, render: (row) => row.duration },
  { key: "version", header: "버전", isMono: true, render: (row) => abbreviated(row.datasetVersionId) },
  {
    key: "error",
    header: "오류",
    render: (row) => (
      <span className={row.errorSummary === "—" ? "text-muted-foreground" : "text-destructive"}>
        {row.errorSummary}
      </span>
    ),
  },
  { key: "started", header: "시작", isMono: true, render: (row) => formatTimestamp(row.startedAt) },
  { key: "completed", header: "완료", isMono: true, render: (row) => formatTimestamp(row.completedAt) },
];

function runRow(run: SourceManagedSyncRun): SyncRunRow {
  const rowCount = readSyncRunRowCount(run.resultSummary);
  return {
    runId: run.runId,
    status: run.status,
    triggerType: run.triggerType,
    rowCount: rowCount === null ? "—" : String(rowCount),
    duration: runDuration(run.startedAt, run.completedAt),
    datasetVersionId: run.datasetVersionId,
    errorSummary: runErrorSummary(run.error),
    startedAt: run.startedAt,
    completedAt: run.completedAt,
    operationsPath: run.operationsPath,
  };
}

function triggerLabel(triggerType: string): string {
  if (triggerType === "manual") return "수동";
  if (triggerType === "recovery") return "복구";
  return "예약";
}

function runDuration(startedAt: string, completedAt: string | null): string {
  if (!completedAt) return "실행 중";
  const duration = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(duration) || duration < 0) return "—";
  if (duration < 1_000) return `${duration}ms`;
  if (duration < 60_000) return `${(duration / 1_000).toFixed(1)}s`;
  return `${Math.floor(duration / 60_000)}m ${Math.floor((duration % 60_000) / 1_000)}s`;
}

function abbreviated(value: string | null): string {
  if (!value) return "—";
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function runErrorSummary(error: Record<string, unknown> | null): string {
  if (!error) return "—";
  const message = error.message ?? error.code ?? error.error;
  return typeof message === "string" ? message : "상세 보기";
}
