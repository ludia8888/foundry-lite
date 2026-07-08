import type { Dataset } from "@foundry-lite/sdk";
import type {
  FoundryLiteOperationsRunListState,
  FoundryLiteOperationsRunRow,
} from "@foundry-lite/sdk/react";
import { useMemo, useState } from "react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  RUN_PHASE_INTENTS,
  RUN_PHASE_LABELS,
} from "@/features/lineage/RunEvidencePanel";
import {
  readRunTargetLabel,
  readRunTimestamp,
} from "@/features/lineage/lineage-model";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";

interface HistoryTabProps {
  runList: FoundryLiteOperationsRunListState;
  datasets: Dataset[];
  selectedRun: SelectedRunRef | null;
  onSelectRun: (run: SelectedRunRef) => void;
}

type PhaseFilter = "all" | "failed" | "running" | "succeeded";

const PHASE_FILTER_LABELS: Record<PhaseFilter, string> = {
  all: "전체 상태",
  failed: "실패만",
  running: "실행 중만",
  succeeded: "성공만",
};

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", { hour12: false });
}

/** 하단 히스토리 탭: run history 테이블 — 행 클릭 시 우측 run evidence와 연결. */
export function HistoryTab({
  runList,
  datasets,
  selectedRun,
  onSelectRun,
}: HistoryTabProps) {
  const [phaseFilter, setPhaseFilter] = useState<PhaseFilter>("all");

  const datasetById = useMemo(
    () => new Map(datasets.map((dataset) => [dataset.id, dataset])),
    [datasets],
  );

  const filteredRuns = useMemo(() => {
    if (phaseFilter === "all") return runList.runRows;
    if (phaseFilter === "failed") return runList.failedRuns;
    if (phaseFilter === "running")
      return [...runList.runningRuns, ...runList.pendingRuns];
    return runList.succeededRuns;
  }, [phaseFilter, runList]);

  const columns: DataTableColumn<FoundryLiteOperationsRunRow>[] = [
    {
      key: "type",
      header: "유형",
      render: (run) => (
        <span className="font-mono text-[11px]">{run.runType}</span>
      ),
    },
    {
      key: "runId",
      header: "Run ID",
      isMono: true,
      className: "max-w-72 truncate",
      render: (run) => run.runId ?? "—",
    },
    {
      key: "target",
      header: "대상 리소스",
      isMono: true,
      render: (run) => readRunTargetLabel(run, datasetById),
    },
    {
      key: "status",
      header: "상태",
      render: (run) => (
        <StatusPill intent={RUN_PHASE_INTENTS[run.phase] ?? "neutral"}>
          {RUN_PHASE_LABELS[run.phase] ?? run.phase}
        </StatusPill>
      ),
    },
    {
      key: "started",
      header: "시작",
      isMono: true,
      render: (run) => formatTimestamp(readRunTimestamp(run, "created_at")),
    },
    {
      key: "completed",
      header: "완료",
      isMono: true,
      render: (run) => formatTimestamp(readRunTimestamp(run, "completed_at")),
    },
  ];

  if (runList.isLoading) return <LoadingState rowCount={5} />;
  if (runList.error) {
    return (
      <ErrorState error={runList.error} onRetry={() => void runList.reload()} />
    );
  }
  if (!runList.hasRuns) {
    return (
      <EmptyState
        title="run 이력이 없습니다"
        description="파이프라인에서 빌드를 실행하면 run 이력이 여기에 기록됩니다."
      />
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center gap-2">
        <Select
          value={phaseFilter}
          onValueChange={(value) => setPhaseFilter(value as PhaseFilter)}
        >
          <SelectTrigger size="sm" className="h-7 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(PHASE_FILTER_LABELS).map(([value, label]) => (
              <SelectItem key={value} value={value} className="text-xs">
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-[11px] text-muted-foreground">
          총 {runList.runRows.length}건 · 실패 {runList.failedRuns.length}건 ·
          실행 중 {runList.runningRuns.length}건
        </span>
      </div>
      <DataTable
        className="min-h-0 flex-1"
        columns={columns}
        rows={filteredRuns}
        rowKey={(run) => `${run.runType}-${run.runId}`}
        selectedKey={
          selectedRun ? `${selectedRun.runType}-${selectedRun.runId}` : null
        }
        onRowClick={(run) =>
          run.runId
            ? onSelectRun({ runType: String(run.runType), runId: run.runId })
            : undefined
        }
        emptyMessage="조건에 맞는 run이 없습니다."
      />
    </div>
  );
}
