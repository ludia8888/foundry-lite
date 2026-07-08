import type {
  RuntimeRunDetail,
  RuntimeRunQueryResult,
} from "@foundry-lite/sdk";
import {
  foundryLiteOperationsRunDetailView,
  foundryLiteOperationsRunListView,
  useFoundryLiteClient,
  type FoundryLiteOperationsRunDetailState,
  type FoundryLiteOperationsRunListState,
} from "@foundry-lite/sdk/react";
import { useCallback, useMemo } from "react";

import type { FoundryLiteOperationsRunRow } from "@foundry-lite/sdk/react";

import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { useLineageQuery } from "@/features/lineage/use-lineage-query";

/**
 * sync run의 종결 상태 "COMMITTED"는 SDK phase 분류에 없어 unknown으로
 * 떨어진다. 화면 evidence 왜곡을 막기 위해 성공으로 정규화한다.
 */
function normalizeRunRow(
  run: FoundryLiteOperationsRunRow,
): FoundryLiteOperationsRunRow {
  if (run.phase !== "unknown") return run;
  if ((run.status ?? "").toLowerCase() !== "committed") return run;
  return {
    ...run,
    phase: "succeeded",
    isSucceeded: true,
    isPending: false,
    isRunning: false,
    isFailure: false,
  };
}

/**
 * operations.runs.list + foundryLiteOperationsRunListView 조합.
 * SDK의 useFoundryLiteOperationsRunList는 inline load로 무한 refetch 루프가
 * 있어(10초에 1,600회+ 관측) StrictMode-safe 로컬 프리미티브로 대체한다.
 */
export function useLineageRunList(
  limit: number,
): FoundryLiteOperationsRunListState {
  const client = useFoundryLiteClient();
  const load = useCallback(
    () => client.operations.runs.list({ limit }),
    [client, limit],
  );
  const query = useLineageQuery<RuntimeRunQueryResult>(
    ["lineage", "runs", limit],
    load,
  );
  const view = useMemo(() => {
    const rawView = foundryLiteOperationsRunListView(query.data);
    const runRows = rawView.runRows.map(normalizeRunRow);
    return {
      ...rawView,
      runRows,
      failedRuns: runRows.filter((run) => run.isFailure),
      runningRuns: runRows.filter((run) => run.isRunning),
      pendingRuns: runRows.filter((run) => run.isPending),
      succeededRuns: runRows.filter((run) => run.isSucceeded),
      unknownRuns: runRows.filter((run) => run.phase === "unknown"),
      selectedRun: rawView.selectedRun
        ? normalizeRunRow(rawView.selectedRun)
        : null,
    };
  }, [query.data]);
  return { ...query, ...view };
}

/** operations.runs.detail + foundryLiteOperationsRunDetailView 조합 (동일 사유). */
export function useLineageRunDetail(
  selectedRun: SelectedRunRef | null,
): FoundryLiteOperationsRunDetailState {
  const client = useFoundryLiteClient();
  const runType = selectedRun?.runType ?? null;
  const runId = selectedRun?.runId ?? null;
  const load = useCallback(() => {
    if (!runType || !runId) {
      throw new Error("run 상세를 조회하려면 run 선택이 필요합니다");
    }
    return client.operations.runs.detail(runType, runId);
  }, [client, runType, runId]);
  const query = useLineageQuery<RuntimeRunDetail>(
    ["lineage", "run-detail", runType, runId],
    load,
    { enabled: Boolean(runType && runId) },
  );
  const view = useMemo(() => {
    const rawView = foundryLiteOperationsRunDetailView(query.data);
    return {
      ...rawView,
      runRow: rawView.runRow ? normalizeRunRow(rawView.runRow) : null,
    };
  }, [query.data]);
  return { ...query, ...view };
}
