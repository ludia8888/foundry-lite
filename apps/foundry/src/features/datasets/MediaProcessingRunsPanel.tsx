import type { MediaProcessingRun } from "@foundry-lite/sdk";
import { Cog } from "lucide-react";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";

import { formatTimestamp } from "./dataset-schema";
import type { ScreenQueryState } from "./use-screen-query";

function readText(run: MediaProcessingRun, key: string): string {
  const value = run[key];
  return typeof value === "string" ? value : "-";
}

function runStatusIntent(status: string): StatusIntent {
  if (status === "SUCCEEDED") return "success";
  if (status === "FAILED") return "danger";
  if (status === "RUNNING") return "info";
  return "neutral";
}

const RUN_COLUMNS: readonly DataTableColumn<MediaProcessingRun>[] = [
  {
    key: "runId",
    header: "Run ID",
    isMono: true,
    className: "max-w-56 truncate",
    render: (run) => {
      const runId = readText(run, "media_processing_run_id");
      return <span title={runId}>{runId}</span>;
    },
  },
  {
    key: "processor",
    header: "프로세서",
    isMono: true,
    render: (run) => readText(run, "processor_name"),
  },
  {
    key: "status",
    header: "상태",
    render: (run) => {
      const status = readText(run, "status");
      return <StatusPill intent={runStatusIntent(status)}>{status}</StatusPill>;
    },
  },
  {
    key: "derivative",
    header: "파생 ID",
    isMono: true,
    className: "max-w-56 truncate",
    render: (run) => {
      const derivativeId = readText(run, "media_derivative_id");
      return <span title={derivativeId}>{derivativeId}</span>;
    },
  },
  {
    key: "failure",
    header: "실패 사유",
    className: "max-w-64 truncate",
    render: (run) => {
      const reason = readText(run, "failure_reason");
      return reason === "-" ? (
        "-"
      ) : (
        <span className="text-destructive" title={reason}>
          {reason}
        </span>
      );
    },
  },
  {
    key: "finishedAt",
    header: "종료 시각",
    isMono: true,
    render: (run) => {
      const finishedAt = run.finished_at;
      return formatTimestamp(
        typeof finishedAt === "string" ? finishedAt : null,
      );
    },
  },
];

interface MediaProcessingRunsPanelProps {
  runsQuery: ScreenQueryState<MediaProcessingRun[]>;
}

/** 미디어 처리 실행 이력: run id / 상태 / 파생 evidence를 숨기지 않고 노출한다. */
export function MediaProcessingRunsPanel({
  runsQuery,
}: MediaProcessingRunsPanelProps) {
  const runs = runsQuery.data ?? [];

  return (
    <section className="space-y-2 rounded border bg-card p-3">
      <div className="flex items-center gap-2">
        <Cog className="size-4 text-primary" />
        <span className="section-label">처리 실행 이력</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {runs.length}건
        </span>
      </div>
      {runsQuery.isLoading && runs.length === 0 ? (
        <LoadingState rowCount={3} />
      ) : runsQuery.error ? (
        <ErrorState error={runsQuery.error} onRetry={runsQuery.reload} />
      ) : (
        <DataTable
          columns={RUN_COLUMNS}
          rows={runs}
          rowKey={(run) => readText(run, "media_processing_run_id")}
          emptyMessage="아직 처리 실행이 없습니다. 위 파이프라인을 실행하면 이력이 기록됩니다."
        />
      )}
    </section>
  );
}
