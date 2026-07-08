import type { FoundryLiteDatasetExplorerState } from "@foundry-lite/sdk/react";
import type { DatasetQualityResultHistoryItem } from "@foundry-lite/sdk";
import { Activity } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { runTypeFromRunId } from "@/features/lineage/lineage-model";

interface DataHealthTabProps {
  explorer: FoundryLiteDatasetExplorerState;
  onSelectRun: (run: SelectedRunRef) => void;
}

function statusIntent(status: string): StatusIntent {
  const normalized = status.toUpperCase();
  if (normalized === "PASS") return "success";
  if (normalized === "FAIL" || normalized === "ERROR") return "danger";
  if (normalized === "WARN" || normalized === "WARNING") return "warning";
  return "neutral";
}

/** 하단 데이터 상태 탭: 품질 결과 요약 + 최근 검증 결과 (와이드 뷰). */
export function DataHealthTab({ explorer, onSelectRun }: DataHealthTabProps) {
  if (!explorer.hasDatasetSelection) {
    return (
      <EmptyState
        icon={Activity}
        title="데이터셋을 선택하세요"
        description="그래프에서 데이터셋 노드를 선택하면 품질 상태가 표시됩니다."
      />
    );
  }
  if (explorer.isLoading) return <LoadingState rowCount={4} />;
  if (explorer.error) {
    return (
      <ErrorState
        error={explorer.error}
        onRetry={() => void explorer.reload()}
      />
    );
  }

  const summary = explorer.qualitySummary;
  if (!summary || summary.totalResults === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="품질 결과가 없습니다"
        description="품질 계약 체크가 실행되면 결과가 여기에 기록됩니다."
      />
    );
  }

  const columns: DataTableColumn<DatasetQualityResultHistoryItem>[] = [
    {
      key: "checkType",
      header: "체크 유형",
      isMono: true,
      render: (result) => result.checkType,
    },
    {
      key: "status",
      header: "상태",
      render: (result) => (
        <StatusPill intent={statusIntent(result.status)}>
          {result.status}
        </StatusPill>
      ),
    },
    {
      key: "severity",
      header: "심각도",
      render: (result) => (
        <StatusPill intent={result.severity === "error" ? "danger" : "warning"}>
          {result.severity}
        </StatusPill>
      ),
    },
    {
      key: "runId",
      header: "검증 run",
      isMono: true,
      className: "max-w-72 truncate",
      render: (result) => (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onSelectRun({
              runType: runTypeFromRunId(result.runId),
              runId: result.runId,
            });
          }}
          className="truncate text-primary hover:underline"
        >
          {result.runId}
        </button>
      ),
    },
    {
      key: "schemaVersion",
      header: "스키마 버전",
      isMono: true,
      render: (result) => `v${result.validatedAgainstSchemaVersion}`,
    },
    {
      key: "createdAt",
      header: "검증 시각",
      isMono: true,
      render: (result) =>
        new Date(result.createdAt).toLocaleString("ko-KR", { hour12: false }),
    },
  ];

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[12px] font-semibold">
          {summary.datasetRef}
        </span>
        <span className="text-[11px] text-muted-foreground">
          검증 결과 {summary.totalResults}건
        </span>
        {summary.statusCounts.map((statusCount) => (
          <StatusPill
            key={statusCount.status}
            intent={statusIntent(statusCount.status)}
          >
            {statusCount.status} {statusCount.count}
          </StatusPill>
        ))}
        {summary.checkTypeStatusCounts.map((typeCount) => (
          <span
            key={`${typeCount.checkType}-${typeCount.status}`}
            className="font-mono text-[10px] text-muted-foreground"
          >
            {typeCount.checkType}:{typeCount.count}
          </span>
        ))}
      </div>
      <DataTable
        className="min-h-0 flex-1"
        columns={columns}
        rows={summary.latestResults}
        rowKey={(result) => result.id}
        emptyMessage="최근 검증 결과가 없습니다."
      />
    </div>
  );
}
