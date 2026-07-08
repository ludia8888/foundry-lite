import type { DatasetQualityContractCheckList } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  type FoundryLiteDatasetExplorerState,
  type FoundryLiteQueryState,
} from "@foundry-lite/sdk/react";
import { MousePointerClick, ShieldCheck } from "lucide-react";
import { useCallback, useMemo } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import type { LineageGraphNode } from "@/features/lineage/lineage-model";
import type { RightPanelTab } from "@/features/lineage/LineageRightPanel";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { runTypeFromRunId } from "@/features/lineage/lineage-model";
import { useLineageQuery } from "@/features/lineage/use-lineage-query";

interface QualityPanelProps {
  node: LineageGraphNode | null;
  explorer: FoundryLiteDatasetExplorerState;
  onSelectRun: (run: SelectedRunRef | null) => void;
  onChangeTab: (tab: RightPanelTab) => void;
}

function qualityStatusIntent(status: string): StatusIntent {
  const normalized = status.toUpperCase();
  if (normalized === "PASS") return "success";
  if (normalized === "FAIL" || normalized === "ERROR") return "danger";
  if (normalized === "WARN" || normalized === "WARNING") return "warning";
  return "neutral";
}

/** datasets.qualityChecks.list — 선택 데이터셋의 품질 계약 체크 목록. */
function useDatasetQualityChecks(
  node: LineageGraphNode | null,
): FoundryLiteQueryState<DatasetQualityContractCheckList> {
  const client = useFoundryLiteClient();
  const namespace = node?.kind === "dataset" ? node.namespace : null;
  const name = node?.kind === "dataset" ? node.name : null;
  const key = useMemo(
    () => ["lineage", "quality-checks", namespace, name] as const,
    [namespace, name],
  );
  const load = useCallback(() => {
    if (!namespace || !name) {
      throw new Error("품질 체크를 조회하려면 데이터셋 선택이 필요합니다");
    }
    return client.datasets.qualityChecks.list(namespace, name);
  }, [client, namespace, name]);
  return useLineageQuery<DatasetQualityContractCheckList>(key, load, {
    enabled: Boolean(namespace && name),
  });
}

/** 선택 데이터셋의 품질 체크/결과 패널 — 품질 실패는 retry 대상이 아님을 구분(acceptance). */
export function QualityPanel({
  node,
  explorer,
  onSelectRun,
  onChangeTab,
}: QualityPanelProps) {
  const checksQuery = useDatasetQualityChecks(node);

  if (!node || node.kind !== "dataset") {
    return (
      <div className="p-3">
        <EmptyState
          icon={MousePointerClick}
          title="데이터셋 노드를 선택하세요"
          description="데이터셋 노드를 선택하면 품질 계약 체크와 최근 검증 결과가 표시됩니다."
        />
      </div>
    );
  }
  if (explorer.isLoading) {
    return (
      <div className="p-3">
        <LoadingState rowCount={5} />
      </div>
    );
  }
  if (explorer.error) {
    return (
      <div className="p-3">
        <ErrorState
          error={explorer.error}
          onRetry={() => void explorer.reload()}
        />
      </div>
    );
  }

  const checks = checksQuery.data?.checks ?? [];
  const summary = explorer.qualitySummary;
  const failedCount =
    summary?.statusCounts.find(
      (statusCount) => statusCount.status.toUpperCase() !== "PASS",
    )?.count ?? 0;

  return (
    <div className="space-y-4 p-3">
      <div className="space-y-1.5">
        <div className="section-label flex items-center gap-1">
          <ShieldCheck className="size-3" />
          품질 요약 — {node.label}
        </div>
        {summary ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[11px]">
              검증 결과 {summary.totalResults}건
            </span>
            {summary.statusCounts.map((statusCount) => (
              <StatusPill
                key={statusCount.status}
                intent={qualityStatusIntent(statusCount.status)}
              >
                {statusCount.status} {statusCount.count}
              </StatusPill>
            ))}
          </div>
        ) : (
          <div className="text-[11px] text-muted-foreground">
            아직 기록된 품질 결과가 없습니다.
          </div>
        )}
        {failedCount > 0 ? (
          <div className="rounded border border-destructive/40 bg-destructive/5 p-2 text-[11px]">
            품질 실패는{" "}
            <span className="font-semibold">재시도 대상이 아닙니다</span>.
            데이터 또는 품질 계약을 수정한 뒤 새 빌드로 재검증하세요. 실행 실패
            재시도는 빌드/실행 탭에 있습니다.
          </div>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <div className="section-label">계약 체크 ({checks.length})</div>
        {checksQuery.isLoading ? (
          <LoadingState rowCount={2} />
        ) : checksQuery.error ? (
          <ErrorState
            error={checksQuery.error}
            onRetry={() => void checksQuery.reload()}
          />
        ) : checks.length === 0 ? (
          <div className="text-[11px] text-muted-foreground">
            등록된 품질 체크가 없습니다. 데이터셋 화면에서 품질 계약을
            추가하세요.
          </div>
        ) : (
          checks.map((check) => (
            <div
              key={check.id}
              className="flex items-center gap-2 rounded border px-2 py-1"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[11px]">
                  {check.checkType}
                </span>
                <span className="block truncate text-[10px] text-muted-foreground">
                  {JSON.stringify(check.config)}
                </span>
              </span>
              <StatusPill
                intent={check.severity === "error" ? "danger" : "warning"}
              >
                {check.severity}
              </StatusPill>
              <StatusPill intent={check.enabled ? "success" : "neutral"}>
                {check.enabled ? "활성" : "비활성"}
              </StatusPill>
            </div>
          ))
        )}
      </div>

      <div className="space-y-1.5">
        <div className="section-label">
          최근 검증 결과 ({summary?.latestResults.length ?? 0})
        </div>
        {(summary?.latestResults ?? []).map((result) => (
          <div key={result.id} className="space-y-1 rounded border px-2 py-1.5">
            <div className="flex items-center gap-2">
              <StatusPill intent={qualityStatusIntent(result.status)}>
                {result.status}
              </StatusPill>
              <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                {result.checkType}
              </span>
            </div>
            <button
              type="button"
              onClick={() => {
                onSelectRun({
                  runType: runTypeFromRunId(result.runId),
                  runId: result.runId,
                });
                onChangeTab("runs");
              }}
              className="block max-w-full truncate font-mono text-[10px] text-primary hover:underline"
            >
              run={result.runId}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
